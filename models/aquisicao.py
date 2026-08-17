"""Serviço de aquisição de telemetria — o elo com o microcontrolador.

Enquanto a porta serial estiver aberta, publica uma amostra por segundo em
``barramento.leitura_recebida``. Quando a porta fecha, o fluxo para: nenhuma
tela precisa saber disso, elas apenas deixam de receber leituras.

Nesta entrega as amostras são sintetizadas por `models.simulador_telemetria`.
Na Unidade 4 basta trocar o corpo de :meth:`_proxima_leitura` pela leitura real
do pyserial — a assinatura publicada no barramento não muda, e por isso nem o
Dashboard nem o Histórico precisarão ser alterados.

Como `models.barramento`, este módulo usa apenas QtCore: nada de QtWidgets.
"""

from datetime import datetime

from PyQt6.QtCore import QObject, QTimer

from models.barramento import barramento
from models.estado_disjuntor import EstadoDisjuntor
from models.leitura import Leitura
from models.simulador_telemetria import proxima_leitura


class ServicoAquisicao(QObject):
    """Produz leituras enquanto houver conexão serial aberta."""

    #: Período de amostragem, em milissegundos.
    INTERVALO_PADRAO_MS = 1000

    def __init__(
        self, intervalo_ms: int = INTERVALO_PADRAO_MS, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)

        self._estado_disjuntor = EstadoDisjuntor.FECHADO

        self._temporizador = QTimer(self)
        self._temporizador.setInterval(intervalo_ms)
        self._temporizador.timeout.connect(self._amostrar)

        barramento.status_conexao_alterado.connect(self._ao_status_conexao)
        barramento.estado_disjuntor_alterado.connect(self._ao_estado_disjuntor)

    @property
    def ativo(self) -> bool:
        """True enquanto estiver publicando leituras."""
        return self._temporizador.isActive()

    def iniciar(self) -> None:
        """Começa a publicar amostras no barramento."""
        if not self._temporizador.isActive():
            self._temporizador.start()

    def parar(self) -> None:
        """Interrompe a publicação de amostras."""
        self._temporizador.stop()

    # ------------------------------------------------------------------
    # Reação ao estado do sistema
    # ------------------------------------------------------------------
    def _ao_status_conexao(self, conectado: bool, _descricao: str) -> None:
        """A aquisição segue a porta serial: sem conexão, sem telemetria."""
        if conectado:
            self.iniciar()
        else:
            self.parar()

    def _ao_estado_disjuntor(self, estado: EstadoDisjuntor) -> None:
        self._estado_disjuntor = estado

    # ------------------------------------------------------------------
    # Amostragem
    # ------------------------------------------------------------------
    def _amostrar(self) -> None:
        barramento.leitura_recebida.emit(self._proxima_leitura())

    def _proxima_leitura(self) -> Leitura:
        """Amostra do instante atual.

        Com o disjuntor aberto a instalação está desenergizada, então os
        sensores não medem nem tensão nem corrente.
        """
        if not self._estado_disjuntor.energizado:
            return Leitura(instante=datetime.now(), tensao=0.0, corrente=0.0)
        return proxima_leitura()
