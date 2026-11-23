"""Simulador do firmware obrigatório para testes e demonstração POSIX.

Usa !S/!0/!1/!P e DAT bruto; botão apenas informa D11. Não possui watchdog.
PTY não modela baud elétrico, USB, DTR ou limites de transmissão do UNO.
"""
from __future__ import annotations

import argparse
import os
import select
import sys
import threading
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import protocolo
from models.protocolo import Comando, ErroProtocolo

AGUARDANDO_LINK = "AGUARDANDO_LINK"
TELEMETRIA = "TELEMETRIA"


class PlacaVirtual:
    def __init__(self, fd_mestre: int, caminho: str, *,
                 falhar_inicio_em: tuple[int, ...] = (), perder_ack: bool = False,
                 boot_s: float = 0.0, rele_fechado: bool = False,
                 botao_pressionado: bool = False) -> None:
        self.caminho = caminho
        self._fd = fd_mestre
        self._falhar_inicio_em = set(falhar_inicio_em)
        self._perder_ack = perder_ack
        self._pronta_em = time.monotonic() + boot_s
        self.estado = AGUARDANDO_LINK
        self.baud = 9600
        self.rele_fechado = rele_fechado  # setup real inicia aberto
        self.botao_pressionado = botao_pressionado
        self.a0, self.a1 = 900, 256
        self.ignorar_rele = False
        self.pacotes_recebidos: list[protocolo.Mensagem] = []
        self._proxima_dat = 0.0
        self._buffer = b""
        self._parar = threading.Event()
        self._thread = threading.Thread(target=self._laco, daemon=True)

    @classmethod
    def criar(cls, **opcoes) -> "PlacaVirtual":
        if os.name != "posix":
            raise OSError("Pseudoterminal requer Linux/macOS (POSIX).")
        import tty
        mestre, escravo = os.openpty()
        tty.setraw(escravo)
        placa = cls(mestre, os.ttyname(escravo), **opcoes)
        placa._fd_escravo = escravo
        return placa

    def iniciar(self) -> "PlacaVirtual":
        self._thread.start()
        return self

    def parar(self) -> None:
        self._parar.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)
        for fd in (self._fd, getattr(self, "_fd_escravo", None)):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def disparar(self) -> None:
        """Pressiona o botão; somente o desktop envia o corte !0."""
        self.botao_pressionado = True

    def liberar_botao(self) -> None:
        self.botao_pressionado = False

    def arrancar_cabo(self) -> None:
        self._parar.set()
        try:
            os.close(self._fd)
        except OSError:
            pass

    def _laco(self) -> None:
        while not self._parar.is_set():
            try:
                prontos, _, _ = select.select([self._fd], [], [], 0.005)
                if prontos:
                    self._receber(os.read(self._fd, 256))
            except OSError:
                time.sleep(0.02)
                continue
            self._temporizar(time.monotonic())

    def _receber(self, dados: bytes) -> None:
        self._buffer += dados
        while b"\n" in self._buffer:
            linha, self._buffer = self._buffer.split(b"\n", 1)
            # O '!' reinicia a captura no firmware, inclusive após ruído.
            inicio = linha.rfind(b"!")
            if inicio < 0 or time.monotonic() < self._pronta_em:
                continue
            try:
                mensagem = protocolo.interpretar(linha[inicio:])
            except ErroProtocolo:
                continue
            self.pacotes_recebidos.append(mensagem)
            self._tratar(mensagem, time.monotonic())

    def _tratar(self, mensagem: protocolo.Mensagem, agora: float) -> None:
        if mensagem.comando is Comando.INICIAR:
            baud = int(mensagem.campos[0])
            if baud in self._falhar_inicio_em:
                self._enviar(b">>> ERR: BAUD INDISPONIVEL NO ENSAIO <<<\r\n")
                return
            if not self._perder_ack:
                self._enviar(f">>> ACK: INICIANDO EM {baud} BPS <<<\r\n".encode())
            self._perder_ack = False
            # Reproduz a limitação real de !S9600: só !P força retorno a 9600.
            if baud != 9600:
                self.baud = baud
            self.estado = TELEMETRIA
            self._proxima_dat = agora + 0.005
        elif mensagem.comando is Comando.PAUSAR:
            self._enviar(b">>> ACK: CONVERSAO PAUSADA. RETORNANDO A 9600 BPS <<<\r\n")
            self.estado = AGUARDANDO_LINK
            self.baud = 9600
        elif mensagem.comando is Comando.RELE and not self.ignorar_rele:
            self.rele_fechado = mensagem.campos[0] == "1"
            nome = "LIGADO" if self.rele_fechado else "DESLIGADO"
            self._enviar(f">>> ACK: RELE {nome} <<<\r\n".encode())

    def _temporizar(self, agora: float) -> None:
        if self.estado != TELEMETRIA:
            return
        # Em memória reproduz a cadência física, com lote limitado por tick.
        for _ in range(40):
            if agora < self._proxima_dat:
                break
            self._proxima_dat += protocolo.PERIODO_AQUISICAO_S
            self._enviar(protocolo.telemetria(self.a0, self.a1, self.rele_fechado,
                                              self.botao_pressionado))

    def _enviar(self, pacote: bytes) -> None:
        try:
            os.write(self._fd, pacote)
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--falhar-inicio", type=int, nargs="*", default=[])
    args = parser.parse_args()
    placa = PlacaVirtual.criar(falhar_inicio_em=tuple(args.falhar_inicio)).iniciar()
    print(f"Placa virtual pronta em: {placa.caminho}")
    print("d = pressionar D11; l = liberar D11; x = arrancar cabo; q = sair")
    try:
        while True:
            prontos, _, _ = select.select([sys.stdin], [], [], 0.5)
            if not prontos:
                continue
            tecla = sys.stdin.readline().strip().lower()
            if tecla == "d":
                placa.disparar()
            elif tecla == "l":
                placa.liberar_botao()
            elif tecla == "x":
                placa.arrancar_cabo()
                return 0
            elif tecla == "q":
                return 0
    except KeyboardInterrupt:
        return 0
    finally:
        placa.parar()


if __name__ == "__main__":
    sys.exit(main())
