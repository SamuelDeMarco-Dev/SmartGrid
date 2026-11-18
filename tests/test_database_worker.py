"""DatabaseWorker, tabela de leituras e eventos vinculados (issue #58)."""

import time
from datetime import datetime, timedelta

import pytest

import database.connection as modulo_conexao
from database.connection import Database
from models.barramento import barramento
from models.database_worker import DatabaseWorker
from models.evento import Evento, TipoEvento
from models.leitura import Leitura
from models.repositorio_leituras import FiltroLeituras, RepositorioLeituras


def esperar(app, condicao, prazo=5.0):
    limite = time.monotonic() + prazo
    while time.monotonic() < limite:
        app.processEvents()
        if condicao():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def banco(tmp_path, monkeypatch):
    monkeypatch.setattr(modulo_conexao, "CAMINHO_DB", tmp_path / "smartgrid.db")
    Database.init_db()


@pytest.fixture
def worker(app, banco):
    worker = DatabaseWorker()
    worker.start()
    yield worker
    worker.parar()
    assert worker.wait(3000)


def _leituras(n, inicio=datetime(2026, 11, 17, 10, 0, 0)):
    return [
        Leitura(inicio + timedelta(seconds=i), tensao=220.0, corrente=float(i))
        for i in range(n)
    ]


# ----------------------------------------------------------------------
# Repositório
# ----------------------------------------------------------------------
def test_lote_grava_potencia_calculada(banco):
    repo = RepositorioLeituras()
    repo.adicionar_lote(_leituras(3))

    conexao = Database.conectar()
    potencias = [linha[0] for linha in conexao.execute("SELECT potencia FROM leituras ORDER BY id")]
    conexao.close()
    assert potencias == [0.0, 220.0, 440.0]


def test_ultimas_n_em_ordem_cronologica(banco):
    repo = RepositorioLeituras()
    repo.adicionar_lote(_leituras(10))

    resultado = repo.consultar(FiltroLeituras.ultimas_n(3))

    assert [leitura.corrente for leitura in resultado] == [7.0, 8.0, 9.0]


def test_intervalo_de_datas(banco):
    repo = RepositorioLeituras()
    inicio = datetime(2026, 11, 17, 10, 0, 0)
    repo.adicionar_lote(_leituras(10, inicio))

    resultado = repo.consultar(
        FiltroLeituras.intervalo(inicio + timedelta(seconds=2), inicio + timedelta(seconds=4))
    )

    assert [leitura.corrente for leitura in resultado] == [2.0, 3.0, 4.0]


@pytest.mark.parametrize(
    "argumentos",
    [
        {},
        {"ultimas": 0},
        {"ultimas": 5, "inicio": datetime(2026, 1, 1), "fim": datetime(2026, 1, 2)},
        {"inicio": datetime(2026, 1, 2), "fim": datetime(2026, 1, 1)},
        {"inicio": datetime(2026, 1, 1)},
    ],
)
def test_filtro_invalido(argumentos):
    with pytest.raises(ValueError):
        FiltroLeituras(**argumentos)


# ----------------------------------------------------------------------
# Worker
# ----------------------------------------------------------------------
def test_grava_leituras_em_segundo_plano(app, worker):
    totais = []
    worker.leituras_gravadas.connect(totais.append)

    for leitura in _leituras(200):
        worker.gravar_leitura(leitura)

    assert esperar(app, lambda: totais and totais[-1] == 200)
    assert RepositorioLeituras().contar() == 200


def test_consulta_historica_volta_por_sinal(app, worker):
    for leitura in _leituras(20):
        worker.gravar_leitura(leitura)
    recebidos = []
    worker.leituras_consultadas.connect(lambda filtro, lista: recebidos.append((filtro, lista)))

    filtro = FiltroLeituras.ultimas_n(5)
    worker.consultar_leituras(filtro)

    assert esperar(app, lambda: recebidos)
    assert recebidos[0][0] == filtro
    assert [leitura.corrente for leitura in recebidos[0][1]] == [15.0, 16.0, 17.0, 18.0, 19.0]


def test_executar_chama_callbacks_na_thread_da_gui(app, worker):
    from PyQt6.QtCore import QThread

    threads = []
    resultados = []
    erros = []

    def concluir(resultado):
        threads.append(QThread.currentThread())
        resultados.append(resultado)

    worker.executar(lambda a, b: a + b, 2, 3, ao_concluir=concluir)
    worker.executar(lambda: 1 / 0, ao_falhar=erros.append)

    assert esperar(app, lambda: resultados and erros)
    assert resultados == [5]
    assert isinstance(erros[0], ZeroDivisionError)
    assert threads == [app.thread()]


def test_evento_gravado_vinculado_ao_dispositivo_e_confirmado(app, worker):
    persistidos = []
    barramento.evento_persistido.connect(persistidos.append)
    evento = Evento.agora(TipoEvento.COMANDO, "Corte de teste", "1.00 A / 220 W")
    try:
        worker.gravar_evento(evento, "DISJ-01")
        assert esperar(app, lambda: evento in persistidos)
    finally:
        barramento.evento_persistido.disconnect(persistidos.append)

    conexao = Database.conectar()
    linha = conexao.execute(
        "SELECT d.tag FROM eventos e JOIN dispositivos d ON d.id = e.dispositivo_id"
    ).fetchone()
    conexao.close()
    assert linha["tag"] == "DISJ-01"


def test_parar_grava_o_que_ja_estava_na_fila(app, banco):
    worker = DatabaseWorker()
    worker.start()
    for leitura in _leituras(50):
        worker.gravar_leitura(leitura)

    worker.parar()

    assert worker.wait(3000)
    assert RepositorioLeituras().contar() == 50
