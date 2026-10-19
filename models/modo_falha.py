"""Modo de falha simulada, usado na demonstração do rollback (issue #31).

Quando ativo, o corte de emergência falha de propósito entre o ``UPDATE`` dos
dispositivos e o ``INSERT`` do evento. Assim dá para provar, ao vivo, que a
transação é desfeita e o banco volta ao estado anterior.

Origem e controle
-----------------
- Valor inicial: chave ``simular_falha_corte`` do ``data/config.json``.
- Durante a execução: atalho ``Ctrl+Shift+F`` na janela principal.
- A troca vale só em memória e **não** é gravada no ``config.json``, para o
  modo de teste não continuar ligado por engano na próxima execução.

Cada mudança gera ``[WARNING] Modo de falha simulada ativado.`` ou
``desativado.`` no log e emite o sinal ``alterado(bool)``. A janela principal
usa esse sinal para mostrar o selo "MODO DE TESTE: falha simulada", e o serviço
de corte, para saber se deve falhar.

Uso::

    modo = ModoFalhaSimulada(config.simular_falha_corte)
    modo.alterado.connect(lambda ativo: ...)
    modo.alternar()
"""

from PyQt6.QtCore import QObject, pyqtSignal

from models.log_config import obter_logger

logger = obter_logger()


class ModoFalhaSimulada(QObject):
    """Interruptor em memória do modo de teste."""

    alterado = pyqtSignal(bool)

    def __init__(self, ativo: bool = False, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ativo = ativo
        if ativo:
            logger.warning("Modo de falha simulada ativado pelo config.json.")

    @property
    def ativo(self) -> bool:
        """True enquanto o corte de emergência deve falhar de propósito."""
        return self._ativo

    def definir(self, ativo: bool) -> None:
        """Liga ou desliga o modo; não faz nada se o estado já for o pedido."""
        if ativo == self._ativo:
            return
        self._ativo = ativo
        logger.warning("Modo de falha simulada %s.", "ativado" if ativo else "desativado")
        self.alterado.emit(ativo)

    def alternar(self) -> None:
        """Inverte o estado atual (ação do atalho ``Ctrl+Shift+F``)."""
        self.definir(not self._ativo)
