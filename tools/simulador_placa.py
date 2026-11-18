"""Placa de medição virtual — o Arduino UNO emulado num pseudo-terminal.

Implementa a mesma máquina de estados de ``firmware/smartgrid_placa`` (ver
``models/protocolo.py``), para testar e demonstrar o supervisório sem o
hardware. A pty não tem velocidade física: a troca de baud rate é só lógica,
mas a sequência de pacotes é idêntica à da placa real.

Uso (macOS/Linux)::

    python tools/simulador_placa.py              # mostra o caminho da porta
    python tools/simulador_placa.py --falhar-sync 115200

Copie o caminho exibido (ex.: ``/dev/ttys004``) para o campo de porta da tela
"Comunicação Serial". Comandos no terminal do simulador:

    d  disparo da proteção (abre o disjuntor, como o botão da placa)
    x  arranca o "cabo" (fecha a pty; o desktop deve detectar a queda)
    q  sair
"""

from __future__ import annotations

import argparse
import os
import select
import sys
import threading
import time
import tty
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import protocolo
from models.protocolo import Comando, ErroProtocolo
from models.simulador_telemetria import gerar_leitura

AGUARDANDO_LINK = "AGUARDANDO_LINK"
AGUARDANDO_SYNC = "AGUARDANDO_SYNC"
TELEMETRIA = "TELEMETRIA"


class PlacaVirtual:
    """Lado da placa do protocolo, rodando numa thread sobre o mestre da pty."""

    def __init__(
        self,
        fd_mestre: int,
        caminho: str,
        *,
        falhar_sync_em: tuple[int, ...] = (),
        tempo_sync_s: float = protocolo.TEMPO_SYNC_S,
        watchdog_s: float = protocolo.WATCHDOG_PLACA_S,
    ) -> None:
        self.caminho = caminho
        self._fd = fd_mestre
        self._falhar_sync_em = set(falhar_sync_em)
        self._tempo_sync_s = tempo_sync_s
        self._watchdog_s = watchdog_s

        self.estado = AGUARDANDO_LINK
        self.baud = protocolo.VELOCIDADE_INICIAL
        self.rele_fechado = True
        self.pacotes_recebidos: list[protocolo.Mensagem] = []

        self._intervalo_s = 1.0
        self._seq = 0
        self._prazo_sync = 0.0
        self._ultimo_host = 0.0
        self._proxima_tlm = 0.0
        self._buffer = b""
        self._parar = threading.Event()
        self._thread = threading.Thread(target=self._laco, daemon=True)

    @classmethod
    def criar(cls, **opcoes) -> "PlacaVirtual":
        """Abre um par pty e devolve a placa; o desktop usa ``placa.caminho``."""
        mestre, escravo = os.openpty()
        tty.setraw(escravo)
        caminho = os.ttyname(escravo)
        placa = cls(mestre, caminho, **opcoes)
        # Mantém o lado escravo aberto: sem nenhum leitor, o mestre recebe EIO
        # até o desktop abrir a porta.
        placa._fd_escravo = escravo
        return placa

    # ------------------------------------------------------------------
    # Controle
    # ------------------------------------------------------------------
    def iniciar(self) -> "PlacaVirtual":
        self._thread.start()
        return self

    def parar(self) -> None:
        self._parar.set()
        self._thread.join(timeout=2)
        for fd in (self._fd, getattr(self, "_fd_escravo", None)):
            if fd is None:
                continue
            try:
                os.close(fd)
            except OSError:
                pass

    def disparar(self) -> None:
        """Botão de proteção: abre a carga sem comando do desktop."""
        self.rele_fechado = False

    def arrancar_cabo(self) -> None:
        """Fecha o mestre: para o desktop é o cabo USB sendo puxado."""
        self._parar.set()
        try:
            os.close(self._fd)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Laço principal
    # ------------------------------------------------------------------
    def _laco(self) -> None:
        while not self._parar.is_set():
            try:
                prontos, _, _ = select.select([self._fd], [], [], 0.02)
                if prontos:
                    self._receber(os.read(self._fd, 256))
            except OSError:
                # Nenhum leitor do outro lado ainda (ou pty fechada).
                time.sleep(0.02)
                continue
            self._temporizar(time.monotonic())

    def _receber(self, dados: bytes) -> None:
        self._buffer += dados
        while b"\n" in self._buffer:
            linha, self._buffer = self._buffer.split(b"\n", 1)
            try:
                mensagem = protocolo.interpretar(linha)
            except ErroProtocolo:
                continue  # ruído: a placa real também ignora
            self.pacotes_recebidos.append(mensagem)
            self._tratar(mensagem, time.monotonic())

    def _tratar(self, mensagem: protocolo.Mensagem, agora: float) -> None:
        self._ultimo_host = agora

        if self.estado == AGUARDANDO_LINK:
            if mensagem.comando is Comando.HS:
                self._tratar_handshake(mensagem, agora)
            return  # nada além do HS é aceito antes do link

        if self.estado == AGUARDANDO_SYNC:
            if mensagem.comando is Comando.SYNC and self.baud not in self._falhar_sync_em:
                self._enviar(protocolo.ack(Comando.SYNC.value))
                self.estado = TELEMETRIA
                self._proxima_tlm = agora
            return

        if mensagem.comando is Comando.RELE and mensagem.campos in (("0",), ("1",)):
            self.rele_fechado = mensagem.campos[0] == "1"
            self._enviar(protocolo.ack(Comando.RELE.value, mensagem.campos[0]))
        elif mensagem.comando is Comando.BYE:
            self._voltar_a_esperar()

    def _tratar_handshake(self, mensagem: protocolo.Mensagem, agora: float) -> None:
        try:
            baud, intervalo_ms = protocolo.decodificar_handshake(mensagem)
        except ErroProtocolo as erro:
            self._enviar(protocolo.nak(str(erro).replace(",", ";")[:40]))
            return
        # O ACK sai ainda em 9600; só depois a placa troca de velocidade.
        self._enviar(protocolo.ack(Comando.HS.value, baud, intervalo_ms))
        self.baud = baud
        self._intervalo_s = intervalo_ms / 1000
        self.estado = AGUARDANDO_SYNC
        self._prazo_sync = agora + self._tempo_sync_s

    def _temporizar(self, agora: float) -> None:
        if self.estado == AGUARDANDO_SYNC and agora > self._prazo_sync:
            self._voltar_a_esperar()
        elif self.estado == TELEMETRIA:
            if agora - self._ultimo_host > self._watchdog_s:
                self._voltar_a_esperar()
            elif agora >= self._proxima_tlm:
                self._proxima_tlm = agora + self._intervalo_s
                self._enviar_telemetria()

    def _enviar_telemetria(self) -> None:
        leitura = gerar_leitura()
        corrente = leitura.corrente if self.rele_fechado else 0.0
        self._seq += 1
        self._enviar(
            protocolo.telemetria(self._seq, leitura.tensao, corrente, self.rele_fechado)
        )

    def _voltar_a_esperar(self) -> None:
        self.estado = AGUARDANDO_LINK
        self.baud = protocolo.VELOCIDADE_INICIAL

    def _enviar(self, pacote: bytes) -> None:
        try:
            os.write(self._fd, pacote)
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--falhar-sync",
        type=int,
        nargs="*",
        default=[],
        help="velocidades em que o SYNC é ignorado (testa o fallback)",
    )
    args = parser.parse_args()

    placa = PlacaVirtual.criar(falhar_sync_em=tuple(args.falhar_sync)).iniciar()
    print(f"Placa virtual pronta em: {placa.caminho}")
    print("Comandos: d = disparo da proteção · x = arrancar o cabo · q = sair")

    estado_anterior = None
    try:
        while True:
            prontos, _, _ = select.select([sys.stdin], [], [], 0.5)
            if placa.estado != estado_anterior:
                estado_anterior = placa.estado
                print(f"[placa] {placa.estado} @ {placa.baud} bps")
            if not prontos:
                continue
            tecla = sys.stdin.readline().strip().lower()
            if tecla == "d":
                placa.disparar()
                print("[placa] disparo da proteção — relé aberto")
            elif tecla == "x":
                placa.arrancar_cabo()
                print("[placa] cabo arrancado")
                return 0
            elif tecla == "q":
                return 0
    except KeyboardInterrupt:
        return 0
    finally:
        placa.parar()


if __name__ == "__main__":
    sys.exit(main())
