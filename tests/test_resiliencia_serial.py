"""SerialWorker e aplicação contra a máquina de estados da placa virtual."""

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
    def conectar_placa(placa, baud=38400):
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
    assert "desconectada" in perdas[0][0]
    assert estados[-1][0] == EstadoLink.LINK_PERDIDO.name
    assert not placa.conexoes[0].is_open

    nova_placa = serial_simulada(placa.caminho)
    segundo, leituras = conectar(nova_placa)
    assert segundo is not primeiro
    aguardar_qt(lambda: estados[-1][0] == EstadoLink.TELEMETRIA.name)
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


def test_checksum_invalido_e_descartado_sem_interromper_telemetria(
    serial_simulada, conectar, aguardar_qt,
):
    placa = serial_simulada()
    worker, leituras = conectar(placa)
    perdas = QSignalSpy(worker.link_perdido)
    total = worker.pacotes_descartados
    pacote = protocolo.telemetria(999, 999.9, 0.1, True)
    corpo, checksum = pacote.rsplit(b"*", 1)
    adulterado = corpo + (b"*00\n" if checksum != b"00\n" else b"*01\n")

    placa.injetar(adulterado)
    placa.injetar(protocolo.telemetria(1000, 987.6, 0.1, True))
    aguardar_qt(lambda: any(args[0].tensao == 987.6 for args in leituras))

    assert worker.pacotes_descartados == total + 1
    assert all(args[0].tensao != 999.9 for args in leituras)
    assert worker.isRunning() and len(perdas) == 0


def test_sync_falho_reinicia_handshake_em_9600_e_negocia_velocidade_menor(
    serial_simulada, aguardar_qt,
):
    placa = serial_simulada(falhar_sync_em=(115200,))
    worker = SerialWorker(placa.caminho, 115200, 100, timeout_ms=150)
    velocidades = QSignalSpy(worker.velocidade_negociada)
    leituras = QSignalSpy(worker.leitura_recebida)
    worker.start()
    try:
        aguardar_qt(lambda: len(leituras) > 0, segundos=8)
        negociacao = []
        for baud, pacote in placa.escritas.copy():
            mensagem = protocolo.interpretar(pacote)
            if mensagem.comando in (protocolo.Comando.HS, protocolo.Comando.SYNC):
                negociacao.append((baud, mensagem.comando, mensagem.campos))
        assert list(velocidades) == [[38400]]
        assert negociacao == [
            (9600, protocolo.Comando.HS, ("115200", "100")),
            (115200, protocolo.Comando.SYNC, ()),
            (9600, protocolo.Comando.HS, ("38400", "100")),
            (38400, protocolo.Comando.SYNC, ()),
        ]
    finally:
        worker.parar()
        assert worker.wait(2000)


def test_ack_rele_incompleto_e_contabilizado_sem_perder_link(
    serial_simulada, conectar, aguardar_qt,
):
    placa = serial_simulada()
    worker, leituras = conectar(placa)
    perdas = QSignalSpy(worker.link_perdido)
    total = worker.pacotes_descartados
    placa.injetar(protocolo.ack("RELE"))
    placa.injetar(protocolo.telemetria(999, 987.6, 0.1, True))
    aguardar_qt(lambda: worker.pacotes_descartados > total or len(perdas) != 0)

    assert len(perdas) == 0
    assert worker.pacotes_descartados == total + 1
    aguardar_qt(lambda: any(args[0].tensao == 987.6 for args in leituras))
