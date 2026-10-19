"""Persistência SQLite das regras de alerta."""

from database.connection import Database
from models.regra_alerta import Grandeza, RegraAlerta


class RepositorioRegras:
    """Consulta e substitui o conjunto de regras de alerta."""

    def obter_regras(self) -> list[RegraAlerta]:
        """Retorna as regras persistidas na ordem de cadastro."""
        conexao = Database.conectar()
        try:
            linhas = conexao.execute(
                "SELECT nome, grandeza, limite_maximo, ativa "
                "FROM regras_limites ORDER BY id"
            ).fetchall()
        finally:
            conexao.close()

        return [
            RegraAlerta(
                nome=linha["nome"],
                grandeza=Grandeza[linha["grandeza"]],
                limite_maximo=linha["limite_maximo"],
                ativa=bool(linha["ativa"]),
            )
            for linha in linhas
        ]

    def salvar_regras(self, novas_regras: list[RegraAlerta]) -> None:
        """Substitui todas as regras em uma única transação."""
        parametros = [
            (
                regra.nome,
                regra.grandeza.name,
                regra.limite_maximo,
                int(regra.ativa),
            )
            for regra in novas_regras
        ]

        with Database.transacao() as conexao:
            conexao.execute("DELETE FROM regras_limites")
            conexao.executemany(
                "INSERT INTO regras_limites "
                "(nome, grandeza, limite_maximo, ativa) "
                "VALUES (?, ?, ?, ?)",
                parametros,
            )
