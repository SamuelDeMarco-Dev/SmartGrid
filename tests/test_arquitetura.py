"""Audita as importações por AST e a delegação de I/O pelas telas reais."""

import ast
from importlib.util import resolve_name
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, QEvent, QThread
from PyQt6.QtWidgets import QDialogButtonBox

import models.config_manager as modulo_config
from controllers.main_window_controller import MainWindowController
from database.connection import Database
from main import _montar_telas
from models.config_manager import ConfigManager, Configuracao
from models.porta_serial import PortaSerial
from models.repositorio_regras import RepositorioRegras
from views.dashboard_view import DashboardView
from views.historico_view import HistoricoView
from views.limites_dialog_view import LimitesDialogView
from views.serial_config_view import SerialConfigView

RAIZ = Path(__file__).resolve().parents[1]
DRIVERS = ("sqlite3", "serial", "database")
MODULOS_IO = (
    "models.config_manager", "models.database_worker", "models.porta_serial",
    "models.repositorio_dispositivos", "models.repositorio_eventos",
    "models.repositorio_leituras", "models.repositorio_regras",
    "models.serial_worker", "models.servico_corte", "models.servico_limites",
)


def importacoes(arquivo):
    arvore = ast.parse(arquivo.read_text(encoding="utf-8"), filename=str(arquivo))
    pacote = ".".join(arquivo.parent.relative_to(RAIZ).parts)
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            for alias in no.names:
                yield no.lineno, alias.name
        if isinstance(no, ast.ImportFrom):
            origem = "." * no.level + (no.module or "")
            origem = resolve_name(origem, pacote) if no.level else origem
            for alias in no.names:
                yield no.lineno, f"{origem}.{alias.name}"


def verificar_importacoes(pasta, proibidos):
    violacoes = []
    for arquivo in sorted((RAIZ / pasta).rglob("*.py")):
        for linha, nome in importacoes(arquivo):
            if any(nome == alvo or nome.startswith(alvo + ".") for alvo in proibidos):
                violacoes.append(f"{arquivo.relative_to(RAIZ)}:{linha} importa {nome}")
    assert not violacoes, "\n".join(violacoes)


def test_models_nao_importam_qtwidgets():
    verificar_importacoes("models", ("PyQt6.QtWidgets",))


def test_views_nao_importam_drivers_repositorios_workers_ou_controllers():
    verificar_importacoes("views", DRIVERS + MODULOS_IO + ("controllers",))


def test_controllers_coordenam_sem_importar_drivers():
    verificar_importacoes("controllers", DRIVERS)


def test_telas_delegam_consultas_descoberta_serial_e_gravacao_do_json(
    app, aplicacao_teste, aguardar_qt, serial_simulada, tmp_path, monkeypatch,
):
    config = Configuracao(porta_com="COM-TESTE")
    caminho = tmp_path / "config.json"
    monkeypatch.setattr(modulo_config, "CAMINHO_CONFIG", caminho)
    conectar_db = Database.conectar
    salvar_json = ConfigManager.salvar
    consultas, sondagens, gravacoes = [], [], []

    def conectar():
        consultas.append(QThread.currentThread())
        return conectar_db()

    def listar_portas():
        sondagens.append(QThread.currentThread())
        return [config.porta_com]

    def salvar(configuracao, **opcoes):
        gravacoes.append(QThread.currentThread())
        salvar_json(configuracao, **opcoes)

    monkeypatch.setattr(Database, "conectar", staticmethod(conectar))
    monkeypatch.setattr(PortaSerial, "listar_portas", staticmethod(listar_portas))
    monkeypatch.setattr(ConfigManager, "salvar", staticmethod(salvar))
    serial_simulada(config.porta_com)
    janela = MainWindowController()
    janela.setParent(janela.view)
    banco = aplicacao_teste.banco
    try:
        _montar_telas(janela, aplicacao_teste._servico_corte, RepositorioRegras(), config, aplicacao_teste)
        dashboard = janela.view.findChild(DashboardView)
        historico = janela.view.findChild(HistoricoView)
        painel_serial = janela.view.findChild(SerialConfigView)
        aguardar_qt(lambda: painel_serial.botaoConectar.isEnabled())
        janela.ir_para("historico")
        aguardar_qt(lambda: historico.botaoAtualizarHistorico.isEnabled())
        dashboard.botaoConfigurarLimites.click()
        limites = dashboard.findChild(LimitesDialogView)
        salvar_limites = limites.button_box.button(QDialogButtonBox.StandardButton.Save)
        aguardar_qt(salvar_limites.isEnabled)
        limites.reject()

        janela.ir_para("serial")
        painel_serial.botaoConectar.click()
        concluiu = []
        banco.executar(lambda: True, ao_concluir=concluiu.append)
        aguardar_qt(lambda: concluiu)

        assert len(consultas) >= 4
        assert all(thread is banco for thread in consultas)
        assert sondagens == gravacoes == [banco]
        assert painel_serial.thread() is dashboard.thread() is historico.thread() is app.thread()
        assert caminho.is_file()
    finally:
        janela.view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
