"""Configuração compartilhada dos testes."""

import os
from time import monotonic

import pytest

# Testes de tela rodam sem janela visível.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def app():
    """Uma única QApplication para toda a sessão.

    Destruir a QApplication entre arquivos de teste invalida objetos Qt globais,
    como o ``barramento``, e quebra os testes seguintes.
    """
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def aguardar_qt(app):
    """Aguarda sinais e deleteLater sem bloquear o event loop da GUI."""
    from PyQt6.QtCore import QCoreApplication, QEvent
    from PyQt6.QtTest import QTest

    def aguardar(condicao, segundos=5):
        prazo = monotonic() + segundos
        while True:
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            if condicao():
                return
            assert monotonic() < prazo, "Resultado assíncrono não chegou no prazo"
            QTest.qWait(5)

    return aguardar


@pytest.fixture
def aplicacao_teste(app, tmp_path, monkeypatch, serial_simulada):
    """Aplicação real com SQLite/JSON isolados e workers de vida limitada ao teste."""
    from PyQt6 import sip
    from PyQt6.QtCore import QCoreApplication, QEvent, QThread

    import database.connection as modulo_db
    import models.config_manager as modulo_config
    from controllers.aplicacao_controller import AplicacaoController
    from database.connection import Database
    from models.config_manager import ConfigManager, Configuracao
    from models.repositorio_dispositivos import RepositorioDispositivos
    from models.repositorio_eventos import RepositorioEventos
    from models.servico_corte import ServicoCorteEmergencial

    monkeypatch.setattr(modulo_db, "CAMINHO_DB", tmp_path / "smartgrid.db")
    monkeypatch.setattr(modulo_config, "CAMINHO_CONFIG", tmp_path / "config.json")
    config = Configuracao(taxa_amostragem_ms=100)
    Database.init_db(config.limites_iniciais)
    ConfigManager.salvar(config)
    servico = ServicoCorteEmergencial(RepositorioDispositivos(), RepositorioEventos())
    aplicacao = AplicacaoController(config, servico)
    try:
        yield aplicacao
    finally:
        # O próprio teste pode já ter encerrado e destruído os workers.
        for worker in aplicacao.findChildren(QThread):
            if worker is not aplicacao.banco:
                worker.parar()
                assert worker.wait(3000), "A thread serial não encerrou"
        app.processEvents()  # entrega as últimas leituras antes de drenar o banco
        if not sip.isdeleted(aplicacao.banco):
            aplicacao.banco.parar()
            assert aplicacao.banco.wait(3000), "A thread de banco não encerrou"
        aplicacao.deleteLater()  # desconecta também os slots do barramento global
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


@pytest.fixture
def serial_simulada(monkeypatch):
    """Substitui somente a abertura da porta pelo transporte em memória."""
    from serial import SerialException
    from serial_simulada import PlacaEmMemoria

    placas = {}
    criadas = []

    def criar(caminho=None, **opcoes):
        caminho = caminho or f"COM-SIMULADA-{len(criadas) + 1}"
        placa = PlacaEmMemoria(caminho, **opcoes)
        placas[caminho] = placa
        criadas.append(placa)
        return placa

    def abrir(porta, baudrate, timeout, *, write_timeout):
        if porta not in placas:
            raise SerialException(f"Porta simulada inexistente: {porta}")
        return placas[porta].abrir(baudrate, timeout, write_timeout=write_timeout)

    monkeypatch.setattr("models.serial_worker.serial.Serial", abrir)
    try:
        yield criar
    finally:
        abertas = []
        for placa in criadas:
            placa.arrancar_cabo()
            for conexao in placa.conexoes:
                if conexao.is_open:
                    abertas.append(conexao)
                    conexao.close()
        assert not abertas, "Uma porta simulada ficou aberta após o teste"


@pytest.fixture
def serial_memoria(serial_simulada):
    """Nome usado pelo D01, com o mesmo transporte e limpeza dos testes da #64."""
    return serial_simulada
