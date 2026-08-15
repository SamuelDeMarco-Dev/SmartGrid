"""Controller da tela de historico de eventos."""

from PyQt6.QtCore import QDate
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidgetItem,
    QWidget,
)

from models.barramento import barramento
from models.evento import Evento, TipoEvento
from models.repositorio_eventos import RepositorioEventos
from ui.historico_ui import Ui_Historico


class HistoricoController(QWidget, Ui_Historico):
    """Tela que exibe os eventos armazenados no repositorio."""

    _TIPOS_POR_TEXTO: dict[str, TipoEvento | None] = {
        "Todos": None,
        TipoEvento.COMANDO.value: TipoEvento.COMANDO,
        TipoEvento.ALERTA.value: TipoEvento.ALERTA,
        TipoEvento.STATUS.value: TipoEvento.STATUS,
    }

    def __init__(self) -> None:
        super().__init__()
        self.setupUi(self)
        self.repositorio = RepositorioEventos()
        self._configurar_filtros()
        self._configurar_tabela()
        self._conectar_filtros()
        self._conectar_barramento()
        self.aplicar_filtros()

    def atualizar_tabela(self, eventos: list[Evento] | None = None) -> None:
        """Recarrega a tabela com os eventos recebidos ou todos do repositorio."""
        if eventos is None:
            eventos = self.repositorio.listar()
        self.tabelaEventos.setRowCount(0)
        self.tabelaEventos.setRowCount(len(eventos))

        for linha, evento in enumerate(eventos):
            for coluna, valor in enumerate(evento.como_linha()):
                self.tabelaEventos.setItem(linha, coluna, QTableWidgetItem(valor))

        self.tabelaEventos.resizeRowsToContents()

    def aplicar_filtros(self) -> None:
        """Atualiza a tabela usando os filtros selecionados na tela."""
        eventos = self.repositorio.filtrar(
            tipo=self._tipo_selecionado(),
            data_inicio=self.dateFiltroInicio.date().toPyDate(),
        )
        self.atualizar_tabela(eventos)

    def registrar_evento(self, evento: Evento) -> None:
        """Armazena um evento recebido e reaplica os filtros atuais."""
        self.repositorio.adicionar(evento)
        self.aplicar_filtros()

    def _configurar_filtros(self) -> None:
        self.dateFiltroInicio.setDate(QDate.currentDate())

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

    def _conectar_filtros(self) -> None:
        self.comboTipoEvento.currentTextChanged.connect(self.aplicar_filtros)
        self.dateFiltroInicio.dateChanged.connect(self.aplicar_filtros)

    def _conectar_barramento(self) -> None:
        barramento.evento_registrado.connect(self.registrar_evento)

    def _tipo_selecionado(self) -> TipoEvento | None:
        texto = self.comboTipoEvento.currentText()
        return self._TIPOS_POR_TEXTO.get(texto)
