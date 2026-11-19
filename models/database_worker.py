"""Thread dedicada à persistência no SQLite.

A GUI nunca espera o disco: ela só enfileira pedidos, e o resultado volta
por sinal, entregue na thread da GUI pela conexão enfileirada do Qt.

Pedidos
-------
``gravar_leitura(leitura)``
    Telemetria vinda do ``SerialWorker``. As leituras acumuladas na fila são
    gravadas em lote, numa única transação.
``gravar_evento(evento, dispositivo_tag=None)``
    Evento de auditoria. Após o COMMIT emite ``barramento.evento_persistido``.
``consultar_leituras(filtro)``
    Consulta histórica do gráfico → ``leituras_consultadas(filtro, lista)``
    ou ``consulta_falhou(mensagem)``.
``executar(funcao, *args, ao_concluir=..., ao_falhar=...)``
    API genérica para as telas rodarem qualquer operação de repositório fora
    da GUI. ``ao_concluir(resultado)`` e ``ao_falhar(erro)`` são chamados na
    thread da GUI, podendo mexer em widgets à vontade.

Encerramento cooperativo: :meth:`parar` coloca uma sentinela no fim da fila.
Tudo o que foi pedido antes dela ainda é gravado; depois, ``run`` retorna e
``finished`` dispara (o controller liga ``finished`` a ``deleteLater``).
"""

from __future__ import annotations

import itertools
import logging
import queue
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from models.barramento import barramento
from models.evento import Evento
from models.leitura import Leitura
from models.repositorio_eventos import RepositorioEventos
from models.repositorio_leituras import FiltroLeituras, RepositorioLeituras

logger = logging.getLogger("smartgrid")

_PARAR = object()


@dataclass(slots=True)
class _Tarefa:
    funcao: Callable[..., Any]
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    id: int | None = None


class DatabaseWorker(QThread):
    """Dono das gravações e consultas demoradas no SQLite."""

    leituras_consultadas = pyqtSignal(object, list)
    consulta_falhou = pyqtSignal(str)

    #: Total de leituras gravadas, emitido a cada lote (útil para a interface).
    leituras_gravadas = pyqtSignal(int)

    # Internos: levam o resultado de ``executar`` de volta à thread da GUI.
    _tarefa_concluida = pyqtSignal(int, object)
    _tarefa_falhou = pyqtSignal(int, object)

    def __init__(
        self,
        repo_leituras: RepositorioLeituras | None = None,
        repo_eventos: RepositorioEventos | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._repo_leituras = repo_leituras or RepositorioLeituras()
        self._repo_eventos = repo_eventos or RepositorioEventos()
        self._fila: queue.SimpleQueue = queue.SimpleQueue()
        self._ids = itertools.count(1)
        self._callbacks: dict[int, tuple[Callable | None, Callable | None]] = {}
        self._trava = threading.Lock()
        self._total_leituras = 0

        # O QThread pertence à thread que o criou (a GUI); por isso estes
        # slots rodam lá, mesmo com o sinal emitido de dentro de run().
        self._tarefa_concluida.connect(self._despachar_conclusao)
        self._tarefa_falhou.connect(self._despachar_falha)

    # ------------------------------------------------------------------
    # API usada pela thread da GUI (thread-safe, nunca bloqueia)
    # ------------------------------------------------------------------
    def gravar_leitura(self, leitura: Leitura) -> None:
        self._fila.put(leitura)

    def gravar_evento(self, evento: Evento, dispositivo_tag: str | None = None) -> None:
        self._fila.put(_Tarefa(self._gravar_evento, (evento, dispositivo_tag)))

    def consultar_leituras(self, filtro: FiltroLeituras) -> None:
        self._fila.put(_Tarefa(self._consultar_leituras, (filtro,)))

    def executar(
        self,
        funcao: Callable[..., Any],
        *args: Any,
        ao_concluir: Callable[[Any], None] | None = None,
        ao_falhar: Callable[[Exception], None] | None = None,
        **kwargs: Any,
    ) -> int:
        """Roda ``funcao(*args, **kwargs)`` na thread do banco.

        Exemplo (na tela de histórico)::

            banco.executar(
                repositorio.filtrar, tipo=TipoEvento.ALERTA,
                ao_concluir=self._preencher_tabela,
                ao_falhar=self._tratar_erro_banco,
            )
        """
        id_tarefa = next(self._ids)
        with self._trava:
            self._callbacks[id_tarefa] = (ao_concluir, ao_falhar)
        self._fila.put(_Tarefa(funcao, args, kwargs, id_tarefa))
        return id_tarefa

    def descartar_callbacks(self, id_tarefa: int) -> None:
        """Desliga os callbacks de uma tela encerrada, sem cancelar o I/O.

        A tarefa ainda termina normalmente na fila; seu resultado não mantém
        referências nem tenta acessar widgets que já foram destruídos.
        """
        self._retirar_callbacks(id_tarefa)

    def parar(self) -> None:
        """Pede o encerramento depois de esvaziar o que já está na fila."""
        self.requestInterruption()
        self._fila.put(_PARAR)

    # ------------------------------------------------------------------
    # Corpo da thread
    # ------------------------------------------------------------------
    def run(self) -> None:
        logger.info("Thread de banco de dados iniciada.")
        pendentes: list[Leitura] = []
        while True:
            item = self._fila.get()
            # Junta tudo o que já estiver esperando: leituras viram um lote só.
            itens = [item]
            while True:
                try:
                    itens.append(self._fila.get_nowait())
                except queue.Empty:
                    break

            parar = False
            for item in itens:
                if item is _PARAR:
                    parar = True
                    break
                if isinstance(item, Leitura):
                    pendentes.append(item)
                    continue
                # Mantém a ordem: leituras pedidas antes de uma tarefa vão antes.
                self._gravar_lote(pendentes)
                pendentes = []
                self._rodar(item)

            self._gravar_lote(pendentes)
            pendentes = []
            if parar:
                break
        logger.info("Thread de banco de dados encerrada.")

    def _gravar_lote(self, leituras: list[Leitura]) -> None:
        if not leituras:
            return
        try:
            self._total_leituras += self._repo_leituras.adicionar_lote(leituras)
        except sqlite3.Error as erro:
            logger.error("Falha ao gravar %d leituras: %s", len(leituras), erro)
            return
        self.leituras_gravadas.emit(self._total_leituras)

    def _rodar(self, tarefa: _Tarefa) -> None:
        try:
            resultado = tarefa.funcao(*tarefa.args, **tarefa.kwargs)
        except Exception as erro:
            # Exceção solta aqui mataria a thread e pararia a persistência.
            if tarefa.id is None:
                logger.error("Falha em tarefa do banco: %s", erro)
            else:
                self._tarefa_falhou.emit(tarefa.id, erro)
            return
        if tarefa.id is not None:
            self._tarefa_concluida.emit(tarefa.id, resultado)

    # ------------------------------------------------------------------
    # Tarefas internas (rodam na thread do banco)
    # ------------------------------------------------------------------
    def _gravar_evento(self, evento: Evento, dispositivo_tag: str | None) -> None:
        try:
            self._repo_eventos.adicionar(evento, dispositivo_tag=dispositivo_tag)
        except sqlite3.Error as erro:
            logger.error("Falha ao gravar evento '%s' no banco: %s", evento.descricao, erro)
            return
        barramento.evento_persistido.emit(evento)

    def _consultar_leituras(self, filtro: FiltroLeituras) -> None:
        try:
            leituras = self._repo_leituras.consultar(filtro)
        except sqlite3.Error as erro:
            logger.error("Falha na consulta histórica (%s): %s", filtro.descricao, erro)
            self.consulta_falhou.emit(f"Não foi possível consultar o histórico: {erro}")
            return
        logger.info("Consulta histórica: %s — %d leituras.", filtro.descricao, len(leituras))
        self.leituras_consultadas.emit(filtro, leituras)

    # ------------------------------------------------------------------
    # Despacho dos callbacks (rodam na thread da GUI)
    # ------------------------------------------------------------------
    def _despachar_conclusao(self, id_tarefa: int, resultado: Any) -> None:
        ao_concluir, _ = self._retirar_callbacks(id_tarefa)
        if ao_concluir is not None:
            self._chamar_com_seguranca(ao_concluir, resultado)

    def _despachar_falha(self, id_tarefa: int, erro: Exception) -> None:
        _, ao_falhar = self._retirar_callbacks(id_tarefa)
        if ao_falhar is not None:
            self._chamar_com_seguranca(ao_falhar, erro)
        else:
            logger.error("Falha em tarefa do banco sem tratador: %s", erro)

    @staticmethod
    def _chamar_com_seguranca(callback: Callable, argumento: Any) -> None:
        # Em PyQt6 uma exceção solta num slot aborta o processo.
        try:
            callback(argumento)
        except Exception:
            logger.exception("Erro no tratador de uma tarefa do banco.")

    def _retirar_callbacks(self, id_tarefa: int) -> tuple[Callable | None, Callable | None]:
        with self._trava:
            return self._callbacks.pop(id_tarefa, (None, None))
