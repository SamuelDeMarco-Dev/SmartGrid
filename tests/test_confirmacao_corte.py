"""Regressões D01: intenção no SQLite não equivale a atuação confirmada."""

import threading
import time
from types import SimpleNamespace

import pytest

import database.connection as modulo_conexao
from controllers.aplicacao_controller import AplicacaoController
from controllers.dashboard_controller import DashboardController
from database.connection import Database
from models import protocolo
from models.barramento import barramento
from models.config_manager import Configuracao
from models.estado_disjuntor import EstadoDisjuntor
from models.evento import TipoEvento
from models.porta_serial import ParametrosConexao
from models.protocolo import Comando
from models.repositorio_dispositivos import RepositorioDispositivos
from models.repositorio_eventos import RepositorioEventos
from models.repositorio_regras import RepositorioRegras
from models.serial_worker import SerialWorker
from models.servico_corte import ServicoCorteEmergencial
from models.situacao_disjuntor import ConfirmacaoDisjuntor

ABERTO = EstadoDisjuntor.ABERTO
FECHADO = EstadoDisjuntor.FECHADO
CONFIRMADO = ConfirmacaoDisjuntor.CONFIRMADO
PENDENTE = ConfirmacaoDisjuntor.PENDENTE
FALHA = ConfirmacaoDisjuntor.FALHA


def esperar(app, condicao, prazo=3.0):
    limite = time.monotonic() + prazo
    while time.monotonic() < limite:
        app.processEvents()
        if condicao():
            return True
        time.sleep(0.005)
    return False


def comandos_rele(placa):
    return [m.campos for m in placa.pacotes_recebidos if m.comando is Comando.RELE]


@pytest.fixture
def worker_conectado(app, serial_memoria):
    workers = []

    def criar(fechado=True):
        placa = serial_memoria().iniciar()
        placa.rele_fechado = fechado
        worker = SerialWorker(placa.caminho, 38400, 100, 500)
        worker.prazo_confirmacao_s = 0.35
        coletor = SimpleNamespace(reportados=[], confirmados=[], falhas=[], quedas=[])
        worker.disjuntor_reportado.connect(coletor.reportados.append)
        worker.rele_confirmado.connect(coletor.confirmados.append)
        worker.rele_falhou.connect(lambda destino, motivo: coletor.falhas.append((destino, motivo)))
        worker.link_perdido.connect(coletor.quedas.append)
        workers.append(worker)
        worker.start()
        assert esperar(app, lambda: coletor.reportados)
        return placa, worker, coletor

    yield criar
    for worker in workers:
        worker.parar()
        assert worker.wait(3000)
    app.processEvents()


@pytest.mark.parametrize("destino", [ABERTO, FECHADO])
@pytest.mark.parametrize("sem_ack", [False, True])
def test_comando_confirma_com_telemetria_com_ou_sem_ack(app, worker_conectado, destino, sem_ack):
    placa, worker, sinais = worker_conectado(fechado=destino is ABERTO)
    placa.omitir_ack = sem_ack

    assert worker.enviar_rele(destino.energizado)

    assert esperar(app, lambda: sinais.confirmados == [destino])
    assert sinais.reportados[-1] is destino
    assert sinais.falhas == []
    # Amostras seguintes não repetem o resultado da operação.
    total = len(sinais.reportados)
    assert esperar(app, lambda: len(sinais.reportados) >= total + 3)
    assert sinais.confirmados == [destino]
    assert len(comandos_rele(placa)) == 1


@pytest.mark.parametrize("destino", [ABERTO, FECHADO])
def test_comando_perdido_detecta_estado_inalterado_e_timeout(app, worker_conectado, destino):
    placa, worker, sinais = worker_conectado(fechado=destino is ABERTO)
    placa.perder_rele = True

    assert worker.enviar_rele(destino.energizado)

    assert esperar(app, lambda: sinais.falhas)
    assert sinais.confirmados == []
    assert sinais.reportados[-1] is destino.alternado()
    assert sinais.falhas[0][0] is destino
    assert "esgotado" in sinais.falhas[0][1]
    total = len(sinais.reportados)
    assert esperar(app, lambda: len(sinais.reportados) >= total + 3)
    assert len(sinais.falhas) == 1
    assert comandos_rele(placa) == [("1" if destino.energizado else "0",)]


@pytest.mark.parametrize("destino", [ABERTO, FECHADO])
def test_ack_nao_supera_telemetria_contraditoria(app, worker_conectado, destino):
    placa, worker, sinais = worker_conectado(fechado=destino is ABERTO)
    placa.ack_sem_atuar = True

    worker.enviar_rele(destino.energizado)

    assert esperar(app, lambda: sinais.falhas)
    assert "posterior reportou" in sinais.falhas[0][1]
    assert sinais.confirmados == []


def test_ack_incorreto_ou_malformado_nao_confirma(app, worker_conectado):
    placa, worker, sinais = worker_conectado()
    placa.ack_sem_atuar = True
    placa.ack_incorreto = True
    worker.enviar_rele(False)
    assert esperar(app, lambda: comandos_rele(placa))
    placa._enviar(protocolo.ack("RELE"))
    placa._enviar(protocolo.ack("RELE", "0", "extra"))

    assert esperar(app, lambda: sinais.falhas)
    assert "esgotado" in sinais.falhas[0][1]
    assert sinais.confirmados == []
    assert worker.pacotes_descartados >= 2


def test_ack_sem_telemetria_nao_confirma_e_esgota_prazo(app, worker_conectado):
    placa, worker, sinais = worker_conectado()
    placa.silenciar_telemetria = True
    worker.enviar_rele(False)

    assert esperar(app, lambda: sinais.falhas)
    assert sinais.confirmados == []
    assert "DESCONHECIDO" in sinais.falhas[0][1]
    assert sinais.quedas == []  # prazo de atuação vence antes do watchdog de telemetria


def test_perda_de_comunicacao_encerra_comando_uma_vez(app, worker_conectado):
    placa, worker, sinais = worker_conectado()
    placa.perder_rele = True
    worker.enviar_rele(False)
    assert esperar(app, lambda: comandos_rele(placa))

    placa.arrancar_cabo()

    assert esperar(app, lambda: sinais.quedas)
    assert worker.wait(3000)
    app.processEvents()
    assert len(sinais.falhas) == 1
    assert sinais.confirmados == []


def test_worker_recusa_comandos_repetidos_e_contraditorios(app, worker_conectado):
    placa, worker, sinais = worker_conectado()
    placa.perder_rele = True

    assert worker.enviar_rele(False)
    assert not worker.enviar_rele(False)
    assert not worker.enviar_rele(True)
    assert esperar(app, lambda: sinais.falhas)
    assert comandos_rele(placa) == [("0",)]

    placa.perder_rele = False
    assert worker.enviar_rele(False)
    assert esperar(app, lambda: sinais.confirmados == [ABERTO])


def test_telemetrias_iguais_confirmam_comandos_sucessivos_sem_ack(app, worker_conectado):
    placa, worker, sinais = worker_conectado()
    placa.omitir_ack = True
    for total in (1, 2):
        assert worker.enviar_rele(True)
        assert esperar(app, lambda: len(sinais.confirmados) == total)
    assert sinais.confirmados == [FECHADO, FECHADO]
    assert sinais.falhas == []
    assert comandos_rele(placa) == [("1",), ("1",)]


@pytest.fixture
def cenario(app, tmp_path, monkeypatch, serial_memoria):
    monkeypatch.setattr(modulo_conexao, "CAMINHO_DB", tmp_path / "smartgrid.db")
    Database.init_db()
    instancias = []

    def criar(inicial=FECHADO, *, conectar=True, placa_fechada=None):
        repo = RepositorioDispositivos()
        with Database.transacao() as conn:
            repo.atualizar_status("DISJ-01", inicial.name, conn)
            repo.atualizar_status("RELE-01", inicial.name, conn)
        servico = ServicoCorteEmergencial(repo, RepositorioEventos())
        aplicacao = AplicacaoController(Configuracao(taxa_amostragem_ms=100), servico, inicial)
        dashboard = DashboardController(servico, RepositorioRegras(), aplicacao)
        erros, avisos, situacoes = [], [], []
        monkeypatch.setattr(dashboard.view, "confirmar", lambda _t, _p: True)
        monkeypatch.setattr(dashboard.view, "avisar_erro", lambda _t, m: erros.append(m))
        monkeypatch.setattr(dashboard.view, "avisar", lambda _t, m: avisos.append(m))
        aplicacao.situacao_disjuntor_alterada.connect(situacoes.append)
        contexto = SimpleNamespace(
            aplicacao=aplicacao, dashboard=dashboard, servico=servico,
            erros=erros, avisos=avisos, situacoes=situacoes,
        )

        def conectar_placa(fechado, **falhas):
            placa = serial_memoria().iniciar()
            placa.rele_fechado = fechado
            for nome, valor in falhas.items():
                setattr(placa, nome, valor)
            aplicacao.conectar_serial(ParametrosConexao(placa.caminho, 38400, 500))
            aplicacao._serial.prazo_confirmacao_s = 0.35
            contexto.placa = placa
            assert esperar(app, lambda: aplicacao.estado_disjuntor is not None)
            return placa

        contexto.conectar = conectar_placa
        instancias.append(contexto)
        if conectar:
            conectar_placa(inicial.energizado if placa_fechada is None else placa_fechada)
            assert esperar(app, lambda: not aplicacao.comando_pendente)
        return contexto

    yield criar
    for c in instancias:
        c.aplicacao.encerrar()
        app.processEvents()
        for sinal, slot in (
            (barramento.evento_registrado, c.aplicacao.banco.gravar_evento),
            (barramento.estado_disjuntor_alterado, c.aplicacao._ao_estado_disjuntor),
            (barramento.leitura_recebida, c.dashboard._ao_nova_amostra),
            (barramento.status_conexao_alterado, c.dashboard._ao_status_conexao),
            (barramento.regras_alteradas, c.dashboard._aplicar_regras),
        ):
            sinal.disconnect(slot)
        c.dashboard.view.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("inicial", [FECHADO, ABERTO])
def test_dashboard_so_confirma_depois_da_telemetria(app, cenario, inicial):
    c = cenario(inicial)
    c.placa.silenciar_telemetria = True
    c.dashboard.alternar_carga()

    assert c.aplicacao.comando_pendente
    assert c.dashboard.estado_disjuntor is inicial
    assert not c.dashboard.view.botaoCorteEmergencia.isEnabled()
    assert esperar(app, lambda: comandos_rele(c.placa))
    assert RepositorioDispositivos().obter_por_tag("DISJ-01")["status"] == inicial.alternado().name
    assert c.aplicacao.situacao_disjuntor.confirmacao is PENDENTE
    assert c.dashboard.estado_disjuntor is inicial  # ACK/COMMIT ainda não confirmam

    c.placa.silenciar_telemetria = False

    assert esperar(app, lambda: c.aplicacao.situacao_disjuntor.confirmacao is CONFIRMADO)
    assert c.dashboard.estado_disjuntor is inicial.alternado()
    assert "CONFIRMADO" in c.dashboard.view.labelBadgeDisjuntor.text()
    assert c.dashboard.view.botaoCorteEmergencia.isEnabled()
    assert c.erros == []


@pytest.mark.parametrize("inicial", [FECHADO, ABERTO])
def test_dashboard_nao_mostra_intencao_como_estado_quando_comando_se_perde(app, cenario, inicial):
    c = cenario(inicial)
    c.placa.perder_rele = True
    c.dashboard.alternar_carga()
    for _ in range(5):
        c.dashboard.alternar_carga()

    assert esperar(app, lambda: c.erros)
    assert c.aplicacao.situacao_disjuntor.confirmacao is FALHA
    assert c.aplicacao.situacao_disjuntor.solicitado is inicial.alternado()
    assert c.dashboard.estado_disjuntor is inicial
    texto = c.dashboard.view.labelBadgeDisjuntor.text()
    assert inicial.name in texto and "FALHA" in texto
    assert "PROTEÇÃO ATIVADA" not in texto
    assert len(comandos_rele(c.placa)) == 1
    # Uma amostra idêntica continua reconciliando a falha sem repetir eventos.
    quantidade = len(c.situacoes)
    assert not esperar(app, lambda: len(c.situacoes) > quantidade, prazo=0.3)
    assert len(c.erros) == 1

    c.placa.perder_rele = False
    c.dashboard.alternar_carga()
    assert esperar(app, lambda: c.aplicacao.situacao_disjuntor.confirmacao is CONFIRMADO)
    assert c.dashboard.estado_disjuntor is inicial.alternado()
    assert len(comandos_rele(c.placa)) == 2


def test_dashboard_mostra_falha_se_ack_contradiz_telemetria(app, cenario):
    c = cenario()
    c.placa.ack_sem_atuar = True
    c.dashboard.alternar_carga()

    assert esperar(app, lambda: c.erros)
    assert c.dashboard.estado_disjuntor is FECHADO
    assert "FECHADO / FALHA" in c.dashboard.view.labelBadgeDisjuntor.text()


def test_desconexao_durante_corte_invalida_indicadores(app, cenario):
    c = cenario()
    c.placa.perder_rele = True
    c.dashboard.alternar_carga()
    assert esperar(app, lambda: comandos_rele(c.placa))

    c.placa.arrancar_cabo()

    assert esperar(app, lambda: not c.aplicacao.serial_ativa)
    assert c.dashboard.estado_disjuntor is None
    assert "DESCONHECIDO" in c.dashboard.view.labelBadgeDisjuntor.text()
    assert not c.dashboard.view.botaoCorteEmergencia.isEnabled()
    assert len(c.erros) == 1


def test_reconexao_com_intencao_aberta_tenta_corte_uma_vez(app, cenario):
    c = cenario(ABERTO)
    c.aplicacao.desconectar_serial()
    assert esperar(app, lambda: not c.aplicacao.serial_ativa)
    placa = c.conectar(True)
    assert esperar(app, lambda: c.aplicacao.situacao_disjuntor.confirmacao is CONFIRMADO)
    assert c.dashboard.estado_disjuntor is ABERTO
    assert comandos_rele(placa) == [("0",)]


def test_reconexao_com_placa_aberta_nao_religa_sem_operador(app, cenario):
    c = cenario(FECHADO)
    c.aplicacao.desconectar_serial()
    assert esperar(app, lambda: not c.aplicacao.serial_ativa)
    placa = c.conectar(False)

    assert esperar(app, lambda: c.avisos)
    assert c.dashboard.estado_disjuntor is ABERTO
    assert RepositorioDispositivos().obter_por_tag("DISJ-01")["status"] == "ABERTO"
    assert comandos_rele(placa) == []
    assert not esperar(app, lambda: placa.rele_fechado, prazo=0.3)


def test_reconciliacao_perdida_mantem_fechado_e_nao_repete_corte(app, cenario):
    c = cenario(ABERTO)
    c.aplicacao.desconectar_serial()
    assert esperar(app, lambda: not c.aplicacao.serial_ativa)
    placa = c.conectar(True, perder_rele=True)

    assert esperar(app, lambda: c.erros)
    assert c.dashboard.estado_disjuntor is FECHADO
    assert "FECHADO / FALHA" in c.dashboard.view.labelBadgeDisjuntor.text()
    assert not esperar(app, lambda: len(comandos_rele(placa)) > 1, prazo=0.4)
    assert comandos_rele(placa) == [("0",)]
    assert len(c.erros) == 1


def test_telemetria_contraditoria_posterior_retira_confirmacao_anterior(app, cenario):
    c = cenario()
    c.dashboard.alternar_carga()
    assert esperar(app, lambda: c.aplicacao.situacao_disjuntor.confirmacao is CONFIRMADO)
    assert c.dashboard.estado_disjuntor is ABERTO

    c.placa.rele_fechado = True  # exemplo: placa reiniciou e reportou carga fechada

    assert esperar(app, lambda: c.aplicacao.situacao_disjuntor.confirmacao is FALHA)
    assert c.dashboard.estado_disjuntor is FECHADO
    texto = c.dashboard.view.labelBadgeDisjuntor.text()
    assert "FECHADO" in texto and "PROTEÇÃO ATIVADA" not in texto
    assert not esperar(app, lambda: len(comandos_rele(c.placa)) > 1, prazo=0.3)


def test_d2_exige_religamento_manual_e_nao_duplica_eventos(app, cenario):
    c = cenario()
    c.placa.disparar()

    assert esperar(app, lambda: c.avisos)
    assert c.dashboard.estado_disjuntor is ABERTO
    assert not esperar(app, lambda: len(c.avisos) > 1, prazo=0.3)
    assert comandos_rele(c.placa) == []
    eventos = RepositorioEventos().listar_todos()
    assert sum("espontaneamente" in e.descricao for e in eventos) == 1

    c.dashboard.alternar_carga()
    assert esperar(app, lambda: c.dashboard.estado_disjuntor is FECHADO)
    assert comandos_rele(c.placa) == [("1",)]


def test_confirmacoes_sucessivas_geram_um_evento_por_operacao(app, cenario):
    c = cenario()
    for esperado in (ABERTO, FECHADO, ABERTO):
        c.dashboard.alternar_carga()
        assert esperar(app, lambda: c.aplicacao.situacao_disjuntor.confirmacao is CONFIRMADO)
        assert c.dashboard.estado_disjuntor is esperado
    assert not esperar(app, lambda: c.aplicacao.comando_pendente, prazo=0.3)
    eventos = RepositorioEventos().listar_todos()
    assert sum(e.tipo is TipoEvento.COMANDO for e in eventos) == 3
    assert sum("confirmado pela telemetria" in e.descricao for e in eventos) == 3
    assert len(comandos_rele(c.placa)) == 3


def test_sem_telemetria_valida_estado_inicial_e_desconhecido(cenario):
    c = cenario(ABERTO, conectar=False)
    assert c.aplicacao.estado_disjuntor is None
    assert "DESCONHECIDO" in c.dashboard.view.labelBadgeDisjuntor.text()
    assert "PROTEÇÃO ATIVADA" not in c.dashboard.view.labelBadgeDisjuntor.text()
    assert not c.aplicacao.solicitar_estado_disjuntor(FECHADO)


def test_simulacao_com_placa_aguarda_confirmacao_antes_do_aviso(app, cenario):
    c = cenario()
    c.placa.silenciar_telemetria = True
    c.aplicacao.simular_disparo()
    assert esperar(app, lambda: comandos_rele(c.placa))
    assert c.avisos == []
    assert c.dashboard.estado_disjuntor is FECHADO

    c.placa.silenciar_telemetria = False

    assert esperar(app, lambda: c.avisos)
    assert c.dashboard.estado_disjuntor is ABERTO
    assert len(c.avisos) == 1


def test_desconexao_antes_do_commit_nao_reenvia_religamento_na_nova_conexao(app, cenario, monkeypatch):
    c = cenario(ABERTO)
    liberar = threading.Event()
    entrou = threading.Event()
    original = c.servico._ponto_de_falha

    def atrasar_commit():
        entrou.set()
        assert liberar.wait(3)
        original()

    monkeypatch.setattr(c.servico, "_ponto_de_falha", atrasar_commit)
    c.dashboard.alternar_carga()
    try:
        assert esperar(app, entrou.is_set)
        c.aplicacao.desconectar_serial()
        assert esperar(app, lambda: not c.aplicacao.serial_ativa)
        placa = c.conectar(False)
    finally:
        liberar.set()

    assert esperar(app, lambda: not c.aplicacao.comando_pendente)
    assert comandos_rele(placa) == []
    assert c.dashboard.estado_disjuntor is ABERTO
    assert len(c.erros) == 1


def test_amostra_invalida_nao_confirma_nem_mantem_link_vivo(app, worker_conectado):
    placa, worker, sinais = worker_conectado()
    placa.silenciar_telemetria = True
    worker.enviar_rele(False)
    assert esperar(app, lambda: comandos_rele(placa))
    placa._enviar(protocolo.montar(Comando.TLM, 100, 220, 1, 9))
    placa._enviar(protocolo.montar(Comando.TLM, 101, -1, 1, 0))

    assert esperar(app, lambda: sinais.quedas)
    assert sinais.confirmados == []
    assert len(sinais.falhas) == 1
    assert worker.pacotes_descartados >= 2
