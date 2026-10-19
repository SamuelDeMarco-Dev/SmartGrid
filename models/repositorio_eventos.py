"""Repositório SQLite dos eventos do histórico."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time

from database.connection import Database
from models.evento import Evento, TipoEvento

FORMATO_DATA_HORA_BANCO = "%Y-%m-%d %H:%M:%S"


class RepositorioEventos:
    """Persiste e consulta os eventos exibidos na tela de histórico."""

    def listar_todos(self) -> list[Evento]:
        """Retorna os eventos do mais recente para o mais antigo."""
        return self._consultar(
            """
            SELECT data_hora, tipo_evento, descricao, valor_medido
            FROM eventos
            ORDER BY data_hora DESC, id DESC
            """
        )

    def adicionar(
        self, evento: Evento, conn: sqlite3.Connection | None = None
    ) -> None:
        """Persiste um evento, participando de uma transação externa se fornecida."""
        if not isinstance(evento, Evento):
            raise TypeError("O repositório aceita somente objetos Evento.")

        parametros = (
            evento.data_hora.strftime(FORMATO_DATA_HORA_BANCO),
            evento.tipo.value,
            evento.valor_medido,
            evento.descricao,
        )
        sql = (
            "INSERT INTO eventos "
            "(data_hora, tipo_evento, valor_medido, descricao) "
            "VALUES (?, ?, ?, ?)"
        )

        if conn is not None:
            conn.execute(sql, parametros)
            return

        with Database.transacao() as conexao:
            conexao.execute(sql, parametros)

    def filtrar(
        self,
        tipo: TipoEvento | None = None,
        data_inicio: date | datetime | None = None,
    ) -> list[Evento]:
        """Filtra eventos por tipo e/ou data inicial."""
        if tipo is not None and not isinstance(tipo, TipoEvento):
            raise TypeError("tipo deve ser uma instância de TipoEvento ou None.")

        condicoes: list[str] = []
        parametros: list[str] = []

        if tipo is not None:
            condicoes.append("tipo_evento = ?")
            parametros.append(tipo.value)

        data_limite = self._normalizar_data_inicio(data_inicio)
        if data_limite is not None:
            condicoes.append("data_hora >= ?")
            parametros.append(data_limite.strftime(FORMATO_DATA_HORA_BANCO))

        sql = (
            "SELECT data_hora, tipo_evento, descricao, valor_medido "
            "FROM eventos"
        )
        if condicoes:
            sql += " WHERE " + " AND ".join(condicoes)
        sql += " ORDER BY data_hora DESC, id DESC"

        return self._consultar(sql, parametros)

    def limpar(self, conn: sqlite3.Connection | None = None) -> int:
        """Remove os eventos e retorna a quantidade de registros afetados.

        Quando a conexão não é fornecida, o retorno só acontece depois do
        commit executado por ``Database.transacao()``.
        """
        if conn is not None:
            cursor = conn.execute("DELETE FROM eventos")
            return cursor.rowcount

        with Database.transacao() as conexao:
            cursor = conexao.execute("DELETE FROM eventos")
            quantidade_removida = cursor.rowcount

        return quantidade_removida

    def _consultar(
        self, sql: str, parametros: list[str] | None = None
    ) -> list[Evento]:
        conexao = Database.conectar()
        try:
            linhas = conexao.execute(sql, parametros or []).fetchall()
        finally:
            conexao.close()

        return [self._evento_da_linha(linha) for linha in linhas]

    @staticmethod
    def _evento_da_linha(linha: sqlite3.Row) -> Evento:
        return Evento(
            data_hora=datetime.strptime(
                linha["data_hora"], FORMATO_DATA_HORA_BANCO
            ),
            tipo=TipoEvento(linha["tipo_evento"]),
            descricao=linha["descricao"],
            valor_medido=linha["valor_medido"] or "",
        )

    @staticmethod
    def _normalizar_data_inicio(
        data_inicio: date | datetime | None,
    ) -> datetime | None:
        if data_inicio is None:
            return None

        if isinstance(data_inicio, datetime):
            return data_inicio

        if isinstance(data_inicio, date):
            return datetime.combine(data_inicio, time.min)

        raise TypeError("data_inicio deve ser date, datetime ou None.")
