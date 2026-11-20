"""SerialWorker contra a placa virtual (issue #57)."""

import os
import time

import pytest

from models.estado_disjuntor import EstadoDisjuntor
from models.estado_link import EstadoLink
from models.protocolo import Comando
from models.serial_worker import SerialWorker
from tools.simulador_placa import AGUARDANDO_LINK, TELEMETRIA, PlacaVirtual


def esperar(app, condicao, prazo=5.0):
    """Roda o event loop até ``condicao()`` ou o prazo — os sinais chegam enfileirados."""
    limite = time.monotonic() + prazo
    while time.monotonic() < limite:
        app.processEvents()
        if condicao():
            return True
        time.sleep(0.01)
    return False


class Coletor:
    """Guarda tudo que o worker emite."""

    def __init__(self, worker: SerialWorker) -> None:
        self.estados: list[str] = []
        self.leituras = []
        self.disjuntor: list[EstadoDisjuntor] = []
        self.velocidades: list[int] = []
        self.quedas: list[str] = []
        worker.estado_alterado.connect(lambda nome, _d: self.estados.append(nome))
        worker.leitura_recebida.connect(self.leituras.append)
        worker.disjuntor_reportado.connect(self.disjuntor.append)
        worker.velocidade_negociada.connect(self.velocidades.append)
        worker.link_perdido.connect(self.quedas.append)


@pytest.fixture
def placa():
    if os.name != "posix":
        pytest.skip("Integração com pseudoterminal real requer POSIX (os.openpty e tty).")
    placa = PlacaVirtual.criar(tempo_sync_s=0.3).iniciar()
    yield placa
    placa.parar()


def _worker(placa, baud=38400, intervalo_ms=100):
    worker = SerialWorker(placa.caminho, baud, intervalo_ms, timeout_ms=300)
    worker.espera_retorno_placa_s = 0.5
    return worker, Coletor(worker)


def _encerrar(app, worker):
    worker.parar()
    assert worker.wait(3000), "a thread serial não encerrou"
    app.processEvents()


def test_handshake_negocia_e_recebe_telemetria(app, placa):
    worker, coletor = _worker(placa)
    worker.start()
    try:
        assert esperar(app, lambda: len(coletor.leituras) >= 3)
        assert coletor.velocidades == [38400]
        assert coletor.estados[:4] == ["ABRINDO_PORTA", "HANDSHAKE", "NEGOCIANDO", "TELEMETRIA"]
        assert coletor.disjuntor == [EstadoDisjuntor.FECHADO]
        # O primeiro pacote enviado pelo desktop é o handshake.
        assert placa.pacotes_recebidos[0].comando is Comando.HS
    finally:
        _encerrar(app, worker)


def test_parar_envia_bye_e_libera_a_porta(app, placa):
    worker, coletor = _worker(placa)
    worker.start()
    assert esperar(app, lambda: placa.estado == TELEMETRIA)

    _encerrar(app, worker)

    assert worker.isFinished()
    assert coletor.estados[-1] == EstadoLink.DESCONECTADO.name
    assert esperar(app, lambda: placa.estado == AGUARDANDO_LINK)
    assert coletor.quedas == []


def test_comando_de_rele_chega_na_placa(app, placa):
    worker, coletor = _worker(placa)
    worker.start()
    try:
        assert esperar(app, lambda: placa.estado == TELEMETRIA)
        worker.enviar_rele(False)
        assert esperar(app, lambda: EstadoDisjuntor.ABERTO in coletor.disjuntor)
        assert placa.rele_fechado is False
    finally:
        _encerrar(app, worker)


def test_disparo_na_placa_e_reportado(app, placa):
    worker, coletor = _worker(placa)
    worker.start()
    try:
        assert esperar(app, lambda: coletor.disjuntor)
        placa.disparar()
        assert esperar(app, lambda: coletor.disjuntor[-1] is EstadoDisjuntor.ABERTO)
    finally:
        _encerrar(app, worker)


@pytest.mark.skipif(os.name != "posix", reason="Pseudoterminal real requer POSIX (os.openpty e tty).")
def test_sync_falho_cai_para_a_velocidade_menor(app):
    placa = PlacaVirtual.criar(tempo_sync_s=0.3, falhar_sync_em=(115200,)).iniciar()
    worker, coletor = _worker(placa, baud=115200)
    worker.start()
    try:
        assert esperar(app, lambda: coletor.velocidades, prazo=8)
        assert coletor.velocidades == [38400]
    finally:
        _encerrar(app, worker)
        placa.parar()


def test_cabo_arrancado_emite_link_perdido_sem_travar(app, placa):
    worker, coletor = _worker(placa)
    worker.start()
    assert esperar(app, lambda: coletor.leituras)

    placa.arrancar_cabo()

    assert esperar(app, lambda: coletor.quedas)
    assert worker.wait(3000)
    assert coletor.estados[-1] == EstadoLink.LINK_PERDIDO.name


def test_porta_inexistente_emite_link_perdido(app):
    worker = SerialWorker("/dev/porta-que-nao-existe", 9600, 1000, 1000)
    coletor = Coletor(worker)
    worker.start()

    assert worker.wait(3000)
    app.processEvents()
    assert coletor.quedas and coletor.estados[-1] == "LINK_PERDIDO"
