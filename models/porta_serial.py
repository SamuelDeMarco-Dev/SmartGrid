"""Valida parâmetros e enumera portas; o I/O de conexão cabe ao SerialWorker.

Não importa Qt: o controller traduz os erros em mensagens da interface.
O campo taxa_amostragem_ms é a janela de agregação no desktop.
"""

from __future__ import annotations

from dataclasses import dataclass

from models.protocolo import INTERVALO_MAXIMO_MS, INTERVALO_MINIMO_MS, VELOCIDADES

#: Velocidades oferecidas na tela — as quatro que a placa negocia. A conexão
#: sempre começa em 9600 bps; o valor escolhido é a velocidade ALVO do handshake.
BAUD_RATES = VELOCIDADES

#: Faixa do intervalo de agregação no desktop; Timer1 fixo em 200 Hz.
TAXA_AMOSTRAGEM_MINIMA_MS = INTERVALO_MINIMO_MS
TAXA_AMOSTRAGEM_MAXIMA_MS = INTERVALO_MAXIMO_MS

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
    """Guarda parâmetros/estado solicitados; o worker mantém a conexão real."""

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
    def validar_taxa_amostragem(taxa_ms: int) -> int:
        """Confere o período da agregação no desktop; Timer1 fixo em 200 Hz."""
        if not TAXA_AMOSTRAGEM_MINIMA_MS <= taxa_ms <= TAXA_AMOSTRAGEM_MAXIMA_MS:
            raise ErroConexaoSerial(
                f"Intervalo de agregação inválido: {taxa_ms} ms. "
                f"Informe um valor entre {TAXA_AMOSTRAGEM_MINIMA_MS} e "
                f"{TAXA_AMOSTRAGEM_MAXIMA_MS} ms."
            )
        return taxa_ms

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
