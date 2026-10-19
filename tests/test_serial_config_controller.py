"""Testes da tela serial lendo e gravando o config.json."""

import json
import logging

import pytest
from PyQt6.QtWidgets import QMessageBox

import models.config_manager as modulo_config
from controllers.serial_config_controller import SerialConfigController
from models.config_manager import ConfigManager, Configuracao
from models.porta_serial import PortaSerial

PORTAS_DA_MAQUINA = ["COM1", "COM3", "COM7"]


@pytest.fixture
def caminho_config(tmp_path, monkeypatch):
    caminho = tmp_path / "data" / "config.json"
    monkeypatch.setattr(modulo_config, "CAMINHO_CONFIG", caminho)
    return caminho


@pytest.fixture
def avisos(monkeypatch):
    """Intercepta as caixas de diálogo para o teste não travar esperando clique."""
    mensagens = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _pai, _titulo, texto: mensagens.append(texto))
    monkeypatch.setattr(QMessageBox, "critical", lambda _pai, _titulo, texto: mensagens.append(texto))
    return mensagens


@pytest.fixture
def log(caplog):
    caplog.set_level(logging.INFO, logger="smartgrid")
    return caplog


@pytest.fixture
def tela(app, caminho_config, avisos, monkeypatch):
    monkeypatch.setattr(PortaSerial, "listar_portas", staticmethod(lambda: list(PORTAS_DA_MAQUINA)))

    def criar(config=None):
        return SerialConfigController(config or Configuracao())

    return criar


def _mensagens(log, nivel):
    return [r.getMessage() for r in log.records if r.levelname == nivel]


def test_abre_com_a_configuracao_salva(tela):
    janela = tela(Configuracao(porta_com="COM3", baud_rate=9600, timeout_ms=2500))

    assert janela.comboPortaSerial.currentText() == "COM3"
    assert janela.comboBaudRate.currentData() == 9600
    assert janela.editTimeout.text() == "2500"


def test_porta_salva_inexistente_seleciona_a_primeira_e_loga_warning(tela, log):
    janela = tela(Configuracao(porta_com="COM99"))

    assert janela.comboPortaSerial.currentText() == "COM1"
    assert _mensagens(log, "WARNING") == ["Porta salva COM99 não está disponível. Selecionada COM1."]


@pytest.mark.parametrize("timeout", ["", "   ", "abc", "1.5", "-5", "50", "10001"])
def test_timeout_invalido_avisa_loga_e_nao_conecta(tela, avisos, log, caminho_config, timeout):
    janela = tela()
    janela.editTimeout.setText(timeout)

    janela.conectar()

    assert not janela.porta_serial.conectado
    assert len(avisos) == 1
    assert len(_mensagens(log, "WARNING")) == 1
    assert f"timeout={timeout!r}" in _mensagens(log, "WARNING")[0]
    assert not caminho_config.exists()


def test_conectar_sem_porta_avisa_e_loga(tela, avisos, log):
    janela = tela()
    janela.comboPortaSerial.setCurrentIndex(0)  # "— Selecione a porta —"

    janela.conectar()

    assert not janela.porta_serial.conectado
    assert avisos == ["Selecione uma porta COM antes de conectar."]
    assert len(_mensagens(log, "WARNING")) == 1


def test_conectar_salva_config_preserva_falha_simulada_e_loga_info(tela, log, caminho_config):
    janela = tela(Configuracao(simular_falha_corte=True))
    janela.comboPortaSerial.setCurrentText("COM7")
    janela.comboBaudRate.setCurrentIndex(janela.comboBaudRate.findData(57600))
    janela.editTimeout.setText("3000")

    janela.conectar()

    assert janela.porta_serial.conectado
    assert json.loads(caminho_config.read_text(encoding="utf-8")) == {
        "porta_com": "COM7",
        "baud_rate": 57600,
        "timeout_ms": 3000,
        "simular_falha_corte": True,
    }
    assert _mensagens(log, "INFO") == [
        "Comunicação serial conectada: COM7 @ 57600 bps (timeout 3000 ms)."
    ]


def test_configuracao_salva_volta_na_proxima_abertura(tela):
    primeira = tela()
    primeira.comboPortaSerial.setCurrentText("COM3")
    primeira.comboBaudRate.setCurrentIndex(primeira.comboBaudRate.findData(9600))
    primeira.editTimeout.setText("4000")
    primeira.conectar()

    segunda = tela(ConfigManager.carregar())

    assert segunda.comboPortaSerial.currentText() == "COM3"
    assert segunda.comboBaudRate.currentData() == 9600
    assert segunda.editTimeout.text() == "4000"


def test_desconectar_loga_info(tela, log):
    janela = tela()
    janela.conectar()
    log.clear()

    janela.desconectar()

    assert not janela.porta_serial.conectado
    assert _mensagens(log, "INFO") == ["Comunicação serial encerrada: COM1 @ 115200 bps."]


def test_falha_ao_gravar_loga_error_e_mantem_conexao(tela, avisos, log, monkeypatch):
    def falhar(_config):
        raise OSError("sem permissão")

    monkeypatch.setattr(ConfigManager, "salvar", staticmethod(falhar))
    janela = tela()

    janela.conectar()

    assert janela.porta_serial.conectado
    assert _mensagens(log, "ERROR") == ["Não foi possível salvar a configuração serial: sem permissão."]
    assert len(avisos) == 1


def test_falha_inesperada_ao_abrir_loga_error(tela, avisos, log, monkeypatch, caminho_config):
    def falhar(*_args, **_kwargs):
        raise RuntimeError("driver ausente")

    janela = tela()
    monkeypatch.setattr(janela.porta_serial, "conectar", falhar)

    janela.conectar()

    assert not janela.porta_serial.conectado
    assert _mensagens(log, "ERROR") == ["Falha ao abrir a porta COM1: driver ausente."]
    assert len(avisos) == 1
    assert not caminho_config.exists()
