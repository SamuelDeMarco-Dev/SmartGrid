"""Repositório SQLite dos eventos do histórico."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time

from database.connection import Database
from models.evento import Evento, TipoEvento
from models.historico import ConsultaHistorico, EventoHistorico, FiltroHistorico

FORMATO_DATA_HORA_BANCO = "%Y-%m-%d %H:%M:%S"

_SELECT_HISTORICO = """
    SELECT e.id, e.data_hora, e.tipo_evento, e.descricao, e.valor_medido,
           e.dispositivo_id, d.tag AS dispositivo_tag
    FROM eventos e LEFT JOIN dispositivos d ON d.id = e.dispositivo_id
"""


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
        self,
        evento: Evento,
        conn: sqlite3.Connection | None = None,
        dispositivo_tag: str | None = None,
    ) -> None:
        """Persiste um evento, participando de uma transação externa se fornecida.

        ``dispositivo_tag`` (ex.: ``"DISJ-01"``) vincula o evento ao dispositivo
        pela chave estrangeira ``dispositivo_id``.
        """
        if not isinstance(evento, Evento):
            raise TypeError("O repositório aceita somente objetos Evento.")

        parametros = (
            evento.data_hora.strftime(FORMATO_DATA_HORA_BANCO),
            evento.tipo.value,
            evento.valor_medido,
            evento.descricao,
            dispositivo_tag,
        )
        sql = (
            "INSERT INTO eventos "
            "(data_hora, tipo_evento, valor_medido, descricao, dispositivo_id) "
            "VALUES (?, ?, ?, ?, (SELECT id FROM dispositivos WHERE tag = ?))"
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
        dispositivo_id: int | None = None,
    ) -> list[Evento]:
        """Filtra eventos, preservando o retorno legado de objetos Evento."""
        if tipo is not None and not isinstance(tipo, TipoEvento):
            raise TypeError("tipo deve ser uma instância de TipoEvento ou None.")

        condicoes: list[str] = []
        parametros: list[str | int] = []

        if tipo is not None:
            condicoes.append("tipo_evento = ?")
            parametros.append(tipo.value)

        data_limite = self._normalizar_data_inicio(data_inicio)
        if data_limite is not None:
            condicoes.append("data_hora >= ?")
            parametros.append(data_limite.strftime(FORMATO_DATA_HORA_BANCO))

        if dispositivo_id is not None:
            condicoes.append("dispositivo_id = ?")
            parametros.append(dispositivo_id)

        sql = (
            "SELECT data_hora, tipo_evento, descricao, valor_medido "
            "FROM eventos"
        )
        if condicoes:
            sql += " WHERE " + " AND ".join(condicoes)
        sql += " ORDER BY data_hora DESC, id DESC"

        return self._consultar(sql, parametros)

    def consultar_historico(self, filtro: FiltroHistorico) -> ConsultaHistorico:
        """Lê linhas, dispositivos e cursor no mesmo snapshot do SQLite.

        O cursor inclui também eventos fora do filtro. Assim uma atualização
        incremental nunca precisa repetir a consulta completa do histórico.
        """
        condicoes: list[str] = []
        parametros: list[str | int] = []
        if filtro.tipo is not None:
            condicoes.append("e.tipo_evento = ?")
            parametros.append(filtro.tipo.value)
        if filtro.data_inicio is not None:
            condicoes.append("e.data_hora >= ?")
            parametros.append(
                self._normalizar_data_inicio(filtro.data_inicio).strftime(FORMATO_DATA_HORA_BANCO)
            )
        if filtro.dispositivo_id is not None:
            condicoes.append("e.dispositivo_id = ?")
            parametros.append(filtro.dispositivo_id)

        sql = _SELECT_HISTORICO
        if condicoes:
            sql += " WHERE " + " AND ".join(condicoes)
        sql += " ORDER BY e.data_hora DESC, e.id DESC"

        conexao = Database.conectar()
        try:
            conexao.execute("BEGIN")
            ultimo_id = conexao.execute("SELECT COALESCE(MAX(id), 0) FROM eventos").fetchone()[0]
            linhas = conexao.execute(sql, parametros).fetchall()
            dispositivos = [
                (linha["id"], linha["tag"])
                for linha in conexao.execute("SELECT id, tag FROM dispositivos ORDER BY tag")
            ]
        finally:
            conexao.close()

        return ConsultaHistorico(
            [self._registro_da_linha(linha) for linha in linhas], ultimo_id, dispositivos
        )

    def consultar_novos(self, apos_id: int) -> list[EventoHistorico]:
        """Busca somente IDs novos; não deduz o dispositivo a partir do sinal."""
        conexao = Database.conectar()
        try:
            linhas = conexao.execute(
                _SELECT_HISTORICO + " WHERE e.id > ? ORDER BY e.data_hora DESC, e.id DESC",
                (apos_id,),
            ).fetchall()
        finally:
            conexao.close()
        return [self._registro_da_linha(linha) for linha in linhas]

    def _registro_da_linha(self, linha: sqlite3.Row) -> EventoHistorico:
        return EventoHistorico(
            linha["id"], self._evento_da_linha(linha),
            linha["dispositivo_id"], linha["dispositivo_tag"],
        )

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
        self, sql: str, parametros: list[str | int] | None = None
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
