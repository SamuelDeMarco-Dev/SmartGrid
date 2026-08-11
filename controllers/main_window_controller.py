"""Controller da janela principal — shell que hospeda as telas da aplicação.

Responsabilidades desta classe:
    - montar a janela principal a partir do layout compilado em ui/main_window_ui.py;
    - conectar os Signals & Slots da navegação;
    - oferecer à equipe os métodos `registrar_pagina` e `ir_para`, usados na
      integração das telas do M1.

Nenhuma regra de negócio de telemetria, serial, limites ou histórico vive aqui.
"""

from PyQt6.QtWidgets import QMainWindow, QWidget

from ui.main_window_ui import Ui_MainWindow


class MainWindowController(QMainWindow, Ui_MainWindow):
    """Janela principal do supervisório SmartGrid."""

    #: Nome lógico de cada página -> índice correspondente no QStackedWidget.
    #: É o contrato usado pelas telas do M1 para se registrarem no shell.
    PAGINAS = {
        "dashboard": 0,
        "serial": 1,
        "historico": 2,
    }

    def __init__(self) -> None:
        super().__init__()
        self.setupUi(self)
        self._conectar_navegacao()
        self.atualizar_status_conexao(False, "Desconectado")
        self.ir_para("dashboard")

    # ------------------------------------------------------------------
    # API pública consumida pelas demais telas
    # ------------------------------------------------------------------
    def registrar_pagina(self, nome: str, widget: QWidget) -> None:
        """Substitui a página reservada `nome` pela tela real recebida.

        Usado na integração do M2:

            janela.registrar_pagina("dashboard", DashboardController())
        """
        indice = self._indice_da_pagina(nome)
        placeholder = self.stackedPaginas.widget(indice)
        self.stackedPaginas.removeWidget(placeholder)
        placeholder.deleteLater()
        self.stackedPaginas.insertWidget(indice, widget)

    def ir_para(self, nome: str) -> None:
        """Exibe a página indicada e sincroniza o botão de navegação."""
        indice = self._indice_da_pagina(nome)
        self.stackedPaginas.setCurrentIndex(indice)
        self._botao_da_pagina(nome).setChecked(True)

    def atualizar_status_conexao(self, conectado: bool, descricao: str) -> None:
        """Reflete na barra de status o estado da comunicação serial.

        A tela de Comunicação Serial alimenta este método através do
        barramento de eventos.
        """
        prefixo = "Conectado" if conectado else "Desconectado"
        self.statusbar.showMessage(f"{prefixo} — {descricao}")

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------
    def _conectar_navegacao(self) -> None:
        self.botaoDashboard.clicked.connect(lambda: self.ir_para("dashboard"))
        self.botaoSerial.clicked.connect(lambda: self.ir_para("serial"))
        self.botaoHistorico.clicked.connect(lambda: self.ir_para("historico"))

        self.actionDashboard.triggered.connect(lambda: self.ir_para("dashboard"))
        self.actionSerial.triggered.connect(lambda: self.ir_para("serial"))
        self.actionHistorico.triggered.connect(lambda: self.ir_para("historico"))
        self.actionSair.triggered.connect(self.close)

    def _indice_da_pagina(self, nome: str) -> int:
        if nome not in self.PAGINAS:
            raise KeyError(
                f"Página desconhecida: {nome!r}. Disponíveis: {sorted(self.PAGINAS)}"
            )
        return self.PAGINAS[nome]

    def _botao_da_pagina(self, nome: str):
        botoes = {
            "dashboard": self.botaoDashboard,
            "serial": self.botaoSerial,
            "historico": self.botaoHistorico,
        }
        return botoes[nome]
