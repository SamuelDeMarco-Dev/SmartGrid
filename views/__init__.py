"""Camada de VIEW — classes de tela (QWidget + layout compilado em /ui).

Uma View só desenha e captura eventos do operador, que saem como sinais
(``corte_solicitado``, ``consulta_ultimas_solicitada``...). Não calcula, não
consulta banco e não fala com a porta serial: quem faz isso é o controller.
"""
