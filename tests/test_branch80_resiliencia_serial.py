"""Falhas reais no transporte em memória, com baud, boot e DAT do firmware."""
from time import monotonic

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QThread, QTimer, Qt
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QPushButton

from models import protocolo
from models.barramento import barramento
from models.estado_link import EstadoLink
from models.porta_serial import ParametrosConexao
from models.serial_worker import SerialWorker


@pytest.fixture
def conectar(aplicacao_teste, aguardar_qt):
    def conectar_placa(placa, baud=115200):
        aplicacao_teste.conectar_serial(ParametrosConexao(placa.caminho, baud, 150))
        worker = aplicacao_teste._serial
        leituras = QSignalSpy(worker.leitura_recebida)
        aguardar_qt(lambda: len(leituras) > 0)
        return worker, leituras
    return conectar_placa


def test_cabo_removido_emite_link_perdido_e_aceita_nova_conexao(
    aplicacao_teste, serial_simulada, conectar, aguardar_qt,
):
    placa = serial_simulada()
    primeiro, _ = conectar(placa)
    perdas = QSignalSpy(primeiro.link_perdido)
    estados = QSignalSpy(barramento.estado_link_alterado)
    placa.arrancar_cabo()
    aguardar_qt(lambda: len(perdas) == 1 and not aplicacao_teste.serial_ativa)
    aguardar_qt(lambda: sip.isdeleted(primeiro))
    assert estados[-1][0] == EstadoLink.LINK_PERDIDO.name
    assert not placa.conexoes[0].is_open
    nova_placa = serial_simulada(placa.caminho)
    segundo, leituras = conectar(nova_placa)
    assert segundo is not primeiro
    assert leituras[0][0].tensao > 0 and nova_placa.conexoes[0].is_open


def test_silencio_durante_telemetria_processa_cliques_ate_link_perdido(
    app, serial_simulada, conectar, aguardar_qt,
):
    placa = serial_simulada()
    worker, _ = conectar(placa)
    perdas = QSignalSpy(worker.link_perdido)
    cliques = []
    botao = QPushButton("Navegar")
    botao.clicked.connect(lambda: cliques.append((monotonic(), QThread.currentThread())))
    timer = QTimer()
    timer.setTimerType(Qt.TimerType.PreciseTimer)
    timer.setInterval(5)
    timer.timeout.connect(botao.click)
    placa.silenciar()
    inicio = monotonic()
    timer.start()
    try:
        aguardar_qt(lambda: len(perdas) == 1)
        fim = monotonic()
    finally:
        timer.stop()
    assert "sem resposta" in perdas[0][0]
    assert len(cliques) >= 10
    assert all(thread is app.thread() for _, thread in cliques)
    instantes = [inicio, *(instante for instante, _ in cliques), fim]
    assert max(b - a for a, b in zip(instantes, instantes[1:])) <= 0.100
    assert placa.conexoes[0].threads == {worker}


def test_todos_os_dat_invalidos_sao_descartados_sem_afetar_medias(
    serial_simulada, conectar, aguardar_qt,
):
    placa = serial_simulada()
    worker, leituras = conectar(placa)
    total = worker.pacotes_descartados
    placa.injetar(b"$DAT,1023,1,0,0*00\n")
    placa.injetar(protocolo.montar(protocolo.Comando.DAT, 1024, 1, 0, 0))
    placa.injetar(protocolo.montar(protocolo.Comando.DAT, 1, 1, 2, 0))
    aguardar_qt(lambda: worker.pacotes_descartados == total + 3)
    aguardar_qt(lambda: len(leituras) >= 2)
    assert all(args[0].tensao == pytest.approx(900 * 250 / 1023) for args in leituras)
    assert worker.isRunning()


@pytest.mark.parametrize("baud", protocolo.VELOCIDADES)
def test_handshake_aceita_as_quatro_velocidades_e_fecha_porta(
    serial_simulada, aguardar_qt, baud,
):
    placa = serial_simulada()
    worker = SerialWorker(placa.caminho, baud, 100, 150)
    velocidades = QSignalSpy(worker.velocidade_negociada)
    leituras = QSignalSpy(worker.leitura_recebida)
    worker.start()
    try:
        aguardar_qt(lambda: len(leituras) > 0)
        assert list(velocidades) == [[baud]]
        assert placa.escritas[0] == (9600, protocolo.iniciar(baud))
    finally:
        worker.parar()
        assert worker.wait(2000)
    assert not placa.conexoes[0].is_open
    assert placa.pacotes_recebidos[-1].comando is protocolo.Comando.PAUSAR


@pytest.mark.parametrize("opcoes", [dict(perder_ack=True), dict(boot_s=1.0)])
def test_ack_perdido_e_reset_lento_nao_impedem_conexao(serial_simulada, conectar, opcoes):
    placa = serial_simulada(**opcoes)
    worker, leituras = conectar(placa)
    assert leituras and placa.baud == 115200 and worker.isRunning()


@pytest.mark.parametrize("baud_anterior", protocolo.VELOCIDADES)
def test_recupera_placa_em_velocidade_anterior_sem_watchdog(
    serial_simulada, conectar, baud_anterior,
):
    placa = serial_simulada()
    placa.baud = baud_anterior
    placa.estado = "TELEMETRIA"
    placa._proxima_dat = monotonic()
    worker, _ = conectar(placa, baud=19200)
    assert placa.baud == 19200 and worker.isRunning()
    if baud_anterior not in (9600, 19200):
        assert any(pacote == b"!P\n" and baud == baud_anterior
                   for baud, pacote in placa.escritas)
    # Se a placa já transmite no alvo, DAT válido basta para recuperá-la.
    assert not any(pacote == b"!1\n" for _, pacote in placa.escritas)


def test_inicio_recusado_recupera_9600_antes_de_tentar_velocidade_menor(
    serial_simulada, aguardar_qt,
):
    placa = serial_simulada(falhar_inicio_em=(115200,))
    worker = SerialWorker(placa.caminho, 115200, 100, 150)
    worker.PRAZO_PRIMEIRO_HS_S = 0.1
    velocidades = QSignalSpy(worker.velocidade_negociada)
    worker.start()
    try:
        aguardar_qt(lambda: len(velocidades) > 0)
        assert list(velocidades) == [[38400]]
        assert (9600, b"!S38400\n") in placa.escritas
    finally:
        worker.parar()
        assert worker.wait(2000)


def test_ack_textual_nao_confirma_rele_nem_mantem_link_sem_dat(
    serial_simulada, conectar, aguardar_qt,
):
    placa = serial_simulada(rele_fechado=True)
    worker, _ = conectar(placa)
    estados = QSignalSpy(worker.disjuntor_reportado)
    perdas = QSignalSpy(worker.link_perdido)
    placa.injetar(b">>> ACK: RELE DESLIGADO <<<\r\n")
    aguardar_qt(lambda: placa.conexoes[0].in_waiting == 0)
    assert all(args[0].energizado for args in estados)  # ACK não fabrica abertura
    placa.silenciar()
    # ACKs válidos não prolongam o prazo que exige telemetria DAT.
    timer = QTimer()
    timer.timeout.connect(lambda: placa.respostas.extend(b">>> ACK: RELE DESLIGADO <<<\n"))
    timer.start(30)
    try:
        aguardar_qt(lambda: len(perdas) == 1)
    finally:
        timer.stop()
    assert all(args[0].energizado for args in estados)  # ACK não fabrica abertura


def test_cancelar_handshake_libera_porta_sem_esperar_timeout(
    serial_simulada, aguardar_qt,
):
    placa = serial_simulada(boot_s=100)
    worker = SerialWorker(placa.caminho, 115200, 5000, 10000)
    worker.start()
    aguardar_qt(lambda: len(placa.conexoes) > 0)
    inicio = monotonic()
    worker.parar()
    assert worker.wait(2000)
    assert monotonic() - inicio < 1.5
    assert not placa.conexoes[0].is_open
