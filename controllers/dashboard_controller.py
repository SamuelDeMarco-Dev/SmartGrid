"""Controller do Dashboard Principal de Monitoramento.

Media a ``DashboardView`` e o Model:

    - telemetria do ``SerialWorker`` (via barramento) → indicadores e gráfico;
    - regra de proteção → ``models.avaliador_protecao``; a tela só pinta;
    - corte/religamento → ``AplicacaoController`` (persistência e confirmação);
    - gráfico em dois modos: **tempo real** (cada amostra acrescenta um ponto)
      e **histórico** (filtro → ``DatabaseWorker.consultar_leituras`` →
      replotagem quando o sinal ``leituras_consultadas`` chega).

Sem conexão não chegam amostras: os indicadores congelam e os comandos ficam
indisponíveis, como aconteceria com o microcontrolador desligado.
"""

from __future__ import annotations

import logging
from datetime import datetime

from PyQt6.QtCore import QObject

from controllers.aplicacao_controller import AplicacaoController
from controllers.limites_dialog_controller import LimitesDialogController
from models.avaliador_protecao import AvaliadorProtecao, NivelProtecao, setpoint_das_regras
from models.barramento import barramento
from models.estado_disjuntor import EstadoDisjuntor
from models.evento import TipoEvento
from models.leitura import Leitura
from models.regra_alerta import RegraAlerta
from models.repositorio_leituras import FiltroLeituras
from models.repositorio_regras import RepositorioRegras
from models.servico_corte import ServicoCorteEmergencial
from models.situacao_disjuntor import SituacaoDisjuntor
from views import dashboard_view as cores
from views.dashboard_view import DashboardView
from views.limites_dialog_view import LimitesDialogView

logger = logging.getLogger("smartgrid")

_COR_DO_NIVEL = {
    NivelProtecao.NORMAL: cores.COR_NORMAL,
    NivelProtecao.ATENCAO: cores.COR_ATENCAO,
    NivelProtecao.CRITICO: cores.COR_CRITICA,
    NivelProtecao.INATIVO: cores.COR_INATIVA,
}


class DashboardController(QObject):
    """Tela central de supervisão da instalação."""

    #: Leituras do banco usadas para não abrir o gráfico de tempo real vazio.
    LEITURAS_PRECARGA = 500

    def __init__(
        self,
        servico_corte: ServicoCorteEmergencial,
        repositorio_regras: RepositorioRegras,
        aplicacao: AplicacaoController,
        view: DashboardView | None = None,
    ) -> None:
        super().__init__()
        self.view = view or DashboardView()
        self._servico_corte = servico_corte
        self._repositorio_regras = repositorio_regras
        self._aplicacao = aplicacao
        self._banco = aplicacao.banco

        self.estado_disjuntor = aplicacao.estado_disjuntor
        self._situacao_disjuntor = aplicacao.situacao_disjuntor
        self.leitura_atual = Leitura.zerada()
        self._avaliador = AvaliadorProtecao(setpoint_w=self.view.setpoint())
        self._conectado = self.estado_disjuntor is not None
        self._confirmando = False
        self._filtro_precarga = FiltroLeituras.ultimas_n(self.LEITURAS_PRECARGA)
        self._filtro_historico: FiltroLeituras | None = None

        self._conectar_view()
        self._conectar_modelo()

        self.view.mostrar_situacao_disjuntor(self._situacao_disjuntor)
        self.view.mostrar_leitura(0.0, 0.0, 0.0)
        self._atualizar_aquisicao("")
        self._atualizar_resumo_regras()

        # Nada de SQLite na GUI: regras e pré-carga do gráfico vêm do worker.
        self._banco.executar(
            self._repositorio_regras.obter_regras,
            ao_concluir=self._aplicar_regras,
            ao_falhar=lambda erro: logger.error("Não foi possível carregar as regras: %s", erro),
        )
        self._banco.consultar_leituras(self._filtro_precarga)

    # ------------------------------------------------------------------
    # Ligações
    # ------------------------------------------------------------------
    def _conectar_view(self) -> None:
        self.view.corte_solicitado.connect(self.alternar_carga)
        self.view.disparo_simulado_solicitado.connect(self._aplicacao.simular_disparo)
        self.view.limites_solicitados.connect(self.abrir_dialogo_limites)
        self.view.setpoint_alterado.connect(self._ao_setpoint_alterado)
        self.view.modo_tempo_real_solicitado.connect(self._ao_modo_tempo_real)
        self.view.consulta_ultimas_solicitada.connect(self._consultar_ultimas)
        self.view.consulta_intervalo_solicitada.connect(self._consultar_intervalo)

    def _conectar_modelo(self) -> None:
        barramento.leitura_recebida.connect(self._ao_nova_amostra)
        barramento.status_conexao_alterado.connect(self._ao_status_conexao)
        barramento.regras_alteradas.connect(self._aplicar_regras)
        self._aplicacao.situacao_disjuntor_alterada.connect(self._ao_estado_alterado)
        self._aplicacao.operacao_falhou.connect(self._ao_falha_operacao)
        self._banco.leituras_consultadas.connect(self._ao_leituras_consultadas)
        self._banco.consulta_falhou.connect(self._ao_consulta_falhou)
        self._aplicacao.disparo_espontaneo.connect(self._ao_disparo_espontaneo)
        self._aplicacao.disparo_simulado.connect(self._ao_disparo_simulado)
        self._aplicacao.botao_alterado.connect(self._ao_botao_alterado)
        self._aplicacao.falha_persistencia.connect(self._avisar_falha_persistencia)
        self._aplicacao.aviso_comunicacao.connect(self._avisar_comunicacao)

    # ------------------------------------------------------------------
    # Telemetria e proteção
    # ------------------------------------------------------------------
    def _ao_nova_amostra(self, leitura: Leitura) -> None:
        self.leitura_atual = leitura
        self.view.mostrar_leitura(leitura.tensao, leitura.corrente, leitura.potencia)
        self.view.acrescentar_ponto(leitura.instante.timestamp(), leitura.potencia)
        self._avaliar_protecao(leitura)

    def _avaliar_protecao(self, leitura: Leitura) -> None:
        avaliacao = self._avaliador.avaliar(leitura)
        self.view.mostrar_situacao(_COR_DO_NIVEL[avaliacao.nivel], avaliacao.texto)
        if not avaliacao.nova_violacao:
            return

        for violacao in avaliacao.violacoes:
            unidade = violacao.grandeza.unidade
            logger.warning(
                "Limite de %s violado: %.1f%s (Limite configurado: %.1f%s).",
                violacao.grandeza.rotulo.lower(), violacao.valor, unidade,
                violacao.limite, unidade,
            )
        # Gravado pelo DatabaseWorker, que escuta este pedido.
        barramento.registrar_evento(
            TipoEvento.ALERTA,
            f"Limite de alerta ultrapassado: {avaliacao.violacoes[0].descricao}",
            leitura.resumo() + " | " + "; ".join(
                f"máximo {v.valor:.2f} {v.grandeza.unidade}" for v in avaliacao.violacoes),
        )

    def _ao_status_conexao(self, conectado: bool, descricao: str) -> None:
        self._conectado = conectado
        self._atualizar_aquisicao(descricao)
        if not conectado:
            # Ao reconectar, um consumo ainda acima do limite alerta de novo.
            self._avaliador.reiniciar()
            self.view.mostrar_situacao(cores.COR_INATIVA, NivelProtecao.INATIVO.value)

    def _atualizar_aquisicao(self, descricao: str) -> None:
        if self._conectado:
            self.view.mostrar_aquisicao(cores.COR_NORMAL, f"Telemetria ativa — {descricao}")
        elif descricao.startswith("Link perdido"):
            self.view.mostrar_aquisicao(cores.COR_CRITICA, descricao)
        else:
            self.view.mostrar_aquisicao(
                cores.COR_INATIVA,
                "Aguardando conexão serial — conecte a porta em “Comunicação Serial”",
            )
        self._atualizar_comandos()

    def _atualizar_comandos(self) -> None:
        """Comandos só fazem sentido com o microcontrolador do outro lado."""
        livre = (
            self._conectado and self.estado_disjuntor is not None
            and not self._aplicacao.comando_pendente and not self._confirmando
        )
        dica = ""
        if not self._conectado:
            dica = "Conecte a porta serial para atuar na carga."
        elif self.estado_disjuntor is None:
            dica = "Aguarde telemetria válida da placa."
        elif self._aplicacao.comando_pendente:
            dica = "Aguarde a confirmação do comando pendente."
        bloqueado = self._aplicacao.botao_pressionado and self.estado_disjuntor is EstadoDisjuntor.ABERTO
        if bloqueado:
            dica = "Libere o botão antes de solicitar o religamento."
        self.view.habilitar_comandos(
            corte=livre and not bloqueado,
            disparo=livre and self.estado_disjuntor is EstadoDisjuntor.FECHADO,
            dica=dica,
        )

    # ------------------------------------------------------------------
    # Gráfico: tempo real × histórico
    # ------------------------------------------------------------------
    def _ao_modo_tempo_real(self) -> None:
        self._filtro_historico = None
        self.view.mostrar_tempo_real()

    def _consultar_ultimas(self, quantidade: int) -> None:
        self._consultar(lambda: FiltroLeituras.ultimas_n(quantidade))

    def _consultar_intervalo(self, inicio: datetime, fim: datetime) -> None:
        self._consultar(lambda: FiltroLeituras.intervalo(inicio, fim))

    def _consultar(self, criar_filtro) -> None:
        try:
            filtro = criar_filtro()
        except ValueError as erro:
            logger.warning("Filtro de consulta histórica inválido: %s", erro)
            self.view.avisar("Consulta histórica", str(erro))
            return
        self._filtro_historico = filtro
        self.view.mostrar_consultando()
        self._banco.consultar_leituras(filtro)

    def _ao_leituras_consultadas(self, filtro: FiltroLeituras, leituras: list[Leitura]) -> None:
        instantes = [leitura.instante.timestamp() for leitura in leituras]
        potencias = [leitura.potencia for leitura in leituras]
        if filtro is self._filtro_precarga:
            self.view.plotar_tempo_real(instantes, potencias)
        elif filtro is self._filtro_historico and not self.view.em_tempo_real():
            self.view.plotar_historico(filtro.descricao, instantes, potencias)

    def _ao_consulta_falhou(self, mensagem: str) -> None:
        if self._filtro_historico is not None:
            self.view.mostrar_falha_consulta(mensagem)

    # ------------------------------------------------------------------
    # Setpoint e regras
    # ------------------------------------------------------------------
    def _ao_setpoint_alterado(self, valor: float) -> None:
        self._avaliador.setpoint_w = valor

    def _aplicar_regras(self, regras: list[RegraAlerta]) -> None:
        """Regras vindas do banco ou do diálogo realimentam a proteção."""
        self._avaliador.atualizar_regras(regras)
        setpoint = setpoint_das_regras(regras)
        if setpoint is not None:
            self.view.definir_setpoint(setpoint)
        self._atualizar_resumo_regras()

    def _atualizar_resumo_regras(self) -> None:
        ativas = self._avaliador.regras_ativas
        if not ativas:
            self.view.mostrar_resumo_regras("Nenhuma regra de alerta ativa.")
            return
        itens = " · ".join(
            f"{regra.nome}: {regra.limite_maximo:g} {regra.grandeza.unidade}" for regra in ativas
        )
        self.view.mostrar_resumo_regras(f"Regras ativas — {itens}")

    def abrir_dialogo_limites(self) -> None:
        """Abre a edição. As regras confirmadas chegam por regras_alteradas."""
        dialogo = LimitesDialogView(self.view)
        LimitesDialogController(dialogo, self._repositorio_regras, self._banco)
        dialogo.open()

    # ------------------------------------------------------------------
    # Disjuntor e atuação
    # ------------------------------------------------------------------
    def alternar_carga(self) -> None:
        """Corte de emergência (ou religamento), sempre sob confirmação."""
        if (
            not self._conectado or self.estado_disjuntor is None
            or self._aplicacao.comando_pendente or self._confirmando
        ):
            return
        if self.estado_disjuntor is EstadoDisjuntor.ABERTO and self._aplicacao.botao_pressionado:
            self.view.avisar("Proteção", "Libere o botão antes de solicitar o religamento.")
            return
        destino = self.estado_disjuntor.alternado()
        contexto = self._aplicacao.contexto_operacao
        if self.estado_disjuntor.energizado:
            titulo = "Corte de emergência"
            pergunta = (
                "Confirmar o CORTE DE EMERGÊNCIA da carga?\n\n"
                "O comando !0 será enviado. Aguarde a confirmação pela telemetria."
            )
        else:
            titulo = "Religar carga"
            pergunta = "Confirmar o religamento da carga?\n\nAguarde a confirmação pela telemetria."

        self._confirmando = True
        self._atualizar_comandos()
        try:
            confirmado = self.view.confirmar(titulo, pergunta)
        finally:
            self._confirmando = False
            self._atualizar_comandos()
        if not confirmado:
            return
        if contexto != self._aplicacao.contexto_operacao:
            self.view.avisar("Operação cancelada", "A proteção ou a conexão mudou durante a confirmação.")
            return
        # O diálogo tem seu próprio event loop: D11 ou a conexão podem ter
        # mudado enquanto o operador decidia. Não reaproveita a confirmação.
        if self.estado_disjuntor is None or destino is not self.estado_disjuntor.alternado():
            return
        self._aplicacao.solicitar_estado_disjuntor(destino, self.leitura_atual.resumo())

    def _ao_falha_operacao(self, mensagem: str) -> None:
        self.view.avisar_erro("Atuação sem confirmação", mensagem)

    def _ao_estado_alterado(self, situacao: SituacaoDisjuntor) -> None:
        self._situacao_disjuntor = situacao
        self.estado_disjuntor = situacao.reportado
        self.view.mostrar_situacao_disjuntor(situacao)
        self._atualizar_comandos()

    def _ao_disparo_espontaneo(self, erro_gravacao: str) -> None:
        # O disjuntor já abriu fisicamente: a tela mostra ABERTO mesmo que a
        # gravação falhe, mas o operador é avisado de que o banco ficou para trás.
        if erro_gravacao:
            self.view.avisar_erro(
                "Falha de registro",
                "O disjuntor abriu, mas não foi possível gravar o disparo no banco.\n\n"
                f"Motivo: {erro_gravacao}",
            )
        self.view.avisar(
            "Proteção atuada",
            "A telemetria confirmou o desligamento da carga.\n\nVerifique a instalação antes de religar a carga.",
        )

    def _ao_disparo_simulado(self, erro_gravacao: str) -> None:
        if erro_gravacao:
            self.view.avisar_erro("Falha de registro", f"Falha ao registrar a simulação: {erro_gravacao}")
            return
        mensagem = (
            "A placa confirmou ABERTO pela telemetria. O religamento exige ação manual."
            if self.estado_disjuntor is EstadoDisjuntor.ABERTO else
            "Simulação registrada. O estado da placa é desconhecido sem telemetria válida."
        )
        self.view.avisar("Disparo simulado", mensagem)

    @property
    def _corte_em_andamento(self) -> bool:
        return self._aplicacao.comando_pendente

    def _ao_botao_alterado(self, _pressionado: bool) -> None:
        self._atualizar_comandos()

    def _avisar_falha_persistencia(self, erro: str) -> None:
        self.view.avisar_erro("Falha de registro", "Falha ao persistir no SQLite; estado da carga depende de DAT.\n" + erro)

    def _avisar_comunicacao(self, mensagem: str) -> None:
        self.view.avisar("Comunicação serial", mensagem)
