"""Geração de telemetria simulada para a entrega A1/1.

Nesta etapa não há comunicação com o microcontrolador (isso é da Unidade 4),
então as amostras são sintetizadas aqui. A curva segue um perfil de demanda
residencial — vale de madrugada, pico no início da noite — para que o gráfico
de tendência pareça um consumo real, e não ruído aleatório.

Contrato compartilhado — congelado após o merge do M0.
"""

import random
from datetime import datetime, timedelta

from models.leitura import Leitura

#: Tensão nominal da rede usada como referência (V).
TENSAO_NOMINAL = 220.0

#: Potência de pico do perfil de demanda (W).
POTENCIA_PICO = 3200.0

#: Fração da potência de pico para cada hora do dia (0h a 23h).
PERFIL_DIARIO = (
    0.22, 0.18, 0.16, 0.15, 0.15, 0.18,  # madrugada
    0.30, 0.48, 0.52, 0.42, 0.38, 0.45,  # manhã
    0.58, 0.50, 0.44, 0.42, 0.46, 0.60,  # tarde
    0.82, 1.00, 0.94, 0.76, 0.52, 0.32,  # noite
)


def _fator_demanda(instante: datetime) -> float:
    """Interpola o perfil diário para o horário exato informado."""
    hora_atual = instante.hour + instante.minute / 60.0
    inicio = int(hora_atual) % 24
    fim = (inicio + 1) % 24
    peso = hora_atual - int(hora_atual)
    return PERFIL_DIARIO[inicio] * (1 - peso) + PERFIL_DIARIO[fim] * peso


def gerar_leitura(instante: datetime | None = None) -> Leitura:
    """Sintetiza uma amostra coerente com o horário informado."""
    instante = instante or datetime.now()
    potencia = POTENCIA_PICO * _fator_demanda(instante)
    potencia *= random.uniform(0.94, 1.06)  # ruído de medição

    tensao = TENSAO_NOMINAL + random.uniform(-3.5, 3.5)
    corrente = potencia / tensao
    return Leitura(instante=instante, tensao=tensao, corrente=corrente)


def gerar_historico_24h(
    fim: datetime | None = None, amostras_por_hora: int = 4
) -> list[Leitura]:
    """Curva de demanda das últimas 24 horas, terminando em `fim`.

    Usada para **pré-carregar** o gráfico de tendência no momento em que o
    dashboard abre, conforme exigido na entrega A1/1.
    """
    if amostras_por_hora < 1:
        raise ValueError("É preciso ao menos uma amostra por hora.")

    fim = fim or datetime.now()
    intervalo = timedelta(hours=1) / amostras_por_hora
    total = 24 * amostras_por_hora

    inicio = fim - timedelta(hours=24)
    return [gerar_leitura(inicio + intervalo * i) for i in range(total + 1)]


def proxima_leitura() -> Leitura:
    """Amostra do instante atual, para o QTimer do dashboard."""
    return gerar_leitura()
