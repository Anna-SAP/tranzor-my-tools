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

& $python.Source -m PyInstaller $specPath --clean

if (-not (Test-Path $artifactPath)) {
    throw "Build finished but EXE was not generated: $artifactPath"
}

$artifact = Get-Item $artifactPath
Write-Host ""
Write-Host "Build completed."
Write-Host "EXE:" $artifact.FullName
Write-Host "Size:" $artifact.Length "bytes"
Write-Host "Updated:" $artifact.LastWriteTime
