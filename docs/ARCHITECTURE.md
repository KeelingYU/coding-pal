# 实现说明

给接手的人和 Agent 读：读完知道东西在哪、为什么这样做、怎么改、怎么验证。写现在的样子，不写开发过程。

## 整体结构

coding-pal 由三部分组成：技能说明（告诉 Agent 怎么做）、小工具 pal（做机械操作）、两个网页模板（看板和验收网页）。全部放在插件目录里，经目录仓库 `pal-plugins` 发给使用者。

```text
plugins/coding-pal/                  # 发给使用者的全部内容
  .claude-plugin/plugin.json         # Claude Code 插件清单（版本号）
  .codex-plugin/plugin.json          # Codex 插件清单（版本号必须和上面一致）
  agents/*.md                        # Claude Code 子代理定义，由 roles/ 生成，不手改
  skills/coding-pal/
    SKILL.md                         # 入口：底线、确认点、开始步骤、阶段表、派子代理、每次都要做的事
    stages/                          # 7 个阶段说明：requirements ui plan build accept small-change release
    references/                      # writing.md（写给人看的规范）、verification.md（验证策略）
    roles/                           # 实现员、验收员、评审员的说明，子代理定义的唯一来源
    templates/                       # 需求说明、实现方案、4 份长期文档、项目规则段落、验收数据
    scripts/pal.py                   # 小工具 pal，只用标准库，Python ≥ 3.9
    scripts/render_acceptance.py     # 验收网页生成器，pal report 调用它
    assets/board/                    # 看板页面模板
    assets/acceptance-report/        # 验收网页模板
scripts/                             # 作者工具：程序检查、示例看板和示例验收页、篇幅预算
tests/                               # 作者工具：单元测试、安装测试、发布前场景、网页浏览检查清单
```

使用者那边：

- **项目里：** 项目规则（AGENTS.md）里的 coding-pal 段落、4 份长期文档（提交）；`.pal/`（任务文件夹、看板、临时文件、验收网页、配置）和 `.worktrees/`（工作副本）都被忽略，只存本机。
- **用户目录 `~/.coding-pal/`：** 全局设置、已接入项目的登记、汇总首页。
- **Codex 的 `~/.codex/agents/coding_pal_*.toml`：** Codex 插件不能分发子代理，由 `pal setup` 写入。

## 关键机制

- **判断交给 Agent，机械步骤交给 pal。** 建任务、改卡片、合并、放弃、发布、生成看板和验收网页都是 pal 命令；技能说明只写判断和确认点。pal 每个失败都打印"发生了什么、下一步怎么做"，Agent 照做；pal 出意外错误时 Agent 可以手动完成同样的步骤。
- **一个任务一个工作副本。** `pal new` 用 `git worktree` 在 `.worktrees/<任务>` 建分支和工作副本（只有项目第一个版本在主文件夹做，它合并前不开新任务）。改动、长期文档和变更记录都在任务分支里改，合并就是 git 合并。
- **合并（`pal merge`）：** 加锁（同一项目同一时间只合并一个，被占用就排队）→ 在工作副本里把主线合进任务分支（冲突就中止、列出冲突文件）→ 列出主线自分出后改过、和本任务重叠的文件 → 跑项目检查 → 主文件夹快进合并 → 删工作副本、分支、临时文件和验收网页 → 记合并记录 → 刷新看板。
- **发布（`pal release`）：** 先拿合并锁，只发已合并的；改版本文件、把变更记录「未发布」归入新版本、提交、打带标题的标记；可选推送和建 GitHub Release；中断后重跑会跳过已完成的步骤，不会重复升号。
- **看板：** 每次 pal 改动后重新生成 `.pal/board/board.js`；页面用 script 标签读各项目的 `board.js`，所以本机 `file://` 打开就能用，不需要服务。汇总首页读 `~/.coding-pal/projects.js` 再加载各项目。
- **子代理：** 三个角色说明只写在 `roles/`；Claude Code 的 `agents/*.md` 和 Codex 的 TOML 都由它生成，程序检查核对三者一致。模型默认：实现员 Sonnet / sol 系列最新版（medium），验收员和评审员 Opus / astra 系列最新版（high）；Codex 按"系列"从本机模型清单里取版本号最大的，找不到就继承主会话模型。主 Agent 派发时按 `pal models` 传模型和档位。
- **验收网页：** Agent 写 `acceptance.json`，证据放任务临时文件夹；`pal report` 校验字段和长度（首屏超长、步骤超过 3 步、证据缺"这说明了什么"、要打开的网页没写成链接都会拒绝），生成到 `.pal/reports/<任务>/`。网页只供阅读，用户的结论在对话里给。
- **篇幅预算：** 技能文字有上限（合计 25,000 字、入口 3,000、单个阶段 2,000、一次动作 8,000），由程序检查强制，防止规则只加不减。

## 数据与接口

- **卡片 `.pal/tasks/<任务>/card.md`：** 头部是中文键值行（标题、类型、风险、阶段、状态、等你做、下一步、影响范围、改到AI行为、分支、工作副本、更新），正文是需求摘要和方案摘要。由 pal 生成和校验，取值不对会被拒绝。
- **项目设置 `.pal/config.json`：** 主线、长期文档目录、合并前检查、是否自动合并、并行上限、发布（是否发布、版本文件、标记前缀、发布检查、是否建 GitHub Release、发布后命令）、AI 案例（成本类型、运行方法、上次全面检查）、角色的模型和档位。`~/.coding-pal/config.json` 是全局默认，项目设置覆盖它。
- **合并记录 `.pal/merged.json`、待开始 `.pal/todo.md`、合并锁 `.pal/merge.lock`。**
- **验收数据 `acceptance.json`：** 格式以 `templates/acceptance.json` 为准，单元测试核对模板能通过生成器。
- **pal 的接口：** `python3 <技能目录>/scripts/pal.py <命令>`，命令有 setup、init、new、set、status、todo、merge、drop、board、release、report、path、models；输出中文短句，另有 `--json`。退出码：0 成功；1 意外错误；2 合并冲突；3 已加锁在排队；4 项目检查失败；5 前提不满足。除 `release --push`、`release --gh` 外不联网。

## 约定

- 规则文字只写一处：通用规则在入口，阶段说明只写本阶段的动作、确认点和交付物。改规则先看能不能删或合并旧规则（见项目规则"防止再次变重"）。
- 技能里写"产品说明""实现说明"，路径由 `pal path` 给出，不写死。
- 角色说明改 `roles/`，再运行 `python3 scripts/verify.py agents` 生成 `agents/*.md`。
- pal 只用标准库，要在 Python 3.9 下能跑；路径一律用正斜杠；只删除自己建的对象（工作副本、分支、`.pal/tmp`、`.pal/reports`）；主线有未提交改动时拒绝合并。
- 两份插件清单的版本号由 `pal release` 一起改，不手改。
- 写给人看的内容按 `references/writing.md`：先写结论和"你要做什么"。

## 限制和容易踩的坑

- **Codex 工作区可写模式下 `.git` 只读：** pal 动 git 的命令（init、new、merge、drop、release）和提交都要请求提权。Claude Code 沙箱保护项目里的 `.git/config` 和 `.git/hooks`：在项目文件夹里新建仓库要授权，其余不受影响。
- **Codex 子代理名字只能用小写字母、数字和下划线**（所以叫 `coding_pal_implementer`）；Claude Code 插件子代理带命名空间（`coding-pal:implementer`），派发时传的 `model`、`effort` 优先于定义。
- **不用平台自带的隔离副本：** Claude Code 的 `isolation: worktree` 从默认分支建，拿不到任务分支的改动；实现员都在任务工作副本里工作、不提交。
- **Codex 装插件时复制一份到缓存：** 改了要重装；使用者开新会话会自动拿到新发布版，Claude Code 要使用者主动更新（比较版本号是否变化，不比大小）。
- **变更记录合并：** `pal init` 在 `.gitattributes` 写 `CHANGELOG.md merge=union`，否则两个并行任务各加一行必冲突。发布说明打标记时用原样保存，否则 `## 新增` 这类标题会被 git 当注释删掉。
- **看板和验收网页的亮暗选择：** Chrome 里所有 `file://` 页面共用一个本机存储，所以验收网页能跟随看板；本机预览服务的网址和 `file://` 各存各的。
- **macOS 文件名不区分大小写：** 已有 `docs/decisions.md` 时，`pal init` 认为 `DECISIONS.md` 已存在，不会新建。
- **场景测试的试验项目放系统临时目录：** 放在本仓库里时 Claude Code 会读到本仓库的项目规则；macOS 会定期清掉系统临时目录里几天没用的文件，评判前别拖太久。

## 怎么运行和测试

命令写在项目规则的"构建与测试"。覆盖范围：

- **单元测试**（`tests/unit/`）：pal 的各个命令（建任务、卡片校验、合并的锁和冲突、放弃时备份、发布的升号和中断重跑、看板数据、模型解析）和验收网页生成器的校验与输出。
- **程序检查**（`scripts/verify.py`）：结构、引用、篇幅、生成物一致、模板格式、网页脚本语法、插件校验。
- **安装测试**（`tests/install/run.sh`）：两个平台在隔离配置里从本机或远端安装，核对文件齐全、pal 能运行、不往使用者项目写文件。
- **发布前场景**（`tests/scenarios/`）：9 个关键场景 × 2 个平台，用真实 pal 搭试验项目、模拟用户回答、无人值守运行，由验收员按评分要点判断。这是 coding-pal 的 AI 案例，只跑和改动相关的。
- **要人工看的：** 看板和验收网页的展示（`tests/report/browser-cases.md`）；评判员只看评分要点，评分要点之外的表现要主 Agent 抽查对话原文。
