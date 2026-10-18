<!-- coding-pal:start -->
## 开发流程（coding-pal）

本项目用 coding-pal 开发：会话开始或用户说"继续"时，使用 coding-pal 技能，先用它自带的 pal 运行 `status` 看有哪些任务。

- 用户是不写代码的产品负责人：只跟用户讲业务影响，技术判断由 Agent 自己做。
- 任务文件（卡片、需求说明、实现方案）在 `.pal/tasks/<任务>/`，只存本机；看板是 `.pal/board/index.html`。
- 长期文档随任务一起更新：
  - `{docs}/PRODUCT.md` 产品说明：现在能做什么、给谁用、主要流程、关键规则
  - `{docs}/ARCHITECTURE.md` 实现说明：结构、关键机制、数据与接口、约定、限制
  - `{docs}/DECISIONS.md` 决定记录：用户做的业务决定
  - `CHANGELOG.md` 变更记录：每个任务在「未发布」写 1~3 行用户看得懂的变化
<!-- coding-pal:end -->
