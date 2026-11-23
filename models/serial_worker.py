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
    Média das amostras ``DAT`` de cada intervalo de telemetria.
``disjuntor_reportado(EstadoDisjuntor)``
    Estado de cada leitura publicada, inclusive quando não muda.
``rele_confirmado(EstadoDisjuntor)`` / ``rele_falhou(EstadoDisjuntor, str)``
    Resultado único por comando. ACK sozinho não confirma a atuação:
    é necessário receber telemetria posterior compatível.
``botao_acionado()``
    O botão da placa foi pressionado (borda de subida, sem repique).
``link_perdido(str)``
    Cabo arrancado, erro de I/O ou placa em silêncio. A thread termina.

Sequência (``models/protocolo.py``): abre SEMPRE a 9600 bps e repete
``!S<baud>`` até a placa responder ``ACK: INICIANDO`` (o UNO reinicia ao
abrir a porta). Troca para a velocidade pedida e espera a primeira amostra
``DAT``. Se ela não chegar, envia ``!P`` (a placa volta a 9600) e tenta a
próxima velocidade menor.

A placa amostra a 200 Hz. O worker agrega as amostras e publica uma leitura
a cada ``intervalo_ms`` — a taxa de amostragem configurada no desktop.

Encerramento cooperativo: :meth:`parar` pede a interrupção; o laço percebe em
até ``PERIODO_LEITURA_S``, envia ``!P``, fecha a porta e ``run`` retorna,
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
from models.protocolo import Amostra, ErroProtocolo, Resposta, TipoResposta

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
    botao_acionado = pyqtSignal()
    link_perdido = pyqtSignal(str)

    #: Timeout de cada ``read``: limita a demora para perceber ``parar()``.
    PERIODO_LEITURA_S = 0.1

    #: Intervalo entre repetições do ``!S`` enquanto a placa não responde.
    PERIODO_INICIO_S = 0.5

    #: Prazo do primeiro ``!S`` — cobre os ~2 s de boot do UNO após o DTR.
    PRAZO_PRIMEIRO_INICIO_S = 6.0

    #: Prazo dos ``!S`` seguintes, depois de uma velocidade que falhou.
    PRAZO_INICIO_S = 3.0

    #: Espera pela primeira amostra ``DAT`` na nova velocidade.
    PRAZO_PRIMEIRA_AMOSTRA_S = 1.0

    #: Botão solto por pelo menos este tempo antes de uma nova borda valer.
    DEBOUNCE_BOTAO_S = 0.05

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
        self.intervalo_s = intervalo_ms / 1000
        #: Silêncio tolerado na telemetria antes de declarar o link perdido.
        self.limite_silencio_s = 3 * intervalo_ms / 1000 + timeout_ms / 1000
        self.prazo_confirmacao_s = max(1.0, 2 * intervalo_ms / 1000 + timeout_ms / 1000)
        #: Depois do ``!P``, quanto esperar a placa voltar a 9600.
        self.espera_retorno_placa_s = 0.2

        self._serial: serial.Serial | None = None
        self._buffer = b""
        self._janela: list[Amostra] = []
        self._ignorar_amostras = 0
        self._botao_solto_desde: float | None = None
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
                self._serial.write(protocolo.pausar())
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
        prazo = self.PRAZO_PRIMEIRO_INICIO_S
        for baud in protocolo.velocidades_para_tentar(self.baud_alvo):
            self._trocar_velocidade(protocolo.VELOCIDADE_INICIAL)
            self._mudar_estado(
                EstadoLink.HANDSHAKE, f"Propondo {baud} bps, intervalo {self.intervalo_ms} ms"
            )

            if not self._iniciar(baud, prazo):
                continue
            prazo = self.PRAZO_INICIO_S

            self._trocar_velocidade(baud)
            self._mudar_estado(EstadoLink.NEGOCIANDO, f"Confirmando {baud} bps")
            if self._aguardar_amostra():
                logger.info("Handshake concluído: telemetria a %d bps.", baud)
                return baud

            logger.warning("Sem amostras a %d bps; tentando velocidade menor.", baud)
            # A placa não tem watchdog: só o !P a traz de volta a 9600.
            self._serial.write(protocolo.pausar())
            self._dormir(self.espera_retorno_placa_s)

        raise ErroLink("A placa não respondeu ao handshake em nenhuma velocidade.")

    def _iniciar(self, baud: int, prazo_s: float) -> bool:
        """Repete o ``!S`` até o ACK; False em ``ERR`` ou prazo esgotado."""
        limite = time.monotonic() + prazo_s
        proximo_envio = 0.0
        while time.monotonic() < limite:
            self._verificar_interrupcao()
            if time.monotonic() >= proximo_envio:
                self._serial.write(protocolo.iniciar(baud))
                proximo_envio = time.monotonic() + self.PERIODO_INICIO_S

            for mensagem in self._ler_mensagens():
                if not isinstance(mensagem, Resposta):
                    continue
                if mensagem.tipo is TipoResposta.INICIANDO and mensagem.baud == baud:
                    return True
                if mensagem.tipo is TipoResposta.ERRO:
                    logger.warning("Placa recusou %d bps: %s", baud, mensagem.texto)
                    return False
        logger.warning("Sem ACK ao início em %d bps.", baud)
        return False

    def _aguardar_amostra(self) -> bool:
        limite = time.monotonic() + self.PRAZO_PRIMEIRA_AMOSTRA_S
        while time.monotonic() < limite:
            self._verificar_interrupcao()
            if any(isinstance(m, Amostra) for m in self._ler_mensagens()):
                return True
        return False

    def _trocar_velocidade(self, baud: int) -> None:
        self._serial.baudrate = baud
        self._serial.reset_input_buffer()
        self._buffer = b""

    # ------------------------------------------------------------------
    # Telemetria
    # ------------------------------------------------------------------
    def _receber_telemetria(self) -> None:
        ultimo_pacote = time.monotonic()
        fim_janela = ultimo_pacote + self.intervalo_s
        self._janela.clear()
        while not self.isInterruptionRequested():
            # Processa primeiro o que já chegou. Uma amostra que estava no
            # buffer antes do envio não deve confirmar o próximo comando.
            for mensagem in self._ler_mensagens():
                if isinstance(mensagem, Amostra):
                    ultimo_pacote = time.monotonic()
                    self._tratar_amostra(mensagem)
                elif isinstance(mensagem, Resposta) and mensagem.tipo in (
                    TipoResposta.RELE_LIGADO, TipoResposta.RELE_DESLIGADO,
                ):
                    self._tratar_ack_rele(mensagem)

            agora = time.monotonic()
            if agora >= fim_janela:
                fim_janela = agora + self.intervalo_s
                self._publicar_janela()

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
                    # Fragmentos e amostras anteriores ao comando não podem
                    # ser usados como sua confirmação.
                    self._buffer = b""
                    self._janela.clear()
                    self._ignorar_amostras = 0
                    self._serial.write(protocolo.rele(pedido.destino.energizado))
                    pedido.prazo = time.monotonic() + self.prazo_confirmacao_s

    def _tratar_amostra(self, amostra: Amostra) -> None:
        agora = time.monotonic()
        if amostra.botao_pressionado:
            solto = self._botao_solto_desde
            if solto is not None and agora - solto >= self.DEBOUNCE_BOTAO_S:
                logger.info("Botão da placa acionado.")
                self.botao_acionado.emit()
            self._botao_solto_desde = None
        elif self._botao_solto_desde is None:
            self._botao_solto_desde = agora

        if self._ignorar_amostras:
            self._ignorar_amostras -= 1
            return
        self._janela.append(amostra)

    def _publicar_janela(self) -> None:
        if not self._janela:
            return
        amostras, self._janela = self._janela, []
        tensao, corrente = protocolo.converter(
            sum(a.a0 for a in amostras) / len(amostras),
            sum(a.a1 for a in amostras) / len(amostras),
        )
        fechado = amostras[-1].rele_ligado
        if not fechado:
            # Sem carga não há corrente; o potenciômetro da bancada não sabe disso.
            corrente = 0.0

        self.leitura_recebida.emit(
            Leitura(instante=datetime.now(), tensao=tensao, corrente=corrente)
        )
        estado = EstadoDisjuntor.FECHADO if fechado else EstadoDisjuntor.ABERTO
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

    def _tratar_ack_rele(self, resposta: Resposta) -> None:
        pedido = self._pedido_rele
        if pedido is None or pedido.prazo is None:
            return  # ACK atrasado ou sem comando correspondente
        if (resposta.tipo is TipoResposta.RELE_LIGADO) != pedido.destino.energizado:
            logger.warning("ACK de RELE não corresponde ao comando pendente.")
            return
        pedido.ack = True
        # O Timer1 pode ter capturado a amostra seguinte antes da troca do
        # relé: ela sai depois do ACK, mas com o estado antigo.
        self._janela.clear()
        self._ignorar_amostras = 1

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
    def _ler_mensagens(self) -> list[Amostra | Resposta]:
        """Lê o que houver (até ``PERIODO_LEITURA_S``) e devolve as linhas completas.

        Texto informativo (o banner do boot) é ignorado sem contar como descarte.
        """
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
                mensagem = protocolo.interpretar(linha)
            except ErroProtocolo as erro:
                self._descartar(erro)
                continue
            if mensagem is not None:
                mensagens.append(mensagem)
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
