from PyQt6.QtWidgets import QDialog, QMessageBox
from ui.limites_dialog_ui import Ui_Dialog
from models.repositorio_regras import RepositorioRegras
from models.barramento import barramento
from models.evento import Evento, TipoEvento

class LimitesDialogController(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = Ui_Dialog()
        self.ui.setupUi(self)
        
        self.repositorio = RepositorioRegras()
        self.regras_salvas = self.repositorio.obter_regras()
        
        self.carregar_dados_na_tela()
        
        self.ui.button_box.accepted.connect(self.validar_e_salvar)
        self.ui.button_box.rejected.connect(self.confirmar_cancelamento)

    def carregar_dados_na_tela(self):
        """Preenche os campos da interface para a tela não abrir zerada."""
        if self.regras_salvas:
            regra_padrao = self.regras_salvas[0]
            self.ui.input_nome.setText(regra_padrao.nome)
            self.ui.combo_grandeza.setCurrentText(regra_padrao.grandeza)
            self.ui.spin_limite.setValue(regra_padrao.limite_maximo)
            self.ui.check_ativa.setChecked(regra_padrao.ativa)

    def validar_e_salvar(self):
        """Valida os dados, atualiza o repositório e emite o barramento."""
        nome = self.ui.input_nome.text().strip()
        grandeza = self.ui.combo_grandeza.currentText()
        limite = self.ui.spin_limite.value()
        ativa = self.ui.check_ativa.isChecked()

        if not nome:
            QMessageBox.warning(self, "Aviso", "O nome da regra não pode ficar vazio.")
            return

        if limite <= 0:
            QMessageBox.warning(self, "Aviso", "O limite máximo deve ser maior que zero.")
            return
        
        # Atualiza a regra na memória
        if self.regras_salvas:
            self.regras_salvas[0].nome = nome
            self.regras_salvas[0].grandeza = grandeza
            self.regras_salvas[0].limite_maximo = limite
            self.regras_salvas[0].ativa = ativa
            
            self.repositorio.salvar_regras(self.regras_salvas)

        # Emite pelo barramento conforme exigido pela issue
        barramento.regras_alteradas(self.regras_salvas)
        barramento.registrar_evento(Evento(
            tipo=TipoEvento.COMANDO,
            origem="LimitesDialog",
            descricao="Parâmetros de limites atualizados e salvos."
        ))
        
        self.accept()

    def confirmar_cancelamento(self):
        """Exibe QMessageBox.question confirmando o descarte ao cancelar."""
        resposta = QMessageBox.question(
            self,
            "Confirmar Descarte",
            "Deseja realmente descartar as alterações pendentes?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if resposta == QMessageBox.StandardButton.Yes:
            self.reject()

    def regras(self) -> list:
        """Método público exigido para consulta pós-execução."""
        return self.regras_salvas