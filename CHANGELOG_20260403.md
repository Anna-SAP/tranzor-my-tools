Updated **TranzorExporter.app** (v20260403)

What's new:

- **Universal Binary for macOS** — the Mac app now ships as a Universal Binary (`universal2`), supporting both Intel (`x86_64`) and Apple Silicon (`arm64`) Macs. Previously, the app was built only for the runner's native architecture (ARM64), causing `bad CPU type in executable` errors on Intel Macs.

Bug fix details:

- **Root cause:** `target_arch` was set to `None` in the PyInstaller spec, which builds only for the host machine's architecture. Since GitHub Actions' `macos-latest` runner is Apple Silicon, the resulting binary was ARM64-only.
- **Fix:** Changed `target_arch` to `'universal2'`, producing a fat binary that runs natively on both Intel and Apple Silicon Macs — no Rosetta required on either platform.

> **Note:** After downloading the new `.app`, if macOS blocks it with "app is damaged", run:
> `xattr -cr /path/to/TranzorExporter.app` in Terminal, then reopen.
