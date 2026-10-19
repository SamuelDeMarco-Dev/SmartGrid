"""Configuração compartilhada dos testes."""

import os

import pytest

# Testes de tela rodam sem janela visível.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def app():
    """Uma única QApplication para toda a sessão.

    Destruir a QApplication entre arquivos de teste invalida objetos Qt globais,
    como o ``barramento``, e quebra os testes seguintes.
    """
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])
