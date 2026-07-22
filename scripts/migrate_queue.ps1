<#
 A11 - PG Queue Balancer migration runner (PowerShell).
 Same as scripts/migrate_queue.sh / scripts/migrate_queue.py

 Usage:
   $env:QUEUE_DATABASE_URL = "postgresql://user:pass@127.0.0.1:15432/lead_monitor"
   .\scripts\migrate_queue.ps1 -DryRun
   .\scripts\migrate_queue.ps1

 If psql is missing or this script fails to parse, use:
   python scripts/migrate_queue.py --dry-run
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
function Die([string]$m) { Write-Error "[migrate-queue][ERROR] $m"; exit 1 }

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DbDir = Resolve-Path (Join-Path $ScriptDir '..\DB')

if (-not (Get-Command psql -ErrorAction SilentlyContinue)) {
  Die "psql not in PATH. Run: python scripts/migrate_queue.py --dry-run"
}
if ([string]::IsNullOrWhiteSpace($Dsn)) {
  Die 'Set $env:QUEUE_DATABASE_URL or -Dsn'
}

$Psql = @('psql', $Dsn, '-v', 'ON_ERROR_STOP=1', '--quiet', '--no-psqlrc')

function Invoke-PsqlScalar([string]$sql) {
  return (& $Psql[0] $Psql[1..($Psql.Length-1)] -tAc $sql).Trim()
}

try { Invoke-PsqlScalar 'SELECT 1;' | Out-Null } catch { Die 'Cannot connect to database' }
Log 'Connected.'

if ($Mode -eq 'auto') {
  $hasMain = Invoke-PsqlScalar "SELECT (to_regclass('public.source_channels') IS NOT NULL AND to_regclass('public.platforms') IS NOT NULL);"
  if ($hasMain -eq 't') { $Mode = 'integrate' } else { $Mode = 'greenfield' }
  Log "Mode (auto): $Mode"
} else {
  Log "Mode (manual): $Mode"
}

$SchemaFile = if ($Mode -eq 'integrate') { Join-Path $DbDir 'A8_integrate_main_db.sql' } else { Join-Path $DbDir 'BD_schema.sql' }
$SeedFile = Join-Path $DbDir 'A9_seed.sql'
if (-not (Test-Path $SchemaFile)) { Die "Schema file not found: $SchemaFile" }
if (-not (Test-Path $SeedFile))   { Die "Seed file not found: $SeedFile" }

$ledgerSql = @'
CREATE TABLE IF NOT EXISTS public._migrations_applied (
  name varchar(255) PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);
'@
& $Psql[0] $Psql[1..($Psql.Length-1)] -c $ledgerSql | Out-Null

function Test-Applied([string]$name) {
  return (Invoke-PsqlScalar "SELECT 1 FROM public._migrations_applied WHERE name = '$name' LIMIT 1;") -eq '1'
}

function Invoke-ApplyOnce([string]$file) {
  $base = Split-Path -Leaf $file
  if (Test-Applied $base) { Log "skip (already applied): $base"; return }
  if ($DryRun) { Log "DRY-RUN would apply: $base"; return }
  Log "apply: $base"
  & $Psql[0] $Psql[1..($Psql.Length-1)] --single-transaction `
    -f $file `
    -c "INSERT INTO public._migrations_applied(name) VALUES ('$base') ON CONFLICT (name) DO NOTHING;"
  if ($LASTEXITCODE -ne 0) { Die "Failed: $base" }
  Log "OK: $base"
}

function Invoke-ApplySeed([string]$file) {
  $base = Split-Path -Leaf $file
  if ($DryRun) { Log "DRY-RUN would apply seed: $base"; return }
  Log "seed: $base"
  $markSeed = "INSERT INTO public._migrations_applied(name) VALUES ('$base') ON CONFLICT (name) DO UPDATE SET applied_at = CURRENT_TIMESTAMP;"
  & $Psql[0] $Psql[1..($Psql.Length-1)] --single-transaction -f $file -c $markSeed
  if ($LASTEXITCODE -ne 0) { Die "Failed seed: $base" }
  Log "OK: $base"
}

$A10File = Join-Path $DbDir 'A10_attempt_status_running.sql'
$A11File = Join-Path $DbDir 'A11_g6_error_detector.sql'
$A12File = Join-Path $DbDir 'A12_g7_monitoring_views.sql'
foreach ($f in @($A10File, $A11File, $A12File)) {
  if (-not (Test-Path $f)) { Die "Migration file not found: $f" }
}

Invoke-ApplyOnce $SchemaFile
Invoke-ApplyOnce $A10File
Invoke-ApplyOnce $A11File
Invoke-ApplyOnce $A12File
if (-not $NoSeed) { Invoke-ApplySeed $SeedFile } else { Log 'seed skipped (-NoSeed)' }

Log 'Done. Applied migrations:'
& $Psql[0] $Psql[1..($Psql.Length-1)] -c 'SELECT name, applied_at FROM public._migrations_applied ORDER BY applied_at;'
