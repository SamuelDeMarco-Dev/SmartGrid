"""Ponto de entrada da aplicação SmartGrid.

Este arquivo deve permanecer enxuto: nenhuma regra de negócio ou tratamento de
evento aqui. Toda a lógica vive em /controllers e /models.
"""

import sys

from PyQt6.QtWidgets import QApplication

from controllers.main_window_controller import MainWindowController


def main() -> int:
    app = QApplication(sys.argv)
    janela = MainWindowController()
    janela.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
