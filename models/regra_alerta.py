"""Regras de alerta (setpoints) configuradas pelo operador.

Contrato compartilhado — congelado após o merge do M0.
"""

from dataclasses import dataclass
from enum import Enum


class Grandeza(Enum):
    """Grandeza elétrica sobre a qual um limite pode ser aplicado."""

    TENSAO = ("Tensão", "V")
    CORRENTE = ("Corrente", "A")
    POTENCIA = ("Potência", "W")

    def __init__(self, rotulo: str, unidade: str) -> None:
        self.rotulo = rotulo
        self.unidade = unidade

    def __str__(self) -> str:
        return f"{self.rotulo} ({self.unidade})"


@dataclass
class RegraAlerta:
    """Limite máximo tolerável para uma grandeza."""

    nome: str
    grandeza: Grandeza
    limite_maximo: float
    ativa: bool = True

    def __post_init__(self) -> None:
        if not self.nome.strip():
            raise ValueError("A regra de alerta precisa de um nome.")
        if self.limite_maximo <= 0:
            raise ValueError("O limite máximo deve ser maior que zero.")

    def violada_por(self, valor: float) -> bool:
        """Indica se `valor` ultrapassa o limite desta regra.

        Regras inativas nunca são violadas.
        """
        return self.ativa and valor > self.limite_maximo

    def descricao(self) -> str:
        """Texto usado nos eventos de auditoria e nas listagens."""
        situacao = "ativa" if self.ativa else "inativa"
        return (
            f"{self.nome} — {self.grandeza.rotulo} máx. "
            f"{self.limite_maximo:g} {self.grandeza.unidade} ({situacao})"
        )
