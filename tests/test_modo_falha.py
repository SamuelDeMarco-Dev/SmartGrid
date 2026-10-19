"""Testes do modo de falha simulada (interruptor, atalho e selo)."""

import logging

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

from controllers.main_window_controller import MainWindowController
from models.modo_falha import ModoFalhaSimulada

CTRL_SHIFT = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier


@pytest.fixture
def log(caplog):
    caplog.set_level(logging.INFO, logger="smartgrid")
    return caplog


def _avisos(log):
    return [r.getMessage() for r in log.records if r.levelname == "WARNING"]


def test_comeca_desligado_sem_log(app, log):
    modo = ModoFalhaSimulada()

    assert modo.ativo is False
    assert _avisos(log) == []


def test_comeca_ligado_pelo_config_e_registra_no_log(app, log):
    modo = ModoFalhaSimulada(True)

    assert modo.ativo is True
    assert _avisos(log) == ["Modo de falha simulada ativado pelo config.json."]


def test_alternar_liga_desliga_loga_e_emite_sinal(app, log):
    modo = ModoFalhaSimulada()
    recebidos = []
    modo.alterado.connect(recebidos.append)

    modo.alternar()
    modo.alternar()

    assert modo.ativo is False
    assert recebidos == [True, False]
    assert _avisos(log) == [
        "Modo de falha simulada ativado.",
        "Modo de falha simulada desativado.",
    ]


def test_definir_mesmo_estado_nao_loga_nem_emite(app, log):
    modo = ModoFalhaSimulada(True)
    log.clear()
    recebidos = []
    modo.alterado.connect(recebidos.append)

    modo.definir(True)

    assert recebidos == []
    assert _avisos(log) == []


@pytest.fixture
def janela(app):
    janela = MainWindowController()
    janela.show()
    # O atalho só dispara com a janela ativa; processa os eventos de exibição.
    janela.activateWindow()
    app.processEvents()
    yield janela
    janela.close()


def test_atalho_alterna_o_modo_e_o_selo(janela):
    modo = ModoFalhaSimulada()
    janela.usar_modo_falha(modo)
    assert not janela.seloModoTeste.isVisible()

    QTest.keyClick(janela, Qt.Key.Key_F, CTRL_SHIFT)
    assert modo.ativo is True
    assert janela.seloModoTeste.isVisible()
    assert janela.seloModoTeste.text() == "MODO DE TESTE: falha simulada"

    QTest.keyClick(janela, Qt.Key.Key_F, CTRL_SHIFT)
    assert modo.ativo is False
    assert not janela.seloModoTeste.isVisible()


def test_selo_ja_aparece_se_o_modo_comecar_ligado(janela):
    janela.usar_modo_falha(ModoFalhaSimulada(True))

    assert janela.seloModoTeste.isVisible()


def test_mensagem_da_conexao_serial_nao_apaga_o_selo(janela):
    janela.usar_modo_falha(ModoFalhaSimulada(True))

    janela.atualizar_status_conexao(True, "COM3 @ 9600 bps")

    assert janela.seloModoTeste.isVisible()
    assert janela.statusbar.currentMessage() == "Conectado — COM3 @ 9600 bps"
