<#
=============================================================================
 A11 — миграционный runner PG Queue Balancer (PowerShell, для Windows-разработки)
=============================================================================
 Аналог scripts/migrate_queue.sh. Применяет DDL очереди + seed к PostgreSQL,
 отслеживая накат в public._migrations_applied.

 Каждый файл применяется в одной транзакции (--single-transaction) с
 ON_ERROR_STOP=1 → при ошибке полный откат файла.

 Использование:
   $env:QUEUE_DATABASE_URL = "postgres://user:pass@host:5432/db"
   ./scripts/migrate_queue.ps1
   ./scripts/migrate_queue.ps1 -Dsn "postgres://..." -Mode integrate
   ./scripts/migrate_queue.ps1 -DryRun
   ./scripts/migrate_queue.ps1 -NoSeed

 Перед запуском на проде сделайте бэкап — см. docs(plan)/db-safe-migration-guide.md.
=============================================================================
#>

[CmdletBinding()]
param(
  [string]$Dsn   = $env:QUEUE_DATABASE_URL,
  [ValidateSet('auto','integrate','greenfield')]
  [string]$Mode  = 'auto',
  [switch]$NoSeed,
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
function Log([string]$m) { Write-Host "[migrate-queue] $m" }
function Die([string]$m) { Write-Error "[migrate-queue][ОШИБКА] $m"; exit 1 }

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DbDir = Resolve-Path (Join-Path $ScriptDir '..\DB')

if (-not (Get-Command psql -ErrorAction SilentlyContinue)) { Die "psql не найден в PATH." }
if ([string]::IsNullOrWhiteSpace($Dsn)) { Die "Не задан DSN. Укажите `$env:QUEUE_DATABASE_URL или -Dsn." }

$Psql = @('psql', $Dsn, '-v', 'ON_ERROR_STOP=1', '--quiet', '--no-psqlrc')

function Invoke-PsqlScalar([string]$sql) {
  return (& $Psql[0] $Psql[1..($Psql.Length-1)] -tAc $sql).Trim()
}

# --- Проверка подключения ---
try { Invoke-PsqlScalar "SELECT 1;" | Out-Null } catch { Die "Не удалось подключиться к БД по DSN." }
Log "Подключение к БД успешно."

# --- Определение режима ---
if ($Mode -eq 'auto') {
  $hasMain = Invoke-PsqlScalar "SELECT (to_regclass('public.source_channels') IS NOT NULL AND to_regclass('public.platforms') IS NOT NULL);"
  if ($hasMain -eq 't') { $Mode = 'integrate' } else { $Mode = 'greenfield' }
  Log "Режим определён автоматически: $Mode"
} else {
  Log "Режим задан вручную: $Mode"
}

$SchemaFile = if ($Mode -eq 'integrate') { Join-Path $DbDir 'A8_integrate_main_db.sql' } else { Join-Path $DbDir 'BD_schema.sql' }
$SeedFile = Join-Path $DbDir 'A9_seed.sql'
if (-not (Test-Path $SchemaFile)) { Die "Файл схемы не найден: $SchemaFile" }
if (-not (Test-Path $SeedFile))   { Die "Файл seed не найден: $SeedFile" }

# --- Журнал миграций ---
& $Psql[0] $Psql[1..($Psql.Length-1)] -c @"
CREATE TABLE IF NOT EXISTS public._migrations_applied (
  name varchar(255) PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);
"@ | Out-Null

function Test-Applied([string]$name) {
  return (Invoke-PsqlScalar "SELECT 1 FROM public._migrations_applied WHERE name = '$name' LIMIT 1;") -eq '1'
}

function Invoke-ApplyOnce([string]$file) {
  $base = Split-Path -Leaf $file
  if (Test-Applied $base) { Log "пропуск (уже применён): $base"; return }
  if ($DryRun) { Log "DRY-RUN применил бы: $base"; return }
  Log "применение: $base"
  & $Psql[0] $Psql[1..($Psql.Length-1)] --single-transaction `
    -f $file `
    -c "INSERT INTO public._migrations_applied(name) VALUES ('$base') ON CONFLICT (name) DO NOTHING;"
  if ($LASTEXITCODE -ne 0) { Die "Не удалось применить $base (откат выполнен)." }
  Log "ОК: $base"
}

function Invoke-ApplySeed([string]$file) {
  $base = Split-Path -Leaf $file
  if ($DryRun) { Log "DRY-RUN применил бы seed: $base"; return }
  Log "seed: $base"
  & $Psql[0] $Psql[1..($Psql.Length-1)] --single-transaction `
    -f $file `
    -c "INSERT INTO public._migrations_applied(name) VALUES ('$base') ON CONFLICT (name) DO UPDATE SET applied_at = now();"
  if ($LASTEXITCODE -ne 0) { Die "Не удалось применить seed $base (откат выполнен)." }
  Log "ОК: $base"
}

$A10File = Join-Path $DbDir 'A10_attempt_status_running.sql'
if (-not (Test-Path $A10File)) { Die "Файл миграции не найден: $A10File" }

Invoke-ApplyOnce $SchemaFile
Invoke-ApplyOnce $A10File
if (-not $NoSeed) { Invoke-ApplySeed $SeedFile } else { Log "seed пропущен (-NoSeed)." }

Log "Готово. Применённые миграции:"
& $Psql[0] $Psql[1..($Psql.Length-1)] -c "SELECT name, applied_at FROM public._migrations_applied ORDER BY applied_at;"
