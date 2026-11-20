"""Grava regras no SQLite e atualiza os limites de inicialização do JSON."""

from models.config_manager import ConfigManager
from models.regra_alerta import RegraAlerta
from models.repositorio_regras import RepositorioRegras
from models.validador_limites import limites_para_config


def salvar_limites(
    repositorio: RepositorioRegras, regras: list[RegraAlerta],
) -> Exception | None:
    """Tarefa do DatabaseWorker. Retorna eventual falha do JSON após o commit.

    SQLite é a fonte das regras em uso. Uma falha no SQLite interrompe a
    operação antes do JSON. Se só o JSON falhar, mantém o commit e permite
    tentar salvar novamente, com aviso explícito de persistência parcial.
    """
    limites = limites_para_config(regras)
    repositorio.salvar_regras(regras)
    try:
        ConfigManager.atualizar(limites_iniciais=limites)
    except Exception as erro:
        # Neste ponto a transação já foi confirmada, mesmo para erros inesperados de arquivo.
        return erro
    return None
