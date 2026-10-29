"""Testes da camada de persistência."""

from __future__ import annotations

from decimal import Decimal

import pytest

from rastreador.banco import Banco


@pytest.fixture()
def banco(tmp_path) -> Banco:
    return Banco(tmp_path / "teste.db")


def test_adicionar_produto_novo(banco: Banco) -> None:
    pid, criado = banco.adicionar_produto("https://loja.com/p/1", titulo="Fone")
    assert criado is True
    assert pid > 0


def test_adicionar_produto_duplicado_atualiza(banco: Banco) -> None:
    pid1, criado1 = banco.adicionar_produto("https://loja.com/p/1", titulo="Fone")
    pid2, criado2 = banco.adicionar_produto("https://loja.com/p/1", preco_alvo=99.9)

    assert criado1 is True
    assert criado2 is False
    assert pid1 == pid2

    produto = banco.obter_produto(pid1)
    assert produto is not None
    assert produto.titulo == "Fone"          # preservado
    assert produto.preco_alvo == 99.9        # atualizado


def test_listar_apenas_ativos(banco: Banco) -> None:
    pid, _ = banco.adicionar_produto("https://loja.com/a")
    banco.adicionar_produto("https://loja.com/b")

    assert len(banco.listar_produtos()) == 2

    banco.remover_produto(pid)
    assert len(banco.listar_produtos()) == 1
    assert len(banco.listar_produtos(apenas_ativos=False)) == 2


def test_remover_preserva_historico(banco: Banco) -> None:
    pid, _ = banco.adicionar_produto("https://loja.com/a")
    banco.registrar_leitura(pid, Decimal("100.00"))
    banco.remover_produto(pid)

    # produto some da lista ativa, mas as leituras continuam
    assert len(banco.listar_produtos()) == 0
    assert len(banco.historico(pid)) == 1


def test_historico_ordem_decrescente(banco: Banco) -> None:
    pid, _ = banco.adicionar_produto("https://loja.com/a")
    for valor in ("100.00", "90.00", "80.00"):
        banco.registrar_leitura(pid, Decimal(valor))

    leituras = banco.historico(pid)
    assert len(leituras) == 3
    assert Decimal(str(leituras[0]["valor"])) == Decimal("80.00")  # mais recente primeiro


def test_estatistica(banco: Banco) -> None:
    pid, _ = banco.adicionar_produto("https://loja.com/a", titulo="Produto")
    for valor in ("100.00", "80.00", "120.00"):
        banco.registrar_leitura(pid, Decimal(valor))

    s = banco.estatistica(pid)
    assert s is not None
    assert s.atual == Decimal("120.00")
    assert s.minimo == Decimal("80.00")
    assert s.maximo == Decimal("120.00")
    assert s.media == Decimal("100.00")
    assert s.leituras == 3


def test_estatistica_sem_leitura(banco: Banco) -> None:
    pid, _ = banco.adicionar_produto("https://loja.com/a")
    assert banco.estatistica(pid) is None


def test_variacao_percentual(banco: Banco) -> None:
    pid, _ = banco.adicionar_produto("https://loja.com/a")
    banco.registrar_leitura(pid, Decimal("100.00"))
    banco.registrar_leitura(pid, Decimal("80.00"))

    s = banco.estatistica(pid)
    assert s is not None
    assert s.variacao_pct == Decimal("-20.00")


def test_menor_preco_detectado(banco: Banco) -> None:
    pid, _ = banco.adicionar_produto("https://loja.com/a")
    banco.registrar_leitura(pid, Decimal("100.00"))
    banco.registrar_leitura(pid, Decimal("80.00"))

    s = banco.estatistica(pid)
    assert s is not None
    assert s.esta_no_menor_preco is True


def test_alerta_registrado(banco: Banco) -> None:
    pid, _ = banco.adicionar_produto("https://loja.com/a")
    assert banco.ultimo_alerta(pid) is None

    banco.registrar_alerta(pid, Decimal("79.90"))
    ultimo = banco.ultimo_alerta(pid)
    assert ultimo is not None
    assert Decimal(str(ultimo["valor"])) == Decimal("79.90")
