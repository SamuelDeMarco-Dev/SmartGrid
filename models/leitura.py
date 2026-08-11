"""Amostra de telemetria elétrica recebida do microcontrolador.

Contrato compartilhado — congelado após o merge do M0.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Leitura:
    """Uma amostra instantânea de tensão e corrente.

    A potência ativa não é armazenada: ela é sempre derivada de P = V x I,
    garantindo que todas as telas mostrem o mesmo valor.
    """

    instante: datetime
    tensao: float
    corrente: float

    def __post_init__(self) -> None:
        if self.tensao < 0:
            raise ValueError("A tensão eficaz não pode ser negativa.")
        if self.corrente < 0:
            raise ValueError("A corrente eficaz não pode ser negativa.")

    @property
    def potencia(self) -> float:
        """Potência ativa aproximada em watts (P = V x I)."""
        return self.tensao * self.corrente

    @property
    def potencia_kw(self) -> float:
        """Potência ativa em quilowatts."""
        return self.potencia / 1000.0

    def resumo(self) -> str:
        """Texto pronto para a coluna 'Valor Medido' do histórico.

        Exemplo: ``12.50 A / 2750 W``
        """
        return f"{self.corrente:.2f} A / {self.potencia:.0f} W"
