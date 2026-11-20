"""Coordena a edição de regras, o worker compartilhado e o barramento."""

import logging
from weakref import WeakMethod

from PyQt6 import sip
from PyQt6.QtCore import QObject

from models.barramento import barramento
from models.database_worker import DatabaseWorker
from models.evento import TipoEvento
from models.regra_alerta import RegraAlerta
from models.repositorio_regras import RepositorioRegras
from models.servico_limites import salvar_limites
from models.validador_limites import EntradaLimiteInvalida, RascunhoRegra, validar_regras
from views.limites_dialog_view import LimitesDialogView

logger = logging.getLogger("smartgrid")


class LimitesDialogController(QObject):
    def __init__(
        self, view: LimitesDialogView, repositorio: RepositorioRegras, banco: DatabaseWorker,
    ) -> None:
        super().__init__(view)
        self.view = view
        self._repositorio = repositorio
        self._banco = banco
        self._rascunhos: list[RascunhoRegra] = []
        self._indice_atual = -1
        self._alteracoes_pendentes = False
        self._salvando = False
        self._encerrado = False
        self._mensagem = ""

        view.regra_selecionada.connect(self._selecionar)
        view.regra_editada.connect(self._editar)
        view.salvar_solicitado.connect(self.validar_e_salvar)
        view.cancelar_solicitado.connect(self.confirmar_cancelamento)
        view.finished.connect(self._ao_fechar)
        view.mostrar_estado("Carregando regras…", ocupado=True)
        banco.executar(
            repositorio.obter_regras,
            ao_concluir=self._callback_tela(self._carregar_regras),
            ao_falhar=self._callback_tela(self._falhar_carregamento),
        )

    def _carregar_regras(self, regras: list[RegraAlerta]) -> None:
        self._rascunhos = [RascunhoRegra.da_regra(regra) for regra in regras]
        self.view.listar_regras([self._nome_na_lista(regra) for regra in self._rascunhos])
        self._mensagem = "" if regras else "Nenhuma regra cadastrada."
        self._selecionar(0 if regras else -1)

    def _falhar_carregamento(self, erro: Exception) -> None:
        logger.error("Erro ao carregar regras de limites: %s", erro)
        self.view.mostrar_estado(
            "Não foi possível carregar as regras. Feche o diálogo e tente novamente.",
        )

    def _selecionar(self, indice: int) -> None:
        if self._salvando or self._encerrado:
            return
        tem_selecao = 0 <= indice < len(self._rascunhos)
        self._indice_atual = indice if tem_selecao else -1
        self.view.selecionar_regra(self._indice_atual)
        self.view.mostrar_regra(self._rascunhos[indice] if tem_selecao else None)
        self._mostrar_edicao()

    def _editar(self, regra: RascunhoRegra) -> None:
        if self._salvando or self._encerrado or self._indice_atual < 0:
            return
        self._rascunhos[self._indice_atual] = regra
        self._alteracoes_pendentes = True
        self.view.mostrar_nome(self._indice_atual, self._nome_na_lista(regra))

    @staticmethod
    def _nome_na_lista(regra: RascunhoRegra) -> str:
        return regra.nome.strip() or "Regra sem nome"

    def validar_e_salvar(self) -> None:
        if self._salvando or self._encerrado or not self._rascunhos:
            return
        try:
            regras = validar_regras(self._rascunhos)
        except EntradaLimiteInvalida as erro:
            logger.warning("Entrada inválida em limites (%s): %s", erro.campo, erro)
            self._selecionar(erro.indice)
            self.view.avisar_entrada(erro.campo, str(erro))
            return

        self._salvando = True
        self.view.mostrar_estado("Salvando regras…", ocupado=True, cancelar=False)
        avisar_tela = self._callback_tela(self._concluir_salvamento)

        def publicar(erro_json: Exception | None) -> None:
            # A confirmação do banco vale para o aplicativo, mesmo se a tela já fechou.
            barramento.regras_alteradas.emit(regras)
            if erro_json is None:
                mensagem = "Parâmetros de limites atualizados."
            else:
                logger.error("Regras salvas no banco, mas o config.json falhou: %s", erro_json)
                mensagem = "Parâmetros de limites atualizados apenas no banco."
            barramento.registrar_evento(TipoEvento.COMANDO, mensagem)
            avisar_tela(erro_json)

        self._banco.executar(
            salvar_limites, self._repositorio, regras,
            ao_concluir=publicar,
            ao_falhar=self._callback_tela(self._falhar_salvamento),
        )

    def _concluir_salvamento(self, erro_json: Exception | None) -> None:
        self._alteracoes_pendentes = False
        self._salvando = False
        if erro_json is None:
            self.view.accept()
            return
        self._mensagem = (
            "As regras foram salvas no banco e já estão em uso. "
            "Não foi possível atualizar o config.json. Use Salvar para tentar novamente."
        )
        self._mostrar_edicao()

    def _falhar_salvamento(self, erro: Exception) -> None:
        logger.error("Erro ao salvar regras de limites no banco: %s", erro)
        self._salvando = False
        self._mensagem = "Não foi possível salvar as regras no banco. Nenhuma alteração foi aplicada."
        self._mostrar_edicao()

    def _mostrar_edicao(self) -> None:
        self.view.mostrar_estado(
            self._mensagem, editar=bool(self._rascunhos), tem_selecao=self._indice_atual >= 0,
        )

    def confirmar_cancelamento(self) -> None:
        if self._salvando or self._encerrado:
            return
        if not self._alteracoes_pendentes or self.view.confirmar_descarte():
            self.view.fechar_cancelado()

    def _ao_fechar(self, _resultado: int) -> None:
        self._encerrado = True

    def _callback_tela(self, metodo):
        """Um trabalho na fila não prolonga a vida do diálogo ou do controller."""
        referencia = WeakMethod(metodo)

        def entregar(resultado):
            callback = referencia()
            if callback is not None:
                controller = callback.__self__
                if not sip.isdeleted(controller) and not controller._encerrado:
                    callback(resultado)

        return entregar
