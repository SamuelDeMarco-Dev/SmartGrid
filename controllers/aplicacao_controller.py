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
- Intenção persistida (``estado_disjuntor_alterado``) → ``RELE`` pendente.
  Só telemetria compatível confirma o estado. A situação exibida é publicada
  separadamente, sem confundir o COMMIT com uma atuação da placa.
- ``SerialWorker.botao_acionado`` → corte solicitado pelo desktop. O botão da
  placa só é reportado na telemetria; quem abre o relé é o software.

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
from models.servico_corte import FalhaCorteEmergencial, ServicoCorteEmergencial
from models.situacao_disjuntor import ConfirmacaoDisjuntor, SituacaoDisjuntor

logger = logging.getLogger("smartgrid")


class AplicacaoController(QObject):
    """Dono das threads de serial e de banco."""

    #: Disjuntor aberto pela proteção (botão da placa ou abertura sem comando),
    #: já refletido no banco quando possível. Carrega a mensagem de erro de
    #: gravação ("" se gravou).
    disparo_espontaneo = pyqtSignal(str)
    disparo_simulado = pyqtSignal(str)
    situacao_disjuntor_alterada = pyqtSignal(object)
    operacao_falhou = pyqtSignal(str)

    #: Velocidade (bps) em que a placa passou a enviar amostras; repassada do SerialWorker
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
        self._estado_solicitado = estado_inicial
        self._estado_reportado: EstadoDisjuntor | None = None
        self._situacao = SituacaoDisjuntor(estado_inicial)
        self._destino_pendente: EstadoDisjuntor | None = None
        self._persistindo = False
        self._serial_da_operacao: SerialWorker | None = None
        self._link_ativo = False
        self._primeira_telemetria = True
        self._simulando_disparo = False
        self._disparo_pelo_botao = False
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
    def estado_disjuntor(self) -> EstadoDisjuntor | None:
        """Estado reportado nesta conexão; None quando não há telemetria válida."""
        return self._estado_reportado

    @property
    def situacao_disjuntor(self) -> SituacaoDisjuntor:
        return self._situacao

    @property
    def comando_pendente(self) -> bool:
        return self._persistindo or self._destino_pendente is not None

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
        worker.rele_confirmado.connect(self._ao_rele_confirmado)
        worker.rele_falhou.connect(self._ao_rele_falhou)
        worker.botao_acionado.connect(self._ao_botao_acionado)
        worker.finished.connect(self._ao_serial_encerrada)
        worker.finished.connect(worker.deleteLater)
        self._serial = worker
        self._link_ativo = False
        self._primeira_telemetria = True
        self._estado_reportado = None
        self._publicar_situacao(ConfirmacaoDisjuntor.DESCONHECIDO)
        worker.start()

    def desconectar_serial(self) -> None:
        """Pede o encerramento cooperativo da thread serial."""
        if self._serial is not None:
            self._invalidar_comunicacao("Comunicação serial sendo encerrada.")
            self._serial.parar()

    def atualizar_configuracao(self, config: Configuracao) -> None:
        """Taxa de amostragem nova vale a partir da próxima conexão."""
        self._config = config

    def simular_disparo(self) -> None:
        """Demonstração: com placa conectada, solicita o corte e espera telemetria."""
        if self.comando_pendente:
            return
        if self._link_ativo:
            if self._estado_reportado is EstadoDisjuntor.FECHADO:
                self._simulando_disparo = self.solicitar_estado_disjuntor(
                    EstadoDisjuntor.ABERTO, self._ultima_leitura.resumo()
                )
        else:
            # Preserva o registro/aviso da demonstração sem fabricar uma
            # confirmação serial quando não há placa conectada.
            if self._estado_solicitado is EstadoDisjuntor.ABERTO:
                return
            self._estado_solicitado = EstadoDisjuntor.ABERTO
            self._publicar_situacao(ConfirmacaoDisjuntor.DESCONHECIDO)
            self.banco.executar(
                self._servico_corte.registrar_disparo_espontaneo,
                self._ultima_leitura.resumo(), simulado=True,
                ao_concluir=lambda _evento: self.disparo_simulado.emit(""),
                ao_falhar=lambda erro: self.disparo_simulado.emit(str(erro)),
            )

    def solicitar_estado_disjuntor(self, destino: EstadoDisjuntor, valor_medido: str = "") -> bool:
        """Persiste a intenção e coordena uma única atuação, fora da thread da GUI."""
        if self.comando_pendente or not self._link_ativo or self._estado_reportado is None:
            return False
        self._destino_pendente = destino
        self._persistindo = True
        self._serial_da_operacao = self._serial
        self._publicar_situacao(ConfirmacaoDisjuntor.PENDENTE, "Salvando solicitação de atuação.")
        self.banco.executar(
            self._servico_corte.executar, destino, valor_medido,
            ao_falhar=self._ao_falhar_persistencia,
        )
        return True

    # ------------------------------------------------------------------
    # Reações aos workers (rodam na thread da GUI)
    # ------------------------------------------------------------------
    def _ao_estado_link(self, nome: str, detalhe: str) -> None:
        if self.sender() is not self._serial:
            return
        barramento.estado_link_alterado.emit(nome, detalhe)
        estado = EstadoLink[nome]

        if estado is EstadoLink.TELEMETRIA:
            self._link_ativo = True
            barramento.status_conexao_alterado.emit(True, detalhe)
            barramento.registrar_evento(
                TipoEvento.STATUS, f"Comunicação serial conectada em {detalhe}", detalhe
            )
            # Primeiro observa a placa. Nunca envia RELE,1 na reconexão.
        elif estado is EstadoLink.DESCONECTADO:
            self._invalidar_comunicacao(detalhe)
            barramento.status_conexao_alterado.emit(False, detalhe)
            barramento.registrar_evento(
                TipoEvento.STATUS, "Comunicação serial encerrada", detalhe
            )
        elif estado is EstadoLink.LINK_PERDIDO:
            self._invalidar_comunicacao(f"Link perdido — {detalhe}")
            barramento.status_conexao_alterado.emit(False, f"Link perdido — {detalhe}")
            barramento.registrar_evento(
                TipoEvento.ALERTA, "Link serial com a placa perdido", detalhe
            )

    def _ao_leitura(self, leitura: Leitura) -> None:
        if not self._link_ativo or self.sender() is not self._serial:
            return
        self._ultima_leitura = leitura
        barramento.leitura_recebida.emit(leitura)
        self.banco.gravar_leitura(leitura)

    def _ao_disjuntor_reportado(self, estado: EstadoDisjuntor) -> None:
        if not self._link_ativo or self.sender() is not self._serial:
            return
        anterior = self._estado_reportado
        primeira = self._primeira_telemetria
        self._primeira_telemetria = False
        self._estado_reportado = estado
        if self.comando_pendente:
            self._publicar_situacao(ConfirmacaoDisjuntor.PENDENTE)
            return
        if estado is self._estado_solicitado:
            self._publicar_situacao(ConfirmacaoDisjuntor.CONFIRMADO)
        elif estado is EstadoDisjuntor.ABERTO and primeira:
            # A placa liga com o relé desligado e o UNO reinicia ao abrir a
            # porta: é o estado de boot, não um disparo. Nunca religa sozinho.
            self._registrar_disparo(inicial=True)
        elif estado is EstadoDisjuntor.ABERTO and anterior is EstadoDisjuntor.FECHADO:
            # Abriu sem comando (ex.: placa reiniciou): exige religamento manual.
            self._registrar_disparo()
        elif primeira and self._estado_solicitado is EstadoDisjuntor.ABERTO:
            # Uma única tentativa segura de corte por conexão. Se falhar,
            # as amostras seguintes apenas mantêm a divergência visível.
            self._iniciar_comando(EstadoDisjuntor.ABERTO)
        elif self._situacao.confirmacao is not ConfirmacaoDisjuntor.FALHA:
            self._publicar_situacao(
                ConfirmacaoDisjuntor.FALHA,
                f"Divergência: solicitado {self._estado_solicitado.name}, placa reporta {estado.name}.",
            )
            barramento.registrar_evento(TipoEvento.ALERTA, self._situacao.detalhe)

    def _registrar_disparo(self, *, inicial: bool = False) -> None:
        if inicial:
            logger.info("Placa iniciou com o relé desligado; religamento exige ação manual.")
        else:
            logger.warning("Abertura espontânea do disjuntor; religamento exige ação manual.")
        self._estado_solicitado = EstadoDisjuntor.ABERTO
        self._publicar_situacao(
            ConfirmacaoDisjuntor.CONFIRMADO if self._estado_reportado is not None
            else ConfirmacaoDisjuntor.DESCONHECIDO
        )
        self.banco.executar(
            self._servico_corte.registrar_disparo_espontaneo,
            self._ultima_leitura.resumo(), inicial=inicial,
            ao_concluir=None if inicial else lambda _evento: self.disparo_espontaneo.emit(""),
            ao_falhar=self._ao_falhar_registro_disparo,
        )

    def _ao_botao_acionado(self) -> None:
        """Botão da placa: o firmware só o reporta; o desktop comanda o corte."""
        if self.sender() is not self._serial or not self._link_ativo:
            return
        resumo = self._ultima_leitura.resumo()
        barramento.registrar_evento(TipoEvento.ALERTA, "Botão de proteção acionado na placa", resumo)
        if self.comando_pendente or self._estado_reportado is not EstadoDisjuntor.FECHADO:
            return
        self._disparo_pelo_botao = self.solicitar_estado_disjuntor(EstadoDisjuntor.ABERTO, resumo)

    def _ao_falhar_registro_disparo(self, erro: Exception) -> None:
        logger.error("Falha ao registrar o disparo espontâneo no banco: %s", erro)
        self.disparo_espontaneo.emit(str(erro))

    def _ao_estado_disjuntor(self, estado: EstadoDisjuntor) -> None:
        """COMMIT confirma a intenção; a telemetria confirma o estado da placa."""
        self._estado_solicitado = estado
        if self._persistindo:
            self._persistindo = False
            if self._serial_da_operacao is not self._serial or not self._link_ativo:
                motivo = "Solicitação salva, mas a comunicação foi interrompida."
                if self._destino_pendente is not None:
                    self._falhar_operacao(motivo)
                else:
                    # A desconexão já notificou esta operação. O COMMIT tardio
                    # só atualiza a intenção, sem reenviar nem duplicar o alerta.
                    self._publicar_situacao(ConfirmacaoDisjuntor.FALHA, motivo)
                return
        elif self._destino_pendente is not None:
            return
        if not self._link_ativo or self._estado_reportado is None:
            self._destino_pendente = estado
            self._falhar_operacao("Solicitação salva, mas não há telemetria válida da placa.")
            return
        self._iniciar_comando(estado)

    def _iniciar_comando(self, destino: EstadoDisjuntor) -> None:
        self._destino_pendente = destino
        self._publicar_situacao(ConfirmacaoDisjuntor.PENDENTE)
        if self._serial is None or not self._serial.enviar_rele(destino.energizado):
            self._falhar_operacao("Não foi possível enviar o comando à placa.")

    def _ao_rele_confirmado(self, destino: EstadoDisjuntor) -> None:
        if self.sender() is not self._serial or destino is not self._destino_pendente:
            return
        if not self._link_ativo or self._estado_reportado is not destino:
            return
        self._destino_pendente = None
        self._publicar_situacao(ConfirmacaoDisjuntor.CONFIRMADO)
        barramento.registrar_evento(
            TipoEvento.STATUS, f"Comando de {destino.name} confirmado pela telemetria da placa"
        )
        if self._simulando_disparo:
            self._simulando_disparo = False
            self.disparo_simulado.emit("")
        if self._disparo_pelo_botao:
            self._disparo_pelo_botao = False
            self.disparo_espontaneo.emit("")

    def _ao_rele_falhou(self, destino: EstadoDisjuntor, motivo: str) -> None:
        if self.sender() is self._serial and destino is self._destino_pendente:
            self._falhar_operacao(motivo)

    def _ao_falhar_persistencia(self, erro: Exception) -> None:
        self._persistindo = False
        if isinstance(erro, FalhaCorteEmergencial):
            motivo = (
                "A operação não foi concluída e foi desfeita (rollback).\n"
                "Nenhum comando foi enviado à placa.\n\n"
                f"Motivo: {erro}"
            )
        else:
            motivo = f"Não foi possível salvar a solicitação. Nenhum comando foi enviado.\n{erro}"
        self._falhar_operacao(motivo, registrar=False)
        if self._estado_reportado is EstadoDisjuntor.ABERTO and self._estado_solicitado is EstadoDisjuntor.FECHADO:
            # D2 pode ter aberto a carga enquanto o SQLite tentava salvar o
            # corte. O rollback não desfaz a abertura reportada pela placa.
            self._registrar_disparo()

    def _falhar_operacao(self, motivo: str, *, registrar: bool = True) -> None:
        self._destino_pendente = None
        self._simulando_disparo = False
        self._disparo_pelo_botao = False
        self._publicar_situacao(ConfirmacaoDisjuntor.FALHA, motivo)
        if registrar:
            barramento.registrar_evento(TipoEvento.ALERTA, f"Falha de confirmação: {motivo}")
        self.operacao_falhou.emit(motivo)

    def _invalidar_comunicacao(self, motivo: str) -> None:
        self._link_ativo = False
        self._estado_reportado = None
        self._serial_da_operacao = None
        if self._destino_pendente is not None:
            self._falhar_operacao(motivo)
        else:
            self._publicar_situacao(ConfirmacaoDisjuntor.DESCONHECIDO, motivo)

    def _publicar_situacao(self, confirmacao: ConfirmacaoDisjuntor, detalhe: str = "") -> None:
        solicitado = self._destino_pendente or self._estado_solicitado
        if not detalhe:
            if confirmacao is ConfirmacaoDisjuntor.PENDENTE:
                reportado = self._estado_reportado.name if self._estado_reportado is not None else "DESCONHECIDO"
                detalhe = f"Aguardando {solicitado.name}; placa reporta {reportado}."
            elif confirmacao is ConfirmacaoDisjuntor.CONFIRMADO:
                detalhe = "Estado confirmado pela telemetria da placa."
            else:
                detalhe = "Aguardando telemetria válida da placa."
        situacao = SituacaoDisjuntor(solicitado, self._estado_reportado, confirmacao, detalhe)
        if situacao != self._situacao:
            self._situacao = situacao
            self.situacao_disjuntor_alterada.emit(situacao)

    def _ao_serial_encerrada(self) -> None:
        if self.sender() is self._serial:
            if self._link_ativo:
                self._invalidar_comunicacao("Comunicação serial encerrada.")
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
