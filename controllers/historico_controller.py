"""Controller da tela de historico de eventos."""

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidgetItem,
    QWidget,
)

from models.repositorio_eventos import RepositorioEventos
from ui.historico_ui import Ui_Historico


class HistoricoController(QWidget, Ui_Historico):
    """Tela que exibe os eventos armazenados no repositorio."""

    def __init__(self) -> None:
        super().__init__()
        self.setupUi(self)
        self.repositorio = RepositorioEventos()
        self._configurar_tabela()
        self.atualizar_tabela()

    def atualizar_tabela(self) -> None:
        """Recarrega a tabela com os eventos atuais do repositorio."""
        eventos = self.repositorio.listar()

        self.tabelaEventos.setRowCount(0)
        self.tabelaEventos.setRowCount(len(eventos))

        for linha, evento in enumerate(eventos):
            for coluna, valor in enumerate(evento.como_linha()):
                self.tabelaEventos.setItem(linha, coluna, QTableWidgetItem(valor))

        self.tabelaEventos.resizeRowsToContents()

    def _configurar_tabela(self) -> None:
        self.tabelaEventos.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.tabelaEventos.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )

        cabecalho = self.tabelaEventos.horizontalHeader()
        cabecalho.setStretchLastSection(False)
        cabecalho.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        cabecalho.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        cabecalho.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        cabecalho.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
