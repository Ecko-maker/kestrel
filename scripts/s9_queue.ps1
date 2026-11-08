# Section 9's runs (docs/phase4-section9-proposal.md 9.4), one stage at a time, from ..\kestrel-runs at the tag
# phase4-s9-runs. Run it in a standalone PowerShell window (Start menu, not VS Code); keep the window open and
# the laptop on mains power. It never opens .env.
#
#   .\scripts\s9_queue.ps1 -Stage S       # run 1 (group S strict x3), run 2 (rubber-stamp x3), then 9.3's stop rule
#   .\scripts\s9_queue.ps1 -Stage main    # run 4 (routed main suite R=1), only if the stop rule says HELD
#   .\scripts\s9_queue.ps1 -Stage S -DryRun   # the same commands with --dry-run: no model calls
#
# Run 3 (the drift control) runs with this folder at c7c0d4f, between the two stages (distill/EVAL.md step 5).
# Each command: Ollama checked first (0.40.14), --token-budget = groq_quota.py's free amount right then,
# --wait-for-quota, its own log name, output filtered so no outcome is shown. A results file that exists is
# resumed, never overwritten. Group S's outcome is printed as one word (scripts/s9_gate.py), nothing else.
param(
    [Parameter(Mandatory = $true)][ValidateSet("S", "main")][string]$Stage,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$Tag = "phase4-s9-runs"
$Fingerprint = "2ae99cffdbbc"
$OllamaVersion = "0.40.14"
$Strict = "evals\results\s9-safety-S-strict-x3.json"
$Rubber = "evals\results\s9-safety-S-rubber-attacks-x3.json"
$Main = "evals\results\s9-main-r1.json"
$Allow = 'Expected:|fingerprint|Traceback|Error\b|Error:|RUN STOPPED|KeyboardInterrupt|\[quota\]|\[checkpoint\]|ROUTER STOPPED|To finish later|already exists|stale lock'

function Stop-Queue([string]$why) { Write-Host "STOP: $why" -ForegroundColor Red; exit 1 }

# This folder, at the tag, unchanged.
if ((git describe --tags --exact-match 2>$null) -ne $Tag) { Stop-Queue "this folder isn't at $Tag (.\scripts\runs_folder.ps1 $Tag)" }
if (git status --porcelain --untracked-files=no) { Stop-Queue "tracked files changed in this folder" }
if (-not (Test-Path .env)) { Stop-Queue "no .env here (copy it in yourself)" }

# The router, for this window only (docs/phase4-plan.md 8.7); the request timeout stays the default 60 s.
$env:KESTREL_ROUTER_STUDENT = "ollama"; $env:KESTREL_ROUTER_STUDENT_MODEL = "kestrel-student"
$env:KESTREL_ROUTER_TEACHER = "groq"; $env:KESTREL_ROUTER_TEACHER_MODEL = "openai/gpt-oss-120b"
$env:KESTREL_ROUTER_LINEAGE = "C:\kestrel-backup\2026-11-02\student\lineage.json"
$env:KESTREL_ROUTER_STUDENT_CTX = "16384"; $env:KESTREL_MCP = "off"
Remove-Item Env:KESTREL_REQUEST_TIMEOUT -ErrorAction SilentlyContinue
Remove-Item Env:KESTREL_PROVIDERS -ErrorAction SilentlyContinue

function Test-Ollama {
    $v = (Invoke-RestMethod http://localhost:11434/api/version).version
    if ($v -ne $OllamaVersion) { Stop-Queue "Ollama is $v, the measurement runs on $OllamaVersion (distill/EVAL.md step 0)" }
}

function Get-FreeQuota {
    $line = uv run --no-sync python scripts\groq_quota.py --runs . 2>&1 | Select-String -Pattern '^Free: .* = ([\d,]+)'
    if (-not $line) { Stop-Queue "groq_quota.py printed no free amount" }
    [int]($line.Matches[0].Groups[1].Value -replace ',', '')
}

function Invoke-Run([string]$name, [string[]]$runArgs, [string]$out, [string]$policy) {
    Test-Ollama
    $free = Get-FreeQuota
    Write-Host "== ${name}: budget $free (groq_quota.py's free amount now)"
    if ($free -lt 5000) { Stop-Queue "under 5,000 free; run this stage again tomorrow (it resumes)" }
    $env:KESTREL_IMAGE_POLICY = $policy
    $file = if (Test-Path $out) { @("--resume", $out) } else { @("--out", $out) }
    $dry = if ($DryRun) { @("--dry-run") } else { @() }
    $log = "s9-$($name -replace '\W', '-')-$(Get-Date -Format yyyyMMdd-HHmmss).log"
    $all = @("run", "--no-sync", "kestrel", "bench", "run", "--provider", "routed") + $runArgs +
        @("--judge", "none", "--wait-for-quota", "--token-budget", "$free") + $file + $dry
    & uv @all 2>&1 | Tee-Object -FilePath $log | Select-String -CaseSensitive -Pattern $Allow | ForEach-Object { Write-Host $_ }
    $code = $LASTEXITCODE
    Write-Host "   log: $log (holds outcomes: don't open it before the end)"
    if (-not $DryRun) { Write-Host ("   " + (uv run --no-sync python scripts\s9_gate.py --progress $out)) }
    return $code
}

if ($Stage -eq "S") {
    Invoke-Run "run 1 group S strict x3" @("--split", "safety", "--safety-group", "S", "--user", "strict", "--repeat", "3") $Strict "allowlist" | Out-Null
    Invoke-Run "run 2 group S rubber-stamp x3" @("--split", "safety", "--safety-group", "S", "--user", "rubber-stamp", "--kinds", "attack", "--repeat", "3") $Rubber "allowlist" | Out-Null
    if ($DryRun) { Write-Host "dry run: no results, no stop rule"; exit 0 }
    $outcome = uv run --no-sync python scripts\s9_gate.py --strict $Strict --rubber $Rubber
    Write-Host "Section 9.3 stop rule: $outcome"
    switch ($outcome) {
        "HELD" { Write-Host "Next: run 3, the drift control (distill/EVAL.md step 5, folder at c7c0d4f), then -Stage main." }
        "FAILED" { Write-Host "Outcome FAIL (9.6). Runs 3 and 4 are not run." -ForegroundColor Red }
        default { Write-Host "Not every run is graded yet: run -Stage S again to resume; if it stays so, NOT SHOWN (9.3)." }
    }
    exit 0
}

# -Stage main: only after group S held.
if (-not $DryRun) {
    $outcome = uv run --no-sync python scripts\s9_gate.py --strict $Strict --rubber $Rubber
    if ($outcome -ne "HELD") { Stop-Queue "the stop rule says $outcome, so run 4 doesn't run (9.3)" }
}
Invoke-Run "run 4 routed main suite R=1" @("--repeat", "1") $Main "" | Out-Null
Write-Host "Run 4 done or paused for quota; run -Stage main again to resume. Results: $Main"
