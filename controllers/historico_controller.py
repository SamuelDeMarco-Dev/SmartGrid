"""Controller da tela de historico de eventos."""

from PyQt6.QtCore import QDate
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMessageBox,
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
        self._conectar_acoes()
        self._conectar_barramento()
        self.aplicar_filtros()

    def atualizar_tabela(self, eventos: list[Evento] | None = None) -> None:
        """Recarrega a tabela com os eventos recebidos ou todos do repositorio."""
        if eventos is None:
            eventos = self.repositorio.listar()
        self.tabelaEventos.setRowCount(0)
        self.tabelaEventos.setRowCount(len(eventos))

        for linha, evento in enumerate(eventos):
            itens = [QTableWidgetItem(valor) for valor in evento.como_linha()]
            self._aplicar_estilo_evento(evento, itens)

            for coluna, item in enumerate(itens):
                self.tabelaEventos.setItem(linha, coluna, item)

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

    def limpar_historico(self) -> None:
        """Confirma e remove todos os eventos armazenados."""
        resposta = QMessageBox.question(
            self,
            "Limpar histórico",
            (
                "Deseja realmente remover todos os eventos do histórico?\n\n"
                "Esta ação não poderá ser desfeita durante esta execução."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if resposta != QMessageBox.StandardButton.Yes:
            return

        self.repositorio.limpar()
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

    def _conectar_acoes(self) -> None:
        self.botaoLimparHistorico.clicked.connect(self.limpar_historico)

    def _conectar_barramento(self) -> None:
        barramento.evento_registrado.connect(self.registrar_evento)

    def _tipo_selecionado(self) -> TipoEvento | None:
        texto = self.comboTipoEvento.currentText()
        return self._TIPOS_POR_TEXTO.get(texto)

    def _aplicar_estilo_evento(
        self, evento: Evento, itens: list[QTableWidgetItem]
    ) -> None:
        fundo, texto = self._cores_do_tipo(evento.tipo)
        item_tipo = itens[1]
        item_tipo.setBackground(QBrush(fundo))
        item_tipo.setForeground(QBrush(texto))

    def _cores_do_tipo(self, tipo: TipoEvento) -> tuple[QColor, QColor]:
        cores = {
            TipoEvento.ALERTA: (QColor("#FCE8E6"), QColor("#8A2D1C")),
            TipoEvento.COMANDO: (QColor("#E8F1FD"), QColor("#1D4F80")),
            TipoEvento.STATUS: (QColor("#ECEFF3"), QColor("#4B5563")),
        }
        return cores[tipo]
