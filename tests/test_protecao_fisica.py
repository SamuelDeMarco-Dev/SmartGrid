"""Regressões de prioridade física, confirmação DAT e transações manuais."""
import sqlite3
from threading import Event

import pytest
from PyQt6.QtTest import QSignalSpy, QTest

from controllers.dashboard_controller import DashboardController
from database.connection import Database
from models import protocolo
from models.estado_disjuntor import EstadoDisjuntor
from models.porta_serial import ParametrosConexao
from models.repositorio_eventos import RepositorioEventos
from models.repositorio_regras import RepositorioRegras
from models.serial_worker import SerialWorker
from models.servico_corte import FalhaCorteEmergencial


@pytest.fixture
def conectar(aplicacao_teste, aguardar_qt):
    def conectar_placa(placa):
        aplicacao_teste.conectar_serial(ParametrosConexao(placa.caminho, 115200, 300))
        worker = aplicacao_teste._serial
        aguardar_qt(lambda: aplicacao_teste._estado_reportado)
        return worker
    return conectar_placa


def comandos(placa, comando):
    return [p for _, p in placa.escritas if p == comando]


def test_botao_ja_pressionado_ao_conectar_corta_sem_religar(
    aplicacao_teste, serial_simulada, conectar, aguardar_qt,
):
    placa = serial_simulada(rele_fechado=True, botao_pressionado=True)
    aplicacao_teste._servico_corte.simular_falha = True
    confirmacoes = QSignalSpy(aplicacao_teste.disparo_espontaneo)
    conectar(placa)
    aguardar_qt(lambda: len(confirmacoes) == 1 and not placa.rele_fechado)
    aguardar_qt(lambda: any("Botão físico" in e.descricao for e in RepositorioEventos().listar_todos()))
    QTest.qWait(150)
    assert comandos(placa, b"!0\n") == [b"!0\n"]
    assert not comandos(placa, b"!1\n")
    assert len([e for e in RepositorioEventos().listar_todos() if "Botão físico" in e.descricao]) == 1


def test_botao_retido_bloqueia_religamento_e_liberar_nao_religa(
    aplicacao_teste, serial_simulada, conectar, aguardar_qt,
):
    placa = serial_simulada(botao_pressionado=True)
    worker = conectar(placa)
    erros, concluidas = [], []
    aplicacao_teste.executar_comando_manual(EstadoDisjuntor.FECHADO, "teste",
                                          ao_concluir=concluidas.append, ao_falhar=erros.append)
    assert erros and not concluidas and not comandos(placa, b"!1\n")
    placa.liberar_botao()
    aguardar_qt(lambda: not worker.botao_pressionado)
    QTest.qWait(150)
    assert not placa.rele_fechado and not comandos(placa, b"!1\n")
    aplicacao_teste.executar_comando_manual(EstadoDisjuntor.FECHADO, "manual após liberação",
                                          ao_concluir=concluidas.append, ao_falhar=erros.append)
    aguardar_qt(lambda: len(concluidas) == 1)
    assert placa.rele_fechado and len(comandos(placa, b"!1\n")) == 1


def test_pressao_durante_transacao_pendente_corta_antes_do_banco_e_cancela_religamento(
    aplicacao_teste, serial_simulada, conectar, aguardar_qt,
):
    placa = serial_simulada()
    worker = conectar(placa)
    entrou, liberar = Event(), Event()
    def bloquear():
        entrou.set()
        if not liberar.wait(3):
            raise TimeoutError("Teste não liberou a fila SQLite")
    aplicacao_teste.banco.executar(bloquear)
    aguardar_qt(entrou.is_set)
    erros, concluidas = [], []
    try:
        aplicacao_teste.executar_comando_manual(EstadoDisjuntor.FECHADO, "pendente",
                                              ao_concluir=concluidas.append, ao_falhar=erros.append)
        placa.disparar()
        aguardar_qt(lambda: len(comandos(placa, b"!0\n")) == 1 and erros)
        # Nem o COMMIT nem o processamento da GUI são necessários para !0.
        assert not liberar.is_set() and not comandos(placa, b"!1\n")
        placa.liberar_botao()
        aguardar_qt(lambda: not worker.botao_pressionado)
    finally:
        liberar.set()
    drenou = []
    aplicacao_teste.banco.executar(lambda: True, ao_concluir=drenou.append)
    aguardar_qt(lambda: drenou)
    assert not comandos(placa, b"!1\n") and not concluidas
    assert not [e for e in RepositorioEventos().listar_todos() if "RELAY_ON" in e.descricao]
    assert aplicacao_teste.estado_disjuntor is EstadoDisjuntor.ABERTO


def test_botao_durante_religamento_ja_enviado_tem_prioridade(
    aplicacao_teste, serial_simulada, conectar, aguardar_qt,
):
    placa = serial_simulada()
    conectar(placa)
    placa.ignorar_rele = True  # !1 fica sem confirmação DAT
    concluidas, erros = [], []
    aplicacao_teste.executar_comando_manual(EstadoDisjuntor.FECHADO, "manual",
                                          ao_concluir=concluidas.append, ao_falhar=erros.append)
    aguardar_qt(lambda: comandos(placa, b"!1\n"))
    placa.ignorar_rele = False
    placa.disparar()
    aguardar_qt(lambda: comandos(placa, b"!0\n") and erros)
    assert not concluidas and not placa.rele_fechado
    assert [p for _, p in placa.escritas if p in (b"!0\n", b"!1\n")] == [b"!1\n", b"!0\n"]


def test_falha_real_do_banco_e_modo_simulado_nao_impedem_corte_fisico(
    aplicacao_teste, serial_simulada, conectar, aguardar_qt, monkeypatch,
):
    placa = serial_simulada(rele_fechado=True)
    conectar(placa)
    servico = aplicacao_teste._servico_corte
    servico.simular_falha = True
    def falhar(*args):
        raise sqlite3.OperationalError("disco indisponível")
    monkeypatch.setattr(servico, "registrar_botao_protecao", falhar)
    monkeypatch.setattr(servico, "sincronizar_estado", falhar)
    falhas = QSignalSpy(aplicacao_teste.falha_persistencia)
    confirmacoes = QSignalSpy(aplicacao_teste.disparo_espontaneo)
    placa.disparar()
    aguardar_qt(lambda: len(falhas) > 0 and len(confirmacoes) == 1)
    assert not placa.rele_fechado
    assert aplicacao_teste.estado_disjuntor is EstadoDisjuntor.ABERTO
    assert "disco indisponível" in falhas[0][0]


def test_sem_dat_de_rele_aberto_nao_exibe_confirmacao_de_corte(
    aplicacao_teste, serial_simulada, conectar, aguardar_qt,
):
    placa = serial_simulada(rele_fechado=True)
    worker = conectar(placa)
    placa.ignorar_rele = True
    confirmacoes = QSignalSpy(aplicacao_teste.disparo_espontaneo)
    perdas = QSignalSpy(worker.link_perdido)
    placa.disparar()
    aguardar_qt(lambda: comandos(placa, b"!0\n"))
    placa.injetar(b">>> ACK: RELE DESLIGADO <<<\n")
    aguardar_qt(lambda: len(perdas) == 1)
    assert len(confirmacoes) == 0
    assert placa.rele_fechado
    assert aplicacao_teste.estado_disjuntor is EstadoDisjuntor.FECHADO


def test_falha_simulada_manual_reverte_banco_sem_enviar_corte(
    aplicacao_teste, serial_simulada, conectar, aguardar_qt,
):
    placa = serial_simulada(rele_fechado=True)
    conectar(placa)
    aplicacao_teste._servico_corte.simular_falha = True
    erros = []
    aplicacao_teste.executar_comando_manual(EstadoDisjuntor.ABERTO, "teste",
                                          ao_concluir=lambda _e: pytest.fail("Confirmou falha"),
                                          ao_falhar=erros.append)
    aguardar_qt(lambda: erros)
    assert isinstance(erros[0], FalhaCorteEmergencial)
    assert not comandos(placa, b"!0\n") and placa.rele_fechado
    with Database.transacao() as conn:
        assert dict(conn.execute("SELECT tag, status FROM dispositivos")) == {
            "DISJ-01": "FECHADO", "RELE-01": "FECHADO"}


def test_interface_aguarda_dat_apos_commit_e_marca_desconexao_como_desconhecida(
    aplicacao_teste, serial_simulada, conectar, aguardar_qt, monkeypatch,
):
    dash = DashboardController(aplicacao_teste._servico_corte, RepositorioRegras(), aplicacao_teste)
    dash.setParent(dash.view)
    avisos = []
    monkeypatch.setattr(dash.view, "confirmar", lambda *_: True)
    monkeypatch.setattr(dash.view, "avisar", lambda *_: None)
    monkeypatch.setattr(dash.view, "avisar_erro", lambda *args: avisos.append(args))
    try:
        placa = serial_simulada(rele_fechado=True)
        conectar(placa)
        placa.ignorar_rele = True
        dash.alternar_carga()
        aguardar_qt(lambda: comandos(placa, b"!0\n"))
        assert dash.estado_disjuntor is EstadoDisjuntor.FECHADO
        assert "FECHADO" in dash.view.labelBadgeDisjuntor.text()
        assert dash._corte_em_andamento
        aguardar_qt(lambda: avisos)
        assert "SEM CONFIRMAÇÃO" in dash.view.labelBadgeDisjuntor.text()
        assert dash.view.labelDetalheDisjuntor.text() == "Estado atual da carga sem confirmação"
    finally:
        dash.view.deleteLater()


def test_debounce_prioriza_pressao_e_so_rearma_apos_50_ms_estaveis(app):
    worker = SerialWorker("teste", 115200, 100, 300)
    escritas = []
    class Porta:
        def write(self, pacote):
            escritas.append(pacote)
    worker._serial = Porta()
    botoes = QSignalSpy(worker.botao_alterado)
    worker._tratar_botao(False, 0.0)
    geracao = worker.geracao_protecao
    assert worker.enviar_rele(True, geracao)
    worker._tratar_botao(True, 0.01)
    worker._tratar_botao(False, 0.02)
    worker._tratar_botao(True, 0.03)
    worker._tratar_botao(False, 0.04)
    worker._tratar_botao(False, 0.08)
    assert worker.botao_pressionado
    worker._tratar_botao(False, 0.10)
    assert not worker.botao_pressionado
    assert not worker.enviar_rele(True, geracao)  # pedido anterior à pressão
    assert list(botoes) == [[False], [True], [False]]
    assert escritas == [b"!0\n"]


def test_corte_substitui_comando_de_religamento_na_fila(app):
    worker = SerialWorker("teste", 115200, 100, 300)
    worker._tratar_botao(False, 0)
    assert worker.enviar_rele(True)
    assert worker.enviar_rele(False)
    assert not worker.enviar_rele(True)
    assert worker._rele_pendente is False

@pytest.mark.parametrize("ligada", [True, False])
def test_protecao_durante_confirmacao_modal_invalida_pedido_anterior(
    aplicacao_teste, serial_simulada, conectar, aguardar_qt, monkeypatch, ligada,
):
    dash = DashboardController(aplicacao_teste._servico_corte, RepositorioRegras(), aplicacao_teste)
    dash.setParent(dash.view)
    avisos = []
    monkeypatch.setattr(dash.view, "avisar", lambda *args: avisos.append(args))
    monkeypatch.setattr(dash.view, "avisar_erro", lambda *_: None)
    placa = serial_simulada(rele_fechado=ligada)
    worker = conectar(placa)
    def confirmar(*args):
        # QMessageBox real mantém um event loop; a proteção pode atuar nele.
        placa.disparar()
        aguardar_qt(lambda: comandos(placa, b"!0\n") and
                    aplicacao_teste.estado_disjuntor is EstadoDisjuntor.ABERTO)
        placa.liberar_botao()
        aguardar_qt(lambda: not worker.botao_pressionado)
        return True
    monkeypatch.setattr(dash.view, "confirmar", confirmar)
    try:
        dash.alternar_carga()
        assert not comandos(placa, b"!1\n")
        assert not dash._corte_em_andamento
        assert any(titulo == "Operação cancelada" for titulo, _ in avisos)
    finally:
        dash.view.deleteLater()


def test_encerrar_durante_atuacao_nao_abre_callback_modal_nem_confirma_carga(
    aplicacao_teste, serial_simulada, conectar, aguardar_qt,
):
    placa = serial_simulada()
    worker = conectar(placa)
    placa.ignorar_rele = True
    concluidas, erros = [], []
    aplicacao_teste.executar_comando_manual(EstadoDisjuntor.FECHADO, "manual",
                                          ao_concluir=concluidas.append, ao_falhar=erros.append)
    aguardar_qt(lambda: comandos(placa, b"!1\n"))
    aplicacao_teste.encerrar()
    assert worker.isFinished() and not placa.conexoes[0].is_open
    assert not concluidas and not erros
    assert aplicacao_teste.estado_disjuntor is EstadoDisjuntor.ABERTO
