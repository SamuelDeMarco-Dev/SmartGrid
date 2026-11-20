"""Limites MVC: fluxo do Dashboard, worker e consistência entre SQLite e JSON."""

import json
from dataclasses import replace
from datetime import datetime
from threading import Event
from time import monotonic

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QThread, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialogButtonBox, QMessageBox

import database.connection as modulo_db
import models.config_manager as modulo_config
from controllers.aplicacao_controller import AplicacaoController
from controllers.dashboard_controller import DashboardController
from controllers.limites_dialog_controller import LimitesDialogController
from database.connection import Database
from models.barramento import barramento
from models.config_manager import ConfigManager, Configuracao
from models.evento import TipoEvento
from models.leitura import Leitura
from models.regra_alerta import Grandeza, RegraAlerta
from models.repositorio_dispositivos import RepositorioDispositivos
from models.repositorio_eventos import RepositorioEventos
from models.repositorio_regras import RepositorioRegras
from models.servico_corte import ServicoCorteEmergencial
from models.validador_limites import (
    EntradaLimiteInvalida, RascunhoRegra, limites_para_config, validar_regras,
)
from views.limites_dialog_view import LimitesDialogView


def aguardar(condicao):
    prazo = monotonic() + 4
    while not condicao() and monotonic() < prazo:
        QTest.qWait(5)
    assert condicao(), "Resultado assíncrono não chegou"


def botao_salvar(view):
    return view.button_box.button(QDialogButtonBox.StandardButton.Save)


@pytest.fixture
def ambiente(app, tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    monkeypatch.setattr(modulo_config, "CAMINHO_CONFIG", config)
    monkeypatch.setattr(modulo_db, "CAMINHO_DB", tmp_path / "smartgrid.db")
    Database.init_db()
    ConfigManager.salvar(Configuracao())
    repositorio = RepositorioRegras()
    servico = ServicoCorteEmergencial(RepositorioDispositivos(), RepositorioEventos())
    aplicacao = AplicacaoController(Configuracao(), servico)
    dashboard = DashboardController(servico, repositorio, aplicacao)
    dashboard.setParent(dashboard.view)
    try:
        yield dashboard, aplicacao.banco, repositorio, config
    finally:
        aplicacao.encerrar()
        dashboard.view.deleteLater()
        aplicacao.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def abrir(dashboard):
    dashboard.view.botaoConfigurarLimites.click()
    view = dashboard.view.findChild(LimitesDialogView)
    assert view is not None and view.isVisible()
    return view


@pytest.mark.parametrize("texto", ["", "abc", "0", "-1", "inf", "NaN"])
def test_validacao_numerica_no_model(texto):
    rascunho = RascunhoRegra("Corrente", Grandeza.CORRENTE, texto, True)
    with pytest.raises(EntradaLimiteInvalida) as erro:
        validar_regras([rascunho])
    assert (erro.value.indice, erro.value.campo) == (0, "limite_maximo")


def test_limites_do_json_escolhem_minimo_ativo_e_preservam_campos_ausentes():
    regras = [
        RegraAlerta("Corrente ativa A", Grandeza.CORRENTE, 12),
        RegraAlerta("Corrente inativa", Grandeza.CORRENTE, 2, False),
        RegraAlerta("Corrente ativa B", Grandeza.CORRENTE, 8),
        RegraAlerta("Tensão inativa A", Grandeza.TENSAO, 250, False),
        RegraAlerta("Tensão inativa B", Grandeza.TENSAO, 215, False),
        RegraAlerta("Potência", Grandeza.POTENCIA, 100),
    ]
    assert limites_para_config(regras) == {"corrente": 8, "tensao": 215}
    assert limites_para_config(list(reversed(regras))) == limites_para_config(regras)
    assert limites_para_config(regras[-1:]) == {}


def test_carrega_e_fecha_com_consulta_pendente(app, ambiente, monkeypatch):
    dashboard, banco, repositorio, _ = ambiente
    entrou, liberar = Event(), Event()
    consultas = []
    mostrar = []
    obter = repositorio.obter_regras
    chamar = LimitesDialogView.listar_regras

    def consultar():
        consultas.append(QThread.currentThread())
        entrou.set()
        if not liberar.wait(3):
            raise TimeoutError("O teste não liberou a consulta")
        return obter()

    def listar(self, nomes):
        mostrar.append(QThread.currentThread())
        chamar(self, nomes)

    # Aguarda a pré-carga já enfileirada antes de observar a abertura do diálogo.
    pronto = Event()
    banco.executar(pronto.set)
    assert pronto.wait(3)
    monkeypatch.setattr(repositorio, "obter_regras", consultar)
    monkeypatch.setattr(LimitesDialogView, "listar_regras", listar)
    view = abrir(dashboard)
    try:
        assert view.progresso.isVisible()
        assert not botao_salvar(view).isEnabled()
        aguardar(entrou.is_set)
        view.close()
        aguardar(lambda: sip.isdeleted(view))
    finally:
        liberar.set()
    entregue = []
    banco.executar(lambda: True, ao_concluir=entregue.append)
    aguardar(lambda: entregue)
    assert consultas == [banco]
    assert not mostrar

    outra = abrir(dashboard)
    aguardar(lambda: botao_salvar(outra).isEnabled())
    assert mostrar == [app.thread()]
    assert outra.listaRegras.count() == 3


def test_edicao_troca_selecao_e_cancelamento_confirma_descarte(ambiente, monkeypatch):
    dashboard, _, repo, config = ambiente
    anteriores = repo.obter_regras()
    original = config.read_bytes()
    view = abrir(dashboard)
    aguardar(lambda: botao_salvar(view).isEnabled())
    view.input_limite.setText("8,5")
    view.listaRegras.setCurrentRow(1)
    view.input_limite.setText("210")
    view.listaRegras.setCurrentRow(0)
    assert view.input_limite.text() == "8,5"
    assert view.input_limite.placeholderText().endswith("A")
    respostas = iter([QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes])
    monkeypatch.setattr(QMessageBox, "question", lambda *_: next(respostas))

    view.close()
    assert view.isVisible()
    QTest.keyClick(view, Qt.Key.Key_Escape)
    aguardar(lambda: sip.isdeleted(view))

    assert repo.obter_regras() == anteriores
    assert config.read_bytes() == original


def test_nome_duplicado_seleciona_regra_invalida_sem_gravar(ambiente, monkeypatch):
    dashboard, _, repo, config = ambiente
    anteriores = repo.obter_regras()
    original = config.read_bytes()
    view = abrir(dashboard)
    aguardar(lambda: botao_salvar(view).isEnabled())
    view.listaRegras.setCurrentRow(1)
    view.input_nome.setText("  ALERTA   SOBRECORRENTE  ")
    view.listaRegras.setCurrentRow(2)
    avisos = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _pai, _titulo, msg: avisos.append(msg))
    botao_salvar(view).click()

    assert "duplicados" in avisos[0]
    assert view.listaRegras.currentRow() == 1
    assert repo.obter_regras() == anteriores
    assert config.read_bytes() == original


def test_salva_no_worker_uma_vez_e_aplica_limites_no_dashboard(app, ambiente, monkeypatch):
    dashboard, banco, repo, config = ambiente
    view = abrir(dashboard)
    aguardar(lambda: botao_salvar(view).isEnabled())
    assert view.findChild(LimitesDialogController).thread() is app.thread()
    view.input_limite.setText("8,5")
    view.listaRegras.setCurrentRow(1)
    view.input_limite.setText("212,5")
    view.listaRegras.setCurrentRow(2)
    view.input_limite.setText("1234")

    recente = Configuracao(
        porta_com="COM7", baud_rate=38400, timeout_ms=2500, taxa_amostragem_ms=300,
        limites_iniciais={"tensao": 225, "corrente": 10, "potencia": 5400},
        simular_falha_corte=True,
    )
    ConfigManager.salvar(recente, extras={"campo_futuro": {"valor": 11}})
    entrou, liberar = Event(), Event()
    threads_banco, threads_json, notificacoes = [], [], []
    salvar_regras = repo.salvar_regras
    salvar_json = ConfigManager.salvar

    def registrar(regras):
        threads_banco.append(QThread.currentThread())
        entrou.set()
        if not liberar.wait(3):
            raise TimeoutError("O teste não liberou a gravação")
        salvar_regras(regras)

    def gravar_json(config, **opcoes):
        threads_json.append(QThread.currentThread())
        salvar_json(config, **opcoes)

    def ao_alterar(regras):
        notificacoes.append((QThread.currentThread(), regras))

    monkeypatch.setattr(repo, "salvar_regras", registrar)
    monkeypatch.setattr(ConfigManager, "salvar", staticmethod(gravar_json))
    barramento.regras_alteradas.connect(ao_alterar)
    try:
        botao_salvar(view).click()
        aguardar(entrou.is_set)
        assert view.progresso.isVisible() and not botao_salvar(view).isEnabled()
        assert not notificacoes
        view.salvar_solicitado.emit()
        view.close()
        assert view.isVisible()
        liberar.set()
        aguardar(lambda: sip.isdeleted(view))
    finally:
        liberar.set()
        barramento.regras_alteradas.disconnect(ao_alterar)

    assert threads_banco == threads_json == [banco]
    assert [thread for thread, _ in notificacoes] == [app.thread()]
    assert [r.limite_maximo for r in repo.obter_regras()] == [8.5, 212.5, 1234.0]
    assert ConfigManager.carregar() == replace(
        recente, limites_iniciais={"tensao": 212.5, "corrente": 8.5, "potencia": 5400},
    )
    assert json.loads(config.read_text(encoding="utf-8"))["campo_futuro"] == {"valor": 11}
    assert "212.5" in dashboard.view.labelRegrasAtivas.text()
    barramento.leitura_recebida.emit(Leitura(datetime.now(), 215.0, 8.0))
    assert "LIMITE ULTRAPASSADO" in dashboard.view.labelSituacaoProtecao.text()
    aguardar(lambda: any(e.tipo is TipoEvento.COMANDO for e in RepositorioEventos().listar_todos()))
    reaberta = abrir(dashboard)
    aguardar(lambda: botao_salvar(reaberta).isEnabled())
    assert reaberta.input_limite.text() == "8.5"


def test_erro_carregamento_impede_salvar(ambiente, monkeypatch):
    import sqlite3

    dashboard, _, repo, _ = ambiente

    def falhar():
        raise sqlite3.OperationalError("Banco indisponível")

    monkeypatch.setattr(repo, "obter_regras", falhar)
    view = abrir(dashboard)
    aguardar(lambda: "Não foi possível carregar" in view.label_status.text())
    assert not botao_salvar(view).isEnabled()
    assert view.listaRegras.count() == 0


def test_rollback_sqlite_deixa_json_e_regras_intactos(ambiente):
    dashboard, _, repo, config = ambiente
    anteriores = repo.obter_regras()
    original = config.read_bytes()
    view = abrir(dashboard)
    aguardar(lambda: botao_salvar(view).isEnabled())
    view.input_limite.setText("9")
    with Database.transacao() as conn:
        conn.execute(
            "CREATE TRIGGER falhar_regras BEFORE INSERT ON regras_limites "
            "BEGIN SELECT RAISE(ABORT, 'teste de rollback'); END"
        )

    botao_salvar(view).click()
    aguardar(lambda: "Nenhuma alteração foi aplicada" in view.label_status.text())
    assert botao_salvar(view).isEnabled()
    assert repo.obter_regras() == anteriores
    assert config.read_bytes() == original


def test_falha_json_mantem_commit_e_informa_persistencia_parcial(ambiente, monkeypatch):
    dashboard, _, repo, config = ambiente
    original = config.read_bytes()
    view = abrir(dashboard)
    aguardar(lambda: botao_salvar(view).isEnabled())
    view.input_limite.setText("7")
    eventos = []
    barramento.evento_registrado.connect(eventos.append)
    try:
        with monkeypatch.context() as contexto:
            def falhar(_origem, _destino):
                raise PermissionError("Sem acesso ao JSON")

            contexto.setattr(modulo_config.os, "replace", falhar)
            botao_salvar(view).click()
            aguardar(lambda: "Não foi possível atualizar o config.json" in view.label_status.text())
            assert view.isVisible() and botao_salvar(view).isEnabled()
            assert repo.obter_regras()[0].limite_maximo == 7
            assert config.read_bytes() == original
            assert "7 A" in dashboard.view.labelRegrasAtivas.text()
            assert [e.descricao for e in eventos] == ["Parâmetros de limites atualizados apenas no banco."]
        botao_salvar(view).click()
        aguardar(lambda: sip.isdeleted(view))
        assert ConfigManager.carregar().limites_iniciais["corrente"] == 7
        assert eventos[-1].descricao == "Parâmetros de limites atualizados."
    finally:
        barramento.evento_registrado.disconnect(eventos.append)


def test_grandeza_ausente_preserva_limite_vigente(ambiente):
    dashboard, _, repo, _ = ambiente
    view = abrir(dashboard)
    aguardar(lambda: botao_salvar(view).isEnabled())
    ConfigManager.atualizar(limites_iniciais={"corrente": 25})
    view.combo_grandeza.setCurrentIndex(view.combo_grandeza.findData(Grandeza.POTENCIA))
    view.listaRegras.setCurrentRow(1)
    view.input_limite.setText("207")

    botao_salvar(view).click()
    aguardar(lambda: sip.isdeleted(view))

    assert all(regra.grandeza is not Grandeza.CORRENTE for regra in repo.obter_regras())
    assert ConfigManager.carregar().limites_iniciais == {
        "tensao": 207.0, "corrente": 25.0, "potencia": 3500.0,
    }


def test_destruicao_da_tela_durante_gravacao_mantem_commit_e_notifica(ambiente, monkeypatch):
    dashboard, _, repo, _ = ambiente
    view = abrir(dashboard)
    aguardar(lambda: botao_salvar(view).isEnabled())
    view.input_limite.setText("6")
    entrou, liberar = Event(), Event()
    salvar = repo.salvar_regras

    def salvar_depois(regras):
        entrou.set()
        if not liberar.wait(3):
            raise TimeoutError("O teste não liberou a gravação")
        salvar(regras)

    monkeypatch.setattr(repo, "salvar_regras", salvar_depois)
    botao_salvar(view).click()
    try:
        aguardar(entrou.is_set)
        view.deleteLater()
        aguardar(lambda: sip.isdeleted(view))
    finally:
        liberar.set()

    aguardar(lambda: "6 A" in dashboard.view.labelRegrasAtivas.text())
    assert repo.obter_regras()[0].limite_maximo == 6
    assert ConfigManager.carregar().limites_iniciais["corrente"] == 6
