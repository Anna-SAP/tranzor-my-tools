// ==UserScript==
// @name         Tranzor Full Email Export
// @namespace    tranzor-my-tools
// @version      1.0.0
// @description  Export the "Full Email" bilingual viewer as a clean side-by-side PNG, or open a clean standalone tab for full-page screenshot / Save-as-PDF — without the scroll-stitch overlap.
// @match        http://tranzor-platform.int.rclabenv.com/*
// @match        https://tranzor-platform.int.rclabenv.com/*
// @require      https://cdn.jsdelivr.net/npm/html-to-image@1.11.11/dist/html-to-image.js
// @grant        GM_addStyle
// @run-at       document-idle
// @updateURL    https://raw.githubusercontent.com/Anna-SAP/tranzor-my-tools/master/userscript/tranzor_full_email_export.user.js
// @downloadURL  https://raw.githubusercontent.com/Anna-SAP/tranzor-my-tools/master/userscript/tranzor_full_email_export.user.js
// ==/UserScript==
// Maintainer note: any change to this file MUST bump @version above
// (semver). Tampermonkey/Violentmonkey only fetch an update when the
// remote version is strictly higher than the installed one.
//
// Why this exists: the Full Email viewer nests several independent scroll
// containers (backdrop, modal body, and each pane's own overflow-auto), so a
// generic scrolling-screenshot extension scrolls the page while the content
// stays put inside the inner panes — its frames stitch with the wrong offset
// and the text ghosts/overlaps. Both actions here avoid scroll+stitch
// entirely: PNG rasterizes the full-height node in one pass; Clean view lays
// the content out as a normal standalone page you can capture or print.

(function () {
    'use strict';

    // ---- Tranzor DOM anchors (single point of attachment; update if UI changes) ----
    //   - The viewer is a Modal (role="dialog") whose header <h3> reads "Full Email".
    //   - The target-language tag is the <span> right after that <h3>.
    //   - The two panes are the only two <section> elements; each section's
    //     first child is its title ("Source HTML" / "Translation" / ...) and
    //     its last child is the content (a <div>/<pre> for text modes, or an
    //     <iframe> for Rendered mode).
    const MODAL_TITLE = 'Full Email';

    // ---- Toast ----
    GM_addStyle(`
    .tz-fx-toast {
        position: fixed; left: 50%; bottom: 32px; transform: translateX(-50%);
        z-index: 2147483647; background: #0f172a; color: #e2e8f0;
        padding: 9px 16px; border-radius: 8px; font-size: 13px; font-weight: 600;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        box-shadow: 0 6px 24px rgba(0,0,0,.35); opacity: 0; pointer-events: none;
        transition: opacity .18s ease;
    }
    .tz-fx-toast.tz-fx-show { opacity: 1; }
    .tz-fx-bar { display: flex; gap: 8px; margin: 0 0 10px; flex-wrap: wrap; }
    .tz-fx-btn {
        display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
        border-radius: 8px; padding: 6px 12px; font-size: 13px; font-weight: 600;
        border: 1px solid transparent; transition: background .15s, border-color .15s;
    }
    .tz-fx-btn.png { background: #14b8a6; color: #fff; }
    .tz-fx-btn.png:hover { background: #0d9488; }
    .tz-fx-btn.clean { background: #fff; color: #334155; border-color: #cbd5e1; }
    .tz-fx-btn.clean:hover { background: #f1f5f9; border-color: #94a3b8; }
    `);

    let toastEl = null, toastTimer = 0;
    function toast(msg) {
        if (!toastEl) {
            toastEl = document.createElement('div');
            toastEl.className = 'tz-fx-toast';
            document.body.appendChild(toastEl);
        }
        toastEl.textContent = msg;
        requestAnimationFrame(() => toastEl.classList.add('tz-fx-show'));
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => toastEl.classList.remove('tz-fx-show'), 2200);
    }

    // ---- Discovery ----
    function discover() {
        for (const dlg of document.querySelectorAll('[role="dialog"]')) {
            const h = dlg.querySelector('h3');
            if (h && h.textContent.trim() === MODAL_TITLE) {
                return { modal: dlg, h3: h, sections: [...dlg.querySelectorAll('section')] };
            }
        }
        return { modal: null, h3: null, sections: [] };
    }
    function langOf(h3) {
        const s = h3 && h3.nextElementSibling;
        return (s && s.textContent.trim()) || '';
    }
    function stamp() {
        const task = (location.pathname.match(/tasks\/([^/?#]+)/) || [])[1]
            || (location.search.match(/mr_id=([^&]+)/) || [])[1]
            || 'full-email';
        const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
        return { task, ts };
    }

    // ---- Action: Export PNG (full-height bilingual grid, one pass) ----
    async function exportPng() {
        const { sections, h3 } = discover();
        if (sections.length < 2) return toast('未找到 Full Email 内容');
        const panes = [sections[0].lastElementChild, sections[1].lastElementChild];
        if (panes.some(p => p && p.tagName === 'IFRAME')) {
            return toast('Rendered 模式请用「Clean view」或 PDF;PNG 适用于 Plain text / Raw HTML');
        }
        if (typeof htmlToImage === 'undefined' || !htmlToImage.toPng) {
            return toast('PNG 库未加载(可能被网络拦截),请改用「Clean view」');
        }
        const grid = sections[0].parentElement;        // the 2-column wrapper
        const saved = panes.map(p => p.getAttribute('style') || '');
        panes.forEach(p => {                            // un-cap height so the node is full-length
            p.style.maxHeight = 'none';
            p.style.height = 'auto';
            p.style.overflow = 'visible';
        });
        toast('正在生成 PNG…');
        try {
            const dataUrl = await htmlToImage.toPng(grid, {
                pixelRatio: 2,
                backgroundColor: '#ffffff',
                style: { margin: '0' },
            });
            const { task, ts } = stamp();
            const a = document.createElement('a');
            a.href = dataUrl;
            a.download = `full-email_${task}_${langOf(h3) || 'xx'}_${ts}.png`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            toast('PNG 已导出 ✓');
        } catch (e) {
            console.error('[tz-full-email-export] toPng failed', e);
            toast('PNG 生成失败(内容可能过长),请改用「Clean view」→ 另存 PDF');
        } finally {
            panes.forEach((p, i) => {
                if (saved[i]) p.setAttribute('style', saved[i]);
                else p.removeAttribute('style');
            });
        }
    }

    // ---- Action: Clean view (standalone tab → screenshot extension / Save-as-PDF) ----
    function cleanView() {
        const { sections, h3 } = discover();
        if (sections.length < 2) return toast('未找到 Full Email 内容');
        const lang = langOf(h3);
        const cols = sections.map(sec => {
            const pane = sec.lastElementChild;
            const title = (sec.firstElementChild && sec.firstElementChild.textContent.trim()) || '';
            if (pane && pane.tagName === 'IFRAME') {
                return { title, kind: 'iframe', srcdoc: pane.getAttribute('srcdoc') || '' };
            }
            return { title, kind: 'text', text: pane ? pane.textContent : '' };
        });
        openCleanTab({ lang, cols });
        toast('已打开 Clean view(新标签页)');
    }

    function openCleanTab(data) {
        // Embed payload safely inside an inline <script>: neutralise "<" so no
        // "</script>" in the email content can break out of the block.
        const payload = JSON.stringify(JSON.stringify(data).replace(/</g, '\\u003c'));
        const html = [
            '<!doctype html><html lang="en"><head><meta charset="utf-8">',
            '<title>Full Email · bilingual ' + (data.lang || '') + '</title><style>',
            ':root{color-scheme:light}*{box-sizing:border-box}',
            'body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;background:#f1f5f9;color:#0f172a}',
            '.bar{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:12px;padding:10px 16px;background:#0f172a;color:#e2e8f0}',
            '.bar h1{font-size:14px;margin:0;font-weight:600}',
            '.bar .lang{font-size:11px;background:#1e293b;padding:2px 8px;border-radius:999px}',
            '.bar .hint{font-size:11px;opacity:.7}',
            '.bar button{margin-left:auto;background:#14b8a6;color:#fff;border:0;border-radius:6px;padding:7px 14px;font-size:13px;font-weight:600;cursor:pointer}',
            '.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:16px;align-items:start}',
            '.col{background:#fff;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden}',
            '.col>h2{margin:0;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:#64748b;padding:8px 12px;border-bottom:1px solid #e2e8f0;background:#f8fafc}',
            'pre{margin:0;padding:12px;white-space:pre-wrap;word-break:break-word;font-family:ui-monospace,"Cascadia Code",Consolas,monospace;font-size:12px;line-height:1.55}',
            'iframe{width:100%;border:0;display:block}',
            '@media print{body{background:#fff}.no-print{display:none!important}.grid{padding:0;gap:0}.col{border:0;border-right:1px solid #ccc;border-radius:0}}',
            '</style></head><body>',
            '<div class="bar no-print"><h1>Full Email · bilingual</h1>',
            data.lang ? '<span class="lang">' + data.lang + '</span>' : '',
            '<span class="hint">整页截图扩展可一次拼接;或点右侧按钮另存 PDF</span>',
            '<button onclick="window.print()">🖨 Print / Save as PDF</button></div>',
            '<div class="grid" id="grid"></div>',
            '<script>(function(){',
            'var DATA=JSON.parse(' + payload + ');',
            'var g=document.getElementById("grid");',
            'DATA.cols.forEach(function(c){',
            'var col=document.createElement("div");col.className="col";',
            'var h=document.createElement("h2");h.textContent=c.title||"";col.appendChild(h);',
            'if(c.kind==="iframe"){',
            'var f=document.createElement("iframe");f.setAttribute("sandbox","allow-same-origin");', // render only, scripts stay disabled
            'f.srcdoc=c.srcdoc||"";',
            'f.addEventListener("load",function(){try{f.style.height=(f.contentDocument.documentElement.scrollHeight+24)+"px";}catch(e){}});',
            'col.appendChild(f);',
            '}else{var pre=document.createElement("pre");pre.textContent=c.text||"";col.appendChild(pre);}',
            'g.appendChild(col);});',
            'document.title="Full Email "+(DATA.lang||"")+" — bilingual";',
            '})();<\/script></body></html>',
        ].join('');

        const url = URL.createObjectURL(new Blob([html], { type: 'text/html;charset=utf-8' }));
        const w = window.open(url, '_blank');
        if (!w) {
            URL.revokeObjectURL(url);
            return toast('弹窗被拦截,请允许本站弹窗后重试');
        }
        setTimeout(() => URL.revokeObjectURL(url), 60000);
    }

    // ---- Toolbar injection ----
    function injectToolbar() {
        const { sections } = discover();
        if (sections.length < 2) return;
        const grid = sections[0].parentElement;
        if (!grid || !grid.parentElement) return;
        if (grid.previousElementSibling && grid.previousElementSibling.dataset.tzFx === '1') return;

        const bar = document.createElement('div');
        bar.className = 'tz-fx-bar';
        bar.dataset.tzFx = '1';

        const png = document.createElement('button');
        png.type = 'button';
        png.className = 'tz-fx-btn png';
        png.innerHTML = '🖼 Export PNG';
        png.title = '把左右双语内容整体导出为一张 PNG(适用于 Plain text / Raw HTML)';
        png.addEventListener('click', exportPng);

        const clean = document.createElement('button');
        clean.type = 'button';
        clean.className = 'tz-fx-btn clean';
        clean.innerHTML = '🗔 Clean view';
        clean.title = '在新标签页打开干净的双语页面,可用整页截图扩展或 Ctrl+P 另存 PDF';
        clean.addEventListener('click', cleanView);

        bar.appendChild(png);
        bar.appendChild(clean);
        grid.parentElement.insertBefore(bar, grid);
    }

    // ---- Boot ----
    let raf = 0;
    new MutationObserver(() => {
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(injectToolbar);
    }).observe(document.body, { childList: true, subtree: true });
    injectToolbar();
})();
