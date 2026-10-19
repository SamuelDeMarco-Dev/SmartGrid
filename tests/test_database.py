"""Testes de integridade do esquema SQLite."""

import sqlite3

import pytest

import database.connection as modulo_conexao
from database.connection import Database


@pytest.fixture
def banco_temporario(tmp_path, monkeypatch):
    caminho = tmp_path / "smartgrid.db"
    monkeypatch.setattr(modulo_conexao, "CAMINHO_DB", caminho)
    Database.init_db()
    return caminho


def test_status_invalido_viola_check(banco_temporario):
    with pytest.raises(sqlite3.IntegrityError):
        with Database.transacao() as conexao:
            conexao.execute(
                "UPDATE dispositivos SET status = ? WHERE tag = ?",
                ("INVALIDO", "DISJ-01"),
            )


def test_evento_com_dispositivo_inexistente_viola_fk(banco_temporario):
    with pytest.raises(sqlite3.IntegrityError):
        with Database.transacao() as conexao:
            conexao.execute(
                "INSERT INTO eventos "
                "(data_hora, tipo_evento, descricao, dispositivo_id) "
                "VALUES (?, ?, ?, ?)",
                ("2026-10-19 10:00:00", "Status", "Teste de FK", 999_999),
            )


@pytest.mark.parametrize("limite", [0, -1])
def test_limite_nao_positivo_viola_check(banco_temporario, limite):
    with pytest.raises(sqlite3.IntegrityError):
        with Database.transacao() as conexao:
            conexao.execute(
                "INSERT INTO regras_limites "
                "(nome, grandeza, limite_maximo, ativa) VALUES (?, ?, ?, ?)",
                (f"Limite {limite}", "CORRENTE", limite, 1),
            )


def test_init_db_e_idempotente(banco_temporario):
    with Database.transacao() as conexao:
        conexao.execute(
            "UPDATE dispositivos SET status = ? WHERE tag = ?",
            ("ABERTO", "DISJ-01"),
        )

    Database.init_db()

    conexao = Database.conectar()
    try:
        dispositivos = conexao.execute(
            "SELECT tag, status FROM dispositivos ORDER BY id"
        ).fetchall()
        quantidade_regras = conexao.execute(
            "SELECT COUNT(*) FROM regras_limites"
        ).fetchone()[0]
    finally:
        conexao.close()

    assert [(linha["tag"], linha["status"]) for linha in dispositivos] == [
        ("DISJ-01", "ABERTO"),
        ("RELE-01", "FECHADO"),
    ]
    assert quantidade_regras == 3


def _limites_por_grandeza(caminho):
    conexao = sqlite3.connect(caminho)
    try:
        return dict(conexao.execute("SELECT grandeza, limite_maximo FROM regras_limites"))
    finally:
        conexao.close()


def test_init_db_semeia_regras_com_limites_do_config(tmp_path, monkeypatch):
    caminho = tmp_path / "smartgrid.db"
    monkeypatch.setattr(modulo_conexao, "CAMINHO_DB", caminho)

    Database.init_db({"corrente": 25.0, "tensao": 220.0, "potencia": 5000})

    assert _limites_por_grandeza(caminho) == {
        "CORRENTE": 25.0, "TENSAO": 220.0, "POTENCIA": 5000.0,
    }


def test_init_db_sem_limites_usa_os_padroes_do_esquema(banco_temporario):
    assert _limites_por_grandeza(banco_temporario) == {
        "CORRENTE": 15.0, "TENSAO": 230.0, "POTENCIA": 3500.0,
    }


def test_limites_do_config_nao_sobrescrevem_regras_ja_salvas(banco_temporario):
    Database.init_db({"corrente": 99.0, "tensao": 99.0, "potencia": 99.0})

    assert _limites_por_grandeza(banco_temporario)["CORRENTE"] == 15.0
