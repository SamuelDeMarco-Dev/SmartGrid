"""Repositório em memória dos eventos do histórico."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from models.evento import Evento, TipoEvento


class RepositorioEventos:
    """Mantém os eventos registrados para exibição posterior na tela."""

    def __init__(self) -> None:
        self._eventos: list[Evento] = self._criar_eventos_amostrais()
        self._ordenar()

    def listar(self) -> list[Evento]:
        """Retorna os eventos do mais recente para o mais antigo."""
        return list(self._eventos)

    def adicionar(self, evento: Evento) -> None:
        """Adiciona um evento ao repositório preservando a ordenação."""
        if not isinstance(evento, Evento):
            raise TypeError("O repositório aceita somente objetos Evento.")

        self._eventos.append(evento)
        self._ordenar()

    def filtrar(
        self,
        tipo: TipoEvento | None = None,
        data_inicio: date | datetime | None = None,
    ) -> list[Evento]:
        """Filtra eventos por tipo e/ou data inicial."""
        if tipo is not None and not isinstance(tipo, TipoEvento):
            raise TypeError("tipo deve ser uma instância de TipoEvento ou None.")

        data_limite = self._normalizar_data_inicio(data_inicio)
        eventos = self._eventos

        if tipo is not None:
            eventos = [evento for evento in eventos if evento.tipo == tipo]

        if data_limite is not None:
            eventos = [
                evento for evento in eventos if evento.data_hora >= data_limite
            ]

        return list(eventos)

    def limpar(self) -> None:
        """Remove todos os eventos armazenados."""
        self._eventos.clear()

    def _ordenar(self) -> None:
        self._eventos.sort(key=lambda evento: evento.data_hora, reverse=True)

    def _normalizar_data_inicio(
        self, data_inicio: date | datetime | None
    ) -> datetime | None:
        if data_inicio is None:
            return None

        if isinstance(data_inicio, datetime):
            return data_inicio

        if isinstance(data_inicio, date):
            return datetime.combine(data_inicio, time.min)

        raise TypeError("data_inicio deve ser date, datetime ou None.")

    def _criar_eventos_amostrais(self) -> list[Evento]:
        agora = datetime.now()

        return [
            Evento(
                data_hora=agora - timedelta(minutes=3),
                tipo=TipoEvento.STATUS,
                descricao="Sistema iniciado",
            ),
            Evento(
                data_hora=agora - timedelta(minutes=8),
                tipo=TipoEvento.STATUS,
                descricao="Comunicação serial conectada",
                valor_medido="COM3 / 9600 bps",
            ),
            Evento(
                data_hora=agora - timedelta(minutes=15),
                tipo=TipoEvento.COMANDO,
                descricao="Limite de corrente configurado",
                valor_medido="15.00 A",
            ),
            Evento(
                data_hora=agora - timedelta(minutes=27),
                tipo=TipoEvento.ALERTA,
                descricao="Limite de potência ultrapassado",
                valor_medido="3820 W",
            ),
            Evento(
                data_hora=agora - timedelta(minutes=42),
                tipo=TipoEvento.COMANDO,
                descricao="Corte de emergência acionado",
            ),
            Evento(
                data_hora=agora - timedelta(hours=1, minutes=5),
                tipo=TipoEvento.ALERTA,
                descricao="Disjuntor aberto por proteção",
                valor_medido="18.40 A",
            ),
        ]
