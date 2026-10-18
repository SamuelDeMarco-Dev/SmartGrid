"""Log de auditoria do SmartGrid.

Objetivo
--------
Registrar em ``logs/smartgrid.log`` tudo o que a aplicação faz de relevante,
para que a operação possa ser auditada depois (o arquivo é aberto ao final da
demonstração). O arquivo é criado automaticamente, gravado em UTF-8 e cada
registro vai para o disco no momento em que é emitido.

Formato da linha de auditoria
-----------------------------
Toda linha segue o padrão::

    AAAA-MM-DD HH:MM:SS [NÍVEL] descrição

- ``AAAA-MM-DD HH:MM:SS``: data e hora locais do registro, com segundos.
- ``[NÍVEL]``: ``INFO``, ``WARNING`` ou ``ERROR``, em maiúsculas e entre
  colchetes.
- ``descrição``: frase completa em português, terminada em ponto.

Padrão de níveis
----------------
- ``INFO``: operação normal concluída (conexão com o banco, gravação de
  evento, limpeza do histórico...).
- ``WARNING``: situação anormal que o sistema tratou sem falhar (limite
  violado, entrada inválida, configuração corrompida).
- ``ERROR``: falha ou exceção, inclusive rollback de transação.

Regras de escrita
-----------------
- Usar sempre ``obter_logger()``; nunca ``print``.
- Passar os valores como parâmetros, sem f-string::

      logger.warning("Limite violado: %.1fA.", corrente)

- Valores numéricos com unidade e, quando houver, o limite configurado.
- Em erros, informar o motivo da falha.
- Nunca registrar dados sensíveis (senhas, dados pessoais).

Uso
---
``configurar_log()`` é chamada uma única vez, no início do ``main.py``. Os
demais módulos apenas obtêm o logger::

    from models.log_config import obter_logger

    logger = obter_logger()

Mensagens-modelo
----------------
::

    logger.info("Conexão com SQLite estabelecida e esquema validado.")
    # 2026-10-20 14:03:11 [INFO] Conexão com SQLite estabelecida e esquema validado.

    logger.warning(
        "Limite de corrente violado: %.1fA (Limite configurado: %.1fA).", 28.5, 25.0
    )
    # 2026-10-20 14:03:12 [WARNING] Limite de corrente violado: 28.5A (Limite configurado: 25.0A).

    logger.error(
        "Falha na operação de corte emergencial. Rollback executado: %s.", erro
    )
    # 2026-10-20 14:03:13 [ERROR] Falha na operação de corte emergencial. Rollback executado: database is locked.
"""

import logging
from pathlib import Path

PASTA_LOGS = Path(__file__).resolve().parent.parent / "logs"
ARQUIVO_LOG = PASTA_LOGS / "smartgrid.log"
NOME_LOGGER = "smartgrid"
FORMATO = "%(asctime)s [%(levelname)s] %(message)s"
FORMATO_DATA = "%Y-%m-%d %H:%M:%S"


def _ja_configurado() -> bool:
    """Indica se o logger raiz já grava no arquivo de auditoria."""
    return any(
        isinstance(handler, logging.FileHandler)
        and Path(handler.baseFilename) == ARQUIVO_LOG
        for handler in logging.getLogger().handlers
    )


def configurar_log() -> None:
    """Direciona o log para ``logs/smartgrid.log``. Chamar mais de uma vez não duplica handlers."""
    PASTA_LOGS.mkdir(parents=True, exist_ok=True)
    if _ja_configurado():
        return

    # O FileHandler criado aqui faz flush a cada registro: o arquivo fica
    # atualizado mesmo se for aberto com a aplicação ainda em execução.
    logging.basicConfig(
        filename=ARQUIVO_LOG,
        level=logging.INFO,
        format=FORMATO,
        datefmt=FORMATO_DATA,
        encoding="utf-8",
    )


def obter_logger() -> logging.Logger:
    """Devolve o logger compartilhado por todos os módulos do SmartGrid."""
    return logging.getLogger(NOME_LOGGER)
