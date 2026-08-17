"""Controller do Painel de Configuração da Comunicação Serial.

Traduz o formulário da tela em chamadas ao serviço :class:`PortaSerial` e
publica o resultado no barramento — a janela principal usa
``status_conexao_alterado`` para alimentar a ``QStatusBar`` e a tela de
Histórico recebe os eventos de conexão e desconexão.

Nesta entrega a conexão é simulada: nenhum byte trafega.
"""

from PyQt6.QtCore import QDate
from PyQt6.QtWidgets import QMessageBox, QWidget

from models.barramento import barramento
from models.evento import TipoEvento
from models.porta_serial import (
    BAUD_RATE_PADRAO,
    BAUD_RATES,
    ErroConexaoSerial,
    PortaSerial,
)
from ui.serial_config_ui import Ui_SerialConfig


class SerialConfigController(QWidget, Ui_SerialConfig):
    """Tela de configuração e abertura da porta serial."""

    #: Item neutro no topo do combo. Existe para que "conectar sem escolher
    #: porta" continue sendo um caminho possível — e, portanto, validável.
    _PLACEHOLDER_PORTA = "— Selecione a porta —"

    #: Mesmas cores do badge de disjuntor, para a tela não destoar do resto.
    _COR_CONECTADO = "#2E7D32"
    _COR_DESCONECTADO = "#C62828"

    def __init__(self) -> None:
        super().__init__()
        self.setupUi(self)
        self.porta_serial = PortaSerial()
        self._preencher_baud_rates()
        self._configurar_data_sessao()
        self.atualizar_portas()
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
                timeout_ms=self.spinTimeout.value(),
            )
        except ErroConexaoSerial as erro:
            self._avisar(str(erro))
            return

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

        self._atualizar_estado_visual()
        barramento.status_conexao_alterado.emit(
            False, f"Porta {parametros.porta} liberada"
        )
        barramento.registrar_evento(
            TipoEvento.STATUS,
            f"Comunicação serial encerrada em {parametros.porta}",
            parametros.descricao,
        )

    # ------------------------------------------------------------------
    # Montagem inicial
    # ------------------------------------------------------------------
    def _preencher_baud_rates(self) -> None:
        for valor in BAUD_RATES:
            self.comboBaudRate.addItem(str(valor), valor)
        self.comboBaudRate.setCurrentIndex(
            self.comboBaudRate.findData(BAUD_RATE_PADRAO)
        )

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
            self.spinTimeout,
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
