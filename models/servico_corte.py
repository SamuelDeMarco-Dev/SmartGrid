"""Transações manuais e auditoria da proteção física.

Operação manual: UPDATE dos dois dispositivos, ponto de falha simulada e
INSERT do comando na mesma transação. Erro antes do commit faz rollback e
impede envio. Commit registra solicitação, não comprova posição do relé.
AplicacaoController aguarda DAT para mostrar o estado na conexão real.
O botão físico solicita !0 antes da persistência; seus métodos de auditoria
e sincronização não passam pelo gancho de falha das operações manuais.
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

    def executar(self, destino: EstadoDisjuntor, valor_medido: str = "", *,
                 publicar_estado: bool = True, validar=None) -> Evento:
        """Registra o estado solicitado e o comando de forma atômica no banco.

        Levanta ``FalhaCorteEmergencial`` se a transação for desfeita.
        """
        status = destino.name  # 'ABERTO' / 'FECHADO', como no CHECK da tabela
        evento = Evento.agora(TipoEvento.COMANDO, self._DESCRICOES[destino], valor_medido)

        try:
            with Database.transacao() as conn:
                if validar is not None:
                    validar()
                self._repo_dispositivos.atualizar_status(self.TAG_DISJUNTOR, status, conn)
                self._repo_dispositivos.atualizar_status(self.TAG_RELE, status, conn)
                self._ponto_de_falha()
                self._repo_eventos.adicionar(evento, conn, self.TAG_DISJUNTOR)
                if validar is not None:
                    validar()
        except Exception as erro:
            logger.error(
                "Falha na operação de corte emergencial. Rollback executado: %s.", erro
            )
            raise FalhaCorteEmergencial(str(erro)) from erro

        logger.info("%s — estado solicitado %s persistido; confirmação física depende de DAT.",
                    evento.descricao, status)
        barramento.evento_persistido.emit(evento)
        if publicar_estado:
            barramento.estado_disjuntor_alterado.emit(destino)
        return evento

    def registrar_botao_protecao(self, valor_medido: str = "") -> Evento:
        """Audita a solicitação; não presume que o relé já abriu.

        Executado depois de !0, sem o gancho de falha das operações manuais.
        Falhas reais do SQLite são propagadas para o aviso na GUI.
        """
        evento = Evento.agora(
            TipoEvento.ALERTA,
            "Botão de proteção acionado na placa (Botão físico pressionado) — solicitado !0; aguardando confirmação DAT",
            valor_medido,
        )
        self._repo_eventos.adicionar(evento, dispositivo_tag=self.TAG_DISJUNTOR)
        barramento.evento_persistido.emit(evento)
        return evento

    def sincronizar_estado(self, estado: EstadoDisjuntor) -> None:
        """Reflete somente um estado recebido em DAT, sem enviar comandos."""
        with Database.transacao() as conn:
            for tag in (self.TAG_DISJUNTOR, self.TAG_RELE):
                self._repo_dispositivos.atualizar_status(tag, estado.name, conn)

    def registrar_disparo_espontaneo(self, valor_medido: str = "", *,
                                    simulado: bool = False, inicial: bool = False) -> Evento:
        """Grava a abertura do disjuntor pelo hardware e o alerta correspondente.

        Diferente do corte, aqui o estado físico JÁ mudou: o serviço apenas
        alinha o banco à realidade, atomicamente. Não emite
        ``estado_disjuntor_alterado`` (quem detectou o disparo já o fez).
        Levanta ``sqlite3.Error``/``LookupError`` se a gravação falhar.
        """
        evento = Evento.agora(
            TipoEvento.STATUS if inicial else TipoEvento.ALERTA,
            "Placa iniciou com o relé desligado — religamento exige ação manual" if inicial else
            "Disparo simulado — intenção de abertura registrada sem confirmação serial" if simulado else
            "Disjuntor abriu espontaneamente — estado aberto confirmado em DAT",
            valor_medido,
        )

        with Database.transacao() as conn:
            self._repo_dispositivos.atualizar_status(
                self.TAG_DISJUNTOR, EstadoDisjuntor.ABERTO.name, conn
            )
            self._repo_dispositivos.atualizar_status(
                self.TAG_RELE, EstadoDisjuntor.ABERTO.name, conn
            )
            self._repo_eventos.adicionar(evento, conn, self.TAG_DISJUNTOR)

        logger.warning("Disparo espontâneo do disjuntor %s registrado.", self.TAG_DISJUNTOR)
        barramento.evento_persistido.emit(evento)
        return evento

    def _ponto_de_falha(self) -> None:
        if self.simular_falha:
            raise RuntimeError("Falha simulada entre corte e histórico")
