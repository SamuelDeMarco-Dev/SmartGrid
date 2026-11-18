"""View da janela principal — shell com menu lateral, páginas e barra de status."""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QLabel, QMainWindow, QWidget

from ui.main_window_ui import Ui_MainWindow


class MainWindowView(QMainWindow, Ui_MainWindow):
    """Desenha o shell e emite a navegação pedida pelo operador."""

    #: Nome lógico de cada página -> índice correspondente no QStackedWidget.
    PAGINAS = {
        "dashboard": 0,
        "serial": 1,
        "historico": 2,
    }

    pagina_solicitada = pyqtSignal(str)
    modo_falha_solicitado = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setupUi(self)
        self._criar_selo_modo_teste()
        self._conectar_navegacao()

    # ------------------------------------------------------------------
    # Exibição
    # ------------------------------------------------------------------
    def registrar_pagina(self, nome: str, widget: QWidget) -> None:
        """Substitui a página reservada `nome` pela tela real recebida."""
        indice = self._indice_da_pagina(nome)
        placeholder = self.stackedPaginas.widget(indice)
        self.stackedPaginas.removeWidget(placeholder)
        placeholder.deleteLater()
        self.stackedPaginas.insertWidget(indice, widget)

    def mostrar_pagina(self, nome: str) -> None:
        """Exibe a página indicada e sincroniza o botão de navegação."""
        self.stackedPaginas.setCurrentIndex(self._indice_da_pagina(nome))
        self._botao_da_pagina(nome).setChecked(True)

    def mostrar_status(self, texto: str) -> None:
        self.statusbar.showMessage(texto)

    def mostrar_selo_modo_teste(self, visivel: bool) -> None:
        self.seloModoTeste.setVisible(visivel)

    def habilitar_atalho_modo_falha(self, sequencia: str) -> None:
        self._atalho_modo_falha = QShortcut(QKeySequence(sequencia), self)
        self._atalho_modo_falha.activated.connect(self.modo_falha_solicitado)

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------
    def _criar_selo_modo_teste(self) -> None:
        # Widget permanente: as mensagens da conexão serial (showMessage) não o
        # apagam, então o selo fica visível enquanto o modo estiver ativo.
        self.seloModoTeste = QLabel("MODO DE TESTE: falha simulada")
        self.seloModoTeste.setStyleSheet(
            "background-color: #C62828; color: #FFFFFF; font-weight: bold;"
            "padding: 2px 8px; border-radius: 3px;"
        )
        self.seloModoTeste.setVisible(False)
        self.statusbar.addPermanentWidget(self.seloModoTeste)

    def _conectar_navegacao(self) -> None:
        for nome, botao, acao in (
            ("dashboard", self.botaoDashboard, self.actionDashboard),
            ("serial", self.botaoSerial, self.actionSerial),
            ("historico", self.botaoHistorico, self.actionHistorico),
        ):
            botao.clicked.connect(lambda _=False, n=nome: self.pagina_solicitada.emit(n))
            acao.triggered.connect(lambda _=False, n=nome: self.pagina_solicitada.emit(n))
        self.actionSair.triggered.connect(self.close)

    def _indice_da_pagina(self, nome: str) -> int:
        if nome not in self.PAGINAS:
            raise KeyError(f"Página desconhecida: {nome!r}. Disponíveis: {sorted(self.PAGINAS)}")
        return self.PAGINAS[nome]

    def _botao_da_pagina(self, nome: str):
        return {
            "dashboard": self.botaoDashboard,
            "serial": self.botaoSerial,
            "historico": self.botaoHistorico,
        }[nome]
