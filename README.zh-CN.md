# coding-pal

[English](README.md) | 简体中文

给 Claude Code 和 Codex 用的开发流程插件。不写代码的人只要通过对话、确认和验收，就能让 Agent 按「**需求 → 界面 → 方案 → 实现 → 验收**」把项目做出来。小改动不走完整流程；机械操作交给自带的小工具 pal，几秒钟做完。

## 你需要准备

- Claude Code 或 Codex。
- git：项目要在 git 仓库里。
- Python 3.9 以上：自带的小工具 pal 要用。macOS 装了苹果命令行工具就有；没有时 Agent 会提示你安装。

## 安装

coding-pal 通过插件目录 [pal-plugins](https://github.com/KeelingYU/pal-plugins) 发布，不需要下载本仓库。

Claude Code：

```bash
claude plugin marketplace add KeelingYU/pal-plugins && claude plugin install coding-pal@pal-plugins
```

Codex：

```bash
codex plugin marketplace add KeelingYU/pal-plugins && codex plugin add coding-pal@pal-plugins
```

安装只下载插件本身，不会往你的项目里写文件。

## 5 分钟上手

1. 装好后新开一个会话（Claude Code 要重启），进入你的项目文件夹。
2. 说「用 coding-pal 做一个……」，把想要的东西用一两句话讲出来。
3. 第一次用时，Agent 会在你电脑上建 `~/.coding-pal`（看板汇总首页在这里），再把项目接入，并问你两件事：这个项目要不要发布版本；要不要关闭 Agent 记忆（推荐关闭）。
4. Agent 先判断任务大小并说明理由：
   - **小改动**（一两句话说清、做完马上能看效果）：它直接改，请你看效果，你说「可以」就合并。
   - **需求**：它先问你几个问题（每题带推荐答案），复述一遍请你确认，再交一份需求说明草稿。有界面的，先做一个能点的演示页给你看；然后交方案开头一页，你回复「继续」它才动手；做完由独立的验收员逐条检查，再给你一份验收网页。
5. 你说「验收通过」，它自动合并；有发布流程的项目，你说「发布」才发布。

之后任何时候在项目里说「继续」，Agent 会列出等你处理和正在做的任务，接着做。每个项目的看板在 `.pal/board/index.html`，用浏览器打开就能看。

## 工作方式

| 阶段 | Agent 做什么 | 你做什么 |
|---|---|---|
| 需求 | 每轮问 3~5 个问题，复述后写需求说明 | 回答问题，确认需求说明 |
| 界面（有界面才有） | 做可点击的静态演示页，不改项目代码 | 确认外观和交互 |
| 方案 | 写实现方案；高风险的请评审员把关 | 看开头一页，回复「继续」 |
| 实现 | 派实现员照方案写代码和测试，逐组检查 | 不用管，除非遇到要你做的业务决定 |
| 验收 | 派验收员逐条独立检查，生成验收网页 | 照"请你试"试一试，说「验收通过」或提意见 |
| 合并 | 自动合并到主线，同一时间只合并一个 | 不用管 |
| 发布（有发布流程才有） | 定版本号、写发布说明、打标记、推送 | 说「发布」 |

- 每个任务在自己的工作副本里改，可以同时做几个，互不影响。
- 只有两类问题会停下来等你：会做出错误的东西；会造成难以挽回的损失（例如删数据、对外发消息）。
- 三条底线：不丢没合并的成果；没验证不说完成；你没说「发布」不推送。
- 项目里留 4 份长期文档：`docs/PRODUCT.md` 产品说明、`docs/ARCHITECTURE.md` 实现说明、`docs/DECISIONS.md` 决定记录、`CHANGELOG.md` 变更记录。任务文件和看板只存在你电脑上（`.pal/`，不提交）。

## 更新

你只会拿到已发布的版本。

- Claude Code 只在你要求时更新：运行 `claude plugin marketplace update pal-plugins && claude plugin update coding-pal@pal-plugins`，然后重启。
- Codex 每次开新会话会自动更新到最新发布版；想立即更新，运行 `codex plugin marketplace upgrade pal-plugins`。

从 0.x 升到 1.0 不兼容旧的项目文件布局，也没有迁移工具：旧项目按新项目重新接入即可。版本对照见 [CHANGELOG.md](CHANGELOG.md)。

## 开发

本仓库用 coding-pal 自己开发自己。开发规则、测试和发布步骤见 [AGENTS.md](AGENTS.md)，结构和机制见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。`scripts/` 和 `tests/` 是作者工具，不随插件分发。

## 许可证

[MIT](LICENSE)
