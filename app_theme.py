"""Light / Dark theme engine for the Tranzor Exporter Tk GUI.

The GUI hard-codes its dark palette as ~150 hex literals spread over twenty
modules (``bg="#1a1a2e"``, ``fg="#fff"``, ``tag_configure(..., "#3a1f24")``
…).  Threading a palette object through every widget would be a rewrite, so
this module instead hooks the funnels every colour passes through on its way
to Tcl:

* ``tkinter.Misc._options``        — every tk widget creation, ``configure()``,
                                     and Text / Canvas / Listbox tag or item
                                     configure calls.
* ``tkinter.ttk._format_optdict``  — ``ttk.Style.configure`` and
                                     ``Treeview.tag_configure``.
* ``tkinter.ttk._format_mapdict``  — ``ttk.Style.map``.

While the *light* theme is active, colour-valued options are translated from
the dark palette to a light one.  Translation is role-aware: a background is
lightened, a foreground is darkened, saturated accent colours (the coral
"Run" button, the green "Success" button, status pills …) are kept so the
brand look survives, and a foreground paired with such an accent background
is left alone too (white text stays white on a coral button).

Switching at runtime (:func:`apply_to_tree`) walks the live widget tree and
re-applies the translation, so widgets created before the switch follow.
Every forward translation is remembered so the walk back to *dark* restores
the exact original literals.

The whole module is display-free except :func:`apply_to_tree`; the colour
maths and the option remapping are pure and unit-tested.
"""
from __future__ import annotations

import colorsys
import threading
from typing import Callable

DARK = "dark"
LIGHT = "light"
_MODES = (DARK, LIGHT)
CONFIG_KEY = "ui_theme"

# ── Option-name → colour role ───────────────────────────────────────────
_BG_OPTS = frozenset({
    "bg", "background", "activebackground", "selectbackground",
    "highlightbackground", "highlightcolor", "troughcolor",
    "readonlybackground", "buttonbackground", "selectcolor",
    "fieldbackground", "bordercolor", "lightcolor", "darkcolor",
    "indicatorbackground",
})
_FG_OPTS = frozenset({
    "fg", "foreground", "activeforeground", "selectforeground",
    "disabledforeground", "insertbackground", "arrowcolor",
    "indicatorforeground", "indicatorcolor", "placeholderforeground",
})
# Canvas item options whose role depends on the item type; see the walker.
_ANY_OPTS = frozenset({"fill", "outline", "activefill", "activeoutline",
                       "disabledfill", "disabledoutline"})
_ALL_COLOR_OPTS = _BG_OPTS | _FG_OPTS | _ANY_OPTS

# Which background option a foreground option is visually paired with.
_FG_PAIR = {
    "fg": ("bg", "background"),
    "foreground": ("background", "bg"),
    "insertbackground": ("bg", "background"),
    "disabledforeground": ("bg", "background"),
    "activeforeground": ("activebackground", "bg", "background"),
    "selectforeground": ("selectbackground", "bg", "background"),
    "arrowcolor": ("background",),
    "indicatorforeground": ("indicatorbackground", "background"),
    "indicatorcolor": ("background",),
    "placeholderforeground": ("fieldbackground", "background"),
}

# ── Explicit overrides for the core palette (dark literal → light) ──────
# The algorithm below handles any colour, but the handful of colours that
# define the page get hand-picked values so the light theme reads as a
# deliberate design (white cards on a cool grey page) rather than as an
# inverted photograph.  Keys are lower-case 6-digit hex.
_BG_OVERRIDES = {
    "#1a1a2e": "#eef0f5",   # ExportApp.BG          → page
    "#16213e": "#ffffff",   # ExportApp.BG_CARD     → cards
    "#1f2a48": "#f4f6fb",   # sub-panels inside cards
    "#0a0a1a": "#f3f5f9",   # Entry / Text / Listbox fields
    "#11111a": "#f3f5f9",
    "#0d1a30": "#fdfdff",   # Treeview rows
    "#0f3460": "#dde5f2",   # ACCENT: secondary buttons, tree headings
    "#1a3a6a": "#cbd7ea",   # secondary hover
    "#2a3a5e": "#d5deee",
    "#1f3a6a": "#cbd7ea",
    "#243a63": "#dbe6f7",
    "#2a2a4a": "#d6dae4",   # BORDER
    "#334155": "#d6dae4",
    "#1e2d50": "#eaeff8",   # SUMMARY_HIGHLIGHT
    "#1e2a44": "#f8f9fc",   # tooltip body
    "#1f1f2e": "#eef0f5",
    "#0f172a": "#ffffff",   # Advanced Filters card
    "#1e293b": "#f3f5f9",
    "#1f2540": "#e9ecf4",
    "#222222": "#e5e7eb",
    "#555555": "#c9cdd6",   # disabled buttons
}
_FG_OVERRIDES = {
    "#ffffff": "#1f2937",   # primary text
    "#e0e0e0": "#1f2937",   # ExportApp.FG
    "#e4e7ef": "#1f2937",
    "#e2e8f0": "#1f2937",
    "#e5e7eb": "#1f2937",
    "#cdd6f4": "#1f2937",
    "#cccccc": "#334155",   # secondary text / button labels
    "#cbd5e1": "#334155",
    "#d1d5db": "#4b5563",
    "#9aa0b0": "#5b6478",   # muted
    "#aaaaaa": "#5b6478",
    "#a0a3b8": "#5b6478",
    "#9aa0bf": "#5b6478",
    "#94a3b8": "#64748b",
    "#888888": "#6b7280",
    "#8888a0": "#6b7280",
    "#7a7a8a": "#7b8494",
    "#7a7d99": "#7b8494",
    "#7a8199": "#7b8494",
    "#999999": "#7b8494",
    "#666666": "#8b93a3",
    "#fbbf24": "#b45309",   # amber status / busy text
    "#fcd34d": "#a16207",   # hint text
    "#fde68a": "#92400e",
    "#f1c40f": "#a16207",
    "#4ade80": "#15803d",   # green status
    "#86efac": "#15803d",
    "#34d399": "#047857",
    "#7ee787": "#15803d",
    "#2ecc71": "#1e9e57",
    "#fca5a5": "#b91c1c",   # red status
    "#f87171": "#dc2626",
    "#ff6b6b": "#dc2626",
    "#ff8a8a": "#dc2626",
    "#e94560": "#cf3552",   # coral used as text
    "#7dd3fc": "#0369a1",   # sky status
    "#38bdf8": "#0369a1",
    "#e7ecff": "#1e3a8a",
    "#c7d2fe": "#3730a3",
}

_lock = threading.RLock()
_mode = DARK
_installed = False
_suspend_depth = 0
# (role, light) → dark literal, filled by every forward translation so the
# walk back to dark can restore exact originals.
_reverse: dict[tuple[str, str], str] = {}
_reverse_any: dict[str, str] = {}
# Style names ever configured / mapped through ttk.Style, for the walker.
_styles_seen: set[str] = set()
for _dark, _light in _BG_OVERRIDES.items():
    _reverse.setdefault(("bg", _light), _dark)
    _reverse_any.setdefault(_light, _dark)
for _dark, _light in _FG_OVERRIDES.items():
    _reverse.setdefault(("fg", _light), _dark)
    _reverse_any.setdefault(_light, _dark)
del _dark, _light


# ── Colour maths ────────────────────────────────────────────────────────
def parse_hex(color: str) -> tuple[float, float, float] | None:
    """``"#rgb"`` / ``"#rrggbb"`` → (r, g, b) floats in 0..1, else None."""
    if not isinstance(color, str) or not color.startswith("#"):
        return None
    hexpart = color[1:]
    if len(hexpart) == 3:
        hexpart = "".join(ch * 2 for ch in hexpart)
    if len(hexpart) != 6:
        return None
    try:
        r, g, b = (int(hexpart[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None
    return r / 255.0, g / 255.0, b / 255.0


def normalize_hex(color: str) -> str | None:
    rgb = parse_hex(color)
    if rgb is None:
        return None
    return fmt_hex(*rgb)


def fmt_hex(r: float, g: float, b: float) -> str:
    clamp = lambda v: max(0, min(255, int(round(v * 255))))  # noqa: E731
    return f"#{clamp(r):02x}{clamp(g):02x}{clamp(b):02x}"


def hls(color: str) -> tuple[float, float, float] | None:
    rgb = parse_hex(color)
    if rgb is None:
        return None
    return colorsys.rgb_to_hls(*rgb)


def luminance(color: str) -> float | None:
    """HLS lightness 0..1 (cheap proxy, good enough for role decisions)."""
    v = hls(color)
    return None if v is None else v[1]


def is_accent(color: str) -> bool:
    """Saturated mid-tone: brand buttons, status pills, chart bars.

    These keep their colour in the light theme.  Very dark saturated colours
    (navy panels, deep-red badge backgrounds) are *not* accents — they are
    backgrounds and get lightened.
    """
    v = hls(color)
    if v is None:
        return False
    _h, l, s = v
    return s >= 0.45 and 0.35 <= l < 0.72


def _lighten_bg(color: str) -> str:
    h, l, s = hls(color)
    if l >= 0.5 or is_accent(color):
        return normalize_hex(color)
    new_l = 1.0 - l * 0.45          # 0.07 → 0.97, 0.3 → 0.865
    new_s = s * 0.55
    return fmt_hex(*colorsys.hls_to_rgb(h, new_l, new_s))


def _darken_fg(color: str) -> str:
    h, l, s = hls(color)
    if l <= 0.42:
        return normalize_hex(color)
    new_l = 1.0 - l
    if new_l < 0.15:                # compress instead of clamping so
        new_l = 0.15 - (0.15 - new_l) * 0.5   # #fff and #e0e0e0 stay distinct
    new_l = min(new_l, 0.42)
    return fmt_hex(*colorsys.hls_to_rgb(h, new_l, s))


def to_light(color: str, role: str = "any", paired_bg: str | None = None) -> str:
    """Translate one dark-theme literal into its light-theme value.

    ``role`` is ``"bg"``, ``"fg"`` or ``"any"`` (canvas items).  ``paired_bg``
    is the dark-theme background the foreground sits on, when known; a
    foreground on an accent background is returned unchanged.  Non-colour
    strings are returned as-is.
    """
    key = normalize_hex(color)
    if key is None:
        return color
    # Idempotent: a value this function already produced is left alone, so
    # re-applying the light theme (or a light value flowing back through the
    # hooks) never drifts.
    if is_light_value(key, role):
        return color
    if role == "fg":
        if paired_bg and is_accent(paired_bg):
            out = key
        else:
            out = _FG_OVERRIDES.get(key) or _darken_fg(key)
    elif role == "bg":
        out = _BG_OVERRIDES.get(key) or _lighten_bg(key)
    else:
        l = luminance(key)
        if is_accent(key):
            out = key
        elif l > 0.5:
            out = _FG_OVERRIDES.get(key) or _darken_fg(key)
        else:
            out = _BG_OVERRIDES.get(key) or _lighten_bg(key)
    if out == key:
        return color                    # unchanged: keep the caller's spelling
    with _lock:
        _reverse.setdefault((role, out), color)
        _reverse_any.setdefault(out, color)
    return out


def to_dark(color: str, role: str = "any") -> str:
    """Inverse of :func:`to_light` for values produced by it; identity else."""
    if not isinstance(color, str):
        return color
    with _lock:
        hit = _reverse.get((role, color))
        if hit is None:
            hit = _reverse_any.get(color)
    return color if hit is None else hit


def is_light_value(color: str, role: str = "any") -> bool:
    """True if ``color`` is a value :func:`to_light` produced for ``role``.

    Role-specific on purpose: the card background maps to ``#ffffff`` while
    ``#ffffff`` is also the most common dark-theme *foreground* literal, so a
    role-agnostic check would leave white text white.
    """
    with _lock:
        if role in ("bg", "fg"):
            return (role, color) in _reverse
        return color in _reverse_any


def role_of(option: str) -> str | None:
    opt = option.lstrip("-").lower()
    if opt in _BG_OPTS:
        return "bg"
    if opt in _FG_OPTS:
        return "fg"
    if opt in _ANY_OPTS:
        return "any"
    return None


def translate(color: str, role: str, mode: str, paired_bg: str | None = None) -> str:
    if mode == LIGHT:
        return to_light(color, role, paired_bg)
    return to_dark(color, role)


# ── Option-dict remapping (shared by the hooks and the walker) ──────────
def remap_options(options: dict, mode: str | None = None,
                  current_bg: Callable[[str], str | None] | None = None) -> dict:
    """Return a copy of ``options`` with colour values translated.

    Foreground options are paired with the background option of the same
    dict when present, otherwise with ``current_bg(option)`` if given (the
    walker passes the widget's live background).
    """
    mode = mode or _mode
    if mode == DARK or not options:
        return options
    out = dict(options)
    changed = False
    for key, val in options.items():
        if val is None or not isinstance(val, str) or not val.startswith("#"):
            continue
        role = role_of(str(key))
        if role is None:
            continue
        paired = None
        if role == "fg":
            for bg_key in _FG_PAIR.get(str(key).lstrip("-").lower(), ("bg", "background")):
                pv = options.get(bg_key) or options.get("-" + bg_key)
                if isinstance(pv, str) and pv.startswith("#"):
                    paired = pv
                    break
            if paired is None and current_bg is not None:
                paired = current_bg(str(key))
        new = to_light(val, role, paired)
        if new != val:
            out[key] = new
            changed = True
    return out if changed else options


def remap_mapdict(mapdict: dict, mode: str | None = None) -> dict:
    """``ttk.Style.map`` payload: {opt: [(state…, value), …]}."""
    mode = mode or _mode
    if mode == DARK or not mapdict:
        return mapdict
    out = {}
    changed = False
    for key, specs in mapdict.items():
        role = role_of(str(key))
        if role is None or not isinstance(specs, (list, tuple)):
            out[key] = specs
            continue
        new_specs = []
        for spec in specs:
            if (isinstance(spec, (list, tuple)) and spec
                    and isinstance(spec[-1], str) and spec[-1].startswith("#")):
                new_val = to_light(spec[-1], role)
                if new_val != spec[-1]:
                    changed = True
                    spec = tuple(spec[:-1]) + (new_val,)
            new_specs.append(spec)
        out[key] = new_specs
    return out if changed else mapdict


# ── Mode state ──────────────────────────────────────────────────────────
def current_mode() -> str:
    return _mode


def set_mode(mode: str) -> str:
    global _mode
    mode = LIGHT if str(mode).lower() == LIGHT else DARK
    with _lock:
        _mode = mode
    return mode


def toggle_mode() -> str:
    return set_mode(LIGHT if _mode == DARK else DARK)


def load_saved_mode() -> str:
    """Theme persisted in ``~/.tranzor_exporter_config.json`` (default dark)."""
    try:
        from gitlab_client import load_config
        cfg = load_config() or {}
        val = str(cfg.get(CONFIG_KEY, DARK)).lower()
        return val if val in _MODES else DARK
    except Exception:
        return DARK


def save_mode(mode: str) -> None:
    try:
        from gitlab_client import update_config
        update_config(**{CONFIG_KEY: LIGHT if mode == LIGHT else DARK})
    except Exception:
        pass


class _Suspend:
    """Context manager: bypass the hooks while the walker writes values."""

    def __enter__(self):
        global _suspend_depth
        with _lock:
            _suspend_depth += 1
        return self

    def __exit__(self, *exc):
        global _suspend_depth
        with _lock:
            _suspend_depth -= 1
        return False


def _hooks_active() -> bool:
    return _mode == LIGHT and _suspend_depth == 0


# ── tkinter hooks ───────────────────────────────────────────────────────
def install() -> bool:
    """Patch tkinter once.  Safe to call repeatedly; returns True on success."""
    global _installed
    with _lock:
        if _installed:
            return True
        try:
            import tkinter as tk
            from tkinter import ttk
        except Exception:
            return False

        orig_options = tk.Misc._options

        def _options(self, cnf, kw=None):
            if _hooks_active():
                try:
                    merged = tk._cnfmerge((cnf, kw)) if kw else tk._cnfmerge(cnf)
                    if isinstance(merged, dict) and merged:
                        merged = remap_options(merged, LIGHT)
                        return orig_options(self, merged, None)
                except Exception:
                    pass
            return orig_options(self, cnf, kw)

        orig_format_optdict = ttk._format_optdict

        def _format_optdict(optdict, script=False, ignore=None):
            if _hooks_active():
                try:
                    if isinstance(optdict, dict) and optdict:
                        optdict = remap_options(optdict, LIGHT)
                except Exception:
                    pass
            return orig_format_optdict(optdict, script, ignore)

        orig_format_mapdict = ttk._format_mapdict

        def _format_mapdict(mapdict, script=False):
            if _hooks_active():
                try:
                    if isinstance(mapdict, dict) and mapdict:
                        mapdict = remap_mapdict(mapdict, LIGHT)
                except Exception:
                    pass
            return orig_format_mapdict(mapdict, script)

        orig_style_configure = ttk.Style.configure
        orig_style_map = ttk.Style.map

        def _style_configure(self, style, query_opt=None, **kw):
            if kw and query_opt is None:
                _styles_seen.add(str(style))
            return orig_style_configure(self, style, query_opt, **kw)

        def _style_map(self, style, query_opt=None, **kw):
            if kw and query_opt is None:
                _styles_seen.add(str(style))
            return orig_style_map(self, style, query_opt, **kw)

        tk.Misc._options = _options
        ttk._format_optdict = _format_optdict
        ttk._format_mapdict = _format_mapdict
        ttk.Style.configure = _style_configure
        ttk.Style.map = _style_map
        _installed = True
        return True


# ── Live re-theming of an existing widget tree ──────────────────────────
def _is_hex(v) -> bool:
    """Accepts str or Tcl_Obj (``Treeview.tag_configure`` / ``Text.tag_cget``
    hand back Tcl objects, not str)."""
    if v is None:
        return False
    v = str(v)
    return v.startswith("#") and parse_hex(v) is not None


def _walk(widget):
    yield widget
    try:
        children = widget.winfo_children()
    except Exception:
        return
    for child in children:
        yield from _walk(child)


def _retheme_tk_widget(widget, mode: str) -> None:
    import tkinter as tk
    changes = {}
    current = {}
    for opt in sorted(_BG_OPTS | _FG_OPTS):
        try:
            val = widget.cget(opt)
        except tk.TclError:
            continue
        except Exception:
            continue
        if _is_hex(val):
            current[opt] = str(val)
    if not current:
        return
    if mode == LIGHT:
        def _current_bg(fg_opt):
            for bg_key in _FG_PAIR.get(fg_opt, ("bg", "background")):
                v = current.get(bg_key)
                if v:
                    return v
            return None
        for opt, val in current.items():
            role = role_of(opt)
            if is_light_value(val, role):
                continue
            new = to_light(val, role, _current_bg(opt) if role == "fg" else None)
            if new != val:
                changes[opt] = new
    else:
        for opt, val in current.items():
            new = to_dark(val, role_of(opt))
            if new != val:
                changes[opt] = new
    if changes:
        # tk exposes both "bg" and "background" for the same option; avoid
        # sending both (harmless, but wasteful).
        for alias, full in (("bg", "background"), ("fg", "foreground")):
            if alias in changes and full in changes:
                changes.pop(alias)
        try:
            widget.configure(**changes)
        except tk.TclError:
            pass


def _retheme_text_tags(text, mode: str) -> None:
    import tkinter as tk
    try:
        tags = text.tag_names()
    except tk.TclError:
        return
    for tag in tags:
        if tag == "sel":
            continue
        changes = {}
        bg = None
        for opt in ("background", "foreground", "selectbackground",
                    "selectforeground"):
            try:
                val = text.tag_cget(tag, opt)
            except tk.TclError:
                continue
            if not _is_hex(val):
                continue
            val = str(val)
            role = role_of(opt)
            if opt == "background":
                bg = val
            if mode == LIGHT:
                if is_light_value(val, role):
                    continue
                new = to_light(val, role, bg if role == "fg" else None)
            else:
                new = to_dark(val, role)
            if new != val:
                changes[opt] = new
        if changes:
            try:
                text.tag_configure(tag, **changes)
            except tk.TclError:
                pass


def _retheme_treeview_tags(tree, mode: str) -> None:
    import tkinter as tk
    try:
        names = tree.tk.splitlist(tree.tk.call(tree._w, "tag", "names"))
    except tk.TclError:
        return
    for tag in names:
        changes = {}
        bg = None
        for opt in ("background", "foreground"):
            try:
                val = tree.tag_configure(tag, opt)
            except tk.TclError:
                continue
            if not _is_hex(val):
                continue
            val = str(val)
            role = role_of(opt)
            if opt == "background":
                bg = val
            if mode == LIGHT:
                if is_light_value(val, role):
                    continue
                new = to_light(val, role, bg if role == "fg" else None)
            else:
                new = to_dark(val, role)
            if new != val:
                changes[opt] = new
        if changes:
            try:
                tree.tag_configure(tag, **changes)
            except tk.TclError:
                pass


def _retheme_canvas_items(canvas, mode: str) -> None:
    import tkinter as tk
    try:
        items = canvas.find_all()
    except tk.TclError:
        return
    for item in items:
        try:
            kind = canvas.type(item)
        except tk.TclError:
            continue
        if kind == "window":
            continue
        changes = {}
        for opt in ("fill", "outline", "activefill", "activeoutline"):
            try:
                val = canvas.itemcget(item, opt)
            except tk.TclError:
                continue
            if not _is_hex(val):
                continue
            val = str(val)
            role = "fg" if kind == "text" else "any"
            if mode == LIGHT:
                if is_light_value(val, role):
                    continue
                new = to_light(val, role)
            else:
                new = to_dark(val, role)
            if new != val:
                changes[opt] = new
        if changes:
            try:
                canvas.itemconfigure(item, **changes)
            except tk.TclError:
                pass


_STYLE_MAP_OPTS = ("background", "foreground", "fieldbackground",
                   "selectbackground", "selectforeground", "bordercolor",
                   "lightcolor", "darkcolor", "arrowcolor", "indicatorcolor",
                   "indicatorbackground", "troughcolor")


def _retheme_styles(root, mode: str) -> None:
    import tkinter as tk
    from tkinter import ttk
    style = ttk.Style(root)
    for name in sorted(_styles_seen):
        try:
            opts = style.configure(name) or {}
        except tk.TclError:
            opts = {}
        current = {k: str(v) for k, v in opts.items()
                   if role_of(str(k)) and _is_hex(str(v))}
        changes = {}
        if mode == LIGHT:
            bg = current.get("background")
            for opt, val in current.items():
                role = role_of(opt)
                if is_light_value(val, role):
                    continue
                new = to_light(val, role, bg if role == "fg" else None)
                if new != val:
                    changes[opt] = new
        else:
            for opt, val in current.items():
                new = to_dark(val, role_of(opt))
                if new != val:
                    changes[opt] = new
        if changes:
            try:
                style.configure(name, **changes)
            except tk.TclError:
                pass
        # state maps
        map_changes = {}
        for opt in _STYLE_MAP_OPTS:
            try:
                specs = style.map(name, query_opt=opt)
            except tk.TclError:
                continue
            if not specs:
                continue
            role = role_of(opt)
            new_specs = []
            touched = False
            for spec in specs:
                spec = tuple(spec)
                val = spec[-1] if spec else None
                if _is_hex(val):
                    if mode == LIGHT:
                        new = val if is_light_value(val, role) else to_light(val, role)
                    else:
                        new = to_dark(val, role)
                    if new != val:
                        touched = True
                        spec = spec[:-1] + (new,)
                new_specs.append(spec)
            if touched:
                map_changes[opt] = new_specs
        if map_changes:
            try:
                style.map(name, **map_changes)
            except tk.TclError:
                pass


def apply_to_tree(root, mode: str | None = None) -> int:
    """Re-theme every live widget under ``root`` for ``mode`` (default: the
    current mode).  Returns the number of widgets visited.  Never raises."""
    import tkinter as tk
    from tkinter import ttk
    mode = mode or _mode
    count = 0
    with _Suspend():
        try:
            _retheme_styles(root, mode)
        except Exception:
            pass
        for w in list(_walk(root)):
            count += 1
            try:
                # ttk widgets mostly colour through styles, but ttk.Label /
                # ttk.Entry accept per-widget -background/-foreground too
                # (unsupported options raise TclError and are skipped).
                _retheme_tk_widget(w, mode)
                if isinstance(w, ttk.Treeview):
                    _retheme_treeview_tags(w, mode)
                elif isinstance(w, tk.Text):
                    _retheme_text_tags(w, mode)
                elif isinstance(w, tk.Canvas):
                    _retheme_canvas_items(w, mode)
            except Exception:
                continue
    return count


def switch(root, mode: str, persist: bool = True) -> str:
    """Set ``mode``, re-theme the live tree and (optionally) persist."""
    mode = set_mode(mode)
    apply_to_tree(root, mode)
    if persist:
        save_mode(mode)
    return mode
