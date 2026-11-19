"""Mede a GUI enquanto SQLite e serial trabalham em threads próprias."""

from datetime import datetime, timedelta
from threading import Event
from time import monotonic

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QObject, QThread, QTimer, Qt, pyqtSlot
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QLabel, QPushButton

from controllers.dashboard_controller import DashboardController
from database.connection import Database
from models.barramento import barramento
from models.leitura import Leitura
from models.porta_serial import ParametrosConexao
from models.protocolo import Comando
from models.repositorio_leituras import FiltroLeituras, RepositorioLeituras
from models.repositorio_regras import RepositorioRegras


def test_mil_leituras_com_sqlite_ocupado_e_event_loop_ativo(
    app, aplicacao_teste, aguardar_qt, monkeypatch,
):
    banco = aplicacao_teste.banco
    repo = banco._repo_leituras
    gravando = Event()
    adicionar = repo.adicionar_lote
    threads = []

    def observar_gravacao(leituras):
        threads.append(QThread.currentThread())
        gravando.set()
        return adicionar(leituras)

    monkeypatch.setattr(repo, "adicionar_lote", observar_gravacao)
    totais = QSignalSpy(banco.leituras_gravadas)
    instante = datetime.now()
    amostras = [Leitura(instante + timedelta(milliseconds=i), 220, 1) for i in range(1000)]
    cliques = []
    botao = QPushButton("Navegar")
    botao.clicked.connect(lambda: cliques.append((monotonic(), QThread.currentThread())))

    # Uma segunda conexão mantém o lock real de escrita enquanto a GUI
    # processa eventos. O próprio timer libera a transação após observar os cliques.
    conexao = Database.conectar()
    conexao.execute("BEGIN IMMEDIATE")
    durante_lock = []
    timer = QTimer()
    timer.setTimerType(Qt.TimerType.PreciseTimer)
    timer.setInterval(5)

    def processar_clique():
        botao.click()
        if gravando.is_set() and conexao.in_transaction:
            durante_lock.append(cliques[-1])
            if len(durante_lock) == 8:
                conexao.rollback()

    timer.timeout.connect(processar_clique)
    inicio = monotonic()
    timer.start()
    try:
        for leitura in amostras:
            banco.gravar_leitura(leitura)
        aguardar_qt(lambda: len(totais) and totais[-1][0] == 1000)
        fim = monotonic()
    finally:
        timer.stop()
        conexao.close()

    assert RepositorioLeituras().contar() == 1000
    assert len(durante_lock) == 8
    assert threads and all(thread is banco for thread in threads)
    assert all(thread is app.thread() for _, thread in cliques)
    instantes = [inicio, *(instante for instante, _ in cliques), fim]
    maior_pausa = max(b - a for a, b in zip(instantes, instantes[1:]))
    assert maior_pausa <= 0.100, f"GUI ficou {maior_pausa * 1000:.1f} ms sem processar cliques"


def test_consulta_historica_execucao_no_banco_e_slot_na_gui(
    app, aplicacao_teste, aguardar_qt, monkeypatch,
):
    banco = aplicacao_teste.banco
    instante = datetime.now()
    amostras = [Leitura(instante + timedelta(seconds=i), 220, i) for i in range(5)]
    for leitura in amostras:
        banco.gravar_leitura(leitura)
    consultas = []
    consultar = banco._repo_leituras.consultar

    def medir_consulta(filtro):
        consultas.append(QThread.currentThread())
        return consultar(filtro)

    monkeypatch.setattr(banco._repo_leituras, "consultar", medir_consulta)
    label = QLabel()
    recebidos = []

    class Receptor(QObject):
        @pyqtSlot(object, list)
        def apresentar(self, filtro, leituras):
            thread = QThread.currentThread()
            recebidos.append((thread, filtro, leituras))
            if thread is app.thread():
                label.setText(f"{len(leituras)} leituras")

    receptor = Receptor()
    banco.leituras_consultadas.connect(receptor.apresentar)
    filtro = FiltroLeituras.ultimas_n(3)
    banco.consultar_leituras(filtro)
    aguardar_qt(lambda: recebidos)

    assert consultas == [banco]
    assert recebidos == [(app.thread(), filtro, amostras[-3:])]
    assert label.text() == "3 leituras"


def test_encerramento_cooperativo_drena_fila_e_libera_workers_em_ate_2_s(
    aplicacao_teste, serial_simulada, aguardar_qt,
):
    placa = serial_simulada()
    aplicacao_teste.conectar_serial(ParametrosConexao(placa.caminho, 19200, 500))
    serial = aplicacao_teste._serial
    banco = aplicacao_teste.banco
    leituras = QSignalSpy(serial.leitura_recebida)
    aguardar_qt(lambda: len(leituras) > 0)
    finalizado_serial = QSignalSpy(serial.finished)
    finalizado_banco = QSignalSpy(banco.finished)
    salvas_antes = RepositorioLeituras().contar()
    for _ in range(100):
        banco.gravar_leitura(Leitura(datetime.now(), 220, 2))

    inicio = monotonic()
    aplicacao_teste.encerrar()  # chama parar() e wait() nos dois workers
    demora = monotonic() - inicio

    assert demora < 2.0, f"Encerramento demorou {demora:.3f} s"
    assert len(finalizado_serial) == len(finalizado_banco) == 1
    assert serial.isFinished() and banco.isFinished()
    assert not placa.conexoes[0].is_open
    assert placa.pacotes_recebidos[-1].comando is Comando.BYE
    assert RepositorioLeituras().contar() >= salvas_antes + 100
    aguardar_qt(lambda: sip.isdeleted(serial) and sip.isdeleted(banco))
    assert not aplicacao_teste.serial_ativa


def test_pre_carga_preserva_amostra_recebida_durante_consulta(
    aplicacao_teste, aguardar_qt, monkeypatch,
):
    banco = aplicacao_teste.banco
    amostra = Leitura(datetime.now(), 220, 1)
    antiga = Leitura(amostra.instante - timedelta(minutes=1), 220, 2)
    RepositorioLeituras().adicionar_lote([antiga])
    consultar = banco._repo_leituras.consultar
    consultada, liberar = Event(), Event()

    def reter_resultado(filtro):
        resultado = consultar(filtro)
        consultada.set()
        if not liberar.wait(3):
            raise TimeoutError("Não foi possível liberar a pré-carga no teste")
        return resultado

    monkeypatch.setattr(banco._repo_leituras, "consultar", reter_resultado)
    dash = DashboardController(aplicacao_teste._servico_corte, RepositorioRegras(), aplicacao_teste)
    dash.setParent(dash.view)
    try:
        aguardar_qt(consultada.is_set)
        barramento.leitura_recebida.emit(amostra)
        assert list(dash.view.curva_consumo.getData()[1]) == [amostra.potencia]
        fim = []
        banco.executar(lambda: True, ao_concluir=fim.append)
        liberar.set()
        aguardar_qt(lambda: fim)
        instantes, potencias = dash.view.curva_consumo.getData()
        assert list(instantes) == [antiga.instante.timestamp(), amostra.instante.timestamp()]
        assert list(potencias) == [antiga.potencia, amostra.potencia]
    finally:
        liberar.set()
        dash.view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
