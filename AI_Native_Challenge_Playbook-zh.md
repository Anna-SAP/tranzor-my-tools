# AI-Native 挑战「全绿」踩坑总结与注意事项

> 写给同样在做 RingCentral *AI-Native Development Challenge* 的同事，尤其是**没有本地开发环境**的 PM。
> 这份总结对应的是 `annasu-tranzor-helper` 这个仓库点亮 Eng dashboard **全部信号**（REPO CREATED → HOSTED AUTO-PLAYED 全部 Yes）的真实过程。
> 所有命令/路径/术语保留英文，便于直接复制。

---

## 0. 先搞清楚「谁在给你打分」

公告板（`igor-cherny-ai-challenge-monitor-*.pages.git.ringcentral.com`）**不是你的项目**，它是一个轮询器：每隔约 5 分钟去 GitLab `rc-ai-learning` group 里扫描每个成员的仓库，把检测到的"完成信号"渲染成那一行行 Yes/No。

**关键认知：你控制的不是公告板，而是你仓库里的信号。** 你要做的就是让仓库里**客观可被机器检测到**的东西齐全。每一列对应一个可验证的信号：

| 公告板列 | 它到底在检测什么 | 你要做的事 |
|---|---|---|
| **REPO CREATED** | group 里存在你的仓库 | 在 `rc-ai-learning` 下建好 GitLab 项目 |
| **NON-EMPTY** | 仓库有内容 | push 真实代码上去 |
| **CODE** | 能识别出源码文件 | 有可检测的源代码（`.py` / `.js` 等） |
| **ALL DOCS** | 四份必交文档齐全 | 根目录放 `README.md` `SPEC.md` `ARCHITECTURE.md` `RETROSPECTIVE.md` |
| **COMPLETE** | CODE + ALL DOCS 都满足 | 上面两项做到就自动亮 |
| **HOSTED PLAYABLE** | README 里有能打开的在线 demo 链接 | GitLab Pages 部署 demo + README 指向它 |
| **HOSTED AUTO-PLAYED** | 无头浏览器冒烟测试能跑通且**页面会静止** | demo 自动播放一小段后**收敛成静态页** |

后两列（Pages + 冒烟测试）是绝大多数人卡住的地方，老板在 Slack 里点名的就是这俩。下面重点讲。

---

## 1. 双远端架构：为什么必须绑两个仓库

最终状态（`git remote -v`）：

```
origin   https://github.com/Anna-SAP/tranzor-my-tools.git          # GitHub：干活 + 跑 CI 构建
gitlab   https://git.ringcentral.com/rc-ai-learning/annasu-tranzor-helper.git   # 内网 GitLab：挑战评审 + 公告板扫描
```

**为什么要两个，而不是只用 GitLab？**

- **GitHub 的云端 runner 免费、开箱即用**，用来构建 Windows `.exe` 和 macOS `.app` 很省事。
- **但 GitHub 云端 runner 进不了公司内网** `git.ringcentral.com`。
- **挑战是在内网 GitLab 上评审的**（公告板只扫 `rc-ai-learning` group）。
- 结论：**只有你自己的笔记本同时够得着 GitHub 和内网 GitLab**，所以你的电脑是这两个世界之间唯一的「桥」。很多自动化在这一步卡住，本质都是"云端到不了内网"。

绑定方法（在已有 GitHub 仓库的目录里）：

```bash
git remote add gitlab https://git.ringcentral.com/rc-ai-learning/annasu-tranzor-helper.git
git push gitlab master:main     # 注意分支名，见下方 ⚠️
```

> ⚠️ **分支名是隐形坑**：GitHub 这边默认分支叫 `master`，GitLab 那个项目默认分支可能叫 `main`。而 `.gitlab-ci.yml` 里的 Pages 任务是 `if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH` 才触发——如果你把代码 push 到 GitLab 的 `master`，但它的默认分支其实是 `main`，那么 **Pages 任务根本不会跑**，你会以为"CI 没动静"而百思不得其解。先去 GitLab `Settings > Repository > Branches` 确认默认分支名，push 时对齐。

---

## 2. HOSTED PLAYABLE：把 demo 挂上 GitLab Pages（头号拦路虎）

这是老板 Slack 提示的核心。拆成三步，每步都有坑。

### 2.1 准备 Pages 产物 + `.gitlab-ci.yml`

GitLab Pages 的规矩：**CI 必须产出一个名为 `public/` 的目录作为 artifact**，里面的 `index.html` 就是站点首页。

本仓库的 `.gitlab-ci.yml` 极简：

```yaml
pages:
  stage: deploy
  image: alpine:latest
  script:
    - echo "Publishing Tranzor MR Pipeline demo to GitLab Pages"
  artifacts:
    paths:
      - public            # ← 这个目录被发布成网站
  rules:
    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'
```

把 demo 做成**纯静态、零依赖的单个 `public/index.html`**（内嵌假数据），这样不需要构建步骤，CI 只要把 `public/` 当 artifact 交出去即可。

### 2.2 没有 runner → 流水线永远 pending（最大的坑）

**PM 账号默认没有共享 runner**，所以 `pages` 这个 job 会一直卡在 *pending / stuck*，永远不部署。这正是老板说的"LLM 卡在这一步"。

**解法：把你自己的电脑变成 runner。**

1. 安装 `gitlab-runner`（Windows/macOS 都有官方安装包）。
2. 到项目 `Settings > CI/CD > Runners`，拿到 registration token。
3. 注册，executor 选 **`shell`**：

   ```bash
   gitlab-runner register \
     --url https://git.ringcentral.com/ \
     --registration-token <项目里的 token> \
     --executor shell \
     --description "my-laptop-shell-runner"
   ```

4. 启动并让它在后台跑（`gitlab-runner run`，或装成系统服务）。

> 💡 **shell executor 的隐藏行为**：选了 `shell`，`.gitlab-ci.yml` 里写的 `image: alpine:latest` 会被**忽略**——任务直接在你本机的 shell 里跑。对纯静态 Pages 来说没影响（`script` 只是 echo 一下，真正起作用的是 `public/` 这个 artifact），但如果你的 `script` 里写了只在 alpine 里才有的命令，本机没装就会失败。所以 demo 越"纯静态、不依赖构建命令"越稳。
> 另外：runner 跑在你电脑上，**你电脑关机/退出，runner 就离线**，新的 pipeline 又会 pending。部署成功一次拿到产物即可，不用一直开着。

### 2.3 把 Pages 设为「Everyone」，否则公告板打不开（沉默的坑）

CI 跑绿了 ≠ 公告板能亮。Pages 默认可能是**只对项目成员可见**，而公告板的扫描器是"外部访客"，打不开 → **HOSTED PLAYABLE 仍然是红的**，且没有任何报错提示你。

去 `Settings > General > Visibility, project features, permissions`，把 **Pages 的可见性设为 Everyone**（老板原话：*Settings > General > Visibility > Pages > Everyone*）。

### 2.4 README 必须指向那个**真实部署出来**的 Pages URL

HOSTED PLAYABLE 的判定是"README 里的 demo 链接能打开"。所以：

- 去 `Deploy > Pages` 复制真实地址，格式形如：
  `http://<project-slug>-<随机hash>.pages.git.ringcentral.com`
  （本项目是 `http://annasu-tranzor-helper-d30d1d.pages.git.ringcentral.com`）
- 把**这个确切 URL** 写进 README 的 "Live demo" 段落。不要写成你以为的地址或占位符——那个 `-<hash>` 后缀是 GitLab 自动生成的，猜不出来。

---

## 3. HOSTED AUTO-PLAYED：让 demo「动起来又停下来」

这是最反直觉的一列。判定方式是**无头浏览器冒烟测试**："打开页面 → 等它稳定 → 截图/检查"。

**踩过的真坑**：我的 demo 原本有个每 4 秒刷新一次的"实时行情"动画，页面**永远在变**。结果：

- HOSTED PLAYABLE 过了（链接能打开），
- 但 HOSTED AUTO-PLAYED **不过**——因为无头测试的"等页面稳定"永远等不到，直接超时。

**修法**（`public/index.html` 里的逻辑）：让页面**先自动播放一小段，再永久停下来**：

```js
function startLive() { /* 每 2s 流入一条新任务，制造可见的“自动播放”效果 */ }

// 自动播放约 9 秒后，彻底停掉所有定时器，让页面进入完全静止状态
function settleLive() { liveSettled = true; stopLive(); }

// 启动后 9s 收敛成静态页
setTimeout(settleLive, 9000);
```

这样同时满足两类冒烟测试：**早期有肉眼可见的自动播放活动**（证明"playable / auto-played"），**之后很快静止**（让"wait until stable"能通过）。

> 一句话记住：**自动播放是给人看的，静止是给机器人看的，两个都要。** 无限 spinner / 无限轮播是 auto-played 这列的头号杀手。

---

## 4. 二进制发布桥接：让没有 GitHub 权限的同事也能下载真 App

这部分不影响公告板那几列，但是"双远端"故事的另一半，坑也最密，一并记下。

**核心坑**：一开始想让 GitHub 的 CI 直接把构建好的 `.exe`/`.zip` 推到内网 GitLab Releases——失败，因为**云端 runner 到不了内网**（为此专门 revert 过一版 CI：*cloud runners can't reach internal GitLab*）。

**解法**：在**你本机**跑 `publish-to-gitlab.ps1`（本机两边都够得着，充当桥）：

1. 用 `gh` 找到并下载 GitHub 上最近一次**成功**的 Windows / macOS 构建产物；
2. 上传到 GitLab 的 generic package registry；
3. 建一个按 commit 命名的 GitLab Release，挂上两个下载链接。

运行前提与坑点：

- 需要 `gh` 已登录（`gh auth status`）+ 环境变量 `GITLAB_TOKEN`（**必须有 `api` scope**）。
- **artifact / workflow 名字必须和 GitHub 上完全一致**：脚本里写死了 `Build Windows EXE` / `Build Mac App` 和 `TranzorExporter-Windows` / `TranzorExporter-Mac`，改了一边忘了另一边就下载不到。
- **复用脚本要先改三处写死的目标**：`-Repo`（你的 GitHub 仓库）、`-ProjectId`（你 GitLab 项目的数字 ID，项目首页 / Settings 里看）、`-ProjectPath`。本脚本默认写死的是 `Anna-SAP/tranzor-my-tools` 与 GitLab 项目 `40545 (annasu-tranzor-helper)`——不改的话你会把自己的构建推到别人的项目上。

### 4.1 GitHub Actions 构建侧的三个坑

- **触发用 `paths-ignore` 黑名单，别用 `paths:` 白名单**。白名单下每加一个新 `*.py` 都得手动往清单里补；漏了的话，那个 PR 合进 master **不会触发构建**，你以为出了新包其实没有。改成 `paths-ignore`（只排除文档 / userscript / 另一平台专属文件）后，新增任何源码自动纳入触发。
- **Windows 用 PyInstaller `onefile`（单文件 .exe），别用 `onedir`（带 `_internal/` 文件夹）**。onedir 时，非技术同事常直接在压缩包预览窗口里双击 `.exe`——系统只解压出那一个 `.exe`、找不到 `_internal\python312.dll` 就启动失败。我们为此 onedir→onefile 来回 revert 过（PR #64/#65/#66）。单文件最防呆。
- **macOS 构建建议设成 `workflow_dispatch` 手动触发**。Windows `.exe` 每次 push 自动出包；但 macOS runner 在私有仓库**按 10× 计费**，所以别让 Mac 包跟着每次 push 自动重建——需要时去 GitHub Actions 页面点 **Run workflow**，别傻等它自动更新。

### 4.2 同事打开时的两个坑

- **macOS Gatekeeper（Sequoia 上尤其坑）**：CI 产出的 `.app` 只是 **ad-hoc 签名、未经 notarize**。同事双击会被 Gatekeeper 拦下，而且 macOS 15 上**没有内联的「Open Anyway」按钮**，看起来就像"打不开 / 已损坏"。一定要在 README / 群里写明绕过方式：**右键 → Open**，或终端跑 `xattr -dr com.apple.quarantine TranzorExporter.app`。
- **macOS 打包别让 `.app` 散架**：`actions/upload-artifact@v4` 会把 `.app` bundle 的顶层目录"压扁"，所以 CI 里要先用 `ditto` 压成 zip 再上传，否则同事下到的 mac 包是坏的。

---

## 5. PowerShell / Token 的小而恶心的坑

这些是脚本化过程中真实修过的 bug，单独拎出来：

- **`gh --json` 字段列表里不能有空格**。`gh run list --json databaseId,headSha,createdAt` 必须连写；写成 `databaseId, headSha` 带空格，PowerShell 会按空格拆成多个参数，命令直接报错。
- **`GITLAB_TOKEN` 别粘成占位符**。如果 token 里出现非 ASCII 字符（比如中文引号、或顺手填了"你的token"几个字），那一定不是真 token。真 token 是一长串纯 ASCII 的 `glpat-...`，且要勾 `api` scope。脚本里专门加了一道友好校验来挡这个。
- **脚本需要 PowerShell 7**（`#requires -Version 7`）。系统自带的 Windows PowerShell 5.1 可能跑不动，用 `pwsh`。

---

## 6. 一页速查 Checklist（照着勾就全绿）

**仓库与文档**
- [ ] 在 `rc-ai-learning` group 下建好 GitLab 项目（→ REPO CREATED）
- [ ] push 真实代码（→ NON-EMPTY / CODE）
- [ ] 根目录齐四份：`README.md` `SPEC.md` `ARCHITECTURE.md` `RETROSPECTIVE.md`（→ ALL DOCS / COMPLETE；RETROSPECTIVE 权重最高，认真写）

**Live demo（HOSTED PLAYABLE）**
- [ ] 做一个纯静态、零依赖的 `public/index.html`
- [ ] `.gitlab-ci.yml` 里 `pages` job 把 `public/` 作为 artifact
- [ ] 确认 GitLab **默认分支名**，CI 的 `$CI_DEFAULT_BRANCH` 规则要能命中你 push 的分支
- [ ] 本机装 `gitlab-runner`，注册成 **shell** executor，让 pipeline 真正跑起来
- [ ] `Settings > General > Visibility > Pages > Everyone`
- [ ] 从 `Deploy > Pages` 复制**真实** URL，写进 README 的 Live demo 段

**自动播放冒烟（HOSTED AUTO-PLAYED）**
- [ ] demo 启动后有**可见的自动播放**（几秒内有动静）
- [ ] 之后**永久静止**（停掉所有 timer），别让页面无限动

**二进制发布（可选，给同事下载真 App）**
- [ ] 本机 `gh auth login` + `$env:GITLAB_TOKEN`（`api` scope，纯 ASCII）
- [ ] 复用脚本先改 `-Repo` / `-ProjectId` / `-ProjectPath` 三处写死目标
- [ ] Windows 用 PyInstaller **onefile**；CI 触发用 **paths-ignore** 黑名单
- [ ] macOS 包用 `ditto` 打包；构建设 `workflow_dispatch` 手动触发（10× 计费）
- [ ] README 写明 macOS Gatekeeper 绕过：右键 Open / `xattr -dr com.apple.quarantine`
- [ ] `pwsh ./publish-to-gitlab.ps1`，核对 workflow / artifact 名字两边一致

---

## 7. 一句话心法

> **公告板是面镜子，照的是你仓库里"机器能客观检测到"的东西。** 凡是卡住的，几乎都归结为三件事之一：①云端到不了内网（→ 用你的电脑当桥）；②没有 runner（→ 把本机注册成 shell runner）；③页面/链接对"外部访客 + 无头机器人"不友好（→ Pages 设 Everyone、demo 播放完要静止、README 写真实 URL）。把这三点想透，全绿只是时间问题。

*—— 整理自 `annasu-tranzor-helper` 真实提交历史，欢迎转发给还在卡关的同事。*
