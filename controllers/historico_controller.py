"""Controller da tela de historico de eventos."""

import logging
import sqlite3
from datetime import date

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

logger = logging.getLogger("smartgrid")


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
            try:
                eventos = self.repositorio.listar_todos()
            except sqlite3.Error as erro:
                self._tratar_erro_banco("carregar o histórico", erro)
                return
            except Exception as erro:
                self._tratar_erro_inesperado("carregar o histórico", erro)
                return

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
        try:
            data_inicio = self._data_inicio_validada()
        except ValueError as erro:
            self._tratar_data_invalida(str(erro))
            return

        try:
            eventos = self.repositorio.filtrar(
                tipo=self._tipo_selecionado(),
                data_inicio=data_inicio,
            )
        except sqlite3.Error as erro:
            self._tratar_erro_banco("filtrar o histórico", erro)
            return
        except Exception as erro:
            self._tratar_erro_inesperado("filtrar o histórico", erro)
            return

        self.atualizar_tabela(eventos)

    def atualizar_apos_evento_persistido(self, evento: Evento) -> None:
        """Atualiza a tela depois que um evento já foi persistido."""
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

        try:
            quantidade_removida = self.repositorio.limpar()
        except sqlite3.Error as erro:
            self._tratar_erro_banco("limpar o histórico", erro)
            return
        except Exception as erro:
            self._tratar_erro_inesperado("limpar o histórico", erro)
            return

        logger.info("Histórico limpo (%d registros)", quantidade_removida)
        self.aplicar_filtros()

    def _configurar_filtros(self) -> None:
        self.dateFiltroInicio.setMinimumDate(QDate(2000, 1, 1))
        self.dateFiltroInicio.setDate(self.dateFiltroInicio.minimumDate())

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
        barramento.evento_persistido.connect(
            self.atualizar_apos_evento_persistido
        )

    def showEvent(self, evento) -> None:
        """Recarrega os eventos persistidos sempre que a tela é exibida."""
        self.aplicar_filtros()
        super().showEvent(evento)

    def _tipo_selecionado(self) -> TipoEvento | None:
        texto = self.comboTipoEvento.currentText()
        return self._TIPOS_POR_TEXTO.get(texto)

    def _data_inicio_validada(self) -> date:
        data_qt = self.dateFiltroInicio.date()
        if not data_qt.isValid():
            raise ValueError("Informe uma data inicial válida.")

        if data_qt > QDate.currentDate():
            raise ValueError(
                "A data inicial não pode ser posterior à data atual."
            )

        return data_qt.toPyDate()

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

    def _tratar_erro_banco(self, acao: str, erro: sqlite3.Error) -> None:
        logger.error("Não foi possível %s: %s", acao, erro)
        QMessageBox.critical(
            self,
            "Erro no banco de dados",
            (
                f"Não foi possível {acao}.\n\n"
                "O banco de dados está temporariamente indisponível ou em uso. "
                "Tente novamente em instantes."
            ),
        )

    def _tratar_data_invalida(self, mensagem: str) -> None:
        logger.warning("Filtro de data inválido: %s", mensagem)
        QMessageBox.warning(self, "Filtro de data", mensagem)

    def _tratar_erro_inesperado(self, acao: str, erro: Exception) -> None:
        logger.error("Erro inesperado ao %s: %s", acao, erro)
        QMessageBox.critical(
            self,
            "Erro inesperado",
            (
                f"Não foi possível {acao}.\n\n"
                "Tente novamente. Se o problema persistir, consulte os logs."
            ),
        )
