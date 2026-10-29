"""
Persistência em SQLite.

Guarda produtos, o histórico de cada leitura e o estado do alerta.
Desenho: append-only nas leituras — nunca sobrescreve preço antigo,
porque o valor do produto está justamente na série histórica.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterator

ESQUEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS produto (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    url           TEXT    NOT NULL UNIQUE,
    titulo        TEXT,
    seletor_css   TEXT,
    preco_alvo    REAL,
    criado_em     TEXT    NOT NULL,
    ativo         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS leitura (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    produto_id    INTEGER NOT NULL REFERENCES produto(id) ON DELETE CASCADE,
    valor         REAL    NOT NULL,
    moeda         TEXT    NOT NULL DEFAULT 'BRL',
    lido_em       TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_leitura_produto ON leitura(produto_id, lido_em DESC);

CREATE TABLE IF NOT EXISTS alerta (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    produto_id    INTEGER NOT NULL REFERENCES produto(id) ON DELETE CASCADE,
    valor         REAL    NOT NULL,
    disparado_em  TEXT    NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class Produto:
    id: int
    url: str
    titulo: str | None
    seletor_css: str | None
    preco_alvo: float | None
    ativo: bool

    @property
    def seletores(self) -> list[str]:
        if not self.seletor_css:
            return []
        return [s.strip() for s in self.seletor_css.split(",") if s.strip()]


@dataclass(frozen=True, slots=True)
class Estatistica:
    """Resumo da série histórica de um produto."""

    produto_id: int
    titulo: str | None
    atual: Decimal
    minimo: Decimal
    maximo: Decimal
    media: Decimal
    variacao_pct: Decimal
    leituras: int
    moeda: str = "BRL"

    @property
    def esta_no_menor_preco(self) -> bool:
        return self.atual <= self.minimo


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Banco:
    """Acesso a dados. Uma instância por processo; o SQLite serializa o resto."""

    def __init__(self, caminho: Path | str) -> None:
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        with self._conexao() as con:
            con.executescript(ESQUEMA)

    @contextmanager
    def _conexao(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.caminho, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    # -- produtos ---------------------------------------------------------

    def adicionar_produto(
        self,
        url: str,
        titulo: str | None = None,
        seletor_css: str | None = None,
        preco_alvo: float | None = None,
    ) -> tuple[int, bool]:
        """
        Insere ou atualiza um produto.

        Retorna (id, criado). `criado=False` significa que a URL já existia
        e os campos informados foram atualizados.
        """
        with self._conexao() as con:
            linha = con.execute("SELECT id FROM produto WHERE url = ?", (url,)).fetchone()
            if linha:
                con.execute(
                    """UPDATE produto
                          SET titulo      = COALESCE(?, titulo),
                              seletor_css = COALESCE(?, seletor_css),
                              preco_alvo  = COALESCE(?, preco_alvo),
                              ativo       = 1
                        WHERE id = ?""",
                    (titulo, seletor_css, preco_alvo, linha["id"]),
                )
                return int(linha["id"]), False

            cur = con.execute(
                """INSERT INTO produto (url, titulo, seletor_css, preco_alvo, criado_em)
                   VALUES (?, ?, ?, ?, ?)""",
                (url, titulo, seletor_css, preco_alvo, _agora()),
            )
            return int(cur.lastrowid), True

    def listar_produtos(self, apenas_ativos: bool = True) -> list[Produto]:
        sql = "SELECT * FROM produto"
        if apenas_ativos:
            sql += " WHERE ativo = 1"
        sql += " ORDER BY id"

        with self._conexao() as con:
            return [self._linha_para_produto(r) for r in con.execute(sql)]

    def obter_produto(self, produto_id: int) -> Produto | None:
        with self._conexao() as con:
            linha = con.execute("SELECT * FROM produto WHERE id = ?", (produto_id,)).fetchone()
            return self._linha_para_produto(linha) if linha else None

    def remover_produto(self, produto_id: int) -> bool:
        """Desativa em vez de apagar — preserva o histórico."""
        with self._conexao() as con:
            cur = con.execute("UPDATE produto SET ativo = 0 WHERE id = ?", (produto_id,))
            return cur.rowcount > 0

    @staticmethod
    def _linha_para_produto(linha: sqlite3.Row) -> Produto:
        return Produto(
            id=int(linha["id"]),
            url=str(linha["url"]),
            titulo=linha["titulo"],
            seletor_css=linha["seletor_css"],
            preco_alvo=linha["preco_alvo"],
            ativo=bool(linha["ativo"]),
        )

    # -- leituras ---------------------------------------------------------

    def registrar_leitura(self, produto_id: int, valor: Decimal, moeda: str = "BRL") -> int:
        with self._conexao() as con:
            cur = con.execute(
                "INSERT INTO leitura (produto_id, valor, moeda, lido_em) VALUES (?, ?, ?, ?)",
                (produto_id, float(valor), moeda, _agora()),
            )
            return int(cur.lastrowid)

    def historico(self, produto_id: int, limite: int = 100) -> list[sqlite3.Row]:
        """
        Leituras em ordem cronologica reversa.

        O desempate por `id DESC` e obrigatorio: `lido_em` tem resolucao de
        segundo, e duas leituras no mesmo segundo empatam. Sem o desempate o
        SQLite devolve ordem indeterminada, quebrando estatistica e deteccao
        de menor preco.
        """
        with self._conexao() as con:
            return list(
                con.execute(
                    """SELECT valor, moeda, lido_em FROM leitura
                        WHERE produto_id = ?
                        ORDER BY lido_em DESC, id DESC LIMIT ?""",
                    (produto_id, limite),
                )
            )

    def estatistica(self, produto_id: int) -> Estatistica | None:
        with self._conexao() as con:
            linha = con.execute(
                """SELECT COUNT(*)                AS n,
                          MIN(valor)              AS minimo,
                          MAX(valor)              AS maximo,
                          AVG(valor)              AS media
                     FROM leitura WHERE produto_id = ?""",
                (produto_id,),
            ).fetchone()

            if not linha or not linha["n"]:
                return None

            ultima = con.execute(
                """SELECT valor, moeda FROM leitura
                    WHERE produto_id = ? ORDER BY lido_em DESC, id DESC LIMIT 1""",
                (produto_id,),
            ).fetchone()

            # moeda dominante da serie — a ultima leitura manda
            moeda = (ultima["moeda"] if ultima and ultima["moeda"] else "BRL")

            produto = con.execute(
                "SELECT titulo FROM produto WHERE id = ?", (produto_id,)
            ).fetchone()

            # primeira leitura registrada — base da variacao percentual
            primeira_linha = con.execute(
                """SELECT valor FROM leitura
                    WHERE produto_id = ? ORDER BY lido_em ASC, id ASC LIMIT 1""",
                (produto_id,),
            ).fetchone()

        atual = Decimal(str(ultima["valor"]))
        primeira = Decimal(str(primeira_linha["valor"])) if primeira_linha else atual

        variacao = Decimal(0)
        if primeira:
            variacao = ((atual - primeira) / primeira * 100).quantize(Decimal("0.01"))

        return Estatistica(
            produto_id=produto_id,
            titulo=produto["titulo"] if produto else None,
            atual=atual,
            minimo=Decimal(str(linha["minimo"])),
            maximo=Decimal(str(linha["maximo"])),
            media=Decimal(str(linha["media"])).quantize(Decimal("0.01")),
            variacao_pct=variacao,
            leituras=int(linha["n"]),
            moeda=moeda,
        )

    # -- alertas ----------------------------------------------------------

    def registrar_alerta(self, produto_id: int, valor: Decimal) -> None:
        with self._conexao() as con:
            con.execute(
                "INSERT INTO alerta (produto_id, valor, disparado_em) VALUES (?, ?, ?)",
                (produto_id, float(valor), _agora()),
            )

    def ultimo_alerta(self, produto_id: int) -> sqlite3.Row | None:
        with self._conexao() as con:
            return con.execute(
                """SELECT valor, disparado_em FROM alerta
                    WHERE produto_id = ? ORDER BY disparado_em DESC, id DESC LIMIT 1""",
                (produto_id,),
            ).fetchone()
