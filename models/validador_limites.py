"""Validação do formulário de limites e seleção dos limites iniciais."""

from dataclasses import dataclass
from math import isfinite

from models.regra_alerta import Grandeza, RegraAlerta


@dataclass(frozen=True, slots=True)
class RascunhoRegra:
    nome: str
    grandeza: Grandeza | None
    limite_maximo: str
    ativa: bool

    @classmethod
    def da_regra(cls, regra: RegraAlerta) -> "RascunhoRegra":
        return cls(regra.nome, regra.grandeza, str(regra.limite_maximo), regra.ativa)


class EntradaLimiteInvalida(ValueError):
    def __init__(self, indice: int, campo: str, mensagem: str) -> None:
        super().__init__(mensagem)
        self.indice = indice
        self.campo = campo


def validar_regras(rascunhos: list[RascunhoRegra]) -> list[RegraAlerta]:
    regras = []
    nomes = set()
    for indice, rascunho in enumerate(rascunhos):
        nome = rascunho.nome.strip()
        normalizado = " ".join(nome.split()).casefold()
        if not nome:
            raise EntradaLimiteInvalida(indice, "nome", "O nome da regra não pode ficar vazio.")
        if normalizado in nomes:
            raise EntradaLimiteInvalida(
                indice, "nome", "Não é permitido cadastrar regras com nomes duplicados.",
            )

        texto = rascunho.limite_maximo.strip()
        if not texto:
            raise EntradaLimiteInvalida(
                indice, "limite_maximo", "O limite máximo não pode ficar vazio.",
            )
        try:
            limite = float(texto.replace(",", "."))
        except ValueError as erro:
            raise EntradaLimiteInvalida(
                indice, "limite_maximo", "O limite máximo deve ser um número.",
            ) from erro
        if not isfinite(limite) or limite <= 0:
            raise EntradaLimiteInvalida(
                indice, "limite_maximo", "O limite máximo deve ser finito e maior que zero.",
            )
        if not isinstance(rascunho.grandeza, Grandeza):
            raise EntradaLimiteInvalida(
                indice, "grandeza", "Selecione uma grandeza válida para a regra.",
            )

        nomes.add(normalizado)
        regras.append(RegraAlerta(nome, rascunho.grandeza, limite, rascunho.ativa))
    return regras


def limites_para_config(regras: list[RegraAlerta]) -> dict[str, float]:
    """Menor limite ativo por grandeza ou o menor inativo na ausência de ativos.

    Uma grandeza sem regras fica fora da atualização. Potência permanece
    independente no JSON.
    """
    limites = {}
    for grandeza in (Grandeza.TENSAO, Grandeza.CORRENTE):
        candidatas = [regra for regra in regras if regra.grandeza is grandeza]
        if candidatas:
            ativas = [regra for regra in candidatas if regra.ativa]
            limites[grandeza.name.lower()] = min(
                regra.limite_maximo for regra in (ativas or candidatas)
            )
    return limites
