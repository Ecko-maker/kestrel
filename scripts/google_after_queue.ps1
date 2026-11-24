# The Google step's after side, in order (docs/google-step-plan.md sections 4 and 6): A1, A2, A3, then A4 only if
# G1 and G2 pass. One Groq job at a time, all on the tag google-before with --tools google (the 2a build, opt-in:
# plan 4, "Sides"; there is no separate after tag).
#
# It runs from a FROZEN COPY outside the repo, so the development folder stays free for the ~9 days:
#
#   uv run --no-sync python scripts/google_after_freeze.py --commit main --dest C:\kestrel-queue   # once, from the dev folder
#   cd ..\kestrel-runs                                    # a standalone PowerShell window (Start menu, not VS Code)
#   & C:\kestrel-queue\google_after_queue.ps1             # A1, A2, A3, A4
#   & C:\kestrel-queue\google_after_queue.ps1 -DryRun     # checks and dry runs only: no model calls
#   Get-Content evals\results\google-after-queue.log -Tail 25     # progress (from any window)
#
# Keep the window open and the laptop on mains power and awake (Modern Standby pauses runs). It never opens .env.
# The copy holds this file and the two helpers it calls (groq_quota.py, google_gate.py); it refuses to start if a
# helper's SHA-256 isn't the one pinned below, or if it runs from inside a git folder (i.e. not the frozen copy).
#
# A1  attacks, strict x3 (30 old incl. 1 probe + 8 email; G1, G2) -> evals\results\google-after-a1-attacks-strict-x3.json
# A2  benign, strict x3 (18 old + 8 twins; G4, G5)                 -> evals\results\google-after-a2-benign-strict-x3.json
# A3  rubber-stamp x1 on the 8 email attacks (reported)            -> evals\results\google-after-a3-rubber-email-x1.json
# A4  main suite x1, judge groq (G3a, G3b); not run if G1 or G2 fails -> evals\results\google-after-a4-main-x1.json
#
# Caps: each run has its own, the estimate x 1.15 (A0's measured f; the before side's overrun), counted from its
# own results file, so a restarted queue still stops at the cap. They take effect only with the owner's approval
# line in STATUS on main. Why the 20,000 margin: --token-budget is checked only before each task starts
# (KI-pending-token-budget-between-tasks), so an attempt can end one task past its budget; the margin is at least
# the most expensive single task measured (web-weather-tokyo, B4: 18,429). Each attempt's --token-budget is the
# smaller of groq_quota.py's free amount and (cap - spent - margin). No attempt starts with a budget under one
# margin (it waits for quota, or stops if the cap is what's short), and none starts while free quota reads 0 or
# less.
#
# Rules from the plan, enforced here:
# - 4.2 stop rule: if G1 or G2 fails, A4 is not run (google_gate.py prints one word; NOT SHOWN stops the queue for
#   the owner). Errored runs are resumed (--resume), never restarted.
# - 4.3: A1 must start within 7 days of the before side's last run (B4's last attempt ended 2026-11-22 23:36:45
#   EST); after that the queue refuses to start A1 (the drift control comes first: an owner decision).
# - 5: the main suite and the safety split run on separate days: A4 starts no earlier than the day after the last
#   safety results file was written.
# - Settings as the before side: KESTREL_MCP=off; safety runs with today's image policy (allowlist), A4 without.
# The queue log holds times, billable tokens, graded counts and the gate's one word: never which run passed.
param([switch]$DryRun)
$ErrorActionPreference = "Stop"

$Tag = "google-before"
$A1StartBy = [DateTimeOffset]::Parse("2026-11-29T23:36:45-05:00")   # B4 ended 2026-11-23T04:36:45Z, + 7 days
$Margin = 20000            # >= the most expensive single task measured (web-weather-tokyo, B4: 18,429)
$NeedFree = $Margin        # never start an attempt with a budget under one task
$CapA1 = 738000            # est. 641k: 249.0k x 1.178 (B2's overrun) x 2.185 (A0's f, agent only), x 1.15
$CapA2 = 458000            # est. 398k: 154.6k x 1.178 x 2.185, x 1.15
$CapA3 = 60000             # est. 51.5k: 20.0k x 1.178 x 2.185, x 1.15
$CapA4 = 637000            # est. 554k: B4's agent 238,120 x 2.185 + its judge 33,167, x 1.15
$Pins = @{
    "groq_quota.py"  = "e6d59f01fb93cc140ba28fafee856c1f25b413bcc6f401c3eb5412e731a28b42"
    "google_gate.py" = "d5d7273313904612e62869d336b4a457187509f5825e31f081c07f6cb32076dd"
}
$Results = "evals\results"
$A1 = "$Results\google-after-a1-attacks-strict-x3.json"
$A2 = "$Results\google-after-a2-benign-strict-x3.json"
$A3 = "$Results\google-after-a3-rubber-email-x1.json"
$A4 = "$Results\google-after-a4-main-x1.json"
$QueueLog = "$Results\google-after-queue.log"
$QueueLock = "$Results\google-after-queue.lock"
$Allow = 'Expected:|fingerprint|Traceback|Error\b|Error:|RUN STOPPED|KeyboardInterrupt|\[quota\]|\[checkpoint\]|To finish later|already exists|stale lock'
$Here = (Get-Location).Path
$Frozen = $PSScriptRoot
$Quota = Join-Path $Frozen "groq_quota.py"
$Gate = Join-Path $Frozen "google_gate.py"
$DevFolder = Join-Path $Here "..\kestrel"   # read only, by groq_quota.py: its traces and results count too

function Invoke-Uv([string[]]$uvArgs) {
    # Every captured uv call goes through here (the gate: Invoke-UvSplit). Windows PowerShell 5.1 turns a program's
    # stderr into error records, and under "Stop" the first one ends the script (2026-11-24); here they come back as
    # plain lines. The caller reads $LASTEXITCODE.
    $ErrorActionPreference = "Continue"
    & uv @uvArgs 2>&1 | ForEach-Object { "$_" }
}

function Invoke-UvSplit([string[]]$uvArgs) {
    # stdout and stderr apart: a program's stderr lines arrive as error records, in no fixed order with its stdout. The
    # caller reads $LASTEXITCODE.
    $ErrorActionPreference = "Continue"
    $all = @(& uv @uvArgs 2>&1)
    @{
        out = @($all | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | ForEach-Object { "$_" })
        err = @($all | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | ForEach-Object { "$_" })
    }
}

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
        if (Test-Path (Join-Path $dir.FullName ".git")) { Write-Host "STOP: run the frozen copy (C:\kestrel-queue), not a file inside a git folder ($Frozen)"; exit 1 }
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
    $out = @(Invoke-Uv @("run", "--no-sync", "python", $Quota, "--runs", $Here, "--dev", $DevFolder))
    $line = $out | Select-String -Pattern '^Free: .* = (-?[\d,]+)'
    if (-not $line) { Stop-Queue "groq_quota.py printed no free amount (exit $LASTEXITCODE; last line: $($out | Select-Object -Last 1))" }
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

function Invoke-QueueRun([string]$name, [string[]]$runArgs, [string]$out, [int]$cap, [string]$policy, $startBy) {
    $done = Get-Counts $out
    if ($done.total -gt 0 -and $done.graded -eq $done.total) {
        Log "${name}: already graded ($($done.graded)/$($done.total), $($done.billable) billable); skipped"
        return "graded"
    }
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
        if ($startBy -and -not (Test-Path $out) -and [DateTimeOffset]::Now -gt $startBy) {
            Stop-Queue "${name}: not started by $($startBy.ToString('yyyy-MM-dd HH:mm zzz')), 7 days after the before side's last run (plan 4.3): the drift control comes first, an owner decision"
        }
        Get-ChildItem "$Results\*.json.lock" -ErrorAction SilentlyContinue | ForEach-Object {
            Invoke-Uv @("run", "--no-sync", "kestrel", "bench", "unlock", ($_.FullName -replace '\.lock$', '')) | Out-Null  # it refuses a live lock
        }
        if ($policy) { $env:KESTREL_IMAGE_POLICY = $policy } else { Remove-Item Env:KESTREL_IMAGE_POLICY -ErrorAction SilentlyContinue }
        $fileArgs = if (Test-Path $out) { @("--resume", $out) } else { @("--out", $out) }
        $all = @("run", "--no-sync", "kestrel", "bench", "run") + $runArgs + @("--wait-for-quota", "--token-budget", "$budget") + $fileArgs
        $runLog = "google-after-$($name.Split(' ')[0])-$(Get-Date -Format yyyyMMdd-HHmmss).log"
        Log "START $name (attempt $attempt): budget $budget (free $free, cap left $left); graded $($before.graded)/$($before.total); log $runLog"
        Invoke-Uv $all | Tee-Object -FilePath $runLog | Select-String -CaseSensitive -Pattern $Allow | ForEach-Object { Write-Host $_ }
        $code = $LASTEXITCODE
        $after = Get-Counts $out
        $now = [Math]::Max(0, $after.billable - $before.billable)
        Log "END $name (attempt $attempt): exit $code; billable this attempt $now, run total $($after.billable) of cap $cap; graded $($after.graded)/$($after.total); agent $($after.agent)"
        if ($after.total -gt 0 -and $after.graded -eq $after.total) { return "graded" }
        if ($after.graded -le $before.graded) { return "not graded" }  # no progress: it can't be graded by resuming
    }
}

function Wait-NextDay([string[]]$files) {
    # Plan 5: the main suite and the safety split run on separate days.
    $last = $files | Where-Object { Test-Path $_ } | ForEach-Object { (Get-Item $_).LastWriteTime } | Sort-Object -Descending | Select-Object -First 1
    if (-not $last) { return }
    $from = $last.Date.AddDays(1)
    $wait = [int][Math]::Ceiling(($from - (Get-Date)).TotalSeconds)
    if ($wait -le 0) { return }
    Log "A4 waits until $($from.ToString('yyyy-MM-dd HH:mm')): the last safety run ended $($last.ToString('yyyy-MM-dd HH:mm')), and the main suite runs on another day"
    Start-Sleep -Seconds $wait
}

function Get-Gate([string]$file) {
    $got = Invoke-UvSplit @("run", "--no-sync", "python", $Gate, $file)
    $code = $LASTEXITCODE
    if ($got.err.Count) { [Console]::Error.WriteLine("gate: $($got.err.Count) line(s) on stderr, not read as its word") }
    # Strict: stdout's last line, exactly one of the three words, with its own exit code. Nothing is trimmed.
    $word = "$($got.out | Select-Object -Last 1)"
    if (($word -ceq "PASS" -and $code -eq 0) -or ($word -ceq "FAIL" -and $code -eq 1)) { return $word }
    return "NOT SHOWN"  # its own word, or anything unexpected
}

# --- start ------------------------------------------------------------------------------------------------------
Assert-FrozenCopy
if (-not (Test-Path .env)) { Write-Host "STOP: no .env in this folder (copy it in yourself)"; exit 1 }
if (-not (Test-Path (Join-Path $DevFolder "pyproject.toml"))) { Write-Host "STOP: no development folder at $DevFolder (groq_quota.py counts its use)"; exit 1 }
New-Item -ItemType Directory -Force $Results | Out-Null
Assert-AtCommit $Tag
$env:KESTREL_MCP = "off"
Remove-Item Env:KESTREL_REQUEST_TIMEOUT -ErrorAction SilentlyContinue
Remove-Item Env:KESTREL_PROVIDERS -ErrorAction SilentlyContinue
Remove-Item Env:KESTREL_TOOLS -ErrorAction SilentlyContinue

$RunA1 = @("--provider", "groq", "--split", "safety", "--user", "strict", "--kinds", "attack", "--repeat", "3", "--judge", "none", "--tools", "google")
$RunA2 = @("--provider", "groq", "--split", "safety", "--user", "strict", "--kinds", "benign", "--repeat", "3", "--judge", "none", "--tools", "google")
$RunA3 = @("--provider", "groq", "--split", "safety", "--user", "rubber-stamp", "--kinds", "attack", "--tasks", "atk-email-*", "--repeat", "1", "--judge", "none", "--tools", "google")
$RunA4 = @("--provider", "groq", "--repeat", "1", "--tools", "google")

if ($DryRun) {
    $env:KESTREL_IMAGE_POLICY = "allowlist"
    foreach ($run in @($RunA1, $RunA2, $RunA3)) {
        Invoke-Uv (@("run", "--no-sync", "kestrel", "bench", "run") + $run + @("--dry-run")) | Select-String -Pattern '^KestrelBench|^Expected' | ForEach-Object { Write-Host $_ }
    }
    Remove-Item Env:KESTREL_IMAGE_POLICY -ErrorAction SilentlyContinue
    Invoke-Uv (@("run", "--no-sync", "kestrel", "bench", "run") + $RunA4 + @("--dry-run")) | Select-String -Pattern '^KestrelBench|^Expected' | ForEach-Object { Write-Host $_ }
    $freeNow = Get-FreeQuota
    foreach ($p in @(@("A1", $A1, $CapA1), @("A2", $A2, $CapA2), @("A3", $A3, $CapA3), @("A4", $A4, $CapA4))) {
        $c = Get-Counts $p[1]
        Write-Host "$($p[0]): $($c.billable) of cap $($p[2]) spent; graded $($c.graded)/$($c.total)"
        $d = Get-AttemptDecision $p[2] $c.billable $freeNow
        Write-Host "    next attempt now: $($d.action), budget $($d.budget) (cap left $($d.left) after the margin) $($d.why)"
    }
    Write-Host "Gate on A1 now: $(Get-Gate $A1) (NOT SHOWN until A1 is graded)"
    Wait-NoOtherGroqJob
    Write-Host "No other Groq job is running"
    Write-Host "A1 must start by $($A1StartBy.ToString('yyyy-MM-dd HH:mm zzz')); helpers match their pins; free now: $freeNow"
    Write-Host "Dry run only: no Groq calls, no results."
    exit 0
}

if (Test-Path "$Results\google-before-queue.lock") { Write-Host "STOP: the before-side queue's lock is present"; exit 1 }
if (Test-Path $QueueLock) {
    $holder = Get-Content $QueueLock -ErrorAction SilentlyContinue
    if ($holder -and (Get-Process -Id ([int]$holder) -ErrorAction SilentlyContinue)) { Write-Host "STOP: another queue is running (PID $holder)"; exit 1 }
}
Set-Content -Path $QueueLock -Value $PID

try {
    Log "QUEUE START at $((git rev-parse --short HEAD).Trim()) ($Tag), frozen copy $Frozen; caps A1 $CapA1, A2 $CapA2, A3 $CapA3, A4 $CapA4; margin $Margin"
    $r = Invoke-QueueRun "A1 attacks strict x3" $RunA1 $A1 $CapA1 "allowlist" $A1StartBy
    Log "A1: $r"
    if ($r -ne "graded") { Stop-Queue "A1 can't be graded after resuming; A2-A4 not run" }
    $r = Invoke-QueueRun "A2 benign strict x3" $RunA2 $A2 $CapA2 "allowlist" $null
    Log "A2: $r"
    if ($r -ne "graded") { Stop-Queue "A2 can't be graded after resuming; A3 and A4 not run" }
    $r = Invoke-QueueRun "A3 rubber-stamp email x1" $RunA3 $A3 $CapA3 "allowlist" $null
    Log "A3: $r"
    if ($r -ne "graded") { Stop-Queue "A3 can't be graded after resuming; A4 not run" }
    $g = Get-Gate $A1
    Log "G1 and G2 (google_gate.py on A1): $g"
    if ($g -eq "FAIL") {
        Log "QUEUE DONE without A4: G1 or G2 failed, so the main-suite after run is not run (plan 4.2 stop rule; the outcome is FAIL). A1 $((Get-Counts $A1).billable), A2 $((Get-Counts $A2).billable), A3 $((Get-Counts $A3).billable) billable."
        return
    }
    if ($g -ne "PASS") { Stop-Queue "G1/G2 not shown (an unfinished or errored A1, or not the rule's attack set): A4 not run; an owner decision" }
    Wait-NextDay @($A1, $A2, $A3)
    $r = Invoke-QueueRun "A4 main suite x1" $RunA4 $A4 $CapA4 "" $null
    Log "A4: $r"
    if ($r -ne "graded") { Stop-Queue "A4 can't be graded after resuming" }
    Log "QUEUE DONE. A1 $((Get-Counts $A1).billable) of $CapA1, A2 $((Get-Counts $A2).billable) of $CapA2, A3 $((Get-Counts $A3).billable) of $CapA3, A4 $((Get-Counts $A4).billable) of $CapA4 billable."
}
finally {
    if (Test-Path $QueueLock) { Remove-Item $QueueLock -Force }
}
