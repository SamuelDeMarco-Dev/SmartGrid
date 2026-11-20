"""Intenção de atuação e confirmação serial são informações independentes."""

from dataclasses import dataclass
from enum import Enum

from models.estado_disjuntor import EstadoDisjuntor


class ConfirmacaoDisjuntor(Enum):
    DESCONHECIDO = "Estado desconhecido"
    PENDENTE = "Comando pendente"
    CONFIRMADO = "Confirmado pela telemetria"
    FALHA = "Falha de confirmação"


@dataclass(frozen=True, slots=True)
class SituacaoDisjuntor:
    solicitado: EstadoDisjuntor
    reportado: EstadoDisjuntor | None = None
    confirmacao: ConfirmacaoDisjuntor = ConfirmacaoDisjuntor.DESCONHECIDO
    detalhe: str = "Aguardando telemetria válida da placa."

    @property
    def pendente(self) -> bool:
        return self.confirmacao is ConfirmacaoDisjuntor.PENDENTE
