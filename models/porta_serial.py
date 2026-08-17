"""Serviço simulado de conexão serial.

Na entrega A1/1 não existe I/O com o hardware: esta classe apenas guarda os
parâmetros escolhidos na tela, valida-os e mantém o estado da conexão. A
comunicação real com o microcontrolador entra na Unidade 4, substituindo o
corpo de :meth:`PortaSerial.conectar` e :meth:`PortaSerial.desconectar` — a
assinatura pública foi pensada para não mudar quando isso acontecer.

Nada aqui importa PyQt6: o controller é quem traduz erro em ``QMessageBox`` e
estado em rótulo colorido.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Velocidades oferecidas na tela. As duas exigidas pelo README são 9600 e
#: 115200; as intermediárias entram por serem as usuais em módulos ESP32.
BAUD_RATES = (9600, 19200, 38400, 57600, 115200)

BAUD_RATE_PADRAO = 115200

#: Faixa aceita para o timeout, em milissegundos.
TIMEOUT_MINIMO_MS = 100
TIMEOUT_MAXIMO_MS = 10_000
TIMEOUT_PADRAO_MS = 1_000

#: Portas usadas quando a máquina não tem nenhuma porta física disponível —
#: o caso da máquina de apresentação. Cobrem os dois sistemas operacionais
#: usados pela equipe.
PORTAS_SIMULADAS = (
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "/dev/ttyUSB0",
)


class ErroConexaoSerial(Exception):
    """Parâmetro inválido ou operação incoerente com o estado atual.

    A mensagem é escrita para ser exibida direto ao usuário, sem tradução
    adicional no controller.
    """


@dataclass(frozen=True, slots=True)
class ParametrosConexao:
    """Configuração efetivamente aplicada em uma conexão."""

    porta: str
    baud_rate: int
    timeout_ms: int

    @property
    def descricao(self) -> str:
        """Resumo curto da conexão: ``COM3 @ 115200 bps``."""
        return f"{self.porta} @ {self.baud_rate} bps"

    @property
    def descricao_completa(self) -> str:
        """Resumo com o timeout, usado na coluna Valor Medido do histórico."""
        return f"{self.descricao} · timeout {self.timeout_ms} ms"


class PortaSerial:
    """Porta serial simulada: guarda parâmetros e estado, sem tocar hardware."""

    def __init__(self) -> None:
        self._parametros: ParametrosConexao | None = None

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------
    @property
    def conectado(self) -> bool:
        """True enquanto houver uma conexão aberta."""
        return self._parametros is not None

    @property
    def parametros(self) -> ParametrosConexao | None:
        """Parâmetros da conexão atual, ou None quando desconectado."""
        return self._parametros

    @property
    def descricao_status(self) -> str:
        """Texto legível do estado atual, pronto para rótulo e barra de status."""
        if self._parametros is None:
            return "Nenhuma porta aberta"
        return self._parametros.descricao

    # ------------------------------------------------------------------
    # Operações
    # ------------------------------------------------------------------
    def conectar(
        self, porta: str, baud_rate: int, timeout_ms: int
    ) -> ParametrosConexao:
        """Valida os parâmetros e marca a porta como conectada.

        Levanta :class:`ErroConexaoSerial` se já houver conexão aberta ou se
        algum parâmetro for inválido — é isso que o controller transforma em
        ``QMessageBox.warning``.
        """
        if self.conectado:
            raise ErroConexaoSerial(
                "Já existe uma conexão aberta. Desconecte antes de abrir outra."
            )

        parametros = ParametrosConexao(
            porta=self._validar_porta(porta),
            baud_rate=self._validar_baud_rate(baud_rate),
            timeout_ms=self._validar_timeout(timeout_ms),
        )
        self._parametros = parametros
        return parametros

    def desconectar(self) -> ParametrosConexao:
        """Encerra a conexão e devolve os parâmetros que estavam em uso."""
        if self._parametros is None:
            raise ErroConexaoSerial("Não há conexão aberta para encerrar.")

        parametros = self._parametros
        self._parametros = None
        return parametros

    # ------------------------------------------------------------------
    # Descoberta de portas
    # ------------------------------------------------------------------
    @staticmethod
    def listar_portas() -> list[str]:
        """Lista as portas oferecidas na tela.

        Tenta enumerar as portas reais da máquina; se não houver nenhuma — ou
        se o pyserial não estiver disponível — cai para :data:`PORTAS_SIMULADAS`.
        A tela nunca pode abrir com o combo vazio.
        """
        return PortaSerial._listar_portas_reais() or list(PORTAS_SIMULADAS)

    @staticmethod
    def _listar_portas_reais() -> list[str]:
        try:
            from serial.tools import list_ports
        except ImportError:
            return []

        try:
            return sorted(porta.device for porta in list_ports.comports())
        except Exception:
            # Enumerar portas depende do SO e pode falhar por permissão ou
            # driver ausente. Nesse caso a tela segue com as simuladas.
            return []

    # ------------------------------------------------------------------
    # Validações
    # ------------------------------------------------------------------
    @staticmethod
    def _validar_porta(porta: str) -> str:
        porta = (porta or "").strip()
        if not porta:
            raise ErroConexaoSerial(
                "Selecione uma porta COM antes de conectar."
            )
        return porta

    @staticmethod
    def _validar_baud_rate(baud_rate: int) -> int:
        if baud_rate not in BAUD_RATES:
            disponiveis = ", ".join(str(valor) for valor in BAUD_RATES)
            raise ErroConexaoSerial(
                f"Baud rate inválido: {baud_rate}. Use um destes: {disponiveis}."
            )
        return baud_rate

    @staticmethod
    def _validar_timeout(timeout_ms: int) -> int:
        if not TIMEOUT_MINIMO_MS <= timeout_ms <= TIMEOUT_MAXIMO_MS:
            raise ErroConexaoSerial(
                f"Timeout inválido: {timeout_ms} ms. "
                f"Informe um valor entre {TIMEOUT_MINIMO_MS} e "
                f"{TIMEOUT_MAXIMO_MS} ms."
            )
        return timeout_ms
