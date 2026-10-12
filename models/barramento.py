"""Barramento de eventos — canal único de comunicação entre as telas.

Existe para que nenhum controller precise importar outro controller: cada tela
emite no barramento e escuta o que lhe interessa. Isso mantém as telas
independentes e permite que sejam desenvolvidas em paralelo.

Uso típico:

    from models.barramento import barramento
    from models.evento import TipoEvento

    barramento.evento_registrado.connect(self._adicionar_linha)
    barramento.registrar_evento(TipoEvento.COMANDO, "Corte acionado", "12.5 A")

Contrato compartilhado — congelado após o merge do M0.
"""

from PyQt6.QtCore import QObject, pyqtSignal

from models.estado_disjuntor import EstadoDisjuntor
from models.evento import Evento, TipoEvento
from models.leitura import Leitura


class Barramento(QObject):
    """Publicador central de sinais da aplicação."""

    #: Nova amostra de telemetria disponível.
    leitura_recebida = pyqtSignal(Leitura)

    #: PEDIDO de gravação: algo aconteceu e precisa entrar na auditoria.
    #: Quem publica um evento emite este sinal; o ``GravadorEventos`` o
    #: persiste no SQLite. Telas NÃO devem escutá-lo para exibir linhas.
    evento_registrado = pyqtSignal(Evento)

    #: CONFIRMAÇÃO: o evento já está gravado no banco. É o sinal que a tabela
    #: do histórico escuta, para só mostrar o que de fato foi persistido.
    #: Emitido pelo ``GravadorEventos`` (ou pelo serviço de corte emergencial,
    #: depois do COMMIT da sua própria transação).
    evento_persistido = pyqtSignal(Evento)

    #: O disjuntor mudou de estado (por comando ou espontaneamente).
    estado_disjuntor_alterado = pyqtSignal(EstadoDisjuntor)

    #: As regras de alerta foram salvas no diálogo de limites.
    #: Carrega uma ``list[RegraAlerta]``.
    regras_alteradas = pyqtSignal(list)

    #: Estado da conexão serial: (conectado, descrição legível).
    status_conexao_alterado = pyqtSignal(bool, str)

    def registrar_evento(
        self, tipo: TipoEvento, descricao: str, valor_medido: str = ""
    ) -> Evento:
        """Cria o evento carimbado com o horário atual e o publica.

        Atalho para o caso mais comum; quem precisar de um timestamp
        específico pode montar o ``Evento`` e emitir o sinal diretamente.
        """
        evento = Evento.agora(tipo, descricao, valor_medido)
        self.evento_registrado.emit(evento)
        return evento


#: Instância única compartilhada por toda a aplicação.
barramento = Barramento()
