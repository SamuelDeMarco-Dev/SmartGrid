from PyQt6.QtWidgets import QDialog
from ui.limites_dialog_ui import Ui_Dialog
from models.repositorio_regras import RepositorioRegras

class LimitesDialogController(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = Ui_Dialog()
        self.ui.setupUi(self)
        
        # 1. Conecta com a nossa "memória" e puxa as regras vigentes
        self.repositorio = RepositorioRegras()
        self.regras_salvas = self.repositorio.obter_regras()
        
        # 2. Executa o resgate de parâmetros exigido pela issue
        self.carregar_dados_na_tela()
        
        self.ui.button_box.accepted.connect(self.accept)
        self.ui.button_box.rejected.connect(self.reject)

    def carregar_dados_na_tela(self):
        """Preenche os campos da interface para a tela não abrir zerada."""
        if self.regras_salvas:
            # Para iniciar, carregamos a primeira regra da lista (Corrente)
            regra_padrao = self.regras_salvas[0]
            self.ui.input_nome.setText(regra_padrao.nome)
            self.ui.combo_grandeza.setCurrentText(regra_padrao.grandeza)
            self.ui.spin_limite.setValue(regra_padrao.limite_maximo)
            self.ui.check_ativa.setChecked(regra_padrao.ativa)