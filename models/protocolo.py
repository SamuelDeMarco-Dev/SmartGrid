"""Protocolo serial entre o desktop e a placa de medição (Arduino UNO).

Enquadramento
-------------
Cada pacote é uma linha ASCII::

    $CORPO*CS\\n

``CORPO`` são campos separados por vírgula (o primeiro é o comando) e ``CS`` é
o XOR de todos os bytes do corpo em dois dígitos hexadecimais maiúsculos —
o mesmo esquema das sentenças NMEA. Linha sem ``$``, sem ``*``, com checksum
errado ou com campos fora do esperado é descartada com :class:`ErroProtocolo`.

Máquina de estados da placa
---------------------------
1. ``AGUARDANDO_LINK`` a **9600 bps**: a placa não transmite nada.
2. Desktop → ``HS,<baud>,<intervalo_ms>`` (handshake de configuração).
   Placa → ``ACK,HS,<baud>,<intervalo_ms>`` ou ``NAK,<motivo>``.
3. Os dois lados trocam para ``<baud>``. Desktop → ``SYNC``; placa →
   ``ACK,SYNC``. Se o ``SYNC`` não chegar em :data:`TEMPO_SYNC_S`, a placa
   volta a 9600 bps e ao passo 1.
4. Placa → ``TLM,<seq>,<tensao>,<corrente>,<disjuntor>`` a cada
   ``<intervalo_ms>`` (``disjuntor``: 1 fechado, 0 aberto).
5. Desktop → ``PING`` periodicamente; sem nenhuma mensagem do host por
   :data:`WATCHDOG_PLACA_S`, a placa volta ao passo 1. ``RELE,0|1`` abre ou
   fecha a carga (resposta ``ACK,RELE,0|1``) e ``BYE`` encerra o link.

O firmware em ``firmware/smartgrid_placa`` e a placa virtual em
``tools/simulador_placa.py`` implementam o lado da placa; o
``models/serial_worker.py``, o lado do desktop.

Este módulo é Python puro: não conhece Qt nem pyserial.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

#: Velocidades aceitas pela placa, da mais lenta para a mais rápida.
VELOCIDADES = (9600, 19200, 38400, 115200)

#: Toda conexão começa aqui; é a única velocidade que a placa escuta ao ligar.
VELOCIDADE_INICIAL = VELOCIDADES[0]

#: Faixa do período de telemetria pedido no handshake, em milissegundos.
INTERVALO_MINIMO_MS = 100
INTERVALO_MAXIMO_MS = 5000

#: Quanto a placa espera o ``SYNC`` na nova velocidade antes de desistir.
TEMPO_SYNC_S = 2.0

#: Silêncio do host que faz a placa voltar a ``AGUARDANDO_LINK``.
WATCHDOG_PLACA_S = 5.0

#: Período do ``PING`` enviado pelo desktop (bem abaixo do watchdog).
PERIODO_PING_S = 1.0

INICIO = "$"
SEPARADOR_CS = "*"
TAMANHO_MAXIMO = 64


class Comando(str, Enum):
    """Primeiro campo de cada pacote."""

    HS = "HS"
    ACK = "ACK"
    NAK = "NAK"
    SYNC = "SYNC"
    TLM = "TLM"
    PING = "PING"
    RELE = "RELE"
    BYE = "BYE"


class ErroProtocolo(ValueError):
    """Pacote malformado, corrompido ou incoerente com o protocolo."""


@dataclass(frozen=True, slots=True)
class Mensagem:
    """Pacote já validado: comando e demais campos, como texto."""

    comando: Comando
    campos: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Telemetria:
    """Conteúdo de um pacote ``TLM``."""

    seq: int
    tensao: float
    corrente: float
    disjuntor_fechado: bool


# ----------------------------------------------------------------------
# Enquadramento
# ----------------------------------------------------------------------
def checksum(corpo: str) -> str:
    """XOR dos bytes do corpo, em dois dígitos hexadecimais."""
    valor = 0
    for byte in corpo.encode("ascii"):
        valor ^= byte
    return f"{valor:02X}"


def montar(comando: Comando, *campos: object) -> bytes:
    """Monta o pacote pronto para ``write``: ``b"$HS,115200,1000*4B\\n"``."""
    corpo = ",".join([comando.value, *(str(campo) for campo in campos)])
    return f"{INICIO}{corpo}{SEPARADOR_CS}{checksum(corpo)}\n".encode("ascii")


def interpretar(linha: bytes | str) -> Mensagem:
    """Valida o enquadramento e o checksum e devolve a :class:`Mensagem`."""
    if isinstance(linha, bytes):
        try:
            linha = linha.decode("ascii")
        except UnicodeDecodeError:
            raise ErroProtocolo("Pacote com bytes fora do ASCII.") from None

    linha = linha.strip()
    if len(linha) > TAMANHO_MAXIMO:
        raise ErroProtocolo("Pacote maior que o tamanho máximo.")
    if not linha.startswith(INICIO) or SEPARADOR_CS not in linha:
        raise ErroProtocolo(f"Pacote sem enquadramento: {linha!r}.")

    corpo, _, recebido = linha[1:].rpartition(SEPARADOR_CS)
    if recebido.upper() != checksum(corpo):
        raise ErroProtocolo(f"Checksum inválido em {linha!r}.")

    nome, *campos = corpo.split(",")
    try:
        comando = Comando(nome)
    except ValueError:
        raise ErroProtocolo(f"Comando desconhecido: {nome!r}.") from None
    return Mensagem(comando, tuple(campos))


# ----------------------------------------------------------------------
# Pacotes do desktop
# ----------------------------------------------------------------------
def handshake(baud: int, intervalo_ms: int) -> bytes:
    """``HS``: pede a nova velocidade e o período da telemetria."""
    validar_velocidade(baud)
    validar_intervalo(intervalo_ms)
    return montar(Comando.HS, baud, intervalo_ms)


def sync() -> bytes:
    return montar(Comando.SYNC)


def ping() -> bytes:
    return montar(Comando.PING)


def bye() -> bytes:
    return montar(Comando.BYE)


def rele(fechado: bool) -> bytes:
    """``RELE,1`` fecha a carga (religamento); ``RELE,0`` abre (corte)."""
    return montar(Comando.RELE, int(fechado))


# ----------------------------------------------------------------------
# Pacotes da placa (usados pela placa virtual e pelos testes)
# ----------------------------------------------------------------------
def ack(*campos: object) -> bytes:
    return montar(Comando.ACK, *campos)


def nak(motivo: str) -> bytes:
    return montar(Comando.NAK, motivo)


def telemetria(
    seq: int, tensao: float, corrente: float, disjuntor_fechado: bool
) -> bytes:
    return montar(
        Comando.TLM, seq, f"{tensao:.1f}", f"{corrente:.3f}", int(disjuntor_fechado)
    )


# ----------------------------------------------------------------------
# Decodificação e validação
# ----------------------------------------------------------------------
def decodificar_handshake(mensagem: Mensagem) -> tuple[int, int]:
    """``(baud, intervalo_ms)`` de um ``HS`` ou de um ``ACK,HS``."""
    campos = mensagem.campos
    if mensagem.comando is Comando.ACK:
        if not campos or campos[0] != Comando.HS.value:
            raise ErroProtocolo("ACK não corresponde ao handshake.")
        campos = campos[1:]
    elif mensagem.comando is not Comando.HS:
        raise ErroProtocolo(f"Esperado HS, recebido {mensagem.comando.value}.")

    if len(campos) != 2:
        raise ErroProtocolo("Handshake deve ter velocidade e intervalo.")
    baud, intervalo = (_inteiro(campo) for campo in campos)
    validar_velocidade(baud)
    validar_intervalo(intervalo)
    return baud, intervalo


def decodificar_telemetria(mensagem: Mensagem) -> Telemetria:
    """Converte um ``TLM`` em :class:`Telemetria`, conferindo cada campo."""
    if mensagem.comando is not Comando.TLM:
        raise ErroProtocolo(f"Esperado TLM, recebido {mensagem.comando.value}.")
    if len(mensagem.campos) != 4:
        raise ErroProtocolo("Telemetria deve ter 4 campos.")

    seq_txt, tensao_txt, corrente_txt, disjuntor_txt = mensagem.campos
    tensao = _real(tensao_txt)
    corrente = _real(corrente_txt)
    if tensao < 0 or corrente < 0:
        raise ErroProtocolo("Tensão e corrente não podem ser negativas.")
    if disjuntor_txt not in ("0", "1"):
        raise ErroProtocolo(f"Estado de disjuntor inválido: {disjuntor_txt!r}.")
    return Telemetria(_inteiro(seq_txt), tensao, corrente, disjuntor_txt == "1")


def e_ack(mensagem: Mensagem, *esperado: str) -> bool:
    """True se a mensagem for ``ACK`` começando pelos campos ``esperado``."""
    return (
        mensagem.comando is Comando.ACK
        and mensagem.campos[: len(esperado)] == esperado
    )


def validar_velocidade(baud: int) -> None:
    if baud not in VELOCIDADES:
        raise ErroProtocolo(f"Velocidade não suportada: {baud} bps.")


def validar_intervalo(intervalo_ms: int) -> None:
    if not INTERVALO_MINIMO_MS <= intervalo_ms <= INTERVALO_MAXIMO_MS:
        raise ErroProtocolo(
            f"Intervalo fora da faixa ({INTERVALO_MINIMO_MS}–"
            f"{INTERVALO_MAXIMO_MS} ms): {intervalo_ms}."
        )


def velocidades_para_tentar(alvo: int) -> list[int]:
    """Da velocidade alvo até 9600, na ordem em que a negociação tenta.

    >>> velocidades_para_tentar(38400)
    [38400, 19200, 9600]
    """
    validar_velocidade(alvo)
    return [baud for baud in reversed(VELOCIDADES) if baud <= alvo]


def _inteiro(texto: str) -> int:
    try:
        return int(texto)
    except ValueError:
        raise ErroProtocolo(f"Campo numérico inválido: {texto!r}.") from None


def _real(texto: str) -> float:
    try:
        valor = float(texto)
    except ValueError:
        raise ErroProtocolo(f"Campo numérico inválido: {texto!r}.") from None
    if not math.isfinite(valor):
        raise ErroProtocolo(f"Valor não finito: {texto!r}.")
    return valor
