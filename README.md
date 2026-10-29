# rastreador-precos

Monitor de preços de produto por linha de comando. Acompanha páginas de loja,
guarda o histórico de cada leitura e avisa quando o preço cruza o valor que
você definiu.

Funciona em páginas estáticas **e** em páginas que montam o preço via
JavaScript — a ferramenta tenta HTTP simples primeiro e só abre um navegador
automatizado quando necessário, mantendo a coleta rápida no caso comum.

---

## Como funciona

```
URL  ──►  Coletor HTTP  ──►  Extração em cascata  ──►  SQLite  ──►  Alerta
              │                      │
              │                      ├─ 1. JSON-LD (schema.org/Product)
              │                      ├─ 2. meta tags (og:price, product:price)
              │                      └─ 3. seletor CSS informado
              │
              └─ falhou ou veio sem preço?  ──►  Playwright (navegador)
```

A ordem da cascata importa: JSON-LD é o dado que a própria loja declara para o
Google, então é o mais confiável. Seletor CSS é o último recurso porque quebra
quando o site muda o layout.

---

## Instalação

Requer **Python 3.10+**.

```bash
git clone https://github.com/SEU-USUARIO/rastreador-precos.git
cd rastreador-precos

python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -e .

# apenas se for usar o coletor de navegador
playwright install chromium
```

---

## Uso

### Adicionar um produto

```bash
rastreador add "https://www.loja.com/produto/12345" --alvo 199.90
```

Com seletor CSS (quando a página não expõe JSON-LD nem meta tag):

```bash
rastreador add "https://loja.com/p/123" --seletor ".product-price" --alvo 150
```

### Listar monitorados

```bash
rastreador list
```

```
 ID           PREÇO          ALVO  PRODUTO
──────────────────────────────────────────────────────────────────────────
  1    R$ 1.299,90      R$ 999,90  Notebook 15" i5
  2      R$ 249,90             —  Fone Bluetooth ANC
```

Preço em **verde** = está no menor valor já registrado.

### Verificar agora

```bash
rastreador check            # todos
rastreador check 1 3        # apenas os IDs 1 e 3
rastreador check --sem-navegador   # só HTTP, mais rápido
```

```
verificando 2 produto(s)...

  ✓ #1 Notebook 15" i5
      R$ 1.199,90  -7.69%  ← menor preço já visto
      ★ ALVO ATINGIDO

  ✗ #2 Fone Bluetooth ANC
      navegador: timeout ao carregar
```

### Histórico e estatísticas

```bash
rastreador history 1 --limit 20
rastreador stats
```

```
#1 Notebook 15" i5
────────────────────────────────────────────────────────────
  atual              R$ 1.199,90   -7.69% desde o início
  mínimo             R$ 1.199,90
  máximo             R$ 1.499,00
  média              R$ 1.349,45
  leituras                    12
  → está no menor preço já registrado
```

### Parar de monitorar

```bash
rastreador rm 2
```

O produto sai da lista ativa, mas **o histórico é preservado**.

---

## Alerta no Telegram

Quando um preço cruza o alvo, o alerta chega no seu celular.

### 1. Criar o bot

No Telegram, fale com **[@BotFather](https://t.me/BotFather)**:

```
/newbot
```

Ele pede um nome e um username (precisa terminar em `bot`) e devolve um token:

```
7412345678:AAH9xK2mPq3vN8wR5tY7uI1oP4aS6dF9gH0
```

### 2. Descobrir seu chat ID

**Envie qualquer mensagem para o seu bot primeiro** — o Telegram só revela
chats que já interagiram com ele.

```bash
export TELEGRAM_BOT_TOKEN="7412345678:AAH..."
rastreador telegram id
```

```
chats encontrados:

          8348412890   private    Bruno
```

### 3. Configurar e testar

```bash
export TELEGRAM_CHAT_ID="8348412890"
rastreador telegram test
```

Se chegar a mensagem, está pronto. Permanente no Windows:

```powershell
[Environment]::SetEnvironmentVariable("TELEGRAM_BOT_TOKEN", "741...", "User")
[Environment]::SetEnvironmentVariable("TELEGRAM_CHAT_ID", "8348412890", "User")
```

### Como fica o alerta

```
🔔 Queda de preço

In Her Wake

💰 Agora: £ 12,84
📉 Antes: £ 15,20
⬇️ Variação: -15.53%
🎯 Alvo: £ 15,00

abrir produto
```

**Anti-spam:** o alerta não repete enquanto o preço não cair **abaixo** do
último valor que já disparou. Uma página que oscila entre R$ 199,89 e
R$ 199,91 não gera enxurrada de mensagens.

---

## Rodando sozinho

### Windows — script pronto

```powershell
# grava as credenciais e registra a tarefa
.\agendar.ps1 -Horarios "09:00","21:00"

# testa na hora
.\agendar.ps1 -RodarAgora

# remove
.\agendar.ps1 -Remover
```

O log de cada execução fica em `%USERPROFILE%\.rastreador-precos\execucao.log`.

### Linux / macOS (cron)

```cron
0 9,21 * * * cd /caminho/rastreador-precos && .venv/bin/rastreador check >> ~/precos.log 2>&1
```

---

## Estrutura

```
rastreador-precos/
├── src/rastreador/
│   ├── __init__.py       API pública
│   ├── extrator.py       normalização de preço + cascata de extração
│   ├── coletor.py        HTTP com retry/backoff + Playwright
│   ├── banco.py          SQLite, histórico e estatísticas
│   ├── motor.py          orquestração do ciclo
│   ├── notificador.py    alerta no Telegram
│   └── cli.py            interface de linha de comando
├── tests/
│   ├── test_extrator.py            25 casos de normalização e extração
│   ├── test_banco.py               persistência, histórico e ordenação
│   ├── test_notificador.py         formatação, credenciais, HTML escaping
│   └── test_integracao_alerta.py   motor <-> notificador, anti-spam
├── agendar.ps1           registra a tarefa no Windows
└── pyproject.toml
```

---

## Testes

```bash
pip install -e ".[dev]"
pytest -v
pytest --cov=rastreador --cov-report=term-missing
```

69 testes cobrindo normalização, extração, persistência, ordenação,
formatação de alerta e integração motor↔notificador.

### Validado ao vivo

Além da suíte, o fluxo completo foi exercitado contra sites reais:

- Coleta e extração de preço em páginas com `application/ld+json`
- Alerta entregue de fato no Telegram, com histórico gravado em SQLite
- Anti-spam confirmado: segundo check no mesmo valor não reenvia

Dois bugs de produção foram encontrados nesse processo e corrigidos:

1. **Ordenação instável.** `ORDER BY lido_em DESC` sem desempate: duas
   leituras no mesmo segundo empatam e o SQLite devolve ordem
   indeterminada, quebrando estatística e detecção de menor preço.
   Corrigido com `id DESC` como critério secundário.

2. **Corpo comprimido.** `Accept-Encoding` anunciava `br` (Brotli) sem a
   biblioteca instalada. O servidor enviava Brotli e o parser recebia
   binário, reportando "preço não encontrado" — sintoma que escondia a
   causa. Corrigido anunciando `br` apenas quando `brotli` está presente,
   mais uma guarda `parece_html` que nomeia o problema.

---

## Detalhes de implementação

**Normalização de preço.** O ponto mais frágil de qualquer rastreador é
distinguir `1.299,90` (pt-BR) de `1,299.90` (en-US). A decisão é tomada pela
posição do **último** separador: se a vírgula vem depois do ponto, ela é o
decimal.

**Anti-spam de alerta.** O alerta não repete enquanto o preço não ficar
**abaixo** do último valor que já disparou. Sem isso, uma página que oscila
entre R$ 199,89 e R$ 199,91 geraria dezenas de avisos.

**Backoff com jitter.** O retry usa espera exponencial somada a um ruído
aleatório de até 30%, para não sincronizar rajadas quando vários produtos são
verificados em sequência.

**Remoção preserva histórico.** `rm` desativa o produto em vez de apagar a
linha. Apagar levaria junto a série histórica por `ON DELETE CASCADE`, que é
exatamente o dado que dá valor à ferramenta.

---

## Limitações conhecidas

- **Sites com proteção anti-bot agressiva** (Cloudflare em modo estrito,
  DataDome) vão bloquear. O detector de bloqueio avisa, mas não contorna.
- **Preço atrás de login** não é acessível sem sessão autenticada.
- **`selectolax`** é usado no lugar do BeautifulSoup por ser ~10x mais rápido,
  mas é menos tolerante a HTML malformado.

---

## Licença

MIT
