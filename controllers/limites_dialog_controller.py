from PyQt6.QtWidgets import QDialog
from ui.limites_dialog_ui import Ui_Dialog

class LimitesDialogController(QDialog):
    def __init__(self, parent=None):
        # Herda os comportamentos de QDialog para permitir abertura modal com exec()
        super().__init__(parent)
        
        # Carrega a interface visual que desenhamos no Qt Designer
        self.ui = Ui_Dialog()
        self.ui.setupUi(self)
        
        # Conecta os botões Salvar e Cancelar aos comandos de aceitar e rejeitar
        self.ui.button_box.accepted.connect(self.accept)
        self.ui.button_box.rejected.connect(self.reject)