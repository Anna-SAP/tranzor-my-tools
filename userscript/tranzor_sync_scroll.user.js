// ==UserScript==
// @name         Tranzor Sync Scroll
// @namespace    tranzor-my-tools
// @version      1.0.0
// @description  Synchronize scrolling between the Source and Translation panes of the "Full Email" viewer (File Translation today; auto-covers the same modal on MR / Scan surfaces if/when it ships there).
// @match        http://tranzor-platform.int.rclabenv.com/*
// @match        https://tranzor-platform.int.rclabenv.com/*
// @grant        GM_addStyle
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @run-at       document-idle
// @updateURL    https://raw.githubusercontent.com/Anna-SAP/tranzor-my-tools/master/userscript/tranzor_sync_scroll.user.js
// @downloadURL  https://raw.githubusercontent.com/Anna-SAP/tranzor-my-tools/master/userscript/tranzor_sync_scroll.user.js
// ==/UserScript==
// Maintainer note: any change to this file MUST bump @version above
// (semver). Tampermonkey/Violentmonkey only fetch an update when the
// remote version is strictly higher than the installed one.

(function () {
    'use strict';

    // ---- Configuration ----
    // Sync the "Rendered" mode too (the two sandboxed email-preview iframes).
    // To read/write an iframe's internal scroll position the parent must be
    // same-origin with it, so we relax sandbox="" to sandbox="allow-same-origin"
    // on the Full Email preview iframes ONLY. We deliberately DO NOT add
    // allow-scripts, so email HTML still cannot execute any script — the XSS
    // surface stays closed. Set to false to leave the iframes fully sandboxed
    // (Plain text / Raw HTML still sync; Rendered will not).
    const ENABLE_RENDERED_SYNC = true;

    // Tranzor DOM anchors. Update if Tranzor's UI changes — this is the single
    // point of attachment to the upstream HTML.
    //   - The viewer is a Modal (role="dialog") whose header <h3> reads
    //     "Full Email".
    //   - Inside it the two panes are the only two <section> elements; each
    //     section's last element child is the scroll container: a <div> for
    //     Plain text, a <pre> for Raw HTML, or an <iframe> for Rendered.
    const MODAL_TITLE = 'Full Email';

    // ---- State ----
    const LINKED = '__tzSsLinked';    // marks a scroll element already wired
    const PATCHED = '__tzSsPatched';  // marks an iframe whose sandbox we relaxed
    let enabled = GM_getValue('enabled', true);

    // ---- Toast styling ----
    GM_addStyle(`
    .tz-ss-toast {
        position: fixed; left: 50%; bottom: 32px; transform: translateX(-50%);
        z-index: 2147483647; background: #0f172a; color: #e2e8f0;
        padding: 9px 16px; border-radius: 8px; font-size: 13px; font-weight: 600;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        box-shadow: 0 6px 24px rgba(0,0,0,.35); opacity: 0; pointer-events: none;
        transition: opacity .18s ease;
    }
    .tz-ss-toast.tz-ss-show { opacity: 1; }
    `);

    let toastEl = null, toastTimer = 0;
    function toast(msg) {
        if (!toastEl) {
            toastEl = document.createElement('div');
            toastEl.className = 'tz-ss-toast';
            document.body.appendChild(toastEl);
        }
        toastEl.textContent = msg;
        requestAnimationFrame(() => toastEl.classList.add('tz-ss-show'));
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => toastEl.classList.remove('tz-ss-show'), 1500);
    }

    // ---- Modal / pane discovery ----
    function findModal() {
        for (const dlg of document.querySelectorAll('[role="dialog"]')) {
            const h = dlg.querySelector('h3');
            if (h && h.textContent.trim() === MODAL_TITLE) return dlg;
        }
        return null;
    }

    // The real scrollable object for a pane: the element itself, or — for the
    // Rendered iframe — its inner document's scrolling element (only readable
    // once the sandbox has been relaxed).
    function scrollerOf(pane) {
        if (!pane) return null;
        if (pane.tagName === 'IFRAME') {
            try {
                const d = pane.contentDocument || pane.contentWindow.document;
                return d.scrollingElement || d.documentElement;
            } catch (e) {
                return null; // still blocked by sandbox
            }
        }
        return pane;
    }
    const eventTargetOf = (pane) => (pane.tagName === 'IFRAME' ? pane.contentWindow : pane);

    // ---- Sync wiring ----
    function linkPair(left, right) {
        let lock = false;
        const sync = (from, to) => {
            if (!enabled || lock) return;
            const a = scrollerOf(from), b = scrollerOf(to);
            if (!a || !b) return;
            const aMax = a.scrollHeight - a.clientHeight;
            const bMax = b.scrollHeight - b.clientHeight;
            const ratio = aMax > 0 ? a.scrollTop / aMax : 0;
            lock = true;
            b.scrollTop = ratio * bMax;          // proportional → tolerates length expansion
            requestAnimationFrame(() => { lock = false; });
        };
        try {
            eventTargetOf(left).addEventListener('scroll', () => sync(left, right), { passive: true });
            eventTargetOf(right).addEventListener('scroll', () => sync(right, left), { passive: true });
            left[LINKED] = right[LINKED] = true;
            return true;
        } catch (e) {
            return false; // cross-origin iframe; cannot attach
        }
    }

    // Relax a Rendered-mode iframe's sandbox so its scroll becomes readable,
    // then reload it once and wire up on load. Returns true if a reload was
    // started (caller should wait for `onReady`), false if nothing to do.
    function patchIframe(iframe, onReady) {
        if (iframe[PATCHED] || iframe.getAttribute('sandbox') !== '') return false;
        iframe[PATCHED] = true;
        iframe.setAttribute('sandbox', 'allow-same-origin'); // NOTE: never add allow-scripts
        iframe.addEventListener('load', onReady, { once: true });
        const doc = iframe.getAttribute('srcdoc');
        if (doc != null) iframe.srcdoc = doc; // reassign to apply the new sandbox (triggers reload)
        return true;
    }

    function setup() {
        if (!enabled) return;
        const modal = findModal();
        if (!modal) return;
        const sections = modal.querySelectorAll('section'); // the two panes
        if (sections.length < 2) return;
        const left = sections[0].lastElementChild;
        const right = sections[1].lastElementChild;
        if (!left || !right || (left[LINKED] && right[LINKED])) return;

        const hasIframe = left.tagName === 'IFRAME' || right.tagName === 'IFRAME';
        if (!hasIframe) { linkPair(left, right); return; } // Plain text / Raw HTML

        if (!ENABLE_RENDERED_SYNC) return;
        // Rendered mode: relax each iframe's sandbox, then link once BOTH inner
        // documents are same-origin accessible.
        const wire = () => {
            if (left[LINKED] && right[LINKED]) return;
            if (scrollerOf(left) && scrollerOf(right)) linkPair(left, right);
        };
        [left, right].forEach((f) => { if (f.tagName === 'IFRAME') patchIframe(f, wire); });
        wire(); // covers the re-entry case where both are already accessible
    }

    // ---- Enable / disable toggle ----
    function toggle() {
        enabled = !enabled;
        GM_setValue('enabled', enabled);
        toast(enabled ? '↕  Full Email 同步滚动:已开启' : '↕  Full Email 同步滚动:已关闭');
        if (enabled) setup();
    }

    // Alt+S — ignored while typing in an input / textarea / contenteditable.
    document.addEventListener('keydown', (e) => {
        if (!e.altKey || e.ctrlKey || e.metaKey) return;
        if (e.key !== 's' && e.key !== 'S') return;
        const t = e.target;
        if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
        e.preventDefault();
        toggle();
    }, true);

    try {
        GM_registerMenuCommand('Toggle Full Email sync scroll (Alt+S)', toggle);
    } catch (e) { /* menu command unsupported — Alt+S still works */ }

    // ---- Boot ----
    // The modal mounts/unmounts dynamically and swaps its inner content when the
    // view mode changes, so re-run discovery on every relevant DOM mutation.
    let raf = 0;
    new MutationObserver(() => {
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(setup);
    }).observe(document.body, { childList: true, subtree: true });
    setup();
})();
