"""Controller da janela principal — navegação, barra de status e modo de teste.

A ``MainWindowView`` desenha o shell; este controller decide qual página
mostrar, formata o status da conexão publicado no barramento e liga o atalho
``Ctrl+Shift+F`` ao modo de falha simulada. Nenhuma regra de telemetria,
serial, limites ou histórico vive aqui.
"""

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QWidget

from models.barramento import barramento
from models.modo_falha import ModoFalhaSimulada
from views.main_window_view import MainWindowView


class MainWindowController(QObject):
    """Janela principal do supervisório SmartGrid."""

    PAGINAS = MainWindowView.PAGINAS

    ATALHO_MODO_FALHA = "Ctrl+Shift+F"

    def __init__(self, view: MainWindowView | None = None) -> None:
        super().__init__()
        self.view = view or MainWindowView()
        self.view.pagina_solicitada.connect(self.ir_para)
        barramento.status_conexao_alterado.connect(self.atualizar_status_conexao)
        self.atualizar_status_conexao(False, "Desconectado")
        self.ir_para("dashboard")

    def registrar_pagina(self, nome: str, widget: QWidget) -> None:
        """Encaixa a View de uma tela na página reservada `nome`."""
        self.view.registrar_pagina(nome, widget)

    def ir_para(self, nome: str) -> None:
        self.view.mostrar_pagina(nome)

    def atualizar_status_conexao(self, conectado: bool, descricao: str) -> None:
        """Reflete na barra de status o estado da comunicação serial."""
        prefixo = "Conectado" if conectado else "Desconectado"
        self.view.mostrar_status(f"{prefixo} — {descricao}")

    def usar_modo_falha(self, modo: ModoFalhaSimulada) -> None:
        """Liga o atalho Ctrl+Shift+F e o selo da barra de status ao modo de teste."""
        self.view.habilitar_atalho_modo_falha(self.ATALHO_MODO_FALHA)
        self.view.modo_falha_solicitado.connect(modo.alternar)
        modo.alterado.connect(self.view.mostrar_selo_modo_teste)
        self.view.mostrar_selo_modo_teste(modo.ativo)

    def show(self) -> None:
        self.view.show()
