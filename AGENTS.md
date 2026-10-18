# 项目规则

coding-pal 是给 Claude Code 和 Codex 用的开发流程插件：让不写代码的人通过对话、确认和验收，按「需求 → 界面 → 方案 → 实现 → 验收」把项目做出来。本项目用它自己开发自己。

<!-- coding-pal:start -->
## 开发流程（coding-pal）

本项目用 coding-pal 开发：会话开始或用户说"继续"时，使用 coding-pal 技能，先用它自带的 pal 运行 `status` 看有哪些任务。

- 用户是不写代码的产品负责人：只跟用户讲业务影响，技术判断由 Agent 自己做。
- 任务文件（卡片、需求说明、实现方案）在 `.pal/tasks/<任务>/`，只存本机；看板是 `.pal/board/index.html`。
- 长期文档随任务一起更新：
  - `docs/PRODUCT.md` 产品说明：现在能做什么、给谁用、主要流程、关键规则
  - `docs/ARCHITECTURE.md` 实现说明：结构、关键机制、数据与接口、约定、限制
  - `docs/DECISIONS.md` 决定记录：用户做的业务决定
  - `CHANGELOG.md` 变更记录：每个任务在「未发布」写 1~3 行用户看得懂的变化
<!-- coding-pal:end -->

## 本项目特有

- 发给使用者的全部内容在 `plugins/coding-pal/`；`scripts/`、`tests/` 是作者工具，不随插件分发。结构和机制见 `docs/ARCHITECTURE.md`。
- 本机两个平台装的是发布版（经目录仓库 `pal-plugins`），这里的修改要发布并更新后才在本机生效。开发中试用：
  - Claude Code：在试验项目里运行 `claude --plugin-dir <本仓库>/plugins/coding-pal`，只对那一次会话加载，直接读工作区。
  - Codex：用本机目录 `.pal/dev-catalog`（被忽略；`plugins/coding-pal` 链接到工作区）装开发版，装时复制一份，改了要重装；试完换回发布版。这几条命令会改 `~/.codex/config.toml`，交给用户运行。
- 1.0.0 之前的完整历史、0.x 标记和发布说明在私有归档仓库 `KeelingYU/coding-pal-archive`（本机远端名 `archive`）；不往公开仓库推 0.x 标记。
- 改 `skills/coding-pal/roles/` 后运行 `python3 scripts/verify.py agents`，重新生成 `plugins/coding-pal/agents/*.md`。

## 防止再次变重

1. 改规则前回答三个问题，写进提交说明：这是第几次出现（一次性失误只修这一次，不加规则）？能删或合并哪条旧规则？会让每个任务多花多少时间？
2. 篇幅预算（`scripts/budgets.json`）由程序检查强制；超出时请用户在"删并哪几条"和"提高上限到多少"之间选，选定后记进 `history`。
3. 给用户的选项写清多花多少时间和 token、换来什么；说不清换来什么就推荐更轻的。
4. 每发布 5 个版本，列出这期间从没用上的规则，删除或合并。
5. 用户提出的问题先分类：一次性失误（修这一次）、反复出现（改规则）、需求变化（开任务）。
6. 测试只测使用者能感受到的行为，不测"规则文字写没写"；不用正则判断 AI 的自然语言回答。
7. 优先删减或合并规则；规则里不写事故经过。

## 构建与测试

- **程序检查**（每个任务都跑，约 40 秒，不调用模型）：`python3 scripts/verify.py`。包括插件结构、文件引用、篇幅预算、子代理定义与角色说明一致、模板格式、单元测试（Python 3.12 和 3.9 各跑一次，pal 必须能在 3.9 下运行）、网页脚本语法、`claude plugin validate`。
- **改了验收网页生成器或看板展示**：另按 `tests/report/browser-cases.md` 在无界面浏览器里检查受影响的生成物。
- **改了插件清单、目录登记或正式发行**：`bash tests/install/run.sh claude|codex|all`（不调用模型）；开发中加 `--local` 从工作区装，不加从远端目录仓库装。
- **AI 案例 = 发布前场景**（会真实调用模型，`.pal/config.json` 的 `ai_cases`）：技能、角色、模板、pal 的提示文字都算"改到 AI 行为"。场景在 `tests/scenarios/scenarios.json`，只跑和改动相关的：
  - 跑：`python3 scripts/verify.py scenarios --platform claude|codex|both --only 4,1,3 --out .pal/tmp/<文件夹>`（耗时长的写前面）。已有结果的场景会跳过，复测换新文件夹或加 `--force`。Claude Code 要先 `claude auth login`。
  - 评判：`python3 tests/scenarios/run.py --judge --platform ... --only ... --out ...`（用 Codex 的验收员）；Codex 额度不够时，可派 Opus 子代理按 `tests/scenarios/judge.md` 评。评判只看评分要点，主 Agent 要抽查对话原文。
  - 汇总成页：写 `page.json` 后运行 `python3 tests/scenarios/report.py <结果文件夹> <page.json>`。
  - 收尾：`python3 tests/scenarios/run.py --clean --out ...` 删试验项目（试验项目在系统临时目录，评判完再删）。
  - 用量参考：场景 1、3、4、9 一批，Claude Code 约 12 美元，Codex 约 400 万 token。别和 Claude 场景同时并行派 Claude 子代理评判，容易用完 5 小时额度。

## 发布（用户说"发布"时）

1. 按技能 `stages/release.md` 定版本号、写发布说明；`pal release --version X.Y.Z --notes <文件> --push --gh`（`version_files` 是两份插件清单，`release.checks` 跑程序检查）。
2. 更新目录仓库 `pal-plugins`（本机在本仓库旁边，远端 `KeelingYU/pal-plugins`）：`python3 scripts/verify.py catalog X.Y.Z --catalog ../pal-plugins --write`，提交并推送。它只登记 `plugins/coding-pal` 和版本：Claude Code 清单填标记名和 `sha`，Codex 清单填提交号。
3. 从远端隔离安装两个平台：`bash tests/install/run.sh all`，并读回 GitHub Release 核对。
4. Codex 使用者开新会话会自动拿到新版；Claude Code 要使用者主动更新。
