"""Ponto único de gravação de eventos.

Escuta ``barramento.evento_registrado`` (pedido de gravação), persiste o evento
pelo repositório e só então emite ``barramento.evento_persistido``. Assim a
tabela do histórico mostra apenas o que realmente está no banco.

Deve existir UMA instância por aplicação (criada no ``main.py``); duas
instâncias gravariam cada evento em duplicidade.
"""

import logging
import sqlite3

from PyQt6.QtCore import QObject, pyqtSlot

from models.barramento import barramento
from models.evento import Evento

logger = logging.getLogger("smartgrid")


class GravadorEventos(QObject):
    """Persiste cada evento publicado no barramento, sem derrubar a UI."""

    def __init__(self, repositorio, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._repositorio = repositorio
        barramento.evento_registrado.connect(self._gravar)

    @pyqtSlot(Evento)
    def _gravar(self, evento: Evento) -> None:
        try:
            self._repositorio.adicionar(evento)
        except sqlite3.Error as erro:
            logger.error("Falha ao gravar evento '%s' no banco: %s", evento.descricao, erro)
            return
        except Exception as erro:
            # Em PyQt6 uma exceção solta num slot aborta o processo.
            logger.error("Erro inesperado ao gravar evento '%s': %s", evento.descricao, erro)
            return

        barramento.evento_persistido.emit(evento)
