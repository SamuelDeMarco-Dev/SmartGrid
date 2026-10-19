"""Testes do ConfigManager (data/config.json)."""

import json
import logging

import pytest

import models.config_manager as modulo_config
from models.config_manager import ConfigManager, Configuracao


@pytest.fixture
def caminho_config(tmp_path, monkeypatch):
    caminho = tmp_path / "data" / "config.json"
    monkeypatch.setattr(modulo_config, "CAMINHO_CONFIG", caminho)
    return caminho


@pytest.fixture
def log(caplog):
    caplog.set_level(logging.INFO, logger="smartgrid")
    return caplog


def _gravar(caminho, conteudo):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(conteudo, encoding="utf-8")


def _niveis(log):
    return [registro.levelname for registro in log.records]


def test_sem_arquivo_cria_com_padroes_e_loga_info(caminho_config, log):
    config = ConfigManager.carregar()

    assert config == Configuracao()
    assert json.loads(caminho_config.read_text(encoding="utf-8")) == {
        "porta_com": "COM1",
        "baud_rate": 115200,
        "timeout_ms": 1000,
        "simular_falha_corte": False,
    }
    assert _niveis(log) == ["INFO"]


def test_json_corrompido_usa_padroes_loga_warning_e_nao_sobrescreve(caminho_config, log):
    _gravar(caminho_config, "{abc")

    assert ConfigManager.carregar() == Configuracao()
    assert _niveis(log) == ["WARNING"]
    assert caminho_config.read_text(encoding="utf-8") == "{abc"


@pytest.mark.parametrize("conteudo", ["[1, 2]", '"texto"', "42"])
def test_raiz_que_nao_e_objeto_usa_padroes(caminho_config, log, conteudo):
    _gravar(caminho_config, conteudo)

    assert ConfigManager.carregar() == Configuracao()
    assert _niveis(log) == ["WARNING"]


def test_campo_invalido_volta_ao_padrao_e_mantem_os_demais(caminho_config, log):
    _gravar(caminho_config, json.dumps({
        "porta_com": "COM7",
        "baud_rate": 12345,
        "timeout_ms": 2500,
        "simular_falha_corte": True,
    }))

    config = ConfigManager.carregar()

    assert config == Configuracao(
        porta_com="COM7", baud_rate=115200, timeout_ms=2500, simular_falha_corte=True
    )
    assert _niveis(log) == ["WARNING"]
    assert "baud_rate" in log.records[0].getMessage()


def test_campo_ausente_usa_padrao_e_campo_desconhecido_e_ignorado(caminho_config, log):
    _gravar(caminho_config, json.dumps({
        "porta_com": "COM3",
        "baud_rate": 9600,
        "timeout_ms": 500,
        "campo_antigo": "qualquer",
    }))

    config = ConfigManager.carregar()

    assert config.simular_falha_corte is False
    assert (config.porta_com, config.baud_rate, config.timeout_ms) == ("COM3", 9600, 500)
    assert _niveis(log) == ["WARNING"]


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("porta_com", ""),
        ("porta_com", "   "),
        ("porta_com", 3),
        ("baud_rate", True),
        ("baud_rate", 12345),
        ("baud_rate", "9600"),
        ("baud_rate", 9600.0),
        ("timeout_ms", 0),
        ("timeout_ms", -100),
        ("timeout_ms", 99),
        ("timeout_ms", 10_001),
        ("timeout_ms", False),
        ("simular_falha_corte", 1),
        ("simular_falha_corte", "false"),
    ],
)
def test_valor_invalido_e_recusado(caminho_config, log, campo, valor):
    dados = {
        "porta_com": "COM3",
        "baud_rate": 9600,
        "timeout_ms": 500,
        "simular_falha_corte": True,
    }
    dados[campo] = valor
    _gravar(caminho_config, json.dumps(dados))

    config = ConfigManager.carregar()

    assert getattr(config, campo) == getattr(Configuracao(), campo)
    assert _niveis(log) == ["WARNING"]
    with pytest.raises(ValueError):
        Configuracao(**dados)


def test_salvar_e_carregar_devolve_o_mesmo(caminho_config):
    config = Configuracao(
        porta_com="/dev/ttyUSB0", baud_rate=57600, timeout_ms=10_000, simular_falha_corte=True
    )

    ConfigManager.salvar(config)

    assert ConfigManager.carregar() == config
    assert not caminho_config.with_name("config.json.tmp").exists()


def test_salvar_e_atomico_quando_a_troca_falha(caminho_config, monkeypatch):
    ConfigManager.salvar(Configuracao(porta_com="COM3"))
    original = caminho_config.read_text(encoding="utf-8")

    def falhar(*_):
        raise OSError("disco cheio")

    monkeypatch.setattr(modulo_config.os, "replace", falhar)
    with pytest.raises(OSError):
        ConfigManager.salvar(Configuracao(porta_com="COM9"))

    assert caminho_config.read_text(encoding="utf-8") == original
    assert not caminho_config.with_name("config.json.tmp").exists()


def test_erro_de_disco_ao_criar_loga_error_e_segue_com_padroes(caminho_config, log, monkeypatch):
    def falhar(_config):
        raise OSError("sem permissão")

    monkeypatch.setattr(ConfigManager, "salvar", staticmethod(falhar))

    assert ConfigManager.carregar() == Configuracao()
    assert _niveis(log) == ["ERROR"]
