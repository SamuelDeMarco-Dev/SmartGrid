"""Protocolo obrigatório: comandos ! e DAT com XOR.

!S<baud> responde ACK na velocidade anterior e inicia DAT na nova. !P pausa
em 9600 preservando o relé. Não há SYNC, PING ou watchdog no firmware.
O intervalo do desktop controla a agregação; Timer1 continua em 200 Hz.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

VELOCIDADES = (9600, 19200, 38400, 115200)
VELOCIDADE_INICIAL = 9600
INTERVALO_MINIMO_MS = 100
INTERVALO_MAXIMO_MS = 5000
PERIODO_AQUISICAO_S = 0.005
TAMANHO_MAXIMO = 128


class Comando(str, Enum):
    INICIAR = "S"
    PAUSAR = "P"
    RELE = "RELE"
    DAT = "DAT"
    ACK = "ACK"
    ERR = "ERR"


class ErroProtocolo(ValueError):
    """Linha malformada, corrompida ou incoerente com o firmware."""


@dataclass(frozen=True, slots=True)
class Mensagem:
    comando: Comando
    campos: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Telemetria:
    a0: int
    a1: int
    disjuntor_fechado: bool
    botao_pressionado: bool

    @property
    def tensao(self) -> float:
        return self.a0 * 250.0 / 1023.0

    @property
    def corrente(self) -> float:
        # A1 continua indicando o potenciômetro mesmo com o relé aberto.
        return self.a1 * 20.0 / 1023.0 if self.disjuntor_fechado else 0.0


def checksum(corpo: str) -> str:
    valor = 0
    for byte in corpo.encode("ascii"):
        valor ^= byte
    return f"{valor:02X}"


def montar(comando: Comando, *campos: object) -> bytes:
    """Monta DAT (também usado nos testes de pacotes inválidos)."""
    corpo = ",".join([comando.value, *(str(campo) for campo in campos)])
    return f"${corpo}*{checksum(corpo)}\n".encode("ascii")


def interpretar(linha: bytes | str) -> Mensagem:
    if isinstance(linha, bytes):
        try:
            linha = linha.decode("ascii")
        except UnicodeDecodeError:
            raise ErroProtocolo("Linha com bytes fora do ASCII.") from None
    linha = linha.rstrip("\r\n")
    if len(linha) > TAMANHO_MAXIMO:
        raise ErroProtocolo("Linha maior que o tamanho máximo.")
    if linha.startswith("$"):
        corpo, separador, recebido = linha[1:].rpartition("*")
        if not separador or not re.fullmatch(r"[0-9a-fA-F]{2}", recebido):
            raise ErroProtocolo("Pacote sem enquadramento/checksum hexadecimal.")
        if recebido.upper() != checksum(corpo):
            raise ErroProtocolo("Checksum inválido.")
        nome, *campos = corpo.split(",")
        if nome != "DAT":
            raise ErroProtocolo(f"Pacote desconhecido: {nome!r}.")
        mensagem = Mensagem(Comando.DAT, tuple(campos))
        decodificar_telemetria(mensagem)
        return mensagem
    inicio = re.fullmatch(r">>> ACK: INICIANDO EM (9600|19200|38400|115200) BPS <<<", linha)
    if inicio:
        return Mensagem(Comando.ACK, ("S", inicio[1]))
    if linha == ">>> ACK: CONVERSAO PAUSADA. RETORNANDO A 9600 BPS <<<":
        return Mensagem(Comando.ACK, ("P",))
    if linha in (">>> ACK: RELE LIGADO <<<", ">>> ACK: RELE DESLIGADO <<<"):
        return Mensagem(Comando.ACK, ("RELE", "1" if " LIGADO " in linha else "0"))
    if linha.startswith(">>> ERR: ") and linha.endswith(" <<<"):
        return Mensagem(Comando.ERR, (linha[9:-4],))
    # Comandos do host, para o simulador e auditoria dos testes.
    if linha == "!P":
        return Mensagem(Comando.PAUSAR)
    if linha in ("!0", "!1"):
        return Mensagem(Comando.RELE, (linha[1],))
    if re.fullmatch(r"!S\d+", linha):
        baud = int(linha[2:])
        validar_velocidade(baud)
        return Mensagem(Comando.INICIAR, (str(baud),))
    raise ErroProtocolo(f"Linha desconhecida: {linha!r}.")


def iniciar(baud: int) -> bytes:
    validar_velocidade(baud)
    return f"!S{baud}\n".encode("ascii")


def pausar() -> bytes:
    return b"!P\n"


def rele(fechado: bool) -> bytes:
    return b"!1\n" if fechado else b"!0\n"


def telemetria(a0: int, a1: int, disjuntor_fechado: bool, botao: bool = False) -> bytes:
    return montar(Comando.DAT, a0, a1, int(disjuntor_fechado), int(botao))


def decodificar_telemetria(mensagem: Mensagem) -> Telemetria:
    if mensagem.comando is not Comando.DAT or len(mensagem.campos) != 4:
        raise ErroProtocolo("DAT deve ter A0, A1, RELE e BOTAO.")
    a0, a1, rele_txt, botao = mensagem.campos
    if not a0.isascii() or not a1.isascii() or not a0.isdecimal() or not a1.isdecimal():
        raise ErroProtocolo("ADC deve ser inteiro decimal entre 0 e 1023.")
    if not (0 <= int(a0) <= 1023 and 0 <= int(a1) <= 1023):
        raise ErroProtocolo("ADC fora da faixa 0–1023.")
    if rele_txt not in ("0", "1") or botao not in ("0", "1"):
        raise ErroProtocolo("RELE e BOTAO devem ser 0 ou 1.")
    return Telemetria(int(a0), int(a1), rele_txt == "1", botao == "1")


def e_ack(mensagem: Mensagem, *esperado: str) -> bool:
    return mensagem.comando is Comando.ACK and mensagem.campos[:len(esperado)] == esperado


def validar_velocidade(baud: int) -> None:
    if isinstance(baud, bool) or not isinstance(baud, int) or baud not in VELOCIDADES:
        raise ErroProtocolo(f"Velocidade não suportada: {baud} bps.")


def validar_intervalo(intervalo_ms: int) -> None:
    if (isinstance(intervalo_ms, bool) or not isinstance(intervalo_ms, int)
            or not INTERVALO_MINIMO_MS <= intervalo_ms <= INTERVALO_MAXIMO_MS):
        raise ErroProtocolo(f"Intervalo de agregação fora de 100–5000 ms: {intervalo_ms}.")


def velocidades_para_tentar(alvo: int) -> list[int]:
    validar_velocidade(alvo)
    return [baud for baud in reversed(VELOCIDADES) if baud <= alvo]
