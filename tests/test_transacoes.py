"""Rollback, atomicidade e auditoria das transações SQLite."""

import logging
from datetime import datetime

import pytest

import database.connection as modulo_conexao
import models.log_config as log_config
from database.connection import Database
from models.estado_disjuntor import EstadoDisjuntor
from models.evento import Evento, TipoEvento
from models.repositorio_dispositivos import RepositorioDispositivos
from models.repositorio_eventos import RepositorioEventos

DATA_HORA_TESTE = datetime(2026, 10, 19, 10, 0, 0)


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


def _executar_corte(
    repositorio_dispositivos,
    repositorio_eventos,
    evento,
    falhar_antes_do_evento=False,
):
    with Database.transacao() as conexao:
        repositorio_dispositivos.atualizar_status(
            "DISJ-01", EstadoDisjuntor.ABERTO, conexao
        )
        repositorio_dispositivos.atualizar_status(
            "RELE-01", EstadoDisjuntor.ABERTO, conexao
        )
        if falhar_antes_do_evento:
            raise RuntimeError("Falha simulada entre atualização e evento")
        repositorio_eventos.adicionar(evento, conexao)


def _quantidade_eventos() -> int:
    conexao = Database.conectar()
    try:
        return conexao.execute("SELECT COUNT(*) FROM eventos").fetchone()[0]
    finally:
        conexao.close()


def _status_dispositivos() -> dict[str, str]:
    repositorio = RepositorioDispositivos()
    return {linha["tag"]: linha["status"] for linha in repositorio.listar()}


def test_falha_reverte_dispositivos_evento_e_registra_erro(ambiente_temporario):
    _, caminho_log = ambiente_temporario
    repositorio_dispositivos = RepositorioDispositivos()
    repositorio_eventos = RepositorioEventos()
    evento = Evento(DATA_HORA_TESTE, TipoEvento.COMANDO, "Corte emergencial")
    quantidade_inicial = _quantidade_eventos()

    with pytest.raises(RuntimeError, match="Falha simulada"):
        _executar_corte(
            repositorio_dispositivos,
            repositorio_eventos,
            evento,
            falhar_antes_do_evento=True,
        )

    assert _status_dispositivos() == {
        "DISJ-01": "FECHADO",
        "RELE-01": "FECHADO",
    }
    assert _quantidade_eventos() == quantidade_inicial

    conteudo_log = caminho_log.read_text(encoding="utf-8")
    assert "[ERROR]" in conteudo_log
    assert "Rollback executado" in conteudo_log


def test_sucesso_persiste_status_e_evento_juntos(ambiente_temporario):
    repositorio_dispositivos = RepositorioDispositivos()
    repositorio_eventos = RepositorioEventos()
    evento = Evento(
        DATA_HORA_TESTE, TipoEvento.COMANDO, "Corte emergencial concluído"
    )
    quantidade_inicial = _quantidade_eventos()

    _executar_corte(repositorio_dispositivos, repositorio_eventos, evento)

    assert _status_dispositivos() == {
        "DISJ-01": "ABERTO",
        "RELE-01": "ABERTO",
    }
    assert _quantidade_eventos() == quantidade_inicial + 1
    assert repositorio_eventos.listar_todos()[0] == evento


def test_falha_na_limpeza_preserva_todos_os_eventos(ambiente_temporario):
    repositorio = RepositorioEventos()
    eventos = [
        Evento(DATA_HORA_TESTE, TipoEvento.STATUS, "Evento antes da limpeza 1"),
        Evento(DATA_HORA_TESTE, TipoEvento.ALERTA, "Evento antes da limpeza 2"),
        Evento(DATA_HORA_TESTE, TipoEvento.COMANDO, "Evento antes da limpeza 3"),
    ]
    for evento in eventos:
        repositorio.adicionar(evento)

    with pytest.raises(RuntimeError, match="Falha simulada na limpeza"):
        with Database.transacao() as conexao:
            assert repositorio.limpar(conexao) == len(eventos)
            raise RuntimeError("Falha simulada na limpeza")

    assert _quantidade_eventos() == len(eventos)
    assert repositorio.listar_todos() == list(reversed(eventos))
