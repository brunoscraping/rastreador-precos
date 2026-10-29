"""Testes da camada de extração e normalização de preço."""

from __future__ import annotations

from decimal import Decimal

import pytest

from rastreador.extrator import extrair_preco, extrair_titulo, normalizar_preco


# ---------------------------------------------------------------------------
# normalizar_preco — o ponto mais propenso a erro
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        # formato brasileiro
        ("R$ 1.299,90", Decimal("1299.90")),
        ("R$1.299,90", Decimal("1299.90")),
        ("1.299,90", Decimal("1299.90")),
        ("R$ 49,90", Decimal("49.90")),
        ("R$ 8,00", Decimal("8.00")),
        # formato americano
        ("$1,299.90", Decimal("1299.90")),
        ("1,299.90", Decimal("1299.90")),
        ("19.99", Decimal("19.99")),
        # inteiros
        ("R$ 150", Decimal("150")),
        ("150", Decimal("150")),
        # com espaços e ruído
        ("  R$  2.499,00  ", Decimal("2499.00")),
        ("De R$ 199,90", Decimal("199.90")),
        ("por apenas R$ 89,90 no pix", Decimal("89.90")),
    ],
)
def test_normalizar_formatos(entrada: str, esperado: Decimal) -> None:
    preco = normalizar_preco(entrada)
    assert preco is not None, f"falhou ao normalizar: {entrada!r}"
    assert preco.valor == esperado


@pytest.mark.parametrize(
    ("entrada", "moeda"),
    [
        ("R$ 100,00", "BRL"),
        ("$100.00", "USD"),
        ("€ 100,00", "EUR"),
        ("£100.00", "GBP"),
    ],
)
def test_deteccao_moeda(entrada: str, moeda: str) -> None:
    preco = normalizar_preco(entrada)
    assert preco is not None
    assert preco.moeda == moeda


@pytest.mark.parametrize(
    "entrada",
    ["", "   ", "indisponível", "esgotado", "R$ 0,00", "0", "-50,00", "sob consulta"],
)
def test_rejeita_invalidos(entrada: str) -> None:
    assert normalizar_preco(entrada) is None


def test_preserva_valor_bruto() -> None:
    preco = normalizar_preco("  R$ 1.299,90  ")
    assert preco is not None
    assert preco.bruto == "R$ 1.299,90"


# ---------------------------------------------------------------------------
# extrair_preco — cascata de estratégias
# ---------------------------------------------------------------------------

HTML_JSON_LD = """
<html><head>
<script type="application/ld+json">
{"@type": "Product", "name": "Fone Bluetooth", "offers": {"@type": "Offer", "price": "249.90", "priceCurrency": "BRL"}}
</script>
</head><body><div class="preco">R$ 299,90</div></body></html>
"""

HTML_META = """
<html><head>
<meta property="product:price:amount" content="189.90">
<meta property="og:title" content="Teclado Mecânico">
</head><body><span class="valor">R$ 199,90</span></body></html>
"""

HTML_SELETOR = """
<html><head><title>Mouse Gamer</title></head>
<body><div class="product-price">R$ 79,90</div></body></html>
"""

HTML_VAZIO = "<html><head><title>Erro</title></head><body>produto não encontrado</body></html>"


def test_json_ld_tem_prioridade() -> None:
    """JSON-LD vence o seletor quando os dois existem."""
    preco = extrair_preco(HTML_JSON_LD, seletores=[".preco"])
    assert preco is not None
    assert preco.valor == Decimal("249.90")


def test_meta_quando_sem_json_ld() -> None:
    preco = extrair_preco(HTML_META, seletores=[".valor"])
    assert preco is not None
    assert preco.valor == Decimal("189.90")


def test_seletor_como_ultimo_recurso() -> None:
    preco = extrair_preco(HTML_SELETOR, seletores=[".nao-existe", ".product-price"])
    assert preco is not None
    assert preco.valor == Decimal("79.90")


def test_retorna_none_sem_preco() -> None:
    assert extrair_preco(HTML_VAZIO, seletores=[".preco"]) is None


def test_extrair_titulo_og() -> None:
    assert extrair_titulo(HTML_META) == "Teclado Mecânico"


def test_extrair_titulo_title() -> None:
    assert extrair_titulo(HTML_SELETOR) == "Mouse Gamer"
