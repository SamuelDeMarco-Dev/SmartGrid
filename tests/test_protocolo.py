"""Protocolo serial do firmware do professor e placa virtual."""

import os
import time

import pytest
import serial

from models import protocolo
from models.protocolo import Amostra, ErroProtocolo, Resposta, TipoResposta
from tools.simulador_placa import PlacaVirtual


# ----------------------------------------------------------------------
# Enquadramento
# ----------------------------------------------------------------------
def test_comandos_do_desktop():
    assert protocolo.iniciar(115200) == b"!S115200\n"
    assert protocolo.pausar() == b"!P\n"
    assert protocolo.rele(True) == b"!1\n"
    assert protocolo.rele(False) == b"!0\n"
    with pytest.raises(ErroProtocolo):
        protocolo.iniciar(57600)


def test_amostra_montada_como_o_firmware_e_interpretada():
    linha = protocolo.amostra(512, 300, True, False)

    # Mesmo XOR do loop() do firmware, sobre "DAT,512,300,1,0".
    assert linha == f"$DAT,512,300,1,0*{protocolo.checksum('DAT,512,300,1,0')}\r\n".encode()
    assert protocolo.interpretar(linha) == Amostra(512, 300, True, False)


def test_checksum_e_o_xor_do_corpo():
    assert protocolo.checksum("DAT") == f"{ord('D') ^ ord('A') ^ ord('T'):02X}"


@pytest.mark.parametrize(
    ("linha", "esperado"),
    [
        (b">>> ACK: INICIANDO EM 38400 BPS <<<\r\n", Resposta(TipoResposta.INICIANDO, 38400, "INICIANDO EM 38400 BPS")),
        (b">>> ACK: RELE LIGADO <<<\r\n", Resposta(TipoResposta.RELE_LIGADO, texto="RELE LIGADO")),
        (b">>> ACK: RELE DESLIGADO <<<\r\n", Resposta(TipoResposta.RELE_DESLIGADO, texto="RELE DESLIGADO")),
        (b">>> ACK: RELE ALTERNADO <<<\r\n", Resposta(TipoResposta.RELE_ALTERNADO, texto="RELE ALTERNADO")),
    ],
)
def test_respostas_de_texto(linha, esperado):
    assert protocolo.interpretar(linha) == esperado


def test_pausa_e_erro_de_baud():
    pausa = protocolo.resposta_pausada().split(b"\n", 1)[1]
    assert protocolo.interpretar(pausa).tipo is TipoResposta.PAUSADA
    erro = protocolo.interpretar(protocolo.resposta_baud_invalido())
    assert erro.tipo is TipoResposta.ERRO and "BAUD INVALIDO" in erro.texto


@pytest.mark.parametrize("texto", protocolo.BANNER)
def test_banner_do_boot_e_ignorado(texto):
    assert protocolo.interpretar(protocolo.linha(texto)) is None


@pytest.mark.parametrize(
    "linha",
    [
        b"$DAT,1,2,1,0",                                   # sem checksum
        b"$DAT,1,2,1,0*00",                                # checksum errado
        b"$XYZ*" + protocolo.checksum("XYZ").encode(),     # pacote desconhecido
        b"$DAT,1,2,1*" + protocolo.checksum("DAT,1,2,1").encode(),            # campos a menos
        b"$DAT,1024,2,1,0*" + protocolo.checksum("DAT,1024,2,1,0").encode(),  # fora do ADC
        b"$DAT,1,2,2,0*" + protocolo.checksum("DAT,1,2,2,0").encode(),        # relé inválido
        b"$DAT,a,2,1,0*" + protocolo.checksum("DAT,a,2,1,0").encode(),        # não numérico
        b">>> ACK: QUALQUER COISA <<<",                    # ACK desconhecido
        b">>> ACK: RELE LIGADO",                           # resposta truncada
        b"\xff\xfe",                                       # lixo de baud rate errado
        b"$" + b"A" * 90 + b"*00",                         # longo demais
    ],
)
def test_linha_invalida_levanta_erro(linha):
    with pytest.raises(ErroProtocolo):
        protocolo.interpretar(linha)


def test_somente_quatro_velocidades_comecando_em_9600():
    assert protocolo.VELOCIDADES == (9600, 19200, 38400, 115200)
    assert protocolo.VELOCIDADE_INICIAL == 9600


def test_negociacao_tenta_do_alvo_ate_9600():
    assert protocolo.velocidades_para_tentar(115200) == [115200, 38400, 19200, 9600]
    assert protocolo.velocidades_para_tentar(9600) == [9600]


def test_conversao_do_adc_e_inversa_aproximada():
    assert protocolo.converter(1023, 1023) == (250.0, 20.0)
    assert protocolo.converter(0, 0) == (0.0, 0.0)
    a0, a1 = protocolo.para_adc(220.0, 5.0)
    tensao, corrente = protocolo.converter(a0, a1)
    assert tensao == pytest.approx(220.0, abs=0.25)
    assert corrente == pytest.approx(5.0, abs=0.02)
    assert protocolo.para_adc(999.0, -1.0) == (1023, 0)


# ----------------------------------------------------------------------
# Placa virtual
# ----------------------------------------------------------------------
@pytest.fixture
def placa():
    if os.name != "posix":
        pytest.skip("Integração com pseudoterminal real requer POSIX (os.openpty e tty).")
    placa = PlacaVirtual.criar().iniciar()
    yield placa
    placa.parar()


@pytest.fixture
def porta(placa):
    conexao = serial.Serial(placa.caminho, 9600, timeout=0.5)
    yield conexao
    conexao.close()


def _proxima(porta, tipo):
    """Lê até a primeira linha do tipo pedido (Amostra ou Resposta)."""
    limite = time.monotonic() + 2
    while time.monotonic() < limite:
        mensagem = protocolo.interpretar(porta.readline())
        if isinstance(mensagem, tipo):
            return mensagem
    raise AssertionError(f"Nenhuma linha {tipo.__name__} recebida.")


def test_placa_liga_com_banner_rele_desligado_e_sem_amostras(placa, porta):
    linhas = [porta.readline() for _ in protocolo.BANNER]
    assert all(protocolo.interpretar(linha) is None for linha in linhas)
    assert porta.readline() == b""
    assert not placa.conversao_habilitada and placa.rele_fechado is False


def test_inicio_troca_velocidade_e_envia_amostras(placa, porta):
    porta.write(protocolo.iniciar(38400))
    resposta = _proxima(porta, Resposta)
    assert resposta.tipo is TipoResposta.INICIANDO and resposta.baud == 38400
    assert placa.baud == 38400 and placa.conversao_habilitada

    amostra = _proxima(porta, Amostra)
    assert amostra.rele_ligado is False and amostra.a0 > 0


def test_baud_invalido_e_recusado(placa, porta):
    porta.write(b"!S57600\n")
    assert _proxima(porta, Resposta).tipo is TipoResposta.ERRO
    assert not placa.conversao_habilitada


def test_rele_e_botao(placa, porta):
    porta.write(protocolo.iniciar(9600))
    _proxima(porta, Resposta)

    porta.write(protocolo.rele(True))
    assert _proxima(porta, Resposta).tipo is TipoResposta.RELE_LIGADO
    assert placa.rele_fechado is True

    placa.pressionar_botao()
    limite = time.monotonic() + 2
    while not _proxima(porta, Amostra).botao_pressionado:
        assert time.monotonic() < limite
    # O botão não atua no relé: só é reportado.
    assert placa.rele_fechado is True


def test_pausa_volta_a_9600_e_para_amostras(placa, porta):
    porta.write(protocolo.iniciar(115200))
    _proxima(porta, Resposta)
    porta.write(protocolo.pausar())
    time.sleep(0.1)

    assert not placa.conversao_habilitada
    assert placa.baud == 9600
    assert placa.comandos_recebidos == ["S115200", "P"]
