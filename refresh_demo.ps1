#requires -Version 7
<#
.SYNOPSIS
  Refresh the Live Demo's real-data snapshot and deploy it to GitLab Pages.

.DESCRIPTION
  Run this before you log off so off-hours colleagues (other timezones) see
  fresh REAL Tranzor data. One command — no multi-line pasting, so the steps
  can't run out of order.

  Steps:
    1. Capture real Tranzor data  -> public/demo_data.json
       (needs: on the corp network + a valid Tranzor login cached by the
        desktop app in ~/.tranzor_exporter_auth.json).
    2. Commit & push to GitHub (origin) and GitLab (gitlab) master — only if
       the snapshot actually changed.
    3. Start your local gitlab-runner so GitLab Pages rebuilds. Watch the
       "pages" job go GREEN, then press Ctrl+C to stop the runner.

  NOTE: this is a data-only refresh, pushed straight to master on purpose
  (no PR) so it's a single quick step. Code changes still go through PRs.

.EXAMPLE
  cd C:\Users\susu82\Tranzor-Platform\my-tools
  .\refresh_demo.ps1
#>
$ErrorActionPreference = 'Stop'

# The repo is wherever this script lives.
$repo = if ($PSScriptRoot) { $PSScriptRoot } else { 'C:\Users\susu82\Tranzor-Platform\my-tools' }
Set-Location $repo

Write-Host '== 1/3  Capturing real Tranzor data ==' -ForegroundColor Cyan
python build_demo_data.py --limit 20
if ($LASTEXITCODE -ne 0) {
  Write-Host 'Capture failed. Check: on the corp network/VPN? Is your Tranzor login still valid (open the desktop app once to refresh it)?' -ForegroundColor Red
  exit 1
}

Write-Host '== 2/3  Publishing snapshot ==' -ForegroundColor Cyan
if (git status --porcelain public/demo_data.json) {
  git add public/demo_data.json
  git commit -m 'chore(demo): refresh real-data snapshot'
  git push origin master
  git push gitlab master:master
  Write-Host 'Pushed. A new GitLab "pages" pipeline is queued.' -ForegroundColor Green
} else {
  Write-Host 'No data change since last refresh — nothing to publish (demo already current).' -ForegroundColor Yellow
  Write-Host 'Done.' -ForegroundColor Green
  exit 0
}

Write-Host ''
Write-Host '== 3/3  Deploying via your local runner ==' -ForegroundColor Cyan
Write-Host '  Watch the "pages" job here:'
Write-Host '  https://git.ringcentral.com/rc-ai-learning/annasu-tranzor-helper/-/pipelines'
Write-Host '  When it turns GREEN (passed), press Ctrl+C to stop the runner — deploy is done.'
Write-Host ''
$runner = Join-Path $HOME 'GitLab-Runner\gitlab-runner.exe'
if (Test-Path $runner) {
  Set-Location (Split-Path $runner)
  & $runner run
} else {
  Write-Host "gitlab-runner.exe not found at $runner — start your runner manually to deploy." -ForegroundColor Yellow
}
