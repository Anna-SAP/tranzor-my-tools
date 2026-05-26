# [Bug] UNS 任务列表 Source 列只显示 500 字符预览且无任何截断提示，看起来像 Tranzor 解析丢内容

| 字段 | 值 |
|---|---|
| **Component / Module** | Tranzor Platform · Dashboard · Legacy task detail · Translation Results Table |
| **Related MR** | gitlab !345 (TRAN-90, "UNS translation") |
| **Related commits** | `dddfb870` `add UNS long-text translation API`（后端引入 500 字符 preview）<br>`f4f34ae9` `wire UNS long email previews`（前端按 preview 展示但未消费 truncated 标志） |
| **Reporter sample task** | http://tranzor-platform.int.rclabenv.com/static/legacy/tasks/244 |
| **Source package** | `LOC-24627.zip` (UNS bugfix, scenario_3_bugfix_target_root) |
| **Environment** | int (rclabenv) — Hanny Han `MR !345` 已 merge 到 master 并上线 |
| **Severity** | **Major** — 不影响翻译产物正确性，但严重误导审阅者，会被当成"解析丢内容"的 P0 上报（如本次） |

---

## 1. 现象（用户视角）

打开 UNS 任务 244 的 Translation Results 表格，Source 列显示的内容明显比 zip 原文件少一大段：
- 显示的 source 在 `<td>` 后只到 `{{#txt "headerTitle"}}Welco`（在 "Welcome" 处戛然而止）
- 缺少 `{{partial "headerLogoAndText" ...}}`、邮件正文段落、`{{partial "footerTwoLogoAndTosAndEula"}}`、`</body></html>` 等内容
- **表格内没有任何"已截断/查看完整"提示**（没有 ellipsis badge，没有"展开全文"按钮，title 提示也只是空字符串）

直观感受：「Tranzor 把 source 解析时砍掉了一大段」。

## 2. 实际行为 vs 期望行为

| | 实际 | 期望 |
|---|---|---|
| 后端 DB 存的 source 是否完整 | ✅ 完整（与 zip 原文件逐字节一致） | ✅ 完整 |
| `/translations` 分页接口的 `source_text` 字段 | 截到 500 + `"..."` = 503 字符 | （现状可保留，前提是 UI 明示） |
| `source_text_truncated` / `source_text_length` 字段 | 后端正确返回（`true` / `2479` 等） | 同 |
| **表格 Source 列 UI** | 直接渲染 503 字符 preview，**无任何截断指示**，看上去就是"丢了内容" | 显示 preview + 显眼的截断提示（如 `… (showing 503 / 2479)`），并在该列就有"查看完整 source"入口（不必绕到 Translation 列的 Eye 按钮） |
| 翻译产物 | ✅ 用完整 source 翻译，输出含 footer / `</html>` | ✅ 同 |

## 3. 复现步骤

1. 上传附件 `LOC-24627.zip`（位于 `D:\@target\uns\20260526_LOC-24627_bugfix\LOC-24627.zip`，包含 `es-ES/`、`fr-FR/` 顶层目录的 scenario 3 包）作为 UNS 翻译任务。
2. 等待任务完成，打开 `/static/legacy/tasks/{task_id}`（本次为 244）。
3. 任意切换到 es-ES 或 fr-FR Tab，查看 Source 列。
4. 观察：Source 内容看起来在 `{{#txt "headerTitle"}}Welco` 附近被砍断，且无任何截断标识。
5. 对比附件 `es-ES/es-ES/opus_jsons/source.json` 中对应 key 的 value，差异明显。

## 4. 根因分析（已定位到具体代码与提交）

### 4.1 后端 — 列表接口对 UNS 任务做 500 字符 preview（设计行为，但需配合 UI）

`app/api/routes/legacy_translate.py:86-100`

```python
UNS_LONG_TEXT_PREVIEW_CHARS = 500

def _preview_long_text(value: str | None, limit: int = UNS_LONG_TEXT_PREVIEW_CHARS) -> dict:
    text = value or ""
    truncated = len(text) > limit
    preview = text[:limit] + ("..." if truncated else "")
    return {"preview": preview, "length": len(text), "truncated": truncated}
```

引入于 commit **`dddfb870`**（"feat: add UNS long-text translation API"）。本意：UNS 邮件单条 source 可能数 KB，避免列表接口巨包；同时通过 `source_text_truncated` / `source_text_length` 让前端知道这是预览。

后端契约 OK，唯一可以争议的小点：`source_text` 字段同时被覆盖成 preview，这是为了向后兼容老前端，但同时导致前端如果什么都不做、直接渲染 `source_text` 也会"看起来正常但其实少内容"。

### 4.2 前端 — 拿到 truncated 标志但完全没用，导致 UI 没有任何截断提示（核心 bug）

`dashboard/src/legacy/components/TranslationResultsTable.tsx:903-979`

```tsx
const sourceText = entry.source_text_preview ?? entry.source_text;   // 503 字符
...
<VisibleWhitespaceText
  text={sourceText}
  title={isUnsTask ? '' : undefined}                                  // tooltip 也清空了
  maxChars={isUnsTask ? 500 : undefined}                              // 再砍一刀
/>
```

而 `entry.source_text_truncated` / `entry.source_text_length`（后端已返回）在整个 `TranslationResultsTable.tsx` 里**0 处引用**：

```bash
$ grep -n truncated dashboard/src/legacy/components/TranslationResultsTable.tsx
# 无任何匹配
```

引入于 commit **`f4f34ae9`**（"feat: wire UNS long email previews"）。"查看完整邮件"入口被放在 Translation 列里、由眼睛图标 `Eye` 触发 `FullEmailModal`，tooltip 写的是 "View the full source and translated email." —— 这个按钮和 Source 列在视觉上不挨着，且仅在 translation 已生成时才出现；用户看 Source 列时不会自然联想到要去点 Translation 列的图标。

### 4.3 一个相关、独立的小问题：scenario_3 下 `trunk` 路径被归类为 language-specific

虽然不是本次"看上去缺内容"的直接根因，顺手记录一下；如要单开 ticket 我可以另外整理：

`app/services/legacy_file_parser.py:140-172` 的 `_classify_json_zip_path`：

- 附件 zip 路径形如 `LOC-24627/es-ES/trunk/opus_jsons/source.json` 与 `LOC-24627/es-ES/es-ES/opus_jsons/source.json`
- 因为顶层是 `es-ES`（locale）而非 `en-US`，没有 `root_idx`，于是 `scope_idx=0`，`scope_segment=es-ES` 直接被判为 `("language_specific", "es-ES")` —— `trunk` 这一段根本没参与判断。
- 结果：任务 244 的 `shared_source_count=0`，`language_source_counts={es-ES:3, fr-FR:3}`，3 个 unit 中那条原本 trunk 共享的 unit (`6a99d923...`) 被各 locale 复制了一份，导致 `source_count=6` 而非预期的 4（2 lang-specific + 1 shared × 2 lang + 1 lang-specific = 5？实际原始期望取决于 wiki 规则，这里只是指出 trunk 判定缺失）。
- 翻译结果数 6 = 2 lang × 3 units，UI 显示 "Total: 3 per language" 是这个原因，不是 dataloss。

> 这一段建议在 fix 主 bug 时一起 review，不强求同 ticket。

## 5. 数据证据（已逐字节核对）

通过 `GET /api/v1/legacy/tasks/244/translations/{translation_id}/full-text` 拿到 DB 中存储的完整 source，与 zip 原文件做 SHA-256 + 长度对比：

| translation_id | opus_id (hash 前 10) | DB source 长度 | 原文件 source 长度 | SHA-256 一致 |
|---|---|---|---|---|
| 590072 (es-ES) | 099b95b94d… | 2479 | 2479 | ✅ `d6c3fe6510b0` = `d6c3fe6510b0` |
| 590073 (es-ES) | 4bb8635b84… | 2549 | 2549 | ✅ `89d7981ffed1` = `89d7981ffed1` |
| 590074 (es-ES) | 6a99d923c4… | 2413 | 2413 | ✅ `342ac7c0bc52` = `342ac7c0bc52` |
| 590075 (fr-FR) | 099b95b94d… | 2479 (length 字段) | 2479 | ✅（与 590072 同 source）|
| 590076 (fr-FR) | 4bb8635b84… | 2549 (length 字段) | 2549 | ✅ |
| 590077 (fr-FR) | 6a99d923c4… | 2413 (length 字段) | 2413 | ✅ |

并且翻译结果（DB 中 `translated_text`）末尾都正确包含被"看起来漏掉"的内容：
- 590072 → `... {{partial "footerTwoLogoAndTosAndEula"}} ... </html>`（长度 2530）
- 590073 → `... {{partial "footerSupportAndCopyright"}} ... </html>`（长度 2610）
- 590074 → `... {{partial "footerCommonTosAndCopyright"}} ... </html>`（长度 2453）

→ **完全证伪 "Tranzor 解析丢内容" 的猜测**；翻译用的是完整 source，产物里也有完整结构。问题在 UI。

而列表分页接口给出的"列表字段"在每一个 entry 上都很清楚地告诉前端这是预览：

```json
{
  "opus_id": "...3460__en_US",
  "source_text": "...503 chars + …",       // 截断的预览
  "source_text_preview": "...same 503...",
  "source_text_length": 2479,              // ← 前端没用
  "source_text_truncated": true            // ← 前端没用
}
```

## 6. 影响范围

- 影响所有 source 长度 > 500 字符的 UNS 任务（基本是全部 UNS 邮件模板）。
- 不影响翻译产物正确性。
- 影响审阅效率与信任度：审阅者会怀疑数据完整性，触发误报（如本次准备开此 ticket 之前的怀疑路径）。
- 影响 LQA / TQA 流程：人工对照 source 时容易把"前端展示截断"误判为"翻译/解析缺失"。

## 7. 建议修复（按优先级）

### P0 (≤1 行 UI 改动即可缓解)
在 Source / Translation 列的 `VisibleWhitespaceText` 旁加一个"已截断"指示，并把完整长度展示出来：

```tsx
{entry.source_text_truncated && (
  <span className="ml-1 inline-flex items-center rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">
    preview · {entry.source_text_length?.toLocaleString()} chars total
  </span>
)}
```

并把 Eye → FullEmailModal 的入口同时也挂到 Source 列（不是只挂在 Translation 列）。

### P1
- 把 Source 列的 cell title (`title=` 属性) 改为 `entry.source_text_length` 提示，鼠标悬停就能看到 "Full length: 2479 chars (preview shows 500)"；目前 `title={isUnsTask ? '' : undefined}` 直接把 tooltip 也清空了。
- 表格内单元格预览长度（前端 `maxChars=500`）与 API 预览长度（后端 `UNS_LONG_TEXT_PREVIEW_CHARS=500`）应改为同一来源/常量；现在前后端各写一次，未来调一边漏一边。

### P2 (与本 bug 弱相关，仅记录)
- `_classify_json_zip_path` 对 scenario 3（顶层无 en-US，直接是 locale 目录）下的 `trunk` 段没有归到 shared；建议在 trunk 路径段被检测到时，无论位置都视为 shared。
- 后端列表接口 `source_text` 字段不要再覆盖成 preview，让 `source_text` 永远是完整文本，`source_text_preview` 才是 preview——这样老前端"什么都不做"的展示也是正确的。短期内为了兼容当前 UI 不必动，但留作下次大改时的契约修正点。

## 8. 验收标准 (Definition of Done)

1. 在任务 244 上，Source 列单元格能看到截断 badge 或 ellipsis 标识，鼠标悬停或点击能看到"完整 2479/2549/2413 字符"信息。
2. 单元格旁能直接点开 FullEmailModal 看到完整 source（不必跨列到 Translation 的 Eye 按钮）。
3. 视觉回归：原非 UNS 任务的 Source/Translation 列展示不变。
4. 单元测试：`TranslationResultsTable` 新增 case 覆盖 `source_text_truncated=true` 时的渲染。

## 9. 附件清单

- `LOC-24627.zip` — 复现用 source package
- 任务 244 截图（用户提供）
- 本 bug 报告中的数据证据可由以下命令复现：
  ```bash
  curl -s http://tranzor-platform.int.rclabenv.com/api/v1/legacy/tasks/244 | jq
  curl -s 'http://tranzor-platform.int.rclabenv.com/api/v1/legacy/tasks/244/translations?target_language=es-ES'
  curl -s http://tranzor-platform.int.rclabenv.com/api/v1/legacy/tasks/244/translations/590072/full-text
  ```

## 10. 关联

- 上线 MR：gitlab.ringcentral.com/agent-stack/tranzor-platform/-/merge_requests/345
- 关键提交：`dddfb870`（后端 preview）、`f4f34ae9`（前端 wire preview）
- 类似/历史改动：`f3c0dfd8`、`5d000b34`、`79d72309` 等 UNS preview 周边修补
