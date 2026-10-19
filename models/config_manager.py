"""Configuração persistente do SmartGrid em ``data/config.json``.

Objetivo
--------
Guardar entre execuções as escolhas que dependem da máquina (porta, velocidade
e timeout da comunicação serial), os limites iniciais de alerta e o interruptor
do modo de falha simulada.
O app sempre recebe uma configuração válida, mesmo que o arquivo não exista,
esteja corrompido ou tenha valores inválidos.

Campos
------
=======================  ===========  =========================================
Campo                    Padrão       Regra
=======================  ===========  =========================================
``porta_com``            ``"COM1"``   texto não vazio
``baud_rate``            ``9600``     uma das velocidades de ``BAUD_RATES``
``timeout_ms``           ``1000``     inteiro de 100 a 10 000 (milissegundos)
``limites_iniciais``     ver abaixo   ``corrente``, ``tensao`` e ``potencia``,
                                      números maiores que zero
``simular_falha_corte``  ``false``    verdadeiro ou falso
=======================  ===========  =========================================

As regras de porta, velocidade e timeout são as mesmas da tela serial
(``models/porta_serial.py``).

``limites_iniciais`` (padrão ``{"corrente": 15.0, "tensao": 230.0,
"potencia": 3500.0}``, em A, V e W) semeia a tabela ``regras_limites`` quando o
banco é criado. Depois disso a fonte oficial dos limites é o banco, editado
pelo diálogo de limites; mudar o JSON só vale para um banco novo.

Tolerância a falhas
-------------------
- Arquivo inexistente: é criado com os padrões e registrado em ``[INFO]``.
- JSON ilegível: usa todos os padrões e registra ``[WARNING]``. O arquivo não
  é sobrescrito, para manter a evidência do problema até o próximo ``salvar``.
- Campo ausente ou inválido: só aquele campo volta ao padrão, com ``[WARNING]``.
- Erro de disco: registra ``[ERROR]`` e segue com os padrões em memória.

Uso
---
::

    from dataclasses import replace
    from models.config_manager import ConfigManager

    config = ConfigManager.carregar()
    config.baud_rate                      # 9600

    nova = replace(config, porta_com="COM3", baud_rate=115200)
    ConfigManager.salvar(nova)
"""

import json
import math
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from database.schema import REGRAS_INICIAIS
from models.log_config import obter_logger
from models.porta_serial import (
    BAUD_RATE_PADRAO,
    BAUD_RATES,
    PORTAS_SIMULADAS,
    TIMEOUT_MAXIMO_MS,
    TIMEOUT_MINIMO_MS,
    TIMEOUT_PADRAO_MS,
)

logger = obter_logger()

CAMINHO_CONFIG = Path(__file__).resolve().parent.parent / "data" / "config.json"


def _inteiro(valor) -> bool:
    # bool é subclasse de int no Python: sem esta checagem, true passaria como 1.
    return isinstance(valor, int) and not isinstance(valor, bool)


#: Limites de alerta usados quando o banco é criado (A, V e W), os mesmos
#: das regras iniciais do esquema: {"corrente": 15.0, "tensao": 230.0, ...}.
LIMITES_INICIAIS_PADRAO = {
    grandeza.lower(): limite for _, grandeza, limite, _ in REGRAS_INICIAIS
}


def _limites_validos(valor) -> bool:
    """Exige exatamente corrente, tensão e potência, todos números finitos > 0."""
    return (
        isinstance(valor, dict)
        and set(valor) == set(LIMITES_INICIAIS_PADRAO)
        and all(
            isinstance(limite, (int, float))
            and not isinstance(limite, bool)
            and math.isfinite(limite)
            and limite > 0
            for limite in valor.values()
        )
    )


_VALIDADORES = {
    "porta_com": lambda v: isinstance(v, str) and bool(v.strip()),
    "baud_rate": lambda v: _inteiro(v) and v in BAUD_RATES,
    "timeout_ms": lambda v: _inteiro(v) and TIMEOUT_MINIMO_MS <= v <= TIMEOUT_MAXIMO_MS,
    "limites_iniciais": _limites_validos,
    "simular_falha_corte": lambda v: isinstance(v, bool),
}


@dataclass(frozen=True, slots=True)
class Configuracao:
    """Configuração vigente. Valores inválidos levantam ``ValueError``."""

    porta_com: str = PORTAS_SIMULADAS[0]
    baud_rate: int = BAUD_RATE_PADRAO
    timeout_ms: int = TIMEOUT_PADRAO_MS
    limites_iniciais: dict[str, float] = field(
        default_factory=lambda: dict(LIMITES_INICIAIS_PADRAO)
    )
    simular_falha_corte: bool = False

    def __post_init__(self) -> None:
        invalidos = [
            campo.name
            for campo in fields(self)
            if not _VALIDADORES[campo.name](getattr(self, campo.name))
        ]
        if invalidos:
            raise ValueError(f"Configuração inválida: {', '.join(invalidos)}.")


def _exibir_caminho() -> str:
    """Caminho curto para as mensagens de log: ``data/config.json``."""
    return f"{CAMINHO_CONFIG.parent.name}/{CAMINHO_CONFIG.name}"


class ConfigManager:
    """Lê e grava ``data/config.json`` sem nunca derrubar a aplicação."""

    @classmethod
    def carregar(cls) -> Configuracao:
        """Devolve a configuração do arquivo, criando-o ou corrigindo o que for preciso."""
        if not os.path.exists(CAMINHO_CONFIG):
            return cls._criar_com_padroes()

        try:
            with open(CAMINHO_CONFIG, encoding="utf-8") as arquivo:
                dados = json.load(arquivo)
        except ValueError as erro:
            logger.warning(
                "Arquivo de configuração %s ilegível (%s). Usando valores padrão.",
                _exibir_caminho(), erro,
            )
            return Configuracao()
        except OSError as erro:
            logger.error(
                "Não foi possível ler %s: %s. Usando valores padrão.", _exibir_caminho(), erro
            )
            return Configuracao()

        if not isinstance(dados, dict):
            logger.warning(
                "Arquivo de configuração %s não contém um objeto JSON. Usando valores padrão.",
                _exibir_caminho(),
            )
            return Configuracao()

        return cls._montar(dados)

    @staticmethod
    def salvar(config: Configuracao) -> None:
        """Grava de forma atômica: escreve em ``.tmp`` e troca o arquivo com ``os.replace``.

        Levanta ``OSError`` se o disco falhar; o arquivo anterior fica intacto.
        """
        CAMINHO_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        temporario = CAMINHO_CONFIG.with_name(CAMINHO_CONFIG.name + ".tmp")
        try:
            with open(temporario, "w", encoding="utf-8") as arquivo:
                json.dump(asdict(config), arquivo, ensure_ascii=False, indent=2)
                arquivo.write("\n")
                arquivo.flush()
                os.fsync(arquivo.fileno())
            os.replace(temporario, CAMINHO_CONFIG)
        except OSError:
            temporario.unlink(missing_ok=True)
            raise

    @classmethod
    def _criar_com_padroes(cls) -> Configuracao:
        config = Configuracao()
        try:
            cls.salvar(config)
        except OSError as erro:
            logger.error(
                "Não foi possível criar %s: %s. Usando valores padrão.", _exibir_caminho(), erro
            )
        else:
            logger.info(
                "Arquivo de configuração não encontrado. %s criado com valores padrão.",
                _exibir_caminho(),
            )
        return config

    @staticmethod
    def _montar(dados: dict) -> Configuracao:
        """Aproveita cada campo válido; os demais voltam ao padrão com ``[WARNING]``."""
        padrao = Configuracao()
        valores = {}
        for campo in fields(Configuracao):
            nome = campo.name
            if nome not in dados:
                logger.warning(
                    "Campo '%s' ausente em %s. Usando o padrão: %r.",
                    nome, _exibir_caminho(), getattr(padrao, nome),
                )
            elif not _VALIDADORES[nome](dados[nome]):
                logger.warning(
                    "Valor inválido para '%s' em %s: %r. Usando o padrão: %r.",
                    nome, _exibir_caminho(), dados[nome], getattr(padrao, nome),
                )
            else:
                valores[nome] = dados[nome]
        return Configuracao(**valores)
