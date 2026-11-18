"""Estados do link serial com a placa, na ordem em que acontecem.

O ``SerialWorker`` emite ``estado_alterado(nome, detalhe)`` com o ``name``
deste enum; telas convertem de volta com ``EstadoLink[nome]``.
"""

from enum import Enum


class EstadoLink(Enum):
    """Fase da conexão, com o rótulo e a cor exibidos na interface."""

    DESCONECTADO = ("Desconectado", "#6B7280")
    ABRINDO_PORTA = ("Abrindo porta", "#1D4F80")
    HANDSHAKE = ("Handshake a 9600 bps", "#1D4F80")
    NEGOCIANDO = ("Negociando velocidade", "#B26A00")
    TELEMETRIA = ("Telemetria ativa", "#2E7D32")
    LINK_PERDIDO = ("Link perdido", "#C62828")

    def __init__(self, rotulo: str, cor: str) -> None:
        self.rotulo = rotulo
        self.cor = cor

    def __str__(self) -> str:
        return self.rotulo

    @property
    def em_andamento(self) -> bool:
        """True enquanto houver uma thread serial viva (conectando ou conectada)."""
        return self in (
            EstadoLink.ABRINDO_PORTA,
            EstadoLink.HANDSHAKE,
            EstadoLink.NEGOCIANDO,
            EstadoLink.TELEMETRIA,
        )
