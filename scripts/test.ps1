<#
.SYNOPSIS
  Roda os testes da Agenda de Salas: backend em grupos paralelos + frontend (Vitest) [+ E2E].

.DESCRIPTION
  Backend: os arquivos backend\tests\test_*.py são divididos em -Groups grupos equilibrados (pelo
  tempo da última execução, ou pelo tamanho dos arquivos na primeira vez) e cada grupo roda num
  processo pytest próprio com um banco exclusivo: <DbPrefix>1, <DbPrefix>2, ... (padrão
  agenda_test_g1..gN). O conftest cria o banco se não existir e recria o schema a cada execução.

  Frontend: npm test -- --run (Vitest), em paralelo com o backend.
  E2E (-E2E): npm run e2e (build + Playwright com o Microsoft Edge), depois dos demais.

  Logs e relatórios JUnit ficam em .devtools\test-results. O script mostra um resumo e termina
  com código diferente de zero se qualquer etapa falhar.

.EXAMPLE
  .\scripts\test.ps1
  Tudo (backend em 4 grupos + frontend).

.EXAMPLE
  .\scripts\test.ps1 -Tests test_rooms.py, 'test_excel_*' -SkipFrontend
  Só alguns arquivos do backend.

.EXAMPLE
  .\scripts\test.ps1 -E2E -E2EPort 8100 -E2EDbName agenda_e2e
  Inclui os testes ponta a ponta (Playwright).
#>
[CmdletBinding()]
param(
    [ValidateRange(1, 16)][int]$Groups = 4,
    [ValidatePattern('^[A-Za-z0-9_]+$')][string]$DbPrefix = 'agenda_test_g',
    [ValidateRange(1, 65535)][int]$PgPort = 5433,
    # Arquivos do backend (nomes ou curingas relativos a backend\tests). Padrão: test_*.py.
    [string[]]$Tests,
    # Argumentos extras repassados a cada pytest (ex.: -PytestArgs '-x', '-k', 'conflict').
    [string[]]$PytestArgs = @(),
    [switch]$SkipBackend,
    [switch]$SkipFrontend,
    [switch]$E2E,
    # Porta e banco do backend dos testes E2E (padrões do playwright.config.ts: 8100 / agenda_e2e).
    [ValidateRange(0, 65535)][int]$E2EPort = 0,
    [string]$E2EDbName,
    # Limite por etapa; um grupo que passar disso é interrompido e conta como falha.
    [ValidateRange(1, 240)][int]$TimeoutMinutes = 45,
    # Não tenta subir o PostgreSQL portátil (scripts\dev-postgres.ps1) se ele estiver parado.
    [switch]$NoPostgres
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$venvPython = Join-Path $backend '.venv\Scripts\python.exe'
$resultsDir = Join-Path $root '.devtools\test-results'
$durationsFile = Join-Path $root '.devtools\test-durations.json'

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

function Format-Duration([TimeSpan]$Span) {
    if ($Span.TotalHours -ge 1) { return '{0}h{1:00}m{2:00}s' -f [int][math]::Floor($Span.TotalHours), $Span.Minutes, $Span.Seconds }
    return '{0}m{1:00}s' -f [int][math]::Floor($Span.TotalMinutes), $Span.Seconds
}

function Test-Port([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        return $iar.AsyncWaitHandle.WaitOne(500) -and $client.Connected
    } catch { return $false } finally { $client.Close() }
}

# Monta a linha de comando com as regras de aspas do Windows (CommandLineToArgvW).
function Join-Arguments([string[]]$Arguments) {
    $quoted = foreach ($arg in $Arguments) {
        if ($arg -ne '' -and $arg -notmatch '[\s"]') { $arg; continue }
        '"' + ($arg -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
    }
    return ($quoted -join ' ')
}

function Stop-Tree([System.Diagnostics.Process]$Process) {
    if ($Process.HasExited) { return }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & taskkill.exe /PID $Process.Id /T /F 2>&1 | Out-Null } finally { $ErrorActionPreference = $previous }
}

# Inicia um processo com stdout/stderr em arquivos e variáveis de ambiente próprias.
# $ArgumentLine vai literal para o processo (cmd.exe tem regras de aspas próprias).
function Start-Logged([string]$FilePath, [string]$ArgumentLine, [string]$WorkingDirectory, [hashtable]$Environment, [string]$Log) {
    $saved = @{}
    foreach ($key in $Environment.Keys) {
        $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        [Environment]::SetEnvironmentVariable($key, [string]$Environment[$key], 'Process')
    }
    try {
        $proc = Start-Process -FilePath $FilePath -ArgumentList $ArgumentLine -WorkingDirectory $WorkingDirectory `
            -RedirectStandardOutput $Log -RedirectStandardError "$Log.stderr" -NoNewWindow -PassThru
        # Sem guardar o handle agora, ExitCode fica vazio depois que o processo termina.
        $null = $proc.Handle
        return $proc
    } finally {
        foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key], 'Process') }
    }
}

function Read-JUnit([string]$Path) {
    $counts = [ordered]@{ Tests = 0; Failures = 0; Errors = 0; Skipped = 0; Found = $false }
    if (-not (Test-Path $Path)) { return [pscustomobject]$counts }
    try {
        [xml]$xml = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
        $suites = @($xml.SelectNodes('//testsuite'))
        foreach ($suite in $suites) {
            $counts.Tests += [int]$suite.tests
            $counts.Failures += [int]$suite.failures
            $counts.Errors += [int]$suite.errors
            $counts.Skipped += [int]$suite.skipped
        }
        $counts.Found = $suites.Count -gt 0
    } catch { Write-Warning "Relatório ilegível: $Path" }
    return [pscustomobject]$counts
}

# Tempo por arquivo (classname "tests.test_rooms" -> "test_rooms.py"), somado dos testcases.
function Read-FileDurations([string]$Path) {
    $result = @{}
    if (-not (Test-Path $Path)) { return $result }
    try {
        [xml]$xml = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
        foreach ($case in @($xml.SelectNodes('//testcase'))) {
            $name = (([string]$case.classname) -split '\.')[1]
            if (-not $name) { continue }
            $file = "$name.py"
            $result[$file] = [double]($result[$file]) + [double]$case.time
        }
    } catch { }
    return $result
}

function Show-Failures([string]$Log) {
    if (-not (Test-Path $Log)) { return }
    $lines = @(Get-Content -LiteralPath $Log -Encoding UTF8 | Where-Object { $_ -match '^(FAILED|ERROR) |^\s*FAIL\s' })
    if ($lines.Count -eq 0) { $lines = @(Get-Content -LiteralPath $Log -Encoding UTF8 -Tail 25) }
    $lines | Select-Object -First 40 | ForEach-Object { Write-Host "      $_" -ForegroundColor Red }
}

$summary = New-Object System.Collections.Generic.List[object]
$overallStart = Get-Date
New-Item -ItemType Directory -Force -Path $resultsDir | Out-Null

# ------------------------------------------------------------------ preparação

$running = New-Object System.Collections.Generic.List[object]

if (-not $SkipBackend) {
    if (-not (Test-Path $venvPython)) { throw 'backend\.venv não encontrado. Rode antes: .\scripts\dev.ps1 (cria o virtualenv).' }
    if (-not (Test-Port $PgPort)) {
        if ($NoPostgres) { throw "PostgreSQL não responde em 127.0.0.1:$PgPort." }
        Write-Step "PostgreSQL parado: iniciando em 127.0.0.1:$PgPort"
        & (Join-Path $PSScriptRoot 'dev-postgres.ps1') -Port $PgPort
    }

    $testsDir = Join-Path $backend 'tests'
    $patterns = if ($Tests) { $Tests } else { @('test_*.py') }
    $files = @(@(
            foreach ($pattern in $patterns) {
                $leaf = Split-Path $pattern -Leaf
                if ($leaf -notmatch '\.py$' -and $leaf -notmatch '\*$') { $leaf = "$leaf.py" }
                Get-ChildItem -Path $testsDir -Filter $leaf -File
            }
        ) | Sort-Object FullName -Unique)
    if ($files.Count -eq 0) { throw "Nenhum arquivo de teste encontrado para: $($patterns -join ', ')" }

    # Peso de cada arquivo: segundos da última execução; sem histórico, estimado pelo tamanho.
    $known = @{}
    if (Test-Path $durationsFile) {
        try {
            $data = Get-Content -LiteralPath $durationsFile -Raw -Encoding UTF8 | ConvertFrom-Json
            foreach ($prop in $data.PSObject.Properties) { $known[$prop.Name] = [double]$prop.Value }
        } catch { }
    }
    $rates = @($files | Where-Object { $known.ContainsKey($_.Name) -and $_.Length -gt 0 } | ForEach-Object { $known[$_.Name] / $_.Length })
    $rate = if ($rates.Count -gt 0) { ($rates | Sort-Object)[[int][math]::Floor($rates.Count / 2)] } else { 0.002 }
    $weighted = foreach ($file in $files) {
        $weight = if ($known.ContainsKey($file.Name)) { $known[$file.Name] } else { $file.Length * $rate }
        [pscustomobject]@{ File = $file; Weight = [math]::Max($weight, 0.1) }
    }

    $groupCount = [math]::Min($Groups, $files.Count)
    $buckets = @(for ($i = 0; $i -lt $groupCount; $i++) { [pscustomobject]@{ Index = $i + 1; Weight = 0.0; Files = New-Object System.Collections.Generic.List[string] } })
    foreach ($item in ($weighted | Sort-Object Weight -Descending)) {
        $target = $buckets | Sort-Object Weight, Index | Select-Object -First 1
        $target.Weight += $item.Weight
        $target.Files.Add("tests/$($item.File.Name)")
    }

    Write-Step "Backend: $($files.Count) arquivo(s) em $groupCount grupo(s) paralelos"
    foreach ($bucket in $buckets) {
        $name = "g$($bucket.Index)"
        $db = "$DbPrefix$($bucket.Index)"
        $log = Join-Path $resultsDir "backend-$name.log"
        $junit = Join-Path $resultsDir "backend-$name.xml"
        Remove-Item -LiteralPath $log, "$log.stderr", $junit -Force -ErrorAction SilentlyContinue
        $env_ = @{
            AGENDA_TEST_DATABASE_URL = "postgresql+asyncpg://postgres@127.0.0.1:$PgPort/$db"
            PYTHONIOENCODING         = 'utf-8'
            PYTHONUTF8               = '1'
        }
        $arguments = @('-m', 'pytest', '-q', '-p', 'no:cacheprovider', "--junitxml=$junit") + $PytestArgs + $bucket.Files
        $proc = Start-Logged -FilePath $venvPython -ArgumentLine (Join-Arguments $arguments) -WorkingDirectory $backend -Environment $env_ -Log $log
        Write-Host ("  {0} (banco {1}, ~{2:N0} s): {3}" -f $name, $db, $bucket.Weight, (($bucket.Files | ForEach-Object { $_ -replace '^tests/', '' }) -join ' '))
        $running.Add([pscustomobject]@{ Kind = 'backend'; Name = "Backend $name"; Process = $proc; Log = $log; JUnit = $junit; Start = Get-Date; Done = $false; TimedOut = $false })
    }
}

if (-not $SkipFrontend) {
    if (-not (Test-Path (Join-Path $frontend 'node_modules'))) { throw 'frontend\node_modules não encontrado. Rode antes: cd frontend; npm install' }
    Write-Step 'Frontend: Vitest (npm test -- --run)'
    $log = Join-Path $resultsDir 'frontend-unit.log'
    $junit = Join-Path $resultsDir 'frontend-unit.xml'
    Remove-Item -LiteralPath $log, "$log.stderr", $junit -Force -ErrorAction SilentlyContinue
    $cmd = 'npm test -- --run --reporter=default --reporter=junit "--outputFile.junit=' + $junit + '"'
    $proc = Start-Logged -FilePath 'cmd.exe' -ArgumentLine "/d /s /c `"$cmd`"" -WorkingDirectory $frontend `
        -Environment @{ FORCE_COLOR = '0'; CI = '1' } -Log $log
    $running.Add([pscustomobject]@{ Kind = 'frontend'; Name = 'Frontend (Vitest)'; Process = $proc; Log = $log; JUnit = $junit; Start = Get-Date; Done = $false; TimedOut = $false })
}

# ------------------------------------------------------------------ espera

$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
$lastNotice = Get-Date
try {
    while (@($running | Where-Object { -not $_.Done }).Count -gt 0) {
        foreach ($job in @($running | Where-Object { -not $_.Done })) {
            if ($job.Process.HasExited) {
                $job.Done = $true
                $job.Process.WaitForExit()
                $counts = Read-JUnit $job.JUnit
                $elapsed = Format-Duration ((Get-Date) - $job.Start)
                $color = if ($job.Process.ExitCode -eq 0) { 'Green' } else { 'Red' }
                Write-Host ("  {0} terminou em {1}: {2} teste(s), {3} falha(s), {4} erro(s)" -f $job.Name, $elapsed, $counts.Tests, $counts.Failures, $counts.Errors) -ForegroundColor $color
            } elseif ((Get-Date) -gt $deadline) {
                $job.TimedOut = $true
                $job.Done = $true
                Stop-Tree $job.Process
                Write-Host "  $($job.Name): interrompido após $TimeoutMinutes min" -ForegroundColor Red
            }
        }
        if (((Get-Date) - $lastNotice).TotalSeconds -ge 60) {
            $pending = @($running | Where-Object { -not $_.Done } | ForEach-Object { $_.Name }) -join ', '
            if ($pending) { Write-Host "  ... em andamento ($(Format-Duration ((Get-Date) - $overallStart))): $pending" }
            $lastNotice = Get-Date
        }
        Start-Sleep -Milliseconds 500
    }
} finally {
    # Ctrl+C ou erro: não deixa pytest/vitest órfãos.
    foreach ($job in $running) { if (-not $job.Process.HasExited) { Stop-Tree $job.Process } }
}

$durations = @{}
foreach ($job in $running) {
    $counts = Read-JUnit $job.JUnit
    $exit = if ($job.TimedOut) { -1 } else { $job.Process.ExitCode }
    # pytest 5 = nenhum teste coletado; um relatório ausente também é falha.
    $ok = ($exit -eq 0) -and $counts.Found
    $summary.Add([pscustomobject]@{
            Etapa    = $job.Name
            Testes   = $counts.Tests
            Falhas   = $counts.Failures
            Erros    = $counts.Errors
            Pulados  = $counts.Skipped
            Tempo    = Format-Duration ($job.Process.ExitTime - $job.Start)
            Situacao = if ($job.TimedOut) { 'TEMPO ESGOTADO' } elseif ($ok) { 'ok' } else { "FALHOU (código $exit)" }
            Log      = $job.Log
            Ok       = $ok
        })
    if ($job.Kind -eq 'backend') {
        $fileTimes = Read-FileDurations $job.JUnit
        foreach ($key in $fileTimes.Keys) { $durations[$key] = [math]::Round($fileTimes[$key], 1) }
    }
}
if ($durations.Count -gt 0) {
    $merged = @{}
    if (Test-Path $durationsFile) {
        try {
            $data = Get-Content -LiteralPath $durationsFile -Raw -Encoding UTF8 | ConvertFrom-Json
            foreach ($prop in $data.PSObject.Properties) { $merged[$prop.Name] = $prop.Value }
        } catch { }
    }
    foreach ($key in $durations.Keys) { $merged[$key] = $durations[$key] }
    $json = $merged | ConvertTo-Json
    [System.IO.File]::WriteAllText($durationsFile, $json, (New-Object System.Text.UTF8Encoding $false))
}

# ------------------------------------------------------------------ E2E

if ($E2E) {
    Write-Step 'E2E: npm run e2e (build + Playwright/Edge)'
    $saved = @{}
    $e2eEnv = @{}
    if ($E2EPort -gt 0) { $e2eEnv['E2E_PORT'] = [string]$E2EPort }
    if ($E2EDbName) { $e2eEnv['E2E_DB_NAME'] = $E2EDbName }
    foreach ($key in $e2eEnv.Keys) {
        $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        [Environment]::SetEnvironmentVariable($key, $e2eEnv[$key], 'Process')
    }
    $start = Get-Date
    Push-Location $frontend
    try {
        # Pelo cmd.exe com stderr junto: avisos do npm/node em stderr não derrubam o script (PS 5.1).
        & cmd.exe /d /s /c "npm run e2e 2>&1"
        $exit = $LASTEXITCODE
    } finally {
        Pop-Location
        foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key], 'Process') }
    }
    $summary.Add([pscustomobject]@{
            Etapa = 'E2E (Playwright)'; Testes = '-'; Falhas = '-'; Erros = '-'; Pulados = '-'
            Tempo = Format-Duration ((Get-Date) - $start)
            Situacao = if ($exit -eq 0) { 'ok' } else { "FALHOU (código $exit)" }
            Log = 'frontend\playwright-report\index.html'; Ok = ($exit -eq 0)
        })
}

# ------------------------------------------------------------------ resumo

Write-Host ''
Write-Host "Resumo ($(Format-Duration ((Get-Date) - $overallStart)) no total)" -ForegroundColor Cyan
# Out-String + Write-Host: o resumo também aparece quando a saída é redirecionada (*>, CI).
($summary | Format-Table Etapa, Testes, Falhas, Erros, Pulados, Tempo, Situacao -AutoSize | Out-String -Width 200).TrimEnd() | Write-Host
$failed = @($summary | Where-Object { -not $_.Ok })
foreach ($item in $failed) {
    Write-Host "  $($item.Etapa): $($item.Log)" -ForegroundColor Red
    if ($item.Log -like '*.log') { Show-Failures $item.Log }
}
if ($summary.Count -eq 0) {
    Write-Host 'Nada foi executado (todas as etapas foram puladas).' -ForegroundColor Yellow
    exit 0
}
if ($failed.Count -gt 0) {
    Write-Host "FALHOU: $($failed.Count) etapa(s) com problema." -ForegroundColor Red
    exit 1
}
Write-Host 'SUCESSO: todos os testes passaram.' -ForegroundColor Green
exit 0
