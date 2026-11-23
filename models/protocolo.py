"""Protocolo serial entre o desktop e a placa de medição (Arduino UNO).

É o protocolo do firmware fornecido pelo professor
(``firmware/smartgrid_placa/smartgrid_placa.ino``). O desktop se adapta a
ele; a placa não é alterada.

Desktop → placa
---------------
Comandos de texto ``!<CMD>`` terminados em ``\\n`` (espaços são ignorados):

* ``!S<baud>`` — inicia a conversão e troca para ``<baud>`` (9600, 19200,
  38400 ou 115200). A placa responde ``>>> ACK: INICIANDO EM <baud> BPS <<<``
  ainda na velocidade antiga, ou ``>>> ERR: BAUD INVALIDO... <<<``.
* ``!P`` — pausa a conversão e volta a 9600 bps
  (``>>> ACK: CONVERSAO PAUSADA. RETORNANDO A 9600 BPS <<<``).
* ``!1`` / ``!0`` — liga / desliga o relé
  (``>>> ACK: RELE LIGADO <<<`` / ``>>> ACK: RELE DESLIGADO <<<``).

Placa → desktop
---------------
Com a conversão habilitada, o Timer1 amostra a 200 Hz e cada amostra sai
como uma linha enquadrada::

    $DAT,<A0>,<A1>,<rele>,<botao>*CS\\r\\n

``A0``/``A1`` são contagens do ADC (0–1023), ``rele`` é 1 com o relé
acionado e ``botao`` é 1 com o botão pressionado. ``CS`` é o XOR dos bytes
entre ``$`` e ``*`` em dois dígitos hexadecimais maiúsculos.

A placa não tem watchdog nem ``seq``, e o período de amostragem é fixo: o
desktop converte as contagens em volts/ampères e agrega as amostras no
intervalo configurado (ver ``models/serial_worker.py``). Ao ligar, ela
imprime um banner de texto, que é ignorado.

Este módulo é Python puro: não conhece Qt nem pyserial.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum, auto

#: Velocidades aceitas pela placa, da mais lenta para a mais rápida.
VELOCIDADES = (9600, 19200, 38400, 115200)

#: Toda conexão começa aqui; é a velocidade da placa ao ligar e após ``!P``.
VELOCIDADE_INICIAL = VELOCIDADES[0]

#: Faixa do período de agregação da telemetria no desktop, em milissegundos.
INTERVALO_MINIMO_MS = 100
INTERVALO_MAXIMO_MS = 5000

#: Período do Timer1 da placa (``OCR1A = 1249``, prescaler 64 → 200 Hz).
PERIODO_AMOSTRA_S = 0.005

#: Maior contagem do ADC de 10 bits do UNO.
ADC_MAXIMO = 1023

#: Grandeza lida com a entrada analógica em 5 V (potenciômetros da bancada).
TENSAO_FUNDO_ESCALA = 250.0
CORRENTE_FUNDO_ESCALA = 20.0

INICIO_COMANDO = "!"
INICIO = "$"
SEPARADOR_CS = "*"
TAMANHO_MAXIMO = 80

#: Primeiro campo das amostras enquadradas.
DAT = "DAT"

_RESPOSTA = re.compile(r"^>>>\s*(ACK|ERR):\s*(.*?)\s*<<<$")
_INICIANDO = re.compile(r"^INICIANDO EM (\d+) BPS$")


class ErroProtocolo(ValueError):
    """Linha malformada, corrompida ou incoerente com o protocolo."""


class TipoResposta(Enum):
    """Respostas de texto ``>>> ACK|ERR: ... <<<`` da placa."""

    INICIANDO = auto()
    PAUSADA = auto()
    RELE_LIGADO = auto()
    RELE_DESLIGADO = auto()
    RELE_ALTERNADO = auto()
    ERRO = auto()


@dataclass(frozen=True, slots=True)
class Resposta:
    tipo: TipoResposta
    baud: int | None = None
    texto: str = ""


@dataclass(frozen=True, slots=True)
class Amostra:
    """Conteúdo de uma linha ``DAT``: contagens brutas e estados digitais."""

    a0: int
    a1: int
    rele_ligado: bool
    botao_pressionado: bool


# ----------------------------------------------------------------------
# Enquadramento
# ----------------------------------------------------------------------
def checksum(corpo: str) -> str:
    """XOR dos bytes do corpo, em dois dígitos hexadecimais."""
    valor = 0
    for byte in corpo.encode("ascii"):
        valor ^= byte
    return f"{valor:02X}"


def interpretar(linha: bytes | str) -> Amostra | Resposta | None:
    """Classifica uma linha recebida da placa.

    Devolve a :class:`Amostra` ou a :class:`Resposta`; ``None`` para texto
    informativo (banner). Levanta :class:`ErroProtocolo` se a linha parecer
    uma amostra ou resposta, mas estiver corrompida.
    """
    if isinstance(linha, bytes):
        try:
            linha = linha.decode("ascii")
        except UnicodeDecodeError:
            raise ErroProtocolo("Linha com bytes fora do ASCII.") from None

    linha = linha.strip()
    if len(linha) > TAMANHO_MAXIMO:
        raise ErroProtocolo("Linha maior que o tamanho máximo.")
    if linha.startswith(INICIO):
        return _interpretar_amostra(linha)
    if linha.startswith(">>>"):
        return _interpretar_resposta(linha)
    return None


def _interpretar_amostra(linha: str) -> Amostra:
    if SEPARADOR_CS not in linha:
        raise ErroProtocolo(f"Amostra sem checksum: {linha!r}.")
    corpo, _, recebido = linha[1:].rpartition(SEPARADOR_CS)
    if recebido.upper() != checksum(corpo):
        raise ErroProtocolo(f"Checksum inválido em {linha!r}.")

    nome, *campos = corpo.split(",")
    if nome != DAT:
        raise ErroProtocolo(f"Pacote desconhecido: {nome!r}.")
    if len(campos) != 4:
        raise ErroProtocolo("Amostra deve ter 4 campos.")
    a0, a1, rele, botao = (_inteiro(campo) for campo in campos)
    if not (0 <= a0 <= ADC_MAXIMO and 0 <= a1 <= ADC_MAXIMO):
        raise ErroProtocolo(f"Contagem do ADC fora da faixa em {linha!r}.")
    if rele not in (0, 1) or botao not in (0, 1):
        raise ErroProtocolo(f"Estado digital inválido em {linha!r}.")
    return Amostra(a0, a1, rele == 1, botao == 1)


def _interpretar_resposta(linha: str) -> Resposta | None:
    combinacao = _RESPOSTA.match(linha)
    if combinacao is None:
        raise ErroProtocolo(f"Resposta malformada: {linha!r}.")
    tipo, texto = combinacao.groups()
    if tipo == "ERR":
        return Resposta(TipoResposta.ERRO, texto=texto)

    iniciando = _INICIANDO.match(texto)
    if iniciando:
        return Resposta(TipoResposta.INICIANDO, baud=int(iniciando.group(1)), texto=texto)
    if texto.startswith("CONVERSAO PAUSADA"):
        return Resposta(TipoResposta.PAUSADA, texto=texto)
    fixas = {
        "RELE LIGADO": TipoResposta.RELE_LIGADO,
        "RELE DESLIGADO": TipoResposta.RELE_DESLIGADO,
        "RELE ALTERNADO": TipoResposta.RELE_ALTERNADO,
    }
    if texto in fixas:
        return Resposta(fixas[texto], texto=texto)
    raise ErroProtocolo(f"ACK desconhecido: {texto!r}.")


# ----------------------------------------------------------------------
# Comandos do desktop
# ----------------------------------------------------------------------
def comando(texto: str) -> bytes:
    """``b"!<texto>\\n"``, pronto para ``write``."""
    return f"{INICIO_COMANDO}{texto}\n".encode("ascii")


def iniciar(baud: int) -> bytes:
    """``!S<baud>``: inicia a conversão na velocidade pedida."""
    validar_velocidade(baud)
    return comando(f"S{baud}")


def pausar() -> bytes:
    """``!P``: pausa a conversão; a placa volta a 9600 bps."""
    return comando("P")


def rele(ligado: bool) -> bytes:
    """``!1`` liga o relé (carga energizada); ``!0`` desliga (corte)."""
    return comando("1" if ligado else "0")


# ----------------------------------------------------------------------
# Linhas da placa (usadas pela placa virtual e pelos testes)
# ----------------------------------------------------------------------
BANNER = (
    "",
    "========================================",
    "PLACA EM ESPERA (9600 baud)",
    "Envie !S115200 para iniciar nessa taxa",
    "Envie !P para pausar e voltar para 9600",
    "========================================",
)


def linha(texto: str) -> bytes:
    """Como o ``Serial.println`` do Arduino: termina em ``\\r\\n``."""
    return f"{texto}\r\n".encode("ascii")


def amostra(a0: int, a1: int, rele_ligado: bool, botao_pressionado: bool) -> bytes:
    corpo = f"{DAT},{a0},{a1},{int(rele_ligado)},{int(botao_pressionado)}"
    return linha(f"{INICIO}{corpo}{SEPARADOR_CS}{checksum(corpo)}")


def resposta_iniciando(baud: int) -> bytes:
    return linha(f">>> ACK: INICIANDO EM {baud} BPS <<<")


def resposta_pausada() -> bytes:
    # O firmware imprime F("\n>>> ACK: ...") com println: linha vazia antes.
    return b"\n" + linha(">>> ACK: CONVERSAO PAUSADA. RETORNANDO A 9600 BPS <<<")


def resposta_rele(ligado: bool) -> bytes:
    return linha(f">>> ACK: RELE {'LIGADO' if ligado else 'DESLIGADO'} <<<")


def resposta_rele_alternado() -> bytes:
    return linha(">>> ACK: RELE ALTERNADO <<<")


def resposta_baud_invalido() -> bytes:
    return linha(">>> ERR: BAUD INVALIDO. Use 9600, 19200, 38400 ou 115200 <<<")


# ----------------------------------------------------------------------
# Conversão e validação
# ----------------------------------------------------------------------
def converter(a0: float, a1: float) -> tuple[float, float]:
    """Contagens (ou médias de contagens) do ADC → ``(tensão V, corrente A)``."""
    tensao = a0 * TENSAO_FUNDO_ESCALA / ADC_MAXIMO
    corrente = a1 * CORRENTE_FUNDO_ESCALA / ADC_MAXIMO
    if not (math.isfinite(tensao) and math.isfinite(corrente)):
        raise ErroProtocolo("Valor não finito na conversão do ADC.")
    return tensao, corrente


def para_adc(tensao: float, corrente: float) -> tuple[int, int]:
    """Inverso de :func:`converter`, saturado na faixa do ADC (placa virtual)."""

    def contagem(valor: float, fundo_escala: float) -> int:
        return max(0, min(ADC_MAXIMO, round(valor * ADC_MAXIMO / fundo_escala)))

    return contagem(tensao, TENSAO_FUNDO_ESCALA), contagem(corrente, CORRENTE_FUNDO_ESCALA)


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
