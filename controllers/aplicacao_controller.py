"""Controller de aplicação — orquestra as threads de serial e de banco.

Três fluxos, cada um na sua thread:

    GUI (main)        telas, cliques, gráfico      ← só recebe sinais
    SerialWorker      porta, handshake, telemetria ─┐
    DatabaseWorker    gravações e consultas SQLite ←┘ leituras e eventos

Este controller é quem cria, liga e encerra os workers. As telas não
conhecem as threads: continuam ouvindo o ``barramento``.

Fiação
------
- ``SerialWorker.leitura_recebida`` → ``barramento.leitura_recebida`` (telas)
  e ``DatabaseWorker.gravar_leitura`` (histórico).
- ``SerialWorker.estado_alterado`` → ``barramento.estado_link_alterado`` e
  ``status_conexao_alterado``; conexão, encerramento e queda viram eventos.
- ``SerialWorker.velocidade_negociada`` → ``velocidade_negociada`` deste
  controller (tela de comunicação serial).
- ``barramento.evento_registrado`` → ``DatabaseWorker.gravar_evento``, que
  confirma com ``evento_persistido`` depois do COMMIT.
- Corte/religamento confirmado no banco (``estado_disjuntor_alterado``) →
  ``RELE`` para a placa. Disjuntor aberto pela placa sem comando → disparo
  espontâneo gravado no banco e avisado às telas.

Ciclo de vida
-------------
Todo worker tem ``finished`` ligado a ``deleteLater``. Ao sair,
:meth:`encerrar` (ligado a ``QApplication.aboutToQuit``) pede a parada
cooperativa e espera cada thread terminar.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, pyqtSignal

from models.barramento import barramento
from models.config_manager import Configuracao
from models.database_worker import DatabaseWorker
from models.estado_disjuntor import EstadoDisjuntor
from models.estado_link import EstadoLink
from models.evento import TipoEvento
from models.leitura import Leitura
from models.porta_serial import ErroConexaoSerial, ParametrosConexao
from models.serial_worker import SerialWorker
from models.servico_corte import ServicoCorteEmergencial

logger = logging.getLogger("smartgrid")


class AplicacaoController(QObject):
    """Dono das threads de serial e de banco."""

    #: Disjuntor aberto pela proteção da placa, já refletido no banco quando
    #: possível. Carrega a mensagem de erro de gravação ("" se gravou).
    disparo_espontaneo = pyqtSignal(str)

    #: Velocidade (bps) que a placa confirmou no SYNC; repassada do SerialWorker
    #: para a tela de comunicação serial.
    velocidade_negociada = pyqtSignal(int)

    #: Tempo máximo esperando cada thread no encerramento.
    ESPERA_ENCERRAMENTO_MS = 3000

    def __init__(
        self,
        config: Configuracao,
        servico_corte: ServicoCorteEmergencial,
        estado_inicial: EstadoDisjuntor = EstadoDisjuntor.FECHADO,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._servico_corte = servico_corte
        #: Estado que o banco diz que o disjuntor tem; a placa é alinhada a ele.
        self._estado_disjuntor = estado_inicial
        self._ultima_leitura = Leitura.zerada()
        self._serial: SerialWorker | None = None

        self.banco = DatabaseWorker(parent=self)
        self.banco.finished.connect(self.banco.deleteLater)
        self.banco.start()

        barramento.evento_registrado.connect(self.banco.gravar_evento)
        barramento.estado_disjuntor_alterado.connect(self._ao_estado_disjuntor)

    # ------------------------------------------------------------------
    # Conexão serial
    # ------------------------------------------------------------------
    @property
    def estado_disjuntor(self) -> EstadoDisjuntor:
        """Último estado do disjuntor confirmado no banco ou reportado pela placa."""
        return self._estado_disjuntor

    @property
    def serial_ativa(self) -> bool:
        return self._serial is not None

    def conectar_serial(self, parametros: ParametrosConexao) -> None:
        """Cria a thread serial e inicia o handshake. Não bloqueia."""
        if self._serial is not None:
            raise ErroConexaoSerial(
                "A conexão anterior ainda está sendo encerrada. Tente em instantes."
            )

        worker = SerialWorker(
            parametros.porta,
            parametros.baud_rate,
            self._config.taxa_amostragem_ms,
            parametros.timeout_ms,
            parent=self,
        )
        worker.estado_alterado.connect(self._ao_estado_link)
        worker.velocidade_negociada.connect(self.velocidade_negociada)
        worker.leitura_recebida.connect(self._ao_leitura)
        worker.disjuntor_reportado.connect(self._ao_disjuntor_reportado)
        worker.finished.connect(self._ao_serial_encerrada)
        worker.finished.connect(worker.deleteLater)
        self._serial = worker
        worker.start()

    def desconectar_serial(self) -> None:
        """Pede o encerramento cooperativo da thread serial."""
        if self._serial is not None:
            self._serial.parar()

    def atualizar_configuracao(self, config: Configuracao) -> None:
        """Taxa de amostragem nova vale a partir da próxima conexão."""
        self._config = config

    def simular_disparo(self) -> None:
        """Mesmo tratamento de um disparo vindo da placa (demonstração sem hardware)."""
        self._ao_disjuntor_reportado(EstadoDisjuntor.ABERTO)

    # ------------------------------------------------------------------
    # Reações aos workers (rodam na thread da GUI)
    # ------------------------------------------------------------------
    def _ao_estado_link(self, nome: str, detalhe: str) -> None:
        barramento.estado_link_alterado.emit(nome, detalhe)
        estado = EstadoLink[nome]

        if estado is EstadoLink.TELEMETRIA:
            barramento.status_conexao_alterado.emit(True, detalhe)
            barramento.registrar_evento(
                TipoEvento.STATUS, f"Comunicação serial conectada em {detalhe}", detalhe
            )
            # A placa liga com a carga fechada; o banco é quem manda.
            self._serial.enviar_rele(self._estado_disjuntor.energizado)
        elif estado is EstadoLink.DESCONECTADO:
            barramento.status_conexao_alterado.emit(False, detalhe)
            barramento.registrar_evento(
                TipoEvento.STATUS, "Comunicação serial encerrada", detalhe
            )
        elif estado is EstadoLink.LINK_PERDIDO:
            barramento.status_conexao_alterado.emit(False, f"Link perdido — {detalhe}")
            barramento.registrar_evento(
                TipoEvento.ALERTA, "Link serial com a placa perdido", detalhe
            )

    def _ao_leitura(self, leitura: Leitura) -> None:
        self._ultima_leitura = leitura
        barramento.leitura_recebida.emit(leitura)
        self.banco.gravar_leitura(leitura)

    def _ao_disjuntor_reportado(self, estado: EstadoDisjuntor) -> None:
        if estado is self._estado_disjuntor:
            return

        if estado is EstadoDisjuntor.FECHADO:
            # Placa fechada com o banco dizendo ABERTO (ex.: reiniciou):
            # realinha a placa em vez de religar a carga sem comando.
            if self._serial is not None:
                self._serial.enviar_rele(False)
            return

        logger.warning("Placa reportou abertura do disjuntor sem comando do operador.")
        self._estado_disjuntor = EstadoDisjuntor.ABERTO
        barramento.estado_disjuntor_alterado.emit(EstadoDisjuntor.ABERTO)
        self.banco.executar(
            self._servico_corte.registrar_disparo_espontaneo,
            self._ultima_leitura.resumo(),
            ao_concluir=lambda _evento: self.disparo_espontaneo.emit(""),
            ao_falhar=self._ao_falhar_registro_disparo,
        )

    def _ao_falhar_registro_disparo(self, erro: Exception) -> None:
        logger.error("Falha ao registrar o disparo espontâneo no banco: %s", erro)
        self.disparo_espontaneo.emit(str(erro))

    def _ao_estado_disjuntor(self, estado: EstadoDisjuntor) -> None:
        """Corte/religamento confirmado no banco: leva o comando à placa."""
        self._estado_disjuntor = estado
        if self._serial is not None:
            self._serial.enviar_rele(estado.energizado)

    def _ao_serial_encerrada(self) -> None:
        if self.sender() is self._serial:
            self._serial = None

    # ------------------------------------------------------------------
    # Encerramento
    # ------------------------------------------------------------------
    def encerrar(self) -> None:
        """Parada cooperativa das duas threads; chamado no ``aboutToQuit``."""
        if self._serial is not None:
            serial = self._serial
            serial.parar()
            if not serial.wait(self.ESPERA_ENCERRAMENTO_MS):
                logger.error("A thread serial não encerrou no prazo.")

        # Por último: grava o que a serial ainda produziu antes de parar.
        self.banco.parar()
        if not self.banco.wait(self.ESPERA_ENCERRAMENTO_MS):
            logger.error("A thread de banco não encerrou no prazo.")
        logger.info("Threads encerradas. Aplicação finalizada.")
