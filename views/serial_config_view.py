"""View da Comunicação Serial — desenha a tela e devolve as ações como sinais.

Não valida, não grava ``config.json`` e não fala com a porta: entrega ao
``SerialConfigController`` o texto bruto dos campos e mostra o que ele mandar
(portas, velocidades, fase do handshake, velocidade negociada).
"""

from __future__ import annotations

from datetime import date

from PyQt6.QtCore import QDate, Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QMessageBox, QWidget

from ui.serial_config_ui import Ui_SerialConfig

#: Cor das etapas do handshake que ainda não aconteceram.
COR_ETAPA_PENDENTE = "#9CA3AF"


class SerialConfigView(QWidget, Ui_SerialConfig):
    """Tela de configuração e acompanhamento do link serial."""

    conectar_solicitado = pyqtSignal()
    desconectar_solicitado = pyqtSignal()
    atualizar_portas_solicitado = pyqtSignal()
    data_sessao_alterada = pyqtSignal()

    #: Item neutro no topo do combo de portas: "nenhuma porta escolhida".
    PLACEHOLDER_PORTA = "— Selecione a porta —"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setupUi(self)
        # Editável para aceitar a porta da placa virtual (/dev/ttysNNN), que
        # não aparece na enumeração do sistema.
        self.comboPortaSerial.setEditable(True)
        # O indicador do link é um selo do tamanho do texto, não uma faixa.
        self.layoutStatusSerial.setAlignment(self.labelStatusConexao, Qt.AlignmentFlag.AlignLeft)
        self._etapas: list[QLabel] = []

        self.botaoConectar.clicked.connect(self.conectar_solicitado)
        self.botaoDesconectar.clicked.connect(self.desconectar_solicitado)
        self.botaoAtualizarPortas.clicked.connect(self.atualizar_portas_solicitado)
        self.dateInicioSessao.dateChanged.connect(self.data_sessao_alterada)

    # ------------------------------------------------------------------
    # Montagem (chamada pelo controller)
    # ------------------------------------------------------------------
    def preencher_velocidades(self, velocidades: list[int], selecionada: int) -> None:
        self.comboBaudRate.clear()
        for valor in velocidades:
            self.comboBaudRate.addItem(str(valor), valor)
        self.comboBaudRate.setCurrentIndex(max(self.comboBaudRate.findData(selecionada), 0))

    def definir_timeout(self, timeout_ms: int) -> None:
        self.editTimeout.setText(str(timeout_ms))

    def definir_taxa_amostragem(self, taxa_ms: int) -> None:
        self.editTaxaAmostragem.setText(str(taxa_ms))

    def definir_data_sessao(self, dia: date, maxima: date) -> None:
        self.dateInicioSessao.setMaximumDate(QDate(maxima))
        self.dateInicioSessao.setDate(QDate(dia))

    def definir_etapas(self, rotulos: list[str]) -> None:
        """Cria a faixa de etapas do handshake, da primeira à telemetria."""
        for etiqueta in self._etapas:
            self.layoutEtapasLink.removeWidget(etiqueta)
            etiqueta.deleteLater()
        self._etapas = []
        for indice, rotulo in enumerate(rotulos):
            if indice:
                seta = QLabel("→", self.frameStatusSerial)
                seta.setStyleSheet(f"color: {COR_ETAPA_PENDENTE};")
                self.layoutEtapasLink.addWidget(seta)
            etiqueta = QLabel(rotulo, self.frameStatusSerial)
            self.layoutEtapasLink.addWidget(etiqueta)
            self._etapas.append(etiqueta)
        self.layoutEtapasLink.addStretch(1)

    # ------------------------------------------------------------------
    # Leitura do formulário (valores brutos, sem validação)
    # ------------------------------------------------------------------
    def porta(self) -> str:
        porta = self.comboPortaSerial.currentText()
        return "" if porta == self.PLACEHOLDER_PORTA else porta

    def velocidade(self) -> int | None:
        return self.comboBaudRate.currentData()

    def timeout_digitado(self) -> str:
        return self.editTimeout.text()

    def taxa_digitada(self) -> str:
        return self.editTaxaAmostragem.text()

    def data_sessao(self) -> date:
        return self.dateInicioSessao.date().toPyDate()

    # ------------------------------------------------------------------
    # Exibição
    # ------------------------------------------------------------------
    def mostrar_buscando_portas(self) -> None:
        self.labelDetalheConexao.setText("Buscando portas…")

    def preencher_portas(self, portas: list[str], selecionada: str) -> None:
        """Lista as portas e seleciona ``selecionada`` (ou o item neutro)."""
        self.comboPortaSerial.clear()
        self.comboPortaSerial.addItem(self.PLACEHOLDER_PORTA)
        self.comboPortaSerial.addItems(portas)
        self.comboPortaSerial.setCurrentIndex(max(self.comboPortaSerial.findText(selecionada), 0))

    def habilitar_controles(self, *, campos: bool, conectar: bool, desconectar: bool) -> None:
        for campo in (
            self.comboPortaSerial,
            self.comboBaudRate,
            self.editTimeout,
            self.editTaxaAmostragem,
            self.botaoAtualizarPortas,
        ):
            campo.setEnabled(campos)
        self.botaoConectar.setEnabled(conectar)
        self.botaoDesconectar.setEnabled(desconectar)

    def mostrar_estado_link(self, rotulo: str, cor: str, detalhe: str) -> None:
        self.labelStatusConexao.setText(rotulo)
        self.labelStatusConexao.setStyleSheet(
            f"color: #FFFFFF; background-color: {cor}; border-radius: 4px; padding: 4px 10px;"
        )
        self.labelDetalheConexao.setText(detalhe)

    def mostrar_progresso(self, atual: int, cor_atual: str, cor_concluida: str) -> None:
        """Etapas antes de ``atual`` ficam concluídas; ``atual`` em destaque."""
        for indice, etiqueta in enumerate(self._etapas):
            if indice < atual:
                estilo = f"color: {cor_concluida};"
            elif indice == atual:
                estilo = (
                    f"color: #FFFFFF; background-color: {cor_atual}; "
                    "border-radius: 4px; padding: 2px 6px; font-weight: bold;"
                )
            else:
                estilo = f"color: {COR_ETAPA_PENDENTE};"
            etiqueta.setStyleSheet(estilo)

    def mostrar_velocidade_negociada(self, texto: str) -> None:
        self.labelVelocidadeNegociada.setText(f"Velocidade negociada: {texto}")

    # --- diálogos ------------------------------------------------------
    def avisar(self, mensagem: str) -> None:
        QMessageBox.warning(self, "Comunicação serial", mensagem)

    def avisar_erro(self, mensagem: str) -> None:
        QMessageBox.critical(self, "Comunicação serial", mensagem)
