# 连接状态指示器（Connection Health Pill）PRD

> Full Translation 面板 · 2026-08-05 · 状态：设计定稿，待实施
> 模块名：`conn_health.py` · UI 名称：中文「平台状态」/ EN "Platform Health"

---

## 1. 背景与动机

2026-08-05 上午的全量导出事故（详见当日 RCA）暴露了两层问题：

1. **导出前不知道环境好不好。** 平台是单 uvicorn 进程 + 5+5 共享 DB 池，API 与所有后台 worker（含每批 60–80s 纯 CPU 的 CometKiwi 评估）共享同一事件循环——拥塞是**阵发性**的：09:43–10:19 对导出器完全无应答，一小时后实测又恢复到 100–360ms/页。用户在坏窗口按下 Export All，只能靠运气。
2. **导出中出了事完全不可见。** 所有 HTTP 重试/退避日志只走 `print()`，打包 exe `console=False`（TranzorExporter.spec:169）全部丢弃；`fetch_tasks()` 无每页日志；`_HTTP_GATE`（2 permit，`export_translations.py:59-61`）acquire 无超时；`_retry_wait` 对 Retry-After 无上限。结果：2 个冻结 socket 占死闸门 18+ 分钟，GUI 表现为无声死机。

本功能在 Full Translation 页头（标题右侧）加一个**连接状态指示灯（pill）**，回答"现在适不适合全量导出"，并保证导出过程中的重试风暴/卡死**在 2 分钟内可见、可诊断**。

## 2. 目标 / 非目标

**目标**
- G1 空闲时：30s 节拍的轻量探测，四色状态一眼可读；拥塞发生后 ≤40s 内点红。
- G2 导出前：非绿状态软确认（劝阻不硬禁），确认文案带实时诊断依据。
- G3 导出中：真实流量被动遥测驱动状态；今天形态的 wedge（冻结 socket 占死闸门）**≤2 分钟内**在进度对话框里显式告警。
- G4 事后可取证：关键事件落盘 `~/.tranzor_exporter/conn_health.log`，绕开 `console=False` 吞 print 的问题。
- G5 探测自身**绝不加重服务端拥塞**，也绝不因客户端闸门卡死而失明。

**非目标**
- 不做导出的自动取消/自动重试（root-cause 加固另列 PR，见 §10 PR2）。
- 不做历史图表/趋势面板（单 power user，tooltip 摘要已足够）。
- 不监视 MR/Scan 通道的逐请求遥测（MVP 用进度心跳兜底 + 如实标注，见 §4.3）。

## 3. 信号模型（双通道）

**设计红线**：探针单飞（single-flight）、零重试、固定节拍、独立 `requests.Session`、**绝不经过 `_HTTP_GATE`**——闸门被冻结 socket 占死时（今天的形态），探针必须仍能报告服务器真相。平台侧已核实无任何 HTTP 限流/metrics 中间件（platform `app/main.py:372-409`），延迟一律客户端 wall-clock 自测。

### 3.1 通道 A：主动探针（空闲时，30s 节拍）

| 层级 | 请求 | 鉴权 | 作用 |
|---|---|---|---|
| 主探针 | `GET /api/v1/legacy/tasks?limit=1` | Bearer | 走导出 `fetch_tasks()` 完全相同的 auth 中间件 + get_db + 共享池路径（platform `legacy_tasks.py:84-132`），是对"全量导出此刻会不会卡"的真实彩排；服务端成本 = 1 次 COUNT + LIMIT 1 SELECT |
| 降级探针 | `GET /livez` | 无 | 未登录时使用；服务端零 I/O（platform `app/main.py:471-483`），延迟 = 纯事件循环拥堵计 |
| 红态分诊 | 主探针失败后追加**一发** `/livez` | 无 | 差分诊断：livez 快 + legacy 慢/失败 → API/DB 路径拥塞；livez 也慢/超时 → 事件循环阻塞（疑似评估批次）或不可达 |

- 超时 `(connect 5s, read 10s)`，read 10s 即红线（正常 100–360ms 的 ~30 倍）。
- **`/livez` 是唯一的 down-vs-congested 判别器**——`/health` 在 CometKiwi 批次期间本身要 60–80s（其 docstring 自认），绝不能单凭它判"宕机"。
- 导出进行中**完全停探**（被动遥测数据更真实，且不给服务器添乱）。
- 忙碌度信号（legacy `status=In Progress` 的 total、`/api/v1/queue/status` 的 running_count）**只在导出确认弹窗时按需拉取**（2s 预算，超时省略），只作展示**绝不参与状态判定**——避免"平台常年有任务在跑 → 慢性黄灯 → 确认框疲劳"。

### 3.2 通道 B：被动遥测（导出进行中）

在 `export_translations.py` 埋点（全部 `try/except pass` 包裹，遥测永不影响导出；CLI 直跑零行为变化）：

- **`last_response_ts`**：`_api_get`（:94 起）每收到任意 HTTP 响应（含 503）刷 `time.monotonic()`。
- **在途登记表**：请求发出前登记 `(id, url, start_mono)`、返回/异常后注销；暴露 `oldest_inflight_age`。今天"2 个 ESTABLISHED 零字节 socket 冻 18 分钟、零日志"的形态，在此模型下 = oldest_inflight_age 单调攀升——正是当时完全不可见的那个信号。
- **闸门观测**：包住 `with _HTTP_GATE:`（:101）记录 acquire 等待时长与等待线程数——能归因"导出慢是因为其它面板（File Translation 预取 / Term Watchtower / OPUS Monitor）占着 2 个 permit"。空闲态也扫描在途表（wedge 可能由其它面板遗留）。
- **重试事件**：`_log_retry` 调用点（:114、:120）记录原因/attempt/等待秒数；`_retry_wait`（:69-81）记录观测到的 Retry-After 数值（未封顶的 Retry-After 本身就是关键遥测）。

### 3.3 兜底信号（评审发现的盲区，MVP 必含）

- **进度心跳**：`_dialog_log` 每次回调刷 `last_progress_ts`。"无 HTTP 响应 **且** 无进度回调 超阈值"才是完备的 stall 判据——顺带覆盖了 MR/Scan 路径（git.ringcentral.com 挂起）而无需给 `export_mr_pipeline` 埋点。
- **导出线程活性**：`_busy == True` 且导出线程 `not is_alive()` 且未收到完成回调 → 独立红态「导出线程已终止」。否则线程因未捕获异常死亡时，所有遥测都是安静的而导出永远不会结束。

## 4. 状态模型

纯函数 `classify(...)`，全部计时用 `time.monotonic()`；阈值为 `conn_health.py` 模块常量，支持 `TRANZOR_HEALTH_*` 环境变量覆盖（阈值只有一天实测支撑，调参不发版）。

### 4.1 空闲态（通道 A）

| 状态 | 判定 | 颜色 |
|---|---|---|
| **GREEN 平台正常** | latency ≤ 1.0s（正常 100–360ms 的 ~3 倍余量） | `#4ade80` |
| **GREEN\* 正常（近期不稳）** | 当前绿，但最近 10 分钟历史（deque ~20 样本）中出现过 AMBER/RED；tooltip 说明 | `#4ade80` + `*` |
| **AMBER 平台缓慢** | 1.0s < latency ≤ 10s（评估批次挤占事件循环的退化区间） | `#fbbf24` |
| **RED 平台拥塞/无响应** | read 超时(>10s) / ConnectionError / HTTP 5xx / 429；分诊后细分文案：连接拒绝/DNS=「不可达（查网络/VPN）」，超时=「无响应」 | `#e94560` |
| **GRAY 未知** | 首个样本前 / 样本年龄 >90s（探针线程自身挂死的元防御：**灯绝不冻结在过期的绿色上**）/ 401（→「需重新登录」，服务器无辜，不染红） | `#8888a0` |

**迟滞**：任何态 → RED **单样本立即生效**（fail fast 是护栏价值）；RED/AMBER → GREEN 需**连续 2 个绿样本**（bursty 教训：单点绿不可信）。GREEN\* 修饰位保证"一小时前死过 36 分钟"的信息在按下 Export All 前一定可见。

### 4.2 导出中（通道 B 覆盖显示）

| 状态 | 判定 |
|---|---|
| 导出·正常（绿） | last_response_age ≤ 30s |
| 导出·迟缓（黄） | 静默或 oldest_inflight_age ∈ (30s, 120s] |
| **导出·疑似卡死（红）** | 静默 > 120s **且** 进度心跳同样静默；显示实际分钟数「已 N 分钟无响应」 |
| **导出线程已终止（红）** | §3.3 线程活性判据 |

120s 阈值：正常响应间隔百毫秒级，120s ≈ 千倍异常，同时吞得下常见 Retry-After 退避；对比今天 18+ 分钟的冻结，告警提前一个数量级。

### 4.3 覆盖范围如实标注

MVP 逐请求遥测只覆盖 Legacy（今天的事故源 + 最重负载）。纯 MR/Scan 阶段 pill 显示「仅监测 Legacy 通道（进度心跳兜底）」，不假装全知。

## 5. UI 规格

**位置**（用户红箭头处）：`gui_tab_full_translations.py:704-714` 现状是 `lbl_title`/`lbl_sub` 直接 `pack(anchor="w")`。改造：新建 `header = ttk.Frame(outer)` pack `fill="x"`；pill **先** `pack(side="right", anchor="ne")` 抢占右缘，再把标题/副标题装入左列——精确复刻主窗口 header 的既有技巧（`export_gui.py:1443-1490`）。

**形态**：与 token pill 同构（`export_gui.py:1449-1490` 成熟模式）——plain `tk.Label`（fg 逐 tick 可变），`bg=#1a1a2e`，`font=(FONT_FAMILY, 10)`；颜色复用 `TOKEN_STATUS_*` 色值（常量在 `conn_health.py` 本地定义，避免循环导入）。

**文案**（● 圆点 + 短语 + 数据）：
- `● 平台正常 (0.2s)` / `● Platform OK (0.2s)`；近期不稳 → `● 平台正常* (0.2s)`
- `● 平台缓慢 (3.4s)` / `● Platform slow (3.4s)`
- `● 平台拥塞/无响应` / `● Platform congested`
- `● 状态未知` · `● 需重新登录` · `● 状态过期`
- 导出中红：`● 已 4 分钟无响应` / `● No response for 4 min`

**Tooltip**（复用 `export_gui.Tooltip`，:961-1022）：上次探测时刻 + 各层延迟；近 10 分钟历史摘要（GREEN\* 时含故障窗口）；闸门归因（若有其它面板占用）；一行行动建议（「✔ 现在适合全量导出」/「⚠ 平台可能正在跑评估批次，建议暂缓」/「✖ 不可达，查网络/VPN」）。

**交互**：pill 点击 → 立即触发一次探测（single-flight 保护）。

**⚠ 模态对话框教训（评审共同盲区）**：`_ExportProgressDialog` 调 `grab_set()`（`gui_tab_full_translations.py:359`），导出期间 pill 的 hover/点击**全部失效**。因此导出期的连接状态必须**常驻在进度对话框内部**：对话框新增一行 `set_conn_state(text, color)` 状态条（**MVP 必含**，不是后期 polish）；wedge 告警同时 `append_log` 醒目行：「⚠ 已 2 分钟无任何 HTTP 响应，平台可能拥塞或请求已卡死…」，此后每 60s 追加一次。头部 pill 在导出期只是余光冗余。

**i18n**：纯函数 `format_conn_status(sample, *, lang, now) -> (text, color, tooltip_text)`，lang 逐次传入（token pill 模式，语言切换随 tick 自动生效）；`STRINGS` 增加 `ft_conn_*` en+zh 键（:134-283）；`refresh_text()`（:917-953）加一行立即重绘。

## 6. 导出预检（软确认闸门）

**落点**：`gui_tab_full_translations.py:1475`，`_preflight_platform_auth()` 通过之后、存盘对话框之前——Export All / Export Selected / Merge to JSON 三入口同经 `_do_export`，一处拦截全覆盖。

**原则：只加摩擦，绝不加锁。** 按钮永不因状态禁用——误报红灯把唯一用户锁在门外，比事故本身更伤信任。

| 状态 | 行为 |
|---|---|
| GREEN | 直接放行，零弹窗（绝大多数日子感知不到闸门存在） |
| GREEN\* | 放行，但进度对话框首行提示近期不稳 |
| AMBER / GRAY | `askyesno`：「平台当前响应缓慢 (3.4s)。全量导出预计明显变慢，仍要继续？」（GRAY 文案改为"状态未知，无法评估负载"） |
| RED | `askyesno` 默认 No：「平台当前拥塞/无响应，导出很可能长时间挂起（8/5 上午即因此冻结）。仍要继续？」 |

细节（评审补强）：弹确认框前先 `probe_soon()` 拿新鲜数据；确认文案附按需拉取的忙碌度行（「进行中 legacy 任务 N · MR 队列 M/K」，2s 预算超时省略）；用户点 Yes 后**复检一次状态**（弹窗期间状态可能已翻红，避免对着过期快照确认）。

## 7. 架构与集成点

**新模块 `conn_health.py`**（仓库根，Tk-free，仅 stdlib + requests；**严禁 import export_translations / export_gui**，静态测试锁定）：
- `ProbeSample` / `ConnSnapshot` dataclass（不可变）；
- `classify(...)` + `format_conn_status(...)` 纯函数（阈值常量 + env 覆盖在此定义）；
- `TelemetryBus`：单 `threading.Lock`，请求/重试/闸门三个 `deque(maxlen=512)` 环形缓冲 + 在途登记 dict；锁内只做内存操作，绝不 I/O/回调；
- `HealthMonitor(base_url, token_provider=None, fetch=None, interval=30.0)`：fetch/token_provider **注入式构造**（测试可完全替身）；自有 Session；single-flight；`probe_soon()` 经 `threading.Event` 即时唤醒；daemon 线程 `"conn-health-probe"`；
- 文件 sink：`~/.tranzor_exporter/conn_health.log`，append + 1MB 轮转。

**既有文件改动**：

| 文件 | 位置 | 改动 |
|---|---|---|
| `export_translations.py` | :94-123 `_api_get`、:101 闸门、:114/:120 重试点、:69-81 `_retry_wait` | 埋点（`try: import conn_health except ImportError: pass` 顶部守卫，CLI 零行为变化） |
| `gui_tab_full_translations.py` | :704-714 | header 行改造 + pill + Tooltip |
| 同上 | :653-694 `__init__` | **monitor 挂 `app` 级**（`app.health_monitor`，其它 tab 日后复用零返工）；`parent.after(1000, _conn_tick)` 首探 |
| 同上 | 新增 `_conn_tick()` | 5s 自调度 tick：只读 snapshot → classify → format → configure；整体 `try/except` + `finally` 续订（token tick / bridge watchdog 同款铁律，`export_gui.py:2683-2721`、`:3101-3139`） |
| 同上 | :1471-1476 `_do_export` | 预检确认（§6） |
| 同上 | :324-646 `_ExportProgressDialog` | `set_conn_state()` 状态行 + wedge 告警 `append_log` |
| 同上 | :917-953 + :134-283 | `refresh_text()` / `STRINGS` 增量 |

**线程模型**：I/O 全在 prober 线程与既有导出工作线程；Tk 线程只有 5s tick（零 I/O、零锁等待——snapshot() 拿锁拷贝微秒级）；UI 更新一律 `parent.after(0, ...)` marshal。

**明确否决**（评审裁定）：设计 2 的「重置连接」按钮——重建 `_session` 并不能释放被卡线程持有的 `BoundedSemaphore` permit，属于假修复；真正的解药是 PR2 的根治项。

## 8. 边界情况

1. **未登录**：探针降级 `/livez`，pill 附注「(未登录)」；探针永不弹登录框。
2. **导出中 token 过期**：通道 B 不依赖 token；空闲探针 401 → GRAY「需重新登录」（token pill 同时变红，语义互补）。
3. **探针自身挂起**：`(5,10)s` 硬超时 + single-flight + 90s 过期降灰三重兜底——任何时刻在飞探针 ≤1，灯不可能停在过期绿色。
4. **睡眠唤醒/时钟跳变**：全 monotonic；检测 wall-vs-mono 差值跳变则重置窗口与在途基线，不把睡了一觉的 socket 判成 wedge。
5. **今天式 wedge 复现**：探针（独立 Session）报服务器真相 + 在途登记表报客户端卡死，叠加显示「平台正常但导出通道卡死」。
6. **未封顶 Retry-After 长静默**：120s 红线照亮它——未封顶等待本就该被看见（根治在 PR2）。
7. **其它面板抢闸门**：闸门等待归因如实显示在 tooltip。
8. **tick 内任意异常**（含 Tk 已销毁）：吞掉 + `finally` 续订，pill 永不拖垮 after 循环。
9. **多实例**：探针 per-instance，30s 节拍 × N 实例的量级依然无害。
10. **代理/网关返回 HTML 错误页**：防御性解析，按 RED 处理。

## 9. 测试计划

零网络、零 Tk 实例化，沿仓库两大既有风格（纯函数断言 `test_token_status.py`；`object.__new__` + `_ImmediateParent` + `_Recorder` 手术 `test_gui_full_translations_partial_export.py`）：

- **`test_conn_health.py`**：classify 阈值边界矩阵（0.99/1.0/9.99/10s、超时/5xx/429/401）；迟滞（单样本降红、2 连绿升绿）；90s 过期；GREEN\* 修饰位；wedge 119s/121s；format en/zh 精确字符串 × 颜色断言；**静态隔离锁**（conn_health 不 import export_translations）。
- **`test_conn_health_monitor.py`**：注入假 transport 驱动单周期；transport 抛异常周期不炸；probe_soon 即时唤醒；single-flight；token_provider=None 走降级探针。
- **`test_export_telemetry.py`**：monkeypatch `_session.get` 依次 200 / 503+Retry-After / Timeout，断言在途表登记-注销、重试与 Retry-After 事件入 BUS；遥测函数 raise 时 `_api_get` 结果不变。
- **`test_gui_full_translations_conn.py`**：四态样本 → label configure 断言；预检 GREEN 不弹窗 / AMBER/RED 弹窗且 No 时不进存盘框 / Yes 后复检；`_busy` 时 tick 读遥测不 spawn 探针；121s 静默红文案含分钟数；tick 抛异常不冒泡且续订仍发生；线程活性 → 「导出线程已终止」。

## 10. 分阶段实施（每期一个独立可合并的非 Draft PR）

**PR1 — MVP（本身即达成"隐形冻结不可能再漏"）**
`conn_health.py`（classify/Monitor/TelemetryBus/formatter/log sink）+ `_api_get` 埋点（在途登记表 + 闸门观测 + 重试事件——评审裁定**必须在 PR1**，这是"2 分钟内可见"的唯一保证）+ header pill/Tooltip/点击即探 + 30s 探测/5s tick + **进度对话框 `set_conn_state` 行与 wedge 告警**（grab_set 教训，必须在 MVP）+ 进度心跳 + 线程活性检查 + `_do_export` 软确认 + i18n + 全部测试。

**PR2 — 根治项（紧跟 PR1，评审语：否则指示器只是给同一颗地雷装了警报器）**
`_retry_wait` Retry-After 封顶（≤60s）；`_HTTP_GATE` acquire 超时 + 饥饿事件上报；`fetch_tasks` 每页进度日志 + 防御性 offset 上限（与仓库其它分页对齐）；wedge 告警附行动指引（「取消后需重启应用以释放连接」→ 修复后更新文案）。*改导出语义，独立评审。*

**PR3 — 诊断纵深**
红态 `/livez` 差分分诊细化 + tooltip 行动建议；确认弹窗按需忙碌度行（In Progress total / queue running_count）；`probe_soon()` 预检前唤醒；env 阈值覆盖。

**PR4 — 扩展（可选）**
Term Watchtower / OPUS ID Monitor 复用 `app.health_monitor` 各加 pill；MR/Scan 通道逐请求遥测 + GitLab 可达性独立标注；点击 pill 详情弹窗。

## 11. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 误报红/黄 →「狼来了」疲劳 | 永不禁用按钮；绿态零弹窗；红线阈值 = 实测最差正常值 ~30 倍；迟滞只延迟好消息不延迟坏消息 |
| header 重构扰动布局（Mac ttk 度量差异） | 改动限十行；pill 为 plain Label 显式 bg；GUI 手术测试锁 pack 参数；合并前双语双平台目检 |
| `_api_get` 热路径埋点开销 | 锁内纯内存操作微秒级 vs 100ms+ 网络往返；全 `try/except pass` |
| `/health` 语义陷阱（评估批次期本身 60–80s） | `/livez` 是唯一宕机判别器，写死为 classify 规则 |
| 阈值只有一天实测支撑 | 模块常量 + env 覆盖，调参不发版 |
| 探针 401 在平台日志刷 INFO | GRAY-auth 态自动降级 `/livez` 直到 token 恢复 |
| 遥测钩子侵入 CLI 路径 | 顶部 ImportError 守卫 + 钩子默认关闭，CLI 零行为变化 |

## 附录 A：设计评审记录

三个独立设计（UX 极简 / 可靠性工程 / 架构集成三视角）+ 两位独立评审：

| 设计 | 评审1 | 评审2 | 关键贡献（入选） |
|---|---|---|---|
| 环境红绿灯（Conn Pill，双通道） | 8.5 | **8.7**（最佳） | 骨架：探针纪律、真实路径彩排探针、120s wedge 线、4 态模型、软确认哲学 |
| ConnHealth（遥测三合一） | **9**（最佳） | 8.0 | 内核：在途登记表/闸门归因进 PR1、GREEN\* 修饰位、`/livez` 判别器规则、log sink |
| Platform Health Pill（探测阶梯） | 7.0 | 7.3 | 工程件：注入式构造、env 阈值、app 级 monitor、probe_soon、忙碌度仅展示 |

两位评审共同发现并已纳入本 PRD 的盲区：`grab_set` 模态对话框使 pill 导出期不可交互（→ §5 对话框状态行进 MVP）；导出线程活性无人监视（→ §3.3）；进度心跳兜底 MR/Scan（→ §3.3）；Yes 后复检（→ §6）；「重置连接」是假修复（→ §7 否决）；根治项升为 PR2 优先级（→ §10）。
