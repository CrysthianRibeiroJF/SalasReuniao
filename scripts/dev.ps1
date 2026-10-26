<#
.SYNOPSIS
  Sobe o ambiente de desenvolvimento da Agenda de Salas com um único comando (Windows).

.DESCRIPTION
  1. PostgreSQL portátil (scripts\dev-postgres.ps1) em 127.0.0.1:<PgPort> com o banco <DbName>;
  2. backend\.venv (criado e instalado com requirements-dev.txt + constraints.txt quando falta) e backend\.env
     (copiado de backend\.env.example quando falta);
  3. python -m app.cli init-db (migrations + administrador inicial) e, com -Seed, seed-demo;
  4. backend (uvicorn --reload) e frontend (Vite; npm install quando falta node_modules) em
     janelas próprias ou, com -Background, ocultos e com log em .devtools\logs;
  5. espera os dois responderem e mostra os endereços.

  -Stop encerra os processos iniciados por este script (o PostgreSQL continua rodando; para
  pará-lo use .\scripts\dev-postgres.ps1 -Stop). -Status mostra o que está rodando.

  -DbName e -PgPort, quando informados, têm prioridade sobre AGENDA_DATABASE_URL do backend\.env.

.EXAMPLE
  .\scripts\dev.ps1 -Seed
  Sobe tudo com dados de demonstração: frontend em http://127.0.0.1:5173, API em :8000.

.EXAMPLE
  .\scripts\dev.ps1 -BackendPort 8400 -FrontendPort 5400 -DbName agenda_deploy -Background
  Outra instância, em outras portas e outro banco, sem abrir janelas.

.EXAMPLE
  .\scripts\dev.ps1 -Stop
  Encerra todas as instâncias iniciadas pelo script (ou só a de -BackendPort, se informado).
#>
[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$BackendPort = 8000,
    [ValidateRange(1, 65535)][int]$FrontendPort = 5173,
    [ValidatePattern('^[A-Za-z0-9_]+$')][string]$DbName = 'agenda',
    [ValidateRange(1, 65535)][int]$PgPort = 5433,
    # Roda seed-demo (salas, usuários e reservas de exemplo; somente AGENDA_ENV=development).
    [switch]$Seed,
    # Processos ocultos com log em arquivo, em vez de uma janela para cada um.
    [switch]$Background,
    # Só o backend (sem Vite).
    [switch]$NoFrontend,
    # Não chama dev-postgres.ps1 (use quando o backend\.env aponta para outro PostgreSQL).
    [switch]$NoPostgres,
    # Reinstala as dependências (pip install -r requirements-dev.txt -c constraints.txt e npm install).
    [switch]$Install,
    # Segundos de espera para backend e frontend responderem.
    [ValidateRange(5, 600)][int]$TimeoutSeconds = 120,
    [switch]$Stop,
    [switch]$Status
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$venvPython = Join-Path $backend '.venv\Scripts\python.exe'
$stateDir = Join-Path $root '.devtools'
$logDir = Join-Path $stateDir 'logs'

# ------------------------------------------------------------------ utilidades

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

function ConvertTo-PSLiteral([string]$Value) { "'" + ($Value -replace "'", "''") + "'" }

function Test-Port([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        return $iar.AsyncWaitHandle.WaitOne(300) -and $client.Connected
    } catch { return $false } finally { $client.Close() }
}

function Get-PortOwner([int]$Port) {
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
        $proc = Get-Process -Id $conn.OwningProcess -ErrorAction Stop
        return "$($proc.ProcessName) (PID $($proc.Id))"
    } catch { return 'outro processo' }
}

function Test-HttpOk([string]$Url) {
    try {
        $request = [System.Net.HttpWebRequest]::Create($Url)
        $request.Proxy = $null  # localhost nunca passa pelo proxy corporativo
        $request.Timeout = 3000
        $response = $request.GetResponse()
        try { return [int]$response.StatusCode -eq 200 } finally { $response.Close() }
    } catch { return $false }
}

# Executa uma linha de comando pelo cmd.exe, com o stderr junto do stdout. No Windows PowerShell
# 5.1, quando a saída do script é redirecionada (CI, Tee-Object, *>), cada linha de stderr de um
# programa nativo (avisos do npm/pip) viraria erro fatal com $ErrorActionPreference = 'Stop'.
function Invoke-Cmd([string]$What, [string]$CommandLine) {
    & cmd.exe /d /s /c "$CommandLine 2>&1"
    if ($LASTEXITCODE -ne 0) { throw "$What falhou (código $LASTEXITCODE)." }
}

function Format-CmdArg([string]$Value) { '"' + $Value + '"' }

# Executa um comando nativo descartando a saída e devolve o código de saída. No Windows
# PowerShell 5.1, "2>" com $ErrorActionPreference = 'Stop' transformaria qualquer linha de stderr
# em erro fatal.
function Invoke-Quiet([scriptblock]$Command) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Command 2>&1 | Out-Null
        return $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
}

function Get-StatePath([int]$Port) { Join-Path $stateDir "dev-$Port.json" }

function Read-States {
    if (-not (Test-Path $stateDir)) { return @() }
    $files = @(Get-ChildItem -Path $stateDir -Filter 'dev-*.json' -File)
    foreach ($file in $files) {
        try {
            $state = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
            $state | Add-Member -NotePropertyName File -NotePropertyValue $file.FullName -Force
            $state
        } catch { Write-Warning "Ignorando estado ilegível: $($file.FullName)" }
    }
}

# Processo registrado ainda é o mesmo (o Windows reutiliza PIDs)?
function Get-TrackedProcess($Entry) {
    $proc = Get-Process -Id $Entry.Pid -ErrorAction SilentlyContinue
    if ($null -eq $proc) { return $null }
    try {
        if ($proc.StartTime.ToUniversalTime().Ticks -ne [long]$Entry.StartTime) { return $null }
    } catch { return $null }
    return $proc
}

function Stop-Instance($State) {
    foreach ($entry in @($State.Processes)) {
        $proc = Get-TrackedProcess $entry
        if ($null -eq $proc) {
            Write-Host "  $($entry.Name): já estava parado"
            continue
        }
        # /T derruba a árvore inteira (cmd -> python/node -> reloader/workers do uvicorn, esbuild...).
        Invoke-Quiet { & taskkill.exe /PID $entry.Pid /T /F } | Out-Null
        Write-Host "  $($entry.Name): encerrado (PID $($entry.Pid), porta $($entry.Port))"
    }
    Remove-Item -LiteralPath $State.File -Force -ErrorAction SilentlyContinue
}

function Start-DevProcess {
    param(
        [string]$Name,
        [string]$Title,
        [string]$WorkingDirectory,
        [string]$CommandLine,
        [hashtable]$Environment,
        [string]$LogFile
    )
    # O processo é um powershell.exe com um script codificado (-EncodedCommand): sem problemas
    # de aspas nem de acentos no caminho ("Reunião"). Ele chama cmd.exe, que resolve npm.cmd e
    # redireciona a saída para o log no modo -Background.
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add('$ErrorActionPreference = ''Continue''')
    foreach ($key in $Environment.Keys) {
        $lines.Add(('$env:{0} = {1}' -f $key, (ConvertTo-PSLiteral ([string]$Environment[$key]))))
    }
    $lines.Add('Set-Location -LiteralPath ' + (ConvertTo-PSLiteral $WorkingDirectory))
    if ($Background) {
        $cmd = '"' + $CommandLine + ' > "' + $LogFile + '" 2>&1"'
    } else {
        $lines.Add('$Host.UI.RawUI.WindowTitle = ' + (ConvertTo-PSLiteral $Title))
        $lines.Add('Write-Host ' + (ConvertTo-PSLiteral "$Title  (Ctrl+C encerra; ou .\scripts\dev.ps1 -Stop)") + ' -ForegroundColor Cyan')
        $cmd = '"' + $CommandLine + '"'
    }
    $lines.Add('& cmd.exe /d /s /c ' + (ConvertTo-PSLiteral $cmd))
    # Com janela (-NoExit) ela continua aberta depois que o servidor para, mostrando o motivo.
    if ($Background) { $lines.Add('exit $LASTEXITCODE') }
    $encoded = [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes(($lines -join "`n")))

    $arguments = @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass')
    if (-not $Background) { $arguments += '-NoExit' }
    $arguments += @('-EncodedCommand', $encoded)
    $style = if ($Background) { 'Hidden' } else { 'Normal' }
    $proc = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -WindowStyle $style -PassThru
    [pscustomobject]@{
        Name      = $Name
        Pid       = $proc.Id
        StartTime = $proc.StartTime.ToUniversalTime().Ticks
        Port      = $null
        Log       = if ($Background) { $LogFile } else { $null }
    }
}

function Wait-Ready([string]$Name, [scriptblock]$Probe, $Entry) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while (-not (& $Probe)) {
        if ($null -eq (Get-TrackedProcess $Entry)) {
            $hint = if ($Entry.Log) { " Veja o log: $($Entry.Log)" } else { ' Veja a janela do processo.' }
            throw "$Name terminou antes de ficar pronto.$hint"
        }
        if ((Get-Date) -gt $deadline) {
            throw "$Name não respondeu em $TimeoutSeconds s."
        }
        Start-Sleep -Milliseconds 500
    }
}

function Find-BasePython {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($version in '-3.13', '-3.12') {
            if ((Invoke-Quiet { & $launcher.Source $version -c 'import sys' }) -eq 0) { return @($launcher.Source, $version) }
        }
    }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        $check = 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'
        if ((Invoke-Quiet { & $python.Source -c $check }) -eq 0) { return @($python.Source) }
    }
    throw 'Python 3.12+ não encontrado. Instale o Python 3.13 (python.org) e rode de novo.'
}

# ------------------------------------------------------------------ -Status / -Stop

if ($Status) {
    $states = @(Read-States)
    if ($states.Count -eq 0) { Write-Host 'Nenhuma instância iniciada por dev.ps1.' }
    foreach ($state in $states) {
        Write-Host "Instância backend :$($state.BackendPort) (iniciada em $($state.StartedAt), banco $($state.Database))"
        foreach ($entry in @($state.Processes)) {
            $alive = if (Get-TrackedProcess $entry) { 'rodando' } else { 'parado' }
            $port = if (Test-Port $entry.Port) { 'porta aberta' } else { 'porta fechada' }
            Write-Host ("  {0,-9} PID {1,-6} :{2,-5} {3}, {4}" -f $entry.Name, $entry.Pid, $entry.Port, $alive, $port)
        }
    }
    $pg = if (Test-Port $PgPort) { 'ativo' } else { 'parado' }
    Write-Host "PostgreSQL 127.0.0.1:${PgPort}: $pg"
    exit 0
}

if ($Stop) {
    $states = @(Read-States)
    if ($PSBoundParameters.ContainsKey('BackendPort')) {
        $states = @($states | Where-Object { $_.BackendPort -eq $BackendPort })
    }
    if ($states.Count -eq 0) {
        Write-Host 'Nada para encerrar: nenhuma instância iniciada por dev.ps1 foi encontrada.'
        exit 0
    }
    foreach ($state in $states) {
        Write-Step "Encerrando a instância do backend :$($state.BackendPort)"
        Stop-Instance $state
    }
    Write-Host 'PostgreSQL continua ativo (para parar: .\scripts\dev-postgres.ps1 -Stop).'
    exit 0
}

# ------------------------------------------------------------------ subida

$statePath = Get-StatePath $BackendPort
if (Test-Path $statePath) {
    $previous = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $previous | Add-Member -NotePropertyName File -NotePropertyValue $statePath -Force
    if (@($previous.Processes | Where-Object { Get-TrackedProcess $_ }).Count -gt 0) {
        throw "Já existe uma instância na porta $BackendPort iniciada por este script. Use -Stop antes (ou outra -BackendPort)."
    }
    Remove-Item -LiteralPath $statePath -Force
}
$ports = @($BackendPort)
if (-not $NoFrontend) {
    if ($FrontendPort -eq $BackendPort) { throw 'Use portas diferentes para backend e frontend.' }
    $ports += $FrontendPort
}
foreach ($port in $ports) {
    if (Test-Port $port) { throw "A porta $port já está em uso por $(Get-PortOwner $port)." }
}

$overrideDb = $PSBoundParameters.ContainsKey('DbName') -or $PSBoundParameters.ContainsKey('PgPort')
$databaseUrl = "postgresql+asyncpg://postgres@127.0.0.1:$PgPort/$DbName"

$installPython = [bool]$Install
if (-not (Test-Path $venvPython)) {
    Write-Step 'Criando backend\.venv'
    $base = @(Find-BasePython)
    $baseArgs = @()
    if ($base.Count -gt 1) { $baseArgs = $base[1..($base.Count - 1)] }
    $venvCmd = (@((Format-CmdArg $base[0])) + $baseArgs + @('-m', 'venv', (Format-CmdArg (Join-Path $backend '.venv')))) -join ' '
    Invoke-Cmd 'Criação do virtualenv' $venvCmd
    $installPython = $true
} elseif (-not $installPython) {
    $imports = 'import fastapi, uvicorn, sqlalchemy, asyncpg, alembic, openpyxl, pytest, httpx'
    if ((Invoke-Quiet { & $venvPython -c $imports }) -ne 0) { $installPython = $true }
}
if ($installPython) {
    Write-Step 'Instalando dependências do backend (requirements-dev.txt + constraints.txt)'
    $requirements = Format-CmdArg (Join-Path $backend 'requirements-dev.txt')
    # constraints.txt: as mesmas versões testadas que a imagem Docker instala.
    $constraints = Format-CmdArg (Join-Path $backend 'constraints.txt')
    Invoke-Cmd 'pip install' "$(Format-CmdArg $venvPython) -m pip install --disable-pip-version-check --timeout 120 --retries 5 -r $requirements -c $constraints"
}

# Depois do venv: dev-postgres.ps1 usa o Python do venv para criar o banco.
if (-not $NoPostgres) {
    Write-Step "PostgreSQL em 127.0.0.1:$PgPort (banco $DbName)"
    & (Join-Path $PSScriptRoot 'dev-postgres.ps1') -Port $PgPort -Databases @($DbName)
}

$envFile = Join-Path $backend '.env'
if (-not (Test-Path $envFile)) {
    Write-Step 'Criando backend\.env a partir de backend\.env.example'
    Copy-Item -LiteralPath (Join-Path $backend '.env.example') -Destination $envFile
}

if (-not $NoFrontend -and ($Install -or -not (Test-Path (Join-Path $frontend 'node_modules')))) {
    Write-Step 'Instalando dependências do frontend (npm install)'
    Push-Location $frontend
    try {
        Invoke-Cmd 'npm install' 'npm install --no-audit --no-fund --fetch-timeout=120000 --fetch-retries=5'
    } finally { Pop-Location }
}

# Variáveis para os comandos da CLI e para os processos filhos.
$childEnv = @{ PYTHONIOENCODING = 'utf-8'; PYTHONUTF8 = '1' }
if ($overrideDb) { $childEnv['AGENDA_DATABASE_URL'] = $databaseUrl }
$savedEnv = @{}
foreach ($key in $childEnv.Keys) {
    $savedEnv[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
    [Environment]::SetEnvironmentVariable($key, $childEnv[$key], 'Process')
}

$started = @()
try {
    Push-Location $backend
    try {
        Write-Step 'Aplicando migrations (init-db)'
        Invoke-Cmd 'init-db' "$(Format-CmdArg $venvPython) -m app.cli init-db"
        if ($Seed) {
            Write-Step 'Dados de demonstração (seed-demo)'
            Invoke-Cmd 'seed-demo' "$(Format-CmdArg $venvPython) -m app.cli seed-demo"
        }
    } finally { Pop-Location }

    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $backendLog = Join-Path $logDir "backend-$BackendPort.log"
    $frontendLog = Join-Path $logDir "frontend-$FrontendPort.log"

    Write-Step "Iniciando o backend (uvicorn --reload) na porta $BackendPort"
    $backendCmd = '"' + $venvPython + '" -m uvicorn app.main:app --reload --reload-dir app --host 127.0.0.1 --port ' + $BackendPort
    $entry = Start-DevProcess -Name 'backend' -Title "Agenda - backend :$BackendPort" -WorkingDirectory $backend `
        -CommandLine $backendCmd -Environment $childEnv -LogFile $backendLog
    $entry.Port = $BackendPort
    $started += $entry

    if (-not $NoFrontend) {
        Write-Step "Iniciando o frontend (Vite) na porta $FrontendPort"
        $frontendEnv = @{ AGENDA_BACKEND_URL = "http://127.0.0.1:$BackendPort"; FORCE_COLOR = '0' }
        if (-not $Background) { $frontendEnv.Remove('FORCE_COLOR') }
        $entry = Start-DevProcess -Name 'frontend' -Title "Agenda - frontend :$FrontendPort" -WorkingDirectory $frontend `
            -CommandLine "npm run dev -- --port $FrontendPort --strictPort" -Environment $frontendEnv -LogFile $frontendLog
        $entry.Port = $FrontendPort
        $started += $entry
    }

    $state = [pscustomobject]@{
        BackendPort  = $BackendPort
        FrontendPort = if ($NoFrontend) { $null } else { $FrontendPort }
        Database     = if ($overrideDb) { $databaseUrl } else { '(backend\.env)' }
        StartedAt    = (Get-Date).ToString('s')
        Processes    = $started
    }
    $json = $state | ConvertTo-Json -Depth 4
    [System.IO.File]::WriteAllText($statePath, $json, (New-Object System.Text.UTF8Encoding $false))

    Write-Step 'Aguardando os serviços responderem'
    $backendEntry = $started[0]
    Wait-Ready 'O backend' { Test-HttpOk "http://127.0.0.1:$BackendPort/api/health" } $backendEntry
    if (-not $NoFrontend) {
        $frontendEntry = $started[1]
        Wait-Ready 'O frontend' { Test-HttpOk "http://127.0.0.1:$FrontendPort/" } $frontendEntry
    }
} catch {
    foreach ($entry in $started) {
        if (Get-TrackedProcess $entry) { Invoke-Quiet { & taskkill.exe /PID $entry.Pid /T /F } | Out-Null }
    }
    Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue
    throw
} finally {
    foreach ($key in $savedEnv.Keys) { [Environment]::SetEnvironmentVariable($key, $savedEnv[$key], 'Process') }
}

Write-Host ''
Write-Host 'Agenda de Salas rodando em modo desenvolvimento' -ForegroundColor Green
if (-not $NoFrontend) { Write-Host "  Aplicação:  http://127.0.0.1:$FrontendPort" }
Write-Host "  API:        http://127.0.0.1:$BackendPort/api  (documentação interativa: http://127.0.0.1:$BackendPort/docs)"
Write-Host "  Banco:      $($state.Database)"
if ($Background) {
    Write-Host "  Logs:       $backendLog"
    if (-not $NoFrontend) { Write-Host "              $frontendLog" }
}
Write-Host ''
Write-Host 'Credenciais de DEMONSTRAÇÃO (somente desenvolvimento; criadas por init-db/-Seed):' -ForegroundColor Yellow
Write-Host '  admin / admin123 (administração)   recepcao1 / recepcao123   recepcao2 / recepcao123'
if (-not $Seed) { Write-Host '  (recepcao1/recepcao2 e as salas de exemplo só existem depois de rodar com -Seed)' }
Write-Host ''
$stopHint = if ($PSBoundParameters.ContainsKey('BackendPort')) { " -BackendPort $BackendPort" } else { '' }
Write-Host "Para encerrar: .\scripts\dev.ps1 -Stop$stopHint"
