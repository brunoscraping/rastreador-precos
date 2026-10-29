"""
Notificação de alerta de preço.

Hoje suporta Telegram. O desenho é por interface (`Notificador`) para que
e-mail, WhatsApp ou webhook entrem depois sem tocar no motor.

Configuração por variável de ambiente, nunca por argumento de linha de
comando — argumento fica no histórico do shell e em `ps`, e o token do bot
é uma credencial.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

import requests


class Notificador(Protocol):
    """Contrato mínimo: recebe um alerta e entrega."""

    def enviar(self, alerta: "Alerta") -> bool: ...

    @property
    def configurado(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class Alerta:
    """Dados de um alerta de queda de preço."""

    produto_id: int
    titulo: str | None
    url: str
    preco_atual: Decimal
    preco_anterior: Decimal | None
    preco_alvo: Decimal | None
    moeda: str = "BRL"

    @property
    def queda(self) -> Decimal | None:
        if self.preco_anterior is None or self.preco_anterior == 0:
            return None
        return ((self.preco_atual - self.preco_anterior) / self.preco_anterior * 100).quantize(
            Decimal("0.01")
        )

    def formatar_valor(self, valor: Decimal) -> str:
        simbolo = {"BRL": "R$", "USD": "US$", "EUR": "€", "GBP": "£"}.get(self.moeda, self.moeda)
        texto = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{simbolo} {texto}"


class NotificadorTelegram:
    """
    Envia alerta via Bot API do Telegram.

    Credenciais (nunca hardcoded):
        TELEGRAM_BOT_TOKEN  — obtido com @BotFather
        TELEGRAM_CHAT_ID    — seu id de chat, descoberto com `rastreador telegram-id`
    """

    API = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        timeout: float = 15.0,
        silencioso: bool = False,
    ) -> None:
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        self.timeout = timeout
        self.silencioso = silencioso

    @property
    def configurado(self) -> bool:
        return bool(self.token and self.chat_id)

    # -- envio ------------------------------------------------------------

    def enviar(self, alerta: Alerta) -> bool:
        if not self.configurado:
            return False
        return self.enviar_texto(self._montar_mensagem(alerta))

    def enviar_texto(self, texto: str) -> bool:
        """Envio cru — usado também pelo comando de teste."""
        if not self.configurado:
            return False

        try:
            resp = requests.post(
                self.API.format(token=self.token),
                json={
                    "chat_id": self.chat_id,
                    "text": texto,
                    "parse_mode": "HTML",
                    "disable_notification": self.silencioso,
                    "disable_web_page_preview": False,
                },
                timeout=self.timeout,
            )
        except requests.RequestException:
            return False

        if resp.status_code != 200:
            return False

        try:
            return bool(resp.json().get("ok"))
        except ValueError:
            return False

    # -- formatação -------------------------------------------------------

    def _montar_mensagem(self, alerta: Alerta) -> str:
        titulo = alerta.titulo or f"Produto #{alerta.produto_id}"
        linhas = [
            f"🔔 <b>Queda de preço</b>",
            "",
            f"<b>{_escapar(titulo)}</b>",
            "",
            f"💰 Agora: <b>{alerta.formatar_valor(alerta.preco_atual)}</b>",
        ]

        if alerta.preco_anterior is not None and alerta.preco_anterior != alerta.preco_atual:
            linhas.append(f"📉 Antes: {alerta.formatar_valor(alerta.preco_anterior)}")

        queda = alerta.queda
        if queda is not None and queda < 0:
            linhas.append(f"⬇️ Variação: <b>{queda}%</b>")

        if alerta.preco_alvo is not None:
            linhas.append(f"🎯 Alvo: {alerta.formatar_valor(alerta.preco_alvo)}")

        linhas += ["", f'<a href="{_escapar(alerta.url)}">abrir produto</a>']
        return "\n".join(linhas)

    # -- descoberta de chat id -------------------------------------------

    @staticmethod
    def descobrir_chat_id(token: str, timeout: float = 15.0) -> list[dict]:
        """
        Lista os chats que já conversaram com o bot.

        O Telegram não expõe "qual é o meu id": é preciso enviar uma mensagem
        ao bot primeiro, e então ler o `getUpdates`. Esta função faz a leitura.
        """
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates", timeout=timeout
            )
            resp.raise_for_status()
            dados = resp.json()
        except (requests.RequestException, ValueError):
            return []

        if not dados.get("ok"):
            return []

        vistos: dict[int, dict] = {}
        for update in dados.get("result", []):
            msg = update.get("message") or update.get("channel_post") or {}
            chat = msg.get("chat") or {}
            if "id" in chat:
                vistos[chat["id"]] = {
                    "id": chat["id"],
                    "tipo": chat.get("type", "?"),
                    "nome": chat.get("title")
                    or " ".join(
                        filter(None, [chat.get("first_name"), chat.get("last_name")])
                    )
                    or chat.get("username")
                    or "(sem nome)",
                }
        return list(vistos.values())


def _escapar(texto: str) -> str:
    """Escapa os três caracteres que quebram o parse_mode=HTML do Telegram."""
    return texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class NotificadorMultiplo:
    """Dispara para vários destinos; sucesso se ao menos um entregar."""

    def __init__(self, destinos: list[Notificador]) -> None:
        self.destinos = destinos

    @property
    def configurado(self) -> bool:
        return any(d.configurado for d in self.destinos)

    def enviar(self, alerta: Alerta) -> bool:
        resultados = [d.enviar(alerta) for d in self.destinos if d.configurado]
        return any(resultados)


def notificador_padrao() -> Notificador:
    """
    Monta o notificador a partir do ambiente.

    Se as variáveis não existirem, devolve um notificador vazio — o motor
    continua funcionando e só não avisa. Falhar aqui derrubaria a coleta
    por causa de uma credencial ausente, que é o trade-off errado.
    """
    return NotificadorTelegram()
