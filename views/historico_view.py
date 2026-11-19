"""Widgets e sinais do histórico; nenhuma operação de persistência nesta View."""

from __future__ import annotations

from collections import deque
from datetime import date

from PyQt6.QtCore import QDate, QSignalBlocker, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import QHeaderView, QMessageBox, QTableWidgetItem, QWidget

from models.evento import TipoEvento
from models.historico import EventoHistorico
from ui.historico_ui import Ui_Historico


class HistoricoView(QWidget, Ui_Historico):
    """Apresenta eventos recebidos do controller e publica ações do operador."""

    filtros_alterados = pyqtSignal()
    limpar_solicitado = pyqtSignal()
    atualizar_solicitado = pyqtSignal()
    tabela_atualizada = pyqtSignal()
    encerrada = pyqtSignal()

    LINHAS_POR_LOTE = 100

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setupUi(self)
        self.dateFiltroInicio.setMinimumDate(QDate(2000, 1, 1))
        self.dateFiltroInicio.setDate(self.dateFiltroInicio.minimumDate())
        self.progressoHistorico.hide()
        self._pendentes: deque[tuple[int, EventoHistorico]] = deque()
        self._inserindo = False
        self._timer = QTimer(self)
        self._timer.setInterval(1)
        self._timer.timeout.connect(self._preencher_lote)
        self._cores = {
            TipoEvento.ALERTA: (QBrush(QColor("#FCE8E6")), QBrush(QColor("#8A2D1C"))),
            TipoEvento.COMANDO: (QBrush(QColor("#E8F1FD")), QBrush(QColor("#1D4F80"))),
            TipoEvento.STATUS: (QBrush(QColor("#ECEFF3")), QBrush(QColor("#4B5563"))),
        }
        self._configurar_tabela()
        self.comboTipoEvento.currentIndexChanged.connect(self.filtros_alterados)
        self.dateFiltroInicio.dateChanged.connect(self.filtros_alterados)
        self.comboDispositivo.currentIndexChanged.connect(self.filtros_alterados)
        self.botaoAtualizarHistorico.clicked.connect(self.atualizar_solicitado)
        self.botaoLimparHistorico.clicked.connect(self._confirmar_limpeza)

    def _configurar_tabela(self) -> None:
        # Larguras interativas e altura fixa evitam medir milhares de células.
        cabecalho = self.tabelaEventos.horizontalHeader()
        cabecalho.setStretchLastSection(False)
        cabecalho.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for coluna, largura in ((0, 170), (1, 130), (3, 155), (4, 150)):
            self.tabelaEventos.setColumnWidth(coluna, largura)
        cabecalho.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tabelaEventos.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)

    def filtros_selecionados(self) -> tuple[str, date | None, int | None]:
        data = self.dateFiltroInicio.date()
        return (
            self.comboTipoEvento.currentText(),
            data.toPyDate() if data.isValid() else None,
            self.comboDispositivo.currentData(),
        )

    def mostrar_dispositivos(self, dispositivos: list[tuple[int, str]]) -> None:
        selecionado = self.comboDispositivo.currentData()
        with QSignalBlocker(self.comboDispositivo):
            self.comboDispositivo.clear()
            self.comboDispositivo.addItem("Todos os dispositivos", None)
            for id_, tag in dispositivos:
                self.comboDispositivo.addItem(tag, id_)
            indice = self.comboDispositivo.findData(selecionado)
            if indice < 0:
                self.comboDispositivo.addItem(f"Dispositivo {selecionado} (indisponível)", selecionado)
                indice = self.comboDispositivo.count() - 1
            self.comboDispositivo.setCurrentIndex(indice)

    def mostrar_eventos(self, registros: list[EventoHistorico]) -> None:
        self.cancelar_preenchimento()
        self.tabelaEventos.setRowCount(0)
        self.tabelaEventos.setRowCount(len(registros))
        self._pendentes.extend(enumerate(registros))
        self._timer.start()

    def inserir_eventos(self, insercoes: list[tuple[int, EventoHistorico]]) -> None:
        self._inserindo = True
        self._pendentes.extend(insercoes)
        self._timer.start()

    def cancelar_preenchimento(self) -> None:
        self._timer.stop()
        self._pendentes.clear()
        self._inserindo = False

    def esvaziar_tabela(self) -> None:
        self.cancelar_preenchimento()
        self.tabelaEventos.setRowCount(0)

    def _preencher_lote(self) -> None:
        self.tabelaEventos.setUpdatesEnabled(False)
        try:
            for _ in range(min(self.LINHAS_POR_LOTE, len(self._pendentes))):
                linha, registro = self._pendentes.popleft()
                if self._inserindo:
                    self.tabelaEventos.insertRow(linha)
                for coluna, texto in enumerate(registro.colunas):
                    item = QTableWidgetItem(texto)
                    item.setToolTip(texto)
                    if coluna == 1:
                        fundo, cor = self._cores[registro.evento.tipo]
                        item.setBackground(fundo)
                        item.setForeground(cor)
                    self.tabelaEventos.setItem(linha, coluna, item)
        finally:
            self.tabelaEventos.setUpdatesEnabled(True)
        if not self._pendentes:
            self._timer.stop()
            self._inserindo = False
            self.tabela_atualizada.emit()

    def mostrar_carregando(self, mensagem: str) -> None:
        self.labelStatusHistorico.setStyleSheet("")
        self.labelStatusHistorico.setText(mensagem)
        self.progressoHistorico.show()
        self.botaoLimparHistorico.setEnabled(False)
        self.botaoAtualizarHistorico.setEnabled(False)

    def mostrar_mensagem(self, mensagem: str) -> None:
        self.progressoHistorico.hide()
        self.botaoLimparHistorico.setEnabled(True)
        self.botaoAtualizarHistorico.setEnabled(True)
        self.labelStatusHistorico.setStyleSheet("")
        self.labelStatusHistorico.setText(mensagem)

    def mostrar_erro(self, mensagem: str) -> None:
        self.mostrar_mensagem(mensagem)
        self.labelStatusHistorico.setStyleSheet("color: #C62828;")

    def _confirmar_limpeza(self) -> None:
        resposta = QMessageBox.question(
            self, "Limpar histórico",
            "Deseja realmente remover todos os eventos do histórico?\n\n"
            "Esta ação não poderá ser desfeita durante esta execução.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resposta == QMessageBox.StandardButton.Yes:
            self.limpar_solicitado.emit()

    def showEvent(self, evento) -> None:
        super().showEvent(evento)
        self.atualizar_solicitado.emit()

    def closeEvent(self, evento) -> None:
        self.cancelar_preenchimento()
        self.encerrada.emit()
        super().closeEvent(evento)
