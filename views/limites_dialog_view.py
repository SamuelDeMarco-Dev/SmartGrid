"""Apresentação do diálogo de limites e sinais das ações do operador."""

from PyQt6.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox, QWidget

from models.regra_alerta import Grandeza
from models.validador_limites import RascunhoRegra
from ui.limites_dialog_ui import Ui_Dialog


class LimitesDialogView(QDialog, Ui_Dialog):
    regra_selecionada = pyqtSignal(int)
    regra_editada = pyqtSignal(object)
    salvar_solicitado = pyqtSignal()
    cancelar_solicitado = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setupUi(self)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        for grandeza in (Grandeza.CORRENTE, Grandeza.TENSAO, Grandeza.POTENCIA):
            self.combo_grandeza.addItem(grandeza.rotulo, grandeza)
        self._campos = (self.input_nome, self.combo_grandeza, self.input_limite, self.check_ativa)
        self._salvar = self.button_box.button(QDialogButtonBox.StandardButton.Save)
        self._cancelar = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
        self.listaRegras.currentRowChanged.connect(self.regra_selecionada)
        self.input_nome.textChanged.connect(self._emitir_edicao)
        self.input_limite.textChanged.connect(self._emitir_edicao)
        self.combo_grandeza.currentIndexChanged.connect(self._ao_mudar_grandeza)
        self.check_ativa.toggled.connect(self._emitir_edicao)
        self.button_box.accepted.connect(self.salvar_solicitado)
        self.button_box.rejected.connect(self.reject)
        self.mostrar_estado()

    def listar_regras(self, nomes: list[str]) -> None:
        with QSignalBlocker(self.listaRegras):
            self.listaRegras.clear()
            self.listaRegras.addItems(nomes)

    def selecionar_regra(self, indice: int) -> None:
        with QSignalBlocker(self.listaRegras):
            self.listaRegras.setCurrentRow(indice)

    def mostrar_regra(self, regra: RascunhoRegra | None) -> None:
        with (
            QSignalBlocker(self.input_nome), QSignalBlocker(self.combo_grandeza),
            QSignalBlocker(self.input_limite), QSignalBlocker(self.check_ativa),
        ):
            self.input_nome.setText(regra.nome if regra else "")
            self.combo_grandeza.setCurrentIndex(
                self.combo_grandeza.findData(regra.grandeza) if regra else -1
            )
            self.input_limite.setText(regra.limite_maximo if regra else "")
            self.check_ativa.setChecked(regra.ativa if regra else False)
        self._atualizar_unidade()

    def mostrar_nome(self, indice: int, nome: str) -> None:
        self.listaRegras.item(indice).setText(nome)

    def mostrar_estado(
        self, mensagem: str = "", *, ocupado: bool = False, editar: bool = False,
        tem_selecao: bool = False, cancelar: bool = True,
    ) -> None:
        self.label_status.setText(mensagem)
        self.label_status.setVisible(bool(mensagem))
        self.progresso.setVisible(ocupado)
        self._salvar.setEnabled(editar)
        self._cancelar.setEnabled(cancelar)
        self.listaRegras.setEnabled(editar)
        for campo in self._campos:
            campo.setEnabled(editar and tem_selecao)

    def avisar_entrada(self, campo: str, mensagem: str) -> None:
        widget = {
            "nome": self.input_nome, "grandeza": self.combo_grandeza,
            "limite_maximo": self.input_limite,
        }[campo]
        widget.setFocus()
        QMessageBox.warning(self, "Entrada inválida", mensagem)

    def confirmar_descarte(self) -> bool:
        resposta = QMessageBox.question(
            self, "Confirmar Descarte", "Deseja realmente descartar as alterações pendentes?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return resposta == QMessageBox.StandardButton.Yes

    def reject(self) -> None:
        """Encaminha Cancelar, Escape e fechamento à mesma decisão do controller."""
        self.cancelar_solicitado.emit()

    def fechar_cancelado(self) -> None:
        super().reject()

    def _ao_mudar_grandeza(self) -> None:
        self._atualizar_unidade()
        self._emitir_edicao()

    def _atualizar_unidade(self) -> None:
        grandeza = self.combo_grandeza.currentData()
        dica = f"Informe o limite em {grandeza.unidade}" if grandeza else ""
        self.input_limite.setPlaceholderText(dica)

    def _emitir_edicao(self) -> None:
        self.regra_editada.emit(RascunhoRegra(
            self.input_nome.text(), self.combo_grandeza.currentData(),
            self.input_limite.text(), self.check_ativa.isChecked(),
        ))
