"""
rastreador-precos — monitor de preço com histórico e alerta.

Núcleo de extração: transforma uma página HTML em um preço normalizado,
usando uma cascata de estratégias (JSON-LD -> meta tags -> seletor CSS).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

# lexbor e o backend ativo do selectolax; o antigo "parser" (modest)
# foi descontinuado na versao 1.0 e levanta ImportError na importacao.
from selectolax.lexbor import LexborHTMLParser as HTMLParser

# ---------------------------------------------------------------------------
# Normalização de preço
# ---------------------------------------------------------------------------

# Formatos aceitos: "R$ 1.299,90", "R$1.299,90", "1299.90", "1.299,90", "€ 19,99"
_MOEDA_SIMBOLOS = "R$€£$¥"


@dataclass(frozen=True, slots=True)
class Preco:
    """Preço normalizado. `valor` é sempre Decimal para não perder centavo."""

    valor: Decimal
    moeda: str
    bruto: str

    def __str__(self) -> str:
        return f"{self.moeda} {self.valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def normalizar_preco(texto: str, moeda_padrao: str = "BRL") -> Preco | None:
    """
    Converte texto de preço em Decimal.

    Lida com os dois formatos que aparecem no mundo real:
      1.299,90  (pt-BR)  -> 1299.90
      1,299.90  (en-US)  -> 1299.90
    A decisão é tomada pela posição do último separador.
    """
    if not texto:
        return None

    bruto = texto.strip()
    moeda = moeda_padrao

    # detecta símbolo de moeda
    for simbolo in _MOEDA_SIMBOLOS:
        if simbolo in bruto:
            moeda = {"R$": "BRL", "€": "EUR", "£": "GBP", "$": "USD", "¥": "JPY"}.get(simbolo, moeda_padrao)
            break

    # remove tudo que não é dígito, separador ou sinal
    limpo = re.sub(r"[^\d.,\-]", "", bruto)
    if not limpo:
        return None

    # remove separador de milhar ambíguo
    ultimo_virgula = limpo.rfind(",")
    ultimo_ponto = limpo.rfind(".")

    if ultimo_virgula > ultimo_ponto:
        # vírgula é decimal: 1.299,90
        limpo = limpo.replace(".", "").replace(",", ".")
    elif ultimo_ponto > ultimo_virgula:
        # ponto é decimal: 1,299.90
        limpo = limpo.replace(",", "")
    # se não há separador, é inteiro

    try:
        valor = Decimal(limpo)
    except (InvalidOperation, ValueError):
        return None

    if valor <= 0:
        return None

    return Preco(valor=valor, moeda=moeda, bruto=bruto)


# ---------------------------------------------------------------------------
# Cascata de extração
# ---------------------------------------------------------------------------


def _de_json_ld(arvore: HTMLParser) -> str | None:
    """Estratégia 1 — schema.org/Product. A mais confiável quando existe."""
    for script in arvore.css('script[type="application/ld+json"]'):
        try:
            dados = json.loads(script.text() or "")
        except (json.JSONDecodeError, TypeError):
            continue

        for no in dados if isinstance(dados, list) else [dados]:
            if not isinstance(no, dict):
                continue
            oferta = no.get("offers")
            if isinstance(oferta, list):
                oferta = oferta[0] if oferta else None
            if isinstance(oferta, dict):
                preco = oferta.get("price") or oferta.get("lowPrice")
                if preco is not None:
                    return str(preco)
    return None


def _de_meta(arvore: HTMLParser) -> str | None:
    """Estratégia 2 — meta tags de Open Graph / product."""
    pares = [
        ('meta[property="product:price:amount"]', "content"),
        ('meta[property="og:price:amount"]', "content"),
        ('meta[itemprop="price"]', "content"),
        ('meta[name="price"]', "content"),
    ]
    for seletor, attr in pares:
        no = arvore.css_first(seletor)
        if no is not None:
            valor = no.attributes.get(attr)
            if valor:
                return valor
    return None


def _de_seletor(arvore: HTMLParser, seletores: list[str]) -> str | None:
    """Estratégia 3 — seletores CSS informados pelo usuário."""
    for seletor in seletores:
        no = arvore.css_first(seletor)
        if no is not None:
            texto = no.text(strip=True)
            if texto:
                return texto
    return None


def extrair_preco(
    html: str,
    seletores: list[str] | None = None,
    moeda_padrao: str = "BRL",
) -> Preco | None:
    """
    Extrai o preço de uma página tentando três estratégias em ordem de confiança.

    Retorna None quando nenhuma funciona — quem chama decide o que fazer.
    """
    arvore = HTMLParser(html)

    for estrategia in (
        lambda: _de_json_ld(arvore),
        lambda: _de_meta(arvore),
        lambda: _de_seletor(arvore, seletores or []),
    ):
        texto = estrategia()
        if texto:
            preco = normalizar_preco(str(texto), moeda_padrao)
            if preco is not None:
                return preco

    return None


def extrair_titulo(html: str) -> str | None:
    """Título do produto — usado no relatório e na notificação."""
    arvore = HTMLParser(html)

    no = arvore.css_first('meta[property="og:title"]')
    if no is not None and no.attributes.get("content"):
        return no.attributes["content"].strip()

    no = arvore.css_first("title")
    if no is not None and no.text(strip=True):
        return no.text(strip=True)

    return None
