#requires -Version 7
<#
.SYNOPSIS
  Keep the Live Demo's data continuously fresh while you're at work.

.DESCRIPTION
  Runs a loop that every few minutes captures the latest REAL Tranzor data and
  pushes it; your local gitlab-runner (running in a SEPARATE window) then
  redeploys GitLab Pages. Colleagues opening the demo — or clicking Refresh —
  see data that's at most one interval old. No tokens are exposed anywhere.

  TWO windows are needed:
    Window 1 (the runner — deploys):
        cd "$HOME\GitLab-Runner"; .\gitlab-runner.exe run
    Window 2 (this loop — refreshes):
        cd C:\Users\susu82\Tranzor-Platform\my-tools; .\auto_refresh_demo.ps1

  Press Ctrl+C in either window to stop. When you log off, stop both — the demo
  then freezes at the last snapshot (still real data, just not updating) until
  you start them again.

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
Write-Host "Make sure the runner is running in another window, or nothing will deploy." -ForegroundColor Yellow
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
