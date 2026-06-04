// ==UserScript==
// @name         Tranzor Bridge
// @namespace    tranzor-my-tools
// @version      0.6.1
// @description  Receive Exporter selections and walk through them on the Tranzor Platform.
// @match        http://tranzor-platform.int.rclabenv.com/*
// @match        https://tranzor-platform.int.rclabenv.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_addStyle
// @grant        GM_notification
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-idle
// @updateURL    https://raw.githubusercontent.com/Anna-SAP/tranzor-my-tools/master/userscript/tranzor_bridge.user.js
// @downloadURL  https://raw.githubusercontent.com/Anna-SAP/tranzor-my-tools/master/userscript/tranzor_bridge.user.js
// ==/UserScript==
// Maintainer note: any change to this file MUST bump @version above
// (semver). Tampermonkey/Violentmonkey only fetch an update when the
// remote version is strictly higher than the installed one.

(function () {
    'use strict';

    // ---- Constants ----
    const PORT_RANGE = [];
    for (let p = 48217; p <= 48226; p++) PORT_RANGE.push(p);

    const POLL_INTERVAL_MS = 3000;
    const DISCOVERY_TTL_MS = 60_000;

    // Tranzor DOM selectors. Update if Tranzor's UI changes — this is the
    // single point of attachment to the upstream HTML.
    const CONFIG = {
        SELECTORS: {
            // Try multiple candidate search inputs. The first one in the DOM wins.
            searchInput: 'input[type="search"], input[name*="search" i], input[placeholder*="search" i], input[placeholder*="Key" i]',
        },
    };

    // URL patterns for Tranzor's three translation surfaces. The
    // Send-to-Tranzor button navigates to whichever one matches the
    // envelope; these regexes let the userscript recognise the current
    // page so highlighting + ticking only fire when the user is on the
    // right task.
    //
    //   File Translation: /static/legacy/tasks/<task_id>
    //   MR Pipeline:      /static/?project_id=<urlencoded>&mr_id=<mr_iid>
    //                     (the bare /static/ path; query params carry identity)
    //   Scan Tasks:       /static/scans/<scan_task_uuid>
    //                     (the page has Overview + Strings sub-tabs;
    //                     translation rows live on Strings)
    const TASK_PATH_RE = /\/static\/legacy\/tasks\/([^/?#]+)/;
    const MR_PATH_RE = /^\/static\/?$/;
    const SCAN_PATH_RE = /^\/static\/scans\/([^/?#]+)/;

    // ---- State ----
    let endpoint = null; // { port, token, instance_id, discoveredAt }
    let lastSeq = 0;
    let currentEnvelope = null;
    let progress = {}; // string_key -> 'fixed' | 'skipped'
    let envelopeId = null;
    let pollTimer = null;
    let mounted = false;
    let highlightMode = true;       // default ON when on the matching task page
    let highlightedNodes = new Set(); // currently-marked DOM nodes (so we can clear cleanly)
    let tickMode = true;            // also tick the platform's own checkbox on each matched row
    let tickedNodes = new Set();    // rows whose checkbox WE ticked (so close can untick cleanly)
    let lastHighlightStats = { found: 0, ticked: 0 };
    let currentFilterKey = null;    // the String Key currently filtering Tranzor's search box
    let dismissed = false;          // user clicked ✕ to hide the panel
    let dismissedEnvelopeId = null; // remember which envelope was dismissed (auto-reopen for new ones)

    // ---- Style ----
    const STYLE = `
    .tz-bridge-panel {
        position: fixed; right: 0; top: 80px; width: 360px; max-height: 80vh;
        z-index: 999999; background: #0f172a; color: #e2e8f0; border-radius: 10px 0 0 10px;
        box-shadow: -4px 0 20px rgba(0,0,0,0.4); font-family: -apple-system, BlinkMacSystemFont, sans-serif;
        display: flex; flex-direction: column; overflow: hidden; transition: transform .25s;
    }
    .tz-bridge-panel.collapsed { transform: translateX(calc(100% - 36px)); }
    .tz-bridge-header {
        padding: 10px 14px; background: #27AE60; color: #fff; font-weight: 600; font-size: 13px;
        display: flex; align-items: center; justify-content: space-between; cursor: pointer;
    }
    .tz-bridge-title { display: flex; align-items: center; gap: 8px; }
    .tz-bridge-instance { font-size: 10px; opacity: 0.75; font-family: monospace; font-weight: 400; }
    .tz-bridge-header-actions { display: flex; align-items: center; gap: 4px; }
    .tz-bridge-close {
        background: transparent; border: 0; color: #fff; cursor: pointer;
        font-size: 14px; line-height: 1; padding: 2px 6px; border-radius: 4px;
        opacity: 0.85; transition: background 0.15s, opacity 0.15s;
    }
    .tz-bridge-close:hover { background: rgba(0,0,0,0.18); opacity: 1; }
    .tz-bridge-toggle { font-size: 14px; line-height: 1; cursor: pointer; padding: 2px 4px; }
    .tz-bridge-panel.dismissed { display: none; }
    .tz-bridge-reopen {
        position: fixed; right: 8px; top: 96px; z-index: 999999;
        background: #27AE60; color: #fff; border: 0; border-radius: 999px;
        width: 36px; height: 36px; cursor: pointer; font-size: 16px;
        box-shadow: -2px 2px 12px rgba(0,0,0,0.3); transition: transform .15s, background .15s;
        display: none;
    }
    .tz-bridge-reopen.visible { display: inline-flex; align-items: center; justify-content: center; }
    .tz-bridge-reopen:hover { background: #1f8c4f; transform: scale(1.08); }
    .tz-bridge-body {
        padding: 10px 12px; overflow-y: auto; flex: 1; font-size: 12px;
    }
    .tz-bridge-status { padding: 4px 0 8px; color: #94a3b8; font-size: 11px; border-bottom: 1px solid #1e293b; margin-bottom: 8px; }
    .tz-bridge-progress { font-weight: 600; color: #38bdf8; }
    .tz-bridge-item {
        padding: 8px; border-radius: 6px; background: #1e293b; margin-bottom: 6px;
        display: flex; flex-direction: column; gap: 4px;
    }
    .tz-bridge-item.fixed { opacity: 0.55; background: #064e3b; }
    .tz-bridge-item.skipped { opacity: 0.4; background: #1e293b; }
    .tz-bridge-item.fixed .tz-bridge-key { text-decoration: line-through; }
    .tz-bridge-row { display: flex; align-items: center; gap: 6px; }
    .tz-bridge-key {
        font-family: ui-monospace, monospace; font-size: 11px; color: #fbbf24;
        word-break: break-all; flex: 1; cursor: pointer;
    }
    .tz-bridge-meta { font-size: 10px; color: #94a3b8; }
    .tz-bridge-btn {
        font-size: 10px; padding: 3px 8px; border-radius: 4px; border: 0; cursor: pointer;
        font-weight: 600; background: #334155; color: #e2e8f0;
    }
    .tz-bridge-btn:hover { background: #475569; }
    .tz-bridge-btn.primary { background: #4472C4; color: #fff; }
    .tz-bridge-btn.primary:hover { background: #3461b0; }
    .tz-bridge-btn.fix { background: #27AE60; color: #fff; }
    .tz-bridge-btn.skip { background: #64748b; color: #fff; }
    .tz-bridge-btn.active { outline: 2px solid #fbbf24; }
    .tz-bridge-empty { padding: 16px 6px; text-align: center; color: #64748b; font-size: 12px; }
    .tz-bridge-paste {
        margin-top: 12px; padding-top: 12px; border-top: 1px solid #1e293b; font-size: 11px; color: #94a3b8;
    }
    .tz-bridge-paste textarea {
        width: 100%; height: 60px; margin-top: 4px; background: #1e293b; color: #e2e8f0;
        border: 1px solid #334155; border-radius: 4px; padding: 6px; font-family: monospace; font-size: 10px;
        box-sizing: border-box;
    }
    .tz-bridge-paste.collapsed > details { color: #64748b; }
    .tz-bridge-paste summary { cursor: pointer; user-select: none; font-size: 10px; }
    /* Per-click flash on the Tranzor row that matched the key */
    .tz-bridge-flash {
        animation: tzBridgeFlash 2.4s ease-out;
        position: relative; z-index: 1;
    }
    @keyframes tzBridgeFlash {
        0%   { background-color: rgba(251, 191, 36, 0.85) !important; box-shadow: 0 0 0 4px rgba(251, 191, 36, 0.95); }
        60%  { background-color: rgba(251, 191, 36, 0.45) !important; box-shadow: 0 0 0 3px rgba(251, 191, 36, 0.7); }
        100% { background-color: transparent; box-shadow: none; }
    }
    /* Persistent soft highlight for every selected row visible on the page */
    .tz-bridge-mark {
        background: rgba(39, 174, 96, 0.18) !important;
        box-shadow: inset 4px 0 0 0 #27AE60 !important;
        transition: background 0.2s;
    }
    .tz-bridge-mark.fixed-mark {
        background: rgba(100, 116, 139, 0.18) !important;
        box-shadow: inset 4px 0 0 0 #64748b !important;
    }
    .tz-bridge-task-badge {
        display: inline-flex; align-items: center; gap: 4px;
        padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: 600;
        font-family: monospace; margin-left: 6px;
    }
    .tz-bridge-task-badge.match { background: rgba(39, 174, 96, 0.25); color: #86efac; }
    .tz-bridge-task-badge.mismatch { background: rgba(230, 126, 34, 0.25); color: #fdba74; cursor: pointer; }
    .tz-bridge-toolbar {
        display: flex; gap: 6px; margin-bottom: 8px; flex-wrap: wrap;
    }
    .tz-bridge-toolbar .tz-bridge-btn {
        font-size: 10px;
    }
    .tz-bridge-langs {
        margin: 4px 0 8px 0; display: flex; flex-wrap: wrap; gap: 4px;
        font-size: 10px; color: #cbd5e1;
    }
    .tz-bridge-lang-chip {
        background: rgba(56, 189, 248, 0.18); color: #38bdf8;
        padding: 1px 6px; border-radius: 999px; font-family: monospace; font-weight: 600;
    }
    .tz-bridge-help {
        background: rgba(230, 126, 34, 0.12);
        border-left: 3px solid #E67E22;
        color: #fdba74; font-size: 11px; line-height: 1.45;
        padding: 8px 10px; margin: 0 0 10px 0; border-radius: 4px;
    }
    .tz-bridge-key-group {
        border: 1px solid #1e293b; border-radius: 6px;
        padding: 8px 8px 4px 8px; margin-bottom: 10px;
    }
    .tz-bridge-key-group.current {
        border-color: #4472C4; box-shadow: 0 0 0 1px rgba(68, 114, 196, 0.4);
    }
    .tz-bridge-key-group-head {
        display: flex; align-items: flex-start; gap: 6px; margin-bottom: 6px;
    }
    .tz-bridge-key-group-head .tz-bridge-key {
        flex: 1; font-size: 11px;
    }
    .tz-bridge-key-group-count {
        font-size: 10px; color: #94a3b8; background: #1e293b;
        padding: 1px 6px; border-radius: 999px; flex-shrink: 0;
        font-family: monospace;
    }
    .tz-bridge-key-group .tz-bridge-item {
        padding: 4px 8px; margin-left: 8px;
    }
    `;

    // ---- Bridge HTTP (via GM_xmlhttpRequest, bypasses PNA) ----
    function gmGet(url) {
        return new Promise((resolve) => {
            GM_xmlhttpRequest({
                method: 'GET',
                url: url,
                headers: endpoint ? { 'X-Bridge-Token': endpoint.token } : {},
                timeout: 1500,
                onload: (r) => resolve({ ok: r.status >= 200 && r.status < 300, status: r.status, body: r.responseText }),
                onerror: () => resolve({ ok: false, status: 0, body: '' }),
                ontimeout: () => resolve({ ok: false, status: 0, body: '' }),
            });
        });
    }

    async function probeHealth(port) {
        const url = `http://127.0.0.1:${port}/health`;
        const r = await gmGet(url);
        if (!r.ok) return null;
        try {
            const body = JSON.parse(r.body);
            return body.ok ? { port, instance_id: body.instance_id } : null;
        } catch (e) { return null; }
    }

    async function discoverBridge(force = false) {
        if (!force && endpoint && (Date.now() - endpoint.discoveredAt < DISCOVERY_TTL_MS)) {
            return endpoint;
        }
        // Try the previously-known port first so wake-up is instant.
        const cached = GM_getValue('bridge_endpoint', null);
        const orderedPorts = cached && cached.port
            ? [cached.port, ...PORT_RANGE.filter(p => p !== cached.port)]
            : PORT_RANGE;

        for (const port of orderedPorts) {
            const r = await probeHealth(port);
            if (r) {
                // Token is learned via the #tzbridge_token=… one-time URL hash
                // that the HTML report appends on successful bridge handoff.
                // Without it, /pull will 401 silently and the sidebar will
                // prompt the user to send a handoff from the report.
                const tokenFromUser = GM_getValue('bridge_token', '');
                endpoint = {
                    port: r.port,
                    instance_id: r.instance_id,
                    token: tokenFromUser,
                    discoveredAt: Date.now(),
                };
                GM_setValue('bridge_endpoint', { port: r.port });
                return endpoint;
            }
        }
        endpoint = null;
        return null;
    }

    function captureTokenFromHash() {
        const m = location.hash.match(/#tzbridge_token=([A-Za-z0-9_\-\.~%]+)/);
        if (!m) return false;
        const token = decodeURIComponent(m[1]);
        if (!token) return false;
        GM_setValue('bridge_token', token);
        if (endpoint) endpoint.token = token;
        history.replaceState(null, '', location.pathname + location.search);
        return true;
    }

    // The @version meta tag at the top of this file. Sent as
    // X-Userscript-Version on every /pull so the my-tools setup wizard can
    // detect outdated installs and prompt for re-install. Keep this in sync
    // with the meta block; any change to either MUST bump the other.
    const USERSCRIPT_VERSION = '0.6.1';

    async function pullEnvelope() {
        if (!endpoint || !endpoint.token) return null;
        const url = `http://127.0.0.1:${endpoint.port}/pull?since=${lastSeq}`;
        return new Promise((resolve) => {
            GM_xmlhttpRequest({
                method: 'GET',
                url: url,
                headers: {
                    'X-Bridge-Token': endpoint.token,
                    'X-Userscript-Version': USERSCRIPT_VERSION,
                    'Origin': 'http://tranzor-platform.int.rclabenv.com',
                },
                timeout: 2000,
                onload: (r) => {
                    if (r.status === 204) { resolve({ updated: false }); return; }
                    if (r.status === 401) { resolve({ updated: false, badToken: true }); return; }
                    if (r.status >= 200 && r.status < 300) {
                        try {
                            const body = JSON.parse(r.responseText);
                            resolve({ updated: true, seq: body.seq, envelope: body.envelope });
                        } catch (e) { resolve({ updated: false }); }
                        return;
                    }
                    resolve({ updated: false });
                },
                onerror: () => resolve({ updated: false }),
                ontimeout: () => resolve({ updated: false }),
            });
        });
    }

    // ---- Sidebar UI ----
    function ensureMounted() {
        if (mounted) return;
        GM_addStyle(STYLE);
        const root = document.createElement('div');
        root.className = 'tz-bridge-panel collapsed';
        root.id = 'tz-bridge-panel';
        root.innerHTML = `
            <div class="tz-bridge-header" id="tz-bridge-header">
                <div class="tz-bridge-title">
                    <span>📋 Tranzor Bridge</span>
                    <span class="tz-bridge-instance" id="tz-bridge-instance"></span>
                </div>
                <div class="tz-bridge-header-actions">
                    <button class="tz-bridge-close" id="tz-bridge-close" title="Close panel (clears on-page highlights)">✕</button>
                    <span class="tz-bridge-toggle" id="tz-bridge-toggle" title="Collapse / expand">«</span>
                </div>
            </div>
            <div class="tz-bridge-body" id="tz-bridge-body">
                <div class="tz-bridge-empty">Waiting for selections from TranzorExporter…</div>
            </div>
        `;
        document.body.appendChild(root);

        // Floating pill to re-open the panel after dismissal. Hidden by default;
        // also auto-shown whenever a new envelope arrives while dismissed.
        const reopen = document.createElement('button');
        reopen.className = 'tz-bridge-reopen';
        reopen.id = 'tz-bridge-reopen';
        reopen.title = 'Reopen Tranzor Bridge';
        reopen.textContent = '📋';
        document.body.appendChild(reopen);
        reopen.addEventListener('click', restorePanel);

        // Header click collapses/expands; ✕ button dismisses entirely.
        document.getElementById('tz-bridge-header').addEventListener('click', (e) => {
            if (e.target.closest('#tz-bridge-close')) return; // handled below
            root.classList.toggle('collapsed');
            const tog = document.getElementById('tz-bridge-toggle');
            tog.textContent = root.classList.contains('collapsed') ? '«' : '»';
        });
        document.getElementById('tz-bridge-close').addEventListener('click', (e) => {
            e.stopPropagation();
            dismissPanel();
        });
        mounted = true;
    }

    function dismissPanel() {
        dismissed = true;
        dismissedEnvelopeId = envelopeId;
        clearAllMarks();
        const root = document.getElementById('tz-bridge-panel');
        if (root) root.classList.add('dismissed');
        const reopen = document.getElementById('tz-bridge-reopen');
        if (reopen) reopen.classList.add('visible');
    }

    function restorePanel() {
        dismissed = false;
        dismissedEnvelopeId = null;
        const root = document.getElementById('tz-bridge-panel');
        if (root) {
            root.classList.remove('dismissed');
            root.classList.remove('collapsed');
            const tog = document.getElementById('tz-bridge-toggle');
            if (tog) tog.textContent = '»';
        }
        const reopen = document.getElementById('tz-bridge-reopen');
        if (reopen) reopen.classList.remove('visible');
        render();
    }

    function escapeHtml(s) {
        if (s === null || s === undefined) return '';
        return String(s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    function render() {
        ensureMounted();
        const instLabel = endpoint
            ? `port ${endpoint.port} · ${endpoint.instance_id.slice(0, 6)}`
            : (endpoint === null ? 'no bridge' : '');
        document.getElementById('tz-bridge-instance').textContent = instLabel;
        const body = document.getElementById('tz-bridge-body');

        if (!currentEnvelope || !currentEnvelope.items || !currentEnvelope.items.length) {
            body.innerHTML = `
                <div class="tz-bridge-status">No selections yet.</div>
                <div class="tz-bridge-empty">
                    Click <b>↗ Send to Tranzor</b> in your Exporter HTML report to populate this list.
                </div>
                ${pasteFallbackHtml(/*expanded=*/true)}
            `;
            wirePasteFallback();
            return;
        }

        const fixedCount = currentEnvelope.items.filter(i => progress[progressKeyOf(i)] === 'fixed').length;
        const total = currentEnvelope.items.length;
        const ctx = currentEnvelope.context || {};
        const ctxBits = [];
        const envMr = envelopeMrCoords();
        const envScan = envelopeScanTaskId();
        if (envMr) {
            ctxBits.push('MR ' + escapeHtml(envMr.project_id) + ' #' + escapeHtml(envMr.mr_id));
        } else if (envScan) {
            ctxBits.push('Scan ' + escapeHtml(envScan.slice(0, 8)));
        } else if (ctx.task_id) {
            ctxBits.push('Task ' + escapeHtml(ctx.task_id));
        }
        if (ctx.language) ctxBits.push(escapeHtml(ctx.language));
        const ctxLabel = ctxBits.length ? ctxBits.join(' · ') : 'mixed';

        // Task-page match indicator + jump link when on the wrong page. The
        // identity differs across the three surfaces (MR Pipeline =
        // project_id + mr_id; Scan Tasks = scan_task_id; File Translation =
        // task_id) but the badge UX is the same.
        const targetPath = envelopeTargetPath();
        const onMatchingPage = isOnEnvelopeTaskPage();
        let taskBadge = '';
        if (envMr) {
            const label = 'MR ' + escapeHtml(envMr.project_id) + ' #' + escapeHtml(envMr.mr_id);
            taskBadge = onMatchingPage
                ? `<span class="tz-bridge-task-badge match" title="You're on the right MR page">on ${label}</span>`
                : `<a class="tz-bridge-task-badge mismatch" href="${targetPath}" title="Open the MR page these items belong to">go to ${label} →</a>`;
        } else if (envScan) {
            const label = 'scan ' + escapeHtml(envScan.slice(0, 8));
            taskBadge = onMatchingPage
                ? `<span class="tz-bridge-task-badge match" title="You're on the right scan task page">on ${label}</span>`
                : `<a class="tz-bridge-task-badge mismatch" href="${targetPath}" title="Open the scan task page these items belong to">go to ${label} →</a>`;
        } else {
            const envTask = envelopeTaskId();
            if (envTask) {
                taskBadge = onMatchingPage
                    ? `<span class="tz-bridge-task-badge match" title="You're on the right task page">on task ${escapeHtml(envTask)}</span>`
                    : `<a class="tz-bridge-task-badge mismatch" href="${targetPath || ('/static/legacy/tasks/' + encodeURIComponent(envTask))}" title="Open the task page these items belong to">go to task ${escapeHtml(envTask)} →</a>`;
            }
        }

        // Group items by unique String Key. Tranzor's list is one big paginated
        // table of (key, language) entries, so the user's 22 selections often
        // reduce to a handful of unique keys — each driving one "fill Tranzor's
        // search → tick matching rows → click Batch Retranslate" cycle.
        const keyGroups = uniqueStringKeyGroups();
        const currentKeyIdx = currentFilterKey
            ? Math.max(0, keyGroups.findIndex(g => g.key === currentFilterKey))
            : -1;

        const groupsHtml = keyGroups.map((grp, gIdx) => {
            const isCurrent = grp.key === currentFilterKey;
            const groupItemsHtml = grp.items.map(it => {
                const pKey = progressKeyOf(it);
                const state = progress[pKey] || '';
                const cls = state ? ' ' + state : '';
                return `
                    <div class="tz-bridge-item${cls}" data-key="${escapeHtml(it.string_key)}" data-lang="${escapeHtml(it.language || '')}">
                        <div class="tz-bridge-meta">${escapeHtml(it.language || '')} · ${escapeHtml(it.translation_type || '')}</div>
                        <div class="tz-bridge-row">
                            <button class="tz-bridge-btn primary" data-action="find">🔍 Find</button>
                            <button class="tz-bridge-btn fix${state === 'fixed' ? ' active' : ''}" data-action="fix">✓ Fixed</button>
                            <button class="tz-bridge-btn skip${state === 'skipped' ? ' active' : ''}" data-action="skip">⤵ Skip</button>
                        </div>
                    </div>
                `;
            }).join('');
            return `
                <div class="tz-bridge-key-group${isCurrent ? ' current' : ''}" data-key="${escapeHtml(grp.key)}">
                    <div class="tz-bridge-key-group-head">
                        <span class="tz-bridge-key" title="Click to copy">${escapeHtml(grp.key)}</span>
                        <span class="tz-bridge-key-group-count">${grp.items.length} langs</span>
                    </div>
                    <div class="tz-bridge-row" style="margin-bottom:6px;">
                        <button class="tz-bridge-btn primary" data-action="filter-key" data-key-idx="${gIdx}">
                            🎯 ${isCurrent ? 'Re-filter' : 'Filter Tranzor by this key'}${isCurrent ? '' : ` (${gIdx + 1}/${keyGroups.length})`}
                        </button>
                    </div>
                    ${groupItemsHtml}
                </div>
            `;
        }).join('');

        const hlStats = lastHighlightStats || { found: 0, ticked: 0 };
        const onTaskPage = highlightMode && isOnEnvelopeTaskPage();
        const onPageNote = onTaskPage
            ? ` · ${hlStats.found}/${total} on page${tickMode ? ` · ${tickedNodes.size} ticked` : ''}`
            : '';

        // Language distribution chips give a quick read of how many languages
        // the selection spans (cosmetic context, not action-driving).
        const langChips = envelopeLanguages().map(({ lang, n }) =>
            `<span class="tz-bridge-lang-chip">${escapeHtml(lang)} · ${n}</span>`
        ).join('');

        // Workflow guidance. Tranzor's list is paginated across all (key,lang)
        // entries; "Filter by next key" → tick → Batch Retranslate → repeat.
        let helpHtml = '';
        if (onTaskPage && keyGroups.length > 1 && hlStats.found < total) {
            helpHtml = `<div class="tz-bridge-help">
                Your selection has <b>${keyGroups.length} unique String Keys</b> across ${total} (key, language) entries. Tranzor's list shows one filter at a time — click <b>🎯 Filter Tranzor</b> on each group to walk through them. Each pass auto-ticks the matched rows; then click Tranzor's <b>Batch Retranslate</b>.
            </div>`;
        } else if (onTaskPage && hlStats.found === 0 && keyGroups.length === 1) {
            helpHtml = `<div class="tz-bridge-help">
                No rows on screen yet. Click <b>🎯 Filter Tranzor by this key</b> to fill Tranzor's search box and surface the ${total} matching rows.
            </div>`;
        }

        // Primary "next key" button is shown when there's more than one key to
        // cycle through. It always advances to the next unique key in order.
        const nextKeyBtn = keyGroups.length > 1
            ? `<button class="tz-bridge-btn primary" id="tz-bridge-next-key" title="Fill Tranzor's search box with the next unique String Key and auto-tick its rows">
                  🎯 Filter Tranzor by next key${currentKeyIdx >= 0 ? ` (next: ${(currentKeyIdx + 1) % keyGroups.length + 1}/${keyGroups.length})` : ` (1/${keyGroups.length})`}
              </button>`
            : '';

        body.innerHTML = `
            <div class="tz-bridge-status">
                ${ctxLabel} · <span class="tz-bridge-progress">${fixedCount}/${total}</span> fixed${onPageNote}${taskBadge}
            </div>
            ${langChips ? `<div class="tz-bridge-langs">Languages: ${langChips}</div>` : ''}
            <div class="tz-bridge-toolbar">
                ${nextKeyBtn}
                <button class="tz-bridge-btn ${highlightMode ? 'active primary' : ''}" id="tz-bridge-hl-toggle" title="Mark every matched row on the Tranzor page with a green stripe">
                    ${highlightMode ? '👀 Highlight on' : '⋯ Highlight off'}
                </button>
                <button class="tz-bridge-btn ${tickMode ? 'active primary' : ''}" id="tz-bridge-tick-toggle" title="Also tick Tranzor's own row checkbox. Closing the panel un-ticks our ticks.">
                    ${tickMode ? '☑ Auto-tick on' : '☐ Auto-tick off'}
                </button>
                <button class="tz-bridge-btn" id="tz-bridge-rescan" title="Re-apply the current filter and rescan for matching rows">
                    🔄 Re-scan
                </button>
            </div>
            ${helpHtml}
            ${groupsHtml}
            ${pasteFallbackHtml(/*expanded=*/false)}
        `;
        wirePasteFallback();
        const toggleBtn = document.getElementById('tz-bridge-hl-toggle');
        if (toggleBtn) {
            toggleBtn.addEventListener('click', () => {
                highlightMode = !highlightMode;
                refreshHighlights();
                render();
            });
        }
        const tickToggleBtn = document.getElementById('tz-bridge-tick-toggle');
        if (tickToggleBtn) {
            tickToggleBtn.addEventListener('click', () => {
                if (tickMode) {
                    // Turning off: untick the rows we ticked, leave user-ticked alone.
                    untickOurCheckboxes();
                    tickMode = false;
                } else {
                    tickMode = true;
                    // Re-applying highlights will tick matching rows.
                    refreshHighlights();
                }
                render();
            });
        }
        const rescanBtn = document.getElementById('tz-bridge-rescan');
        if (rescanBtn) {
            rescanBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                manualRescan();
            });
        }
        const nextKeyBtnEl = document.getElementById('tz-bridge-next-key');
        if (nextKeyBtnEl) {
            nextKeyBtnEl.addEventListener('click', (e) => {
                e.stopPropagation();
                const next = nextUniqueKeyToFilter();
                if (next) filterTranzorByKey(next);
            });
        }
        // Per-group key-headers: copy key on click; wire the filter-key button.
        body.querySelectorAll('.tz-bridge-key-group').forEach(grpEl => {
            const key = grpEl.dataset.key;
            const keyEl = grpEl.querySelector('.tz-bridge-key-group-head .tz-bridge-key');
            if (keyEl) {
                keyEl.addEventListener('click', () => {
                    navigator.clipboard.writeText(key).catch(() => {});
                });
            }
            const filterBtn = grpEl.querySelector('[data-action="filter-key"]');
            if (filterBtn) {
                filterBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    filterTranzorByKey(key);
                });
            }
        });
        body.querySelectorAll('.tz-bridge-item').forEach(itemEl => {
            const key = itemEl.dataset.key;
            const lang = itemEl.dataset.lang || '';
            const item = { string_key: key, language: lang };
            const pKey = progressKeyOf(item);
            const keyEl = itemEl.querySelector('.tz-bridge-key');
            if (keyEl) {
                keyEl.addEventListener('click', () => {
                    navigator.clipboard.writeText(key).catch(() => {});
                });
            }
            itemEl.querySelectorAll('.tz-bridge-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const action = btn.dataset.action;
                    if (action === 'find') findItemOnPage(item);
                    else if (action === 'fix') toggleProgress(pKey, 'fixed');
                    else if (action === 'skip') toggleProgress(pKey, 'skipped');
                });
            });
        });
        // Refresh the on-page highlights after re-rendering the sidebar.
        refreshHighlights();
    }

    function pasteFallbackHtml(expanded) {
        if (expanded) {
            return `
                <div class="tz-bridge-paste">
                    Bridge not used? Paste the JSON copied by the report here:
                    <textarea id="tz-bridge-paste" placeholder='{"items":[…]}'></textarea>
                </div>
            `;
        }
        // When an envelope is already loaded, collapse the paste box so it
        // doesn't compete visually with the items list.
        return `
            <div class="tz-bridge-paste">
                <details>
                    <summary>Paste JSON from another report (advanced)</summary>
                    <textarea id="tz-bridge-paste" placeholder='{"items":[…]}'></textarea>
                </details>
            </div>
        `;
    }

    function wirePasteFallback() {
        const ta = document.getElementById('tz-bridge-paste');
        if (!ta) return;
        ta.addEventListener('paste', (e) => {
            setTimeout(() => {
                try {
                    const env = JSON.parse(ta.value);
                    ingestEnvelope(env, /*fromPaste=*/true);
                } catch (err) { /* ignore until valid */ }
            }, 10);
        });
    }

    function toggleProgress(key, state) {
        if (progress[key] === state) {
            delete progress[key];
        } else {
            progress[key] = state;
        }
        if (envelopeId) GM_setValue('progress::' + envelopeId, progress);
        render();
    }

    function getCurrentTaskId() {
        const m = TASK_PATH_RE.exec(location.pathname);
        return m ? decodeURIComponent(m[1]) : null;
    }

    function getCurrentMrCoords() {
        if (!MR_PATH_RE.test(location.pathname)) return null;
        const params = new URLSearchParams(location.search);
        const project_id = params.get('project_id');
        const mr_id = params.get('mr_id');
        if (!project_id || !mr_id) return null;
        return { project_id, mr_id };
    }

    function getCurrentScanTaskId() {
        const m = SCAN_PATH_RE.exec(location.pathname);
        return m ? decodeURIComponent(m[1]) : null;
    }

    function envelopeTaskId() {
        if (!currentEnvelope) return null;
        const ctx = currentEnvelope.context || {};
        if (ctx.task_id) return String(ctx.task_id);
        const first = currentEnvelope.items && currentEnvelope.items[0];
        return first && first.task_id ? String(first.task_id) : null;
    }

    function envelopeMrCoords() {
        if (!currentEnvelope) return null;
        const ctx = currentEnvelope.context || {};
        const first = (currentEnvelope.items && currentEnvelope.items[0]) || {};
        const project_id = ctx.project_id || first.project_id;
        const mr_id = ctx.mr_id || first.mr_id;
        if (!project_id || !mr_id) return null;
        return { project_id: String(project_id), mr_id: String(mr_id) };
    }

    function envelopeScanTaskId() {
        if (!currentEnvelope) return null;
        const ctx = currentEnvelope.context || {};
        if (ctx.scan_task_id) return String(ctx.scan_task_id);
        const first = currentEnvelope.items && currentEnvelope.items[0];
        return first && first.scan_task_id ? String(first.scan_task_id) : null;
    }

    // Where the envelope wants the user to be. Priority order matches the
    // exporter's sendToTranzor: MR Pipeline > Scan Tasks > File Translation.
    // Scan task UUIDs and File Translation task IDs overlap shape-wise, so
    // routing must come from the explicit scan_task_id field — falling back
    // to the legacy task path for a scan envelope would 404 with "Failed
    // to load task detail".
    function envelopeTargetPath() {
        const mr = envelopeMrCoords();
        if (mr) {
            return '/static/?project_id=' + encodeURIComponent(mr.project_id)
                 + '&mr_id=' + encodeURIComponent(mr.mr_id);
        }
        const scan = envelopeScanTaskId();
        if (scan) return '/static/scans/' + encodeURIComponent(scan);
        const t = envelopeTaskId();
        if (t) return '/static/legacy/tasks/' + encodeURIComponent(t);
        return null;
    }

    function isOnEnvelopeTaskPage() {
        const envMr = envelopeMrCoords();
        if (envMr) {
            const onMr = getCurrentMrCoords();
            return Boolean(onMr && onMr.project_id === envMr.project_id && onMr.mr_id === envMr.mr_id);
        }
        const envScan = envelopeScanTaskId();
        if (envScan) {
            const onScan = getCurrentScanTaskId();
            return Boolean(onScan && String(onScan) === String(envScan));
        }
        const onTask = getCurrentTaskId();
        const envTask = envelopeTaskId();
        return Boolean(onTask && envTask && String(onTask) === String(envTask));
    }

    // Walk text nodes to find a row container holding the given key. Uses
    // TreeWalker so it's O(n) and skips nodes inside the bridge sidebar itself.
    function findRowContainingText(text) {
        if (!text) return null;
        const root = document.body;
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
            acceptNode: (node) => {
                if (!node.nodeValue || node.nodeValue.indexOf(text) === -1) return NodeFilter.FILTER_SKIP;
                // Skip our own panel
                let p = node.parentElement;
                while (p) {
                    if (p.id === 'tz-bridge-panel') return NodeFilter.FILTER_REJECT;
                    p = p.parentElement;
                }
                return NodeFilter.FILTER_ACCEPT;
            },
        });
        const node = walker.nextNode();
        if (!node) return null;
        return rowAncestorOf(node.parentElement);
    }

    function rowAncestorOf(el) {
        if (!el) return null;
        // Use closest() for canonical row containers (table rows, list items,
        // ARIA rows). Avoids surprises from intermediate <span>/<div> wraps.
        if (el.closest) {
            const row = el.closest('tr, li, [role="row"]');
            if (row) return row;
        }
        // Fallback for div-based "row" layouts (flex/grid with 3+ children).
        let p = el;
        while (p && p !== document.body) {
            if (p.nodeType === 1 && p.tagName === 'DIV' && p.children && p.children.length >= 3) {
                const style = window.getComputedStyle(p);
                if (style.display.startsWith('flex') || style.display.startsWith('grid')) return p;
            }
            p = p.parentElement;
        }
        return null;
    }

    // Check whether a row element contains the given language code. Tranzor
    // renders the language code as the text of a dedicated <td> cell, so we
    // scan cells individually — row.textContent concatenates cell text WITHOUT
    // separators, which strips the word-boundary the previous regex relied on
    // (e.g. "...Implementationfr-CA..." → "fr-CA" preceded by 'n' fails the
    // [^A-Za-z0-9_] guard, leaving only languages whose adjacent cell text
    // happens to end in punctuation like ')'.matched).
    function rowContainsLanguageToken(row, lang) {
        if (!lang) return true;
        const cells = row.querySelectorAll('td, th, [role="cell"], [role="gridcell"]');
        if (cells.length > 0) {
            const wanted = lang.toLowerCase();
            for (let i = 0; i < cells.length; i++) {
                const t = (cells[i].textContent || '').trim();
                if (!t) continue;
                if (t === lang || t.toLowerCase() === wanted) return true;
                // Also accept a cell that contains the lang surrounded by
                // safe boundaries (rare: lang next to a badge or icon text).
                const escaped = lang.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                const re = new RegExp('(^|[^A-Za-z0-9_])' + escaped + '($|[^A-Za-z0-9_])', 'i');
                if (re.test(t)) return true;
            }
            return false;
        }
        // Non-table layouts: innerText respects rendered line breaks between
        // child blocks, so adjacent "cell" text won't fuse together.
        const txt = row.innerText || row.textContent || '';
        const escaped = lang.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const re = new RegExp('(^|[^A-Za-z0-9_])' + escaped + '($|[^A-Za-z0-9_])', 'i');
        return re.test(txt);
    }

    // True when `key` appears in `text` as a COMPLETE OPUS ID rather than as a
    // prefix/substring of a longer one. OPUS IDs are runs of [A-Za-z0-9_.], so
    // an occurrence is "whole" only when the characters bracketing it are not
    // more identifier characters. This is correctness-critical: a short key
    // like "…app.rooms.SECONDARY" is a textual prefix of the sibling key
    // "…app.rooms.SECONDARY_DISPLAY_LAYOUT", and a plain indexOf() match binds
    // it to whichever row renders first in the DOM (the longer key's row,
    // which sorts earlier here). That steals the longer row's tick and leaves
    // the real "SECONDARY" row unticked — the "20 selected → only 18 ticked"
    // symptom. The whole-key guard rejects the prefix match so the walker
    // keeps scanning to the genuine row.
    const KEY_CONTINUATION_RE = /[A-Za-z0-9_.]/;
    function textContainsWholeKey(text, key) {
        if (!text || !key) return false;
        let from = 0;
        for (;;) {
            const idx = text.indexOf(key, from);
            if (idx === -1) return false;
            const after = text.charAt(idx + key.length);
            const before = idx > 0 ? text.charAt(idx - 1) : '';
            if (!KEY_CONTINUATION_RE.test(after) && !KEY_CONTINUATION_RE.test(before)) return true;
            from = idx + 1;
        }
    }

    // Find a row that contains BOTH the envelope item's string_key AND its
    // language code. This is the correctness-critical primitive: without the
    // language check, multi-language selections all collapse onto the first
    // language row Tranzor happens to render (e.g. en-GB), wrongly matching
    // 22 items to the same row. The string_key is matched whole (see
    // textContainsWholeKey) so prefix-sibling keys don't cross-bind rows.
    function findRowForItem(item) {
        if (!item || !item.string_key) return null;
        const key = item.string_key;
        const root = document.body;
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
            acceptNode: (node) => {
                if (!node.nodeValue || !textContainsWholeKey(node.nodeValue, key)) return NodeFilter.FILTER_SKIP;
                let p = node.parentElement;
                while (p) {
                    if (p.id === 'tz-bridge-panel') return NodeFilter.FILTER_REJECT;
                    p = p.parentElement;
                }
                return NodeFilter.FILTER_ACCEPT;
            },
        });
        let textNode;
        const seenRows = new Set();
        while ((textNode = walker.nextNode())) {
            const row = rowAncestorOf(textNode.parentElement);
            if (!row || seenRows.has(row)) continue;
            seenRows.add(row);
            if (rowContainsLanguageToken(row, item.language)) {
                return row;
            }
        }
        return null;
    }

    // MR Pipeline pages show one language at a time via a top tab strip
    // (de-DE · en-AU · … · zh-TW). Tranzor lands on the first tab (de-DE) by
    // default, so a multi-language envelope (e.g. zh-TW + zh-HK) sees zero
    // matching rows until the user clicks a tab. This regex matches tab
    // labels like "de-DE", "zh-TW (5)", "es-419 (12)" — a lowercase IETF
    // language subtag, a required region/script subtag of 2–4 chars, and an
    // optional row-count badge. Two-letter region "DE" / three-digit "419"
    // / four-letter script "Hant" all fit; arbitrary long suffixes don't,
    // which keeps "fr-CAFoo"-style false positives out.
    const LANG_TAB_RE = /^([a-z]{2,3}-[A-Za-z0-9]{2,4})\s*(?:\(\s*\d+\s*\))?\s*$/;
    // Defence in depth against bare words that survive the regex anyway.
    const LANG_TAB_MIN_LEN = 5; // shortest real code is "fr-FR" (5 chars)

    function isElementActiveTab(el) {
        if (!el) return false;
        if (
            el.getAttribute('aria-selected') === 'true' ||
            el.getAttribute('aria-pressed') === 'true' ||
            el.classList.contains('active') ||
            el.classList.contains('selected') ||
            el.classList.contains('is-active') ||
            el.classList.contains('is-selected')
        ) return true;
        // Some tab UIs mark the active item on the parent <li> only.
        const p = el.parentElement;
        if (p && (
            p.classList.contains('active') ||
            p.classList.contains('selected') ||
            p.classList.contains('is-active') ||
            p.classList.contains('is-selected')
        )) return true;
        return false;
    }

    function findLanguageTabs() {
        // Selector order = preference. Click-handler-bearing elements
        // ([role="tab"], <a>, <button>) come before container <li>, so when
        // multiple ancestors share the same textContent we keep the one
        // most likely to fire the tab-switch handler.
        const tabSelectors = [
            '[role="tab"]',
            'button',
            'a',
            '.nav-link',
            'li',
            '.tab',
            '.nav-item',
        ];
        const byLang = new Map();
        const visited = new Set();
        tabSelectors.forEach(sel => {
            document.querySelectorAll(sel).forEach(el => {
                if (visited.has(el)) return;
                visited.add(el);
                if (isInOurPanel(el)) return;
                const text = (el.textContent || '').trim();
                if (text.length < LANG_TAB_MIN_LEN) return;
                const m = LANG_TAB_RE.exec(text);
                if (!m) return;
                const lang = m[1];
                if (byLang.has(lang)) return; // earlier (more specific) wins
                byLang.set(lang, { lang, el, isActive: isElementActiveTab(el) });
            });
        });
        return Array.from(byLang.values());
    }

    // Click the language tab matching `lang`. Returns true if a click was
    // dispatched (i.e. we actually changed tabs). No-op if already active,
    // or if no tab strip is on the page (File Translation surface).
    function ensureLanguageTab(lang) {
        if (!lang) return false;
        const tabs = findLanguageTabs();
        if (!tabs.length) return false;
        const target = tabs.find(t => t.lang === lang);
        if (!target) return false;
        if (target.isActive) return false;
        try { target.el.click(); return true; } catch (e) { return false; }
    }

    // Choose the best language tab to switch to for a given set of languages.
    // Prefer the currently-active tab if it's in the set (no-op switch is
    // free), otherwise pick the first language in the set. Returns the chosen
    // language code, or null if no tab strip exists.
    function pickLanguageTab(langs) {
        if (!langs || !langs.length) return null;
        const tabs = findLanguageTabs();
        if (!tabs.length) return null;
        const active = tabs.find(t => t.isActive);
        if (active && langs.includes(active.lang)) return active.lang;
        // First envelope language that actually has a tab on this page.
        const present = langs.find(l => tabs.some(t => t.lang === l));
        return present || null;
    }

    // Tranzor's task page exposes a "By Language" / "All Languages" toggle.
    // The default "By Language" view shows only one language at a time, so a
    // filtered search may hide rows we want to tick. Click "All Languages"
    // once after each filter unless it's already active.
    function ensureAllLanguagesView() {
        const candidates = Array.from(document.querySelectorAll(
            'button, [role="button"], [role="tab"], a.btn, [aria-pressed]'
        )).filter(el => !isInOurPanel(el));
        const target = candidates.find(b => {
            const text = (b.textContent || '').trim();
            return /^all\s+languages$/i.test(text);
        });
        if (!target) return false;
        const isActive =
            target.getAttribute('aria-pressed') === 'true' ||
            target.getAttribute('aria-selected') === 'true' ||
            target.classList.contains('selected') ||
            target.classList.contains('active') ||
            target.classList.contains('is-active') ||
            target.classList.contains('is-selected');
        if (isActive) return false;
        try { target.click(); return true; } catch (e) { return false; }
    }

    // Scan Task pages (/static/scans/<uuid>) open on the Overview sub-tab
    // by default. Translation rows live on the Strings sub-tab, so the
    // bridge has to click "Strings" before searching/ticking can do
    // anything useful. No-op when already on Strings or when the sub-tab
    // strip doesn't exist (i.e. on MR / File Translation surfaces).
    function ensureScanStringsTab() {
        if (!SCAN_PATH_RE.test(location.pathname)) return false;
        const candidates = Array.from(document.querySelectorAll(
            'a, button, [role="tab"], .nav-link, li'
        )).filter(el => !isInOurPanel(el));
        const target = candidates.find(el => {
            const text = (el.textContent || '').trim();
            return /^strings$/i.test(text);
        });
        if (!target) return false;
        const isActive =
            target.getAttribute('aria-selected') === 'true' ||
            target.classList.contains('active') ||
            target.classList.contains('selected') ||
            target.classList.contains('is-active') ||
            target.classList.contains('is-selected') ||
            (target.parentElement && (
                target.parentElement.classList.contains('active') ||
                target.parentElement.classList.contains('selected') ||
                target.parentElement.classList.contains('is-active') ||
                target.parentElement.classList.contains('is-selected')
            ));
        if (isActive) return false;
        try { target.click(); return true; } catch (e) { return false; }
    }

    // Per-(key, language) identifier so per-item progress tracks both axes.
    // Otherwise marking the ja-JP version of a key as Fixed would also dim
    // the es-419 / zh-TW versions, which is wrong for multi-language batches.
    function progressKeyOf(item) {
        return (item.string_key || '') + '|' + (item.language || '');
    }

    function scrollAndFlash(el) {
        if (!el) return;
        try {
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        } catch (e) {
            el.scrollIntoView();
        }
        el.classList.remove('tz-bridge-flash');
        // Force reflow so the animation restarts even on repeat clicks.
        void el.offsetWidth;
        el.classList.add('tz-bridge-flash');
        setTimeout(() => el.classList.remove('tz-bridge-flash'), 2500);
    }

    // Find a checkbox-like control inside the matched row and set it to `want`.
    // Tries native <input type=checkbox> first (works for plain HTML and most
    // React/Vue controlled inputs via the standard .click() toggle path), then
    // falls back to setter+events, then ARIA role=checkbox.
    function tickPlatformCheckbox(row, want) {
        const cb = row.querySelector('input[type="checkbox"]:not([disabled])');
        if (cb) {
            if (cb.checked === want) return { ok: true, changed: false };
            cb.click();
            if (cb.checked === want) return { ok: true, changed: true };
            // Some custom React inputs ignore programmatic .click(); fall back to
            // the descriptor-setter trick + dispatching change events.
            try {
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'checked').set;
                setter.call(cb, want);
                cb.dispatchEvent(new Event('input', { bubbles: true }));
                cb.dispatchEvent(new Event('change', { bubbles: true }));
                return { ok: true, changed: true };
            } catch (e) {
                return { ok: false, changed: false };
            }
        }
        const aria = row.querySelector('[role="checkbox"]:not([aria-disabled="true"])');
        if (aria) {
            const current = aria.getAttribute('aria-checked') === 'true';
            if (current === want) return { ok: true, changed: false };
            aria.click();
            return { ok: true, changed: true };
        }
        return { ok: false, changed: false };
    }

    function untickOurCheckboxes() {
        tickedNodes.forEach(row => {
            try { tickPlatformCheckbox(row, false); } catch (e) { /* row may be gone */ }
        });
        tickedNodes.clear();
    }

    function clearAllMarks(opts) {
        const untick = !opts || opts.untick !== false;
        highlightedNodes.forEach(el => {
            el.classList.remove('tz-bridge-mark', 'fixed-mark');
        });
        highlightedNodes.clear();
        if (untick) untickOurCheckboxes();
        lastHighlightStats = { found: 0, ticked: 0 };
    }

    function highlightAllOnPage() {
        // Don't auto-untick here — we're about to re-apply, and the inner
        // tickPlatformCheckbox is idempotent on already-correct state.
        clearAllMarks({ untick: false });
        // Drop tickedNodes entries whose rows are no longer in the DOM (e.g.
        // Tranzor swapped them out when the search filter changed). Otherwise
        // the "N ticked" counter inflates and untickOurCheckboxes does
        // pointless work on detached nodes.
        Array.from(tickedNodes).forEach(n => {
            if (!document.contains(n)) tickedNodes.delete(n);
        });
        if (!currentEnvelope || !currentEnvelope.items) {
            lastHighlightStats = { found: 0, ticked: 0 };
            return lastHighlightStats;
        }
        let found = 0, ticked = 0;
        currentEnvelope.items.forEach(it => {
            const row = findRowForItem(it);
            if (!row) return;
            row.classList.add('tz-bridge-mark');
            const pKey = progressKeyOf(it);
            const isFixed = progress[pKey] === 'fixed';
            if (isFixed) row.classList.add('fixed-mark');
            highlightedNodes.add(row);
            found++;
            // Auto-tick only un-fixed rows (no point queueing already-done work
            // for batch retranslate). Track so close can clean up.
            if (tickMode && !isFixed) {
                const r = tickPlatformCheckbox(row, true);
                if (r.ok) {
                    tickedNodes.add(row);
                    if (r.changed) ticked++;
                }
            }
        });
        lastHighlightStats = { found, ticked };
        return lastHighlightStats;
    }

    function refreshHighlights() {
        if (!highlightMode || !isOnEnvelopeTaskPage()) {
            clearAllMarks();
            return lastHighlightStats;
        }
        return highlightAllOnPage();
    }

    // ---- DOM observer to catch lazily-rendered rows ----
    // Tranzor renders rows lazily (per language section / virtual list /
    // pagination), so a single scan at ingest time often finds 0 matches. We
    // watch the body for new content and re-scan; new matches get auto-ticked
    // as they appear, no matter how the user navigates the platform.
    let mutationObserver = null;
    let mutationDebounceTimer = null;
    let lastObserverScanFound = -1;

    function isInOurPanel(node) {
        let n = node;
        while (n) {
            if (n.nodeType === 1) {
                const id = n.id;
                if (id === 'tz-bridge-panel' || id === 'tz-bridge-reopen') return true;
                if (n.classList && (n.classList.contains('tz-bridge-panel') || n.classList.contains('tz-bridge-reopen'))) return true;
            }
            n = n.parentNode;
        }
        return false;
    }

    function startMutationObserver() {
        if (mutationObserver || typeof MutationObserver === 'undefined') return;
        mutationObserver = new MutationObserver((mutations) => {
            // Skip mutations that are entirely within our own sidebar / reopen pill,
            // otherwise our own render() would trigger an infinite re-scan loop.
            const external = mutations.some(m => !isInOurPanel(m.target));
            if (!external) return;
            if (mutationDebounceTimer) clearTimeout(mutationDebounceTimer);
            mutationDebounceTimer = setTimeout(() => {
                if (dismissed || !currentEnvelope) return;
                if (!highlightMode || !isOnEnvelopeTaskPage()) return;
                const before = lastHighlightStats.found;
                refreshHighlights();
                // Only re-render the sidebar (which is expensive) when the
                // numbers actually changed, to avoid layout thrash.
                if (lastHighlightStats.found !== before) {
                    lastObserverScanFound = lastHighlightStats.found;
                    render();
                }
            }, 350);
        });
        mutationObserver.observe(document.body, { childList: true, subtree: true });
    }

    function envelopeLanguages() {
        if (!currentEnvelope || !currentEnvelope.items) return [];
        const counts = {};
        currentEnvelope.items.forEach(it => {
            const lang = it.language || '(unknown)';
            counts[lang] = (counts[lang] || 0) + 1;
        });
        return Object.entries(counts)
            .sort((a, b) => b[1] - a[1])
            .map(([lang, n]) => ({ lang, n }));
    }

    // Group envelope items by unique String Key. Each Tranzor row is a
    // (key, language) pair, so a 22-item envelope spanning 11 languages
    // typically reduces to ~2 unique keys, each driving one filter pass.
    function uniqueStringKeyGroups() {
        if (!currentEnvelope || !currentEnvelope.items) return [];
        const groups = new Map();
        currentEnvelope.items.forEach(it => {
            const k = it.string_key;
            if (!groups.has(k)) groups.set(k, []);
            groups.get(k).push(it);
        });
        return Array.from(groups, ([key, items]) => ({ key, items }));
    }

    // Drive Tranzor's own search box. Tranzor's task page is one big
    // paginated list (~50 rows per page × dozens of pages), so the only way
    // to make 22 scattered (key, lang) entries land on one screen is to
    // filter via the platform's own search.
    function filterTranzorByKey(key, _tabAttempts) {
        // MR Pipeline: if the active language tab isn't represented in this
        // key's group, switch to a language that IS — otherwise the filter
        // would surface zero rows even though the data exists, just on a
        // different tab. Defer the actual filter until the tab re-renders.
        // Cap the click-then-recurse loop at 2 attempts in case Tranzor's
        // active-state class lags behind the click event.
        const attempts = _tabAttempts || 0;
        // Scan Task pages: translation rows + search box only exist on the
        // Strings sub-tab. If the user is on Overview (or we just landed),
        // flip to Strings before driving anything else.
        if (attempts < 2 && ensureScanStringsTab()) {
            setTimeout(() => filterTranzorByKey(key, attempts + 1), 300);
            return true;
        }
        if (currentEnvelope && currentEnvelope.items && attempts < 2) {
            const groupLangs = Array.from(new Set(
                currentEnvelope.items
                    .filter(it => it.string_key === key)
                    .map(it => it.language)
                    .filter(Boolean)
            ));
            const chosen = pickLanguageTab(groupLangs);
            if (chosen && ensureLanguageTab(chosen)) {
                setTimeout(() => filterTranzorByKey(key, attempts + 1), 300);
                return true;
            }
        }
        const input = document.querySelector(CONFIG.SELECTORS.searchInput);
        if (!input) {
            flashHeader("Search box not found — can't auto-filter");
            return false;
        }
        input.focus();
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        try { setter && setter.call(input, key); } catch (e) { input.value = key; }
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true }));
        input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', bubbles: true }));
        currentFilterKey = key;
        // After the search updates, Tranzor often still shows only one
        // language at a time. Click "All Languages" to ensure every language
        // row of this key renders so we can highlight + tick them all.
        setTimeout(() => { ensureAllLanguagesView(); }, 250);
        // Then re-scan after the view re-renders. MutationObserver also fires
        // as rows appear; this just speeds up the visible "ticked" counter.
        setTimeout(() => { refreshHighlights(); render(); }, 750);
        return true;
    }

    function nextUniqueKeyToFilter() {
        const groups = uniqueStringKeyGroups();
        if (!groups.length) return null;
        if (!currentFilterKey) return groups[0].key;
        const idx = groups.findIndex(g => g.key === currentFilterKey);
        if (idx < 0) return groups[0].key;
        return groups[(idx + 1) % groups.length].key;
    }

    function manualRescan() {
        // Re-run the most useful action: filter Tranzor to the current (or
        // first) unique key, then re-highlight after the list re-renders.
        const key = currentFilterKey || (uniqueStringKeyGroups()[0] && uniqueStringKeyGroups()[0].key);
        if (key) filterTranzorByKey(key);
        else { refreshHighlights(); render(); }
    }

    function findItemOnPage(item, _tabAttempts) {
        const key = item && item.string_key;
        if (!key) return;
        const attempts = _tabAttempts || 0;
        // Scan Task pages: rows live under the Strings sub-tab. Flip to
        // Strings first if we're still on Overview (the default).
        if (attempts < 2 && ensureScanStringsTab()) {
            setTimeout(() => findItemOnPage(item, attempts + 1), 300);
            return;
        }
        // On MR Pipeline pages, the per-language tab strip hides rows from
        // every other language. If this item belongs to a different language
        // than the currently active tab, switch tabs first — then re-enter
        // findItemOnPage after the new tab's rows have rendered. Cap retries
        // so a tab whose active state never updates can't loop us forever.
        if (item.language && attempts < 2 && ensureLanguageTab(item.language)) {
            setTimeout(() => findItemOnPage(item, attempts + 1), 300);
            return;
        }
        // Tier A: scroll to the row matching BOTH the key and the language.
        const row = findRowForItem(item);
        if (row) {
            scrollAndFlash(row);
            return;
        }
        // Tier B: re-filter Tranzor's search to this key. Once the matching
        // rows render, MutationObserver picks them up and the user can click
        // Find again to scroll to this specific language row.
        const input = document.querySelector(CONFIG.SELECTORS.searchInput);
        if (input) {
            input.focus();
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            try { setter && setter.call(input, key); } catch (e) { input.value = key; }
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
            input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true }));
            input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', bubbles: true }));
            currentFilterKey = key;
            setTimeout(() => { ensureAllLanguagesView(); }, 200);
            setTimeout(() => { refreshHighlights(); render(); }, 700);
            return;
        }
        // Tier C: clipboard + native page-find as a last resort.
        navigator.clipboard.writeText(key).catch(() => {});
        window.find && window.find(key, /*caseSensitive*/false, /*backwards*/false, /*wrapAround*/true);
        flashHeader('Row not on this page — key copied to clipboard');
    }

    // Back-compat shim — some old call sites used findKeyOnPage(key).
    function findKeyOnPage(key) {
        return findItemOnPage({ string_key: key });
    }

    function flashHeader(msg) {
        const h = document.querySelector('.tz-bridge-header');
        if (!h) return;
        const original = h.style.background;
        h.style.background = '#E67E22';
        const titleEl = h.querySelector('.tz-bridge-instance');
        const prev = titleEl.textContent;
        titleEl.textContent = msg;
        setTimeout(() => {
            h.style.background = original || '';
            titleEl.textContent = prev;
        }, 2200);
    }

    function ingestEnvelope(envelope, fromPaste) {
        if (!envelope || !envelope.items) return;
        currentEnvelope = envelope;
        const newId = envelope.envelope_id || 'paste-' + Date.now();
        // A genuinely new envelope (different id) overrides any prior dismissal:
        // users almost certainly want to see the freshly-sent batch.
        if (dismissed && newId !== dismissedEnvelopeId) {
            restorePanel();
        }
        envelopeId = newId;
        progress = GM_getValue('progress::' + envelopeId, {}) || {};
        const root = document.getElementById('tz-bridge-panel');
        if (root && !dismissed) root.classList.remove('collapsed');
        const tog = document.getElementById('tz-bridge-toggle');
        if (tog) tog.textContent = '»';
        currentFilterKey = null;
        render();
        if (fromPaste) flashHeader('Loaded from clipboard');
        // Tranzor's task page is one big paginated list of (key, language)
        // entries — selected items are scattered across pages. Drive Tranzor's
        // own search box with the first unique String Key so the user sees
        // those rows on one screen, ready for Batch Retranslate. Users with
        // multiple unique keys cycle through them via the "🎯 Filter next" UI.
        if (isOnEnvelopeTaskPage()) {
            const groups = uniqueStringKeyGroups();
            if (groups.length) {
                // On MR Pipeline pages, the language tab strip may not have
                // rendered yet — wait briefly for it so filterTranzorByKey
                // can switch to a tab that matches our envelope's languages
                // (instead of filtering on the default de-DE tab where no
                // selected row will ever appear).
                const onMr = Boolean(envelopeMrCoords());
                const onScan = Boolean(envelopeScanTaskId());
                if (onMr) {
                    waitForLanguageTabs(2500, () => filterTranzorByKey(groups[0].key));
                } else if (onScan) {
                    // Scan Task pages open on Overview — flip to Strings
                    // before searching, otherwise the translation table
                    // isn't even in the DOM. Tab click triggers a re-render
                    // so we wait briefly before driving the search box.
                    setTimeout(() => {
                        ensureScanStringsTab();
                        setTimeout(() => filterTranzorByKey(groups[0].key), 400);
                    }, 250);
                } else {
                    setTimeout(() => filterTranzorByKey(groups[0].key), 250);
                }
            }
        }
    }

    // Poll for the MR Pipeline language tab strip to render, then run `cb`.
    // Falls through after `maxMs` even if no tabs appeared (cb still runs so
    // the rest of the ingest flow isn't blocked on a missing DOM).
    function waitForLanguageTabs(maxMs, cb) {
        const deadline = Date.now() + Math.max(0, maxMs || 0);
        const check = () => {
            if (findLanguageTabs().length > 0 || Date.now() >= deadline) {
                cb();
                return;
            }
            setTimeout(check, 150);
        };
        // First check after a small delay so we don't fight the SPA's
        // initial paint loop.
        setTimeout(check, 150);
    }

    // ---- Polling loop ----
    let lastObservedPath = location.pathname;
    async function tick() {
        try {
            if (document.visibilityState !== 'visible') return;
            // Re-render if SPA navigation moved us between task pages so the
            // task badge and on-page highlights track the current URL.
            if (location.pathname !== lastObservedPath) {
                lastObservedPath = location.pathname;
                if (currentEnvelope) render();
            }
            // Token may arrive at any tick via a freshly opened tab carrying
            // #tzbridge_token=… in the URL hash.
            captureTokenFromHash();
            if (!endpoint) {
                await discoverBridge();
                if (!endpoint) return;
            }
            if (!endpoint.token) {
                // No paired token yet — try envelope-in-hash fallback transport.
                const m = location.hash.match(/#tzbridge=([A-Za-z0-9_\-]+)/);
                if (m) {
                    try {
                        const json = decodeURIComponent(escape(atob(m[1].replace(/-/g, '+').replace(/_/g, '/'))));
                        const env = JSON.parse(json);
                        ingestEnvelope(env, /*fromPaste=*/false);
                        history.replaceState(null, '', location.pathname + location.search);
                    } catch (e) { /* ignore */ }
                }
                return;
            }
            const r = await pullEnvelope();
            if (r && r.updated && r.envelope) {
                lastSeq = r.seq;
                ingestEnvelope(r.envelope, /*fromPaste=*/false);
            } else if (r && r.badToken) {
                // Token rotated (GUI restarted) — clear and wait for a fresh
                // pairing via #tzbridge_token=.
                GM_setValue('bridge_token', '');
                endpoint.token = '';
            }
        } catch (e) { /* swallow; keep polling */ }
    }

    function startPolling() {
        if (pollTimer) return;
        pollTimer = setInterval(tick, POLL_INTERVAL_MS);
        tick();
    }

    // ---- Bootstrap ----
    function init() {
        ensureMounted();
        // Restore last endpoint hint so we don't sweep all 10 ports first.
        const cached = GM_getValue('bridge_endpoint', null);
        const cachedToken = GM_getValue('bridge_token', '');
        if (cached) {
            endpoint = {
                port: cached.port,
                instance_id: '',
                token: cachedToken || '',
                discoveredAt: 0,
            };
        }
        // Pick up token from URL hash (#tzbridge_token=…) on initial load.
        captureTokenFromHash();
        // Or pick up an envelope-in-hash payload as last-resort transport.
        const m = location.hash.match(/#tzbridge=([A-Za-z0-9_\-]+)/);
        if (m) {
            try {
                const json = decodeURIComponent(escape(atob(m[1].replace(/-/g, '+').replace(/_/g, '/'))));
                const env = JSON.parse(json);
                ingestEnvelope(env, false);
                history.replaceState(null, '', location.pathname + location.search);
            } catch (e) { /* ignore */ }
        }
        startPolling();
        startMutationObserver();
        // Ctrl+Shift+V → focus paste textarea for clipboard fallback ingestion.
        document.addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.shiftKey && (e.key === 'V' || e.key === 'v')) {
                const ta = document.getElementById('tz-bridge-paste');
                if (ta) {
                    const root = document.getElementById('tz-bridge-panel');
                    if (root) root.classList.remove('collapsed');
                    ta.focus();
                }
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
