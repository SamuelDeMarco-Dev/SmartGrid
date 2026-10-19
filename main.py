"""Ponto de entrada da aplicação SmartGrid.

Este arquivo deve permanecer enxuto: nenhuma regra de negócio ou tratamento de
evento aqui. Toda a lógica vive em /controllers e /models.

Ordem de inicialização: log → config.json → banco → GravadorEventos → telas.
"""

import sqlite3
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QMessageBox

from controllers.dashboard_controller import DashboardController
from controllers.historico_controller import HistoricoController
from controllers.main_window_controller import MainWindowController
from controllers.serial_config_controller import SerialConfigController
from database.connection import Database
from models.aquisicao import ServicoAquisicao
from models.config_manager import ConfigManager, Configuracao
from models.gravador_eventos import GravadorEventos
from models.log_config import configurar_log, obter_logger
from models.repositorio_dispositivos import RepositorioDispositivos
from models.repositorio_eventos import RepositorioEventos
from models.repositorio_regras import RepositorioRegras
from models.servico_corte import ServicoCorteEmergencial

FOLHA_DE_ESTILO = Path(__file__).parent / "ui" / "estilo.qss"

logger = obter_logger()


def _aplicar_estilo(app: QApplication) -> None:
    """Aplica a folha de estilo única compartilhada por todas as telas."""
    app.setStyleSheet(FOLHA_DE_ESTILO.read_text(encoding="utf-8"))


def _iniciar_banco() -> bool:
    """Cria/valida o SQLite. Em falha avisa o operador e devolve False."""
    try:
        Database.init_db()
    except sqlite3.Error as erro:
        logger.error("Falha ao inicializar o banco de dados: %s.", erro)
        QMessageBox.critical(
            None,
            "SmartGrid — Banco de dados",
            "Não foi possível abrir o banco de dados da aplicação.\n\n"
            f"Motivo: {erro}\n\nA aplicação será encerrada.",
        )
        return False
    return True


def _montar_telas(
    janela: MainWindowController,
    servico_corte: ServicoCorteEmergencial,
    repositorio_regras: RepositorioRegras,
    config: Configuracao,
) -> None:
    """Encaixa as telas nas páginas reservadas pelo shell da janela principal."""
    janela.registrar_pagina(
        "dashboard", DashboardController(servico_corte, repositorio_regras)
    )
    janela.registrar_pagina("serial", SerialConfigController(config))
    janela.registrar_pagina("historico", HistoricoController())
    janela.ir_para("dashboard")


def main() -> int:
    # Antes de tudo, para que a própria inicialização fique registrada.
    configurar_log()
    config = ConfigManager.carregar()

    app = QApplication(sys.argv)
    _aplicar_estilo(app)

    if not _iniciar_banco():
        return 1

    repo_eventos = RepositorioEventos()
    # Único ponto de gravação dos eventos publicados no barramento.
    GravadorEventos(repo_eventos, parent=app)

    servico_corte = ServicoCorteEmergencial(RepositorioDispositivos(), repo_eventos)
    repositorio_regras = RepositorioRegras()

    # Fonte da telemetria: publica leituras no barramento enquanto a porta
    # serial estiver aberta. Fica sob a guarda da QApplication para viver
    # enquanto a aplicação estiver de pé.
    ServicoAquisicao(parent=app)

    janela = MainWindowController()
    _montar_telas(janela, servico_corte, repositorio_regras, config)
    janela.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
