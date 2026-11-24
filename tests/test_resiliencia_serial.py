"""SerialWorker e aplicação contra a máquina de estados da placa virtual."""

import logging
from time import monotonic

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QThread, QTimer, Qt
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QPushButton

from models import protocolo
from models.barramento import barramento
from models.estado_disjuntor import EstadoDisjuntor
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
    _silenciar(placa)
    total = worker.pacotes_descartados
    adulterado = _adulterar(protocolo.amostra(1023, 5, False, False))

    placa.injetar(adulterado)
    placa.injetar(protocolo.amostra(1000, 5, False, False))
    aguardar_qt(lambda: any(_tensao(args, 1000) for args in leituras))

    assert worker.pacotes_descartados == total + 1
    assert not any(_tensao(args, 1023) for args in leituras)
    assert worker.isRunning() and len(perdas) == 0


def test_banner_no_meio_da_telemetria_e_ignorado_sem_descarte(
    serial_simulada, conectar, aguardar_qt,
):
    placa = serial_simulada()
    worker, leituras = conectar(placa)
    total = worker.pacotes_descartados
    quantidade = len(leituras)

    for texto in protocolo.BANNER:
        placa.injetar(protocolo.linha(texto))
    aguardar_qt(lambda: len(leituras) >= quantidade + 3)

    assert worker.pacotes_descartados == total


def test_velocidade_sem_amostras_pausa_e_negocia_velocidade_menor_em_9600(
    serial_simulada, aguardar_qt,
):
    placa = serial_simulada(falhar_em=(115200,))
    worker = SerialWorker(placa.caminho, 115200, 100, timeout_ms=150)
    velocidades = QSignalSpy(worker.velocidade_negociada)
    leituras = QSignalSpy(worker.leitura_recebida)
    worker.start()
    try:
        aguardar_qt(lambda: len(leituras) > 0, segundos=8)
        assert list(velocidades) == [[38400]]
        assert placa.escritas[0] == (9600, b"!S115200\n")
        assert (115200, b"!P\n") in placa.escritas
        assert (9600, b"!S38400\n") in placa.escritas
        # A recuperação sonda também os outros bauds: não pressupõe watchdog.
    finally:
        worker.parar()
        assert worker.wait(2000)


@pytest.mark.parametrize("estado", ["0", "1"])
def test_ack_rele_valido_e_confirmado_sem_descarte(
    serial_simulada, conectar, aguardar_qt, caplog, estado,
):
    placa = serial_simulada()
    caplog.set_level(logging.INFO, logger="smartgrid")
    worker, leituras = conectar(placa)
    perdas = QSignalSpy(worker.link_perdido)
    confirmacoes = QSignalSpy(worker.rele_confirmado)
    falhas = QSignalSpy(worker.rele_falhou)
    total = worker.pacotes_descartados
    caplog.clear()

    # Sem religamento automático na conexão. Exercita um comando explícito e
    # verifica que o ACK correto aguarda a confirmação pela telemetria.
    placa.silenciar_telemetria = True
    fechado = estado == "1"
    assert worker.enviar_rele(fechado)
    aguardar_qt(lambda: worker._pedido_rele is not None and worker._pedido_rele.ack)
    assert len(confirmacoes) == 0
    # A primeira amostra depois do ACK pode ser anterior à troca: é ignorada.
    placa.injetar(protocolo.amostra(1, 5, not fechado, False))
    placa.injetar(protocolo.amostra(1000, 5, fechado, False))
    aguardar_qt(lambda: any(_tensao(args, 1000) for args in leituras))
    aguardar_qt(lambda: len(confirmacoes) == 1)

    destino = EstadoDisjuntor.FECHADO if fechado else EstadoDisjuntor.ABERTO
    assert list(confirmacoes) == [[destino]]
    assert len(falhas) == 0
    assert worker.pacotes_descartados == total
    assert worker.isRunning() and len(perdas) == 0


@pytest.mark.parametrize(
    "linha",
    [
        b">>> ACK: RELE <<<\r\n",
        b">>> ACK: RELE 2 <<<\r\n",
        b">>> ACK: RELE LIGADO\r\n",
        b">>> ACK: RELE LIGADO EXTRA <<<\r\n",
    ],
)
def test_ack_rele_invalido_e_contabilizado_sem_perder_link(
    serial_simulada, conectar, aguardar_qt, caplog, linha,
):
    placa = serial_simulada()
    caplog.set_level(logging.INFO, logger="smartgrid")
    worker, leituras = conectar(placa)
    perdas = QSignalSpy(worker.link_perdido)
    confirmacoes = QSignalSpy(worker.rele_confirmado)
    total = worker.pacotes_descartados
    caplog.clear()
    _silenciar(placa)
    placa.injetar(linha)
    placa.injetar(protocolo.amostra(1000, 5, False, False))
    aguardar_qt(lambda: worker.pacotes_descartados > total or len(perdas) != 0)

    assert len(perdas) == 0
    assert worker.pacotes_descartados == total + 1
    aguardar_qt(lambda: any(_tensao(args, 1000) for args in leituras))
    assert worker.isRunning()
    assert any(
        registro.levelno == logging.WARNING and "Pacote serial descartado" in registro.message
        for registro in caplog.records
    )
    assert len(confirmacoes) == 0


def test_amostra_com_checksum_invalido_nao_confirma_comando(
    serial_simulada, conectar, aguardar_qt, caplog,
):
    placa = serial_simulada()
    caplog.set_level(logging.INFO, logger="smartgrid")
    worker, leituras = conectar(placa)
    perdas = QSignalSpy(worker.link_perdido)
    confirmacoes = QSignalSpy(worker.rele_confirmado)
    _silenciar(placa)
    placa.perder_rele = True
    assert worker.enviar_rele(True)
    aguardar_qt(lambda: "1" in placa.comandos_recebidos)
    total = worker.pacotes_descartados
    caplog.clear()

    placa.injetar(_adulterar(protocolo.amostra(1023, 5, True, False)))
    placa.injetar(protocolo.amostra(1000, 5, False, False))
    aguardar_qt(lambda: any(_tensao(args, 1000) for args in leituras))

    assert worker.pacotes_descartados == total + 1
    assert len(confirmacoes) == 0
    assert any(
        registro.levelno == logging.WARNING and "Checksum inválido" in registro.message
        for registro in caplog.records
    )
    assert worker.isRunning() and len(perdas) == 0


def _adulterar(pacote: bytes) -> bytes:
    corpo, checksum = pacote.rsplit(b"*", 1)
    return corpo + (b"*00\r\n" if not checksum.startswith(b"00") else b"*01\r\n")


def _tensao(args, contagem: int) -> bool:
    """A leitura publicada é a média da janela; procura a de uma só amostra."""
    return args[0].tensao == pytest.approx(protocolo.converter(contagem, 0)[0])


def _silenciar(placa) -> None:
    """Para as amostras e deixa o worker publicar a janela em andamento."""
    placa.silenciar_telemetria = True
    QTest.qWait(250)
