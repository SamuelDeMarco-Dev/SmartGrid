"""Orquestra GUI, SerialWorker e DatabaseWorker, preservando as três threads.

Transações manuais continuam antes do comando. O botão físico é atendido
na própria serial: !0 independe do SQLite e da GUI. Somente DAT confirma o
estado físico; o banco guarda solicitações e é reconciliado com a placa.
"""
from __future__ import annotations

import logging

from PyQt6 import sip
from PyQt6.QtCore import QObject, pyqtSignal

from models.barramento import barramento
from models.config_manager import Configuracao
from models.database_worker import DatabaseWorker
from models.estado_disjuntor import EstadoDisjuntor
from models.estado_link import EstadoLink
from models.evento import TipoEvento
from models.leitura import Leitura
from models.porta_serial import ErroConexaoSerial, ParametrosConexao
from models.serial_worker import SerialWorker
from models.servico_corte import ServicoCorteEmergencial

logger = logging.getLogger("smartgrid")


class AplicacaoController(QObject):
    disparo_espontaneo = pyqtSignal(str)
    velocidade_negociada = pyqtSignal(int)
    rele_confirmado = pyqtSignal(EstadoDisjuntor)
    botao_alterado = pyqtSignal(bool)
    falha_persistencia = pyqtSignal(str)
    aviso_comunicacao = pyqtSignal(str)
    ESPERA_ENCERRAMENTO_MS = 3000

    def __init__(self, config: Configuracao, servico_corte: ServicoCorteEmergencial,
                 estado_inicial: EstadoDisjuntor = EstadoDisjuntor.FECHADO,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._servico_corte = servico_corte
        self._estado_disjuntor = estado_inicial
        self._estado_reportado = False
        self._ultima_leitura = Leitura.zerada()
        self._serial: SerialWorker | None = None
        self._botao_pressionado = False
        self._corte_fisico_aguardando = False
        self._operacao_pendente = None
        self._encerrando = False
        self.banco = DatabaseWorker(parent=self)
        self.banco.finished.connect(self.banco.deleteLater)
        self.banco.falha_persistencia.connect(self.falha_persistencia)
        self.banco.start()
        barramento.evento_registrado.connect(self.banco.gravar_evento)
        barramento.estado_disjuntor_alterado.connect(self._ao_estado_disjuntor)

    @property
    def estado_disjuntor(self) -> EstadoDisjuntor:
        """Último estado conhecido; desconexão não equivale a abertura."""
        return self._estado_disjuntor

    @property
    def serial_ativa(self) -> bool:
        return self._serial is not None

    @property
    def botao_pressionado(self) -> bool:
        if self._serial is not None:
            return self._serial.botao_pressionado
        return self._botao_pressionado

    @property
    def contexto_operacao(self) -> tuple:
        """Invalida uma confirmação modal aberta antes de proteção/reconexão."""
        worker = self._serial
        return (worker, worker.geracao_protecao if worker is not None else 0)

    def conectar_serial(self, parametros: ParametrosConexao) -> None:
        if self._serial is not None or self._encerrando:
            raise ErroConexaoSerial("A conexão anterior ainda está sendo encerrada.")
        worker = SerialWorker(parametros.porta, parametros.baud_rate,
                              self._config.taxa_amostragem_ms, parametros.timeout_ms, parent=self)
        worker.estado_alterado.connect(self._ao_estado_link)
        worker.velocidade_negociada.connect(self.velocidade_negociada)
        worker.leitura_recebida.connect(self._ao_leitura)
        worker.botao_alterado.connect(self._ao_botao)
        worker.disjuntor_reportado.connect(self._ao_disjuntor_reportado)
        worker.aviso.connect(self.aviso_comunicacao)
        worker.finished.connect(self._ao_serial_encerrada)
        worker.finished.connect(worker.deleteLater)
        self._serial = worker
        self._estado_reportado = False
        self._botao_pressionado = False
        self._corte_fisico_aguardando = False
        worker.start()

    def desconectar_serial(self) -> None:
        if self._serial is not None:
            self._serial.parar()
            self._cancelar_operacao("Comunicação encerrada antes da confirmação do relé.")

    def atualizar_configuracao(self, config: Configuracao) -> None:
        """O novo intervalo de agregação vale na próxima conexão."""
        self._config = config

    def executar_comando_manual(self, destino, resumo, *, ao_concluir, ao_falhar) -> None:
        """Mantém rollback manual; cancela comandos antigos após proteção/reconexão."""
        worker = self._serial
        if worker is None:
            # Caminho usado pela demonstração sem placa e pelos testes do banco.
            self.banco.executar(self._servico_corte.executar, destino, resumo,
                                ao_concluir=ao_concluir, ao_falhar=ao_falhar)
            return
        if self._operacao_pendente is not None:
            ao_falhar(RuntimeError("Já existe uma atuação aguardando confirmação DAT."))
            return
        geracao = worker.geracao_protecao
        if destino.energizado and not worker.pode_religar(geracao):
            ao_falhar(RuntimeError("Libere o botão de proteção antes de solicitar o religamento."))
            return
        operacao = dict(worker=worker, destino=destino, geracao=geracao,
                        concluir=ao_concluir, falhar=ao_falhar, evento=None, enviada=False)
        self._operacao_pendente = operacao

        def validar():
            if (worker.isInterruptionRequested() or worker.geracao_protecao != geracao
                    or (destino.energizado and not worker.pode_religar(geracao))):
                raise RuntimeError("Operação cancelada pela proteção ou pela desconexão.")

        def concluida(evento):
            if self._operacao_pendente is not operacao:
                return
            if self._serial is not worker or not worker.enviar_rele(destino.energizado, geracao):
                self._cancelar_operacao("Comando cancelado pela proteção ou pela desconexão.")
                return
            operacao['evento'] = evento
            operacao['enviada'] = True
            # _ao_disjuntor_reportado conclui somente após DAT fresco.

        def falhou(erro):
            if self._operacao_pendente is operacao:
                self._operacao_pendente = None
                ao_falhar(erro)

        self.banco.executar(self._servico_corte.executar, destino, resumo,
                            publicar_estado=False, validar=validar,
                            ao_concluir=concluida, ao_falhar=falhou)

    def _cancelar_operacao(self, motivo: str) -> None:
        operacao, self._operacao_pendente = self._operacao_pendente, None
        if operacao is not None:
            if self._encerrando:
                # aboutToQuit já fechou a janela; não abrir um QMessageBox
                # modal enquanto aguardamos os workers terminarem.
                logger.warning("Operação interrompida no encerramento, sem confirmação: %s", motivo)
            else:
                operacao['falhar'](RuntimeError(motivo))

    def simular_disparo(self) -> None:
        """Na conexão real simula a solicitação; offline simula estado confirmado."""
        self._ao_botao(True)
        if self._serial is not None:
            self._serial.enviar_rele(False)
        else:
            self._ao_disjuntor_reportado(EstadoDisjuntor.ABERTO)
        self._ao_botao(False)

    def _ao_estado_link(self, nome: str, detalhe: str) -> None:
        barramento.estado_link_alterado.emit(nome, detalhe)
        estado = EstadoLink[nome]
        if estado is EstadoLink.TELEMETRIA:
            barramento.status_conexao_alterado.emit(True, detalhe)
            barramento.registrar_evento(TipoEvento.STATUS,
                                       f"Comunicação serial conectada em {detalhe}", detalhe)
            # Nenhum religamento automático ao conectar; o DAT é a referência.
        elif estado in (EstadoLink.DESCONECTADO, EstadoLink.LINK_PERDIDO):
            perdido = estado is EstadoLink.LINK_PERDIDO
            mensagem = f"Link perdido — {detalhe}" if perdido else detalhe
            barramento.status_conexao_alterado.emit(False, mensagem)
            barramento.registrar_evento(TipoEvento.ALERTA if perdido else TipoEvento.STATUS,
                                       "Link serial com a placa perdido" if perdido else
                                       "Comunicação serial encerrada", detalhe)
            self._cancelar_operacao("Sem confirmação do estado físico da carga: " + detalhe)
            self._estado_reportado = False

    def _ao_leitura(self, leitura: Leitura) -> None:
        self._ultima_leitura = leitura
        barramento.leitura_recebida.emit(leitura)
        self.banco.gravar_leitura(leitura)

    def _ao_botao(self, pressionado: bool) -> None:
        if pressionado == self._botao_pressionado:
            return
        self._botao_pressionado = pressionado
        if pressionado:
            self._corte_fisico_aguardando = True
            self._cancelar_operacao("Operação manual cancelada pelo botão de proteção; corte solicitado.")
            self.banco.executar(self._servico_corte.registrar_botao_protecao,
                                self._ultima_leitura.resumo(), ao_falhar=self._ao_falhar_persistencia)
        self.botao_alterado.emit(pressionado)

    def _ao_disjuntor_reportado(self, estado: EstadoDisjuntor) -> None:
        anterior = self._estado_disjuntor
        primeiro = not self._estado_reportado
        mudou = primeiro or estado is not anterior
        self._estado_reportado = True
        self._estado_disjuntor = estado
        self.rele_confirmado.emit(estado)
        operacao = self._operacao_pendente
        manual = bool(operacao and operacao['enviada'] and operacao['destino'] is estado)
        fisico = self._corte_fisico_aguardando and estado is EstadoDisjuntor.ABERTO
        if fisico:
            self._corte_fisico_aguardando = False
            self.disparo_espontaneo.emit("")  # DAT confirmou; nunca depende do commit
        if mudou or fisico:
            if (not primeiro and not manual and not fisico
                    and estado is EstadoDisjuntor.ABERTO):
                self.banco.executar(self._servico_corte.registrar_disparo_espontaneo,
                                    self._ultima_leitura.resumo(),
                                    ao_concluir=lambda _e: self.disparo_espontaneo.emit(""),
                                    ao_falhar=self._ao_falhar_registro_disparo)
            else:
                self.banco.executar(self._servico_corte.sincronizar_estado, estado,
                                    ao_falhar=self._ao_falhar_persistencia)
        if manual:
            self._operacao_pendente = None
            operacao['concluir'](operacao['evento'])

    def _ao_falhar_persistencia(self, erro: Exception) -> None:
        logger.error("Falha de persistência; estado físico não depende do banco: %s", erro)
        self.falha_persistencia.emit(str(erro))

    def _ao_falhar_registro_disparo(self, erro: Exception) -> None:
        logger.error("Falha ao registrar abertura confirmada: %s", erro)
        self.disparo_espontaneo.emit(str(erro))

    def _ao_estado_disjuntor(self, estado: EstadoDisjuntor) -> None:
        """Compatibilidade com serviços manuais; commit não confirma relé."""
        if self._serial is not None:
            if not estado.energizado:
                self._serial.enviar_rele(False)
            # Religamento exige o fluxo manual com geração de proteção; um
            # sinal de commit atrasado jamais pode reenergizar a carga.
        else:
            self._estado_disjuntor = estado

    def _ao_serial_encerrada(self) -> None:
        if self.sender() is self._serial:
            self._serial.entregar_leituras()
            self._serial = None

    def encerrar(self) -> None:
        if self._encerrando:
            return
        self._encerrando = True
        self.desconectar_serial()
        if self._serial is not None:
            worker = self._serial
            if not worker.wait(self.ESPERA_ENCERRAMENTO_MS):
                logger.warning("Aguardando encerramento cooperativo da serial.")
                worker.wait()  # nunca destruir uma QThread em execução
            worker.entregar_leituras()
        # Não aceitar tarefas após a sentinela; drena o que já foi recebido.
        if not sip.isdeleted(self.banco):
            self.banco.parar()
            if not self.banco.wait(self.ESPERA_ENCERRAMENTO_MS):
                logger.warning("Aguardando SQLite concluir a fila de persistência.")
                self.banco.wait()
        logger.info("Workers encerrados e recursos liberados.")
