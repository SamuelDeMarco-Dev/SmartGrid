"""Controller do Dashboard Principal de Monitoramento.

Reúne as quatro responsabilidades da tela central do supervisório:

    - telemetria instantânea (V, I e P) alimentada pelo simulador;
    - estado binário do disjuntor, com badge colorido e registro de disparos;
    - atuação do operador (corte de emergência) e ajuste do setpoint;
    - gráfico de tendência, pré-carregado com as últimas 24 h ao abrir.

A tela não gera telemetria: ela consome `barramento.leitura_recebida`, que é
alimentado por `models.aquisicao` enquanto a porta serial estiver aberta. Sem
conexão não chegam amostras — os indicadores congelam e os comandos ficam
indisponíveis, como aconteceria com o microcontrolador desligado.
"""

from datetime import datetime

import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QSizePolicy, QWidget

from controllers.limites_dialog_controller import LimitesDialogController
from models.barramento import barramento
from models.estado_disjuntor import EstadoDisjuntor
from models.evento import TipoEvento
from models.leitura import Leitura
from models.regra_alerta import Grandeza, RegraAlerta
from models.repositorio_regras import repositorio_regras_compartilhado
from models.simulador_telemetria import gerar_historico_24h
from ui.dashboard_ui import Ui_Dashboard


class DashboardController(QWidget, Ui_Dashboard):
    """Tela central de supervisão da instalação."""

    #: Acima desta fração do setpoint o painel entra em atenção (amarelo).
    _FRACAO_ATENCAO = 0.90

    #: Teto de pontos mantidos no gráfico — evita crescer sem limite.
    _MAX_PONTOS = 3000

    _COR_NORMAL = "#2E7D32"
    _COR_ATENCAO = "#B26A00"
    _COR_CRITICA = "#C62828"
    _COR_INATIVA = "#6B7280"

    def __init__(self) -> None:
        super().__init__()
        self.setupUi(self)

        self.estado_disjuntor = EstadoDisjuntor.FECHADO
        self.leitura_atual = Leitura(
            instante=datetime.now(), tensao=0.0, corrente=0.0
        )
        self._regras: list[RegraAlerta] = repositorio_regras_compartilhado.obter_regras()
        self._em_violacao = False
        self._conectado = False
        #: Marca que a próxima mudança de estado veio do hardware, e não de um
        #: comando do operador — só essas geram aviso e registro automático.
        self._disparo_espontaneo = False

        self._montar_grafico()
        self._precarregar_historico()
        self._conectar_acoes()
        self._conectar_barramento()

        self._aplicar_regras(self._regras)
        self._atualizar_badge_disjuntor()
        self._atualizar_indicadores(self.leitura_atual)
        self._atualizar_estado_aquisicao("")

    # ------------------------------------------------------------------
    # Montagem do gráfico de tendência
    # ------------------------------------------------------------------
    def _montar_grafico(self) -> None:
        """Cria a área de plotagem e a encaixa no container do Qt Designer."""
        self.grafico = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        # O PlotWidget pede ~450 px de altura por conta própria, o que faria a
        # página inteira crescer e forçar rolagem mesmo em telas grandes. Com
        # a política Ignored ele passa a ocupar só o espaço que sobrar, tendo
        # como piso o mínimo definido para o grupo no Qt Designer.
        self.grafico.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored
        )
        self.grafico.setBackground("w")
        self.grafico.showGrid(x=True, y=True, alpha=0.30)
        self.grafico.setLabel("left", "Potência (W)")
        self.grafico.setLabel("bottom", "Horário")
        self.grafico.addLegend(offset=(-10, 10))

        self.curva_consumo = self.grafico.plot(
            [], [], pen=pg.mkPen("#1D4F80", width=2), name="Consumo"
        )
        self.linha_setpoint = pg.InfiniteLine(
            angle=0,
            pen=pg.mkPen(self._COR_CRITICA, width=1, style=Qt.PenStyle.DashLine),
        )
        self.grafico.addItem(self.linha_setpoint)

        self.layoutGrafico.addWidget(self.grafico)

    def _precarregar_historico(self) -> None:
        """Preenche o gráfico com a curva de demanda das últimas 24 horas.

        Exigência da entrega A1/1: a área de plotagem não pode aparecer vazia
        no momento em que a tela abre.
        """
        historico = gerar_historico_24h()
        self._instantes = [leitura.instante.timestamp() for leitura in historico]
        self._potencias = [leitura.potencia for leitura in historico]
        self.curva_consumo.setData(self._instantes, self._potencias)

    # ------------------------------------------------------------------
    # Ligações
    # ------------------------------------------------------------------
    def _conectar_acoes(self) -> None:
        self.botaoCorteEmergencia.clicked.connect(self.alternar_carga)
        self.botaoSimularDisparo.clicked.connect(self.simular_disparo_hardware)
        self.botaoConfigurarLimites.clicked.connect(self.abrir_dialogo_limites)

        # Slider e spin editam o mesmo setpoint — mantê-los espelhados evita
        # que a tela mostre dois valores diferentes para o mesmo limite.
        self.sliderSetpoint.valueChanged.connect(self._ao_slider_alterado)
        self.spinSetpoint.valueChanged.connect(self._ao_spin_alterado)

    def _conectar_barramento(self) -> None:
        barramento.leitura_recebida.connect(self._ao_nova_amostra)
        barramento.status_conexao_alterado.connect(self._ao_status_conexao)
        barramento.regras_alteradas.connect(self._aplicar_regras)
        barramento.estado_disjuntor_alterado.connect(self._ao_estado_alterado)

    # ------------------------------------------------------------------
    # Telemetria
    # ------------------------------------------------------------------
    def _ao_nova_amostra(self, leitura: Leitura) -> None:
        """Recebe uma amostra do serviço de aquisição e atualiza a tela."""
        self.leitura_atual = leitura

        self._atualizar_indicadores(leitura)
        self._acrescentar_ao_grafico(leitura)
        self._avaliar_protecao(leitura)

    def _atualizar_indicadores(self, leitura: Leitura) -> None:
        self.lcdTensao.display(f"{leitura.tensao:.1f}")
        self.lcdCorrente.display(f"{leitura.corrente:.2f}")
        self.lcdPotencia.display(f"{leitura.potencia:.0f}")

    def _acrescentar_ao_grafico(self, leitura: Leitura) -> None:
        self._instantes.append(leitura.instante.timestamp())
        self._potencias.append(leitura.potencia)

        if len(self._instantes) > self._MAX_PONTOS:
            del self._instantes[: len(self._instantes) - self._MAX_PONTOS]
            del self._potencias[: len(self._potencias) - self._MAX_PONTOS]

        self.curva_consumo.setData(self._instantes, self._potencias)

    # ------------------------------------------------------------------
    # Dependência da conexão serial
    # ------------------------------------------------------------------
    def _ao_status_conexao(self, conectado: bool, descricao: str) -> None:
        """Sem porta aberta não há telemetria nem atuação sobre a carga."""
        self._conectado = conectado
        self._atualizar_estado_aquisicao(descricao)

        if not conectado:
            # Zerar a marca faz o alerta ser registrado de novo ao reconectar,
            # caso o consumo ainda esteja acima do limite.
            self._em_violacao = False
            self._pintar_situacao(self._COR_INATIVA, "Telemetria interrompida")

    def _atualizar_estado_aquisicao(self, descricao: str) -> None:
        if self._conectado:
            cor = self._COR_NORMAL
            texto = f"Telemetria ativa — {descricao}"
        else:
            cor = self._COR_INATIVA
            texto = "Aguardando conexão serial — conecte a porta em “Comunicação Serial”"

        self.labelStatusAquisicao.setText(texto)
        self.labelStatusAquisicao.setStyleSheet(
            f"color: #FFFFFF; background-color: {cor}; border-radius: 4px;"
        )
        self._atualizar_disponibilidade_comandos()

    def _atualizar_disponibilidade_comandos(self) -> None:
        """Comandos só fazem sentido com o microcontrolador do outro lado."""
        dica = "" if self._conectado else "Conecte a porta serial para atuar na carga."

        self.botaoCorteEmergencia.setEnabled(self._conectado)
        self.botaoCorteEmergencia.setToolTip(dica)

        self.botaoSimularDisparo.setEnabled(
            self._conectado and self.estado_disjuntor.energizado
        )

    # ------------------------------------------------------------------
    # Lógica local de proteção de software
    # ------------------------------------------------------------------
    def _avaliar_protecao(self, leitura: Leitura) -> None:
        """Compara a amostra com o setpoint e com as regras cadastradas."""
        violacoes = self._violacoes(leitura)

        if violacoes:
            self._pintar_situacao(
                self._COR_CRITICA, f"LIMITE ULTRAPASSADO — {violacoes[0]}"
            )
            # O evento sai apenas na transição normal → violado: com uma
            # amostra por segundo, registrar sempre inundaria o histórico.
            if not self._em_violacao:
                self._em_violacao = True
                barramento.registrar_evento(
                    TipoEvento.ALERTA,
                    f"Limite de alerta ultrapassado: {violacoes[0]}",
                    leitura.resumo(),
                )
            return

        self._em_violacao = False

        if leitura.potencia >= self._setpoint() * self._FRACAO_ATENCAO:
            self._pintar_situacao(self._COR_ATENCAO, "Consumo próximo do limite")
            return

        self._pintar_situacao(self._COR_NORMAL, "Consumo dentro do limite")

    def _violacoes(self, leitura: Leitura) -> list[str]:
        """Descrições legíveis de tudo que está fora do limite nesta amostra."""
        fora = []

        if leitura.potencia > self._setpoint():
            fora.append(f"potência em {leitura.potencia:.0f} W")

        for regra in self._regras:
            valor = self._valor_da_grandeza(leitura, regra.grandeza)
            if regra.violada_por(valor):
                fora.append(
                    f"{regra.nome} — {valor:.2f} {regra.grandeza.unidade}"
                )

        return fora

    @staticmethod
    def _valor_da_grandeza(leitura: Leitura, grandeza: Grandeza) -> float:
        valores = {
            Grandeza.TENSAO: leitura.tensao,
            Grandeza.CORRENTE: leitura.corrente,
            Grandeza.POTENCIA: leitura.potencia,
        }
        return valores[grandeza]

    def _pintar_situacao(self, cor: str, texto: str) -> None:
        """Reflete a situação da proteção na cor do painel de telemetria."""
        self.labelSituacaoProtecao.setText(texto)
        self.labelSituacaoProtecao.setStyleSheet(
            f"color: #FFFFFF; background-color: {cor}; border-radius: 4px;"
        )
        self.framePainelTelemetria.setStyleSheet(
            f"QGroupBox {{ border: 2px solid {cor}; border-radius: 6px; "
            "margin-top: 8px; padding-top: 8px; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; }"
        )

    # ------------------------------------------------------------------
    # Setpoint e regras
    # ------------------------------------------------------------------
    def _setpoint(self) -> float:
        return self.spinSetpoint.value()

    def _ao_slider_alterado(self, valor: int) -> None:
        if self.spinSetpoint.value() != valor:
            self.spinSetpoint.setValue(float(valor))
        self._atualizar_linha_setpoint()

    def _ao_spin_alterado(self, valor: float) -> None:
        if self.sliderSetpoint.value() != int(valor):
            self.sliderSetpoint.setValue(int(valor))
        self._atualizar_linha_setpoint()

    def _atualizar_linha_setpoint(self) -> None:
        self.linha_setpoint.setPos(self._setpoint())

    def _aplicar_regras(self, regras: list[RegraAlerta]) -> None:
        """Recebe as regras vindas do diálogo modal e realimenta a proteção."""
        self._regras = list(regras)
        self._sincronizar_setpoint_com_regras()
        self._atualizar_resumo_regras()

    def _sincronizar_setpoint_com_regras(self) -> None:
        """Faz a regra ativa de potência governar o setpoint do painel.

        As duas formas de configurar o limite precisam concordar: sem isso o
        operador salvaria 2800 W no diálogo e o painel continuaria alertando
        pelo valor antigo do slider.
        """
        for regra in self._regras:
            if regra.grandeza is Grandeza.POTENCIA and regra.ativa:
                limite = min(
                    max(regra.limite_maximo, self.spinSetpoint.minimum()),
                    self.spinSetpoint.maximum(),
                )
                self.spinSetpoint.setValue(limite)
                return

        self._atualizar_linha_setpoint()

    def _atualizar_resumo_regras(self) -> None:
        ativas = [regra for regra in self._regras if regra.ativa]

        if not ativas:
            self.labelRegrasAtivas.setText("Nenhuma regra de alerta ativa.")
            return

        itens = " · ".join(
            f"{regra.nome}: {regra.limite_maximo:g} {regra.grandeza.unidade}"
            for regra in ativas
        )
        self.labelRegrasAtivas.setText(f"Regras ativas — {itens}")

    def abrir_dialogo_limites(self) -> None:
        """Abre o QDialog modal e resgata os parâmetros ao fechar."""
        dialogo = LimitesDialogController(parent=self)

        if dialogo.exec() == LimitesDialogController.DialogCode.Accepted:
            # O diálogo também publica `regras_alteradas`; o resgate pelo
            # método público é o caminho exigido pelo enunciado.
            self._aplicar_regras(dialogo.regras())

    # ------------------------------------------------------------------
    # Estado do disjuntor e atuação
    # ------------------------------------------------------------------
    def alternar_carga(self) -> None:
        """Corte de emergência (ou religamento), sempre sob confirmação."""
        if self.estado_disjuntor.energizado:
            titulo = "Corte de emergência"
            pergunta = (
                "Confirmar o CORTE DE EMERGÊNCIA da carga?\n\n"
                "O comando RELAY_OFF será enviado e a instalação "
                "ficará desenergizada."
            )
            descricao = "Corte de emergência acionado via software (RELAY_OFF)"
        else:
            titulo = "Religar carga"
            pergunta = (
                "Confirmar o religamento da carga?\n\n"
                "A instalação voltará a ser energizada."
            )
            descricao = "Religamento da carga acionado via software (RELAY_ON)"

        resposta = QMessageBox.question(
            self,
            titulo,
            pergunta,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if resposta != QMessageBox.StandardButton.Yes:
            return

        barramento.registrar_evento(
            TipoEvento.COMANDO, descricao, self.leitura_atual.resumo()
        )
        barramento.estado_disjuntor_alterado.emit(self.estado_disjuntor.alternado())

    def simular_disparo_hardware(self) -> None:
        """Reproduz um disparo espontâneo vindo do hardware.

        Na Unidade 4 quem dispara este caminho é o evento lido na porta
        serial; aqui o botão existe para demonstrar o tratamento.
        """
        self._disparo_espontaneo = True
        barramento.estado_disjuntor_alterado.emit(EstadoDisjuntor.ABERTO)

    def _ao_estado_alterado(self, estado: EstadoDisjuntor) -> None:
        """Aplica o novo estado do disjuntor na tela."""
        espontaneo = self._disparo_espontaneo
        self._disparo_espontaneo = False

        if estado is self.estado_disjuntor:
            return

        self.estado_disjuntor = estado
        self._atualizar_badge_disjuntor()

        if espontaneo:
            self._tratar_disparo_espontaneo()

    def _tratar_disparo_espontaneo(self) -> None:
        barramento.registrar_evento(
            TipoEvento.ALERTA,
            "Disjuntor abriu espontaneamente — proteção atuada no hardware",
            self.leitura_atual.resumo(),
        )
        QMessageBox.warning(
            self,
            "Proteção atuada",
            "O disjuntor abriu espontaneamente.\n\n"
            "Verifique a instalação antes de religar a carga.",
        )

    def _atualizar_badge_disjuntor(self) -> None:
        estado = self.estado_disjuntor

        self.labelBadgeDisjuntor.setText(estado.texto_indicador)
        self.labelBadgeDisjuntor.setStyleSheet(
            f"color: #FFFFFF; background-color: {estado.cor}; border-radius: 6px;"
        )

        if estado.energizado:
            self.labelDetalheDisjuntor.setText("Instalação energizada")
            self.botaoCorteEmergencia.setText("CORTE DE EMERGÊNCIA")
            self.botaoCorteEmergencia.setStyleSheet(
                f"background-color: {self._COR_CRITICA}; color: #FFFFFF;"
            )
        else:
            self.labelDetalheDisjuntor.setText("Instalação desenergizada")
            self.botaoCorteEmergencia.setText("RELIGAR CARGA")
            self.botaoCorteEmergencia.setStyleSheet(
                f"background-color: {self._COR_NORMAL}; color: #FFFFFF;"
            )

        self._atualizar_disponibilidade_comandos()
