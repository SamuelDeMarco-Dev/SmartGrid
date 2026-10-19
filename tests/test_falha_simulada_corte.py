"""Modo de falha simulada ligado ao corte transacional (issue #50)."""

import logging
import sqlite3

import pytest

import database.connection as modulo_conexao
import models.log_config as log_config
from controllers.main_window_controller import MainWindowController
from database.connection import Database
from main import _ligar_modo_falha
from models.estado_disjuntor import EstadoDisjuntor
from models.modo_falha import ModoFalhaSimulada
from models.repositorio_dispositivos import RepositorioDispositivos
from models.repositorio_eventos import RepositorioEventos
from models.servico_corte import FalhaCorteEmergencial, ServicoCorteEmergencial

MENSAGEM_ROLLBACK = (
    "[ERROR] Falha na operação de corte emergencial. "
    "Rollback executado: Falha simulada entre corte e histórico."
)


@pytest.fixture
def ambiente_temporario(tmp_path, monkeypatch):
    caminho_db = tmp_path / "data" / "smartgrid.db"
    pasta_logs = tmp_path / "logs"
    caminho_log = pasta_logs / "smartgrid.log"

    monkeypatch.setattr(modulo_conexao, "CAMINHO_DB", caminho_db)
    monkeypatch.setattr(log_config, "PASTA_LOGS", pasta_logs)
    monkeypatch.setattr(log_config, "ARQUIVO_LOG", caminho_log)

    logger_raiz = logging.getLogger()
    handlers_anteriores = list(logger_raiz.handlers)
    nivel_anterior = logger_raiz.level
    for handler in handlers_anteriores:
        logger_raiz.removeHandler(handler)

    try:
        log_config.configurar_log()
        Database.init_db()
        yield caminho_db, caminho_log
    finally:
        for handler in list(logger_raiz.handlers):
            logger_raiz.removeHandler(handler)
            handler.close()
        for handler in handlers_anteriores:
            logger_raiz.addHandler(handler)
        logger_raiz.setLevel(nivel_anterior)


@pytest.fixture
def cenario(app, ambiente_temporario):
    """Janela, serviço e modo ligados como no main.py, com o modo desligado."""
    caminho_db, caminho_log = ambiente_temporario
    servico = ServicoCorteEmergencial(RepositorioDispositivos(), RepositorioEventos())
    modo = ModoFalhaSimulada(False)
    janela = MainWindowController()
    _ligar_modo_falha(janela, servico, modo)
    yield servico, modo, caminho_db, caminho_log
    janela.deleteLater()


def _estado_banco(caminho_db):
    """(status por tag, total de eventos), lido direto por SQL."""
    conexao = sqlite3.connect(caminho_db)
    try:
        status = dict(conexao.execute("SELECT tag, status FROM dispositivos"))
        total_eventos = conexao.execute("SELECT COUNT(*) FROM eventos").fetchone()[0]
    finally:
        conexao.close()
    return status, total_eventos


def test_modo_ativo_faz_o_corte_falhar_sem_alterar_o_banco(cenario):
    servico, modo, caminho_db, caminho_log = cenario
    antes = _estado_banco(caminho_db)

    modo.alternar()
    with pytest.raises(FalhaCorteEmergencial):
        servico.executar(EstadoDisjuntor.ABERTO)

    assert _estado_banco(caminho_db) == antes
    assert antes == ({"DISJ-01": "FECHADO", "RELE-01": "FECHADO"}, 0)
    assert MENSAGEM_ROLLBACK in caminho_log.read_text(encoding="utf-8")


def test_desativar_o_modo_faz_o_corte_voltar_a_funcionar(cenario):
    servico, modo, caminho_db, _ = cenario

    modo.alternar()
    with pytest.raises(FalhaCorteEmergencial):
        servico.executar(EstadoDisjuntor.ABERTO)
    modo.alternar()
    servico.executar(EstadoDisjuntor.ABERTO)

    assert _estado_banco(caminho_db) == ({"DISJ-01": "ABERTO", "RELE-01": "ABERTO"}, 1)


def test_valor_inicial_do_config_ja_vale_para_o_servico(app, ambiente_temporario):
    servico = ServicoCorteEmergencial(RepositorioDispositivos(), RepositorioEventos())
    janela = MainWindowController()

    _ligar_modo_falha(janela, servico, ModoFalhaSimulada(True))

    assert servico.simular_falha is True
    janela.deleteLater()
