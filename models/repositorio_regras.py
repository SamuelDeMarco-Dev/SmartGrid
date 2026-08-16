from models.regra_alerta import Grandeza, RegraAlerta


class RepositorioRegras:
    def __init__(self, regras_iniciais: list[RegraAlerta] | None = None):
        self._regras = self._copiar_regras(
            regras_iniciais if regras_iniciais is not None else self._regras_padrao()
        )

    def obter_regras(self) -> list[RegraAlerta]:
        """Retorna uma nova lista com cópias das regras vigentes."""
        return self._copiar_regras(self._regras)

    def salvar_regras(self, novas_regras: list[RegraAlerta]) -> None:
        """Sobrescreve as regras armazenadas sem expor a lista interna."""
        self._regras = self._copiar_regras(novas_regras)

    @staticmethod
    def _regras_padrao() -> list[RegraAlerta]:
        return [
            RegraAlerta(
                nome="Alerta Sobrecorrente",
                grandeza=Grandeza.CORRENTE,
                limite_maximo=15.0,
                ativa=True,
            ),
            RegraAlerta(
                nome="Alerta Sobretensão",
                grandeza=Grandeza.TENSAO,
                limite_maximo=230.0,
                ativa=True,
            ),
            RegraAlerta(
                nome="Pico de Potência",
                grandeza=Grandeza.POTENCIA,
                limite_maximo=3500.0,
                ativa=False,
            ),
        ]

    @classmethod
    def _copiar_regras(cls, regras: list[RegraAlerta]) -> list[RegraAlerta]:
        return [cls._copiar_regra(regra) for regra in regras]

    @staticmethod
    def _copiar_regra(regra: RegraAlerta) -> RegraAlerta:
        return RegraAlerta(
            nome=regra.nome,
            grandeza=regra.grandeza,
            limite_maximo=regra.limite_maximo,
            ativa=regra.ativa,
        )


repositorio_regras_compartilhado = RepositorioRegras()
