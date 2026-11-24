# The data step's runs, in order (docs/data-rca-design.md 5.2, 5.3): B1, A1, A1b, then A2 and A3 only if R1 and R2
# pass. One Groq job at a time, all on the tag data-before (the merged data build; B1 without the data tools, the rest
# with --tools data). No run starts without the owner's approval line in STATUS on main: every cap is a parameter, and
# a cap of 0 (the default) stops the queue before that run.
#
# It runs from a FROZEN COPY outside the repo:
#
#   uv run --no-sync python scripts/data_freeze.py --commit main --dest C:\kestrel-data-queue   # once, from the dev folder
#   cd ..\kestrel-runs                                    # a standalone PowerShell window (Start menu, not VS Code)
#   & C:\kestrel-data-queue\data_queue.ps1 -DryRun        # checks and dry runs only: no model calls
#   & C:\kestrel-data-queue\data_queue.ps1 -CapB1 <n> -CapA1 <n> -CapA1b <n>
#   # it stops before A2 and before each part of A3; after the approval line, start it again with the old caps and the
#   # new one(s): finished runs are skipped
#   & C:\kestrel-data-queue\data_queue.ps1 -CapB1 <n> -CapA1 <n> -CapA1b <n> -CapA2 <n> -CapA3First <n>
#   & C:\kestrel-data-queue\data_queue.ps1 -CapB1 <n> -CapA1 <n> -CapA1b <n> -CapA2 <n> -CapA3First <n> -CapA3 <n>
#   Get-Content evals\results\data-queue.log -Tail 25     # progress (from any window)
#
# Keep the window open and the laptop on mains power and awake (Modern Standby pauses runs). It never opens .env.
# The copy holds this file and the two helpers it calls (groq_quota.py, data_gate.py); it refuses to start if a
# helper's SHA-256 isn't the one pinned below, or if it runs from inside a git folder (i.e. not the frozen copy).
#
# B1   main suite x1, default agent (R3a, R3b)             -> evals\results\data-before-b1-main-x1.json
# A1   attacks, strict x3 (30 old incl. 1 probe + 5 data; R1, R2) -> evals\results\data-a1-attacks-strict-x3.json
# A1b  the 5 data twins, strict x3 (R5)                    -> evals\results\data-a1b-twins-strict-x3.json
# A2   main suite x1 (R3a, R3b); not run if R1 or R2 fails -> evals\results\data-a2-main-x1.json
# A3   rca x1 (R4), one file: the first 5 tasks (one per scenario, the cost check), then the other 25 by --resume;
#      not run if R1 or R2 fails                           -> evals\results\data-a3-rca-x1.json
#
# Caps: counted from each run's own results file, so a restarted queue still stops at the cap. -CapA3 covers the whole
# A3 file, the first 5 included. Why the 20,000 margin: --token-budget is checked only before each task starts
# (KI-pending-token-budget-between-tasks), so an attempt can end one task past its budget. Each attempt's budget is the
# smaller of groq_quota.py's free amount and (cap - spent - margin). No attempt starts with a budget under one margin
# (it waits for quota, or stops if the cap is what's short), and none starts while free quota reads 0 or less. So a cap
# under 40,000 can never start a run.
#
# Rules from the design, enforced here:
# - 5.2 stop rule: if R1 or R2 fails, A2 and A3 are not run (data_gate.py prints one word; NOT SHOWN stops the queue
#   for the owner). Errored runs are resumed (--resume), never restarted.
# - 5.4: nothing starts while a Google queue's lock is present or another bench run is live.
# - question 8: kestrel bench validate must be OK with 0 TODO before anything runs.
# - Settings: KESTREL_MCP=off; safety runs with today's image policy (allowlist), B1, A2 and A3 without.
# The queue log holds times, billable tokens, graded counts and the gate's one word: never which run passed.
param(
    [switch]$DryRun,
    [int]$CapB1 = 0,
    [int]$CapA1 = 0,
    [int]$CapA1b = 0,
    [int]$CapA2 = 0,
    [int]$CapA3First = 0,
    [int]$CapA3 = 0
)
$ErrorActionPreference = "Stop"

$Tag = "data-before"
$Margin = 20000            # >= the most expensive single main-suite task measured (web-weather-tokyo, B4: 18,429)
$NeedFree = $Margin        # never start an attempt with a budget under one task
$Pins = @{
    "groq_quota.py" = "1b86d0432ab5d883cfd7bd839f975f0fccf7e394692e97e23170f9e973bcfb5a"
    "data_gate.py"  = "6438327d042918d9d2fdd1ad4f945507956bf8f40238d70784fd44cd76f279af"
}
$Results = "evals\results"
$B1 = "$Results\data-before-b1-main-x1.json"
$A1 = "$Results\data-a1-attacks-strict-x3.json"
$A1b = "$Results\data-a1b-twins-strict-x3.json"
$A2 = "$Results\data-a2-main-x1.json"
$A3 = "$Results\data-a3-rca-x1.json"
$QueueLog = "$Results\data-queue.log"
$QueueLock = "$Results\data-queue.lock"
$Allow = 'Expected:|fingerprint|Traceback|Error\b|Error:|RUN STOPPED|KeyboardInterrupt|\[quota\]|\[checkpoint\]|To finish later|already exists|stale lock'
$Here = (Get-Location).Path
$Frozen = $PSScriptRoot
$Quota = Join-Path $Frozen "groq_quota.py"
$Gate = Join-Path $Frozen "data_gate.py"
$DevFolder = Join-Path $Here "..\kestrel"   # read only, by groq_quota.py: its traces and results count too

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

function Assert-FrozenCopy {
    $dir = Get-Item $Frozen
    while ($dir) {
        if (Test-Path (Join-Path $dir.FullName ".git")) { Write-Host "STOP: run the frozen copy (C:\kestrel-data-queue), not a file inside a git folder ($Frozen)"; exit 1 }
        $dir = $dir.Parent
    }
    foreach ($name in $Pins.Keys) {
        $path = Join-Path $Frozen $name
        if (-not (Test-Path $path)) { Write-Host "STOP: $name is missing next to the queue"; exit 1 }
        $have = (Get-FileHash -Algorithm SHA256 $path).Hash.ToLower()
        if ($have -ne $Pins[$name]) { Write-Host "STOP: $name has SHA-256 $have, not the pinned $($Pins[$name])"; exit 1 }
    }
}

function Assert-AtCommit([string]$ref) {
    git rev-parse -q --verify "$ref^{commit}" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "STOP: no tag $ref"; exit 1 }
    $want = (git rev-parse "$ref^{commit}").Trim()
    $have = (git rev-parse HEAD).Trim()
    if ($want -ne $have) { Write-Host "STOP: this folder is at $($have.Substring(0, 7)), not $ref"; exit 1 }
    if (git status --porcelain --untracked-files=no) { Write-Host "STOP: tracked files changed in this folder"; exit 1 }
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
    $out = & uv run --no-sync python $Quota --runs $Here --dev $DevFolder 2>&1
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

function Test-StepDone($counts, [int]$runs) {
    # A step is done at $runs graded runs. A3 is one file: 5 runs after the cost check, then 30.
    return ($counts.total -gt $runs) -or ($counts.total -eq $runs -and $counts.graded -eq $runs)
}

function Get-AttemptDecision([int]$approved, [int]$spent, [int]$free) {
    # start / wait / stop, from numbers only (tested offline). An attempt can end one task past its budget, so
    # budget + one task (<= $Margin) stays within the cap.
    $left = $approved - $spent - $Margin
    if ($left -lt $NeedFree) {
        return @{ action = "stop"; budget = 0; left = $left; why = "the cap of $approved billable can't cover another attempt ($spent spent; $left left after the $Margin margin, an attempt needs $NeedFree)" }
    }
    if ($free -le 0) {
        return @{ action = "wait"; budget = 0; left = $left; why = "free quota reads $free (0 or less), cap left $left" }
    }
    $budget = [Math]::Min($free, $left)
    if ($budget -lt $NeedFree) {
        return @{ action = "wait"; budget = $budget; left = $left; why = "$budget billable available now (free $free, cap left $left), needs $NeedFree" }
    }
    return @{ action = "start"; budget = $budget; left = $left; why = "" }
}

function Invoke-QueueRun([string]$name, [string[]]$runArgs, [string]$out, [int]$runs, [int]$cap, [string]$capName, [string]$policy) {
    $done = Get-Counts $out
    if (Test-StepDone $done $runs) {
        Log "${name}: already graded ($($done.graded)/$($done.total), $($done.billable) billable); skipped"
        return "graded"
    }
    if ($cap -le 0) { Stop-Queue "${name}: no approved cap. Add the owner's approval line to STATUS on main, then start the queue again with -$capName <billable>" }
    $attempt = 0
    while ($true) {
        $attempt += 1
        Wait-NoOtherGroqJob
        $before = Get-Counts $out
        $spent = $before.billable
        $free = if ($cap - $spent - $Margin -ge $NeedFree) { Get-FreeQuota } else { 0 }
        $d = Get-AttemptDecision $cap $spent $free
        if ($d.action -eq "stop") { Stop-Queue "${name}: $($d.why)" }
        $left = $d.left
        $budget = $d.budget
        if ($d.action -eq "wait") {
            Log "${name}: $($d.why); waiting 30 min"
            Start-Sleep -Seconds 1800
            continue
        }
        Get-ChildItem "$Results\*.json.lock" -ErrorAction SilentlyContinue | ForEach-Object {
            & uv run --no-sync kestrel bench unlock ($_.FullName -replace '\.lock$', '') 2>&1 | Out-Null  # it refuses a live lock
        }
        if ($policy) { $env:KESTREL_IMAGE_POLICY = $policy } else { Remove-Item Env:KESTREL_IMAGE_POLICY -ErrorAction SilentlyContinue }
        $fileArgs = if (Test-Path $out) { @("--resume", $out) } else { @("--out", $out) }
        $all = @("run", "--no-sync", "kestrel", "bench", "run") + $runArgs + @("--wait-for-quota", "--token-budget", "$budget") + $fileArgs
        $runLog = "data-$($name.Split(' ')[0])-$(Get-Date -Format yyyyMMdd-HHmmss).log"
        Log "START $name (attempt $attempt): budget $budget (free $free, cap left $left); graded $($before.graded)/$($before.total); log $runLog"
        & uv @all 2>&1 | Tee-Object -FilePath $runLog | Select-String -CaseSensitive -Pattern $Allow | ForEach-Object { Write-Host $_ }
        $code = $LASTEXITCODE
        $after = Get-Counts $out
        $now = [Math]::Max(0, $after.billable - $before.billable)
        Log "END $name (attempt $attempt): exit $code; billable this attempt $now, run total $($after.billable) of cap $cap; graded $($after.graded)/$($after.total); agent $($after.agent)"
        if (Test-StepDone $after $runs) { return "graded" }
        if ($after.graded -le $before.graded) { return "not graded" }  # no progress: it can't be graded by resuming
    }
}

function Write-DriftNote([string]$b1, [string]$a2) {
    # 5.2: the control is reported, not a gate, so the queue goes on; the control is its own approved run.
    if ((Test-Path $a2) -or -not (Test-Path $b1)) { return }
    $data = Get-Content $b1 -Raw -Encoding UTF8 | ConvertFrom-Json
    $first = @($data.meta.attempts)[0].started
    if (-not $first) { return }
    # PowerShell 7 turns ISO dates in JSON into DateTime; Windows PowerShell 5.1 leaves text
    $from = if ($first -is [datetime]) { [DateTimeOffset]$first } else { [DateTimeOffset]::Parse($first) }
    if (([DateTimeOffset]::Now - $from).TotalDays -gt 7) {
        Log "DRIFT CONTROL NEEDED (design 5.2): A2 starts more than 7 days after B1. The before agent on the CI subset x2 (~67.5k in the design) needs its own approval line; it is reported, not a gate"
    }
}

function Get-Gate {
    $commit = (git rev-parse HEAD).Trim()
    $out = & uv run --no-sync python $Gate $A1 --commit $commit 2>&1
    $code = $LASTEXITCODE
    $word = ($out | Select-Object -Last 1).ToString().Trim()
    $want = @{ "PASS" = 0; "FAIL" = 1; "NOT SHOWN" = 2 }
    if (-not $want.ContainsKey($word) -or $want[$word] -ne $code) { return "NOT SHOWN" }  # anything unexpected
    return $word
}

# --- start ------------------------------------------------------------------------------------------------------
Assert-FrozenCopy
if (-not (Test-Path .env)) { Write-Host "STOP: no .env in this folder (copy it in yourself)"; exit 1 }
if (-not (Test-Path (Join-Path $DevFolder "pyproject.toml"))) { Write-Host "STOP: no development folder at $DevFolder (groq_quota.py counts its use)"; exit 1 }
foreach ($lock in @("google-before-queue.lock", "google-after-queue.lock")) {
    if (Test-Path "$Results\$lock") { Write-Host "STOP: a Google queue's lock is present ($lock): one Groq consumer at a time"; exit 1 }
}
if (Test-Path $QueueLock) {
    $holder = Get-Content $QueueLock -ErrorAction SilentlyContinue
    if ($holder -and (Get-Process -Id ([int]$holder) -ErrorAction SilentlyContinue)) { Write-Host "STOP: another queue is running (PID $holder)"; exit 1 }
}
New-Item -ItemType Directory -Force $Results | Out-Null
Assert-AtCommit $Tag
$env:KESTREL_MCP = "off"
Remove-Item Env:KESTREL_REQUEST_TIMEOUT -ErrorAction SilentlyContinue
Remove-Item Env:KESTREL_PROVIDERS -ErrorAction SilentlyContinue
Remove-Item Env:KESTREL_TOOLS -ErrorAction SilentlyContinue
Remove-Item Env:KESTREL_IMAGE_POLICY -ErrorAction SilentlyContinue

$RunB1 = @("--provider", "groq", "--repeat", "1")
$RunA1 = @("--provider", "groq", "--split", "safety", "--user", "strict", "--kinds", "attack", "--repeat", "3", "--judge", "none", "--tools", "data")
$RunA1b = @("--provider", "groq", "--split", "safety", "--user", "strict", "--kinds", "benign", "--tasks", "ben-data-*", "--repeat", "3", "--judge", "none", "--tools", "data")
$RunA2 = @("--provider", "groq", "--repeat", "1", "--tools", "data")
$RunA3 = @("--provider", "groq", "--split", "rca", "--repeat", "1", "--tools", "data")
$RunA3First = $RunA3 + @("--tasks", "rca-*-1")

$checked = & uv run --no-sync kestrel bench validate --split safety 2>&1   # never printed: it may quote a task
if ($LASTEXITCODE -ne 0 -or @($checked | Select-String -CaseSensitive -Pattern '\bTODO\b').Count -gt 0) {
    Write-Host "STOP: kestrel bench validate --split safety isn't OK with 0 TODO"
    exit 1
}

if ($DryRun) {
    $show = { param($run) & uv run --no-sync kestrel bench run @run --dry-run 2>&1 | Select-String -Pattern '^KestrelBench|^Expected' | ForEach-Object { Write-Host $_ } }
    & $show $RunB1
    $env:KESTREL_IMAGE_POLICY = "allowlist"
    & $show $RunA1
    & $show $RunA1b
    Remove-Item Env:KESTREL_IMAGE_POLICY -ErrorAction SilentlyContinue
    & $show $RunA2
    & $show $RunA3First
    & $show $RunA3
    foreach ($p in @(@("B1", $B1, $CapB1), @("A1", $A1, $CapA1), @("A1b", $A1b, $CapA1b), @("A2", $A2, $CapA2), @("A3", $A3, $CapA3))) {
        $c = Get-Counts $p[1]
        Write-Host "$($p[0]): $($c.billable) of cap $($p[2]) spent; graded $($c.graded)/$($c.total)"
    }
    Write-Host "Helpers match their pins; free now: $(Get-FreeQuota)"
    Write-Host "Dry run only: no Groq calls, no results. A cap of 0 is a run with no approval."
    exit 0
}

Set-Content -Path $QueueLock -Value $PID

try {
    Log "QUEUE START at $((git rev-parse --short HEAD).Trim()) ($Tag), frozen copy $Frozen; caps B1 $CapB1, A1 $CapA1, A1b $CapA1b, A2 $CapA2, A3 first 5 $CapA3First, A3 $CapA3; margin $Margin"
    $r = Invoke-QueueRun "B1 main suite x1 before" $RunB1 $B1 100 $CapB1 "CapB1" ""
    Log "B1: $r"
    if ($r -ne "graded") { Stop-Queue "B1 can't be graded after resuming; nothing else run" }
    $r = Invoke-QueueRun "A1 attacks strict x3" $RunA1 $A1 105 $CapA1 "CapA1" "allowlist"
    Log "A1: $r"
    if ($r -ne "graded") { Stop-Queue "A1 can't be graded after resuming; A1b, A2 and A3 not run" }
    $r = Invoke-QueueRun "A1b data twins strict x3" $RunA1b $A1b 15 $CapA1b "CapA1b" "allowlist"
    Log "A1b: $r"
    if ($r -ne "graded") { Stop-Queue "A1b can't be graded after resuming; A2 and A3 not run" }
    $g = Get-Gate
    Log "R1 and R2 (data_gate.py on A1): $g"
    if ($g -eq "FAIL") {
        Log "QUEUE DONE without A2 and A3: R1 or R2 failed (design 5.2 stop rule; the outcome is FAIL). B1 $((Get-Counts $B1).billable), A1 $((Get-Counts $A1).billable), A1b $((Get-Counts $A1b).billable) billable."
        return
    }
    if ($g -ne "PASS") { Stop-Queue "R1/R2 not shown (an unfinished or errored A1, or not the rule's attack set): A2 and A3 not run; an owner decision" }
    Write-DriftNote $B1 $A2
    $r = Invoke-QueueRun "A2 main suite x1 after" $RunA2 $A2 100 $CapA2 "CapA2" ""
    Log "A2: $r"
    if ($r -ne "graded") { Stop-Queue "A2 can't be graded after resuming; A3 not run" }
    $r = Invoke-QueueRun "A3 rca, first 5 tasks" $RunA3First $A3 5 $CapA3First "CapA3First" ""
    Log "A3 first 5: $r; A3's file is at $((Get-Counts $A3).billable) billable"
    if ($r -ne "graded") { Stop-Queue "A3's first 5 can't be graded after resuming" }
    $r = Invoke-QueueRun "A3 rca, all 30 tasks" $RunA3 $A3 30 $CapA3 "CapA3" ""
    Log "A3: $r"
    if ($r -ne "graded") { Stop-Queue "A3 can't be graded after resuming" }
    Log "QUEUE DONE. B1 $((Get-Counts $B1).billable) of $CapB1, A1 $((Get-Counts $A1).billable) of $CapA1, A1b $((Get-Counts $A1b).billable) of $CapA1b, A2 $((Get-Counts $A2).billable) of $CapA2, A3 $((Get-Counts $A3).billable) of $CapA3 billable."
}
finally {
    if (Test-Path $QueueLock) { Remove-Item $QueueLock -Force }
}
