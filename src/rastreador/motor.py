"""
Motor de monitoramento: orquestra coleta, extração, persistência e alerta.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .banco import Banco, Estatistica, Produto
from .coletor import ColetorNavegador, ColetorSimples, ColetaFalhou, Resposta
from .extrator import Preco, extrair_preco, extrair_titulo
from .notificador import Alerta, Notificador, notificador_padrao


@dataclass(slots=True)
class Resultado:
    """O que aconteceu numa verificação de um produto."""

    produto_id: int
    url: str
    titulo: str | None
    preco: Preco | None
    erro: str | None
    precisa_alerta: bool = False
    estatistica: Estatistica | None = None
    notificado: bool = False

    @property
    def sucesso(self) -> bool:
        return self.preco is not None and self.erro is None


class Motor:
    """
    Executa o ciclo: buscar -> extrair -> gravar -> comparar alvo.

    O navegador só é acionado quando o HTML simples não entrega preço,
    mantendo o custo baixo no caso comum.
    """

    def __init__(
        self,
        banco: Banco,
        coletor_simples: ColetorSimples | None = None,
        usar_navegador: bool = True,
        pausa_entre: float = 1.5,
        notificador: Notificador | None = None,
    ) -> None:
        self.banco = banco
        self.simples = coletor_simples or ColetorSimples()
        self.usar_navegador = usar_navegador
        self.pausa_entre = pausa_entre
        self.notificador = notificador if notificador is not None else notificador_padrao()
        self._navegador: ColetorNavegador | None = None

    # -- ciclo ------------------------------------------------------------

    def verificar(self, produto: Produto) -> Resultado:
        resultado = Resultado(
            produto_id=produto.id,
            url=produto.url,
            titulo=produto.titulo,
            preco=None,
            erro=None,
        )

        resposta: Resposta | None = None

        try:
            resposta = self.simples.buscar(produto.url)
        except ColetaFalhou as exc:
            resultado.erro = f"http: {exc}"

        # decide se vale acionar o navegador
        precisa_navegador = (
            self.usar_navegador
            and (
                resposta is None
                or resposta.bloqueado
                or extrair_preco(resposta.html, produto.seletores) is None
            )
        )

        if precisa_navegador:
            try:
                resposta = self._obter_navegador().buscar(produto.url)
                resultado.erro = None
            except ColetaFalhou as exc:
                resultado.erro = f"navegador: {exc}"
                if resposta is None:
                    return resultado

        if resposta is None:
            return resultado

        if resposta.bloqueado and not resposta.html:
            resultado.erro = "página bloqueada (captcha ou proteção anti-bot)"
            return resultado

        if not resposta.parece_html:
            resultado.erro = (
                f"resposta não é HTML ({len(resposta.html)} bytes) — "
                "possível compressão não descomprimida ou bloqueio silencioso"
            )
            return resultado

        preco = extrair_preco(resposta.html, produto.seletores)
        if preco is None:
            resultado.erro = "preço não encontrado na página"
            return resultado

        resultado.preco = preco
        if not produto.titulo:
            resultado.titulo = extrair_titulo(resposta.html)

        # o preco anterior precisa ser lido ANTES de gravar a leitura nova,
        # senao o alerta perde a referencia e nao calcula a queda
        leitura_anterior = self.banco.historico(produto.id, limite=1)
        preco_anterior = (
            Decimal(str(leitura_anterior[0]["valor"])) if leitura_anterior else None
        )

        self.banco.registrar_leitura(produto.id, preco.valor, preco.moeda)
        resultado.estatistica = self.banco.estatistica(produto.id)

        resultado.precisa_alerta = self._avaliar_alerta(produto, preco)

        if resultado.precisa_alerta:
            resultado.notificado = self._notificar(
                produto, preco, preco_anterior, resultado.titulo
            )

        return resultado

    def _notificar(
        self,
        produto: Produto,
        preco: Preco,
        preco_anterior: Decimal | None,
        titulo: str | None,
    ) -> bool:
        """
        Entrega o alerta pelo notificador configurado.

        Falha de rede no envio nao propaga: o dado ja esta gravado e o
        alerta ja esta registrado. Derrubar a coleta por causa de um
        Telegram fora do ar seria o trade-off errado.
        """
        if self.notificador is None or not self.notificador.configurado:
            return False

        alerta = Alerta(
            produto_id=produto.id,
            titulo=titulo or produto.titulo,
            url=produto.url,
            preco_atual=preco.valor,
            preco_anterior=preco_anterior,
            preco_alvo=Decimal(str(produto.preco_alvo)) if produto.preco_alvo else None,
            moeda=preco.moeda,
        )

        try:
            return self.notificador.enviar(alerta)
        except Exception:
            return False

    def _avaliar_alerta(self, produto: Produto, preco: Preco) -> bool:
        """
        Dispara alerta quando o preço cruza o alvo.

        Regra anti-spam: não repete o alerta se o último disparo foi
        com o mesmo valor ou menor.
        """
        if produto.preco_alvo is None:
            return False

        alvo = Decimal(str(produto.preco_alvo))
        if preco.valor > alvo:
            return False

        anterior = self.banco.ultimo_alerta(produto.id)
        if anterior is not None and Decimal(str(anterior["valor"])) <= preco.valor:
            return False

        self.banco.registrar_alerta(produto.id, preco.valor)
        return True

    def verificar_todos(self, produtos: list[Produto]) -> list[Resultado]:
        resultados: list[Resultado] = []
        for i, produto in enumerate(produtos):
            resultados.append(self.verificar(produto))
            if i < len(produtos) - 1:
                import time

                time.sleep(self.pausa_entre)
        return resultados

    # -- navegador --------------------------------------------------------

    def _obter_navegador(self) -> ColetorNavegador:
        if self._navegador is None:
            self._navegador = ColetorNavegador()
        return self._navegador

    def fechar(self) -> None:
        if self._navegador is not None:
            self._navegador.fechar()
            self._navegador = None

    def __enter__(self) -> "Motor":
        return self

    def __exit__(self, *exc: object) -> None:
        self.fechar()


def banco_padrao() -> Banco:
    """Local default do banco: %USERPROFILE%/.rastreador-precos/precos.db"""
    raiz = Path.home() / ".rastreador-precos"
    return Banco(raiz / "precos.db")
