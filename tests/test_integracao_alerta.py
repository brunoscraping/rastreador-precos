"""
Testes de integracao motor <-> notificador.

O motor nao deve notificar quando:
  - o preco esta acima do alvo
  - o notificador nao esta configurado
  - o envio falha (nao pode derrubar a coleta)
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from rastreador.banco import Banco
from rastreador.coletor import ColetaFalhou, Resposta
from rastreador.motor import Motor
from rastreador.notificador import Alerta

# ---------------------------------------------------------------------------
# Dublês
# ---------------------------------------------------------------------------

HTML = """
<html><head><title>Produto Teste</title>
<script type="application/ld+json">
{"@type":"Product","name":"Produto Teste","offers":{"@type":"Offer","price":"%s"}}
</script></head><body></body></html>
"""


class ColetorFalso:
    """Devolve um HTML fixo, com o preco controlado pelo teste."""

    def __init__(self, preco: str) -> None:
        self.preco = preco
        self.chamadas = 0

    def buscar(self, url: str) -> Resposta:
        self.chamadas += 1
        return Resposta(
            url=url,
            html=HTML % self.preco,
            status=200,
            origem="http",
        )


class ColetorQuebrado:
    def buscar(self, url: str) -> Resposta:
        raise ColetaFalhou("rede fora")


class NotificadorFalso:
    """Registra os alertas recebidos; sucesso controlado pelo teste."""

    def __init__(self, configurado: bool = True, sucesso: bool = True, explode: bool = False) -> None:
        self._configurado = configurado
        self.sucesso = sucesso
        self.explode = explode
        self.recebidos: list[Alerta] = []

    @property
    def configurado(self) -> bool:
        return self._configurado

    def enviar(self, alerta: Alerta) -> bool:
        if self.explode:
            raise RuntimeError("telegram fora do ar")
        self.recebidos.append(alerta)
        return self.sucesso


@pytest.fixture()
def banco(tmp_path) -> Banco:
    return Banco(tmp_path / "t.db")


# ---------------------------------------------------------------------------
# Notificacao dispara
# ---------------------------------------------------------------------------


def test_notifica_quando_cruza_alvo(banco: Banco) -> None:
    pid, _ = banco.adicionar_produto("https://x.com/p", preco_alvo=100.0)
    produto = banco.obter_produto(pid)
    assert produto is not None

    notif = NotificadorFalso()
    motor = Motor(banco, coletor_simples=ColetorFalso("80.00"), usar_navegador=False, notificador=notif)
    resultado = motor.verificar(produto)

    assert resultado.preciso if False else resultado.sucesso
    assert resultado.precisa_alerta is True
    assert resultado.notificado is True
    assert len(notif.recebidos) == 1
    assert notif.recebidos[0].preco_atual == Decimal("80.00")


def test_alerta_carrega_preco_anterior(banco: Banco) -> None:
    """A variacao percentual depende do preco anterior estar correto."""
    pid, _ = banco.adicionar_produto("https://x.com/p", preco_alvo=100.0)
    banco.registrar_leitura(pid, Decimal("120.00"))  # leitura antiga

    produto = banco.obter_produto(pid)
    assert produto is not None

    notif = NotificadorFalso()
    motor = Motor(banco, coletor_simples=ColetorFalso("90.00"), usar_navegador=False, notificador=notif)
    motor.verificar(produto)

    assert len(notif.recebidos) == 1
    alerta = notif.recebidos[0]
    assert alerta.preco_anterior == Decimal("120.00")
    assert alerta.preco_atual == Decimal("90.00")
    assert alerta.queda == Decimal("-25.00")


# ---------------------------------------------------------------------------
# Notificacao NAO dispara
# ---------------------------------------------------------------------------


def test_nao_notifica_acima_do_alvo(banco: Banco) -> None:
    pid, _ = banco.adicionar_produto("https://x.com/p", preco_alvo=50.0)
    produto = banco.obter_produto(pid)
    assert produto is not None

    notif = NotificadorFalso()
    motor = Motor(banco, coletor_simples=ColetorFalso("80.00"), usar_navegador=False, notificador=notif)
    resultado = motor.verificar(produto)

    assert resultado.precisa_alerta is False
    assert notif.recebidos == []


def test_nao_notifica_sem_alvo_definido(banco: Banco) -> None:
    pid, _ = banco.adicionar_produto("https://x.com/p")  # sem preco_alvo
    produto = banco.obter_produto(pid)
    assert produto is not None

    notif = NotificadorFalso()
    motor = Motor(banco, coletor_simples=ColetorFalso("10.00"), usar_navegador=False, notificador=notif)
    resultado = motor.verificar(produto)

    assert resultado.precisa_alerta is False
    assert notif.recebidos == []


def test_nao_repete_alerta_mesmo_preco(banco: Banco) -> None:
    """Anti-spam: segundo check no mesmo valor nao notifica de novo."""
    pid, _ = banco.adicionar_produto("https://x.com/p", preco_alvo=100.0)
    produto = banco.obter_produto(pid)
    assert produto is not None

    notif = NotificadorFalso()
    coletor = ColetorFalso("80.00")
    motor = Motor(banco, coletor_simples=coletor, usar_navegador=False, notificador=notif)

    motor.verificar(produto)
    motor.verificar(produto)

    assert len(notif.recebidos) == 1, "alerta repetido para o mesmo preco"


def test_notifica_de_novo_se_cair_mais(banco: Banco) -> None:
    pid, _ = banco.adicionar_produto("https://x.com/p", preco_alvo=100.0)
    produto = banco.obter_produto(pid)
    assert produto is not None

    notif = NotificadorFalso()
    coletor = ColetorFalso("80.00")
    motor = Motor(banco, coletor_simples=coletor, usar_navegador=False, notificador=notif)

    motor.verificar(produto)
    coletor.preco = "70.00"  # caiu mais
    motor.verificar(produto)

    assert len(notif.recebidos) == 2
    assert notif.recebidos[1].preco_atual == Decimal("70.00")


# ---------------------------------------------------------------------------
# Robustez
# ---------------------------------------------------------------------------


def test_notificador_nao_configurado_nao_quebra(banco: Banco) -> None:
    pid, _ = banco.adicionar_produto("https://x.com/p", preco_alvo=100.0)
    produto = banco.obter_produto(pid)
    assert produto is not None

    notif = NotificadorFalso(configurado=False)
    motor = Motor(banco, coletor_simples=ColetorFalso("80.00"), usar_navegador=False, notificador=notif)
    resultado = motor.verificar(produto)

    # alerta registrado no banco, mas nao enviado
    assert resultado.precisa_alerta is True
    assert resultado.notificado is False
    assert notif.recebidos == []


def test_falha_no_envio_nao_derruba_coleta(banco: Banco) -> None:
    """Telegram fora do ar nao pode impedir a leitura de ser gravada."""
    pid, _ = banco.adicionar_produto("https://x.com/p", preco_alvo=100.0)
    produto = banco.obter_produto(pid)
    assert produto is not None

    notif = NotificadorFalso(explode=True)
    motor = Motor(banco, coletor_simples=ColetorFalso("80.00"), usar_navegador=False, notificador=notif)
    resultado = motor.verificar(produto)

    assert resultado.sucesso is True
    assert resultado.notificado is False
    # a leitura FOI gravada apesar da falha do notificador
    assert len(banco.historico(pid)) == 1


def test_sem_notificador_nao_quebra(banco: Banco) -> None:
    pid, _ = banco.adicionar_produto("https://x.com/p", preco_alvo=100.0)
    produto = banco.obter_produto(pid)
    assert produto is not None

    # notificador explicito vazio
    motor = Motor(
        banco,
        coletor_simples=ColetorFalso("80.00"),
        usar_navegador=False,
        notificador=NotificadorFalso(configurado=False),
    )
    resultado = motor.verificar(produto)
    assert resultado.sucesso is True
    assert resultado.precisa_alerta is True
