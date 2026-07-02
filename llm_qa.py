"""
Send to LLM QA —— 一键"导出全量翻译 JSON + 复制配套 LQA 提示词"
==============================================================
File Translation / MR Pipeline / Scan Tasks 三个 tab 共用的轻量帮助模块。

用户在表格里选中任务后点 "🤖 Send to LLM QA"，各 tab 会：
  1. 走既有导出逻辑，强制以 **JSON + 全量翻译（All Translations）** 导出，
     产物 schema 与 ``/rc-core-products-trans-checker`` 等 LQA Skill 期望一致
     （见 export_json.py）；
  2. 把本模块的 :data:`LLM_QA_PROMPT` 写入系统剪贴板；
  3. 弹一个提示框告诉用户"文件已导出、提示词已复制，去 LLM 窗口上传并粘贴"。

本模块刻意保持 **纯逻辑 + 极薄 Tk 封装**：所有面向用户的文案（按钮标签、
弹框标题与正文）都集中在这里，且用不依赖 Tk 的纯函数产出，便于单元测试
（见 test_llm_qa.py）——真正碰 Tk 的只有 :func:`copy_prompt_to_clipboard`。
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# 提示词 —— 与 predefined skill /rc-core-products-trans-checker 对齐的固定内容。
# 用户点 "Send to LLM QA" 后自动复制到剪贴板；粘贴到 LLM 对话窗口即可（连同
# 刚导出的 JSON 附件一起）触发该 LQA Skill。
# ⚠ 一字不改：下游 Skill 的触发/解析依赖这段文本，改动前务必同步 Skill 侧。
# ---------------------------------------------------------------------------
LLM_QA_PROMPT = (
    "/rc-core-products-trans-checker 检查附件JSON这批翻译的质量，重点关注 Critical 问题。"
)


# 面向用户的文案（en / zh）。占位符：{filename} = 导出文件名；{prompt} = 上面的提示词。
_TEXT = {
    "en": {
        "button": "🤖 Send to LLM QA",
        "title": "Send to LLM QA",
        "ok": (
            "JSON exported and the LLM prompt was copied to your clipboard. "
            "Open your LLM chat window, upload the attachment, and paste the "
            "prompt.\n\n📄 {filename}"
        ),
        "noclip": (
            "JSON exported (📄 {filename}), but the prompt could not be copied "
            "to the clipboard automatically. Copy it manually:\n\n{prompt}"
        ),
    },
    "zh": {
        "button": "🤖 发送到 LLM QA",
        "title": "发送到 LLM QA",
        # 需求指定的核心文案，逐字保留；末尾追加文件名便于用户定位刚导出的附件。
        "ok": (
            "JSON 文件已导出，且 LLM 提示词已复制到剪贴板，请前往 LLM 对话窗口"
            "上传附件并粘贴提示词。\n\n📄 {filename}"
        ),
        "noclip": (
            "JSON 文件已导出（📄 {filename}），但提示词自动复制到剪贴板失败，"
            "请手动复制：\n\n{prompt}"
        ),
    },
}


def _lang(lang) -> str:
    """把任意语言码归一化为受支持的 'en' / 'zh'，未知一律回退 'en'。"""
    return "zh" if str(lang).lower().startswith("zh") else "en"


def button_label(lang: str = "en") -> str:
    """"Send to LLM QA" 按钮的本地化标签（建按钮 + 语言切换刷新时都用它）。"""
    return _TEXT[_lang(lang)]["button"]


def build_message(filename: str, lang: str = "en", copied: bool = True):
    """产出提示框的 ``(title, body)``。纯函数，不碰 Tk，便于测试。

    - ``copied=True``：正常路径，正文含需求指定的核心文案 + 文件名。
    - ``copied=False``：剪贴板写入失败的兜底，正文附上提示词全文供手动复制。
    """
    t = _TEXT[_lang(lang)]
    key = "ok" if copied else "noclip"
    body = t[key].format(filename=filename or "", prompt=LLM_QA_PROMPT)
    return t["title"], body


def copy_prompt_to_clipboard(widget, text: str = LLM_QA_PROMPT) -> bool:
    """把提示词写入系统剪贴板，返回是否成功。

    关键细节（对齐 gui_tab_opus_id_monitor._copy_all_opus_ids 的注释）：
      clipboard_clear 之后必须 clipboard_append + update()，否则 Windows 上
      Tk 的 owner-based 剪贴板会在进程/窗口退出时把内容丢掉，用户会以为
      "按钮没生效"。任何异常都吞掉并返回 False，让调用方走手动复制兜底，
      绝不因剪贴板被占用之类的边缘情况让整个导出流程崩掉。
    """
    try:
        widget.clipboard_clear()
        widget.clipboard_append(text)
        widget.update()  # 把剪贴板真正交给系统
        return True
    except Exception:
        return False


def send_prompt_and_notify(parent, filename: str, lang: str = "en") -> bool:
    """便捷入口：复制提示词 → 弹提示框。必须在 Tk 主线程调用。

    返回剪贴板是否复制成功。``parent`` 既用作剪贴板宿主也用作弹框父窗口。
    """
    # 延迟 import messagebox：本模块的纯函数部分（供测试）不应强依赖 Tk。
    from tkinter import messagebox

    copied = copy_prompt_to_clipboard(parent)
    title, body = build_message(filename, lang=lang, copied=copied)
    try:
        messagebox.showinfo(title, body, parent=parent)
    except Exception:
        # 极端情况下 parent 已销毁——退化为不带 parent 的弹框，仍不让流程崩。
        try:
            messagebox.showinfo(title, body)
        except Exception:
            pass
    return copied
