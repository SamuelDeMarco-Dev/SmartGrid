"""Tela serial em MVC: config.json, taxa de amostragem e fases do handshake (#61)."""

import json
import logging
import time

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMessageBox

import controllers.aplicacao_controller as modulo_aplicacao
import database.connection as modulo_conexao
import models.config_manager as modulo_config
from controllers.aplicacao_controller import AplicacaoController
from controllers.serial_config_controller import SerialConfigController
from models.config_manager import ConfigManager, Configuracao
from models.barramento import barramento
from models.database_worker import DatabaseWorker
from models.estado_link import EstadoLink
from models.porta_serial import ParametrosConexao, PortaSerial
from models.protocolo import VELOCIDADES
from views import serial_config_view as modulo_view

PORTAS_DA_MAQUINA = ["COM1", "COM3", "COM7"]


class ServicoSerialFalso(QObject):
    """Faz o papel do AplicacaoController sem criar a thread serial."""

    velocidade_negociada = pyqtSignal(int)

    def __init__(self, banco):
        super().__init__()
        self.banco = banco
        self.conexoes = []
        self.configuracoes = []
        self.desconexoes = 0

    def conectar_serial(self, parametros):
        taxa = self.configuracoes[-1].taxa_amostragem_ms if self.configuracoes else None
        self.conexoes.append((parametros, taxa))

    def desconectar_serial(self):
        self.desconexoes += 1

    def atualizar_configuracao(self, config):
        self.configuracoes.append(config)


@pytest.fixture
def caminho_config(tmp_path, monkeypatch):
    caminho = tmp_path / "data" / "config.json"
    monkeypatch.setattr(modulo_config, "CAMINHO_CONFIG", caminho)
    return caminho


@pytest.fixture
def avisos(monkeypatch):
    """Intercepta as caixas de diálogo para o teste não travar esperando clique."""
    mensagens = []
    monkeypatch.setattr(modulo_view.QMessageBox, "warning", lambda _pai, _titulo, texto: mensagens.append(texto))
    monkeypatch.setattr(modulo_view.QMessageBox, "critical", lambda _pai, _titulo, texto: mensagens.append(texto))
    return mensagens


@pytest.fixture
def log(caplog):
    caplog.set_level(logging.INFO, logger="smartgrid")
    return caplog


@pytest.fixture
def banco(app):
    worker = DatabaseWorker()
    worker.start()
    yield worker
    worker.parar()
    assert worker.wait(2000)
    app.processEvents()


@pytest.fixture
def aguardar_banco(banco):
    def aguardar():
        concluidos = []
        banco.executar(lambda: None, ao_concluir=concluidos.append)
        prazo = time.monotonic() + 3
        while not concluidos and time.monotonic() < prazo:
            QTest.qWait(5)
        assert concluidos, "O callback do DatabaseWorker não chegou à GUI"
    return aguardar


@pytest.fixture
def servico(banco):
    return ServicoSerialFalso(banco)


@pytest.fixture
def tela(caminho_config, avisos, monkeypatch, servico, aguardar_banco):
    monkeypatch.setattr(PortaSerial, "listar_portas", staticmethod(lambda: list(PORTAS_DA_MAQUINA)))
    criadas = []

    def criar(config=None):
        controller = SerialConfigController(config or Configuracao(), servico)
        controller.setParent(controller.view)
        criadas.append(controller)
        aguardar_banco()
        assert controller.view.botaoConectar.isEnabled()
        return controller

    yield criar
    for controller in criadas:
        barramento.estado_link_alterado.disconnect(controller._ao_estado_link)
        controller.view.deleteLater()


def _link(estado, detalhe=""):
    barramento.estado_link_alterado.emit(estado.name, detalhe)


def _editaveis(view):
    return [
        campo.isEnabled()
        for campo in (
            view.comboPortaSerial, view.comboBaudRate, view.editTimeout,
            view.editTaxaAmostragem, view.botaoAtualizarPortas, view.botaoConectar,
        )
    ]


def _mensagens(log, nivel):
    return [r.getMessage() for r in log.records if r.levelname == nivel]


def test_abre_com_a_configuracao_salva(tela):
    janela = tela(Configuracao(porta_com="COM3", baud_rate=9600, timeout_ms=2500))

    assert janela.view.comboPortaSerial.currentText() == "COM3"
    assert janela.view.comboBaudRate.currentData() == 9600
    assert janela.view.editTimeout.text() == "2500"


def test_porta_salva_inexistente_seleciona_a_primeira_e_loga_warning(tela, log):
    janela = tela(Configuracao(porta_com="COM99"))

    assert janela.view.comboPortaSerial.currentText() == "COM1"
    assert _mensagens(log, "WARNING") == ["Porta salva COM99 não está disponível. Selecionada COM1."]


@pytest.mark.parametrize("timeout", ["", "   ", "abc", "1.5", "-5", "50", "10001"])
def test_timeout_invalido_avisa_loga_e_nao_conecta(tela, avisos, log, caminho_config, timeout):
    janela = tela()
    janela.view.editTimeout.setText(timeout)

    janela.conectar()

    assert not janela.porta_serial.conectado
    assert len(avisos) == 1
    assert len(_mensagens(log, "WARNING")) == 1
    assert f"timeout={timeout!r}" in _mensagens(log, "WARNING")[0]
    assert not caminho_config.exists()


def test_conectar_sem_porta_avisa_e_loga(tela, avisos, log):
    janela = tela()
    janela.view.comboPortaSerial.setCurrentIndex(0)  # "— Selecione a porta —"

    janela.conectar()

    assert not janela.porta_serial.conectado
    assert avisos == ["Selecione uma porta COM antes de conectar."]
    assert len(_mensagens(log, "WARNING")) == 1


def test_conectar_salva_config_preserva_falha_simulada_e_loga_info(
    tela, log, caminho_config, aguardar_banco,
):
    janela = tela(Configuracao(simular_falha_corte=True))
    janela.view.comboPortaSerial.setCurrentText("COM7")
    janela.view.comboBaudRate.setCurrentIndex(janela.view.comboBaudRate.findData(38400))
    janela.view.editTimeout.setText("3000")
    log.clear()

    janela.conectar()
    aguardar_banco()

    assert janela.porta_serial.conectado
    assert json.loads(caminho_config.read_text(encoding="utf-8")) == {
        "porta_com": "COM7",
        "baud_rate": 38400,
        "timeout_ms": 3000,
        "limites_iniciais": {"corrente": 15.0, "tensao": 230.0, "potencia": 3500.0},
        "taxa_amostragem_ms": 1000,
        "simular_falha_corte": True,
    }
    assert _mensagens(log, "INFO") == [
        "Conexão serial solicitada: COM7 @ 38400 bps (timeout 3000 ms, amostragem 1000 ms). "
        "Iniciando handshake a 9600 bps."
    ]


def test_configuracao_salva_volta_na_proxima_abertura(tela, aguardar_banco):
    primeira = tela()
    primeira.view.comboPortaSerial.setCurrentText("COM3")
    primeira.view.comboBaudRate.setCurrentIndex(primeira.view.comboBaudRate.findData(9600))
    primeira.view.editTimeout.setText("4000")
    primeira.conectar()
    aguardar_banco()

    segunda = tela(ConfigManager.carregar())

    assert segunda.view.comboPortaSerial.currentText() == "COM3"
    assert segunda.view.comboBaudRate.currentData() == 9600
    assert segunda.view.editTimeout.text() == "4000"


def test_configuracao_serial_preserva_limites_atualizados_por_outra_tela(tela, aguardar_banco):
    janela = tela()
    limites = {"corrente": 8.0, "tensao": 210.0, "potencia": 4200.0}
    ConfigManager.salvar(Configuracao(limites_iniciais=limites, simular_falha_corte=True))
    janela.view.comboPortaSerial.setCurrentText("COM7")
    janela.view.editTaxaAmostragem.setText("250")

    janela.conectar()
    aguardar_banco()

    gravado = ConfigManager.carregar()
    assert gravado.porta_com == "COM7"
    assert gravado.limites_iniciais == limites
    assert gravado.simular_falha_corte is True
    assert gravado.taxa_amostragem_ms == 250


def test_desconectar_loga_info(tela, log, aguardar_banco):
    janela = tela()
    janela.conectar()
    aguardar_banco()
    log.clear()

    janela.desconectar()

    assert not janela.porta_serial.conectado
    assert _mensagens(log, "INFO") == ["Comunicação serial encerrada: COM1 @ 115200 bps."]


def test_falha_ao_gravar_loga_error_e_mantem_conexao(tela, avisos, log, monkeypatch, aguardar_banco):
    def falhar(_config):
        raise OSError("sem permissão")

    monkeypatch.setattr(ConfigManager, "salvar", staticmethod(falhar))
    janela = tela()

    janela.conectar()
    aguardar_banco()

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


# ----------------------------------------------------------------------
# Issue #61 — velocidades, taxa de amostragem e fases do handshake
# ----------------------------------------------------------------------
def test_combo_oferece_so_as_quatro_velocidades_e_avisa_que_inicia_em_9600(tela):
    view = tela().view

    velocidades = [view.comboBaudRate.itemData(i) for i in range(view.comboBaudRate.count())]

    assert velocidades == list(VELOCIDADES) == [9600, 19200, 38400, 115200]
    assert "sempre inicia em 9600 bps" in view.labelAvisoBaudRate.text()


@pytest.mark.parametrize("taxa", ["", "abc", "1.5", "-1", "99", "5001"])
def test_taxa_invalida_avisa_loga_e_nao_conecta(tela, avisos, log, servico, caminho_config, taxa):
    janela = tela()
    janela.view.editTaxaAmostragem.setText(taxa)

    janela.conectar()

    assert not janela.porta_serial.conectado
    assert servico.conexoes == []
    assert len(avisos) == 1
    assert f"taxa={taxa!r}" in _mensagens(log, "WARNING")[0]
    assert not caminho_config.exists()
    assert all(_editaveis(janela.view))


def test_taxa_nova_vale_ja_nesta_conexao_e_persiste_entre_execucoes(tela, servico, aguardar_banco):
    primeira = tela()
    primeira.view.editTaxaAmostragem.setText("250")

    primeira.conectar()
    aguardar_banco()

    assert servico.conexoes[0][1] == 250
    assert ConfigManager.carregar().taxa_amostragem_ms == 250
    segunda = tela(ConfigManager.carregar())
    assert segunda.view.editTaxaAmostragem.text() == "250"


def test_fases_do_handshake_mudam_indicador_e_travam_o_formulario(tela):
    janela = tela()
    view = janela.view
    janela.conectar()

    for estado, detalhe in (
        (EstadoLink.ABRINDO_PORTA, "COM1"),
        (EstadoLink.HANDSHAKE, "Propondo 115200 bps, intervalo 1000 ms"),
        (EstadoLink.NEGOCIANDO, "Confirmando 115200 bps"),
        (EstadoLink.TELEMETRIA, "COM1 @ 115200 bps"),
    ):
        _link(estado, detalhe)

        assert view.labelStatusConexao.text() == estado.rotulo
        assert estado.cor in view.labelStatusConexao.styleSheet()
        assert view.labelDetalheConexao.text().startswith(detalhe)
        assert not any(_editaveis(view))
        assert view.botaoDesconectar.isEnabled()

    cores = {EstadoLink.ABRINDO_PORTA.cor, EstadoLink.NEGOCIANDO.cor, EstadoLink.TELEMETRIA.cor}
    assert len(cores) == 3


def test_velocidade_negociada_aparece_e_some_ao_desconectar(tela, servico):
    janela = tela()
    janela.conectar()
    _link(EstadoLink.TELEMETRIA, "COM1 @ 38400 bps")

    servico.velocidade_negociada.emit(38400)
    assert janela.view.labelVelocidadeNegociada.text() == "Velocidade negociada: 38400 bps"

    _link(EstadoLink.LINK_PERDIDO, "cabo removido")
    assert janela.view.labelVelocidadeNegociada.text() == "Velocidade negociada: —"


def test_link_perdido_mostra_motivo_e_reabilita_campos_para_reconectar(tela, servico):
    janela = tela()
    view = janela.view
    janela.conectar()
    _link(EstadoLink.HANDSHAKE, "Propondo 115200 bps")
    _link(EstadoLink.TELEMETRIA, "COM1 @ 115200 bps")

    _link(EstadoLink.LINK_PERDIDO, "Porta COM1 desapareceu")

    assert view.labelStatusConexao.text() == "Link perdido"
    assert EstadoLink.LINK_PERDIDO.cor in view.labelStatusConexao.styleSheet()
    assert "Porta COM1 desapareceu" in view.labelDetalheConexao.text()
    assert not janela.porta_serial.conectado
    assert all(_editaveis(view))
    assert not view.botaoDesconectar.isEnabled()

    view.botaoConectar.click()

    assert len(servico.conexoes) == 2
    assert view.labelStatusConexao.text() == EstadoLink.ABRINDO_PORTA.rotulo


def test_desconectar_espera_a_thread_liberar_a_porta(tela, servico):
    janela = tela()
    view = janela.view
    janela.conectar()
    _link(EstadoLink.TELEMETRIA, "COM1 @ 115200 bps")

    view.botaoDesconectar.click()

    assert servico.desconexoes == 1
    assert view.labelStatusConexao.text() == "Encerrando conexão"
    assert not any(_editaveis(view))

    _link(EstadoLink.DESCONECTADO, "Porta COM1 liberada")

    assert view.labelStatusConexao.text() == "Desconectado"
    assert all(_editaveis(view))


def test_aplicacao_repassa_a_velocidade_negociada_pela_thread(app, monkeypatch, tmp_path):
    monkeypatch.setattr(modulo_conexao, "CAMINHO_DB", tmp_path / "smartgrid.db")
    criados = []

    class SerialWorkerFalso(QObject):
        estado_alterado = pyqtSignal(str, str)
        leitura_recebida = pyqtSignal(object)
        disjuntor_reportado = pyqtSignal(object)
        rele_confirmado = pyqtSignal(object)
        rele_falhou = pyqtSignal(object, str)
        botao_acionado = pyqtSignal()
        velocidade_negociada = pyqtSignal(int)
        finished = pyqtSignal()

        def __init__(self, *_args, parent=None):
            super().__init__(parent)
            criados.append(self)

        def start(self):
            pass

    monkeypatch.setattr(modulo_aplicacao, "SerialWorker", SerialWorkerFalso)
    aplicacao = AplicacaoController(Configuracao(), servico_corte=None)
    recebidas = []
    aplicacao.velocidade_negociada.connect(recebidas.append)
    try:
        aplicacao.conectar_serial(ParametrosConexao("COM1", 38400, 1000))
        criados[0].velocidade_negociada.emit(19200)
    finally:
        aplicacao._serial = None
        aplicacao.encerrar()
        for sinal, slot in (
            (barramento.evento_registrado, aplicacao.banco.gravar_evento),
            (barramento.estado_disjuntor_alterado, aplicacao._ao_estado_disjuntor),
        ):
            sinal.disconnect(slot)

    assert recebidas == [19200]
