"""Ponto de entrada da aplicação SmartGrid.

Este arquivo deve permanecer enxuto: nenhuma regra de negócio ou tratamento de
evento aqui. Toda a lógica vive em /controllers e /models.
"""

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from controllers.dashboard_controller import DashboardController
from controllers.historico_controller import HistoricoController
from controllers.main_window_controller import MainWindowController
from controllers.serial_config_controller import SerialConfigController
from models.aquisicao import ServicoAquisicao

FOLHA_DE_ESTILO = Path(__file__).parent / "ui" / "estilo.qss"


def _aplicar_estilo(app: QApplication) -> None:
    """Aplica a folha de estilo única compartilhada por todas as telas."""
    app.setStyleSheet(FOLHA_DE_ESTILO.read_text(encoding="utf-8"))


def _montar_telas(janela: MainWindowController) -> None:
    """Encaixa as telas nas páginas reservadas pelo shell da janela principal."""
    janela.registrar_pagina("dashboard", DashboardController())
    janela.registrar_pagina("serial", SerialConfigController())
    janela.registrar_pagina("historico", HistoricoController())
    janela.ir_para("dashboard")


def main() -> int:
    app = QApplication(sys.argv)
    _aplicar_estilo(app)

    # Fonte da telemetria: publica leituras no barramento enquanto a porta
    # serial estiver aberta. Fica sob a guarda da QApplication para viver
    # enquanto a aplicação estiver de pé.
    ServicoAquisicao(parent=app)

    janela = MainWindowController()
    _montar_telas(janela)
    janela.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
