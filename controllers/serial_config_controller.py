"""Controller do Painel de Configuração da Comunicação Serial.

Traduz o formulário da tela em chamadas ao serviço :class:`PortaSerial` e
publica o resultado no barramento — a janela principal usa
``status_conexao_alterado`` para alimentar a ``QStatusBar`` e a tela de
Histórico recebe os eventos de conexão e desconexão.

A tela abre com a última configuração do ``data/config.json`` e grava porta,
baud rate e timeout a cada conexão bem-sucedida. Entradas inválidas geram
``[WARNING]`` no log; conectar e desconectar geram ``[INFO]``; falha ao abrir a
porta ou ao gravar o arquivo gera ``[ERROR]``.

Nesta entrega a conexão é simulada: nenhum byte trafega.
"""

from dataclasses import replace

from PyQt6.QtCore import QDate
from PyQt6.QtWidgets import QMessageBox, QWidget

from models.barramento import barramento
from models.config_manager import ConfigManager, Configuracao
from models.evento import TipoEvento
from models.log_config import obter_logger
from models.porta_serial import (
    BAUD_RATES,
    ErroConexaoSerial,
    ParametrosConexao,
    PortaSerial,
)
from ui.serial_config_ui import Ui_SerialConfig

logger = obter_logger()


class SerialConfigController(QWidget, Ui_SerialConfig):
    """Tela de configuração e abertura da porta serial."""

    #: Item neutro no topo do combo. Existe para que "conectar sem escolher
    #: porta" continue sendo um caminho possível — e, portanto, validável.
    _PLACEHOLDER_PORTA = "— Selecione a porta —"

    #: Mesmas cores do badge de disjuntor, para a tela não destoar do resto.
    _COR_CONECTADO = "#2E7D32"
    _COR_DESCONECTADO = "#C62828"

    def __init__(self, config: Configuracao) -> None:
        super().__init__()
        self.setupUi(self)
        self.porta_serial = PortaSerial()
        self._config = config
        self._preencher_baud_rates()
        self.editTimeout.setText(str(config.timeout_ms))
        self._configurar_data_sessao()
        self.atualizar_portas()
        self._selecionar_porta_salva()
        self._conectar_acoes()
        self._atualizar_estado_visual()

    # ------------------------------------------------------------------
    # Ações da tela
    # ------------------------------------------------------------------
    def atualizar_portas(self) -> None:
        """Recarrega o combo de portas preservando a seleção atual."""
        selecionada = self.comboPortaSerial.currentText()

        self.comboPortaSerial.clear()
        self.comboPortaSerial.addItem(self._PLACEHOLDER_PORTA)
        self.comboPortaSerial.addItems(PortaSerial.listar_portas())

        indice = self.comboPortaSerial.findText(selecionada)
        self.comboPortaSerial.setCurrentIndex(max(indice, 0))

    def conectar(self) -> None:
        """Abre a conexão simulada e propaga o novo status."""
        try:
            parametros = self.porta_serial.conectar(
                porta=self._porta_selecionada(),
                baud_rate=self._baud_rate_selecionado(),
                timeout_ms=self._timeout_digitado(),
            )
        except ErroConexaoSerial as erro:
            logger.warning(
                "Entrada inválida em serial: porta=%r, timeout=%r. %s",
                self._porta_selecionada(), self.editTimeout.text(), erro,
            )
            self._avisar(str(erro))
            return
        except Exception as erro:
            # Com a porta simulada isto não ocorre; com hardware real (Unidade 4)
            # é aqui que caem driver ausente, porta ocupada etc.
            porta = self._porta_selecionada()
            logger.error("Falha ao abrir a porta %s: %s.", porta, erro)
            QMessageBox.critical(
                self, "Comunicação serial", f"Não foi possível abrir a porta {porta}.\n\n{erro}"
            )
            return

        logger.info(
            "Comunicação serial conectada: %s (timeout %d ms).",
            parametros.descricao, parametros.timeout_ms,
        )
        self._salvar_configuracao(parametros)
        self._atualizar_estado_visual()
        barramento.status_conexao_alterado.emit(True, parametros.descricao)
        barramento.registrar_evento(
            TipoEvento.STATUS,
            f"Comunicação serial conectada em {parametros.porta}",
            parametros.descricao_completa,
        )

    def desconectar(self) -> None:
        """Encerra a conexão simulada e propaga o novo status."""
        try:
            parametros = self.porta_serial.desconectar()
        except ErroConexaoSerial as erro:
            self._avisar(str(erro))
            return

        logger.info("Comunicação serial encerrada: %s.", parametros.descricao)
        self._atualizar_estado_visual()
        barramento.status_conexao_alterado.emit(
            False, f"Porta {parametros.porta} liberada"
        )
        barramento.registrar_evento(
            TipoEvento.STATUS,
            f"Comunicação serial encerrada em {parametros.porta}",
            parametros.descricao,
        )

    def _salvar_configuracao(self, parametros: ParametrosConexao) -> None:
        """Grava a conexão bem-sucedida no config.json, preservando os demais campos."""
        nova = replace(
            self._config,
            porta_com=parametros.porta,
            baud_rate=parametros.baud_rate,
            timeout_ms=parametros.timeout_ms,
        )
        try:
            ConfigManager.salvar(nova)
        except OSError as erro:
            logger.error("Não foi possível salvar a configuração serial: %s.", erro)
            self._avisar(
                "A porta foi conectada, mas a configuração não pôde ser salva "
                "e não será lembrada na próxima abertura."
            )
            return
        self._config = nova

    # ------------------------------------------------------------------
    # Montagem inicial
    # ------------------------------------------------------------------
    def _preencher_baud_rates(self) -> None:
        for valor in BAUD_RATES:
            self.comboBaudRate.addItem(str(valor), valor)
        self.comboBaudRate.setCurrentIndex(
            self.comboBaudRate.findData(self._config.baud_rate)
        )

    def _selecionar_porta_salva(self) -> None:
        """Seleciona a porta do config.json ou, se ela sumiu, a primeira disponível."""
        porta_salva = self._config.porta_com
        indice = self.comboPortaSerial.findText(porta_salva)
        if indice < 0:
            # Índice 0 é o placeholder; listar_portas() nunca devolve lista vazia.
            indice = 1
            logger.warning(
                "Porta salva %s não está disponível. Selecionada %s.",
                porta_salva, self.comboPortaSerial.itemText(indice),
            )
        self.comboPortaSerial.setCurrentIndex(indice)

    def _configurar_data_sessao(self) -> None:
        hoje = QDate.currentDate()
        self.dateInicioSessao.setDate(hoje)
        # A sessão de monitoramento não começa no futuro.
        self.dateInicioSessao.setMaximumDate(hoje)

    def _conectar_acoes(self) -> None:
        self.botaoConectar.clicked.connect(self.conectar)
        self.botaoDesconectar.clicked.connect(self.desconectar)
        self.botaoAtualizarPortas.clicked.connect(self.atualizar_portas)
        # A data segue editável com a porta aberta — é uma anotação da sessão,
        # não um parâmetro da conexão —, então o rótulo precisa acompanhá-la.
        self.dateInicioSessao.dateChanged.connect(self._atualizar_rotulo_status)

    # ------------------------------------------------------------------
    # Leitura do formulário
    # ------------------------------------------------------------------
    def _porta_selecionada(self) -> str:
        porta = self.comboPortaSerial.currentText()
        if porta == self._PLACEHOLDER_PORTA:
            return ""
        return porta

    def _baud_rate_selecionado(self) -> int:
        return self.comboBaudRate.currentData()

    def _timeout_digitado(self) -> int:
        """Converte o texto do campo; a faixa válida é conferida por PortaSerial."""
        texto = self.editTimeout.text().strip()
        if not texto:
            raise ErroConexaoSerial("Informe o timeout em milissegundos.")
        try:
            timeout_ms = int(texto)
        except ValueError:
            raise ErroConexaoSerial(
                "O timeout deve ser um número inteiro de milissegundos."
            ) from None
        if timeout_ms < 0:
            raise ErroConexaoSerial("O timeout não pode ser negativo.")
        return timeout_ms

    # ------------------------------------------------------------------
    # Reflexo do estado na interface
    # ------------------------------------------------------------------
    def _atualizar_estado_visual(self) -> None:
        conectado = self.porta_serial.conectado

        self.botaoConectar.setEnabled(not conectado)
        self.botaoDesconectar.setEnabled(conectado)

        # Os parâmetros ficam travados enquanto a porta está aberta: mudá-los
        # com a conexão de pé daria a falsa impressão de terem sido aplicados.
        for campo in (
            self.comboPortaSerial,
            self.comboBaudRate,
            self.editTimeout,
            self.botaoAtualizarPortas,
        ):
            campo.setEnabled(not conectado)

        self._atualizar_rotulo_status()

    def _atualizar_rotulo_status(self) -> None:
        parametros = self.porta_serial.parametros

        if parametros is None:
            self.labelStatusConexao.setText("Desconectado")
            self.labelStatusConexao.setStyleSheet(
                f"color: {self._COR_DESCONECTADO};"
            )
            self.labelDetalheConexao.setText("Nenhuma porta aberta")
            return

        inicio = self.dateInicioSessao.date().toString("dd/MM/yyyy")
        self.labelStatusConexao.setText(f"Conectado — {parametros.descricao}")
        self.labelStatusConexao.setStyleSheet(f"color: {self._COR_CONECTADO};")
        self.labelDetalheConexao.setText(
            f"Timeout de {parametros.timeout_ms} ms · "
            f"sessão de monitoramento iniciada em {inicio}"
        )

    def _avisar(self, mensagem: str) -> None:
        QMessageBox.warning(self, "Comunicação serial", mensagem)
