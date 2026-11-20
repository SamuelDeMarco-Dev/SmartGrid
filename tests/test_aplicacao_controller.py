"""AplicacaoController: serial + banco + barramento, ponta a ponta (issue #59)."""

import os
import time

import pytest

import database.connection as modulo_conexao
from controllers.aplicacao_controller import AplicacaoController
from database.connection import Database
from models.barramento import barramento
from models.config_manager import Configuracao
from models.estado_disjuntor import EstadoDisjuntor
from models.porta_serial import ParametrosConexao
from models.protocolo import Comando
from models.repositorio_dispositivos import RepositorioDispositivos
from models.repositorio_eventos import RepositorioEventos
from models.repositorio_leituras import RepositorioLeituras
from models.servico_corte import ServicoCorteEmergencial
from models.situacao_disjuntor import ConfirmacaoDisjuntor
from tools.simulador_placa import TELEMETRIA, PlacaVirtual


def esperar(app, condicao, prazo=5.0):
    limite = time.monotonic() + prazo
    while time.monotonic() < limite:
        app.processEvents()
        if condicao():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def placa():
    if os.name != "posix":
        pytest.skip("Integração com pseudoterminal real requer POSIX (os.openpty e tty).")
    placa = PlacaVirtual.criar().iniciar()
    yield placa
    placa.parar()


@pytest.fixture
def aplicacao(app, tmp_path, monkeypatch):
    monkeypatch.setattr(modulo_conexao, "CAMINHO_DB", tmp_path / "smartgrid.db")
    Database.init_db()
    servico = ServicoCorteEmergencial(RepositorioDispositivos(), RepositorioEventos())
    aplicacao = AplicacaoController(Configuracao(taxa_amostragem_ms=100), servico)
    yield aplicacao
    aplicacao.encerrar()
    barramento.evento_registrado.disconnect(aplicacao.banco.gravar_evento)
    barramento.estado_disjuntor_alterado.disconnect(aplicacao._ao_estado_disjuntor)


def _conectar(app, placa, aplicacao):
    aplicacao.conectar_serial(ParametrosConexao(placa.caminho, 115200, 500))
    assert esperar(app, lambda: placa.estado == TELEMETRIA)
    assert esperar(app, lambda: aplicacao.situacao_disjuntor.confirmacao is ConfirmacaoDisjuntor.CONFIRMADO)


def _descricoes():
    return [evento.descricao for evento in RepositorioEventos().listar_todos()]


def test_telemetria_chega_nas_telas_e_no_banco(app, placa, aplicacao):
    leituras = []
    barramento.leitura_recebida.connect(leituras.append)
    try:
        _conectar(app, placa, aplicacao)
        assert esperar(app, lambda: len(leituras) >= 3)
        assert esperar(app, lambda: RepositorioLeituras().contar() >= 3)
        assert esperar(app, lambda: any("conectada" in d for d in _descricoes()))
    finally:
        barramento.leitura_recebida.disconnect(leituras.append)


def test_corte_confirmado_no_banco_abre_o_rele_da_placa(app, placa, aplicacao):
    _conectar(app, placa, aplicacao)

    barramento.estado_disjuntor_alterado.emit(EstadoDisjuntor.ABERTO)

    assert esperar(app, lambda: placa.rele_fechado is False)


def test_disparo_na_placa_e_gravado_e_avisado(app, placa, aplicacao):
    avisos = []
    aplicacao.disparo_espontaneo.connect(avisos.append)
    _conectar(app, placa, aplicacao)

    placa.disparar()

    assert esperar(app, lambda: avisos)
    assert avisos == [""]
    assert RepositorioDispositivos().obter_por_tag("DISJ-01")["status"] == "ABERTO"
    assert any("espontaneamente" in d for d in _descricoes())


def test_cabo_arrancado_registra_alerta_e_libera_nova_conexao(app, placa, aplicacao):
    status = []
    barramento.status_conexao_alterado.connect(lambda ok, _d: status.append(ok))
    _conectar(app, placa, aplicacao)

    placa.arrancar_cabo()

    assert esperar(app, lambda: not aplicacao.serial_ativa)
    assert status[-1] is False
    assert esperar(app, lambda: any("perdido" in d for d in _descricoes()))


def test_encerrar_para_as_duas_threads(app, placa, aplicacao):
    _conectar(app, placa, aplicacao)
    serial = aplicacao._serial

    aplicacao.encerrar()

    assert serial.isFinished()
    assert aplicacao.banco.isFinished()
