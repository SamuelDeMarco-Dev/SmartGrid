"""Histórico MVC, operações assíncronas e atualização incremental (issue #63)."""

import ast
import gc
import sqlite3
import threading
import time
import weakref
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QDate, QEvent, QObject, QThread, QTimer
from PyQt6.QtWidgets import QAbstractItemView, QMessageBox, QPushButton, QWidget

import database.connection as modulo_conexao
from controllers.historico_controller import HistoricoController
from database.connection import Database
from models.barramento import barramento
from models.database_worker import DatabaseWorker
from models.evento import Evento, TipoEvento
from models.historico import ConsultaHistorico, EventoHistorico
from models.repositorio_eventos import RepositorioEventos
from views.historico_view import HistoricoView


def esperar(app, condicao, prazo=8):
    limite = time.monotonic() + prazo
    while time.monotonic() < limite:
        app.processEvents()
        if condicao():
            return True
        time.sleep(0.002)
    return False


class RepositorioObservado(RepositorioEventos):
    def __init__(self):
        self.operacoes = []

    def consultar_historico(self, filtro):
        self.operacoes.append(("consulta", QThread.currentThread()))
        return super().consultar_historico(filtro)

    def consultar_novos(self, apos_id):
        self.operacoes.append(("incremento", QThread.currentThread()))
        return super().consultar_novos(apos_id)

    def limpar(self, conn=None):
        self.operacoes.append(("limpeza", QThread.currentThread()))
        return super().limpar(conn)


@pytest.fixture
def ambiente(app, tmp_path, monkeypatch):
    monkeypatch.setattr(modulo_conexao, "CAMINHO_DB", tmp_path / "smartgrid.db")
    Database.init_db()
    repo = RepositorioObservado()
    banco = DatabaseWorker(repo_eventos=repo)
    banco.start()
    telas = []

    def montar():
        view = HistoricoView()
        controller = HistoricoController(view, banco, repo)
        controller.setParent(view)
        telas.append((controller, view))
        return controller, view

    yield repo, banco, montar
    for controller, view in telas:
        if not sip.isdeleted(controller):
            controller.encerrar()
        if not sip.isdeleted(view):
            view.deleteLater()
    banco.parar()
    assert banco.wait(3000)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def pronta(app, controller):
    assert esperar(app, lambda: controller._consulta_valida and not controller._carregando)


def descricoes(view):
    return [view.tabelaEventos.item(i, 2).text() for i in range(view.tabelaEventos.rowCount())]


def evento(texto="Evento", tipo=TipoEvento.STATUS, dias=0):
    return Evento(datetime.now().replace(microsecond=0) + timedelta(days=dias), tipo, texto)


def test_mvc_e_widgets_somente_leitura(app, ambiente):
    _, _, montar = ambiente
    controller, view = montar()
    pronta(app, controller)

    assert isinstance(controller, QObject) and not isinstance(controller, QWidget)
    assert view.tabelaEventos.columnCount() == 5
    assert view.tabelaEventos.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
    assert view.comboDispositivo.itemText(0) == "Todos os dispositivos"
    caminho = Path(__file__).resolve().parents[1] / "views" / "historico_view.py"
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    importacoes = {
        no.module for no in ast.walk(arvore) if isinstance(no, ast.ImportFrom)
    } | {alias.name for no in ast.walk(arvore) if isinstance(no, ast.Import) for alias in no.names}
    assert not importacoes & {
        "sqlite3", "database.connection", "models.repositorio_eventos", "models.database_worker",
    }


def test_consulta_e_limpeza_rodam_no_worker_callbacks_na_gui(app, ambiente):
    repo, banco, montar = ambiente
    repo.adicionar(evento())
    controller, view = montar()
    recebimentos = []
    mostrar = view.mostrar_eventos
    view.mostrar_eventos = lambda registros: (recebimentos.append(QThread.currentThread()), mostrar(registros))
    pronta(app, controller)

    controller.limpar_historico()
    assert not view.botaoLimparHistorico.isEnabled()
    assert esperar(app, lambda: "Histórico limpo" in view.labelStatusHistorico.text())

    assert {nome for nome, _ in repo.operacoes} == {"consulta", "limpeza"}
    assert all(thread is banco for _, thread in repo.operacoes)
    assert recebimentos and all(thread is app.thread() for thread in recebimentos)


def test_filtros_combinados_e_eventos_sem_dispositivo(app, ambiente):
    repo, _, montar = ambiente
    repo.adicionar(evento("Antigo", TipoEvento.COMANDO, -3), dispositivo_tag="DISJ-01")
    repo.adicionar(evento("Disjuntor", TipoEvento.COMANDO), dispositivo_tag="DISJ-01")
    repo.adicionar(evento("Relé", TipoEvento.COMANDO), dispositivo_tag="RELE-01")
    repo.adicionar(evento("Sem vínculo"))
    controller, view = montar()
    pronta(app, controller)
    assert "Sem dispositivo" in [view.tabelaEventos.item(i, 4).text() for i in range(4)]

    view.comboTipoEvento.setCurrentText("Comando")
    view.dateFiltroInicio.setDate(QDate.currentDate().addDays(-1))
    view.comboDispositivo.setCurrentIndex(view.comboDispositivo.findText("DISJ-01"))
    pronta(app, controller)

    assert descricoes(view) == ["Disjuntor"]


def test_so_exibe_depois_da_persistencia(app, ambiente):
    repo, banco, montar = ambiente
    controller, view = montar()
    pronta(app, controller)
    novo = evento()

    barramento.evento_registrado.emit(novo)
    app.processEvents()
    assert view.tabelaEventos.rowCount() == 0
    assert len(repo.operacoes) == 1

    banco.gravar_evento(novo)
    assert esperar(app, lambda: view.tabelaEventos.rowCount() == 1 and not controller._delta_em_andamento)
    assert descricoes(view) == [novo.descricao]


def test_rajada_agrupada_sem_consulta_completa_nem_duplicatas(app, ambiente):
    repo, banco, montar = ambiente
    controller, view = montar()
    pronta(app, controller)
    novo = evento("Mesmo conteúdo e timestamp")
    gravados = threading.Event()
    for _ in range(40):
        banco.gravar_evento(novo)
    banco.executar(gravados.set)
    assert gravados.wait(3)  # entrega os 40 sinais à GUI juntos

    assert esperar(app, lambda: view.tabelaEventos.rowCount() == 40 and not controller._delta_em_andamento)
    assert [nome for nome, _ in repo.operacoes].count("consulta") == 1
    assert [nome for nome, _ in repo.operacoes].count("incremento") == 1

    barramento.evento_persistido.emit(novo)
    assert esperar(app, lambda: [nome for nome, _ in repo.operacoes].count("incremento") == 2)
    assert esperar(app, lambda: not controller._delta_em_andamento)
    assert view.tabelaEventos.rowCount() == 40
    assert len(controller._ids) == 40  # eventos iguais, mas IDs distintos, são preservados


def test_incremento_respeita_dispositivo_tipo_data_e_ordem(app, ambiente):
    _, banco, montar = ambiente
    controller, view = montar()
    pronta(app, controller)
    view.comboTipoEvento.setCurrentText("Comando")
    view.dateFiltroInicio.setDate(QDate.currentDate().addDays(-1))
    view.comboDispositivo.setCurrentIndex(view.comboDispositivo.findText("DISJ-01"))
    pronta(app, controller)

    banco.gravar_evento(evento("Mais recente", TipoEvento.COMANDO), "DISJ-01")
    assert esperar(app, lambda: view.tabelaEventos.rowCount() == 1 and not controller._delta_em_andamento)
    banco.gravar_evento(evento("DISJ-01 no texto", TipoEvento.COMANDO), "RELE-01")
    banco.gravar_evento(evento("DISJ-01 sem vínculo", TipoEvento.COMANDO))
    banco.gravar_evento(evento("Outro tipo", TipoEvento.ALERTA), "DISJ-01")
    banco.gravar_evento(evento("Antes da data", TipoEvento.COMANDO, -3), "DISJ-01")
    banco.gravar_evento(evento("Atrasado", TipoEvento.COMANDO, -1), "DISJ-01")

    assert esperar(app, lambda: controller._ultimo_id == 6 and not controller._delta_em_andamento)
    assert descricoes(view) == ["Mais recente", "Atrasado"]
    assert [view.tabelaEventos.item(i, 4).text() for i in range(2)] == ["DISJ-01", "DISJ-01"]


def test_evento_durante_snapshot_nao_e_perdido(app, ambiente, monkeypatch):
    repo, banco, montar = ambiente
    lido, liberar = threading.Event(), threading.Event()
    consultar = repo.consultar_historico

    def consulta_lenta(filtro):
        resultado = consultar(filtro)
        lido.set()
        assert liberar.wait(3)
        return resultado

    monkeypatch.setattr(repo, "consultar_historico", consulta_lenta)
    controller, view = montar()
    try:
        assert lido.wait(3)
        banco.gravar_evento(evento("Durante a consulta"), "DISJ-01")
    finally:
        liberar.set()

    assert esperar(app, lambda: view.tabelaEventos.rowCount() == 1 and not controller._delta_em_andamento)
    assert descricoes(view) == ["Durante a consulta"]


class BancoControlado(QObject):
    """Entrega callbacks fora de ordem, inclusive os já invalidados."""

    def __init__(self):
        super().__init__()
        self.tarefas = []
        self.descartados = set()

    def executar(self, funcao, *args, ao_concluir=None, ao_falhar=None):
        self.tarefas.append((ao_concluir, ao_falhar))
        return len(self.tarefas)

    def descartar_callbacks(self, id_tarefa):
        self.descartados.add(id_tarefa)


def test_resultado_e_erro_antigos_nao_sobrescrevem_filtro_recente(app):
    banco = BancoControlado()
    view = HistoricoView()
    controller = HistoricoController(view, banco)
    try:
        view.comboTipoEvento.setCurrentText("Alerta")
        registro = EventoHistorico(2, evento("Atual", TipoEvento.ALERTA), None, None)
        banco.tarefas[1][0](ConsultaHistorico([registro], 2, []))
        pronta(app, controller)
        banco.tarefas[0][0](ConsultaHistorico([], 0, []))
        banco.tarefas[0][1](sqlite3.OperationalError("Erro antigo"))

        assert descricoes(view) == ["Atual"]
        assert view.labelStatusHistorico.text() == "1 eventos"
        assert 1 in banco.descartados
    finally:
        controller.encerrar()
        view.close()
        controller.deleteLater()
        view.deleteLater()


def test_data_futura_invalida_consulta_anterior(app):
    banco = BancoControlado()
    view = HistoricoView()
    controller = HistoricoController(view, banco)
    try:
        view.dateFiltroInicio.setDate(QDate.currentDate().addDays(1))
        banco.tarefas[0][0](ConsultaHistorico([], 0, []))
        assert "data inicial válida" in view.labelStatusHistorico.text()
        assert len(banco.tarefas) == 1
        assert view.botaoAtualizarHistorico.isEnabled()
    finally:
        controller.encerrar()
        controller.deleteLater()
        view.deleteLater()


def test_limpeza_exige_confirmacao_visual(app, ambiente, monkeypatch):
    repo, _, montar = ambiente
    repo.adicionar(evento())
    controller, view = montar()
    pronta(app, controller)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    view.botaoLimparHistorico.click()
    assert not any(nome == "limpeza" for nome, _ in repo.operacoes)
    assert view.tabelaEventos.rowCount() == 1

    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    view.botaoLimparHistorico.click()
    assert not view.botaoLimparHistorico.isEnabled()
    assert esperar(app, lambda: "1 registros removidos" in view.labelStatusHistorico.text())
    assert descricoes(view) == []
    assert repo.listar_todos() == []


def test_eventos_apos_limpeza_e_filtro_alterado_durante_operacao(app, ambiente, monkeypatch):
    repo, banco, montar = ambiente
    repo.adicionar(evento("Antigo"))
    controller, view = montar()
    pronta(app, controller)
    entrou, liberar = threading.Event(), threading.Event()
    limpar = repo.limpar

    def limpeza_lenta():
        entrou.set()
        assert liberar.wait(3)
        return limpar()

    monkeypatch.setattr(repo, "limpar", limpeza_lenta)
    controller.limpar_historico()
    try:
        assert entrou.wait(3)
        view.comboTipoEvento.setCurrentText("Alerta")
        banco.gravar_evento(evento("Depois do DELETE", TipoEvento.ALERTA))
        assert not view.botaoLimparHistorico.isEnabled()
    finally:
        liberar.set()
    assert esperar(app, lambda: not controller._limpando and not controller._carregando and view.tabelaEventos.rowCount() == 1)
    assert descricoes(view) == ["Depois do DELETE"]


def test_falha_sqlite_na_limpeza_preserva_dados_e_libera_interface(app, ambiente):
    repo, _, montar = ambiente
    repo.adicionar(evento("Preservado"))
    with Database.transacao() as conexao:
        conexao.execute("""CREATE TRIGGER impedir_limpeza BEFORE DELETE ON eventos
            BEGIN SELECT RAISE(ABORT, 'Falha de escrita'); END""")
    controller, view = montar()
    pronta(app, controller)

    controller.limpar_historico()

    assert esperar(app, lambda: "Não foi possível limpar" in view.labelStatusHistorico.text())
    assert descricoes(view) == ["Preservado"]
    assert len(repo.listar_todos()) == 1
    assert view.botaoLimparHistorico.isEnabled()


def test_falha_de_consulta_tem_feedback_e_permite_tentar_novamente(app, ambiente, monkeypatch):
    repo, _, montar = ambiente
    consultar = repo.consultar_historico

    def falhar(_filtro):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repo, "consultar_historico", falhar)
    controller, view = montar()
    assert esperar(app, lambda: "Não foi possível carregar" in view.labelStatusHistorico.text())
    assert view.botaoAtualizarHistorico.isEnabled()
    monkeypatch.setattr(repo, "consultar_historico", consultar)
    view.botaoAtualizarHistorico.click()
    pronta(app, controller)
    assert view.labelStatusHistorico.text() == "0 eventos"


def test_falha_incremental_nao_avanca_cursor_e_proxima_notificacao_recupera(app, ambiente, monkeypatch):
    repo, banco, montar = ambiente
    controller, view = montar()
    pronta(app, controller)
    consultar = repo.consultar_novos

    def falhar(_cursor):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repo, "consultar_novos", falhar)
    banco.gravar_evento(evento("Primeiro"))
    assert esperar(app, lambda: "Não foi possível atualizar" in view.labelStatusHistorico.text())
    assert controller._ultimo_id == 0
    monkeypatch.setattr(repo, "consultar_novos", consultar)
    banco.gravar_evento(evento("Segundo"))
    assert esperar(app, lambda: view.tabelaEventos.rowCount() == 2 and not controller._delta_em_andamento)
    assert descricoes(view) == ["Segundo", "Primeiro"]


@pytest.mark.parametrize("encerramento", ["close", "view", "controller"])
def test_encerramento_descarta_callbacks_sem_reter_tela(app, ambiente, monkeypatch, encerramento):
    repo, banco, montar = ambiente
    entrou, liberar = threading.Event(), threading.Event()
    consultar = repo.consultar_historico

    def consulta_lenta(filtro):
        entrou.set()
        assert liberar.wait(3)
        return consultar(filtro)

    monkeypatch.setattr(repo, "consultar_historico", consulta_lenta)
    controller, view = montar()
    try:
        assert entrou.wait(3)
        ids = set(controller._tarefas)
        assert ids and ids <= banco._callbacks.keys()
        if encerramento == "close":
            view.close()
        else:
            (view if encerramento == "view" else controller).deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not ids & banco._callbacks.keys()
    finally:
        liberar.set()
    finalizou = threading.Event()
    banco.executar(finalizou.set)
    assert esperar(app, finalizou.is_set)


def test_callbacks_guardam_apenas_referencia_fraca_ao_controller(app):
    banco = BancoControlado()
    view = HistoricoView()
    controller = HistoricoController(view, banco)
    referencia = weakref.ref(controller)
    controller.encerrar()
    controller.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    del controller
    gc.collect()
    assert referencia() is None
    banco.tarefas[0][0](ConsultaHistorico([], 0, []))
    view.deleteLater()


def test_milhares_de_registros_mantem_event_loop_e_cliques_responsivos(app, ambiente):
    repo, banco, montar = ambiente
    quantidade = 8000
    base = datetime.now().replace(microsecond=0) - timedelta(hours=3)
    inseridos = []

    def preparar():
        with Database.transacao() as conexao:
            for i in range(quantidade):
                repo.adicionar(Evento(base + timedelta(seconds=i), TipoEvento.STATUS, f"Evento {i}"), conexao)

    banco.executar(preparar, ao_concluir=lambda _: inseridos.append(True))
    assert esperar(app, lambda: inseridos)
    batimentos, cliques = [], []
    timer = QTimer()
    timer.setInterval(2)
    timer.timeout.connect(lambda: batimentos.append(time.monotonic()))
    botao = QPushButton("Navegar")
    botao.clicked.connect(lambda: cliques.append(time.monotonic()))
    timer.timeout.connect(botao.click)
    timer.start()
    try:
        controller, view = montar()
        view.show()
        pronta(app, controller)
    finally:
        timer.stop()
    intervalos = [fim - inicio for inicio, fim in zip(batimentos, batimentos[1:])]

    assert view.tabelaEventos.rowCount() == quantidade
    assert descricoes(view)[0] == f"Evento {quantidade - 1}"
    assert len(cliques) > 10 and intervalos
    assert max(intervalos) < 0.1, f"Event loop parado por {max(intervalos):.3f}s"


def test_reiniciar_worker_e_tela_recupera_eventos_persistidos(app, ambiente):
    repo, banco, montar = ambiente
    controller, view = montar()
    pronta(app, controller)
    banco.gravar_evento(evento("Persistiu ao reiniciar"), "DISJ-01")
    assert esperar(app, lambda: view.tabelaEventos.rowCount() == 1 and not controller._delta_em_andamento)
    controller.encerrar()
    banco.parar()
    assert banco.wait(3000)
    Database.init_db()

    novo_banco = DatabaseWorker()
    nova_view = HistoricoView()
    novo_controller = HistoricoController(nova_view, novo_banco)
    novo_banco.start()
    try:
        pronta(app, novo_controller)
        assert descricoes(nova_view) == ["Persistiu ao reiniciar"]
        assert nova_view.tabelaEventos.item(0, 4).text() == "DISJ-01"
    finally:
        novo_controller.encerrar()
        novo_banco.parar()
        assert novo_banco.wait(3000)
        novo_controller.deleteLater()
        nova_view.deleteLater()


def test_main_registra_view_com_worker_compartilhado_e_preserva_navegacao(app, ambiente):
    from controllers.aplicacao_controller import AplicacaoController
    from controllers.main_window_controller import MainWindowController
    from main import _montar_telas
    from models.config_manager import Configuracao
    from models.repositorio_dispositivos import RepositorioDispositivos
    from models.repositorio_regras import RepositorioRegras
    from models.servico_corte import ServicoCorteEmergencial

    repo, _, _ = ambiente
    servico = ServicoCorteEmergencial(RepositorioDispositivos(), repo)
    config = Configuracao()
    aplicacao = AplicacaoController(config, servico)
    janela = MainWindowController()
    try:
        _montar_telas(janela, servico, RepositorioRegras(), config, aplicacao)
        view = janela.view.stackedPaginas.widget(2)
        assert isinstance(view, HistoricoView)
        controller = view.findChild(HistoricoController)
        assert controller._banco is aplicacao.banco
        pronta(app, controller)
        for pagina in ("historico", "serial", "dashboard", "historico"):
            janela.ir_para(pagina)
            assert janela.view.stackedPaginas.currentIndex() == janela.PAGINAS[pagina]
    finally:
        aplicacao.encerrar()
        janela.view.deleteLater()
        janela.deleteLater()
        aplicacao.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
