"""View do Dashboard — desenha a tela e devolve as ações do operador como sinais.

Não calcula, não consulta banco e não fala com a serial: recebe valores
prontos do ``DashboardController`` e os mostra.
"""

from __future__ import annotations

import pyqtgraph as pg
from PyQt6.QtCore import QDateTime, Qt, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QSizePolicy, QWidget

from models.estado_disjuntor import EstadoDisjuntor
from ui.dashboard_ui import Ui_Dashboard

COR_NORMAL = "#2E7D32"
COR_ATENCAO = "#B26A00"
COR_CRITICA = "#C62828"
COR_INATIVA = "#6B7280"
COR_CURVA = "#1D4F80"
COR_CURVA_HISTORICO = "#7B3FA0"


class DashboardView(QWidget, Ui_Dashboard):
    """Tela central de supervisão."""

    corte_solicitado = pyqtSignal()
    disparo_simulado_solicitado = pyqtSignal()
    limites_solicitados = pyqtSignal()
    setpoint_alterado = pyqtSignal(float)
    modo_tempo_real_solicitado = pyqtSignal()
    consulta_ultimas_solicitada = pyqtSignal(int)
    consulta_intervalo_solicitada = pyqtSignal(object, object)  # (datetime, datetime)

    #: Teto de pontos mantidos na curva de tempo real.
    MAXIMO_PONTOS = 3000

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setupUi(self)
        self._instantes: list[float] = []
        self._potencias: list[float] = []
        self._montar_grafico()
        self._preparar_filtros()
        self._conectar_widgets()

    # ------------------------------------------------------------------
    # Montagem
    # ------------------------------------------------------------------
    def _montar_grafico(self) -> None:
        self.grafico = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        # O PlotWidget pede ~450 px de altura por conta própria, o que faria a
        # página inteira crescer e forçar rolagem mesmo em telas grandes. Com
        # a política Ignored ele passa a ocupar só o espaço que sobrar.
        self.grafico.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.grafico.setBackground("w")
        self.grafico.showGrid(x=True, y=True, alpha=0.30)
        self.grafico.setLabel("left", "Potência (W)")
        self.grafico.setLabel("bottom", "Horário")

        self.curva_consumo = self.grafico.plot([], [], pen=pg.mkPen(COR_CURVA, width=2))
        self.linha_setpoint = pg.InfiniteLine(
            angle=0, pen=pg.mkPen(COR_CRITICA, width=1, style=Qt.PenStyle.DashLine)
        )
        self.linha_setpoint.setPos(self.spinSetpoint.value())
        self.grafico.addItem(self.linha_setpoint)
        self.layoutGrafico.addWidget(self.grafico, stretch=1)
        # A barra de modo fica com a altura mínima; o resto é do gráfico.
        self.labelStatusGrafico.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def _preparar_filtros(self) -> None:
        agora = QDateTime.currentDateTime()
        self.dateTimeFim.setDateTime(agora)
        self.dateTimeInicio.setDateTime(agora.addSecs(-3600))
        self._atualizar_campos_filtro()

    def _conectar_widgets(self) -> None:
        self.botaoCorteEmergencia.clicked.connect(self.corte_solicitado)
        self.botaoSimularDisparo.clicked.connect(self.disparo_simulado_solicitado)
        self.botaoConfigurarLimites.clicked.connect(self.limites_solicitados)

        # Slider e spin editam o mesmo setpoint e ficam espelhados.
        self.sliderSetpoint.valueChanged.connect(
            lambda valor: self.spinSetpoint.setValue(float(valor))
        )
        self.spinSetpoint.valueChanged.connect(self._ao_spin_alterado)

        self.radioTempoReal.toggled.connect(self._ao_modo_alterado)
        self.comboFiltroHistorico.currentIndexChanged.connect(self._atualizar_campos_filtro)
        self.botaoConsultarHistorico.clicked.connect(self._ao_consultar)

    # ------------------------------------------------------------------
    # Eventos dos widgets → sinais
    # ------------------------------------------------------------------
    def _ao_spin_alterado(self, valor: float) -> None:
        if self.sliderSetpoint.value() != int(valor):
            self.sliderSetpoint.setValue(int(valor))
        self.linha_setpoint.setPos(valor)
        self.setpoint_alterado.emit(valor)

    def _ao_modo_alterado(self) -> None:
        self._atualizar_campos_filtro()
        if self.radioTempoReal.isChecked():
            self.modo_tempo_real_solicitado.emit()

    def _ao_consultar(self) -> None:
        if self.comboFiltroHistorico.currentIndex() == 0:
            self.consulta_ultimas_solicitada.emit(self.spinUltimasN.value())
        else:
            self.consulta_intervalo_solicitada.emit(
                self.dateTimeInicio.dateTime().toPyDateTime(),
                self.dateTimeFim.dateTime().toPyDateTime(),
            )

    def _atualizar_campos_filtro(self) -> None:
        historico = self.radioHistorico.isChecked()
        por_intervalo = self.comboFiltroHistorico.currentIndex() == 1
        self.comboFiltroHistorico.setEnabled(historico)
        self.botaoConsultarHistorico.setEnabled(historico)
        self.spinUltimasN.setVisible(not por_intervalo)
        self.spinUltimasN.setEnabled(historico)
        for campo in (self.dateTimeInicio, self.dateTimeFim):
            campo.setVisible(por_intervalo)
            campo.setEnabled(historico)

    # ------------------------------------------------------------------
    # Exibição (chamada pelo controller)
    # ------------------------------------------------------------------
    def mostrar_leitura(self, tensao: float, corrente: float, potencia: float) -> None:
        self.lcdTensao.display(f"{tensao:.1f}")
        self.lcdCorrente.display(f"{corrente:.2f}")
        self.lcdPotencia.display(f"{potencia:.0f}")

    def mostrar_situacao(self, cor: str, texto: str) -> None:
        self.labelSituacaoProtecao.setText(texto)
        self.labelSituacaoProtecao.setStyleSheet(
            f"color: #FFFFFF; background-color: {cor}; border-radius: 4px;"
        )
        self.framePainelTelemetria.setStyleSheet(
            f"QGroupBox {{ border: 2px solid {cor}; border-radius: 6px; "
            "margin-top: 8px; padding-top: 8px; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; }"
        )

    def mostrar_aquisicao(self, cor: str, texto: str) -> None:
        self.labelStatusAquisicao.setText(texto)
        self.labelStatusAquisicao.setStyleSheet(
            f"color: #FFFFFF; background-color: {cor}; border-radius: 4px;"
        )

    def mostrar_sem_confirmacao(self, ultimo_estado: EstadoDisjuntor) -> None:
        self.labelBadgeDisjuntor.setText(f"Disjuntor: SEM CONFIRMAÇÃO (último: {ultimo_estado.name})")
        self.labelBadgeDisjuntor.setStyleSheet(f"background-color: {COR_INATIVA}; color: white;")
        self.labelDetalheDisjuntor.setText("Estado atual da carga sem confirmação")

    def mostrar_disjuntor(self, estado: EstadoDisjuntor) -> None:
        self.labelBadgeDisjuntor.setText(estado.texto_indicador)
        self.labelBadgeDisjuntor.setStyleSheet(
            f"color: #FFFFFF; background-color: {estado.cor}; border-radius: 6px;"
        )
        if estado.energizado:
            self.labelDetalheDisjuntor.setText("Instalação energizada")
            self.botaoCorteEmergencia.setText("CORTE DE EMERGÊNCIA")
            cor = COR_CRITICA
        else:
            self.labelDetalheDisjuntor.setText("Instalação desenergizada")
            self.botaoCorteEmergencia.setText("RELIGAR CARGA")
            cor = COR_NORMAL
        self.botaoCorteEmergencia.setStyleSheet(f"background-color: {cor}; color: #FFFFFF;")

    def habilitar_comandos(self, corte: bool, disparo: bool, dica: str = "") -> None:
        self.botaoCorteEmergencia.setEnabled(corte)
        self.botaoCorteEmergencia.setToolTip(dica)
        self.botaoSimularDisparo.setEnabled(disparo)

    def mostrar_resumo_regras(self, texto: str) -> None:
        self.labelRegrasAtivas.setText(texto)

    def definir_setpoint(self, valor: float) -> None:
        valor = min(max(valor, self.spinSetpoint.minimum()), self.spinSetpoint.maximum())
        self.spinSetpoint.setValue(valor)
        self.linha_setpoint.setPos(self.spinSetpoint.value())

    def setpoint(self) -> float:
        return self.spinSetpoint.value()

    # --- gráfico -------------------------------------------------------
    def em_tempo_real(self) -> bool:
        return self.radioTempoReal.isChecked()

    def plotar_tempo_real(self, instantes: list[float], potencias: list[float]) -> None:
        """Pré-carrega o histórico preservando amostras recebidas durante a consulta."""
        pontos = dict(zip(instantes, potencias))
        pontos.update(zip(self._instantes, self._potencias))
        recentes = sorted(pontos.items())[-self.MAXIMO_PONTOS:]
        self._instantes = [instante for instante, _ in recentes]
        self._potencias = [potencia for _, potencia in recentes]
        if self.em_tempo_real():
            self._redesenhar_tempo_real()

    def acrescentar_ponto(self, instante: float, potencia: float) -> None:
        self._instantes.append(instante)
        self._potencias.append(potencia)
        if len(self._instantes) > self.MAXIMO_PONTOS:
            del self._instantes[0]
            del self._potencias[0]
        if self.em_tempo_real():
            self.curva_consumo.setData(self._instantes, self._potencias)

    def _redesenhar_tempo_real(self) -> None:
        self.grupoGrafico.setTitle("Potência ativa — tempo real")
        self.curva_consumo.setPen(pg.mkPen(COR_CURVA, width=2))
        self.curva_consumo.setData(self._instantes, self._potencias)
        self.grafico.enableAutoRange()
        self.labelStatusGrafico.setText("")

    def mostrar_tempo_real(self) -> None:
        self._redesenhar_tempo_real()

    def plotar_historico(self, titulo: str, instantes: list[float], potencias: list[float]) -> None:
        self.grupoGrafico.setTitle(f"Potência ativa — histórico: {titulo}")
        self.curva_consumo.setPen(pg.mkPen(COR_CURVA_HISTORICO, width=2))
        self.curva_consumo.setData(instantes, potencias)
        self.grafico.enableAutoRange()
        self.botaoConsultarHistorico.setEnabled(True)
        self.labelStatusGrafico.setText(f"{len(instantes)} leituras")

    def mostrar_consultando(self) -> None:
        self.botaoConsultarHistorico.setEnabled(False)
        self.labelStatusGrafico.setText("Consultando o banco…")

    def mostrar_falha_consulta(self, mensagem: str) -> None:
        self.botaoConsultarHistorico.setEnabled(self.radioHistorico.isChecked())
        self.labelStatusGrafico.setText("")
        self.avisar("Consulta histórica", mensagem)

    # --- diálogos ------------------------------------------------------
    def confirmar(self, titulo: str, pergunta: str) -> bool:
        resposta = QMessageBox.question(
            self, titulo, pergunta,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return resposta == QMessageBox.StandardButton.Yes

    def avisar(self, titulo: str, mensagem: str) -> None:
        QMessageBox.warning(self, titulo, mensagem)

    def avisar_erro(self, titulo: str, mensagem: str) -> None:
        QMessageBox.critical(self, titulo, mensagem)
