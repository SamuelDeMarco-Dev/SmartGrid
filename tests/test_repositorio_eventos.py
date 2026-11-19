"""Consultas do histórico e compatibilidade do repositório (issue #63)."""

from datetime import date, datetime, timedelta

import pytest

import database.connection as modulo_conexao
from database.connection import Database
from models.evento import Evento, TipoEvento
from models.historico import FiltroHistorico
from models.repositorio_eventos import RepositorioEventos


@pytest.fixture
def repositorio(tmp_path, monkeypatch):
    monkeypatch.setattr(modulo_conexao, "CAMINHO_DB", tmp_path / "smartgrid.db")
    Database.init_db()
    return RepositorioEventos()


@pytest.fixture
def dados(repositorio):
    inicio = datetime(2026, 10, 19, 10)
    eventos = [
        Evento(inicio, TipoEvento.COMANDO, "Corte", "220 W"),
        Evento(inicio + timedelta(days=1), TipoEvento.ALERTA, "Disparo", "1 A"),
        Evento(inicio + timedelta(days=2), TipoEvento.STATUS, "Link conectado"),
        Evento(inicio + timedelta(days=2), TipoEvento.COMANDO, "Religar"),
    ]
    for evento, tag in zip(eventos, ("DISJ-01", "RELE-01", None, "DISJ-01")):
        repositorio.adicionar(evento, dispositivo_tag=tag)
    consulta = repositorio.consultar_historico(FiltroHistorico())
    return eventos, dict((tag, id_) for id_, tag in consulta.dispositivos)


def test_left_join_preserva_eventos_sem_dispositivo_e_ordena_por_id(repositorio, dados):
    eventos, _ = dados
    consulta = repositorio.consultar_historico(FiltroHistorico())

    assert [registro.evento for registro in consulta.registros] == list(reversed(eventos))
    assert [registro.dispositivo_tag for registro in consulta.registros] == [
        "DISJ-01", None, "RELE-01", "DISJ-01",
    ]
    assert consulta.registros[1].colunas[4] == "Sem dispositivo"
    assert consulta.ultimo_id == 4


@pytest.mark.parametrize(
    "tipo,inicio,dispositivo,indices",
    [
        (TipoEvento.COMANDO, None, None, [3, 0]),
        (None, date(2026, 10, 20), None, [3, 2, 1]),
        (None, None, "RELE-01", [1]),
        (TipoEvento.COMANDO, date(2026, 10, 20), "DISJ-01", [3]),
        (TipoEvento.STATUS, None, "DISJ-01", []),
    ],
)
def test_combina_filtros_sem_alterar_retorno_legado(
    repositorio, dados, tipo, inicio, dispositivo, indices
):
    eventos, ids = dados
    dispositivo_id = ids.get(dispositivo)
    filtro = FiltroHistorico(tipo, inicio, dispositivo_id)

    consulta = repositorio.consultar_historico(filtro)

    assert [registro.evento for registro in consulta.registros] == [eventos[i] for i in indices]
    assert all(filtro.aceita(registro) for registro in consulta.registros)
    assert repositorio.filtrar(tipo, inicio, dispositivo_id) == [eventos[i] for i in indices]
    assert consulta.ultimo_id == 4  # inclui IDs fora do filtro
    assert repositorio.listar_todos() == list(reversed(eventos))


def test_dispositivo_e_data_sao_parametrizados(repositorio, dados):
    filtro = FiltroHistorico(dispositivo_id="1 OR 1=1")
    assert repositorio.consultar_historico(filtro).registros == []
    assert repositorio.filtrar(dispositivo_id="1 OR 1=1") == []
    assert len(repositorio.listar_todos()) == 4


def test_incremento_usa_id_mesmo_com_timestamp_antigo(repositorio, dados):
    consulta = repositorio.consultar_historico(FiltroHistorico(tipo=TipoEvento.STATUS))
    evento = Evento(datetime(2020, 1, 1), TipoEvento.ALERTA, "Evento atrasado")
    repositorio.adicionar(evento, dispositivo_tag="DISJ-01")

    novos = repositorio.consultar_novos(consulta.ultimo_id)

    assert [registro.evento for registro in novos] == [evento]
    assert novos[0].dispositivo_tag == "DISJ-01"
    assert repositorio.consultar_novos(novos[0].id) == []


def test_remocao_do_dispositivo_mantem_auditoria(repositorio, dados):
    _, ids = dados
    with Database.transacao() as conexao:
        conexao.execute("DELETE FROM dispositivos WHERE id = ?", (ids["DISJ-01"],))

    consulta = repositorio.consultar_historico(FiltroHistorico())

    assert len(consulta.registros) == 4
    assert consulta.registros[0].dispositivo_id is None
    assert consulta.registros[0].colunas[4] == "Sem dispositivo"


def test_reabrir_banco_preserva_eventos_e_limpeza_nao_reutiliza_ids(repositorio, dados):
    Database.init_db()
    novo_repositorio = RepositorioEventos()
    assert len(novo_repositorio.listar_todos()) == 4
    assert novo_repositorio.limpar() == 4
    assert novo_repositorio.consultar_historico(FiltroHistorico()).registros == []

    novo_repositorio.adicionar(Evento(datetime(2026, 10, 22), TipoEvento.STATUS, "Novo"))
    assert novo_repositorio.consultar_novos(4)[0].id == 5
