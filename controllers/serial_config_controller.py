"""Controller do Painel de Configuração da Comunicação Serial.

Media a ``SerialConfigView`` e o Model:

    - formulário → validação em :class:`PortaSerial` → ``conectar_serial`` do
      ``AplicacaoController``, que cria a thread do handshake;
    - fases do link (``barramento.estado_link_alterado``) → faixa de etapas e
      indicador colorido: Desconectado → Abrindo porta → Handshake a 9600 bps
      → Negociando velocidade → Telemetria ativa, ou Link perdido;
    - velocidade efetivamente negociada (``velocidade_negociada`` do serviço
      serial) → rótulo da tela;
    - porta, velocidade alvo, timeout e taxa de amostragem → ``data/config.json``,
      gravado no ``DatabaseWorker`` para não bloquear a GUI.

Entradas inválidas geram ``[WARNING]`` no log; conectar e desconectar geram
``[INFO]``; falha ao abrir a porta ou ao gravar o arquivo gera ``[ERROR]``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from weakref import WeakMethod

from PyQt6 import sip
from PyQt6.QtCore import QObject

from models.barramento import barramento
from models.config_manager import ConfigManager, Configuracao
from models.estado_link import EstadoLink
from models.log_config import obter_logger
from models.porta_serial import (
    BAUD_RATES,
    PORTAS_SIMULADAS,
    ErroConexaoSerial,
    ParametrosConexao,
    PortaSerial,
)
from views.serial_config_view import SerialConfigView

logger = obter_logger()

#: Fases mostradas na faixa da tela, na ordem do protocolo.
ETAPAS = (
    EstadoLink.DESCONECTADO,
    EstadoLink.ABRINDO_PORTA,
    EstadoLink.HANDSHAKE,
    EstadoLink.NEGOCIANDO,
    EstadoLink.TELEMETRIA,
)


class SerialConfigController(QObject):
    """Configura a porta, dispara o handshake e acompanha o link com a placa."""

    def __init__(
        self,
        config: Configuracao,
        servico_serial,
        view: SerialConfigView | None = None,
    ) -> None:
        super().__init__()
        self.view = view or SerialConfigView()
        self.porta_serial = PortaSerial()
        #: O AplicacaoController é dono dos workers usados pelo formulário.
        self._servico_serial = servico_serial
        self._config = config
        self._consultando_portas = False
        self._primeira_listagem = True
        self._estado = EstadoLink.DESCONECTADO
        self._detalhe = ""
        #: Última etapa alcançada, para mostrar onde o link caiu.
        self._etapa = 0

        hoje = date.today()
        self.view.preencher_velocidades(list(BAUD_RATES), config.baud_rate)
        self.view.definir_timeout(config.timeout_ms)
        self.view.definir_taxa_amostragem(config.taxa_amostragem_ms)
        self.view.definir_data_sessao(hoje, maxima=hoje)
        self.view.definir_etapas([estado.rotulo for estado in ETAPAS])
        self.view.mostrar_velocidade_negociada("—")

        self._conectar_view()
        barramento.estado_link_alterado.connect(self._ao_estado_link)
        servico_serial.velocidade_negociada.connect(self._ao_velocidade_negociada)
        self.atualizar_portas()

    def _conectar_view(self) -> None:
        self.view.conectar_solicitado.connect(self.conectar)
        self.view.desconectar_solicitado.connect(self.desconectar)
        self.view.atualizar_portas_solicitado.connect(self.atualizar_portas)
        # A data segue editável com a porta aberta — é uma anotação da sessão,
        # não um parâmetro da conexão —, então o detalhe precisa acompanhá-la.
        self.view.data_sessao_alterada.connect(self._atualizar_tela)

    # ------------------------------------------------------------------
    # Descoberta de portas
    # ------------------------------------------------------------------
    def atualizar_portas(self) -> None:
        """Enfileira a descoberta de portas do sistema."""
        if self._consultando_portas or self._link_ocupado:
            return
        self._consultando_portas = True
        self._atualizar_tela()
        self.view.mostrar_buscando_portas()
        self._servico_serial.banco.executar(
            PortaSerial.listar_portas,
            ao_concluir=self._callback_tela(self._mostrar_portas),
            ao_falhar=self._callback_tela(self._falhar_listagem),
        )

    def _mostrar_portas(self, portas: list[str]) -> None:
        if self._primeira_listagem:
            self._primeira_listagem = False
            selecionada = self._porta_inicial(portas)
        else:
            selecionada = self.view.porta()
        self.view.preencher_portas(portas, selecionada)
        self._consultando_portas = False
        self._atualizar_tela()

    def _porta_inicial(self, portas: list[str]) -> str:
        """A porta do config.json ou, se ela sumiu, a primeira disponível."""
        porta_salva = self._config.porta_com
        if porta_salva in portas:
            return porta_salva
        # listar_portas() nunca devolve lista vazia.
        logger.warning(
            "Porta salva %s não está disponível. Selecionada %s.", porta_salva, portas[0]
        )
        return portas[0]

    def _falhar_listagem(self, erro: Exception) -> None:
        logger.error("Não foi possível listar as portas seriais: %s.", erro)
        self._mostrar_portas(list(PORTAS_SIMULADAS))

    # ------------------------------------------------------------------
    # Conectar / desconectar
    # ------------------------------------------------------------------
    def conectar(self) -> None:
        """Valida o formulário e pede ao serviço serial o handshake na porta."""
        if self._consultando_portas or self._link_ocupado:
            return
        try:
            parametros = self.porta_serial.conectar(
                porta=self.view.porta(),
                baud_rate=self.view.velocidade(),
                timeout_ms=self._inteiro(self.view.timeout_digitado(), "o timeout"),
            )
            try:
                taxa_ms = PortaSerial.validar_taxa_amostragem(
                    self._inteiro(self.view.taxa_digitada(), "a taxa de amostragem")
                )
                # A taxa nova já vale para esta conexão: o SerialWorker nasce
                # com a configuração em memória do serviço serial.
                self._servico_serial.atualizar_configuracao(
                    replace(self._config, taxa_amostragem_ms=taxa_ms)
                )
                self._servico_serial.conectar_serial(parametros)
            except Exception:
                self.porta_serial.desconectar()
                raise
        except ErroConexaoSerial as erro:
            logger.warning(
                "Entrada inválida em serial: porta=%r, timeout=%r, taxa=%r. %s",
                self.view.porta(), self.view.timeout_digitado(),
                self.view.taxa_digitada(), erro,
            )
            self.view.avisar(str(erro))
            return
        except Exception as erro:
            # Driver ausente, porta ocupada etc.
            porta = self.view.porta()
            logger.error("Falha ao abrir a porta %s: %s.", porta, erro)
            self.view.avisar_erro(f"Não foi possível abrir a porta {porta}.\n\n{erro}")
            return

        logger.info(
            "Conexão serial solicitada: %s (timeout %d ms, amostragem %d ms). "
            "Iniciando handshake a 9600 bps.",
            parametros.descricao, parametros.timeout_ms, taxa_ms,
        )
        self.view.mostrar_velocidade_negociada("—")
        self._salvar_configuracao(parametros, taxa_ms)
        self._atualizar_tela()

    def desconectar(self) -> None:
        """Pede o encerramento cooperativo da thread serial."""
        try:
            parametros = self.porta_serial.desconectar()
        except ErroConexaoSerial as erro:
            self.view.avisar(str(erro))
            return

        logger.info("Comunicação serial encerrada: %s.", parametros.descricao)
        self._servico_serial.desconectar_serial()
        self._atualizar_tela()

    @property
    def _link_ocupado(self) -> bool:
        """Há conexão pedida ou thread serial viva (conectando, ativa ou encerrando)."""
        return self.porta_serial.conectado or self._estado.em_andamento

    # ------------------------------------------------------------------
    # Fases do link (vindas da thread serial pelo barramento)
    # ------------------------------------------------------------------
    def _ao_estado_link(self, nome: str, detalhe: str) -> None:
        estado = EstadoLink[nome]
        self._estado = estado
        self._detalhe = detalhe
        if estado in ETAPAS:
            self._etapa = ETAPAS.index(estado)
        if estado is EstadoLink.ABRINDO_PORTA or not estado.em_andamento:
            self.view.mostrar_velocidade_negociada("—")
        if not estado.em_andamento and self.porta_serial.conectado:
            # Link caiu ou foi encerrado pela thread: libera o formulário.
            self.porta_serial.desconectar()
        self._atualizar_tela()

    def _ao_velocidade_negociada(self, baud: int) -> None:
        self.view.mostrar_velocidade_negociada(f"{baud} bps")

    # ------------------------------------------------------------------
    # Reflexo do estado na interface
    # ------------------------------------------------------------------
    def _atualizar_tela(self) -> None:
        livre = not self._link_ocupado and not self._consultando_portas
        self.view.habilitar_controles(
            campos=livre, conectar=livre, desconectar=self.porta_serial.conectado
        )
        self._mostrar_estado()

    def _mostrar_estado(self) -> None:
        estado, detalhe, etapa = self._estado, self._detalhe, self._etapa
        parametros = self.porta_serial.parametros

        if parametros is not None and not estado.em_andamento:
            # "Conectar" aceito; a thread ainda não reportou a primeira fase.
            estado, detalhe = EstadoLink.ABRINDO_PORTA, parametros.porta
            etapa = ETAPAS.index(estado)
        elif parametros is None and estado.em_andamento:
            # "Desconectar" pedido; a thread ainda está liberando a porta.
            self.view.mostrar_estado_link(
                "Encerrando conexão", EstadoLink.DESCONECTADO.cor,
                "Aguardando a thread serial liberar a porta…",
            )
            return

        if estado is EstadoLink.DESCONECTADO:
            etapa = 0
            detalhe = detalhe or "Nenhuma porta aberta"
        elif estado.em_andamento and parametros is not None:
            inicio = self.view.data_sessao().strftime("%d/%m/%Y")
            detalhe = (
                f"{detalhe} · timeout {parametros.timeout_ms} ms · "
                f"sessão de monitoramento iniciada em {inicio}"
            )
        elif estado is EstadoLink.LINK_PERDIDO:
            detalhe = f"{detalhe} — verifique o cabo e conecte novamente."

        self.view.mostrar_estado_link(estado.rotulo, estado.cor, detalhe)
        self.view.mostrar_progresso(etapa, estado.cor, EstadoLink.TELEMETRIA.cor)

    # ------------------------------------------------------------------
    # Persistência do config.json
    # ------------------------------------------------------------------
    def _salvar_configuracao(self, parametros: ParametrosConexao, taxa_ms: int) -> None:
        """Atualiza os campos da conexão sobre o JSON vigente, no worker."""
        self._servico_serial.banco.executar(
            ConfigManager.atualizar,
            padrao=self._config,
            porta_com=parametros.porta,
            baud_rate=parametros.baud_rate,
            timeout_ms=parametros.timeout_ms,
            taxa_amostragem_ms=taxa_ms,
            ao_concluir=self._callback_tela(self._configuracao_salva),
            ao_falhar=self._callback_tela(self._falhar_configuracao),
        )

    def _configuracao_salva(self, config: Configuracao) -> None:
        self._config = config
        self._servico_serial.atualizar_configuracao(config)

    def _falhar_configuracao(self, erro: Exception) -> None:
        logger.error("Não foi possível salvar a configuração serial: %s.", erro)
        self.view.avisar(
            "A porta foi conectada, mas a configuração não pôde ser salva "
            "e não será lembrada na próxima abertura."
        )

    def _callback_tela(self, metodo):
        """Entrega o resultado do worker só se a tela ainda existir."""
        referencia = WeakMethod(metodo)

        def entregar(resultado):
            callback = referencia()
            if callback is not None and not sip.isdeleted(callback.__self__):
                callback(resultado)

        return entregar

    # ------------------------------------------------------------------
    # Leitura do formulário
    # ------------------------------------------------------------------
    @staticmethod
    def _inteiro(texto: str, campo: str) -> int:
        """Converte o texto do campo; a faixa válida é conferida por PortaSerial."""
        texto = texto.strip()
        if not texto:
            raise ErroConexaoSerial(f"Informe {campo} em milissegundos.")
        try:
            valor = int(texto)
        except ValueError:
            raise ErroConexaoSerial(
                f"{campo[0].upper()}{campo[1:]} deve ser um número inteiro de milissegundos."
            ) from None
        if valor < 0:
            raise ErroConexaoSerial(f"{campo[0].upper()}{campo[1:]} não aceita valor negativo.")
        return valor
