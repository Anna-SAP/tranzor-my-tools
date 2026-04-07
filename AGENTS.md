# Agent Notes

Before making changes in this repository, read:

- `C:\Users\susu82\Tranzor-Platform\my-tools\.agent\context.md`

Key rules:

- `my-tools` is a standalone nested Git repository inside `Tranzor-Platform`.
- Default work scope is `my-tools` only.
- Treat the parent `Tranzor-Platform` repository as read-only unless the user explicitly says otherwise.
- Windows release builds must use `build_windows.ps1` or `TranzorExporter.spec`.
- Formal Mac app releases must use `.github/workflows/build-mac.yml`.
