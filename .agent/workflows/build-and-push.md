---
description: Build EXE and push changes to GitHub after code modifications
---

# Post-Change Build & Deploy Workflow

After every code change to the TranzorExporter project, perform these steps:

// turbo-all

## 1. Build Windows EXE

```
Set-Location C:\Users\susu82\Tranzor-Platform\my-tools; pyinstaller TranzorExporter.spec --noconfirm 2>&1 | Select-Object -Last 10
```

Verify the output shows "Building EXE ... completed successfully."

## 2. Check Git Status

```
Set-Location C:\Users\susu82\Tranzor-Platform\my-tools; git status --short
```

## 3. Commit Changes

Stage all modified files and commit with a descriptive message following conventional commits format (e.g., `feat:`, `fix:`, `refactor:`).

```
Set-Location C:\Users\susu82\Tranzor-Platform\my-tools; git add -A; git commit -m "<type>: <description>"
```

## 4. Push to GitHub

```
Set-Location C:\Users\susu82\Tranzor-Platform\my-tools; git push origin master
```

Target repository: https://github.com/Anna-SAP/tranzor-my-tools

This enables the user to follow up with the macOS build on their Mac machine.
