"""
MR Pipeline (Stage) — optional tab
==================================

Same browser as the core MR Pipeline tab, pointed at the Stage platform
(``http://tranzor-platform-stage.int.rclabenv.com``) instead of production.

Pure-additive: this module is imported in a top-level ``try/except`` and
the tab is appended last so existing notebook indices stay put. All
fetch / export / ✏️-cache work is delegated to
:class:`gui_tabs.MRPipelineTab` with ``env_key="stage"``.
"""
from __future__ import annotations


STRINGS = {
    "en": {
        "tab_mr_pipeline_stage": "🧪 MR Pipeline (Stage)",
        "mr_sidebar_title_stage": "📊 Stage MR Pipeline Stats",
    },
    "zh": {
        "tab_mr_pipeline_stage": "🧪 MR Pipeline (Stage)",
        "mr_sidebar_title_stage": "📊 Stage MR Pipeline 统计",
    },
}


class MrPipelineStageTab:
    """Thin wrapper so export_gui can treat Stage as an optional tab."""

    def __init__(self, parent, app):
        # Late imports: this module is loaded from export_gui while
        # gui_tabs is still mid-import (gui_tabs → export_gui → here).
        import export_mr_pipeline as mr_api
        from gui_tabs import MRPipelineTab

        self.app = app
        self.parent = parent
        self.inner = MRPipelineTab(
            parent, app,
            base_url=mr_api.TRANZOR_STAGE_URL,
            env_key="stage",
        )

    def refresh_text(self):
        self.inner.refresh_text()

    def on_first_show(self):
        """Lazy-load filters, sidebar stats, and the first page of tasks."""
        self.inner.load_filters()
        self.inner._load_overview()
        self.inner.load_initial_tasks()
