"""Corte de emergência (e religamento) como operação atômica.

Dentro de UMA transação SQLite:

    1. ``UPDATE`` do status do disjuntor (DISJ-01) e do relé (RELE-01);
    2. ponto de injeção de falha (modo de teste da issue #31);
    3. ``INSERT`` do evento de comando no histórico.

Se qualquer passo falhar, ``Database.transacao()`` faz ROLLBACK: os
dispositivos voltam ao estado anterior e nenhum evento é gravado. Só depois do
COMMIT o serviço avisa o restante da aplicação pelo barramento — a UI nunca
mostra um estado que não está no banco.

Uso (no ``main.py``):

    servico = ServicoCorteEmergencial(repo_dispositivos, repo_eventos)
    servico.executar(EstadoDisjuntor.ABERTO, leitura.resumo())

Modo de falha simulada (#31): ``servico.simular_falha = True``.
"""

import logging

from database.connection import Database
from models.barramento import barramento
from models.estado_disjuntor import EstadoDisjuntor
from models.evento import Evento, TipoEvento

logger = logging.getLogger("smartgrid")


class FalhaCorteEmergencial(Exception):
    """A operação foi desfeita por rollback; o estado anterior foi preservado."""


class ServicoCorteEmergencial:
    """Abre ou fecha disjuntor e relé registrando o comando na mesma transação."""

    TAG_DISJUNTOR = "DISJ-01"
    TAG_RELE = "RELE-01"

    _DESCRICOES = {
        EstadoDisjuntor.ABERTO: "Corte de emergência acionado via software (RELAY_OFF)",
        EstadoDisjuntor.FECHADO: "Religamento da carga acionado via software (RELAY_ON)",
    }

    def __init__(self, repo_dispositivos, repo_eventos, simular_falha: bool = False) -> None:
        self._repo_dispositivos = repo_dispositivos
        self._repo_eventos = repo_eventos
        #: Gancho da issue #31: quando True, a operação falha entre o UPDATE e o INSERT.
        self.simular_falha = simular_falha

    def estado_atual(self) -> EstadoDisjuntor:
        """Estado do disjuntor gravado no banco (usado ao abrir o dashboard)."""
        linha = self._repo_dispositivos.obter_por_tag(self.TAG_DISJUNTOR)
        return EstadoDisjuntor[linha["status"]]

    def executar(self, destino: EstadoDisjuntor, valor_medido: str = "") -> Evento:
        """Leva disjuntor e relé ao estado ``destino`` de forma atômica.

        Levanta ``FalhaCorteEmergencial`` se a transação for desfeita.
        """
        status = destino.name  # 'ABERTO' / 'FECHADO', como no CHECK da tabela
        evento = Evento.agora(TipoEvento.COMANDO, self._DESCRICOES[destino], valor_medido)

        try:
            with Database.transacao() as conn:
                self._repo_dispositivos.atualizar_status(self.TAG_DISJUNTOR, status, conn)
                self._repo_dispositivos.atualizar_status(self.TAG_RELE, status, conn)
                self._ponto_de_falha()
                self._repo_eventos.adicionar(evento, conn)
        except Exception as erro:
            logger.error(
                "Falha na operação de corte emergencial. Rollback executado: %s.", erro
            )
            raise FalhaCorteEmergencial(str(erro)) from erro

        logger.info("%s — disjuntor e relé em %s.", evento.descricao, status)
        barramento.evento_persistido.emit(evento)
        barramento.estado_disjuntor_alterado.emit(destino)
        return evento

    def _ponto_de_falha(self) -> None:
        if self.simular_falha:
            raise RuntimeError("Falha simulada entre corte e histórico")
