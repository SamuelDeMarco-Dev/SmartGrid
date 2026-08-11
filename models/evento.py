"""Evento de auditoria do supervisório.

Espelha exatamente as colunas da tabela de histórico definida no README:
Data/Hora · Tipo de Evento · Descrição · Valor Medido.

Contrato compartilhado — congelado após o merge do M0.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

FORMATO_DATA_HORA = "%d/%m/%Y %H:%M:%S"


class TipoEvento(Enum):
    """Categoria do evento registrado."""

    COMANDO = "Comando"
    ALERTA = "Alerta"
    STATUS = "Status"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Evento:
    """Registro imutável de algo que aconteceu no sistema."""

    data_hora: datetime
    tipo: TipoEvento
    descricao: str
    valor_medido: str = ""

    @classmethod
    def agora(
        cls, tipo: TipoEvento, descricao: str, valor_medido: str = ""
    ) -> "Evento":
        """Cria um evento carimbado com o horário atual."""
        return cls(datetime.now(), tipo, descricao, valor_medido)

    @property
    def data_hora_formatada(self) -> str:
        """Timestamp no formato exibido na tabela: ``10/08/2026 10:30:15``."""
        return self.data_hora.strftime(FORMATO_DATA_HORA)

    def como_linha(self) -> tuple[str, str, str, str]:
        """Devolve as quatro colunas da tabela, na ordem, já formatadas."""
        return (
            self.data_hora_formatada,
            self.tipo.value,
            self.descricao,
            self.valor_medido,
        )
