"""Camada de CONTROLLER — mediação entre View e Model.

Cada controller recebe a sua View (em /views), conecta os sinais dela aos
serviços e repositórios de /models e devolve os resultados à tela. O
``AplicacaoController`` coordena o ciclo de vida das threads de serial e banco.
"""
