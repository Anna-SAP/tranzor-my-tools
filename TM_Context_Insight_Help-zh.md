# 🔬 TM 与上下文洞察 — 使用指南

> 适用面板：TranzorExporter v2.0+ 中的「🔬 TM & Context Insight」标签页

---

## 这个面板是干什么的？

Tranzor Platform 在给你产出每一条翻译时，内部其实经过了一条**流水线**：
有时直接复用记忆库里现成的译文（最省钱）；有时套用历史相似句的译文；
有时调用 LLM 现翻；有时还会先查上下文证据再翻……

以前这条流水线对语言专家是**黑盒**——你只看到译文，不知道它是哪条路出来的。

**这个面板把黑盒打开**，让你一眼看到：

- 每条翻译是从 **TM / ICE / 缓存 / LLM / 精炼 / 人工修订** 哪个环节产生的
- LLM 翻译时有没有拿到 **Context Service** 提供的上下文证据
- 按目标语言看比例，按行查细节

---

## 30 秒上手

1. 打开 TranzorExporter，点击最右边的 **「🔬 TM & Context Insight」** 标签页
2. 默认拉取**近 30 天**的 MR Pipeline 翻译数据。等待几秒「✓ Loaded (N)」
3. 上半区看**聚合**，下半区看**单条**，右边看**总数**
4. **双击任意一行** → 弹出抽屉，看那条翻译当时 LLM 拿到的上下文片段

---

## 三个区域逐一解读

### 1️⃣ 上半区 · 聚合面板

#### 「Translation Source Composition」翻译来源构成

按**目标语言**分行，每行展示该语言这批翻译里各来源的占比。

| 列名 | 含义 |
|---|---|
| **Language** | 目标语言（如 `zh-CN`、`fr-FR`） |
| **Bar** | 堆叠条形图，从左到右依次是 TM → ICE → Cached → LLM → Refined → Human 的颜色块（视觉上是连续色带） |
| **TM** | 由 **翻译记忆库 (Translation Memory)** 命中产生 — 之前批准过的同样句子，零成本复用 |
| **ICE** | **In-Context Exact match** — 在同一项目历史译文中找到一字不差的匹配 |
| **Cached** | 命中 Tranzor 缓存（上一个任务里翻过同样的字符串） |
| **LLM** | LLM 全新翻译（最常见的情况） |
| **Refined** | LLM 翻译后评估分数低，又被回炼了第 2 轮 |
| **Human** | 语言专家在 Dashboard 上修订过 |
| **Total translations** | 该语言这批翻译的总条数 |

**怎么读**：如果 `zh-CN` 这行 LLM 列是 140、TM 是 0，说明这周 zh-CN 没有 TM 复用，所有翻译都是新译。

#### 「Context Service Coverage」上下文覆盖率

同样按目标语言分行，展示**有多少翻译挂上了上下文证据**。

| 列名 | 含义 |
|---|---|
| **ctx_ok** / **有上下文** | 翻译时 LLM 拿到了有意义的上下文证据（最理想） |
| **partial** | 有 `context_id` 但 Context Service 没生成实质性内容 |
| **NoCtx** / **无上下文** | 完全没挂上下文（要么 Context Service 没启用，要么这条字符串没有适配的上下文） |

**怎么读**：如果某语言「无上下文」占绝大多数，说明 LLM 这批译文基本是**裸翻**——可能要在 Dashboard 上做更多 review。

---

### 2️⃣ 下半区 · 最近翻译表格

每行一条翻译，最多 500 行。重点看 **Badges** 列。

#### 徽章字典

| 徽章 | 含义 |
|---|---|
| **TM** | 来自翻译记忆库 |
| **ICE** | 来自同项目历史译文 |
| **Cached** / **缓存** | 来自 Tranzor 缓存 |
| **LLM** | LLM 新译 |
| **Refined×N** / **精炼×N** | 被回炼了 N 轮（迭代次数 ≥ 2） |
| **Human** / **人工** | 被 Language Lead 在 Dashboard 上修订过 |
| **Ctx✓** / **有上下文** | LLM 拿到了上下文证据 |
| **Ctx◐** | 有 context_id 但内容空/弱 |
| **NoCtx** / **无上下文** | 没有任何上下文 |

> 💡 一条翻译可以同时挂多个徽章。例如 `TM  Refined×2  Ctx✓` 表示：先 TM 命中，但分数低被回炼了两轮，且翻译时挂了上下文。

#### 操作

- **双击任意一行** → 弹出「上下文片段」抽屉，异步从 Tranzor 拉取该条翻译当时使用的 context JSON（包含 moduleName / fileName / usage / placeholders 等字段）
- 如果某行徽章里有 **NoCtx**，双击后抽屉会告诉你「该翻译没有 context_id」

---

### 3️⃣ 右侧 · 管线路由 KPI

九个数字总览：

| KPI | 含义 |
|---|---|
| **Total** | 当前筛选范围内的翻译总条数 |
| **TM hits** | TM 命中条数 |
| **ICE hits** | ICE 命中条数 |
| **Cached** | 缓存复用条数 |
| **LLM (fresh)** | 一次性 LLM 新译条数（未被回炼） |
| **Refined (iter ≥ 2)** | 被回炼至少一轮的条数 |
| **Human-fixed** | 人工修订过的条数 |
| **With context** | 挂了实质性上下文的条数 |
| **No context** | 完全没有上下文的条数 |

**怎么用**：
- **每周扫一眼 KPI**，看 TM/ICE 命中数是否在涨（如果停滞，说明 TM 没在学新内容）
- **看 With context vs No context 比例**，判断 Context Service 是否在你关心的项目上生效

---

## 筛选器

| 字段 | 用法 |
|---|---|
| **Project** | 留空 = 全部项目；填入项目 ID（如 `CoreLib/mthor`、`web/chc`）= 只看该项目 |
| **Language** | 留空 = 全部语言；填入语言代码（如 `zh-CN`、`fr-FR`）= 只看该语言 |
| **From / To** | 日期范围。默认近 30 天。改完点 🔍 Refresh 重新拉取 |
| **Reset** | 一键恢复默认范围 + 清空筛选 |

---

## 常见问题

### Q1：「TM」「ICE」「缓存」到底有什么区别？

| 概念 | 比喻 |
|---|---|
| **TM (Translation Memory)** | 翻译公司多年累积的「批准译文备忘录」——同样一句话之前批准过，直接复用 |
| **ICE (In-Context Exact)** | 限定在「**同一个产品项目**」内的历史译文——同一个 String Key 之前翻过 |
| **缓存** | Tranzor 内部短期 Redis 缓存——同一个字符串值，上一个任务刚翻过，原样拿过来 |

三者优先级：TM > ICE > 缓存 > LLM 新译。先命中谁就用谁，不调用 LLM 即可省钱。

### Q2：什么是 Context Service？为什么它重要？

Context Service 是 Tranzor 的「**配景调研员**」：在 LLM 翻译之前，它会扫一遍源代码，找到这个 String Key 在产品里**怎么用的**（哪个模块、哪个文件、哪些 placeholder、UI 是按钮还是标题），打包成上下文证据喂给 LLM。

LLM 拿到上下文证据 → 译得更准；没拿到 → 容易出歧义错误。所以 **Ctx✓** 是个好信号。

### Q3：「Refined×2」是好事还是坏事？

**两面看**：
- ✅ 好的一面：系统发现初译质量低，自动回炼提高了分数（说明质量门禁在起作用）
- ⚠️ 不好的一面：回炼次数多意味着初译质量普遍不高，可能 Context Service 没生效或 LLM 选型有问题

如果某语言 Refined 比例 > 20%，值得跟 Tranzor 团队反馈一下。

### Q4：「Human-fixed」的徽章和 Human Revisions tab 是什么关系？

- **Human-fixed 徽章**：通过 Dashboard 的「Language Lead Fix」流程修订过
- **Human Revisions tab**：列出**所有**人工修订（fix-translation + retranslate-preview 采纳后），数据源更全

如果想看人工修订的完整历史和 diff，去 **Human Revisions tab**；如果只是想知道某条翻译是不是被改过，看这里的徽章就够了。

### Q5：为什么 File Translation 数据不显示？

v1 版本**只覆盖 MR Pipeline**（dashboard/cases 数据源）。File Translation (Legacy) 的数据 schema 略有不同——没有 `tm_match` 字段，因为 Legacy 流程当前不走 TM。

如果你需要看 File Translation 那边的翻译来源分布，**今天的方案**：去 **Full Translations** tab 看明细，每条记录的 `translation_type` 字段已经标了 LLM / Cached / Manual Edit / LLM Retranslate。

### Q6：「无上下文」率太高怎么办？

可能的原因：
1. 该项目还没接入 Context Service
2. 这些 String Key 在源代码里搜不到（可能是新增字符串、代码未提交）
3. Context Service 那次拉取超时或失败

**行动**：发现某个项目「无上下文」占比 > 80%，跟 Tranzor 平台团队提一下，让他们看一下接入状态。

---

## 已知限制（v1）

- **仅 MR Pipeline 数据**：File Translation / Scan Tasks 不在范围内
- **表格上限 500 行**：超过的需要靠日期范围缩窄
- **上下文片段按需拉取**：双击行才发请求，避免开 tab 就发 N 次（首次双击有 1~2 秒延迟正常）
- **没有时间序列**：当前只看快照，不看趋势。后续如果有需求可以加

---

## 反馈 / 提需求

[GitHub Issues — Anna-SAP/tranzor-my-tools](https://github.com/Anna-SAP/tranzor-my-tools/issues)

或直接联系 TranzorExporter 维护者。
