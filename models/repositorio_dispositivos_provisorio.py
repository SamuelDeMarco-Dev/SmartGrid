"""Repositório PROVISÓRIO de dispositivos, usado até o merge da issue #28.

Implementa só o necessário para o corte emergencial (#29), seguindo a
assinatura combinada para ``models/repositorio_dispositivos.py``:

    obter_por_tag(tag)
    atualizar_status(tag, status, conn=None)

Quando o ``RepositorioDispositivos`` definitivo chegar à ``main``, basta trocar
o import no ``main.py`` e apagar este arquivo.
"""

import sqlite3
from datetime import datetime

from database.connection import Database


class RepositorioDispositivosProvisorio:
    """Leitura e atualização do status de disjuntor e relé na tabela ``dispositivos``."""

    def obter_por_tag(self, tag: str) -> sqlite3.Row:
        conn = Database.conectar()
        try:
            linha = conn.execute(
                "SELECT tag, tipo, status, ultima_atualizacao "
                "FROM dispositivos WHERE tag = ?",
                (tag,),
            ).fetchone()
        finally:
            conn.close()

        if linha is None:
            raise LookupError(f"Dispositivo '{tag}' não cadastrado.")
        return linha

    def atualizar_status(
        self, tag: str, status: str, conn: sqlite3.Connection | None = None
    ) -> None:
        """Grava o novo status. Com ``conn`` externo, participa da transação do chamador."""
        if conn is None:
            with Database.transacao() as propria:
                self._atualizar(propria, tag, status)
            return

        self._atualizar(conn, tag, status)

    @staticmethod
    def _atualizar(conn: sqlite3.Connection, tag: str, status: str) -> None:
        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = conn.execute(
            "UPDATE dispositivos SET status = ?, ultima_atualizacao = ? WHERE tag = ?",
            (status, agora, tag),
        )
        if cursor.rowcount == 0:
            raise LookupError(f"Dispositivo '{tag}' não cadastrado.")
