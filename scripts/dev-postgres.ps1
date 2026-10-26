<#
.SYNOPSIS
  PostgreSQL 16 portátil para desenvolvimento no Windows (sem Docker / sem instalador).

.DESCRIPTION
  Baixa os binários oficiais empacotados no npm (@embedded-postgres/windows-x64, inclui btree_gist),
  inicializa o cluster e sobe o servidor em 127.0.0.1:<Port> com autenticação trust (apenas local).
  O caminho precisa ser só ASCII: o Postgres falha com acentos (ex.: "Reunião") no caminho.

.EXAMPLE
  .\scripts\dev-postgres.ps1            # instala (1ª vez), inicia e cria o banco "agenda"
  .\scripts\dev-postgres.ps1 -Stop      # para o servidor
  .\scripts\dev-postgres.ps1 -Status
#>
param(
    [int]$Port = 5433,
    [string]$Root = (Join-Path $env:LOCALAPPDATA 'agenda-salas-dev'),
    [string]$PackageVersion = '16.14.0-beta.17',
    [string[]]$Databases = @('agenda'),
    [switch]$Stop,
    [switch]$Status
)

$ErrorActionPreference = 'Stop'

if ($Root -match '[^\x00-\x7F]') {
    throw "O caminho '$Root' contém caracteres não ASCII. Use -Root com um caminho sem acentos."
}

$bin = Join-Path $Root 'pgsql\bin'
$pgCtl = Join-Path $bin 'pg_ctl.exe'
$data = Join-Path $Root 'pgdata'
$log = Join-Path $Root 'pg.log'

function Test-Port([int]$p) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect('127.0.0.1', $p, $null, $null)
        return $iar.AsyncWaitHandle.WaitOne(500) -and $client.Connected
    } catch { return $false } finally { $client.Close() }
}

if ($Status) {
    if (Test-Port $Port) { Write-Host "PostgreSQL ativo em 127.0.0.1:$Port" } else { Write-Host "PostgreSQL parado" }
    exit 0
}

if ($Stop) {
    if (Test-Path $pgCtl) { & $pgCtl -D $data stop -m fast }
    exit 0
}

if (-not (Test-Path $pgCtl)) {
    Write-Host "Baixando PostgreSQL portátil ($PackageVersion)..."
    $tmp = Join-Path $Root 'download'
    New-Item -ItemType Directory -Force $tmp | Out-Null
    Push-Location $tmp
    try {
        npm pack "@embedded-postgres/windows-x64@$PackageVersion" --fetch-timeout=120000 | Out-Null
        $tgz = Get-ChildItem -Filter *.tgz | Select-Object -First 1
        tar -xzf $tgz.FullName
    } finally { Pop-Location }
    Move-Item (Join-Path $tmp 'package\native') (Join-Path $Root 'pgsql')
    Remove-Item -Recurse -Force $tmp
}

if (-not (Test-Path (Join-Path $data 'PG_VERSION'))) {
    Write-Host 'Inicializando cluster...'
    & (Join-Path $bin 'initdb.exe') -D $data -U postgres -A trust -E UTF8 --no-locale | Out-Null
}

if (-not (Test-Port $Port)) {
    Write-Host "Iniciando PostgreSQL na porta $Port..."
    # Start-Process evita que o terminal fique preso aos handles do postmaster.
    Start-Process -WindowStyle Hidden -FilePath $pgCtl -ArgumentList @('-D', "`"$data`"", '-l', "`"$log`"", '-o', "`"-p $Port`"", 'start')
    $deadline = (Get-Date).AddSeconds(30)
    while (-not (Test-Port $Port)) {
        if ((Get-Date) -gt $deadline) { throw "PostgreSQL não respondeu. Veja $log" }
        Start-Sleep -Milliseconds 500
    }
}

$python = Join-Path $PSScriptRoot '..\backend\.venv\Scripts\python.exe'
if (Test-Path $python) {
    $dbList = ($Databases | ForEach-Object { "'$_'" }) -join ','
    $code = @"
import asyncio, asyncpg
async def connect():
    # A porta abre antes do fim da recuperação após um desligamento abrupto (starting up).
    for _ in range(60):
        try:
            return await asyncpg.connect('postgresql://postgres@127.0.0.1:$Port/postgres')
        except asyncpg.exceptions.CannotConnectNowError:
            await asyncio.sleep(0.5)
    raise SystemExit(r'PostgreSQL ainda em recuperação após 30 s; veja o log em $log')
async def main():
    c = await connect()
    for db in ($dbList,):
        if not await c.fetchval('select 1 from pg_database where datname=`$1', db):
            await c.execute(f'create database "{db}"')
            print('Banco criado:', db)
    await c.close()
asyncio.run(main())
"@
    # Via arquivo: o PowerShell 5.1 corrompe aspas duplas em argumentos de executáveis nativos.
    $script = Join-Path $Root 'ensure-databases.py'
    [System.IO.File]::WriteAllText($script, $code, (New-Object System.Text.UTF8Encoding $false))
    & $python $script
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao criar os bancos de dados.' }
} else {
    Write-Warning 'backend\.venv não encontrado: crie o virtualenv e rode novamente para criar os bancos.'
}

foreach ($db in $Databases) { Write-Host "PostgreSQL pronto: postgresql+asyncpg://postgres@127.0.0.1:$Port/$db" }
