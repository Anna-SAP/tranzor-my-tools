#requires -Version 7
<#
.SYNOPSIS
    Publish the latest GitHub-built Tranzor Helper binaries (Windows .exe +
    macOS .zip) to the internal GitLab project's Releases, so colleagues can
    download the real app without GitHub access.

.DESCRIPTION
    GitHub's cloud runners build the binaries but cannot reach the internal
    git.ringcentral.com. Your laptop can reach BOTH github.com and the internal
    GitLab, so it is the bridge. This script:
      1. uses `gh` to find + download the latest *successful* Windows and macOS
         build artifacts from the GitHub repo,
      2. uploads each to the GitLab generic package registry,
      3. creates (or reuses) a per-commit GitLab Release and attaches both files.

    No CI runner is involved — just run it from inside the RC network.

.PREREQUISITES
    - GitHub CLI `gh` installed and logged in.   Check:  gh auth status
    - A GitLab access token with the `api` scope, exported as $env:GITLAB_TOKEN.

.EXAMPLE
    $env:GITLAB_TOKEN = "glpat-xxxxxxxxxxxx"
    pwsh ./publish-to-gitlab.ps1
#>
[CmdletBinding()]
param(
    [string]$Repo        = "Anna-SAP/tranzor-my-tools",
    [int]   $ProjectId   = 40545,
    [string]$GitLabHost  = "https://git.ringcentral.com",
    [string]$ProjectPath = "rc-ai-learning/annasu-tranzor-helper",
    [string]$GitLabToken = $env:GITLAB_TOKEN
)

$ErrorActionPreference = "Stop"

function Info($m) { Write-Host $m -ForegroundColor Cyan }
function Ok($m)   { Write-Host $m -ForegroundColor Green }
function Warn($m) { Write-Host $m -ForegroundColor Yellow }
function Die($m)  { Write-Host "ERROR: $m" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- preflight ---
if (-not $GitLabToken) {
    Die "No GitLab token. Run first:  `$env:GITLAB_TOKEN = 'glpat-...'   (needs the 'api' scope)"
}
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Die "GitHub CLI 'gh' not found. Install it and run 'gh auth login'."
}
& gh auth status 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { Die "GitHub CLI is not logged in. Run: gh auth login" }

$apiBase = "$GitLabHost/api/v4/projects/$ProjectId"
$headers = @{ "PRIVATE-TOKEN" = $GitLabToken }

# ---------------------------------------- 1. pull the latest GitHub builds ---
$work = Join-Path ([System.IO.Path]::GetTempPath()) "tranzor-publish"
if (Test-Path $work) { Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory -Force -Path $work | Out-Null

function Get-LatestArtifact {
    param([string]$Workflow, [string]$ArtifactName)
    Info "Finding latest successful '$Workflow' build on GitHub..."
    $runs = & gh run list --repo $Repo --workflow $Workflow --status success --limit 1 `
                 --json databaseId,headSha,createdAt | ConvertFrom-Json
    if (-not $runs) { Die "No successful '$Workflow' run found on GitHub." }
    $run  = $runs[0]
    $sha8 = $run.headSha.Substring(0, 8)
    Info "  run #$($run.databaseId)  commit $sha8  ($($run.createdAt))"
    $dest = Join-Path $work $ArtifactName
    & gh run download $run.databaseId --repo $Repo --name $ArtifactName --dir $dest
    if ($LASTEXITCODE -ne 0) { Die "Could not download artifact '$ArtifactName' (run #$($run.databaseId))." }
    $file = Get-ChildItem $dest -Recurse -File | Select-Object -First 1
    if (-not $file) { Die "Artifact '$ArtifactName' was empty." }
    return [pscustomobject]@{ Path = $file.FullName; Sha = $run.headSha; Sha8 = $sha8 }
}

$win = Get-LatestArtifact -Workflow "Build Windows EXE" -ArtifactName "TranzorExporter-Windows"
$mac = Get-LatestArtifact -Workflow "Build Mac App"     -ArtifactName "TranzorExporter-Mac"

# The release is identified by the Windows build's commit.
$sha8    = $win.Sha8
$tag     = "build-$sha8"
$relName = "Build $(Get-Date -Format 'yyyy-MM-dd') ($sha8)"
if ($mac.Sha8 -ne $win.Sha8) {
    Warn "NOTE: latest Windows ($($win.Sha8)) and macOS ($($mac.Sha8)) builds are from different commits."
    Warn "      Releasing under the Windows commit; the macOS asset is the newest available."
}

# ------------------------------ 2. upload binaries to the package registry ---
function Publish-Package {
    param([string]$FilePath, [string]$PkgFileName)
    $url = "$apiBase/packages/generic/tranzor-helper/$sha8/$PkgFileName"
    $sizeMB = [math]::Round((Get-Item $FilePath).Length / 1MB, 1)
    Info "Uploading $(Split-Path $FilePath -Leaf) ($sizeMB MB) -> GitLab package registry..."
    try {
        Invoke-RestMethod -Method Put -Uri $url -Headers $headers `
            -InFile $FilePath -ContentType "application/octet-stream" | Out-Null
    } catch {
        $code = $null
        try { $code = [int]$_.Exception.Response.StatusCode } catch {}
        if ($code -eq 400 -or $code -eq 409) {
            Warn "  this version already exists in the registry — reusing it."
        } else {
            throw
        }
    }
    return $url
}

$winUrl = Publish-Package -FilePath $win.Path -PkgFileName "TranzorExporter-windows.exe"
$macUrl = Publish-Package -FilePath $mac.Path -PkgFileName "TranzorExporter-macos.zip"

# --------------------------------- 3. create release + attach asset links ----
Info "Creating/locating GitLab release '$tag'..."
try {
    Invoke-RestMethod -Method Post -Uri "$apiBase/releases" -Headers $headers -Body @{
        name        = $relName
        tag_name    = $tag
        ref         = "master"
        description = "Automated build published from $($win.Sha)."
    } | Out-Null
    Ok "  created release $tag"
} catch {
    Warn "  release $tag already exists — reusing it."
}

function Add-Link {
    param([string]$Name, [string]$Url)
    try {
        Invoke-RestMethod -Method Post -Uri "$apiBase/releases/$tag/assets/links" `
            -Headers $headers -Body @{ name = $Name; url = $Url } | Out-Null
        Ok "  linked: $Name"
    } catch {
        Warn "  link '$Name' may already exist — skipping."
    }
}
Add-Link -Name "Windows EXE (.exe)" -Url $winUrl
Add-Link -Name "macOS app (.zip)"   -Url $macUrl

Write-Host ""
Ok "Done. Published '$relName'."
Info "Colleagues download from:  $GitLabHost/$ProjectPath/-/releases"
