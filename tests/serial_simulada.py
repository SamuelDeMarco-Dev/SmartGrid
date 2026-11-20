"""Transporte de bytes para testes do SerialWorker, sem porta física nem pty."""

from threading import Condition
from time import monotonic

from PyQt6.QtCore import QThread
from serial import SerialException

from models import protocolo
from models.protocolo import Comando, ErroProtocolo
from tools.simulador_placa import TELEMETRIA, PlacaVirtual


class PlacaEmMemoria(PlacaVirtual):
    """Usa a máquina de estados da placa virtual sobre um buffer com timeout."""

    def __init__(self, caminho: str, **opcoes):
        super().__init__(-1, caminho, **opcoes)
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
                    pass  # A injeção de bytes corrompidos deve chegar ao worker.
                else:
                    if protocolo.e_ack(mensagem, "RELE") and len(mensagem.campos) == 2:
                        if self.omitir_ack:
                            return
                        pacote = protocolo.ack("RELE", "0" if mensagem.campos[1] == "1" else "1")
            self.respostas.extend(pacote)
            self.condicao.notify_all()

    def _tratar(self, mensagem, agora):
        if (
            self.estado == TELEMETRIA and mensagem.comando is Comando.RELE
            and mensagem.campos in (("0",), ("1",))
        ):
            if self.perder_rele:
                return
            if self.ack_sem_atuar:
                self._enviar(protocolo.ack("RELE", *mensagem.campos))
                return
        super()._tratar(mensagem, agora)

    def _enviar_telemetria(self):
        if not self.silenciar_telemetria:
            super()._enviar_telemetria()

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
                self.placa.condicao.wait(self.timeout)
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
