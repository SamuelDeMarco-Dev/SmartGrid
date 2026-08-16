from PyQt6.QtWidgets import QDialog, QMessageBox
from ui.limites_dialog_ui import Ui_Dialog
from models.barramento import barramento
from models.evento import TipoEvento
from models.regra_alerta import Grandeza, RegraAlerta
from models.repositorio_regras import (
    RepositorioRegras,
    repositorio_regras_compartilhado,
)


class LimitesDialogController(QDialog):
    def __init__(
        self,
        parent=None,
        repositorio: RepositorioRegras | None = None,
    ):
        super().__init__(parent)
        self.ui = Ui_Dialog()
        self.ui.setupUi(self)

        self.repositorio = (
            repositorio if repositorio is not None else repositorio_regras_compartilhado
        )
        self.regras_salvas = self.repositorio.obter_regras()

        self._rascunhos = [
            self._regra_para_rascunho(regra) for regra in self.regras_salvas
        ]
        self._indice_atual = -1
        self._carregando_formulario = False
        self._alteracoes_pendentes = False

        self._configurar_componentes()
        self._conectar_sinais()
        self.carregar_dados_na_tela()

    def _configurar_componentes(self) -> None:
        self.ui.combo_grandeza.clear()
        for grandeza in (Grandeza.CORRENTE, Grandeza.TENSAO, Grandeza.POTENCIA):
            self.ui.combo_grandeza.addItem(grandeza.rotulo, grandeza)

        self.ui.spin_limite.setDecimals(2)
        self.ui.spin_limite.setRange(0.01, 1_000_000.00)
        self.ui.spin_limite.setSingleStep(1.00)
        self._atualizar_sufixo_limite()

    def _conectar_sinais(self) -> None:
        self.ui.listaRegras.currentRowChanged.connect(self._selecionar_regra)
        self.ui.input_nome.textChanged.connect(self._registrar_alteracao)
        self.ui.combo_grandeza.currentIndexChanged.connect(self._ao_grandeza_alterada)
        self.ui.spin_limite.valueChanged.connect(self._registrar_alteracao)
        self.ui.check_ativa.toggled.connect(self._registrar_alteracao)
        self.ui.button_box.accepted.connect(self.validar_e_salvar)
        self.ui.button_box.rejected.connect(self.confirmar_cancelamento)

    def carregar_dados_na_tela(self) -> None:
        """Preenche a lista e carrega a primeira regra no formulário."""
        self.ui.listaRegras.clear()
        for indice in range(len(self._rascunhos)):
            self.ui.listaRegras.addItem(self._texto_item_lista(indice))

        if self._rascunhos:
            self.ui.listaRegras.setCurrentRow(0)

        self._alteracoes_pendentes = False

    def _selecionar_regra(self, indice: int) -> None:
        if self._carregando_formulario:
            return

        self._sincronizar_formulario_com_rascunho()

        if indice < 0 or indice >= len(self._rascunhos):
            self._indice_atual = -1
            return

        self._indice_atual = indice
        self._carregar_rascunho_no_formulario(indice)

    def _carregar_rascunho_no_formulario(self, indice: int) -> None:
        rascunho = self._rascunhos[indice]
        self._carregando_formulario = True
        try:
            self.ui.input_nome.setText(rascunho["nome"])
            self._selecionar_grandeza(rascunho["grandeza"])
            self.ui.spin_limite.setValue(rascunho["limite_maximo"])
            self.ui.check_ativa.setChecked(rascunho["ativa"])
            self._atualizar_sufixo_limite()
        finally:
            self._carregando_formulario = False

    def _selecionar_grandeza(self, grandeza: Grandeza) -> None:
        indice = self.ui.combo_grandeza.findData(grandeza)
        if indice >= 0:
            self.ui.combo_grandeza.setCurrentIndex(indice)

    def _registrar_alteracao(self) -> None:
        if self._carregando_formulario or self._indice_atual < 0:
            return

        self._alteracoes_pendentes = True
        self._sincronizar_formulario_com_rascunho()
        self._atualizar_item_lista(self._indice_atual)

    def _ao_grandeza_alterada(self) -> None:
        self._atualizar_sufixo_limite()
        self._registrar_alteracao()

    def _atualizar_sufixo_limite(self) -> None:
        grandeza = self.ui.combo_grandeza.currentData()
        if isinstance(grandeza, Grandeza):
            self.ui.spin_limite.setSuffix(f" {grandeza.unidade}")

    def _sincronizar_formulario_com_rascunho(self) -> None:
        if self._indice_atual < 0 or self._indice_atual >= len(self._rascunhos):
            return

        self._rascunhos[self._indice_atual] = {
            "nome": self.ui.input_nome.text(),
            "grandeza": self.ui.combo_grandeza.currentData(),
            "limite_maximo": self.ui.spin_limite.value(),
            "ativa": self.ui.check_ativa.isChecked(),
        }

    def _atualizar_item_lista(self, indice: int) -> None:
        item = self.ui.listaRegras.item(indice)
        if item is not None:
            item.setText(self._texto_item_lista(indice))

    def _texto_item_lista(self, indice: int) -> str:
        nome = self._rascunhos[indice]["nome"].strip()
        return nome or "Regra sem nome"

    def validar_e_salvar(self) -> None:
        """Valida os dados, atualiza o repositório e emite o barramento."""
        self._sincronizar_formulario_com_rascunho()
        novas_regras = self._construir_regras_validas()
        if novas_regras is None:
            return

        self.repositorio.salvar_regras(novas_regras)
        self.regras_salvas = self.repositorio.obter_regras()
        self._rascunhos = [
            self._regra_para_rascunho(regra) for regra in self.regras_salvas
        ]
        self._alteracoes_pendentes = False

        barramento.regras_alteradas.emit(self.regras())
        barramento.registrar_evento(
            TipoEvento.COMANDO,
            "Parâmetros de limites atualizados.",
        )
        self.accept()

    def _construir_regras_validas(self) -> list[RegraAlerta] | None:
        regras = []
        nomes_normalizados = set()

        for indice, rascunho in enumerate(self._rascunhos):
            nome = rascunho["nome"].strip()
            limite = float(rascunho["limite_maximo"])
            grandeza = rascunho["grandeza"]

            if not nome:
                self._selecionar_indice_com_erro(indice)
                QMessageBox.warning(
                    self,
                    "Aviso",
                    "O nome da regra não pode ficar vazio.",
                )
                return None

            if limite <= 0:
                self._selecionar_indice_com_erro(indice)
                QMessageBox.warning(
                    self,
                    "Aviso",
                    "O limite máximo deve ser maior que zero.",
                )
                return None

            if not isinstance(grandeza, Grandeza):
                self._selecionar_indice_com_erro(indice)
                QMessageBox.warning(
                    self,
                    "Aviso",
                    "Selecione uma grandeza válida para a regra.",
                )
                return None

            nome_normalizado = self._normalizar_nome(nome)
            if nome_normalizado in nomes_normalizados:
                self._selecionar_indice_com_erro(indice)
                QMessageBox.warning(
                    self,
                    "Aviso",
                    "Não é permitido cadastrar regras com nomes duplicados.",
                )
                return None

            nomes_normalizados.add(nome_normalizado)
            regras.append(
                RegraAlerta(
                    nome=nome,
                    grandeza=grandeza,
                    limite_maximo=limite,
                    ativa=bool(rascunho["ativa"]),
                )
            )

        return regras

    def _selecionar_indice_com_erro(self, indice: int) -> None:
        if self.ui.listaRegras.currentRow() != indice:
            self.ui.listaRegras.setCurrentRow(indice)

    @staticmethod
    def _normalizar_nome(nome: str) -> str:
        return " ".join(nome.split()).casefold()

    def confirmar_cancelamento(self) -> None:
        """Fecha diretamente ou confirma descarte quando há edição pendente."""
        if not self._alteracoes_pendentes:
            self.reject()
            return

        resposta = QMessageBox.question(
            self,
            "Confirmar Descarte",
            "Deseja realmente descartar as alterações pendentes?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if resposta == QMessageBox.StandardButton.Yes:
            self.reject()

    def regras(self) -> list[RegraAlerta]:
        """Método público exigido para consulta pós-execução."""
        return [
            RegraAlerta(
                nome=regra.nome,
                grandeza=regra.grandeza,
                limite_maximo=regra.limite_maximo,
                ativa=regra.ativa,
            )
            for regra in self.regras_salvas
        ]

    @staticmethod
    def _regra_para_rascunho(regra: RegraAlerta) -> dict:
        return {
            "nome": regra.nome,
            "grandeza": regra.grandeza,
            "limite_maximo": regra.limite_maximo,
            "ativa": regra.ativa,
        }

if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    janela_teste = LimitesDialogController()
    janela_teste.exec()
