## Workspace Context

This file records stable background context for work in this workspace.

### Repository Relationship

- `C:\Users\susu82\Tranzor-Platform` is the parent product repository.
- The parent repository should be treated as read-only in normal work.
- `C:\Users\susu82\Tranzor-Platform\my-tools` is a separate nested Git repository.
- `my-tools` is the default writable codebase for Tranzor Exporter changes.

### Default Editing Scope

- Unless the user explicitly says otherwise, make code changes only inside `my-tools`.
- Do not edit, stage, or commit files in the parent `Tranzor-Platform` repository.
- If a request appears to involve both repositories, confirm scope before touching the parent repository.

### Version Control

- `my-tools` has its own Git history and remote.
- Default remote for `my-tools`: `https://github.com/Anna-SAP/tranzor-my-tools.git`
- The user manages `my-tools` locally with SourceTree, but standard non-interactive Git CLI commands are fine.

### Build And Release Rules

- Windows release builds must use `build_windows.ps1` or `TranzorExporter.spec`.
- Do not replace the formal Windows build with an ad-hoc PyInstaller command.
- Formal Mac app builds must use GitHub Actions workflow `.github/workflows/build-mac.yml`.
- Do not treat a local ad-hoc Mac packaging command as the official release path.

### Practical Working Rule

- If the user starts a session from the parent workspace root, still assume `my-tools` is the intended target unless they explicitly redirect scope.
