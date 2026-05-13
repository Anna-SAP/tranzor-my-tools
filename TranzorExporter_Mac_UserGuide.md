# TranzorExporter for Mac — Now Available! 🎉

Hi team,

Great news — **TranzorExporter is now available for macOS**! It has full feature parity with the Windows version, so you can enjoy the same powerful export capabilities on your Mac.

---

## ⬇️ Download

Download **TranzorExporter.app** from our GitHub repository:

👉 https://github.com/Anna-SAP/tranzor-my-tools/actions

1. Click the latest **Build Mac App** workflow run (with ✅ green checkmark)
2. Scroll to the **Artifacts** section at the bottom
3. Download **TranzorExporter-Mac** (13.4 MB)
4. Unzip the downloaded file → you'll get `TranzorExporter.app`

---

## 🚀 Getting Started

### Step 1: Open the App

Double-click `TranzorExporter.app` to launch.

> **First launch note (macOS Sequoia / 15 and later):** Because the app isn't notarized with an Apple Developer ID, macOS will block the first launch with a dialog saying *"Apple could not verify TranzorExporter is free of malware"* — and on Sequoia this dialog only offers **Done / Move to Trash** (no inline "Open Anyway" button).
>
> **Recommended (one Terminal command):** open Terminal and run:
> ```bash
> xattr -dr com.apple.quarantine ~/Downloads/TranzorExporter.app
> ```
> (Adjust the path if you moved the app, e.g. `/Applications/TranzorExporter.app`.) Then double-click the app — it will launch normally. You only need to do this once per download.
>
> **Fallback (GUI):** if you prefer not to use Terminal, double-click the app once and click **Done** to dismiss the warning. Then open **System Settings → Privacy & Security**, scroll to the bottom, and within about a minute you'll see a row *"'TranzorExporter' was blocked…"* with an **Open Anyway** button. Click it, authenticate, and confirm in the follow-up dialog.

### Step 2: Connect to VPN

Make sure your VPN is connected so the app can reach the Tranzor platform.

### Step 3: Start Exporting!

The interface is identical to the Windows version:

| Feature | Description |
|---------|-------------|
| 📝 **Export Changes** | Export translation edit logs with word-level diffs |
| 📋 **Export All Translations** | Export full translation sets for any task |
| 🔄 **MR Pipeline** | View and export MR translation results |
| 📊 **Quality Overview** | View quality scores, charts, and detailed reports |

#### Quick Export Guide

1. Enter a **Task ID** (or leave blank to export all tasks)
2. Select export type: **Changes** or **All Translations**
3. Choose format: **HTML** (opens in browser) or **Excel**
4. Click the **Export** button
5. The report will be generated and opened automatically

---

## 📁 Output Formats

- **HTML** — Interactive report that opens in your browser with filtering, search, and TMX export
- **Excel** — Formatted `.xlsx` file with color-coded cells, ready for sharing

---

## ❓ Troubleshooting

| Issue | Solution |
|-------|----------|
| App blocked by macOS ("Apple could not verify…") | Run `xattr -dr com.apple.quarantine ~/Downloads/TranzorExporter.app` in Terminal, then double-click. Or use System Settings → Privacy & Security → "Open Anyway" after a blocked attempt. |
| App "is damaged and can't be opened" | Same fix: `xattr -dr com.apple.quarantine <path-to-TranzorExporter.app>`. This is caused by the quarantine flag, not actual file corruption. |
| Connection timeout | Check that your VPN is active |
| App doesn't open | Make sure you unzipped the download first |

---

## 📌 Version Info

- Same codebase as Windows `TranzorExporter.exe`
- All features are fully supported on macOS
- Reports generated on Mac are identical to those from Windows

If you have any questions or run into issues, feel free to reach out!
