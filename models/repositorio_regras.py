from models.regra_alerta import RegraAlerta

class RepositorioRegras:
    def __init__(self):
        # Armazenamento em memória com valores padrão iniciais exigidos pela issue
        self._regras = [
            RegraAlerta(nome="Alerta Sobrecorrente", grandeza="Corrente", limite_maximo=15.0, ativa=True),
            RegraAlerta(nome="Alerta Sobretensão", grandeza="Tensão", limite_maximo=230.0, ativa=True),
            RegraAlerta(nome="Pico de Potência", grandeza="Potência", limite_maximo=3500.0, ativa=False)
        ]

    def obter_regras(self):
        """Retorna uma cópia da lista de regras atuais."""
        return self._regras.copy()

    def salvar_regras(self, novas_regras):
        """Sobrescreve as regras armazenadas com a nova lista."""
        self._regras = novas_regras