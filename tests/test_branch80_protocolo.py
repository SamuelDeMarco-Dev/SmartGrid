"""Contrato do firmware obrigatório, sem dependência de Arduino físico."""
import pytest

from models import protocolo
from models.protocolo import Comando, ErroProtocolo


@pytest.mark.parametrize("baud", protocolo.VELOCIDADES)
def test_iniciar_so_envia_baud_sem_intervalo(baud):
    assert protocolo.iniciar(baud) == f"!S{baud}\n".encode()
    assert protocolo.interpretar(protocolo.iniciar(baud)).campos == (str(baud),)


def test_comandos_rele_e_pausa():
    assert protocolo.rele(True) == b"!1\n"
    assert protocolo.rele(False) == b"!0\n"
    assert protocolo.pausar() == b"!P\n"


def test_checksum_e_o_xor_do_corpo():
    corpo = "DAT,1023,512,1,0"
    esperado = 0
    for byte in corpo.encode():
        esperado ^= byte
    assert protocolo.checksum(corpo) == f"{esperado:02X}"


@pytest.mark.parametrize("terminador", [b"\n", b"\r\n", b""])
def test_dat_aceita_lf_crlf_e_linha_sem_terminador(terminador):
    linha = protocolo.telemetria(1023, 1023, True, True).rstrip(b"\n") + terminador
    tlm = protocolo.decodificar_telemetria(protocolo.interpretar(linha))
    assert tlm == protocolo.Telemetria(1023, 1023, True, True)
    assert tlm.tensao == 250.0 and tlm.corrente == 20.0


def test_extremos_e_corrente_zero_quando_rele_aberto():
    assert protocolo.Telemetria(0, 0, True, False).tensao == 0
    assert protocolo.Telemetria(0, 0, True, False).corrente == 0
    assert protocolo.Telemetria(1023, 1023, False, False).corrente == 0


@pytest.mark.parametrize("campos", [
    (-1, 100, 1, 0), (1024, 100, 1, 0), (100, 1024, 1, 0),
    (100, -1, 1, 0), (100, "nan", 1, 0), ("1.0", 20, 1, 0),
    ("+1", 20, 1, 0), (100, 20, 2, 0), (100, 20, 1, 2),
    (100, 20, "True", 0), (100, 20, 1), (100, 20, 1, 0, 0),
])
def test_dat_incoerente_e_rejeitado_mesmo_com_checksum_correto(campos):
    with pytest.raises(ErroProtocolo):
        protocolo.interpretar(protocolo.montar(Comando.DAT, *campos))


@pytest.mark.parametrize("linha", [
    b"DAT,1,2,1,0*00", b"$DAT,1,2,1,0", b"$DAT,1,2,1,0*XX", b"$DAT,1,2,1,0*00",
    b"\xff\xfe", b"$" + b"A" * 130, b"$TLM,1,220,1,1*00",
    b">>> ACK: RELE 2 <<<", b">>> ACK: RELE LIGADO <<<extra",
])
def test_pacote_invalido_levanta_erro(linha):
    with pytest.raises(ErroProtocolo):
        protocolo.interpretar(linha)


@pytest.mark.parametrize("linha,campos", [
    (b">>> ACK: INICIANDO EM 115200 BPS <<<\r\n", ("S", "115200")),
    (b">>> ACK: RELE LIGADO <<<\n", ("RELE", "1")),
    (b">>> ACK: RELE DESLIGADO <<<\n", ("RELE", "0")),
    (b">>> ACK: CONVERSAO PAUSADA. RETORNANDO A 9600 BPS <<<\n", ("P",)),
])
def test_ack_textual(linha, campos):
    resposta = protocolo.interpretar(linha)
    assert resposta.comando is Comando.ACK and resposta.campos == campos


def test_err_textual_e_velocidades_validas():
    assert protocolo.interpretar(b">>> ERR: BAUD INVALIDO <<<\n").comando is Comando.ERR
    assert protocolo.VELOCIDADES == (9600, 19200, 38400, 115200)
    assert protocolo.velocidades_para_tentar(115200) == [115200, 38400, 19200, 9600]
    with pytest.raises(ErroProtocolo):
        protocolo.iniciar(57600)
