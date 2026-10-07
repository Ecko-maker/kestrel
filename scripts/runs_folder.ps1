<#
.SYNOPSIS
    Create (or move) the measurement folder ..\kestrel-runs at a tag or commit, with its own .venv.

.DESCRIPTION
    Measurements (kestrel bench run on a real provider) happen in ..\kestrel-runs, a git worktree checked
    out at a fixed tag or commit (detached), so a long run never sees edits made in the working folder and
    the results say exactly which code produced them. Development stays in this folder.

    The script never creates, copies or reads .env: copy your .env into ..\kestrel-runs yourself.

.EXAMPLE
    .\scripts\runs_folder.ps1 v0.2.0
    .\scripts\runs_folder.ps1 7b1bdd4
    .\scripts\runs_folder.ps1            # the current commit (HEAD)
#>
param(
    [string]$Ref = "HEAD",
    [string]$Path = (Join-Path (Split-Path -Parent (git rev-parse --show-toplevel)) "kestrel-runs")
)
$ErrorActionPreference = "Stop"

$commit = git rev-parse --verify --quiet "$Ref^{commit}"
if (-not $commit) { throw "Unknown tag or commit: $Ref" }
$short = $commit.Substring(0, 7)

if (Test-Path $Path) {
    $isWorktree = (git worktree list --porcelain) -match [regex]::Escape("worktree " + ((Resolve-Path $Path).Path -replace '\\', '/'))
    if (-not $isWorktree) { throw "$Path exists and is not a worktree of this repository: move it away first." }
    if (git -C $Path status --porcelain --untracked-files=no) {
        throw "$Path has uncommitted changes to tracked files: a runs folder must match its commit exactly."
    }
    git -C $Path checkout --quiet --detach $commit
    Write-Host "Moved $Path to $short."
} else {
    git worktree add --detach $Path $commit
    Write-Host "Created $Path at $short."
}

# Its own virtual environment, exactly as uv.lock pins it (never the working folder's .venv).
Push-Location $Path
try {
    $env:VIRTUAL_ENV = $null
    uv sync --locked
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed in $Path" }
    # git-ignored, so a fresh worktree lacks it; a log redirected there before the first run would fail
    New-Item -ItemType Directory -Force (Join-Path $Path "evals\results") | Out-Null
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Runs folder ready: $Path at $short ($Ref)."
if (Test-Path (Join-Path $Path ".env")) {
    Write-Host "A .env is already there (not opened)."
} else {
    Write-Host "Copy your .env into ..\kestrel-runs yourself."
}
Write-Host "Then, from that folder: uv run kestrel bench run ... (results land in its evals/results/)."
