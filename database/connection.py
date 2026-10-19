"""Conexão SQLite, inicialização do esquema e transações atômicas."""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from database.schema import DISPOSITIVOS_INICIAIS, REGRAS_INICIAIS, TABELAS

logger = logging.getLogger("smartgrid")

CAMINHO_DB = Path(__file__).resolve().parent.parent / "data" / "smartgrid.db"
TIMEOUT_S = 5.0


class Database:
    @staticmethod
    def conectar() -> sqlite3.Connection:
        """Abre conexão com timeout, chaves estrangeiras ativas e linhas nomeadas."""
        CAMINHO_DB.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: nós controlamos BEGIN/COMMIT/ROLLBACK explicitamente.
        conn = sqlite3.connect(CAMINHO_DB, timeout=TIMEOUT_S, isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.row_factory = sqlite3.Row
        return conn

    @classmethod
    def init_db(cls, limites_iniciais: dict[str, float] | None = None) -> None:
        """Cria o arquivo, as tabelas e os dados iniciais (somente se vazios).

        ``limites_iniciais`` (chaves ``corrente``, ``tensao`` e ``potencia``, do
        ``config.json``) substitui o limite padrão de cada regra inicial. Só é
        usado quando ``regras_limites`` está vazia: depois disso vale o banco.
        """
        with cls.transacao() as conn:
            for ddl in TABELAS:
                conn.execute(ddl)

            if conn.execute("SELECT COUNT(*) FROM dispositivos").fetchone()[0] == 0:
                agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn.executemany(
                    "INSERT INTO dispositivos (tag, tipo, status, ultima_atualizacao) "
                    "VALUES (?, ?, ?, ?)",
                    [(tag, tipo, status, agora) for tag, tipo, status in DISPOSITIVOS_INICIAIS],
                )

            if conn.execute("SELECT COUNT(*) FROM regras_limites").fetchone()[0] == 0:
                limites = limites_iniciais or {}
                conn.executemany(
                    "INSERT INTO regras_limites (nome, grandeza, limite_maximo, ativa) "
                    "VALUES (?, ?, ?, ?)",
                    [
                        (nome, grandeza, float(limites.get(grandeza.lower(), limite)), ativa)
                        for nome, grandeza, limite, ativa in REGRAS_INICIAIS
                    ],
                )
        logger.info("Conexão com SQLite estabelecida e esquema validado.")

    @classmethod
    @contextmanager
    def transacao(cls):
        """BEGIN → yield → COMMIT; qualquer exceção faz ROLLBACK, loga e relança."""
        conn = cls.conectar()
        try:
            conn.execute("BEGIN")
            yield conn
            conn.execute("COMMIT")
        except Exception as erro:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            logger.error("Rollback executado: %s", erro)
            raise
        finally:
            conn.close()
