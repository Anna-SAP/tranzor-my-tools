# TranzorExporter — Quick Start Guide

> **Platform:** Windows (64-bit)  
> **No installation required** — just download and double-click.

---

## What Is It?

**TranzorExporter** is a standalone desktop tool that exports translation data from the Tranzor Platform into ready-to-use formats:

| Feature | Description |
|---------|-------------|
| **Export Changes** | Export only manually edited translations (with before/after diff) |
| **Export All Translations** | Export the complete set of translations for a task |
| **HTML Report** | Interactive report with filtering, search, and in-browser **TMX export** |
| **Excel Report** | Standard `.xlsx` spreadsheet output |
| **TMX Export** | Select entries in the HTML report → one-click download as TMX (XTM Cloud compatible) |

---

## How to Use

1. **Download** `TranzorExporter.exe` and save it anywhere on your computer.

2. **Double-click** the exe to launch. No Python or other software needed.

3. **Configure your export:**

   | Setting | Options |
   |---------|---------|
   | **Task ID** | Enter a specific task ID, or leave empty to export all completed tasks |
   | **Export Type** | `Changes` (manual edits only) or `All Translations` (full translation set) |
   | **Output Format** | `HTML` (recommended — includes filters + TMX export) or `Excel` |

4. **Click** `▶ Start Export` and wait for it to finish.

5. **Click** `📂 Open Report` to view the result in your browser (HTML) or Excel.

6. **To export TMX** (from HTML report):
   - Select rows using checkboxes (or click `Select All`)
   - Click `📦 Export TMX` in the toolbar
   - A `.tmx` file (single language) or `.zip` (multiple languages) will download automatically

7. **To send selected rows back to Tranzor Platform** (HTML report → browser tab):
   - Install the **Tranzor Bridge** userscript once (see below)
   - Filter + tick the rows you need to fix
   - Click `↗ Send to Tranzor` in the toolbar — your Tranzor tab will open with a fix-list sidebar; the sidebar's `🔍 Find` button fills the platform's search box for each key in turn

---

## Tranzor Bridge userscript (one-time install)

The Send-to-Tranzor feature uses a small browser userscript to receive the selection on the Tranzor Platform side.

1. Install **Tampermonkey** (Chrome / Edge / Firefox) or **Violentmonkey**.
2. Open `userscript/tranzor_bridge.user.js` (shipped alongside the exe; also in the GitHub repo) → the userscript manager will prompt to install.
3. Confirm the `@match http://tranzor-platform.int.rclabenv.com/*` and `@connect 127.0.0.1` permissions.

While `TranzorExporter` is running, a loopback HTTP service on `127.0.0.1` (port 48217 or next free) carries selections from the report to the userscript. If the bridge is unreachable, the report falls back to copying the selection to your clipboard — press `Ctrl+Shift+V` inside the userscript's sidebar to paste it.

---

## Language Toggle

The interface defaults to **English**. Click the **中文** button (top-right corner) to switch to Chinese, and back again anytime.

---

## Requirements

- **OS:** Windows 10 / 11 (64-bit)
- **Network:** Must be connected to the corporate network (or VPN) to reach the Tranzor API server
- **No Python installation needed**
