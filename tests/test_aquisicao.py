"""Médias, transições do relé, preservação de picos e limites de memória."""
from datetime import datetime

import pytest
from PyQt6.QtTest import QSignalSpy

from models import protocolo
from models.avaliador_protecao import AvaliadorProtecao, NivelProtecao
from models.database_worker import DatabaseWorker
from models.leitura import Leitura
from models.regra_alerta import Grandeza, RegraAlerta
from models.serial_worker import SerialWorker


def amostra(worker, a0, a1, ligado):
    worker._tratar_telemetria(protocolo.interpretar(protocolo.telemetria(a0, a1, ligado)))


def test_media_linear_e_janela_separada_quando_rele_muda(app):
    worker = SerialWorker("teste", 115200, 5000, 300)
    leituras = QSignalSpy(worker.leitura_recebida)
    amostra(worker, 100, 200, True)
    amostra(worker, 300, 400, True)
    amostra(worker, 900, 1023, False)
    worker._publicar_janela()
    worker.entregar_leituras()
    assert len(leituras) == 2
    ligada, desligada = (args[0] for args in leituras)
    assert ligada.tensao == pytest.approx(200 * 250 / 1023)
    assert ligada.corrente == pytest.approx(300 * 20 / 1023)
    assert ligada.potencia == ligada.tensao * ligada.corrente
    assert desligada.corrente == desligada.potencia == 0.0
    assert desligada.tensao == pytest.approx(900 * 250 / 1023)


def test_pico_curto_nao_se_perde_na_avaliacao_de_tensao_corrente_potencia(app):
    worker = SerialWorker("teste", 115200, 5000, 300)
    leituras = QSignalSpy(worker.leitura_recebida)
    for _ in range(199):
        amostra(worker, 500, 50, True)
    amostra(worker, 1023, 1023, True)
    worker._publicar_janela()
    worker.entregar_leituras()
    leitura = leituras[0][0]
    assert leitura.tensao < 230 and leitura.corrente < 15 and leitura.potencia < 3500
    regras = [RegraAlerta("Tensão", Grandeza.TENSAO, 230, True),
              RegraAlerta("Corrente", Grandeza.CORRENTE, 15, True)]
    avaliacao = AvaliadorProtecao(regras).avaliar(leitura)
    assert avaliacao.nivel is NivelProtecao.CRITICO
    assert {v.grandeza for v in avaliacao.violacoes} == set(Grandeza)
    assert leitura.picos == (250.0, 20.0, 5000.0)


def test_fila_de_entrega_a_gui_tem_limite_e_uma_notificacao_pendente(app):
    worker = SerialWorker("teste", 115200, 5000, 300)
    worker.MAX_LEITURAS_PENDENTES = 3
    notificacoes = QSignalSpy(worker._leituras_disponiveis)
    avisos = QSignalSpy(worker.aviso)
    for _ in range(10):
        amostra(worker, 100, 100, True)
        worker._publicar_janela()
    assert len(worker._entregas) == 3 and worker.leituras_descartadas == 7
    assert len(notificacoes) == len(avisos) == 1
    worker.entregar_leituras()
    assert not worker._entregas and not worker._entrega_agendada


def test_saturacao_sqlite_limita_leituras_sem_descartar_tarefas(app):
    # Sem iniciar o worker, a fila fica deterministicamente congestionada.
    worker = DatabaseWorker()
    worker.MAX_LEITURAS_NA_FILA = 3
    avisos = QSignalSpy(worker.falha_persistencia)
    for _ in range(10):
        worker.gravar_leitura(Leitura(datetime.now(), 220, 1))
    tarefa = worker.executar(lambda: True)
    assert worker._leituras_na_fila == 3 and worker.leituras_descartadas == 7
    assert worker._fila.qsize() == 4 and tarefa in worker._callbacks
    assert len(avisos) == 1
    worker.parar()
    worker.gravar_leitura(Leitura(datetime.now(), 220, 1))
    assert worker._leituras_na_fila == 3  # não há pedidos após a sentinela
