"""Dashboard em MVC: proteção no Model e gráfico em dois modos (issue #60)."""

import time
from datetime import datetime, timedelta

import pytest

import database.connection as modulo_conexao
from controllers.aplicacao_controller import AplicacaoController
from controllers.dashboard_controller import DashboardController
from database.connection import Database
from models.avaliador_protecao import AvaliadorProtecao, NivelProtecao, setpoint_das_regras
from models.barramento import barramento
from models.config_manager import Configuracao
from models.estado_disjuntor import EstadoDisjuntor
from models.leitura import Leitura
from models.regra_alerta import Grandeza, RegraAlerta
from models.repositorio_dispositivos import RepositorioDispositivos
from models.repositorio_eventos import RepositorioEventos
from models.repositorio_leituras import RepositorioLeituras
from models.repositorio_regras import RepositorioRegras
from models.servico_corte import ServicoCorteEmergencial
from models.porta_serial import ParametrosConexao
from models.situacao_disjuntor import ConfirmacaoDisjuntor


def esperar(app, condicao, prazo=5.0):
    limite = time.monotonic() + prazo
    while time.monotonic() < limite:
        app.processEvents()
        if condicao():
            return True
        time.sleep(0.01)
    return False


def _leitura(potencia, tensao=220.0):
    return Leitura(datetime.now(), tensao=tensao, corrente=potencia / tensao)


# ----------------------------------------------------------------------
# Model: avaliador de proteção
# ----------------------------------------------------------------------
def test_faixas_normal_atencao_e_critico():
    avaliador = AvaliadorProtecao(setpoint_w=1000)

    assert avaliador.avaliar(_leitura(500)).nivel is NivelProtecao.NORMAL
    assert avaliador.avaliar(_leitura(950)).nivel is NivelProtecao.ATENCAO
    assert avaliador.avaliar(_leitura(1100)).nivel is NivelProtecao.CRITICO


def test_nova_violacao_so_na_transicao():
    avaliador = AvaliadorProtecao(setpoint_w=1000)

    primeiras = [avaliador.avaliar(_leitura(1100)).nova_violacao for _ in range(3)]
    avaliador.avaliar(_leitura(100))
    depois_de_normalizar = avaliador.avaliar(_leitura(1100)).nova_violacao

    assert primeiras == [True, False, False]
    assert depois_de_normalizar is True


def test_regra_de_tensao_e_setpoint_das_regras():
    regras = [
        RegraAlerta("Sobretensão", Grandeza.TENSAO, 230.0, True),
        RegraAlerta("Pico", Grandeza.POTENCIA, 2800.0, True),
    ]
    avaliador = AvaliadorProtecao(regras, setpoint_w=5000)

    avaliacao = avaliador.avaliar(_leitura(100, tensao=240))

    assert avaliacao.nivel is NivelProtecao.CRITICO
    assert "Sobretensão" in avaliacao.texto
    assert setpoint_das_regras(regras) == 2800.0


# ----------------------------------------------------------------------
# Controller + View
# ----------------------------------------------------------------------
@pytest.fixture
def ambiente(app, tmp_path, monkeypatch, serial_memoria):
    monkeypatch.setattr(modulo_conexao, "CAMINHO_DB", tmp_path / "smartgrid.db")
    Database.init_db()
    servico = ServicoCorteEmergencial(RepositorioDispositivos(), RepositorioEventos())
    aplicacao = AplicacaoController(Configuracao(), servico)
    dashboard = DashboardController(servico, RepositorioRegras(), aplicacao)
    pronto = []
    aplicacao.banco.executar(lambda: None, ao_concluir=pronto.append)
    assert esperar(app, lambda: pronto)
    yield dashboard, aplicacao
    aplicacao.encerrar()
    for sinal, slot in (
        (barramento.evento_registrado, aplicacao.banco.gravar_evento),
        (barramento.estado_disjuntor_alterado, aplicacao._ao_estado_disjuntor),
        (barramento.leitura_recebida, dashboard._ao_nova_amostra),
        (barramento.status_conexao_alterado, dashboard._ao_status_conexao),
        (barramento.regras_alteradas, dashboard._aplicar_regras),
        (aplicacao.situacao_disjuntor_alterada, dashboard._ao_estado_alterado),
    ):
        sinal.disconnect(slot)


def test_regras_carregadas_do_banco_em_segundo_plano(app, ambiente):
    dashboard, _ = ambiente

    assert esperar(app, lambda: "Regras ativas" in dashboard.view.labelRegrasAtivas.text())


def test_amostra_atualiza_indicadores_e_curva_de_tempo_real(app, ambiente):
    dashboard, _ = ambiente

    barramento.leitura_recebida.emit(_leitura(1000))

    assert dashboard.view.lcdPotencia.value() == pytest.approx(1000, abs=1)
    assert len(dashboard.view.curva_consumo.getData()[0]) == 1


def test_consulta_historica_replota_sem_bloquear(app, ambiente):
    dashboard, aplicacao = ambiente
    # A pré-carga precisa terminar antes de inserir as amostras destinadas
    # apenas à consulta histórica; a ordem de execução das threads varia.
    precarga_concluida = []
    aplicacao.banco.executar(lambda: True, ao_concluir=precarga_concluida.append)
    assert esperar(app, lambda: precarga_concluida)
    inicio = datetime.now() - timedelta(minutes=10)
    RepositorioLeituras().adicionar_lote(
        Leitura(inicio + timedelta(seconds=i), 220.0, 1.0) for i in range(30)
    )
    view = dashboard.view

    view.radioHistorico.setChecked(True)
    view.spinUltimasN.setValue(12)
    view.botaoConsultarHistorico.click()

    assert view.labelStatusGrafico.text() == "Consultando o banco…"
    assert esperar(app, lambda: view.labelStatusGrafico.text() == "12 leituras")
    assert len(view.curva_consumo.getData()[0]) == 12
    assert "histórico" in view.grupoGrafico.title()

    # Em modo histórico, amostras novas não bagunçam a curva consultada.
    barramento.leitura_recebida.emit(_leitura(500))
    assert len(view.curva_consumo.getData()[0]) == 12

    view.radioTempoReal.setChecked(True)
    assert len(view.curva_consumo.getData()[0]) == 1


def test_intervalo_invertido_avisa(app, ambiente, monkeypatch):
    dashboard, _ = ambiente
    avisos = []
    monkeypatch.setattr(dashboard.view, "avisar", lambda _t, msg: avisos.append(msg))

    agora = datetime.now()
    dashboard.view.consulta_intervalo_solicitada.emit(agora, agora - timedelta(hours=1))

    assert avisos and "anterior" in avisos[0]


def _conectar(app, aplicacao, serial_memoria):
    placa = serial_memoria().iniciar()
    aplicacao._config = Configuracao(taxa_amostragem_ms=100)
    aplicacao.conectar_serial(ParametrosConexao(placa.caminho, 115200, 500))
    assert esperar(app, lambda: aplicacao.estado_disjuntor is EstadoDisjuntor.FECHADO)
    return placa


def test_corte_roda_no_worker_e_atualiza_o_badge(app, ambiente, monkeypatch, serial_memoria):
    dashboard, aplicacao = ambiente
    monkeypatch.setattr(dashboard.view, "confirmar", lambda _t, _p: True)
    _conectar(app, aplicacao, serial_memoria)

    dashboard.alternar_carga()

    assert esperar(app, lambda: dashboard.estado_disjuntor is EstadoDisjuntor.ABERTO)
    assert "ABERTO" in dashboard.view.labelBadgeDisjuntor.text()
    assert esperar(app, lambda: aplicacao.situacao_disjuntor.confirmacao is ConfirmacaoDisjuntor.CONFIRMADO)
    assert RepositorioDispositivos().obter_por_tag("DISJ-01")["status"] == "ABERTO"


def test_corte_com_falha_simulada_avisa_rollback(app, ambiente, monkeypatch, serial_memoria):
    dashboard, aplicacao = ambiente
    erros = []
    monkeypatch.setattr(dashboard.view, "confirmar", lambda _t, _p: True)
    monkeypatch.setattr(dashboard.view, "avisar_erro", lambda _t, msg: erros.append(msg))
    dashboard._servico_corte.simular_falha = True
    _conectar(app, aplicacao, serial_memoria)

    dashboard.alternar_carga()

    assert esperar(app, lambda: erros)
    assert "rollback" in erros[0]
    assert dashboard.estado_disjuntor is EstadoDisjuntor.FECHADO


def test_disparo_simulado_avisa_operador(app, ambiente, monkeypatch):
    dashboard, _ = ambiente
    avisos = []
    monkeypatch.setattr(dashboard.view, "avisar", lambda titulo, _m: avisos.append(titulo))

    dashboard.view.disparo_simulado_solicitado.emit()

    assert esperar(app, lambda: avisos == ["Disparo simulado"])
    assert dashboard.estado_disjuntor is None
    assert "DESCONHECIDO" in dashboard.view.labelBadgeDisjuntor.text()
    assert RepositorioDispositivos().obter_por_tag("DISJ-01")["status"] == "ABERTO"
