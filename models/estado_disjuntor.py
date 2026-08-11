"""Estado binário do disjuntor / chave de proteção geral.

Contrato compartilhado — congelado após o merge do M0.
"""

from enum import Enum


class EstadoDisjuntor(Enum):
    """Estado do disjuntor, com o rótulo e a cor padronizados no README."""

    FECHADO = ("FECHADO / NORMAL", "#2E7D32")
    ABERTO = ("ABERTO / PROTEÇÃO ATIVADA", "#C62828")

    def __init__(self, rotulo: str, cor: str) -> None:
        self.rotulo = rotulo
        self.cor = cor

    def __str__(self) -> str:
        return self.rotulo

    @property
    def energizado(self) -> bool:
        """True quando a instalação está energizada (disjuntor fechado)."""
        return self is EstadoDisjuntor.FECHADO

    @property
    def texto_indicador(self) -> str:
        """Texto do badge no dashboard: ``Disjuntor: FECHADO / NORMAL``."""
        return f"Disjuntor: {self.rotulo}"

    def alternado(self) -> "EstadoDisjuntor":
        """Devolve o estado oposto ao atual."""
        if self is EstadoDisjuntor.FECHADO:
            return EstadoDisjuntor.ABERTO
        return EstadoDisjuntor.FECHADO
