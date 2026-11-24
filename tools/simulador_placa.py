"""Placa de medição virtual — o Arduino UNO emulado num pseudo-terminal.

Reproduz o firmware do professor (``firmware/smartgrid_placa``, ver
``models/protocolo.py``) para testar e demonstrar o supervisório sem o
hardware. A pty não tem velocidade física: a troca de baud rate é só lógica,
mas as linhas trocadas são idênticas às da placa real.

Uso (macOS/Linux)::

    python tools/simulador_placa.py              # mostra o caminho da porta
    python tools/simulador_placa.py --falhar-em 115200

Copie o caminho exibido (ex.: ``/dev/ttys004``) para o campo de porta da tela
"Comunicação Serial". Comandos no terminal do simulador:

    b  pressiona o botão da placa (D11) por um instante
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
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import protocolo
from models.simulador_telemetria import gerar_leitura

#: Tamanho do ``bufferCmd`` do firmware (15 caracteres + terminador).
TAMANHO_COMANDO = 15

#: Quanto tempo ``pressionar_botao`` mantém o botão em nível alto.
DURACAO_BOTAO_S = 0.2

#: Máximo de amostras atrasadas enviadas de uma vez pelo laço da placa.
RAJADA_MAXIMA = 5
AGUARDANDO_LINK = "AGUARDANDO_LINK"
TELEMETRIA = "TELEMETRIA"


class PlacaVirtual:
    """Lado da placa do protocolo, rodando numa thread sobre o mestre da pty."""

    def __init__(
        self,
        fd_mestre: int,
        caminho: str,
        *,
        falhar_em: tuple[int, ...] = (),
        periodo_amostra_s: float = protocolo.PERIODO_AMOSTRA_S,
        falhar_inicio_em: tuple[int, ...] = (), perder_ack: bool = False,
        boot_s: float = 0.0, rele_fechado: bool = False, botao_pressionado: bool = False,
    ) -> None:
        self.caminho = caminho
        self._fd = fd_mestre
        self._falhar_em = set(falhar_em)
        self._periodo_amostra_s = periodo_amostra_s
        self._falhar_inicio_em = set(falhar_inicio_em)
        self._perder_ack = perder_ack
        self._pronta_em = time.monotonic() + boot_s
        self.pacotes_recebidos = []
        self.ignorar_rele = False
        self.a0 = self.a1 = None

        #: Comandos já processados, sem o ``!`` (ex.: ``"S115200"``, ``"0"``).
        self.comandos_recebidos: list[str] = []
        self._parar = threading.Event()
        self._thread = threading.Thread(target=self._laco, daemon=True)
        self.reiniciar()
        self.rele_fechado = rele_fechado
        self.botao_pressionado = botao_pressionado
        if botao_pressionado:
            self._soltar_botao_em = float("inf")

    @property
    def estado(self) -> str:
        return TELEMETRIA if self.conversao_habilitada else AGUARDANDO_LINK

    @estado.setter
    def estado(self, estado: str) -> None:
        self.conversao_habilitada = estado == TELEMETRIA

    @property
    def _proxima_dat(self):
        return self._proxima_amostra

    @_proxima_dat.setter
    def _proxima_dat(self, valor):
        self._proxima_amostra = valor

    def disparar(self) -> None:
        self.botao_pressionado = True
        self._soltar_botao_em = float("inf")

    def liberar_botao(self) -> None:
        self.botao_pressionado = False
        self._soltar_botao_em = 0.0

    @classmethod
    def criar(cls, **opcoes) -> "PlacaVirtual":
        """Abre um par pty e devolve a placa; o desktop usa ``placa.caminho``."""
        if os.name != "posix":
            raise OSError(
                "O simulador de pseudoterminal requer Linux/macOS (POSIX)."
            )

        # Importado aqui: ``tty`` só existe em macOS/Linux, e os testes usam a
        # máquina de estados (PlacaEmMemoria) também no Windows.
        import tty

        mestre, escravo = os.openpty()
        tty.setraw(escravo)
        caminho = os.ttyname(escravo)
        placa = cls(mestre, caminho, **opcoes)
        # Mantém o lado escravo aberto: sem nenhum leitor, o mestre recebe EIO
        # até o desktop abrir a porta.
        placa._fd_escravo = escravo
        return placa

    def reiniciar(self) -> None:
        """Equivale ao ``setup()``: relé desligado, conversão parada, 9600 bps."""
        self.conversao_habilitada = False
        self.baud = protocolo.VELOCIDADE_INICIAL
        self.rele_fechado = False
        self.botao_pressionado = False
        self._soltar_botao_em = 0.0
        self._proxima_amostra = 0.0
        self._buffer_cmd = ""
        self._gravando_cmd = False
        for texto in protocolo.BANNER:
            self._enviar(protocolo.linha(texto))

    # ------------------------------------------------------------------
    # Controle
    # ------------------------------------------------------------------
    def iniciar(self) -> "PlacaVirtual":
        self._thread.start()
        return self

    def parar(self) -> None:
        self._parar.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)
        for fd in (self._fd, getattr(self, "_fd_escravo", None)):
            if fd is None:
                continue
            try:
                os.close(fd)
            except OSError:
                pass

    def pressionar_botao(self, duracao_s: float = DURACAO_BOTAO_S) -> None:
        """Botão em D11: só aparece no campo ``botao`` das amostras."""
        self.botao_pressionado = True
        self._soltar_botao_em = time.monotonic() + duracao_s

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
                prontos, _, _ = select.select([self._fd], [], [], 0.005)
                if prontos:
                    self._receber(os.read(self._fd, 256))
            except OSError:
                # Nenhum leitor do outro lado ainda (ou pty fechada).
                time.sleep(0.02)
                continue
            self._temporizar(time.monotonic())

    def _receber(self, dados: bytes) -> None:
        """Mesmo parser caractere a caractere do ``loop()`` do firmware."""
        for c in dados.decode("ascii", errors="replace"):
            if c == protocolo.INICIO_COMANDO:
                self._gravando_cmd = True
                self._buffer_cmd = ""
            elif self._gravando_cmd:
                if c in "\r\n":
                    if self._buffer_cmd:
                        self._processar(self._buffer_cmd)
                    self._gravando_cmd = False
                    self._buffer_cmd = ""
                elif len(self._buffer_cmd) < TAMANHO_COMANDO and c != " ":
                    self._buffer_cmd += c

    def _processar(self, cmd: str) -> None:
        if time.monotonic() < self._pronta_em:
            return
        self.comandos_recebidos.append(cmd)
        try:
            mensagem = protocolo.interpretar(protocolo.comando(cmd))
        except protocolo.ErroProtocolo:
            # O parser do host valida bauds; a placa deve responder ERR ao
            # comando inválido, sem abortar seu laço de emulação.
            mensagem = None
        if mensagem is not None:
            self.pacotes_recebidos.append(mensagem)
        if cmd in ("0", "1") and self.ignorar_rele:
            return
        if cmd[0] in "Ss":
            self._iniciar_conversao(cmd[1:])
        elif cmd.upper() in ("P", "STOP"):
            self.conversao_habilitada = False
            self._enviar(protocolo.resposta_pausada())
            self.baud = protocolo.VELOCIDADE_INICIAL
        elif cmd in ("1", "L", "l"):
            self._acionar_rele(True)
            self._enviar(protocolo.resposta_rele(True))
        elif cmd in ("0", "D", "d"):
            self._acionar_rele(False)
            self._enviar(protocolo.resposta_rele(False))
        elif cmd in ("T", "t"):
            self._acionar_rele(not self.rele_fechado)
            self._enviar(protocolo.resposta_rele_alternado())

    def _iniciar_conversao(self, argumento: str) -> None:
        baud = 115200
        if argumento:
            digitos = ""
            for c in argumento:  # strtoul: para no primeiro não dígito
                if not c.isdigit():
                    break
                digitos += c
            baud = int(digitos) if digitos else 0
        if baud not in protocolo.VELOCIDADES:
            self._enviar(protocolo.resposta_baud_invalido())
            return
        if baud in self._falhar_inicio_em:
            self._enviar(protocolo.resposta_baud_invalido())
            return
        # O ACK sai na velocidade atual; só depois a placa troca.
        if not self._perder_ack:
            self._enviar(protocolo.resposta_iniciando(baud))
        self._perder_ack = False
        if baud != protocolo.VELOCIDADE_INICIAL:
            self.baud = baud
        self.conversao_habilitada = True
        self._proxima_amostra = time.monotonic()

    def _acionar_rele(self, ligar: bool) -> None:
        self.rele_fechado = ligar

    def _temporizar(self, agora: float) -> None:
        if self.botao_pressionado and agora >= self._soltar_botao_em:
            self.botao_pressionado = False
        if not self.conversao_habilitada or agora < self._proxima_amostra:
            return
        atrasadas = int((agora - self._proxima_amostra) / self._periodo_amostra_s) + 1
        for _ in range(min(atrasadas, RAJADA_MAXIMA)):
            self._enviar_amostra()
        self._proxima_amostra = agora + self._periodo_amostra_s

    def _enviar_amostra(self) -> None:
        if self.baud in self._falhar_em:
            return  # enlace ruim nesta velocidade: nada chega ao desktop
        leitura = gerar_leitura()
        a0, a1 = protocolo.para_adc(leitura.tensao, leitura.corrente)
        if self.a0 is not None:
            a0 = self.a0
        if self.a1 is not None:
            a1 = self.a1
        self._enviar(protocolo.amostra(a0, a1, self.rele_fechado, self.botao_pressionado))

    def _enviar(self, pacote: bytes) -> None:
        try:
            os.write(self._fd, pacote)
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--falhar-em",
        type=int,
        nargs="*",
        default=[],
        help="velocidades em que as amostras não chegam (testa o fallback)",
    )
    parser.add_argument("--falhar-inicio", type=int, nargs="*", default=[])
    args = parser.parse_args()

    placa = PlacaVirtual.criar(falhar_em=tuple(args.falhar_em), falhar_inicio_em=tuple(args.falhar_inicio)).iniciar()
    print(f"Placa virtual pronta em: {placa.caminho}")
    print("Comandos: b = pulso D11 · d = manter D11 · l = liberar · x = remover cabo · q = sair")

    estado_anterior = None
    try:
        while True:
            prontos, _, _ = select.select([sys.stdin], [], [], 0.5)
            estado = (placa.conversao_habilitada, placa.baud, placa.rele_fechado)
            if estado != estado_anterior:
                estado_anterior = estado
                conversao = "convertendo" if placa.conversao_habilitada else "em espera"
                rele = "ligado" if placa.rele_fechado else "desligado"
                print(f"[placa] {conversao} @ {placa.baud} bps · relé {rele}")
            if not prontos:
                continue
            tecla = sys.stdin.readline().strip().lower()
            if tecla == "d":
                placa.disparar()
            elif tecla == "l":
                placa.liberar_botao()
            elif tecla == "b":
                placa.pressionar_botao()
                print("[placa] botão pressionado")
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
