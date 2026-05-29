$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

$python = Get-Command python -ErrorAction Stop
$specPath = Join-Path $PSScriptRoot "TranzorExporter.spec"
$artifactPath = Join-Path $PSScriptRoot "dist\\TranzorExporter.exe"

if (-not (Test-Path $specPath)) {
    throw "Spec file not found: $specPath"
}

Write-Host "Using Python:" $python.Source
Write-Host "Building from spec:" $specPath

# ---------------------------------------------------------------------------
# PR-J: optionally embed a read-only GitLab service-account credential so the
# distributed EXE works for non-technical language reviewers with zero setup.
#
# Set these on the BUILD MACHINE only (never commit them):
#   $env:TRANZOR_GITLAB_TOKEN_EMBED    = "<service-account read_api token>"
#   $env:TRANZOR_GITLAB_BASE_URL_EMBED = "https://your.gitlab"   # optional
#
# The token is read by Python from the env var (NOT passed on the command
# line, so it never shows up in the process list). The generated
# gitlab_token_embed.py is .gitignore'd and deleted right after packaging.
# ---------------------------------------------------------------------------
$embedFile = Join-Path $PSScriptRoot "gitlab_token_embed.py"
try {
    if ($env:TRANZOR_GITLAB_TOKEN_EMBED) {
        Write-Host "Embedding GitLab service-account credential into the build..."
        & $python.Source -c @"
import os, gitlab_client
gitlab_client.write_embedded_credentials(
    os.environ.get('TRANZOR_GITLAB_TOKEN_EMBED', ''),
    os.environ.get('TRANZOR_GITLAB_BASE_URL_EMBED', ''),
)
"@
        if ($LASTEXITCODE -ne 0) { throw "Failed to generate gitlab_token_embed.py" }
    } else {
        Write-Host "No TRANZOR_GITLAB_TOKEN_EMBED set - building WITHOUT an embedded GitLab credential."
        Write-Host "(Reviewers will need to set a token via the GitLab dialog.)"
    }

    & $python.Source -m PyInstaller $specPath --clean
}
finally {
    # Always remove the embedded credential from the working tree, even if
    # the build fails - it must never linger on disk.
    if (Test-Path $embedFile) {
        Remove-Item $embedFile -Force
        Write-Host "Cleaned up gitlab_token_embed.py."
    }
}

if (-not (Test-Path $artifactPath)) {
    throw "Build finished but EXE was not generated: $artifactPath"
}

$artifact = Get-Item $artifactPath
Write-Host ""
Write-Host "Build completed."
Write-Host "EXE:" $artifact.FullName
Write-Host "Size:" $artifact.Length "bytes"
Write-Host "Updated:" $artifact.LastWriteTime
