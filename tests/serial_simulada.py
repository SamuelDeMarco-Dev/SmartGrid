"""Transporte de bytes para testes do SerialWorker, sem porta física nem pty."""

from threading import Condition
from time import monotonic

from PyQt6.QtCore import QThread
from serial import SerialException

from models import protocolo
from models.protocolo import ErroProtocolo, Resposta, TipoResposta
from tools.simulador_placa import PlacaVirtual

ACKS_RELE = (TipoResposta.RELE_LIGADO, TipoResposta.RELE_DESLIGADO)


class PlacaEmMemoria(PlacaVirtual):
    """Usa a máquina de estados da placa virtual sobre um buffer com timeout."""

    def __init__(self, caminho: str, **opcoes):
        # ``reiniciar()`` já envia o banner: o buffer precisa existir antes.
        self.condicao = Condition()
        self.respostas = bytearray()
        self.cabo_conectado = True
        self.silenciosa = False
        self.conexoes = []
        self.escritas = []
        self.perder_rele = False
        self.omitir_ack = False
        self.ack_sem_atuar = False
        self.ack_incorreto = False
        self.silenciar_telemetria = False
        super().__init__(-1, caminho, **opcoes)

    def iniciar(self):
        # Este transporte é temporizado pelos reads/writes, sem thread de pty.
        return self

    def parar(self):
        self.arrancar_cabo()

    def _enviar(self, pacote: bytes) -> None:
        with self.condicao:
            if self.silenciosa:
                return
            if self.omitir_ack or self.ack_incorreto:
                try:
                    mensagem = protocolo.interpretar(pacote)
                except ErroProtocolo:
                    mensagem = None  # A injeção de bytes corrompidos deve chegar ao worker.
                if isinstance(mensagem, Resposta) and mensagem.tipo in ACKS_RELE:
                    if self.omitir_ack:
                        return
                    pacote = protocolo.resposta_rele(mensagem.tipo is TipoResposta.RELE_DESLIGADO)
            self.respostas.extend(pacote)
            self.condicao.notify_all()

    def _processar(self, cmd: str) -> None:
        if self.conversao_habilitada and cmd in ("0", "1"):
            if self.perder_rele:
                self.comandos_recebidos.append(cmd)
                return
            if self.ack_sem_atuar:
                self.comandos_recebidos.append(cmd)
                self._enviar(protocolo.resposta_rele(cmd == "1"))
                return
        super()._processar(cmd)

    def _enviar_amostra(self):
        if not self.silenciar_telemetria:
            super()._enviar_amostra()

    def injetar(self, pacote: bytes) -> None:
        with self.condicao:
            self._enviar(pacote)
            self.condicao.notify_all()

    def arrancar_cabo(self) -> None:
        with self.condicao:
            self.cabo_conectado = False
            self.condicao.notify_all()

    def silenciar(self) -> None:
        with self.condicao:
            self.silenciosa = True
            self.respostas.clear()

    def abrir(self, baudrate, timeout, *, write_timeout):
        with self.condicao:
            if not self.cabo_conectado:
                raise SerialException("Placa virtual desconectada")
            conexao = PortaEmMemoria(self, baudrate, timeout)
            self.conexoes.append(conexao)
            return conexao


class PortaEmMemoria:
    def __init__(self, placa, baudrate, timeout):
        self.placa = placa
        self.baudrate = baudrate
        self.timeout = timeout
        self.is_open = True
        self.threads = {QThread.currentThread()}

    def _verificar_link(self) -> None:
        self.threads.add(QThread.currentThread())
        if not self.is_open or not self.placa.cabo_conectado:
            raise SerialException("Placa virtual desconectada")

    @property
    def in_waiting(self):
        with self.placa.condicao:
            self._verificar_link()
            return len(self.placa.respostas)

    def write(self, pacote: bytes) -> int:
        with self.placa.condicao:
            self._verificar_link()
            self.placa.escritas.append((self.baudrate, pacote))
            self.placa._temporizar(monotonic())
            if self.baudrate == self.placa.baud:
                self.placa._receber(pacote)
            return len(pacote)

    def read(self, tamanho: int) -> bytes:
        with self.placa.condicao:
            self._verificar_link()
            self.placa._temporizar(monotonic())
            if not self.placa.respostas:
                espera = self.timeout
                if self.placa.conversao_habilitada:
                    # Acorda a tempo da próxima amostra do Timer1 simulado.
                    espera = min(espera, max(0.0, self.placa._proxima_amostra - monotonic()))
                self.placa.condicao.wait(espera)
                self._verificar_link()
                self.placa._temporizar(monotonic())
            dados = bytes(self.placa.respostas[:tamanho])
            del self.placa.respostas[:tamanho]
            return dados

    def reset_input_buffer(self):
        with self.placa.condicao:
            self._verificar_link()
            self.placa.respostas.clear()

    def flush(self):
        self._verificar_link()

    def close(self):
        self.threads.add(QThread.currentThread())
        self.is_open = False
