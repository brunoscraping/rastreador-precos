"""Testes do notificador — formatação, credenciais e degradação sem config."""

from __future__ import annotations

from decimal import Decimal

import pytest

from rastreador.notificador import (
    Alerta,
    NotificadorMultiplo,
    NotificadorTelegram,
)


@pytest.fixture()
def alerta() -> Alerta:
    return Alerta(
        produto_id=7,
        titulo="Notebook 15 i5",
        url="https://loja.com/p/7",
        preco_atual=Decimal("1199.90"),
        preco_anterior=Decimal("1499.00"),
        preco_alvo=Decimal("1200.00"),
        moeda="BRL",
    )


# ---------------------------------------------------------------------------
# Alerta — cálculos e formatação
# ---------------------------------------------------------------------------


def test_queda_percentual(alerta: Alerta) -> None:
    # 1199.90 vs 1499.00 -> -19.95%
    assert alerta.queda == Decimal("-19.95")


def test_queda_sem_preco_anterior() -> None:
    a = Alerta(
        produto_id=1,
        titulo="X",
        url="https://x.com",
        preco_atual=Decimal("10"),
        preco_anterior=None,
        preco_alvo=None,
    )
    assert a.queda is None


def test_queda_com_anterior_zero() -> None:
    """Divisão por zero não pode explodir."""
    a = Alerta(
        produto_id=1,
        titulo="X",
        url="https://x.com",
        preco_atual=Decimal("10"),
        preco_anterior=Decimal("0"),
        preco_alvo=None,
    )
    assert a.queda is None


def test_formatar_valor_brl(alerta: Alerta) -> None:
    assert alerta.formatar_valor(Decimal("1199.90")) == "R$ 1.199,90"


def test_formatar_valor_usd() -> None:
    a = Alerta(1, "X", "https://x.com", Decimal("10"), None, None, moeda="USD")
    assert a.formatar_valor(Decimal("1299.90")) == "US$ 1.299,90"


def test_formatar_valor_eur() -> None:
    a = Alerta(1, "X", "https://x.com", Decimal("10"), None, None, moeda="EUR")
    assert a.formatar_valor(Decimal("49.99")) == "€ 49,99"


# ---------------------------------------------------------------------------
# NotificadorTelegram — sem credencial
# ---------------------------------------------------------------------------


def test_nao_configurado_sem_token(monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert NotificadorTelegram().configurado is False


def test_nao_configurado_com_apenas_token(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert NotificadorTelegram().configurado is False


def test_configurado_com_ambos(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    assert NotificadorTelegram().configurado is True


def test_envio_sem_config_retorna_false(alerta: Alerta, monkeypatch) -> None:
    """Sem credencial o envio falha silenciosamente — não levanta exceção."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    n = NotificadorTelegram()
    assert n.enviar(alerta) is False
    assert n.enviar_texto("teste") is False


# ---------------------------------------------------------------------------
# Montagem da mensagem
# ---------------------------------------------------------------------------


def test_mensagem_contem_dados_essenciais(alerta: Alerta) -> None:
    n = NotificadorTelegram(token="123:ABC", chat_id="999")
    msg = n._montar_mensagem(alerta)

    assert "Notebook 15 i5" in msg
    assert "R$ 1.199,90" in msg       # preço atual
    assert "R$ 1.499,00" in msg       # preço anterior
    assert "-19.95%" in msg           # variação
    assert "R$ 1.200,00" in msg       # alvo
    assert "loja.com/p/7" in msg      # link


def test_mensagem_sem_titulo_usa_id(alerta: Alerta) -> None:
    sem_titulo = Alerta(
        produto_id=42,
        titulo=None,
        url=alerta.url,
        preco_atual=alerta.preco_atual,
        preco_anterior=None,
        preco_alvo=None,
    )
    n = NotificadorTelegram(token="123:ABC", chat_id="999")
    assert "Produto #42" in n._montar_mensagem(sem_titulo)


def test_mensagem_sem_anterior_omite_linha(alerta: Alerta) -> None:
    sem_anterior = Alerta(
        produto_id=1,
        titulo="X",
        url=alerta.url,
        preco_atual=Decimal("50"),
        preco_anterior=None,
        preco_alvo=None,
    )
    n = NotificadorTelegram(token="123:ABC", chat_id="999")
    msg = n._montar_mensagem(sem_anterior)
    assert "Antes:" not in msg
    assert "Variação:" not in msg


def test_mensagem_escapa_html(alerta: Alerta) -> None:
    """Título com < > & não pode quebrar o parse_mode=HTML."""
    perigoso = Alerta(
        produto_id=1,
        titulo="Fone <b>barato</b> & bom",
        url=alerta.url,
        preco_atual=Decimal("99"),
        preco_anterior=None,
        preco_alvo=None,
    )
    n = NotificadorTelegram(token="123:ABC", chat_id="999")
    msg = n._montar_mensagem(perigoso)

    assert "&lt;b&gt;" in msg
    assert "&amp;" in msg
    assert "<b>barato</b>" not in msg


# ---------------------------------------------------------------------------
# NotificadorMultiplo
# ---------------------------------------------------------------------------


class _Falso:
    def __init__(self, ok: bool, config: bool = True) -> None:
        self.ok = ok
        self.configurado = config
        self.chamado = False

    def enviar(self, alerta: Alerta) -> bool:
        self.chamado = True
        return self.ok


def test_multiplo_sucesso_se_um_entregar(alerta: Alerta) -> None:
    a, b = _Falso(False), _Falso(True)
    assert NotificadorMultiplo([a, b]).enviar(alerta) is True
    assert a.chamado and b.chamado


def test_multiplo_falha_se_todos_falharem(alerta: Alerta) -> None:
    assert NotificadorMultiplo([_Falso(False), _Falso(False)]).enviar(alerta) is False


def test_multiplo_pula_nao_configurado(alerta: Alerta) -> None:
    nao_config = _Falso(True, config=False)
    NotificadorMultiplo([nao_config]).enviar(alerta)
    assert nao_config.chamado is False


def test_multiplo_configurado() -> None:
    assert NotificadorMultiplo([_Falso(True, config=False)]).configurado is False
    assert NotificadorMultiplo([_Falso(True, config=True)]).configurado is True
