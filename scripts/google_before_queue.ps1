# The Google step's before side and A0 calibration, in order (docs/google-step-plan.md sections 5 and 6):
# B2, then B4, then A0, one Groq job at a time. Run from ..\kestrel-runs at the tag google-before, in a standalone
# PowerShell window (Start menu, not VS Code). Keep the window open and the laptop on mains power and awake
# (Modern Standby pauses runs). It never opens .env. This file lives in the development folder; the runs
# folder's tagged code is what runs:
#
#   cd ..\kestrel-runs
#   & ..\kestrel\scripts\google_before_queue.ps1            # B2, B4, A0
#   & ..\kestrel\scripts\google_before_queue.ps1 -DryRun    # the three dry runs: no model calls
#   Get-Content evals\results\google-before-queue.log -Tail 25    # progress (from any window)
#
# B2  old benign, strict x3 (18 x 3)            -> evals\results\google-before-b2-benign-x3.json
# B4  main suite x1 (100, judge groq)           -> evals\results\google-before-b4-main-x1.json
# A0  CI subset x1 with --tools google (stubs)  -> evals\results\google-a0-calibration.json
#
# Approvals (STATUS, 2026-11-20): B2 + B4 together at most 300,000 billable; A0 at most 70,000. Each attempt's
# --token-budget is the smaller of groq_quota.py's free amount and what is left of its approval minus one task's
# margin (--token-budget stops starting tasks once reached, so one task can go past it), counted from the
# results files themselves, so a restarted queue still stops at the approval. --wait-for-quota on every run, and
# --resume once the file exists. A run with ungraded runs left is resumed while each attempt makes progress.
# The queue log holds times, billable tokens and graded counts: never an outcome.
#
# Settings, as plan section 5 says: the safety run uses KESTREL_MCP=off and today's image policy (allowlist,
# as the s9 safety runs did); B4 and A0 leave the image policy unset, like every main run and CI. A0 is the CI
# job's own command (--subset ci --provider groq --judge groq --pause 2, KESTREL_MCP=off) plus --tools google,
# so its billable compares with the default CI-subset runs of the same code (f = A0 / default).
param([switch]$DryRun)
$ErrorActionPreference = "Stop"

$Tag = "google-before"
$ApprovedBefore = 300000   # B2 + B4
$ApprovedA0 = 70000
$Margin = 5000             # one task's worth past --token-budget
$NeedFree = 5000           # don't start an attempt with less than this free
$Results = "evals\results"
$B2 = "$Results\google-before-b2-benign-x3.json"
$B4 = "$Results\google-before-b4-main-x1.json"
$A0 = "$Results\google-a0-calibration.json"
$QueueLog = "$Results\google-before-queue.log"
$QueueLock = "$Results\google-before-queue.lock"
$Allow = 'Expected:|fingerprint|Traceback|Error\b|Error:|RUN STOPPED|KeyboardInterrupt|\[quota\]|\[checkpoint\]|To finish later|already exists|stale lock'
$Here = (Get-Location).Path
$Repo = (Resolve-Path (Join-Path $Here "..\kestrel")).Path
$Quota = Join-Path $Repo "scripts\groq_quota.py"

New-Item -ItemType Directory -Force $Results | Out-Null

function Log([string]$line) {
    $stamped = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $line"
    Add-Content -Path $QueueLog -Value $stamped -Encoding UTF8
    Write-Host $stamped
}

function Stop-Queue([string]$why) {
    Log "QUEUE STOPPED: $why"
    if (Test-Path $QueueLock) { Remove-Item $QueueLock -Force }
    exit 1
}

function Assert-AtCommit([string]$ref) {
    $want = (git rev-parse "$ref^{commit}").Trim()
    $have = (git rev-parse HEAD).Trim()
    if ($want -ne $have) { Stop-Queue "this folder is at $($have.Substring(0, 7)), not $ref" }
    if (git status --porcelain --untracked-files=no) { Stop-Queue "tracked files changed in this folder" }
}

function Wait-NoOtherGroqJob {
    while ($true) {
        $others = @(Get-CimInstance Win32_Process | Where-Object {
                $_.CommandLine -match 'kestrel(\.exe)?"?\s+bench\s+(run|rejudge|calibrate)\b' -and $_.CommandLine -notmatch '--dry-run'
            })
        if ($others.Count -eq 0) { return }
        Log "waiting: another Groq job is running (process $($others[0].ProcessId)); checking again in 60 s"
        Start-Sleep -Seconds 60
    }
}

function Get-FreeQuota {
    if (-not (Test-Path $Quota)) { Stop-Queue "no $Quota" }
    $out = & uv run --no-sync python $Quota --runs $Here 2>&1
    $line = $out | Select-String -Pattern '^Free: .* = (-?[\d,]+)'
    if (-not $line) { Stop-Queue "groq_quota.py printed no free amount" }
    [int]($line.Matches[0].Groups[1].Value -replace ',', '')
}

function Get-Counts([string]$file) {
    # graded, total, billable, agent: numbers and the fingerprint only, never which run passed.
    if (-not (Test-Path $file)) { return @{ graded = 0; total = 0; billable = 0; agent = "" } }
    $data = Get-Content $file -Raw -Encoding UTF8 | ConvertFrom-Json
    $rows = @($data.tasks)
    $graded = @($rows | Where-Object { $_.status -in @("pass", "fail", "excluded") }).Count
    $billable = 0
    if ($data.summary -and $data.summary.billable_tokens_total) { $billable = [int]$data.summary.billable_tokens_total }
    return @{ graded = $graded; total = $rows.Count; billable = $billable; agent = $data.meta.agent.sha }
}

function Get-Spent([string[]]$files) {
    $sum = 0
    foreach ($f in $files) { $sum += (Get-Counts $f).billable }
    return $sum
}

function Invoke-QueueRun([string]$name, [string[]]$runArgs, [string]$out, [int]$approved, [string[]]$pool, [string]$policy) {
    $attempt = 0
    while ($true) {
        $attempt += 1
        Wait-NoOtherGroqJob
        $before = Get-Counts $out
        $spent = Get-Spent $pool
        $left = $approved - $spent - $Margin
        if ($left -le 0) { Stop-Queue "${name}: the approval of $approved billable is used up ($spent spent, margin $Margin)" }
        $free = Get-FreeQuota
        $budget = [Math]::Min($free, $left)
        if ($budget -lt $NeedFree) {
            Log "${name}: $budget billable available now (free $free, approval left $left), needs $NeedFree; waiting 30 min"
            Start-Sleep -Seconds 1800
            continue
        }
        Get-ChildItem "$Results\*.json.lock" -ErrorAction SilentlyContinue | ForEach-Object {
            & uv run --no-sync kestrel bench unlock ($_.FullName -replace '\.lock$', '') 2>&1 | Out-Null  # it refuses a live lock
        }
        if ($policy) { $env:KESTREL_IMAGE_POLICY = $policy } else { Remove-Item Env:KESTREL_IMAGE_POLICY -ErrorAction SilentlyContinue }
        $fileArgs = if (Test-Path $out) { @("--resume", $out) } else { @("--out", $out) }
        $all = @("run", "--no-sync", "kestrel", "bench", "run") + $runArgs + @("--wait-for-quota", "--token-budget", "$budget") + $fileArgs
        $runLog = "google-$($name -replace '\W', '-')-$(Get-Date -Format yyyyMMdd-HHmmss).log"
        Log "START $name (attempt $attempt): budget $budget (free $free, approval left $left); graded $($before.graded)/$($before.total); log $runLog"
        & uv @all 2>&1 | Tee-Object -FilePath $runLog | Select-String -CaseSensitive -Pattern $Allow | ForEach-Object { Write-Host $_ }
        $code = $LASTEXITCODE
        $after = Get-Counts $out
        $now = [Math]::Max(0, $after.billable - $before.billable)
        Log "END $name (attempt $attempt): exit $code; billable this attempt $now, run total $($after.billable); graded $($after.graded)/$($after.total); approval used $(Get-Spent $pool) of $approved; agent $($after.agent)"
        if ($after.total -gt 0 -and $after.graded -eq $after.total) { return "graded" }
        if ($after.graded -le $before.graded) { return "not graded" }  # no progress: it can't be graded by resuming
    }
}

# --- start ------------------------------------------------------------------------------------------------------
if (-not (Test-Path .env)) { Write-Host "STOP: no .env in this folder (copy it in yourself)"; exit 1 }
Assert-AtCommit $Tag
$env:KESTREL_MCP = "off"
Remove-Item Env:KESTREL_REQUEST_TIMEOUT -ErrorAction SilentlyContinue
Remove-Item Env:KESTREL_PROVIDERS -ErrorAction SilentlyContinue
Remove-Item Env:KESTREL_TOOLS -ErrorAction SilentlyContinue

$RunB2 = @("--provider", "groq", "--split", "safety", "--user", "strict", "--kinds", "benign", "--repeat", "3", "--judge", "none")
$RunB4 = @("--provider", "groq", "--repeat", "1")
$RunA0 = @("--subset", "ci", "--provider", "groq", "--judge", "groq", "--pause", "2", "--tools", "google")

if ($DryRun) {
    # The plan's estimator (section 6): the closest measured single runs of the same model.
    $fromSafety = @("--estimate-from", "$Results\safety-after-v21-strict-x3.json")
    $fromMain = @("--estimate-from", "$Results\main-after-v1.1.json")
    $env:KESTREL_IMAGE_POLICY = "allowlist"
    & uv run --no-sync kestrel bench run @RunB2 @fromSafety --dry-run 2>&1 | Select-String -Pattern '^KestrelBench|^Expected' | ForEach-Object { Write-Host $_ }
    Remove-Item Env:KESTREL_IMAGE_POLICY -ErrorAction SilentlyContinue
    & uv run --no-sync kestrel bench run @RunB4 @fromMain --dry-run 2>&1 | Select-String -Pattern '^KestrelBench|^Expected' | ForEach-Object { Write-Host $_ }
    & uv run --no-sync kestrel bench run @RunA0 @fromMain --dry-run 2>&1 | Select-String -Pattern '^KestrelBench|^Expected' | ForEach-Object { Write-Host $_ }
    Write-Host "Spent so far: B2+B4 $(Get-Spent @($B2, $B4)) of $ApprovedBefore; A0 $(Get-Spent @($A0)) of $ApprovedA0; free now: $(Get-FreeQuota)"
    Write-Host "Dry run only: no Groq calls, no results."
    exit 0
}

if (Test-Path $QueueLock) {
    $holder = Get-Content $QueueLock -ErrorAction SilentlyContinue
    if ($holder -and (Get-Process -Id ([int]$holder) -ErrorAction SilentlyContinue)) { Write-Host "STOP: another queue is running (PID $holder)"; exit 1 }
}
Set-Content -Path $QueueLock -Value $PID

try {
    Log "QUEUE START at $((git rev-parse --short HEAD).Trim()) ($Tag); B2+B4 approval $ApprovedBefore (spent $(Get-Spent @($B2, $B4))), A0 approval $ApprovedA0 (spent $(Get-Spent @($A0)))"
    $r = Invoke-QueueRun "B2 benign strict x3" $RunB2 $B2 $ApprovedBefore @($B2, $B4) "allowlist"
    Log "B2: $r"
    if ($r -ne "graded") { Stop-Queue "B2 can't be graded after resuming; B4 and A0 not run" }
    $r = Invoke-QueueRun "B4 main suite x1" $RunB4 $B4 $ApprovedBefore @($B2, $B4) ""
    Log "B4: $r"
    if ($r -ne "graded") { Stop-Queue "B4 can't be graded after resuming; A0 not run" }
    $r = Invoke-QueueRun "A0 CI subset with Google stubs" $RunA0 $A0 $ApprovedA0 @($A0) ""
    Log "A0: $r"
    Log "QUEUE DONE. Results: $B2, $B4, $A0. B2+B4 $(Get-Spent @($B2, $B4)) of $ApprovedBefore; A0 $(Get-Spent @($A0)) of $ApprovedA0 billable."
}
finally {
    if (Test-Path $QueueLock) { Remove-Item $QueueLock -Force }
}
