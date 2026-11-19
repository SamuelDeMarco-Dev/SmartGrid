"""Media o histórico e o DatabaseWorker compartilhado, sem I/O na GUI."""

from __future__ import annotations

import logging
import weakref
from bisect import bisect_right
from datetime import date, datetime

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer

from models.barramento import barramento
from models.database_worker import DatabaseWorker
from models.evento import Evento, TipoEvento
from models.historico import ConsultaHistorico, EventoHistorico, FiltroHistorico
from models.repositorio_eventos import RepositorioEventos
from views.historico_view import HistoricoView

logger = logging.getLogger("smartgrid")


class HistoricoController(QObject):
    """Coordena filtros, snapshots e incrementos confirmados no SQLite."""

    INTERVALO_ATUALIZACAO_MS = 150

    def __init__(
        self,
        view: HistoricoView,
        banco: DatabaseWorker,
        repositorio: RepositorioEventos | None = None,
    ) -> None:
        super().__init__()
        self.view = view
        self._banco = banco
        self._repositorio = repositorio or RepositorioEventos()
        self._filtro = FiltroHistorico()
        self._revisao = 0
        self._tarefas: set[int] = set()
        self._ids: set[int] = set()
        self._ordem: list[tuple[datetime, int]] = []  # crescente; a tabela é decrescente
        self._ultimo_id = 0
        self._carregando = False
        self._limpando = False
        self._delta_em_andamento = False
        self._consulta_valida = False
        self._atualizacao_pendente = False
        self._encerrado = False
        self._mensagem_limpeza = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.INTERVALO_ATUALIZACAO_MS)
        self._timer.timeout.connect(self._consultar_novos)

        self.view.filtros_alterados.connect(self.aplicar_filtros)
        self.view.atualizar_solicitado.connect(self.atualizar)
        self.view.limpar_solicitado.connect(self.limpar_historico)
        self.view.tabela_atualizada.connect(self._ao_tabela_preenchida)
        self.view.encerrada.connect(self.encerrar)
        self.view.destroyed.connect(self.encerrar)
        barramento.evento_persistido.connect(self.atualizar_apos_evento_persistido)

        # Também cobre deleteLater() direto no controller, sem fechar a View.
        tarefas = self._tarefas

        def descartar() -> None:
            if not sip.isdeleted(banco):
                for id_tarefa in tuple(tarefas):
                    banco.descartar_callbacks(id_tarefa)
            tarefas.clear()

        self.destroyed.connect(descartar)
        self.aplicar_filtros()

    def atualizar(self) -> None:
        if not self._carregando and not self._limpando:
            self.aplicar_filtros()

    def aplicar_filtros(self) -> None:
        if self._encerrado:
            return
        texto, data_inicio, dispositivo_id = self.view.filtros_selecionados()
        if data_inicio is None or data_inicio > date.today():
            if not self._limpando:
                self._invalidar_consultas()
                self._carregando = False
                self.view.mostrar_erro("Informe uma data inicial válida, até a data atual.")
            return

        tipo = next((tipo for tipo in TipoEvento if tipo.value == texto), None)
        self._filtro = FiltroHistorico(tipo, data_inicio, dispositivo_id)
        if self._limpando:
            return  # a conclusão da limpeza consultará o filtro mais recente

        self._invalidar_consultas()
        self._carregando = True
        self._atualizacao_pendente = False
        self.view.mostrar_carregando("Carregando eventos…")
        self._enfileirar(
            self._repositorio.consultar_historico,
            "_receber_consulta", "_falhar_consulta", self._filtro,
        )

    def _receber_consulta(self, consulta: ConsultaHistorico) -> None:
        self._ultimo_id = consulta.ultimo_id
        self._ids = {registro.id for registro in consulta.registros}
        self._ordem = [
            (registro.evento.data_hora, registro.id) for registro in reversed(consulta.registros)
        ]
        self._consulta_valida = True
        self.view.mostrar_dispositivos(consulta.dispositivos)
        self.view.mostrar_eventos(consulta.registros)

    def atualizar_apos_evento_persistido(self, _evento: Evento) -> None:
        """Agrupa sinais; Evento não possui ID nem dispositivo para montar a linha.

        A consulta incremental traz somente os IDs novos e o vínculo real do
        banco. Nem a descrição nem o timestamp são usados como identidade.
        """
        if not self._encerrado:
            self._atualizacao_pendente = True
            self._agendar_atualizacao()

    def _agendar_atualizacao(self) -> None:
        if (
            self._atualizacao_pendente and self._consulta_valida
            and not self._carregando and not self._limpando
            and not self._delta_em_andamento and not self._timer.isActive()
            and not self._encerrado
        ):
            self._timer.start()

    def _consultar_novos(self) -> None:
        self._atualizacao_pendente = False
        self._delta_em_andamento = True
        self.view.mostrar_carregando("Atualizando eventos…")
        self._enfileirar(
            self._repositorio.consultar_novos,
            "_receber_novos", "_falhar_incremento", self._ultimo_id,
        )

    def _receber_novos(self, registros: list[EventoHistorico]) -> None:
        insercoes: list[tuple[int, EventoHistorico]] = []
        for registro in registros:
            self._ultimo_id = max(self._ultimo_id, registro.id)
            if registro.id in self._ids or not self._filtro.aceita(registro):
                continue
            chave = (registro.evento.data_hora, registro.id)
            posicao = bisect_right(self._ordem, chave)
            insercoes.append((len(self._ordem) - posicao, registro))
            self._ordem.insert(posicao, chave)
            self._ids.add(registro.id)
        if insercoes:
            self.view.inserir_eventos(insercoes)
        else:
            self._ao_tabela_preenchida()

    def _ao_tabela_preenchida(self) -> None:
        if self._encerrado:
            return
        self._carregando = False
        self._delta_em_andamento = False
        mensagem = f"{len(self._ids)} eventos"
        if self._mensagem_limpeza:
            mensagem = f"{self._mensagem_limpeza} · {mensagem}"
            self._mensagem_limpeza = ""
        self.view.mostrar_mensagem(mensagem)
        self._agendar_atualizacao()

    def limpar_historico(self) -> None:
        """A View só publica limpar_solicitado após a confirmação visual."""
        if self._encerrado or self._limpando or self._carregando or self._delta_em_andamento:
            return
        self._invalidar_consultas()
        self._limpando = True
        self.view.mostrar_carregando("Limpando histórico…")
        self._enfileirar(self._repositorio.limpar, "_concluir_limpeza", "_falhar_limpeza")

    def _concluir_limpeza(self, quantidade: int) -> None:
        self._limpando = False
        self._ids.clear()
        self._ordem.clear()
        self._ultimo_id = 0
        self.view.esvaziar_tabela()
        logger.info("Histórico limpo (%d registros)", quantidade)
        self._mensagem_limpeza = f"Histórico limpo: {quantidade} registros removidos"
        # Inclui eventos gravados depois do DELETE, sem ressuscitar linhas antigas.
        self.aplicar_filtros()

    def _falhar_consulta(self, erro: Exception) -> None:
        self._carregando = False
        self._mostrar_falha("carregar o histórico", erro)

    def _falhar_incremento(self, erro: Exception) -> None:
        self._delta_em_andamento = False
        self._mostrar_falha("atualizar o histórico", erro)

    def _falhar_limpeza(self, erro: Exception) -> None:
        self._limpando = False
        self._mostrar_falha("limpar o histórico", erro)

    def _mostrar_falha(self, acao: str, erro: Exception) -> None:
        logger.error("Não foi possível %s: %s", acao, erro)
        self.view.mostrar_erro(f"Não foi possível {acao}. Tente novamente em Atualizar.")

    def _invalidar_consultas(self) -> None:
        self._revisao += 1
        self._timer.stop()
        self._consulta_valida = False
        self._delta_em_andamento = False
        self.view.cancelar_preenchimento()
        for id_tarefa in tuple(self._tarefas):
            self._banco.descartar_callbacks(id_tarefa)
        self._tarefas.clear()

    def _enfileirar(self, funcao, concluir: str, falhar: str, *args) -> None:
        referencia = weakref.ref(self)
        revisao = self._revisao
        identificador: list[int] = []

        def entregar(metodo: str, resultado) -> None:
            controller = referencia()
            if controller is None or sip.isdeleted(controller):
                return
            controller._tarefas.discard(identificador[0])
            if not controller._encerrado and controller._revisao == revisao:
                getattr(controller, metodo)(resultado)

        id_tarefa = self._banco.executar(
            funcao, *args,
            ao_concluir=lambda resultado: entregar(concluir, resultado),
            ao_falhar=lambda erro: entregar(falhar, erro),
        )
        identificador.append(id_tarefa)
        self._tarefas.add(id_tarefa)

    def encerrar(self) -> None:
        """Solta callbacks, timer e barramento sem esperar nenhuma operação de I/O."""
        if self._encerrado:
            return
        self._encerrado = True
        self._timer.stop()
        for id_tarefa in tuple(self._tarefas):
            self._banco.descartar_callbacks(id_tarefa)
        self._tarefas.clear()
        barramento.evento_persistido.disconnect(self.atualizar_apos_evento_persistido)
