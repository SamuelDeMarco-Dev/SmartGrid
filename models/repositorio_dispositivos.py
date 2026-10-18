"""Acesso aos dispositivos persistidos no SQLite."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from database.connection import Database
from models.estado_disjuntor import EstadoDisjuntor

FORMATO_DATA_HORA_BANCO = "%Y-%m-%d %H:%M:%S"


class RepositorioDispositivos:
    """Consulta dispositivos e atualiza seus estados."""

    def listar(self) -> list[sqlite3.Row]:
        """Retorna todos os dispositivos na ordem em que foram cadastrados."""
        conexao = Database.conectar()
        try:
            return conexao.execute(
                "SELECT id, tag, tipo, status, ultima_atualizacao "
                "FROM dispositivos ORDER BY id"
            ).fetchall()
        finally:
            conexao.close()

    def obter_por_tag(self, tag: str) -> sqlite3.Row:
        """Retorna o dispositivo identificado pela tag."""
        conexao = Database.conectar()
        try:
            linha = conexao.execute(
                "SELECT id, tag, tipo, status, ultima_atualizacao "
                "FROM dispositivos WHERE tag = ?",
                (tag,),
            ).fetchone()
        finally:
            conexao.close()

        if linha is None:
            raise LookupError(f"Dispositivo '{tag}' não cadastrado.")
        return linha

    def atualizar_status(
        self,
        tag: str,
        status: EstadoDisjuntor | str,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        """Atualiza o status, participando da transação externa quando fornecida."""
        status_banco = self._status_para_banco(status)

        if conn is not None:
            self._atualizar(conn, tag, status_banco)
            return

        with Database.transacao() as conexao:
            self._atualizar(conexao, tag, status_banco)

    @staticmethod
    def _atualizar(conn: sqlite3.Connection, tag: str, status: str) -> None:
        agora = datetime.now().strftime(FORMATO_DATA_HORA_BANCO)
        cursor = conn.execute(
            "UPDATE dispositivos "
            "SET status = ?, ultima_atualizacao = ? "
            "WHERE tag = ?",
            (status, agora, tag),
        )
        if cursor.rowcount == 0:
            raise LookupError(f"Dispositivo '{tag}' não cadastrado.")

    @staticmethod
    def _status_para_banco(status: EstadoDisjuntor | str) -> str:
        if isinstance(status, EstadoDisjuntor):
            return status.name
        if isinstance(status, str):
            return status
        raise TypeError("status deve ser EstadoDisjuntor ou str.")
