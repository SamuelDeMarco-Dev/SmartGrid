"""Dados de consulta do histórico, sem alterar o contrato compartilhado Evento."""

from dataclasses import dataclass, field
from datetime import date

from models.evento import Evento, TipoEvento


@dataclass(frozen=True, slots=True)
class FiltroHistorico:
    tipo: TipoEvento | None = None
    data_inicio: date | None = None
    dispositivo_id: int | None = None

    def aceita(self, registro: "EventoHistorico") -> bool:
        evento = registro.evento
        return (
            (self.tipo is None or evento.tipo is self.tipo)
            and (self.data_inicio is None or evento.data_hora.date() >= self.data_inicio)
            and (self.dispositivo_id is None or registro.dispositivo_id == self.dispositivo_id)
        )


@dataclass(frozen=True, slots=True)
class EventoHistorico:
    id: int
    evento: Evento
    dispositivo_id: int | None
    dispositivo_tag: str | None
    # A formatação acontece na thread de consulta, uma única vez por registro.
    colunas: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "colunas",
            (*self.evento.como_linha(), self.dispositivo_tag or "Sem dispositivo"),
        )


@dataclass(frozen=True, slots=True)
class ConsultaHistorico:
    registros: list[EventoHistorico]
    ultimo_id: int
    dispositivos: list[tuple[int, str]]
