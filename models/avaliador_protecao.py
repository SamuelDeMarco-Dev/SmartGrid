"""Regra de proteção de software: compara cada amostra com os limites.

Saiu do Dashboard para o Model na A1/5: a tela só pinta o resultado. Dois
limites valem ao mesmo tempo:

- o **setpoint de potência** do painel (slider/spin), com faixa de atenção a
  partir de :data:`FRACAO_ATENCAO` do valor;
- as **regras ativas** do diálogo de limites (tensão, corrente ou potência).

A avaliação também diz quando uma violação *começa* (``nova_violacao``):
com uma amostra por segundo, registrar log e evento a cada amostra
inundaria o histórico.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from models.leitura import Leitura
from models.regra_alerta import Grandeza, RegraAlerta

#: Acima desta fração do setpoint o painel entra em atenção.
FRACAO_ATENCAO = 0.90


class NivelProtecao(Enum):
    NORMAL = "Consumo dentro do limite"
    ATENCAO = "Consumo próximo do limite"
    CRITICO = "LIMITE ULTRAPASSADO"
    INATIVO = "Telemetria interrompida"


@dataclass(frozen=True, slots=True)
class Violacao:
    """Um limite ultrapassado por uma amostra."""

    descricao: str
    grandeza: Grandeza
    valor: float
    limite: float


@dataclass(frozen=True, slots=True)
class Avaliacao:
    nivel: NivelProtecao
    violacoes: tuple[Violacao, ...] = ()
    #: True só na transição normal → violado.
    nova_violacao: bool = False

    @property
    def texto(self) -> str:
        if self.violacoes:
            return f"{self.nivel.value} — {self.violacoes[0].descricao}"
        return self.nivel.value


def valor_da_grandeza(leitura: Leitura, grandeza: Grandeza) -> float:
    if leitura.picos is not None:
        return leitura.picos[{
            Grandeza.TENSAO: 0, Grandeza.CORRENTE: 1, Grandeza.POTENCIA: 2,
        }[grandeza]]
    return {
        Grandeza.TENSAO: leitura.tensao,
        Grandeza.CORRENTE: leitura.corrente,
        Grandeza.POTENCIA: leitura.potencia,
    }[grandeza]


def setpoint_das_regras(regras: Iterable[RegraAlerta]) -> float | None:
    """Limite da regra ativa de potência, que governa o setpoint do painel.

    Sem isso o operador salvaria 2800 W no diálogo e o painel continuaria
    alertando pelo valor antigo do slider.
    """
    for regra in regras:
        if regra.grandeza is Grandeza.POTENCIA and regra.ativa:
            return regra.limite_maximo
    return None


class AvaliadorProtecao:
    """Guarda limites e o estado de violação entre uma amostra e outra."""

    def __init__(self, regras: Iterable[RegraAlerta] = (), setpoint_w: float = 3500.0) -> None:
        self.regras: list[RegraAlerta] = list(regras)
        self.setpoint_w = setpoint_w
        self._em_violacao = False

    def atualizar_regras(self, regras: Iterable[RegraAlerta]) -> None:
        self.regras = list(regras)

    @property
    def regras_ativas(self) -> list[RegraAlerta]:
        return [regra for regra in self.regras if regra.ativa]

    def reiniciar(self) -> None:
        """Esquece a violação em curso (ex.: link caiu): ao voltar, alerta de novo."""
        self._em_violacao = False

    def avaliar(self, leitura: Leitura) -> Avaliacao:
        violacoes = tuple(self._violacoes(leitura))
        if violacoes:
            nova = not self._em_violacao
            self._em_violacao = True
            return Avaliacao(NivelProtecao.CRITICO, violacoes, nova)

        self._em_violacao = False
        if valor_da_grandeza(leitura, Grandeza.POTENCIA) >= self.setpoint_w * FRACAO_ATENCAO:
            return Avaliacao(NivelProtecao.ATENCAO)
        return Avaliacao(NivelProtecao.NORMAL)

    def _violacoes(self, leitura: Leitura) -> list[Violacao]:
        fora = []
        potencia = valor_da_grandeza(leitura, Grandeza.POTENCIA)
        if potencia > self.setpoint_w:
            fora.append(Violacao(
                f"potência em {potencia:.0f} W (máximo da janela)",
                Grandeza.POTENCIA, potencia, self.setpoint_w,
            ))
        for regra in self.regras:
            valor = valor_da_grandeza(leitura, regra.grandeza)
            if regra.violada_por(valor):
                fora.append(Violacao(
                    f"{regra.nome} — {valor:.2f} {regra.grandeza.unidade}",
                    regra.grandeza, valor, regra.limite_maximo,
                ))
        return fora
