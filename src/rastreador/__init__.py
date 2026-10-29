"""rastreador-precos — monitor de preço com histórico e alerta."""

__version__ = "1.0.0"

from .banco import Banco, Estatistica, Produto
from .coletor import ColetaFalhou, ColetorNavegador, ColetorSimples, Resposta
from .extrator import Preco, extrair_preco, extrair_titulo, normalizar_preco
from .motor import Motor, Resultado, banco_padrao
from .notificador import (
    Alerta,
    Notificador,
    NotificadorMultiplo,
    NotificadorTelegram,
    notificador_padrao,
)

__all__ = [
    "Banco",
    "Estatistica",
    "Produto",
    "ColetaFalhou",
    "ColetorNavegador",
    "ColetorSimples",
    "Resposta",
    "Preco",
    "extrair_preco",
    "extrair_titulo",
    "normalizar_preco",
    "Motor",
    "Resultado",
    "banco_padrao",
    "Alerta",
    "Notificador",
    "NotificadorMultiplo",
    "NotificadorTelegram",
    "notificador_padrao",
]
