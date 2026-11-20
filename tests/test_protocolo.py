"""Protocolo serial e placa virtual (issue #56)."""

import os
import time

import pytest
import serial

from models import protocolo
from models.protocolo import Comando, ErroProtocolo
from tools.simulador_placa import AGUARDANDO_LINK, TELEMETRIA, PlacaVirtual


# ----------------------------------------------------------------------
# Enquadramento
# ----------------------------------------------------------------------
def test_montar_e_interpretar_sao_inversos():
    pacote = protocolo.handshake(115200, 1000)

    assert pacote.startswith(b"$HS,115200,1000*") and pacote.endswith(b"\n")
    assert protocolo.interpretar(pacote) == protocolo.Mensagem(
        Comando.HS, ("115200", "1000")
    )


def test_checksum_e_o_xor_do_corpo():
    assert protocolo.checksum("PING") == f"{ord('P') ^ ord('I') ^ ord('N') ^ ord('G'):02X}"


@pytest.mark.parametrize(
    "linha",
    [
        b"HS,9600,1000*00",          # sem $
        b"$HS,9600,1000",            # sem checksum
        b"$HS,9600,1000*00",         # checksum errado
        b"$XYZ*" + protocolo.checksum("XYZ").encode(),  # comando desconhecido
        b"\xff\xfe",                 # lixo de baud rate errado
        b"$" + b"A" * 80 + b"*00",   # longo demais
    ],
)
def test_pacote_invalido_levanta_erro(linha):
    with pytest.raises(ErroProtocolo):
        protocolo.interpretar(linha)


def test_somente_quatro_velocidades_comecando_em_9600():
    assert protocolo.VELOCIDADES == (9600, 19200, 38400, 115200)
    assert protocolo.VELOCIDADE_INICIAL == 9600
    with pytest.raises(ErroProtocolo):
        protocolo.handshake(57600, 1000)


def test_negociacao_tenta_do_alvo_ate_9600():
    assert protocolo.velocidades_para_tentar(115200) == [115200, 38400, 19200, 9600]
    assert protocolo.velocidades_para_tentar(9600) == [9600]


def test_decodificar_telemetria():
    mensagem = protocolo.interpretar(protocolo.telemetria(7, 221.4, 3.25, True))

    tlm = protocolo.decodificar_telemetria(mensagem)

    assert tlm == protocolo.Telemetria(7, 221.4, 3.25, True)


@pytest.mark.parametrize(
    "campos",
    [("1", "220", "2"), ("1", "-1", "2", "1"), ("1", "220", "abc", "1"), ("1", "220", "2", "9")],
)
def test_telemetria_incoerente_e_rejeitada(campos):
    with pytest.raises(ErroProtocolo):
        protocolo.decodificar_telemetria(protocolo.Mensagem(Comando.TLM, campos))


def test_ack_do_handshake_confere_velocidade_e_intervalo():
    mensagem = protocolo.interpretar(protocolo.ack("HS", 38400, 500))

    assert protocolo.decodificar_handshake(mensagem) == (38400, 500)
    assert protocolo.e_ack(mensagem, "HS")
    assert not protocolo.e_ack(mensagem, "SYNC")


# ----------------------------------------------------------------------
# Placa virtual
# ----------------------------------------------------------------------
@pytest.fixture
def placa():
    if os.name != "posix":
        pytest.skip("Integração com pseudoterminal real requer POSIX (os.openpty e tty).")
    placa = PlacaVirtual.criar(tempo_sync_s=0.3, watchdog_s=0.5).iniciar()
    yield placa
    placa.parar()


@pytest.fixture
def porta(placa):
    conexao = serial.Serial(placa.caminho, 9600, timeout=0.5)
    yield conexao
    conexao.close()


def _ler(porta) -> protocolo.Mensagem:
    return protocolo.interpretar(porta.readline())


def test_placa_nao_transmite_antes_do_handshake(placa, porta):
    porta.write(protocolo.ping())
    assert porta.readline() == b""
    assert placa.estado == AGUARDANDO_LINK


def test_handshake_sync_e_telemetria(placa, porta):
    porta.write(protocolo.handshake(38400, 100))
    assert protocolo.decodificar_handshake(_ler(porta)) == (38400, 100)

    porta.baudrate = 38400
    porta.write(protocolo.sync())
    assert protocolo.e_ack(_ler(porta), "SYNC")
    assert placa.estado == TELEMETRIA

    tlm = protocolo.decodificar_telemetria(_ler(porta))
    assert tlm.disjuntor_fechado and tlm.tensao > 0


def test_rele_e_disparo(placa, porta):
    porta.write(protocolo.handshake(9600, 100))
    _ler(porta)
    porta.write(protocolo.sync())
    _ler(porta)

    porta.write(protocolo.rele(False))
    respostas = [_ler(porta) for _ in range(4)]
    assert any(protocolo.e_ack(m, "RELE", "0") for m in respostas)
    assert placa.rele_fechado is False


def test_sem_sync_a_placa_volta_a_9600(placa, porta):
    porta.write(protocolo.handshake(115200, 1000))
    _ler(porta)
    time.sleep(0.5)

    assert placa.estado == AGUARDANDO_LINK
    assert placa.baud == 9600


def test_watchdog_derruba_o_link_sem_ping(placa, porta):
    porta.write(protocolo.handshake(9600, 100))
    _ler(porta)
    porta.write(protocolo.sync())
    _ler(porta)
    time.sleep(0.8)

    assert placa.estado == AGUARDANDO_LINK
