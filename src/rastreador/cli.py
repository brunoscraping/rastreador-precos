"""
Interface de linha de comando.

Uso:
    rastreador add  <url> [--alvo 199.90] [--seletor ".preco"] [--titulo "..."]
    rastreador list
    rastreador check [ID...] [--sem-navegador]
    rastreador history <ID> [--limit 30]
    rastreador stats  [ID...]
    rastreador rm <ID>
"""

from __future__ import annotations

import sys
from decimal import Decimal

import click

from .banco import Banco
from .extrator import Preco
from .motor import Motor, banco_padrao
from .notificador import NotificadorTelegram


def _preparar_saida() -> None:
    """
    Garante que acentos e simbolos cheguem ao terminal.

    No Windows, um processo sem console interativo (Agendador de Tarefas,
    redirecionamento para arquivo) usa cp1252 por padrao. Nesse encoding
    '✓', '£' e '—' nao existem, e o click.echo derruba o programa com
    UnicodeEncodeError — o pior tipo de falha, porque so aparece quando
    ninguem esta olhando.

    A ordem importa: tenta UTF-8, e se o console nao aceitar, cai para
    errors='replace' em vez de explodir. Perder um simbolo e aceitavel;
    perder a coleta inteira nao e.
    """
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            # fluxo redirecionado ou ambiente sem suporte — segue como esta
            pass


_preparar_saida()

# ---------------------------------------------------------------------------
# Apresentação
# ---------------------------------------------------------------------------

VERDE = "\033[92m"
VERMELHO = "\033[91m"
AMARELO = "\033[93m"
CIANO = "\033[96m"
NEGRITO = "\033[1m"
CINZA = "\033[90m"
FIM = "\033[0m"


def _simbolos_seguros(texto: str) -> str:
    """
    Substitui simbolos que o encoding de saida nao suporta.

    Chamado apenas quando o terminal nao e UTF-8 — mantem a saida legivel
    em vez de imprimir '?' ou quebrar.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        texto.encode(encoding)
        return texto
    except (UnicodeEncodeError, LookupError):
        return (
            texto.replace("✓", "OK")
            .replace("✗", "X")
            .replace("★", "*")
            .replace("←", "<-")
            .replace("→", "->")
            .replace("⬇", "v")
            .replace("⬆", "^")
            .replace("—", "-")
            .replace("£", "GBP")
            .replace("€", "EUR")
        )


def _echo(texto: str = "") -> None:
    """Saida segura: nunca derruba o processo por causa de um simbolo."""
    click.echo(_simbolos_seguros(texto))


def _cor(texto: str, cor: str) -> str:
    if not sys.stdout.isatty():
        return texto
    return f"{cor}{texto}{FIM}"


def _formata(valor: Decimal, moeda: str = "BRL") -> str:
    simbolo = {"BRL": "R$", "USD": "US$", "EUR": "€", "GBP": "£"}.get(moeda, moeda)
    texto = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{simbolo} {texto}"


def _seta(variacao: Decimal) -> str:
    if variacao > 0:
        return _cor(f"+{variacao}%", VERMELHO)
    if variacao < 0:
        return _cor(f"{variacao}%", VERDE)
    return _cor("0%", CINZA)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option("1.0.0", prog_name="rastreador")
def cli() -> None:
    """
    Rastreador de preços — monitora páginas de produto e avisa quando o preço cai.

    O histórico fica em ~/.rastreador-precos/precos.db
    """


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("url")
@click.option("--alvo", type=float, default=None, help="Preço alvo que dispara o alerta.")
@click.option("--seletor", default=None, help="Seletor CSS do preço (separe vários por vírgula).")
@click.option("--titulo", default=None, help="Nome do produto (senão é lido da página).")
def add(url: str, alvo: float | None, seletor: str | None, titulo: str | None) -> None:
    """Adiciona um produto para monitorar."""
    banco = banco_padrao()
    produto_id, criado = banco.adicionar_produto(url, titulo, seletor, alvo)

    if criado:
        click.echo(_simbolos_seguros(f"{_cor('✓', VERDE)} produto #{produto_id} adicionado"))
    else:
        click.echo(_simbolos_seguros(f"{_cor('•', AMARELO)} produto #{produto_id} já existia — dados atualizados"))

    if alvo is not None:
        click.echo(_simbolos_seguros(f"  alerta quando ficar abaixo de {_formata(Decimal(str(alvo)))}"))
    click.echo(_simbolos_seguros(f"  {_cor('rode:', CINZA)} rastreador check {produto_id}"))


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@cli.command(name="list")
def listar() -> None:
    """Lista os produtos monitorados."""
    banco = banco_padrao()
    produtos = banco.listar_produtos()

    if not produtos:
        click.echo(_simbolos_seguros("nenhum produto. use: rastreador add <url>"))
        return

    click.echo()
    click.echo(_simbolos_seguros(f"{_cor('ID', NEGRITO)}  {_cor('PREÇO', NEGRITO):>14}  {_cor('ALVO', NEGRITO):>12}  PRODUTO"))
    click.echo(_simbolos_seguros(_cor("─" * 78, CINZA)))

    for p in produtos:
        stats = banco.estatistica(p.id)
        if stats:
            preco_txt = _formata(stats.atual, stats.moeda)
            if stats.esta_no_menor_preco and stats.leituras > 1:
                preco_txt = _cor(preco_txt, VERDE)
        else:
            preco_txt = _cor("—", CINZA)

        alvo_txt = _formata(Decimal(str(p.preco_alvo))) if p.preco_alvo else _cor("—", CINZA)
        nome = (p.titulo or p.url)[:38]

        click.echo(_simbolos_seguros(f"{p.id:>3}  {preco_txt:>22}  {alvo_txt:>20}  {nome}"))

    click.echo()


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("ids", nargs=-1, type=int)
@click.option("--sem-navegador", is_flag=True, help="Só HTTP simples (mais rápido, menos eficaz).")
@click.option("--pausa", type=float, default=1.5, help="Segundos entre produtos.")
def check(ids: tuple[int, ...], sem_navegador: bool, pausa: float) -> None:
    """Verifica o preço agora. Sem IDs, verifica todos."""
    banco = banco_padrao()

    if ids:
        produtos = [p for i in ids if (p := banco.obter_produto(i)) is not None]
        if not produtos:
            click.echo(_simbolos_seguros("nenhum produto encontrado com esses IDs"), err=True)
            raise SystemExit(1)
    else:
        produtos = banco.listar_produtos()
        if not produtos:
            click.echo(_simbolos_seguros("nada para verificar. use: rastreador add <url>"))
            return

    click.echo(_simbolos_seguros(f"verificando {len(produtos)} produto(s)...\n"))

    with Motor(banco, usar_navegador=not sem_navegador, pausa_entre=pausa) as motor:
        for produto in produtos:
            resultado = motor.verificar(produto)
            nome = (resultado.titulo or produto.url)[:50]

            if resultado.sucesso and resultado.preco:
                preco = resultado.preco
                linha = f"  {_cor('✓', VERDE)} #{produto.id} {nome}\n      {_formata(preco.valor, preco.moeda)}"

                if resultado.estatistica and resultado.estatistica.leituras > 1:
                    linha += f"  {_seta(resultado.estatistica.variacao_pct)}"
                    if resultado.estatistica.esta_no_menor_preco:
                        linha += f"  {_cor('← menor preço já visto', VERDE)}"

                click.echo(_simbolos_seguros(linha))

                if resultado.precisa_alerta:
                    click.echo(_simbolos_seguros(f"      {_cor('★ ALVO ATINGIDO', AMARELO)}"))
            else:
                click.echo(_simbolos_seguros(f"  {_cor('✗', VERMELHO)} #{produto.id} {nome}"))
                click.echo(_simbolos_seguros(f"      {_cor(resultado.erro or 'falha desconhecida', CINZA)}"))

            click.echo()


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("produto_id", type=int)
@click.option("--limit", type=int, default=30, help="Quantas leituras mostrar.")
def history(produto_id: int, limit: int) -> None:
    """Mostra o histórico de preço de um produto."""
    banco = banco_padrao()
    produto = banco.obter_produto(produto_id)

    if produto is None:
        click.echo(_simbolos_seguros(f"produto #{produto_id} não existe"), err=True)
        raise SystemExit(1)

    leituras = banco.historico(produto_id, limite=limit)
    if not leituras:
        click.echo(_simbolos_seguros("sem leituras ainda. rode: rastreador check"))
        return

    click.echo()
    click.echo(_simbolos_seguros(f"{_cor(produto.titulo or produto.url, NEGRITO)}"))
    click.echo(_simbolos_seguros(_cor("─" * 60, CINZA)))

    for linha in leituras:
        valor = Decimal(str(linha["valor"]))
        quando = str(linha["lido_em"]).replace("T", " ")[:19]
        click.echo(_simbolos_seguros(f"  {quando}   {_formata(valor, linha['moeda'])}"))

    click.echo()


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("ids", nargs=-1, type=int)
def stats(ids: tuple[int, ...]) -> None:
    """Estatísticas: mínimo, máximo, média e variação."""
    banco = banco_padrao()

    if ids:
        produtos = [p for i in ids if (p := banco.obter_produto(i)) is not None]
    else:
        produtos = banco.listar_produtos()

    if not produtos:
        click.echo(_simbolos_seguros("nada para mostrar"))
        return

    for produto in produtos:
        s = banco.estatistica(produto.id)
        if s is None:
            click.echo(_simbolos_seguros(f"#{produto.id} {produto.titulo or produto.url} — sem leituras"))
            continue

        click.echo()
        click.echo(_simbolos_seguros(f"{_cor(f'#{produto.id} ' + (s.titulo or produto.url), NEGRITO)}"))
        click.echo(_simbolos_seguros(_cor("─" * 60, CINZA)))
        click.echo(_simbolos_seguros(f"  atual      {_formata(s.atual, s.moeda):>16}   {_seta(s.variacao_pct)} desde o início"))
        click.echo(_simbolos_seguros(f"  mínimo     {_formata(s.minimo, s.moeda):>16}"))
        click.echo(_simbolos_seguros(f"  máximo     {_formata(s.maximo, s.moeda):>16}"))
        click.echo(_simbolos_seguros(f"  média      {_formata(s.media, s.moeda):>16}"))
        click.echo(_simbolos_seguros(f"  leituras   {s.leituras:>16}"))

        if s.esta_no_menor_preco and s.leituras > 1:
            click.echo(_simbolos_seguros(f"  {_cor('→ está no menor preço já registrado', VERDE)}"))

    click.echo()


# ---------------------------------------------------------------------------
# rm
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("produto_id", type=int)
def rm(produto_id: int) -> None:
    """Para de monitorar um produto (o histórico é preservado)."""
    banco = banco_padrao()
    if banco.remover_produto(produto_id):
        click.echo(_simbolos_seguros(f"{_cor('✓', VERDE)} produto #{produto_id} desativado"))
    else:
        click.echo(_simbolos_seguros(f"produto #{produto_id} não encontrado"), err=True)
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# telegram — configuração e teste do canal de alerta
# ---------------------------------------------------------------------------


@cli.group()
def telegram() -> None:
    """Configura o alerta no Telegram."""


@telegram.command(name="id")
@click.option("--token", default=None, help="Token do bot (senão lê TELEGRAM_BOT_TOKEN).")
def telegram_id(token: str | None) -> None:
    """
    Descobre o seu chat ID.

    Envie qualquer mensagem para o seu bot no Telegram ANTES de rodar isto —
    o Telegram só revela chats que já interagiram com o bot.
    """
    import os

    token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        click.echo(_simbolos_seguros(_cor("token ausente.", VERMELHO)))
        click.echo(_simbolos_seguros("  defina TELEGRAM_BOT_TOKEN ou passe --token"))
        raise SystemExit(1)

    chats = NotificadorTelegram.descobrir_chat_id(token)

    if not chats:
        click.echo(_simbolos_seguros(_cor("nenhum chat encontrado.", AMARELO)))
        click.echo()
        click.echo(_simbolos_seguros("  causas comuns:"))
        click.echo(_simbolos_seguros("    1. você ainda não mandou mensagem para o bot"))
        click.echo(_simbolos_seguros(f"       abra: https://t.me/SEU_BOT  e envie um oi"))
        click.echo(_simbolos_seguros("    2. o token está errado"))
        raise SystemExit(1)

    click.echo(_simbolos_seguros(f"\n{_cor('chats encontrados:', NEGRITO)}\n"))
    for c in chats:
        click.echo(_simbolos_seguros(f"  {_cor(str(c['id']), CIANO):>18}   {c['tipo']:<10} {c['nome']}"))

    click.echo()
    click.echo(_simbolos_seguros(f"  {_cor('configure com:', CINZA)}"))
    click.echo(_simbolos_seguros(f'    $env:TELEGRAM_CHAT_ID = "{chats[0]["id"]}"'))
    click.echo()


@telegram.command(name="test")
def telegram_test() -> None:
    """Envia uma mensagem de teste para confirmar que está funcionando."""
    notif = NotificadorTelegram()

    if not notif.configurado:
        click.echo(_simbolos_seguros(_cor("Telegram não configurado.", VERMELHO)))
        click.echo()
        click.echo(_simbolos_seguros("  defina as duas variáveis:"))
        click.echo(_simbolos_seguros('    $env:TELEGRAM_BOT_TOKEN = "123456:ABC..."'))
        click.echo(_simbolos_seguros('    $env:TELEGRAM_CHAT_ID   = "123456789"'))
        click.echo()
        click.echo(_simbolos_seguros(f"  {_cor('não sabe o chat id?', CINZA)} rastreador telegram id"))
        raise SystemExit(1)

    click.echo(_simbolos_seguros("enviando mensagem de teste..."))

    if notif.enviar_texto(
        "✅ <b>Rastreador de preços</b>\n\n"
        "Alerta configurado e funcionando.\n"
        "Você vai receber uma mensagem aqui quando um preço cruzar o alvo."
    ):
        click.echo(_simbolos_seguros(f"{_cor('✓', VERDE)} mensagem entregue — confira o Telegram"))
    else:
        click.echo(_simbolos_seguros(f"{_cor('✗', VERMELHO)} falha no envio"))
        click.echo()
        click.echo(_simbolos_seguros("  verifique:"))
        click.echo(_simbolos_seguros("    - o token está correto"))
        click.echo(_simbolos_seguros("    - o chat id é o seu (não de outro bot)"))
        click.echo(_simbolos_seguros("    - você já enviou /start para o bot"))
        raise SystemExit(1)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()