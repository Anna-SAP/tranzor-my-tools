// ==UserScript==
// @name         Tranzor Bridge
// @namespace    tranzor-my-tools
// @version      0.1.0
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
// ==/UserScript==

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

    // URL pattern of Tranzor's per-task list view. The Send-to-Tranzor button
    // already navigates here; this regex lets the userscript know whether the
    // current page corresponds to the envelope's task (→ enable highlighting).
    const TASK_PATH_RE = /\/static\/legacy\/tasks\/([^/?#]+)/;

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
    .tz-bridge-toggle { font-size: 14px; line-height: 1; }
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

    async function pullEnvelope() {
        if (!endpoint || !endpoint.token) return null;
        const url = `http://127.0.0.1:${endpoint.port}/pull?since=${lastSeq}`;
        return new Promise((resolve) => {
            GM_xmlhttpRequest({
                method: 'GET',
                url: url,
                headers: {
                    'X-Bridge-Token': endpoint.token,
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
                <span class="tz-bridge-toggle" id="tz-bridge-toggle">«</span>
            </div>
            <div class="tz-bridge-body" id="tz-bridge-body">
                <div class="tz-bridge-empty">Waiting for selections from TranzorExporter…</div>
            </div>
        `;
        document.body.appendChild(root);
        document.getElementById('tz-bridge-header').addEventListener('click', () => {
            root.classList.toggle('collapsed');
            const tog = document.getElementById('tz-bridge-toggle');
            tog.textContent = root.classList.contains('collapsed') ? '«' : '»';
        });
        mounted = true;
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

        const fixedCount = currentEnvelope.items.filter(i => progress[i.string_key] === 'fixed').length;
        const total = currentEnvelope.items.length;
        const ctx = currentEnvelope.context || {};
        const ctxBits = [];
        if (ctx.task_id) ctxBits.push('Task ' + escapeHtml(ctx.task_id));
        if (ctx.language) ctxBits.push(escapeHtml(ctx.language));
        const ctxLabel = ctxBits.length ? ctxBits.join(' · ') : 'mixed';

        // Task-page match indicator + jump link when on wrong page.
        const onTask = getCurrentTaskId();
        const envTask = envelopeTaskId();
        let taskBadge = '';
        if (envTask) {
            if (onTask && String(onTask) === String(envTask)) {
                taskBadge = `<span class="tz-bridge-task-badge match" title="You're on the right task page">on task ${escapeHtml(envTask)}</span>`;
            } else {
                taskBadge = `<a class="tz-bridge-task-badge mismatch" href="/static/legacy/tasks/${encodeURIComponent(envTask)}" title="Open the task page these items belong to">go to task ${escapeHtml(envTask)} →</a>`;
            }
        }

        const itemsHtml = currentEnvelope.items.map((it, i) => {
            const state = progress[it.string_key] || '';
            const cls = state ? ' ' + state : '';
            return `
                <div class="tz-bridge-item${cls}" data-key="${escapeHtml(it.string_key)}">
                    <div class="tz-bridge-row">
                        <span class="tz-bridge-key" title="Click to copy">${escapeHtml(it.string_key)}</span>
                    </div>
                    <div class="tz-bridge-meta">${escapeHtml(it.language || '')} · ${escapeHtml(it.translation_type || '')}</div>
                    <div class="tz-bridge-row">
                        <button class="tz-bridge-btn primary" data-action="find">🔍 Find</button>
                        <button class="tz-bridge-btn fix${state === 'fixed' ? ' active' : ''}" data-action="fix">✓ Fixed</button>
                        <button class="tz-bridge-btn skip${state === 'skipped' ? ' active' : ''}" data-action="skip">⤵ Skip</button>
                    </div>
                </div>
            `;
        }).join('');

        body.innerHTML = `
            <div class="tz-bridge-status">
                ${ctxLabel} · <span class="tz-bridge-progress">${fixedCount}/${total}</span> fixed${taskBadge}
            </div>
            <div class="tz-bridge-toolbar">
                <button class="tz-bridge-btn ${highlightMode ? 'active primary' : ''}" id="tz-bridge-hl-toggle" title="Mark every selected row on the Tranzor page with a green stripe">
                    ${highlightMode ? '👀 Highlighting on page' : '⋯ Highlight on page'}
                </button>
            </div>
            ${itemsHtml}
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
        body.querySelectorAll('.tz-bridge-item').forEach(itemEl => {
            const key = itemEl.dataset.key;
            itemEl.querySelector('.tz-bridge-key').addEventListener('click', () => {
                navigator.clipboard.writeText(key).catch(() => {});
            });
            itemEl.querySelectorAll('.tz-bridge-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const action = btn.dataset.action;
                    if (action === 'find') findKeyOnPage(key);
                    else if (action === 'fix') toggleProgress(key, 'fixed');
                    else if (action === 'skip') toggleProgress(key, 'skipped');
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

    function envelopeTaskId() {
        if (!currentEnvelope) return null;
        const ctx = currentEnvelope.context || {};
        if (ctx.task_id) return String(ctx.task_id);
        const first = currentEnvelope.items && currentEnvelope.items[0];
        return first && first.task_id ? String(first.task_id) : null;
    }

    function isOnEnvelopeTaskPage() {
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
        // Walk up to the smallest reasonable row container.
        let el = node.parentElement;
        while (el && el !== document.body) {
            const tag = el.tagName;
            if (tag === 'TR' || tag === 'LI') return el;
            if (el.getAttribute && el.getAttribute('role') === 'row') return el;
            // Heuristic: a div that has 3+ children laid out as a row is probably one.
            if (tag === 'DIV' && el.children.length >= 3) {
                const style = window.getComputedStyle(el);
                if (style.display.startsWith('flex') || style.display.startsWith('grid')) return el;
            }
            el = el.parentElement;
        }
        return node.parentElement;
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

    function clearAllMarks() {
        highlightedNodes.forEach(el => {
            el.classList.remove('tz-bridge-mark', 'fixed-mark');
        });
        highlightedNodes.clear();
    }

    function highlightAllOnPage() {
        clearAllMarks();
        if (!currentEnvelope || !currentEnvelope.items) return 0;
        let found = 0;
        currentEnvelope.items.forEach(it => {
            const row = findRowContainingText(it.string_key);
            if (!row) return;
            row.classList.add('tz-bridge-mark');
            if (progress[it.string_key] === 'fixed') row.classList.add('fixed-mark');
            highlightedNodes.add(row);
            found++;
        });
        return found;
    }

    function refreshHighlights() {
        if (!highlightMode || !isOnEnvelopeTaskPage()) {
            clearAllMarks();
            return 0;
        }
        return highlightAllOnPage();
    }

    function findKeyOnPage(key) {
        // Tier A: scroll to the matching row directly in Tranzor's DOM and
        // flash it. Works without knowing any platform-specific selector.
        const row = findRowContainingText(key);
        if (row) {
            scrollAndFlash(row);
            return;
        }
        // Tier B: fill Tranzor's search input as a soft fallback.
        const input = document.querySelector(CONFIG.SELECTORS.searchInput);
        if (input) {
            input.focus();
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter && setter.call(input, key);
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
            input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true }));
            input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', bubbles: true }));
            return;
        }
        // Tier C: clipboard + native page-find as a last resort.
        navigator.clipboard.writeText(key).catch(() => {});
        window.find && window.find(key, /*caseSensitive*/false, /*backwards*/false, /*wrapAround*/true);
        flashHeader('Row not on this page — key copied to clipboard');
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
        envelopeId = envelope.envelope_id || 'paste-' + Date.now();
        progress = GM_getValue('progress::' + envelopeId, {}) || {};
        const root = document.getElementById('tz-bridge-panel');
        if (root) root.classList.remove('collapsed');
        const tog = document.getElementById('tz-bridge-toggle');
        if (tog) tog.textContent = '»';
        render();
        if (fromPaste) flashHeader('Loaded from clipboard');
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
