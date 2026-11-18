"""Repositório SQLite do histórico de telemetria (tabela ``leituras``).

Chamado só de dentro do ``DatabaseWorker``: nada aqui deve rodar na thread
da GUI.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from database.connection import Database
from models.leitura import Leitura

#: Com milissegundos: a telemetria chega a 10 amostras por segundo.
FORMATO_DATA_HORA_BANCO = "%Y-%m-%d %H:%M:%S.%f"

#: Teto de linhas devolvidas numa consulta, para o gráfico não engasgar.
MAXIMO_LINHAS = 50_000


@dataclass(frozen=True, slots=True)
class FiltroLeituras:
    """Filtro da consulta histórica: últimas N leituras OU intervalo de datas."""

    ultimas: int | None = None
    inicio: datetime | None = None
    fim: datetime | None = None

    def __post_init__(self) -> None:
        por_quantidade = self.ultimas is not None
        por_intervalo = self.inicio is not None or self.fim is not None
        if por_quantidade == por_intervalo:
            raise ValueError("Informe as últimas N leituras ou um intervalo, não os dois.")
        if por_quantidade and not 1 <= self.ultimas <= MAXIMO_LINHAS:
            raise ValueError(f"N deve estar entre 1 e {MAXIMO_LINHAS}.")
        if por_intervalo:
            if self.inicio is None or self.fim is None:
                raise ValueError("O intervalo precisa de início e fim.")
            if self.inicio >= self.fim:
                raise ValueError("O início do intervalo deve ser anterior ao fim.")

    @classmethod
    def ultimas_n(cls, n: int) -> "FiltroLeituras":
        return cls(ultimas=n)

    @classmethod
    def intervalo(cls, inicio: datetime, fim: datetime) -> "FiltroLeituras":
        return cls(inicio=inicio, fim=fim)

    @property
    def descricao(self) -> str:
        """Texto curto para o título do gráfico."""
        if self.ultimas is not None:
            return f"Últimas {self.ultimas} leituras"
        formato = "%d/%m/%Y %H:%M"
        return f"{self.inicio.strftime(formato)} a {self.fim.strftime(formato)}"


class RepositorioLeituras:
    """Grava e consulta amostras de telemetria."""

    def adicionar_lote(
        self, leituras: Iterable[Leitura], conn: sqlite3.Connection | None = None
    ) -> int:
        """Grava as leituras numa única transação; devolve quantas foram gravadas."""
        linhas = [
            (
                leitura.instante.strftime(FORMATO_DATA_HORA_BANCO),
                leitura.tensao,
                leitura.corrente,
                leitura.potencia,
            )
            for leitura in leituras
        ]
        if not linhas:
            return 0

        sql = (
            "INSERT INTO leituras (data_hora, tensao, corrente, potencia) "
            "VALUES (?, ?, ?, ?)"
        )
        if conn is not None:
            conn.executemany(sql, linhas)
        else:
            with Database.transacao() as conexao:
                conexao.executemany(sql, linhas)
        return len(linhas)

    def consultar(self, filtro: FiltroLeituras) -> list[Leitura]:
        """Leituras do filtro, em ordem cronológica (pronta para plotar)."""
        if filtro.ultimas is not None:
            # As N mais recentes, devolvidas da mais antiga para a mais nova.
            sql = (
                "SELECT data_hora, tensao, corrente FROM ("
                "  SELECT id, data_hora, tensao, corrente FROM leituras"
                "  ORDER BY data_hora DESC, id DESC LIMIT ?"
                ") ORDER BY data_hora, id"
            )
            parametros: tuple = (filtro.ultimas,)
        else:
            sql = (
                "SELECT data_hora, tensao, corrente FROM leituras "
                "WHERE data_hora BETWEEN ? AND ? "
                "ORDER BY data_hora, id LIMIT ?"
            )
            parametros = (
                filtro.inicio.strftime(FORMATO_DATA_HORA_BANCO),
                filtro.fim.strftime(FORMATO_DATA_HORA_BANCO),
                MAXIMO_LINHAS,
            )

        conexao = Database.conectar()
        try:
            linhas = conexao.execute(sql, parametros).fetchall()
        finally:
            conexao.close()
        return [
            Leitura(
                instante=datetime.strptime(linha["data_hora"], FORMATO_DATA_HORA_BANCO),
                tensao=linha["tensao"],
                corrente=linha["corrente"],
            )
            for linha in linhas
        ]

    def contar(self) -> int:
        conexao = Database.conectar()
        try:
            return conexao.execute("SELECT COUNT(*) FROM leituras").fetchone()[0]
        finally:
            conexao.close()
