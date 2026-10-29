# ============================================================
# ZYON - agendador do rastreador de precos
# ============================================================
# Objetivo: fazer o rastreador rodar sozinho em horarios fixos,
#           com as credenciais do Telegram persistidas no ambiente
#           do usuario (nao na sessao do PowerShell).
#
# Uso:
#   .\agendar.ps1                  -> cria/atualiza com horarios padrao
#   .\agendar.ps1 -Horarios "09:00","18:00"
#   .\agendar.ps1 -Remover         -> apaga a tarefa
#   .\agendar.ps1 -RodarAgora      -> executa a tarefa imediatamente
# ============================================================

[CmdletBinding()]
param(
    [string]   $Projeto   = 'D:\Projetos\rastreador-precos',
    [string]   $NomeTarefa = 'RastreadorPrecos',
    [string[]] $Horarios  = @('09:00', '21:00'),
    [string]   $Token     = $env:TELEGRAM_BOT_TOKEN,
    [string]   $ChatId    = $env:TELEGRAM_CHAT_ID,
    [switch]   $Remover,
    [switch]   $RodarAgora
)

$ErrorActionPreference = 'Stop'

function Say  { param([string]$m) Write-Host "[*] $m" -ForegroundColor Cyan }
function Ok   { param([string]$m) Write-Host "[+] $m" -ForegroundColor Green }
function Warn { param([string]$m) Write-Host "[!] $m" -ForegroundColor Yellow }
function Fail { param([string]$m) Write-Host "[-] $m" -ForegroundColor Red }

# ------------------------------------------------------------
# Normalizacao dos horarios
# ------------------------------------------------------------
# Quando o script e chamado com `powershell -File`, o PowerShell NAO
# converte "-Horarios 09:00,21:00" em array: chega uma string unica
# "09:00,21:00", que o New-ScheduledTaskTrigger rejeita como DateTime.
# Quebramos por virgula e ponto-e-virgula para aceitar as duas formas.
$Horarios = $Horarios |
    ForEach-Object { $_ -split '[,;]' } |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ }

if (-not $Horarios) { $Horarios = @('09:00', '21:00') }

foreach ($h in $Horarios) {
    if ($h -notmatch '^\d{1,2}:\d{2}$') {
        Fail "horario invalido: '$h' - use o formato HH:MM (ex: 09:00)"
        exit 1
    }
}

# ------------------------------------------------------------
# Credenciais: o padrao do parametro le a sessao atual, que pode
# estar vazia. Se estiver, buscamos o que ja foi persistido no
# ambiente do USUARIO - que e a fonte que a tarefa agendada le.
# ------------------------------------------------------------
if (-not $Token)  { $Token  = [Environment]::GetEnvironmentVariable('TELEGRAM_BOT_TOKEN', 'User') }
if (-not $ChatId) { $ChatId = [Environment]::GetEnvironmentVariable('TELEGRAM_CHAT_ID', 'User') }

# ------------------------------------------------------------
# Remocao
# ------------------------------------------------------------
if ($Remover) {
    Say "removendo tarefa '$NomeTarefa'..."
    if (Get-ScheduledTask -TaskName $NomeTarefa -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $NomeTarefa -Confirm:$false
        Ok 'tarefa removida'
    } else {
        Warn 'tarefa nao existia'
    }
    exit 0
}

# ------------------------------------------------------------
# Validacao
# ------------------------------------------------------------
Say 'validando ambiente...'

$exe = Join-Path $Projeto '.venv\Scripts\rastreador.exe'
if (-not (Test-Path $exe)) {
    Fail "executavel nao encontrado: $exe"
    Fail 'rode antes: pip install -e . dentro do venv do projeto'
    exit 1
}
Ok "rastreador: $exe"

# ------------------------------------------------------------
# Credenciais - persistidas no ambiente do USUARIO
# ------------------------------------------------------------
if ($Token) {
    [Environment]::SetEnvironmentVariable('TELEGRAM_BOT_TOKEN', $Token, 'User')
    Ok 'TELEGRAM_BOT_TOKEN gravado no ambiente do usuario'
} else {
    Warn 'token nao informado - o alerta no Telegram nao vai funcionar'
    Warn '  rode: .\agendar.ps1 -Token "123:ABC" -ChatId "999"'
}

if ($ChatId) {
    [Environment]::SetEnvironmentVariable('TELEGRAM_CHAT_ID', $ChatId, 'User')
    Ok 'TELEGRAM_CHAT_ID gravado no ambiente do usuario'
} else {
    Warn 'chat id nao informado - rode: rastreador telegram id'
}

# ------------------------------------------------------------
# A tarefa roda um .cmd porque o Agendador nao executa
# sequencias de comando - precisa de um processo unico.
# ------------------------------------------------------------
$cmdPath = Join-Path $Projeto 'executar_verificacao.cmd'

# Os valores sao resolvidos AQUI, fora do here-string. Dentro de um
# here-string expansivel (@"..."@) o PowerShell tenta interpretar
# $(...) e o parser quebra com "[Environment]::GetEnvironmentVariable".
$tokenPersistido = [Environment]::GetEnvironmentVariable('TELEGRAM_BOT_TOKEN', 'User')
$chatPersistido  = [Environment]::GetEnvironmentVariable('TELEGRAM_CHAT_ID', 'User')
$logDir          = Join-Path $env:USERPROFILE '.rastreador-precos'

$conteudo = @"
@echo off
REM gerado por agendar.ps1 - nao editar a mao
cd /d "$Projeto"

REM credenciais do alerta (vazio = alerta desligado)
set "TELEGRAM_BOT_TOKEN=$tokenPersistido"
set "TELEGRAM_CHAT_ID=$chatPersistido"

set "LOG=$logDir\execucao.log"
if not exist "$logDir" mkdir "$logDir"

echo. >> "%LOG%"
echo ===== %DATE% %TIME% ===== >> "%LOG%"
"$exe" check >> "%LOG%" 2>&1
echo codigo de saida: %ERRORLEVEL% >> "%LOG%"
"@

Set-Content -Path $cmdPath -Value $conteudo -Encoding OEM
Ok "script de execucao: $cmdPath"

# ------------------------------------------------------------
# Registro no Agendador de Tarefas
# ------------------------------------------------------------
Say "registrando tarefa '$NomeTarefa'..."

if (Get-ScheduledTask -TaskName $NomeTarefa -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $NomeTarefa -Confirm:$false
    Say 'tarefa antiga removida'
}

$acao = New-ScheduledTaskAction -Execute $cmdPath -WorkingDirectory $Projeto

$gatilhos = foreach ($h in $Horarios) {
    New-ScheduledTaskTrigger -Daily -At $h
}

$config = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
    -TaskName $NomeTarefa `
    -Action $acao `
    -Trigger $gatilhos `
    -Settings $config `
    -Description 'Verifica precos monitorados e alerta no Telegram quando cruzam o alvo' | Out-Null

Ok "tarefa registrada - roda todo dia as: $($Horarios -join ', ')"

# ------------------------------------------------------------
# Verificacao final
# ------------------------------------------------------------
Say 'confirmando registro:'
Get-ScheduledTask -TaskName $NomeTarefa |
    Select-Object TaskName, State |
    Format-Table -AutoSize

Get-ScheduledTaskInfo -TaskName $NomeTarefa |
    Select-Object NextRunTime, LastRunTime, LastTaskResult |
    Format-List

if ($RodarAgora) {
    Say 'executando agora...'
    Start-ScheduledTask -TaskName $NomeTarefa
    Start-Sleep -Seconds 8
    $log = Join-Path $env:USERPROFILE '.rastreador-precos\execucao.log'
    if (Test-Path $log) {
        Write-Host ''
        Say 'ultimas linhas do log:'
        Get-Content $log -Tail 25
    }
}

Write-Host ''
Ok 'pronto.'
# a forma com chaves e obrigatoria aqui: sem elas o PowerShell tenta ler
# o nome da variavel de ambiente incluindo a barra seguinte e quebra a string
Write-Host "  log de execucao : ${env:USERPROFILE}\.rastreador-precos\execucao.log" -ForegroundColor DarkGray
Write-Host "  remover tarefa  : .\agendar.ps1 -Remover" -ForegroundColor DarkGray
