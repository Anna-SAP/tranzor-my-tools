#requires -Version 7
<#
.SYNOPSIS
  Keep the Live Demo's data continuously fresh while you're at work.

.DESCRIPTION
  Runs a loop that every few minutes captures the latest REAL Tranzor data and
  pushes it; your local gitlab-runner then redeploys GitLab Pages. Colleagues
  opening the demo — or clicking Refresh — see data that's at most one interval
  old. No tokens are exposed anywhere.

  The deploying half (the runner) is handled by the gitlab-runner WINDOWS
  SERVICE, installed once — it auto-starts on boot and picks up every "pages"
  job, so you only need to run THIS loop:
        cd C:\Users\susu82\Tranzor-Platform\my-tools; .\auto_refresh_demo.ps1

  (No service? Start a runner in a separate window instead:
        cd "$HOME\GitLab-Runner"; .\gitlab-runner.exe run )

  Press Ctrl+C to stop this loop. When you log off, the demo freezes at the last
  snapshot (still real data, just not updating) until you start the loop again.
  The service keeps running regardless; it only deploys when something is pushed.

.PARAMETER IntervalMinutes
  Minutes between refreshes (default 10). Each refresh is a light "--fast"
  capture (rows + global stats; reuses Recently Added) to stay easy on the API.

.EXAMPLE
  .\auto_refresh_demo.ps1 -IntervalMinutes 10
#>
param(
  [int]$IntervalMinutes = 10
)

$repo = if ($PSScriptRoot) { $PSScriptRoot } else { 'C:\Users\susu82\Tranzor-Platform\my-tools' }
Set-Location $repo

Write-Host "Auto-refresh loop started (every $IntervalMinutes min)." -ForegroundColor Cyan
$svc = Get-Service -Name 'gitlab-runner' -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq 'Running') {
  Write-Host "gitlab-runner service is Running — pushes deploy automatically." -ForegroundColor Green
} else {
  Write-Host "gitlab-runner service NOT running — start a runner in another window, or nothing will deploy." -ForegroundColor Yellow
}
Write-Host "Press Ctrl+C to stop.`n"

while ($true) {
  $ts = Get-Date -Format 'HH:mm:ss'
  Write-Host "[$ts] refreshing..." -ForegroundColor Cyan
  try {
    # Stay in sync in case anything else pushed (best-effort).
    git pull --ff-only 2>$null | Out-Null

    python build_demo_data.py --limit 20 --fast
    if ($LASTEXITCODE -ne 0) {
      Write-Host "[$ts] capture failed (on VPN? Tranzor login valid?) - will retry next cycle." -ForegroundColor Red
    }
    elseif (git status --porcelain public/demo_data.json) {
      git add public/demo_data.json
      git commit -m "chore(demo): auto-refresh snapshot" --quiet
      git push origin master --quiet
      git push gitlab master:master --quiet
      Write-Host "[$ts] pushed - runner will deploy shortly." -ForegroundColor Green
    }
    else {
      Write-Host "[$ts] no data change - nothing to push." -ForegroundColor DarkGray
    }
  }
  catch {
    Write-Host "[$ts] error: $($_.Exception.Message) - continuing." -ForegroundColor Red
  }

  Start-Sleep -Seconds ($IntervalMinutes * 60)
}
