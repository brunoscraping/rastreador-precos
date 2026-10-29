"""
Coleta HTTP com retry, backoff e detecção de bloqueio.

Duas camadas:
  - `ColetorSimples`  : requests, rápido, resolve página estática.
  - `ColetorNavegador`: Playwright, resolve JavaScript, custa ~2s por página.

A escolha é por URL: o simples tenta primeiro; se o HTML vier sem preço,
o navegador é acionado. Quem chama não precisa saber a diferença.
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from typing import Final

import requests

UA_PADRAO: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# marcadores de página de bloqueio / captcha
_SINAIS_BLOQUEIO: Final = (
    "cf-browser-verification",
    "captcha",
    "unusual traffic",
    "access denied",
    "are you a robot",
    "checking your browser",
)


class ColetaFalhou(Exception):
    """Falha definitiva após esgotar as tentativas."""


@dataclass
class Resposta:
    """O que o coletor devolve, independente de qual camada respondeu."""

    url: str
    html: str
    status: int
    origem: str          # "http" | "navegador"
    tentativas: int = 1
    bloqueado: bool = False
    cabecalhos: dict[str, str] = field(default_factory=dict)

    @property
    def vazio(self) -> bool:
        return not self.html or len(self.html.strip()) < 64

    @property
    def parece_html(self) -> bool:
        """
        True se o corpo realmente parece HTML.

        Guarda contra corpo comprimido que o cliente HTTP nao descomprimiu:
        nesse caso o parser recebe binario e devolve "preco nao encontrado",
        escondendo a causa real.
        """
        if not self.html:
            return False
        amostra = self.html[:512].lstrip().lower()
        return amostra.startswith(("<!doctype", "<html", "<?xml")) or "<html" in amostra


def _detectar_bloqueio(html: str, status: int) -> bool:
    if status in (403, 429, 503):
        return True
    amostra = html[:4000].lower()
    return any(sinal in amostra for sinal in _SINAIS_BLOQUEIO)


def _decodificar(resp: requests.Response) -> str:
    """
    Decodifica o corpo preservando acentos e simbolos de moeda.

    O problema real: muitos sites servem `Content-Type: text/html` sem charset,
    e o requests assume ISO-8859-1 (padrao legado do HTTP). Isso transforma
    "R$ 1.299,90" e "£51.77" em mojibake ("RÂ$", "Â£"), que quebra a extracao
    de preco por causa do simbolo.

    Ordem de decisao:
      1. charset declarado no cabecalho, se for plausivel;
      2. meta charset dentro do HTML;
      3. deteccao pelo proprio conteudo (chardet via requests);
      4. UTF-8 como ultimo recurso.
    """
    if not resp.content:
        return ""

    # 1. cabecalho — mas desconfia de ISO-8859-1 implicito
    charset = resp.encoding
    if charset and charset.lower() in ("iso-8859-1", "latin-1", "ascii"):
        charset = None  # provavelmente e palpite do requests, nao declaracao real

    # 2. meta charset no HTML
    if not charset:
        cabeca = resp.content[:2048].decode("ascii", errors="ignore")
        match = re.search(r'charset=["\']?([\w-]+)', cabeca, re.IGNORECASE)
        if match:
            charset = match.group(1)

    # 3. deteccao pelo conteudo — tenta UTF-8 primeiro, que e o caso dominante
    if not charset:
        for tentativa in ("utf-8", "cp1252", "latin-1"):
            try:
                return resp.content.decode(tentativa)
            except (UnicodeDecodeError, LookupError):
                continue
        return resp.content.decode("utf-8", errors="replace")

    try:
        return resp.content.decode(charset, errors="replace")
    except LookupError:
        return resp.content.decode("utf-8", errors="replace")


@dataclass
class ColetorSimples:
    """Cliente HTTP com retry exponencial e jitter."""

    timeout: float = 20.0
    tentativas: int = 3
    pausa_base: float = 1.0
    pausa_max: float = 30.0
    user_agent: str = UA_PADRAO
    sessao: requests.Session = field(default_factory=requests.Session)

    def __post_init__(self) -> None:
        # `br` (Brotli) so entra em Accept-Encoding se a lib estiver presente.
        # Sem `brotli` instalado o requests NAO descomprime, e o corpo chega
        # como lixo binario — o parser entao nao encontra preco nenhum.
        codificacoes = ["gzip", "deflate"]
        try:
            import brotli  # noqa: F401

            codificacoes.append("br")
        except ImportError:
            pass

        self.sessao.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
                "Accept-Encoding": ", ".join(codificacoes),
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }
        )

    def buscar(self, url: str) -> Resposta:
        ultimo_erro: Exception | None = None

        for tentativa in range(1, self.tentativas + 1):
            try:
                resp = self.sessao.get(url, timeout=self.timeout, allow_redirects=True)

                if resp.status_code == 200:
                    corpo = _decodificar(resp)
                    return Resposta(
                        url=resp.url,
                        html=corpo,
                        status=200,
                        origem="http",
                        tentativas=tentativa,
                        bloqueado=_detectar_bloqueio(corpo, 200),
                        cabecalhos=dict(resp.headers),
                    )

                # 4xx (menos 429) não melhora tentando de novo
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    raise ColetaFalhou(f"HTTP {resp.status_code} em {url}")

                if resp.status_code in (429, 503):
                    espera = self._espera(tentativa, resp.headers.get("Retry-After"))
                else:
                    espera = self._espera(tentativa)

                if tentativa < self.tentativas:
                    time.sleep(espera)

            except (requests.Timeout, requests.ConnectionError) as exc:
                ultimo_erro = exc
                if tentativa < self.tentativas:
                    time.sleep(self._espera(tentativa))
            except ColetaFalhou:
                raise
            except requests.RequestException as exc:
                ultimo_erro = exc
                if tentativa < self.tentativas:
                    time.sleep(self._espera(tentativa))

        raise ColetaFalhou(f"esgotou {self.tentativas} tentativas em {url}: {ultimo_erro}")

    def _espera(self, tentativa: int, retry_after: str | None = None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), self.pausa_max)
            except ValueError:
                pass
        # backoff exponencial com jitter — evita rajada sincronizada
        bruto = self.pausa_base * (2 ** (tentativa - 1))
        return min(bruto + random.uniform(0, bruto * 0.3), self.pausa_max)


class ColetorNavegador:
    """
    Playwright para páginas que só montam o preço via JavaScript.

    O navegador é caro: mantém UMA instância viva e abre contexto por chamada.
    Feche com `fechar()` quando terminar o lote.
    """

    def __init__(
        self,
        timeout_ms: int = 30_000,
        headless: bool = True,
        bloquear_imagem: bool = True,
    ) -> None:
        self.timeout_ms = timeout_ms
        self.headless = headless
        self.bloquear_imagem = bloquear_imagem
        self._pw = None
        self._navegador = None

    def _garantir_navegador(self):
        if self._navegador is not None:
            return self._navegador

        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._navegador = self._pw.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        return self._navegador

    def buscar(self, url: str, esperar_por: str | None = None) -> Resposta:
        navegador = self._garantir_navegador()
        contexto = navegador.new_context(
            user_agent=UA_PADRAO,
            viewport={"width": 1366, "height": 768},
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
        )

        if self.bloquear_imagem:
            contexto.route(
                "**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf}",
                lambda rota: rota.abort(),
            )

        pagina = contexto.new_page()
        try:
            pagina.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
            if esperar_por:
                try:
                    pagina.wait_for_selector(esperar_por, timeout=8_000)
                except Exception:
                    pass  # seletor ausente não invalida a página
            else:
                pagina.wait_for_timeout(1_500)  # deixa o JS de preço rodar

            html = pagina.content()
            status = 200
            return Resposta(
                url=pagina.url,
                html=html,
                status=status,
                origem="navegador",
                bloqueado=_detectar_bloqueio(html, status),
            )
        except Exception as exc:
            raise ColetaFalhou(f"navegador falhou em {url}: {exc}") from exc
        finally:
            contexto.close()
            pagina.close()

    def fechar(self) -> None:
        if self._navegador is not None:
            self._navegador.close()
            self._navegador = None
        if self._pw is not None:
            self._pw.stop()
            self._pw = None

    def __enter__(self) -> "ColetorNavegador":
        return self

    def __exit__(self, *exc: object) -> None:
        self.fechar()
