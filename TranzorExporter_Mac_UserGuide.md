# TranzorExporter for Mac — Now Available! 🎉

Hi team,

Great news — **TranzorExporter is now available for macOS**! It has full feature parity with the Windows version, so you can enjoy the same powerful export capabilities on your Mac.

---

## ⬇️ Download

Download the Mac build from our GitHub repository:

👉 https://github.com/Anna-SAP/tranzor-my-tools/actions

1. Click the latest **Build Mac App** workflow run (with ✅ green checkmark)
2. Scroll to the **Artifacts** section at the bottom
3. Download **TranzorExporter-Mac** — you get `TranzorExporter-Mac.zip`
4. Unzip **twice** — GitHub wraps the artifact in an outer zip, and we ship the .app inside an inner ditto zip so its code signature survives the trip intact:
   - Double-click the downloaded `TranzorExporter-Mac.zip` → you get another `TranzorExporter-Mac.zip` (macOS may add ` 2` to disambiguate)
   - Double-click that one → you get a `TranzorExporter-Mac/` folder

Inside the `TranzorExporter-Mac/` folder you'll see two items side by side:

- **TranzorExporter.app** — the main program
- **首次打开必读.txt** — first-launch guide (two Gatekeeper-bypass methods)

> Why two unzip steps? Without the inner ditto zip the .app's code signature gets damaged in transit through GitHub Actions and macOS refuses to launch it even after the Gatekeeper bypass. The inner zip is what keeps the .app working.

---

## 🚀 Getting Started

### Step 1: Install the app

Drag `TranzorExporter.app` from the unzipped folder into your `/Applications` folder.

### Step 2: Get past the first-launch Gatekeeper prompt

> Because the app isn't notarized with an Apple Developer ID, macOS will block the first launch with a dialog saying *"Apple could not verify TranzorExporter is free of malware"* — on Sequoia (macOS 15+) this dialog only offers **Done / Move to Trash** with no inline "Open Anyway" button. You only need to do this **once per download**.

Pick whichever of these two feels easiest — they do the same thing:

**A. One Terminal command (recommended, fastest)**

```bash
xattr -dr com.apple.quarantine /Applications/TranzorExporter.app
```

Then double-click `TranzorExporter.app` — it launches normally.

**B. Pure GUI (no Terminal at all)**

1. Double-click `TranzorExporter.app` — let it get blocked.
2. Open **System Settings → Privacy & Security**, scroll to the bottom.
3. Click **Open Anyway** next to the blocked entry, authenticate, and confirm the follow-up dialog.

After either of these, double-clicking the app from now on just works.

### Step 3: Connect to VPN

Make sure your VPN is connected so the app can reach the Tranzor platform.

### Step 4: Start Exporting!

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
| App blocked by macOS ("Apple could not verify…") | Pick either method above. Recommended: `xattr -dr com.apple.quarantine /Applications/TranzorExporter.app` in Terminal. |
| App "is damaged and can't be opened" | Same fix — caused by the quarantine flag, not actual corruption. |
| Folder is empty after unzipping | Make sure you unzipped **twice** — once for the GitHub artifact wrapper, once for the inner ditto zip. After the second unzip you should get a `TranzorExporter-Mac/` folder with two items inside. |
| App still won't open even after "Open Anyway" in System Settings | The .app's code signature may be damaged. Re-download the artifact and make sure you unzipped both layers (outer GitHub wrapper + inner ditto zip). Skipping the inner zip step is what damages the signature. |
| Connection timeout | Check that your VPN is active. |
| App still won't open after `xattr` | Make sure you ran it against the actual install path. If the app is somewhere other than `/Applications`, replace the path in the command. |

---

## 📌 Version Info

- Same codebase as Windows `TranzorExporter.exe`
- All features are fully supported on macOS
- Reports generated on Mac are identical to those from Windows

If you have any questions or run into issues, feel free to reach out!
