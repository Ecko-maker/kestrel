# Section 9's four runs in order (docs/phase4-section9-proposal.md 9.4), from ..\kestrel-runs at the tag
# phase4-s9-runs, in a standalone PowerShell window (Start menu, not VS Code). Keep the window open and the
# laptop on mains power. It never opens .env.
#
#   .\scripts\s9_queue.ps1            # run 1, stop rule, run 2, stop rule, run 3, run 4
#   .\scripts\s9_queue.ps1 -DryRun    # runs 1, 2 and 4 with --dry-run: no model calls, no folder move
#   Get-Content evals\results\s9-queue.log -Tail 25    # progress (from any window)
#
# 1  group S strict x3          -> evals\results\s9-safety-S-strict-x3.json
# 2  group S rubber-stamp x3    -> evals\results\s9-safety-S-rubber-attacks-x3.json
# 3  drift control, old agent   -> evals\results\control-old-ci-<date>.json (this folder moved to c7c0d4f and back)
# 4  routed main suite R=1      -> evals\results\s9-main-r1.json
#
# Runs 1, 2 and 4: --wait-for-quota, and --resume once the file exists (--out the first time). Run 3 runs the
# old agent's code, which has no --wait-for-quota (as on 11-03), so the queue waits until 55,000 is free first.
# Each --token-budget is the smaller of groq_quota.py's free amount and what is left of the owner's approval,
# counted from the results files themselves, so a restarted queue still stops at the approval. A run with
# ungraded runs left is resumed while each attempt makes progress; one that stops making progress "can't be
# graded after resuming". The stop rule (9.3, scripts/s9_gate.py) is checked offline after run 1 and after
# run 2; if it fires, the queue stops and the log says why. One Groq job at a time: the queue waits while
# any other `kestrel bench run/rejudge/calibrate` runs, and refuses to start while another queue holds
# evals\results\s9-queue.lock. The log (evals\results\s9-queue.log) holds times, billable tokens and graded
# counts: never an outcome. The run logs (s9-*.log) hold outcomes: don't open them before the end.
param([switch]$DryRun)
$ErrorActionPreference = "Stop"

$Tag = "phase4-s9-runs"
$DriftCommit = "c7c0d4f"
$OllamaVersion = "0.40.14"
$Approved = 303000      # the owner's approval for section 9 (STATUS, 2026-11-07)
$SpentOnCi = 55952      # PR #79's two CI runs (31,604 + 24,348)
$DriftNeeds = 55000     # run 3 can't wait for quota itself: start it only with this much free
$Results = "evals\results"
$Strict = "$Results\s9-safety-S-strict-x3.json"
$Rubber = "$Results\s9-safety-S-rubber-attacks-x3.json"
$Main = "$Results\s9-main-r1.json"
$QueueLog = "$Results\s9-queue.log"
$QueueLock = "$Results\s9-queue.lock"
$Allow = 'Expected:|fingerprint|Traceback|Error\b|Error:|RUN STOPPED|KeyboardInterrupt|\[quota\]|\[checkpoint\]|ROUTER STOPPED|To finish later|already exists|stale lock'
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

function Test-Ollama {
    $v = (Invoke-RestMethod http://localhost:11434/api/version).version
    if ($v -ne $OllamaVersion) { Stop-Queue "Ollama is $v, the measurement runs on $OllamaVersion (distill/EVAL.md step 0)" }
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

function Get-DriftFile {
    # This week's drift control, if one was started; else a new one dated today.
    $existing = Get-ChildItem "$Results\control-old-ci-*.json" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne "control-old-ci-2026-11-03.json" -and $_.LastWriteTime -gt (Get-Date).AddDays(-6) } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($existing) { return "$Results\$($existing.Name)" }
    return "$Results\control-old-ci-$(Get-Date -Format yyyy-MM-dd).json"
}

function Get-SectionSpent {
    # From the results files, so a restarted queue counts what earlier attempts saved.
    $sum = $SpentOnCi
    foreach ($f in @($Strict, $Rubber, (Get-DriftFile), $Main)) { $sum += (Get-Counts $f).billable }
    return $sum
}

function Invoke-QueueRun([string]$name, [string[]]$runArgs, [string]$out, [string]$policy, [bool]$waitForQuota, [int]$needFree) {
    $attempt = 0
    while ($true) {
        $attempt += 1
        Wait-NoOtherGroqJob
        if ($runArgs -contains "routed") { Test-Ollama }
        $before = Get-Counts $out
        $spent = Get-SectionSpent
        $left = $Approved - $spent
        if ($left -le 0) { Stop-Queue "the approval of $Approved billable is used up ($spent spent)" }
        $free = Get-FreeQuota
        $budget = [Math]::Min($free, $left)
        if ($budget -lt $needFree) {
            Log "${name}: $budget billable available now (free $free, approval left $left), needs $needFree; waiting 30 min"
            Start-Sleep -Seconds 1800
            continue
        }
        Get-ChildItem "$Results\*.json.lock" -ErrorAction SilentlyContinue | ForEach-Object {
            & uv run --no-sync kestrel bench unlock ($_.FullName -replace '\.lock$', '') 2>&1 | Out-Null  # it refuses a live lock
        }
        if ($policy) { $env:KESTREL_IMAGE_POLICY = $policy } else { Remove-Item Env:KESTREL_IMAGE_POLICY -ErrorAction SilentlyContinue }
        $fileArgs = if (Test-Path $out) { @("--resume", $out) } else { @("--out", $out) }
        $quotaArgs = if ($waitForQuota) { @("--wait-for-quota") } else { @() }
        $all = @("run", "--no-sync", "kestrel", "bench", "run") + $runArgs + $quotaArgs + @("--token-budget", "$budget") + $fileArgs
        $runLog = "s9-$($name -replace '\W', '-')-$(Get-Date -Format yyyyMMdd-HHmmss).log"
        Log "START $name (attempt $attempt): budget $budget (free $free, approval left $left); graded $($before.graded)/$($before.total); log $runLog"
        & uv @all 2>&1 | Tee-Object -FilePath $runLog | Select-String -CaseSensitive -Pattern $Allow | ForEach-Object { Write-Host $_ }
        $code = $LASTEXITCODE
        $after = Get-Counts $out
        $now = [Math]::Max(0, $after.billable - $before.billable)
        Log "END $name (attempt $attempt): exit $code; billable this attempt $now, run total $($after.billable); graded $($after.graded)/$($after.total); section 9 total $(Get-SectionSpent) of $Approved; agent $($after.agent)"
        if ($after.total -gt 0 -and $after.graded -eq $after.total) { return "graded" }
        if ($after.graded -le $before.graded) { return "not graded" }  # no progress: it can't be graded by resuming
    }
}

function Test-StopRule([string]$after, [string[]]$gateArgs) {
    $outcome = ((& uv run --no-sync python scripts\s9_gate.py @gateArgs) | Select-Object -Last 1).Trim()
    Log "stop rule after ${after}: $outcome"
    if ($outcome -eq "FAILED") { Stop-Queue "stop rule (9.3) after ${after}: a system-level attack success. Outcome FAIL (9.6); the rest is not run." }
    if ($outcome -ne "HELD") { Stop-Queue "stop rule (9.3) after ${after}: a run can't be graded after resuming. Outcome NOT SHOWN; the rest is not run." }
}

# --- start ------------------------------------------------------------------------------------------------------
if (-not (Test-Path .env)) { Write-Host "STOP: no .env in this folder (copy it in yourself)"; exit 1 }
Assert-AtCommit $Tag

# The router, for this window only (docs/phase4-plan.md 8.7); the request timeout stays the default 60 s.
$env:KESTREL_ROUTER_STUDENT = "ollama"; $env:KESTREL_ROUTER_STUDENT_MODEL = "kestrel-student"
$env:KESTREL_ROUTER_TEACHER = "groq"; $env:KESTREL_ROUTER_TEACHER_MODEL = "openai/gpt-oss-120b"
$env:KESTREL_ROUTER_LINEAGE = "C:\kestrel-backup\2026-11-02\student\lineage.json"
$env:KESTREL_ROUTER_STUDENT_CTX = "16384"; $env:KESTREL_MCP = "off"
Remove-Item Env:KESTREL_REQUEST_TIMEOUT -ErrorAction SilentlyContinue
Remove-Item Env:KESTREL_PROVIDERS -ErrorAction SilentlyContinue

$Run1 = @("--provider", "routed", "--split", "safety", "--safety-group", "S", "--user", "strict", "--repeat", "3", "--judge", "none")
$Run2 = @("--provider", "routed", "--split", "safety", "--safety-group", "S", "--user", "rubber-stamp", "--kinds", "attack", "--repeat", "3", "--judge", "none")
$Run3 = @("--subset", "ci", "--repeat", "2")
$Run4 = @("--provider", "routed", "--repeat", "1", "--judge", "none")

if ($DryRun) {
    $env:KESTREL_IMAGE_POLICY = "allowlist"
    & uv run --no-sync kestrel bench run @Run1 --dry-run 2>&1 | Select-String -Pattern '^KestrelBench|^Expected|fingerprint' | ForEach-Object { Write-Host $_ }
    & uv run --no-sync kestrel bench run @Run2 --dry-run 2>&1 | Select-String -Pattern '^KestrelBench|^Expected|fingerprint' | ForEach-Object { Write-Host $_ }
    Remove-Item Env:KESTREL_IMAGE_POLICY -ErrorAction SilentlyContinue
    & uv run --no-sync kestrel bench run @Run4 --dry-run 2>&1 | Select-String -Pattern '^KestrelBench|^Expected|fingerprint' | ForEach-Object { Write-Host $_ }
    Write-Host "Section 9 spent so far: $(Get-SectionSpent) of $Approved; free now: $(Get-FreeQuota)"
    Write-Host "Dry run only: no Groq, no results, no move to $DriftCommit."
    exit 0
}

if (Test-Path $QueueLock) {
    $holder = Get-Content $QueueLock -ErrorAction SilentlyContinue
    if ($holder -and (Get-Process -Id ([int]$holder) -ErrorAction SilentlyContinue)) { Write-Host "STOP: another queue is running (PID $holder)"; exit 1 }
}
Set-Content -Path $QueueLock -Value $PID

try {
    Log "QUEUE START at $((git rev-parse --short HEAD).Trim()) ($Tag); approval $Approved, spent so far $(Get-SectionSpent)"

    Invoke-QueueRun "run 1 group S strict x3" $Run1 $Strict "allowlist" $true 5000 | Out-Null
    Test-StopRule "run 1" @("--strict", $Strict)

    Invoke-QueueRun "run 2 group S rubber-stamp x3" $Run2 $Rubber "allowlist" $true 5000 | Out-Null
    Test-StopRule "run 2" @("--strict", $Strict, "--rubber", $Rubber)

    $drift = Get-DriftFile
    Log "moving this folder to $DriftCommit for run 3"
    Push-Location $Repo; & .\scripts\runs_folder.ps1 $DriftCommit | Out-Null; Pop-Location
    Assert-AtCommit $DriftCommit
    $result3 = Invoke-QueueRun "run 3 drift control" $Run3 $drift "" $false $DriftNeeds
    Log "run 3: $result3 (its compare is read after all runs, distill/EVAL.md step 5)"
    Log "moving this folder back to $Tag"
    Push-Location $Repo; & .\scripts\runs_folder.ps1 $Tag | Out-Null; Pop-Location
    Assert-AtCommit $Tag

    $result4 = Invoke-QueueRun "run 4 routed main suite R=1" $Run4 $Main "" $true 5000
    Log "run 4: $result4"
    Log "QUEUE DONE. Results: $Strict, $Rubber, $drift, $Main. Section 9 total $(Get-SectionSpent) of $Approved billable."
}
finally {
    if (Test-Path $QueueLock) { Remove-Item $QueueLock -Force }
}
