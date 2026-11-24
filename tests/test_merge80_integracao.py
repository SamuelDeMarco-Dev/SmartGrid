"""Interações entre confirmação D01 da main e proteção prioritária da #80."""
from PyQt6.QtTest import QTest

from models import protocolo
from models.estado_disjuntor import EstadoDisjuntor
from models.porta_serial import ParametrosConexao
from models.situacao_disjuntor import ConfirmacaoDisjuntor


def _conectar(aplicacao, placa, aguardar):
    aplicacao.conectar_serial(ParametrosConexao(placa.caminho, 115200, 500))
    aguardar(lambda: aplicacao.estado_disjuntor is EstadoDisjuntor.FECHADO)


def test_religamento_externo_durante_botao_retido_recebe_novo_corte(
    aplicacao_teste, serial_simulada, aguardar_qt,
):
    placa = serial_simulada(rele_fechado=True)
    _conectar(aplicacao_teste, placa, aguardar_qt)
    placa.disparar()
    aguardar_qt(lambda: aplicacao_teste.situacao_disjuntor.confirmacao is ConfirmacaoDisjuntor.CONFIRMADO
                and aplicacao_teste.estado_disjuntor is EstadoDisjuntor.ABERTO)
    placa.rele_fechado = True
    aguardar_qt(lambda: placa.comandos_recebidos.count('0') == 2 and not placa.rele_fechado)
    assert placa.botao_pressionado
    assert '1' not in placa.comandos_recebidos


def test_corte_fisico_perdido_falha_sem_retransmissao_por_amostra(
    aplicacao_teste, serial_simulada, aguardar_qt,
):
    placa = serial_simulada(rele_fechado=True)
    _conectar(aplicacao_teste, placa, aguardar_qt)
    placa.perder_rele = True
    aplicacao_teste._serial.prazo_confirmacao_s = 0.15
    placa.disparar()
    aguardar_qt(lambda: aplicacao_teste.situacao_disjuntor.confirmacao is ConfirmacaoDisjuntor.FALHA)
    QTest.qWait(200)
    assert placa.comandos_recebidos.count('0') == 1
    assert aplicacao_teste.estado_disjuntor is EstadoDisjuntor.FECHADO
    assert aplicacao_teste.serial_ativa


def test_simulador_responde_err_a_baud_invalido_sem_abortar_parser(serial_simulada):
    placa = serial_simulada()
    porta = placa.abrir(9600, 0.02, write_timeout=1)
    try:
        porta.reset_input_buffer()
        porta.write(b'!S57600\n')
        resposta = protocolo.interpretar(porta.read(1024))
        assert resposta.tipo is protocolo.TipoResposta.ERRO
        assert not placa.conversao_habilitada
        assert placa.comandos_recebidos == ['S57600']
    finally:
        porta.close()