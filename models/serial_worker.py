"""Thread dedicada à comunicação serial com a placa de medição.

Tudo o que bloqueia — abrir a porta, ``read``, ``write``, esperar o ACK —
acontece em :meth:`SerialWorker.run`, fora da thread da GUI. O resto da
aplicação só enxerga sinais, entregues na thread de quem os escuta
(conexão enfileirada do Qt):

``estado_alterado(str, str)``
    ``EstadoLink.name`` e um detalhe legível, a cada fase do link.
``velocidade_negociada(int)``
    Velocidade em que a telemetria passou a fluir.
``leitura_recebida(Leitura)``
    Cada pacote ``TLM`` válido.
``disjuntor_reportado(EstadoDisjuntor)``
    Estado de cada telemetria válida, inclusive quando não muda.
``rele_confirmado(EstadoDisjuntor)`` / ``rele_falhou(EstadoDisjuntor, str)``
    Resultado único por comando. ACK sozinho não confirma a atuação:
    é necessário receber telemetria posterior compatível.
``link_perdido(str)``
    Cabo arrancado, erro de I/O ou placa em silêncio. A thread termina.

Sequência (``models/protocolo.py``): abre SEMPRE a 9600 bps, repete o ``HS``
até a placa responder (o UNO reinicia ao abrir a porta), troca para a
velocidade aceita e confirma com ``SYNC``. Se o ``SYNC`` falhar, espera a
placa voltar a 9600 e tenta a próxima velocidade menor.

Encerramento cooperativo: :meth:`parar` pede a interrupção; o laço percebe em
até ``PERIODO_LEITURA_S``, envia ``BYE``, fecha a porta e ``run`` retorna,
disparando ``finished`` (o controller liga ``finished`` a ``deleteLater``).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime

import serial
from PyQt6.QtCore import QObject, QThread, pyqtSignal

from models import protocolo
from models.estado_disjuntor import EstadoDisjuntor
from models.estado_link import EstadoLink
from models.leitura import Leitura
from models.protocolo import Comando, ErroProtocolo

logger = logging.getLogger("smartgrid")


class ErroLink(Exception):
    """A placa não respondeu como o protocolo exige."""


@dataclass(slots=True)
class _PedidoRele:
    destino: EstadoDisjuntor
    prazo: float | None = None
    ack: bool = False
    reportado: EstadoDisjuntor | None = None


class SerialWorker(QThread):
    """Dono exclusivo da porta serial durante uma conexão."""

    estado_alterado = pyqtSignal(str, str)
    velocidade_negociada = pyqtSignal(int)
    leitura_recebida = pyqtSignal(Leitura)
    disjuntor_reportado = pyqtSignal(EstadoDisjuntor)
    rele_confirmado = pyqtSignal(EstadoDisjuntor)
    rele_falhou = pyqtSignal(EstadoDisjuntor, str)
    link_perdido = pyqtSignal(str)

    #: Timeout de cada ``read``: limita a demora para perceber ``parar()``.
    PERIODO_LEITURA_S = 0.1

    #: Intervalo entre repetições do ``HS`` enquanto a placa não responde.
    PERIODO_HS_S = 0.5

    #: Prazo do primeiro handshake — cobre os ~2 s de boot do UNO após o DTR.
    PRAZO_PRIMEIRO_HS_S = 6.0

    #: Prazo dos handshakes seguintes, depois de um ``SYNC`` que falhou.
    PRAZO_HS_S = 3.0

    #: Espera pelo ``ACK,SYNC`` na nova velocidade.
    PRAZO_SYNC_S = 1.0

    def __init__(
        self,
        porta: str,
        baud_alvo: int,
        intervalo_ms: int,
        timeout_ms: int,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        protocolo.validar_velocidade(baud_alvo)
        protocolo.validar_intervalo(intervalo_ms)

        self.porta = porta
        self.baud_alvo = baud_alvo
        self.intervalo_ms = intervalo_ms
        #: Silêncio tolerado na telemetria antes de declarar o link perdido.
        self.limite_silencio_s = 3 * intervalo_ms / 1000 + timeout_ms / 1000
        self.prazo_confirmacao_s = max(1.0, 2 * intervalo_ms / 1000 + timeout_ms / 1000)
        #: Depois de um ``SYNC`` sem resposta, quanto esperar a placa voltar a 9600.
        self.espera_retorno_placa_s = protocolo.TEMPO_SYNC_S + 0.2

        self._serial: serial.Serial | None = None
        self._buffer = b""
        self._comandos: queue.SimpleQueue[_PedidoRele] = queue.SimpleQueue()
        self._trava_rele = threading.Lock()
        self._pedido_rele: _PedidoRele | None = None
        self._disjuntor: EstadoDisjuntor | None = None
        self.pacotes_descartados = 0

    # ------------------------------------------------------------------
    # API usada pela thread da GUI (thread-safe)
    # ------------------------------------------------------------------
    def enviar_rele(self, fechado: bool) -> bool:
        """Reserva um único comando; recusa cliques repetidos ou contraditórios.

        Não há retransmissão automática, especialmente de religamento.
        """
        with self._trava_rele:
            if self._pedido_rele is not None or self.isInterruptionRequested():
                return False
            pedido = _PedidoRele(EstadoDisjuntor.FECHADO if fechado else EstadoDisjuntor.ABERTO)
            self._pedido_rele = pedido
            self._comandos.put(pedido)
        return True

    def parar(self) -> None:
        """Pede o encerramento cooperativo; não bloqueia."""
        self.requestInterruption()

    # ------------------------------------------------------------------
    # Corpo da thread
    # ------------------------------------------------------------------
    def run(self) -> None:
        try:
            self._abrir()
            baud = self._negociar()
            self._mudar_estado(EstadoLink.TELEMETRIA, f"{self.porta} @ {baud} bps")
            self.velocidade_negociada.emit(baud)
            self._receber_telemetria()
        except (serial.SerialException, OSError, ErroLink) as erro:
            self._concluir_rele(str(erro) or "Comunicação serial interrompida.")
            if not self.isInterruptionRequested():
                motivo = str(erro) or erro.__class__.__name__
                logger.error("Link serial perdido em %s: %s", self.porta, motivo)
                self._mudar_estado(EstadoLink.LINK_PERDIDO, motivo)
                self.link_perdido.emit(motivo)
        except Exception as erro:
            # Exceção solta em run() mataria a thread sem aviso nenhum.
            logger.exception("Erro inesperado na thread serial: %s", erro)
            self._concluir_rele(f"Erro na comunicação serial: {erro}")
            self._mudar_estado(EstadoLink.LINK_PERDIDO, f"Erro inesperado: {erro}")
            self.link_perdido.emit(str(erro))
        finally:
            self._concluir_rele("Comunicação serial encerrada antes da confirmação.")
            self._fechar()

    def _abrir(self) -> None:
        self._mudar_estado(EstadoLink.ABRINDO_PORTA, self.porta)
        self._serial = serial.Serial(
            self.porta,
            protocolo.VELOCIDADE_INICIAL,
            timeout=self.PERIODO_LEITURA_S,
            write_timeout=1.0,
        )
        logger.info(
            "Porta %s aberta a %d bps. Iniciando handshake.",
            self.porta, protocolo.VELOCIDADE_INICIAL,
        )

    def _fechar(self) -> None:
        if self._serial is None:
            return
        interrompido = self.isInterruptionRequested()
        try:
            if interrompido and self._serial.is_open:
                self._serial.write(protocolo.bye())
                self._serial.flush()
        except (serial.SerialException, OSError):
            pass  # a porta já pode ter sumido
        try:
            self._serial.close()
        except (serial.SerialException, OSError):
            pass
        self._serial = None
        if interrompido:
            logger.info("Porta %s fechada pela thread serial.", self.porta)
            self._mudar_estado(EstadoLink.DESCONECTADO, f"Porta {self.porta} liberada")

    # ------------------------------------------------------------------
    # Handshake e negociação
    # ------------------------------------------------------------------
    def _negociar(self) -> int:
        prazo_hs = self.PRAZO_PRIMEIRO_HS_S
        for baud in protocolo.velocidades_para_tentar(self.baud_alvo):
            self._serial.baudrate = protocolo.VELOCIDADE_INICIAL
            self._serial.reset_input_buffer()
            self._buffer = b""
            self._mudar_estado(
                EstadoLink.HANDSHAKE, f"Propondo {baud} bps, intervalo {self.intervalo_ms} ms"
            )

            if not self._handshake(baud, prazo_hs):
                continue
            prazo_hs = self.PRAZO_HS_S

            self._serial.baudrate = baud
            self._mudar_estado(EstadoLink.NEGOCIANDO, f"Confirmando {baud} bps")
            if self._sincronizar():
                logger.info("Handshake concluído: telemetria a %d bps.", baud)
                return baud

            logger.warning("Placa não confirmou %d bps; tentando velocidade menor.", baud)
            # Sem o SYNC a placa volta sozinha a 9600 depois de TEMPO_SYNC_S.
            self._dormir(self.espera_retorno_placa_s)

        raise ErroLink("A placa não respondeu ao handshake em nenhuma velocidade.")

    def _handshake(self, baud: int, prazo_s: float) -> bool:
        """Repete o ``HS`` até o ``ACK``; False em ``NAK`` ou prazo esgotado."""
        limite = time.monotonic() + prazo_s
        proximo_envio = 0.0
        while time.monotonic() < limite:
            self._verificar_interrupcao()
            if time.monotonic() >= proximo_envio:
                self._serial.write(protocolo.handshake(baud, self.intervalo_ms))
                proximo_envio = time.monotonic() + self.PERIODO_HS_S

            for mensagem in self._ler_mensagens():
                if protocolo.e_ack(mensagem, Comando.HS.value):
                    try:
                        aceito = protocolo.decodificar_handshake(mensagem)
                    except ErroProtocolo as erro:
                        self._descartar(erro)
                        continue
                    if aceito == (baud, self.intervalo_ms):
                        return True
                elif mensagem.comando is Comando.NAK:
                    logger.warning("Placa recusou %d bps: %s", baud, ",".join(mensagem.campos))
                    return False
        logger.warning("Sem ACK ao handshake de %d bps.", baud)
        return False

    def _sincronizar(self) -> bool:
        self._buffer = b""
        self._serial.reset_input_buffer()
        self._serial.write(protocolo.sync())
        limite = time.monotonic() + self.PRAZO_SYNC_S
        while time.monotonic() < limite:
            self._verificar_interrupcao()
            for mensagem in self._ler_mensagens():
                if protocolo.e_ack(mensagem, Comando.SYNC.value):
                    return True
        return False

    # ------------------------------------------------------------------
    # Telemetria
    # ------------------------------------------------------------------
    def _receber_telemetria(self) -> None:
        ultimo_pacote = time.monotonic()
        proximo_ping = 0.0
        while not self.isInterruptionRequested():
            agora = time.monotonic()
            if agora >= proximo_ping:
                self._serial.write(protocolo.ping())
                proximo_ping = agora + protocolo.PERIODO_PING_S
            # Processa primeiro o que já chegou. Uma amostra que estava no
            # buffer antes do envio não deve confirmar o próximo comando.
            for mensagem in self._ler_mensagens():
                if mensagem.comando is Comando.TLM:
                    if self._tratar_telemetria(mensagem):
                        ultimo_pacote = time.monotonic()
                elif protocolo.e_ack(mensagem, Comando.RELE.value):
                    self._tratar_ack_rele(mensagem)

            self._verificar_prazo_rele()
            silencio = time.monotonic() - ultimo_pacote
            if silencio > self.limite_silencio_s:
                raise ErroLink(f"Placa sem resposta há {silencio:.1f} s.")
            if not self.isInterruptionRequested():
                try:
                    pedido = self._comandos.get_nowait()
                except queue.Empty:
                    pass
                else:
                    # Fragmentos iniciados antes do comando não podem ser
                    # completados depois e usados como sua confirmação.
                    self._buffer = b""
                    self._serial.write(protocolo.rele(pedido.destino.energizado))
                    pedido.prazo = time.monotonic() + self.prazo_confirmacao_s

    def _tratar_telemetria(self, mensagem: protocolo.Mensagem) -> bool:
        try:
            tlm = protocolo.decodificar_telemetria(mensagem)
        except ErroProtocolo as erro:
            self._descartar(erro)
            return False

        self.leitura_recebida.emit(
            Leitura(instante=datetime.now(), tensao=tlm.tensao, corrente=tlm.corrente)
        )
        estado = EstadoDisjuntor.FECHADO if tlm.disjuntor_fechado else EstadoDisjuntor.ABERTO
        self._disjuntor = estado
        self.disjuntor_reportado.emit(estado)
        pedido = self._pedido_rele
        if pedido is not None and pedido.prazo is not None:
            pedido.reportado = estado
            if time.monotonic() >= pedido.prazo:
                self._verificar_prazo_rele()
            elif estado is pedido.destino:
                self._concluir_rele()
            elif pedido.ack:
                self._concluir_rele(
                    f"A placa aceitou {pedido.destino.name}, mas a telemetria "
                    f"posterior reportou {estado.name}."
                )
        return True

    def _tratar_ack_rele(self, mensagem: protocolo.Mensagem) -> None:
        if len(mensagem.campos) != 2 or mensagem.campos[1] not in ("0", "1"):
            self._descartar(ErroProtocolo("ACK de RELE inválido."))
            return
        pedido = self._pedido_rele
        if pedido is None or pedido.prazo is None:
            return  # ACK atrasado ou sem comando correspondente
        if mensagem.campos[1] != ("1" if pedido.destino.energizado else "0"):
            logger.warning("ACK de RELE não corresponde ao comando pendente.")
            return
        pedido.ack = True

    def _verificar_prazo_rele(self) -> None:
        pedido = self._pedido_rele
        if pedido is None or pedido.prazo is None or time.monotonic() < pedido.prazo:
            return
        reportado = pedido.reportado.name if pedido.reportado is not None else "DESCONHECIDO"
        self._concluir_rele(
            f"Tempo de confirmação esgotado para {pedido.destino.name}. "
            f"Estado reportado após o envio: {reportado}."
        )

    def _concluir_rele(self, erro: str = "") -> None:
        with self._trava_rele:
            pedido = self._pedido_rele
            if pedido is None:
                return
            self._pedido_rele = None
        if erro:
            logger.warning("Comando RELE sem confirmação: %s", erro)
            self.rele_falhou.emit(pedido.destino, erro)
        else:
            self.rele_confirmado.emit(pedido.destino)

    # ------------------------------------------------------------------
    # Leitura de bytes
    # ------------------------------------------------------------------
    def _ler_mensagens(self) -> list[protocolo.Mensagem]:
        """Lê o que houver (até ``PERIODO_LEITURA_S``) e devolve os pacotes completos."""
        dados = self._serial.read(max(1, self._serial.in_waiting))
        if not dados:
            return []
        self._buffer += dados

        mensagens = []
        while b"\n" in self._buffer:
            linha, self._buffer = self._buffer.split(b"\n", 1)
            if not linha.strip():
                continue
            try:
                mensagens.append(protocolo.interpretar(linha))
            except ErroProtocolo as erro:
                self._descartar(erro)
        if len(self._buffer) > protocolo.TAMANHO_MAXIMO:
            self._buffer = b""  # lixo sem \n (ex.: velocidade trocada)
        return mensagens

    def _descartar(self, erro: ErroProtocolo) -> None:
        self.pacotes_descartados += 1
        logger.warning("Pacote serial descartado: %s", erro)

    # ------------------------------------------------------------------
    # Auxiliares
    # ------------------------------------------------------------------
    def _mudar_estado(self, estado: EstadoLink, detalhe: str) -> None:
        self.estado_alterado.emit(estado.name, detalhe)

    def _verificar_interrupcao(self) -> None:
        if self.isInterruptionRequested():
            raise ErroLink("Conexão cancelada pelo operador.")

    def _dormir(self, segundos: float) -> None:
        """``sleep`` que acorda ao pedido de interrupção."""
        limite = time.monotonic() + segundos
        while time.monotonic() < limite:
            self._verificar_interrupcao()
            time.sleep(0.05)
