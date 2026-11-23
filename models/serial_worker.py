"""Serial em uma QThread: negociação !S, DAT, proteção prioritária e agregação.

A porta pertence exclusivamente a run(). As filas de comandos e de entrega
à GUI são limitadas; os 200 Hz físicos não viram 200 gravações/s no SQLite.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime

import serial
from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal, pyqtSlot

from models import protocolo
from models.estado_disjuntor import EstadoDisjuntor
from models.estado_link import EstadoLink
from models.leitura import Leitura
from models.protocolo import Comando, ErroProtocolo

logger = logging.getLogger("smartgrid")


class ErroLink(Exception):
    """Não há telemetria válida para confirmar o estado físico."""


class SerialWorker(QThread):
    estado_alterado = pyqtSignal(str, str)
    velocidade_negociada = pyqtSignal(int)
    leitura_recebida = pyqtSignal(Leitura)
    disjuntor_reportado = pyqtSignal(EstadoDisjuntor)
    botao_alterado = pyqtSignal(bool)
    link_perdido = pyqtSignal(str)
    aviso = pyqtSignal(str)
    _leituras_disponiveis = pyqtSignal()

    PERIODO_LEITURA_S = 0.02
    PRAZO_PRIMEIRO_HS_S = 6.0  # inclui boot/reset do UNO por DTR
    PRAZO_ACK_S = 0.25
    PRAZO_DAT_S = 0.6
    PRAZO_ATUACAO_S = 1.0
    DEBOUNCE_LIBERACAO_S = 0.05
    MAX_LEITURAS_PENDENTES = 1000

    def __init__(self, porta: str, baud_alvo: int, intervalo_ms: int,
                 timeout_ms: int, parent: QObject | None = None) -> None:
        super().__init__(parent)
        protocolo.validar_velocidade(baud_alvo)
        protocolo.validar_intervalo(intervalo_ms)
        self.porta = porta
        self.baud_alvo = baud_alvo
        self.intervalo_ms = intervalo_ms
        # O silêncio é da transmissão física, não da janela de agregação.
        self.limite_silencio_s = max(0.5, timeout_ms / 1000)
        self._serial = None
        self._buffer = b""
        self._trava = threading.Lock()
        self._rele_pendente: bool | None = None
        self._aguardando: tuple[bool, float, int] | None = None
        self._lote = 0
        self._botao: bool | None = None
        self._liberacao_desde: float | None = None
        self._geracao_protecao = 0
        self._disjuntor: EstadoDisjuntor | None = None
        self._primeiras_mensagens = []
        self._contagem = 0
        self._soma_v = self._soma_i = 0.0
        self._picos = (0.0, 0.0, 0.0)
        self._inicio_janela = time.monotonic()
        self._entregas: deque[Leitura] = deque()
        self._entrega_agendada = False
        self.pacotes_descartados = 0
        self.pacotes_validos = 0
        self.leituras_descartadas = 0
        self._leituras_disponiveis.connect(self.entregar_leituras, Qt.ConnectionType.QueuedConnection)

    @property
    def geracao_protecao(self) -> int:
        with self._trava:
            return self._geracao_protecao

    @property
    def botao_pressionado(self) -> bool:
        with self._trava:
            return self._botao is not False

    def pode_religar(self, geracao: int) -> bool:
        with self._trava:
            return (self._botao is False and geracao == self._geracao_protecao
                    and not self.isInterruptionRequested())

    def enviar_rele(self, fechado: bool, geracao: int | None = None) -> bool:
        """Uma posição pendente: !0 substitui !1 e não pode ser ultrapassado."""
        with self._trava:
            if self.isInterruptionRequested():
                return False
            if fechado and (self._botao is not False or self._rele_pendente is False
                            or (geracao is not None and geracao != self._geracao_protecao)):
                return False
            self._rele_pendente = fechado
        return True

    def parar(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        try:
            self._abrir()
            baud = self._negociar()
            self._mudar_estado(EstadoLink.TELEMETRIA, f"{self.porta} @ {baud} bps")
            self.velocidade_negociada.emit(baud)
            self._receber_telemetria()
        except (serial.SerialException, OSError, ErroLink) as erro:
            if not self.isInterruptionRequested():
                motivo = str(erro) or erro.__class__.__name__
                logger.error("Link serial perdido: %s. Estado da carga não confirmado.", motivo)
                self._mudar_estado(EstadoLink.LINK_PERDIDO, motivo)
                self.link_perdido.emit(motivo)
        except Exception as erro:
            logger.exception("Erro inesperado na thread serial")
            self._mudar_estado(EstadoLink.LINK_PERDIDO, str(erro))
            self.link_perdido.emit(str(erro))
        finally:
            self._publicar_janela()
            self._fechar()

    def _abrir(self) -> None:
        self._mudar_estado(EstadoLink.ABRINDO_PORTA, self.porta)
        self._serial = serial.Serial(self.porta, 9600, timeout=self.PERIODO_LEITURA_S,
                                     write_timeout=1.0)

    def _fechar(self) -> None:
        if self._serial is None:
            return
        interrompido = self.isInterruptionRequested()
        try:
            if self._serial.is_open:
                # !P NÃO abre o relé. Nenhum requisito exige corte ao sair.
                self._serial.write(protocolo.pausar())
                # Ler o ACK drena a escrita antes do close, sem flush que
                # poderia esperar indefinidamente por um driver defeituoso.
                limite = time.monotonic() + 0.15
                while time.monotonic() < limite:
                    if any(protocolo.e_ack(m, "P") for m in self._ler_mensagens()):
                        break
        except (serial.SerialException, OSError):
            pass
        finally:
            try:
                self._serial.close()
            except (serial.SerialException, OSError):
                pass
            self._serial = None
        if interrompido:
            self._mudar_estado(EstadoLink.DESCONECTADO,
                               f"Porta {self.porta} liberada; último estado conhecido preservado")

    def _trocar_velocidade(self, baud: int) -> None:
        self._serial.baudrate = baud
        self._buffer = b""  # fragmentos de uma velocidade não pertencem à próxima
        self._serial.reset_input_buffer()

    def _negociar(self) -> int:
        self._mudar_estado(EstadoLink.HANDSHAKE, "Iniciando em 9600 bps; aguardando boot do UNO")
        prazo_boot = time.monotonic() + self.PRAZO_PRIMEIRO_HS_S
        # Não repetir !S na velocidade antiga sem testar DAT na nova: o ACK
        # pode se perder depois de o firmware já ter trocado de velocidade.
        while time.monotonic() < prazo_boot:
            if self._iniciar_em_9600(self.baud_alvo):
                return self.baud_alvo
            self._verificar_interrupcao()
            # Recupera inclusive placa que ficou transmitindo sem watchdog.
            self._recuperar_9600()
        for baud in protocolo.velocidades_para_tentar(self.baud_alvo):
            self._recuperar_9600()
            if self._iniciar_em_9600(baud):
                return baud
        raise ErroLink("Sem DAT válido após boot e recuperação nas quatro velocidades.")

    def _recuperar_9600(self) -> None:
        # !P é enviado em TODAS as velocidades possíveis, sem pressupor
        # retorno automático do firmware. A porta permanece na mesma thread.
        for baud in dict.fromkeys((self.baud_alvo, *reversed(protocolo.VELOCIDADES))):
            self._verificar_interrupcao()
            self._trocar_velocidade(baud)
            self._serial.write(protocolo.pausar())
            self._dormir(0.12)  # ACK a 9600 + troca de 20 ms e margem USB
        self._trocar_velocidade(9600)

    def _iniciar_em_9600(self, baud: int) -> bool:
        self._trocar_velocidade(9600)
        self._serial.write(protocolo.iniciar(baud))
        limite = time.monotonic() + self.PRAZO_ACK_S
        while time.monotonic() < limite:
            self._verificar_interrupcao()
            mensagens = self._ler_mensagens()
            if baud == 9600 and any(m.comando is Comando.DAT for m in mensagens):
                self._primeiras_mensagens = mensagens
                self._mudar_estado(EstadoLink.NEGOCIANDO, "DAT válido a 9600 bps")
                return True
            if any(protocolo.e_ack(m, "S", str(baud)) for m in mensagens):
                break
        self._mudar_estado(EstadoLink.NEGOCIANDO, f"Confirmando DAT em {baud} bps")
        if baud != 9600:
            self._trocar_velocidade(baud)
        limite = time.monotonic() + self.PRAZO_DAT_S
        while time.monotonic() < limite:
            self._verificar_interrupcao()
            mensagens = self._ler_mensagens()
            if any(m.comando is Comando.DAT for m in mensagens):
                self._primeiras_mensagens = mensagens
                return True
        return False

    def _receber_telemetria(self) -> None:
        ultimo_dat = time.monotonic()
        mensagens = self._primeiras_mensagens
        while not self.isInterruptionRequested():
            # Processa os DAT já recebidos ANTES de um eventual religamento.
            self._lote += 1
            for mensagem in mensagens:
                if mensagem.comando is Comando.DAT:
                    self._tratar_telemetria(mensagem)
                    ultimo_dat = time.monotonic()
                elif mensagem.comando is Comando.ERR:
                    self.aviso.emit("Erro informado pela placa: " + ", ".join(mensagem.campos))
            self._enviar_pendente()
            agora = time.monotonic()
            if self._aguardando and agora - self._aguardando[1] > self.PRAZO_ATUACAO_S:
                raise ErroLink("Atuação do relé sem confirmação por DAT; estado físico desconhecido.")
            if agora - ultimo_dat > self.limite_silencio_s:
                raise ErroLink(f"Placa sem resposta DAT há {agora - ultimo_dat:.1f} s.")
            mensagens = self._ler_mensagens()

    def _enviar_pendente(self) -> None:
        with self._trava:
            destino = self._rele_pendente
            self._rele_pendente = None
            if destino is True and self._botao is not False:
                destino = False
        if destino is None:
            return
        if self._aguardando and self._aguardando[0] is destino:
            return
        self._serial.write(protocolo.rele(destino))
        self._aguardando = (destino, time.monotonic(), self._lote)

    def _tratar_botao(self, pressionado: bool, agora: float) -> None:
        mudou = False
        with self._trava:
            if pressionado:
                self._liberacao_desde = None
                if self._botao is not True:
                    self._geracao_protecao += 1
                    self._botao = True
                    self._rele_pendente = False
                    mudou = True
            elif self._botao is None:
                self._botao = False
                mudou = True
            elif self._botao:
                if self._liberacao_desde is None:
                    self._liberacao_desde = agora
                elif agora - self._liberacao_desde >= self.DEBOUNCE_LIBERACAO_S:
                    self._botao = False
                    mudou = True
        if mudou:
            # Pressão atua imediatamente; somente liberação exige 50 ms
            # estáveis. Bounce não rearma nem produz outra ocorrência.
            if pressionado:
                self._enviar_pendente()  # independe de event loop e SQLite
            self.botao_alterado.emit(pressionado)

    def _tratar_telemetria(self, mensagem: protocolo.Mensagem) -> None:
        tlm = protocolo.decodificar_telemetria(mensagem)
        self.pacotes_validos += 1
        agora = time.monotonic()
        self._tratar_botao(tlm.botao_pressionado, agora)
        estado = EstadoDisjuntor.FECHADO if tlm.disjuntor_fechado else EstadoDisjuntor.ABERTO
        confirmou = (self._aguardando is not None and self._lote > self._aguardando[2]
                     and tlm.disjuntor_fechado is self._aguardando[0])
        if confirmou:
            self._aguardando = None
        if estado is not self._disjuntor:
            self._publicar_janela()  # não mistura corrente de relé ligado/desligado
            self._disjuntor = estado
            self.disjuntor_reportado.emit(estado)
        elif confirmou:
            self.disjuntor_reportado.emit(estado)
        if tlm.botao_pressionado and tlm.disjuntor_fechado and self._aguardando is None:
            self.enviar_rele(False)  # fechamento externo enquanto botão está retido
            self._enviar_pendente()
        if self._contagem == 0:
            self._inicio_janela = agora
        self._soma_v += tlm.tensao
        self._soma_i += tlm.corrente
        self._contagem += 1
        self._picos = tuple(max(a, b) for a, b in zip(
            self._picos, (tlm.tensao, tlm.corrente, tlm.tensao * tlm.corrente)))
        if agora - self._inicio_janela >= self.intervalo_ms / 1000:
            self._publicar_janela()

    def _publicar_janela(self) -> None:
        if not self._contagem:
            return
        leitura = Leitura(datetime.now(), self._soma_v / self._contagem,
                          self._soma_i / self._contagem, self._picos)
        self._contagem = 0
        self._soma_v = self._soma_i = 0.0
        self._picos = (0.0, 0.0, 0.0)
        self._inicio_janela = time.monotonic()
        avisar = False
        with self._trava:
            if len(self._entregas) >= self.MAX_LEITURAS_PENDENTES:
                self._entregas.popleft()
                self.leituras_descartadas += 1
                if self.leituras_descartadas == 1:
                    avisar = True
            self._entregas.append(leitura)
            agendar = not self._entrega_agendada
            self._entrega_agendada = True
        if agendar:
            self._leituras_disponiveis.emit()
        if avisar:
            self.aviso.emit("GUI atrasada: fila de agregados cheia; leituras antigas descartadas.")

    @pyqtSlot()
    def entregar_leituras(self) -> None:
        """Executado na GUI; também chamado antes de parar o banco ao sair."""
        with self._trava:
            leituras = list(self._entregas)
            self._entregas.clear()
            self._entrega_agendada = False
        for leitura in leituras:
            self.leitura_recebida.emit(leitura)

    def _ler_mensagens(self) -> list[protocolo.Mensagem]:
        dados = self._serial.read(min(4096, max(1, self._serial.in_waiting)))
        self._buffer += dados
        mensagens = []
        while b"\n" in self._buffer:
            linha, self._buffer = self._buffer.split(b"\n", 1)
            if not linha.strip():
                continue
            # O banner de boot é informativo; todos os DAT são validados.
            if linha.startswith((b"=", b"PLACA EM ESPERA", b"Envie !")):
                continue
            try:
                mensagens.append(protocolo.interpretar(linha))
            except ErroProtocolo as erro:
                self._descartar(erro)
        if len(self._buffer) > protocolo.TAMANHO_MAXIMO:
            self._buffer = b""
            self._descartar(ErroProtocolo("Fragmento serial sem terminador excedeu o limite."))
        return mensagens

    def _descartar(self, erro: ErroProtocolo) -> None:
        self.pacotes_descartados += 1
        # Evita I/O de log a cada byte quando a velocidade está errada.
        if self.pacotes_descartados <= 5 or self.pacotes_descartados % 200 == 0:
            logger.warning("Pacote serial descartado (%d): %s", self.pacotes_descartados, erro)

    def _mudar_estado(self, estado: EstadoLink, detalhe: str) -> None:
        self.estado_alterado.emit(estado.name, detalhe)

    def _verificar_interrupcao(self) -> None:
        if self.isInterruptionRequested():
            raise ErroLink("Conexão cancelada pelo operador.")

    def _dormir(self, segundos: float) -> None:
        limite = time.monotonic() + segundos
        while time.monotonic() < limite:
            self._verificar_interrupcao()
            time.sleep(0.01)
