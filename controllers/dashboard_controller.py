"""Controller do Dashboard Principal de Monitoramento.

Media a ``DashboardView`` e o Model:

    - telemetria do ``SerialWorker`` (via barramento) → indicadores e gráfico;
    - regra de proteção → ``models.avaliador_protecao``; a tela só pinta;
    - corte/religamento → ``ServicoCorteEmergencial`` rodando no
      ``DatabaseWorker`` (transação atômica fora da GUI);
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
from models.servico_corte import FalhaCorteEmergencial, ServicoCorteEmergencial
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
        self.leitura_atual = Leitura.zerada()
        self._avaliador = AvaliadorProtecao(setpoint_w=self.view.setpoint())
        self._conectado = False
        self._corte_em_andamento = False
        self._filtro_precarga = FiltroLeituras.ultimas_n(self.LEITURAS_PRECARGA)
        self._filtro_historico: FiltroLeituras | None = None

        self._conectar_view()
        self._conectar_modelo()

        self.view.mostrar_sem_confirmacao(self.estado_disjuntor)
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
        barramento.estado_disjuntor_alterado.connect(self._ao_estado_alterado)
        self._banco.leituras_consultadas.connect(self._ao_leituras_consultadas)
        self._banco.consulta_falhou.connect(self._ao_consulta_falhou)
        self._aplicacao.disparo_espontaneo.connect(self._ao_disparo_espontaneo)
        self._aplicacao.rele_confirmado.connect(self._mostrar_estado_confirmado)
        self._aplicacao.botao_alterado.connect(self._ao_botao_alterado)
        self._aplicacao.falha_persistencia.connect(self._avisar_falha_persistencia)
        self._aplicacao.aviso_comunicacao.connect(self._avisar_comunicacao)

    def _ao_botao_alterado(self, _pressionado: bool) -> None:
        self._atualizar_comandos()

    def _avisar_comunicacao(self, mensagem: str) -> None:
        self.view.avisar("Comunicação serial", mensagem)

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
            self.view.mostrar_sem_confirmacao(self.estado_disjuntor)

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
        livre = self._conectado and not self._corte_em_andamento
        bloqueado = self._aplicacao.botao_pressionado and not self.estado_disjuntor.energizado
        self.view.habilitar_comandos(
            corte=livre and not bloqueado,
            disparo=livre and self.estado_disjuntor.energizado,
            dica=("Libere o botão de proteção para solicitar o religamento." if bloqueado else
                  "" if self._conectado else "Conecte a porta serial para atuar na carga."),
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
        if self._corte_em_andamento:
            return
        if not self.estado_disjuntor.energizado and self._aplicacao.botao_pressionado:
            self.view.avisar("Proteção", "Libere o botão de proteção antes de solicitar o religamento.")
            return
        destino = self.estado_disjuntor.alternado()
        contexto = self._aplicacao.contexto_operacao
        if self.estado_disjuntor.energizado:
            titulo = "Corte de emergência"
            pergunta = (
                "Confirmar o CORTE DE EMERGÊNCIA da carga?\n\n"
                "O comando RELAY_OFF será enviado e a instalação ficará desenergizada."
            )
        else:
            titulo = "Religar carga"
            pergunta = "Confirmar o religamento da carga?\n\nA instalação voltará a ser energizada."

        if not self.view.confirmar(titulo, pergunta):
            return
        if contexto != self._aplicacao.contexto_operacao:
            self.view.avisar("Operação cancelada",
                             "A proteção ou a conexão mudou durante a confirmação.\n"
                             "Confira a carga e solicite a operação novamente.")
            return

        # O COMMIT autoriza enviar !0/!1. O badge e a conclusão da operação
        # aguardam DAT na conexão real; rollback manual continua no serviço.
        self._corte_em_andamento = True
        self._atualizar_comandos()
        self._aplicacao.executar_comando_manual(
            destino,
            self.leitura_atual.resumo(),
            ao_concluir=lambda _evento: self._finalizar_corte(),
            ao_falhar=lambda erro: self._falhar_corte(titulo, erro),
        )

    def _finalizar_corte(self) -> None:
        self._corte_em_andamento = False
        self._atualizar_comandos()

    def _falhar_corte(self, titulo: str, erro: Exception) -> None:
        self._finalizar_corte()
        if isinstance(erro, FalhaCorteEmergencial):
            mensagem = (
                "A operação não foi concluída e foi desfeita (rollback).\n"
                "O estado do disjuntor permanece inalterado.\n\n"
                f"Motivo: {erro}"
            )
        else:
            logger.error("Erro inesperado no corte de emergência: %s", erro)
            mensagem = f"Erro inesperado ao executar o comando.\n\nMotivo: {erro}"
        self.view.avisar_erro(titulo, mensagem)

    def _ao_estado_alterado(self, estado: EstadoDisjuntor) -> None:
        # O sinal do serviço confirma a transação, não a posição do relé.
        if self._aplicacao.serial_ativa:
            return
        self._mostrar_estado_confirmado(estado)

    def _mostrar_estado_confirmado(self, estado: EstadoDisjuntor) -> None:
        if estado is self.estado_disjuntor:
            self.view.mostrar_disjuntor(estado)
            self._atualizar_comandos()
            return
        self.estado_disjuntor = estado
        self.view.mostrar_disjuntor(estado)
        self._atualizar_comandos()

    def _avisar_falha_persistencia(self, erro: str) -> None:
        self.view.avisar_erro(
            "Falha de registro",
            "Não foi possível persistir uma ocorrência ou medição no SQLite.\n"
            "O estado da carga depende da confirmação DAT da placa.\n\n" + erro,
        )

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
            ("A telemetria confirmou o desligamento da carga.\n\n"
             if self._aplicacao.serial_ativa else "Simulação de desligamento, sem confirmação de hardware.\n\n") +
            "Verifique a instalação e libere o botão antes de solicitar o religamento manual.",
        )
