"""
Full Translations (Stage) — optional tab
========================================

Same selector / export panel as the core Full Translations tab, pointed at
the Stage platform (``http://tranzor-platform-stage.int.rclabenv.com``)
instead of production.

Pure-additive: this module is imported in a top-level ``try/except`` and
the tab is appended last so existing notebook indices stay put. All
inventory / export work is delegated to
:class:`gui_tab_full_translations.FullTranslationsTab` with
``env_key="stage"``.
"""
from __future__ import annotations


STRINGS = {
    "en": {
        "tab_full_translations_stage": "🧪 Full Translations (Stage)",
        "ft_title_stage": (
            "Stage Full Translation Export (by Product × Language)"),
        "ft_subtitle_stage": (
            "Stage environment — pick products + languages, then export. "
            "Heavy data is fetched only on Export."),
    },
    "zh": {
        "tab_full_translations_stage": "🧪 全量翻译 (Stage)",
        "ft_title_stage": "Stage 全量翻译导出（按产品 × 按语言）",
        "ft_subtitle_stage": (
            "Stage 测试环境 — 选择产品 + 语言后再导出。"
            "真正的翻译数据只在点击导出时才拉取。"),
    },
}


class FullTranslationsStageTab:
    """Thin wrapper so export_gui can treat Stage as an optional tab."""

    def __init__(self, parent, app):
        import export_mr_pipeline as mr_api
        from gui_tab_full_translations import FullTranslationsTab

        self.app = app
        self.parent = parent
        self.inner = FullTranslationsTab(
            parent, app,
            base_url=mr_api.TRANZOR_STAGE_URL,
            env_key="stage",
        )

    def refresh_text(self):
        self.inner.refresh_text()

    def on_first_show(self):
        """Lazy-load the lightweight product × language inventory."""
        self.inner.on_first_show()
