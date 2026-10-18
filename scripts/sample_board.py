"""Build the board page demo with the real pal: example data for coding-pal, design-pal and a project that never releases.

Usage: python3 scripts/sample_board.py [out_dir]     default out_dir: <system temp>/pal-board-sample
Prints the hub page and the project pages to open in Chrome.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PAL = Path(__file__).resolve().parents[1] / "plugins" / "coding-pal" / "skills" / "coding-pal" / "scripts" / "pal.py"


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(tempfile.gettempdir()) / "pal-board-sample").resolve()
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    env = dict(os.environ, PAL_HOME=str(out / "home"), CODEX_HOME=str(out / "codex"), PYTHONDONTWRITEBYTECODE="1",
               GIT_AUTHOR_NAME="sample", GIT_AUTHOR_EMAIL="sample@example.com",
               GIT_COMMITTER_NAME="sample", GIT_COMMITTER_EMAIL="sample@example.com")

    def run(cwd, *args):
        p = subprocess.run([str(a) for a in args], cwd=str(cwd), env=env, capture_output=True, text=True, encoding="utf-8")
        if p.returncode != 0:
            sys.exit(f"失败：{' '.join(map(str, args))}\n{p.stdout}{p.stderr}")

    def pal(cwd, *args):
        run(cwd, sys.executable, PAL, *args)

    def project(folder, name, releases, tags=()):
        root = out / folder
        root.mkdir()
        run(root, "git", "init", "-q", "-b", "main")
        (root / "README.md").write_text(f"# {name}\n", encoding="utf-8")
        run(root, "git", "add", "-A")
        run(root, "git", "commit", "-q", "-m", "start")
        pal(root, "init", "--name", name)
        cfg_path = root / ".pal" / "config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["release"]["enabled"] = releases
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        for tag, title in tags:
            run(root, "git", "tag", "-a", tag, "-m", f"{tag} {title}")
        return root

    def change(cwd, rel, text, message):
        path = Path(cwd) / rel
        path.write_text(path.read_text(encoding="utf-8") + text if path.exists() else text, encoding="utf-8")
        run(cwd, "git", "add", "-A")
        run(cwd, "git", "commit", "-q", "-m", message)

    def merged(root, task, kind, title, entry, *pairs):
        pal(root, "new", task, "--type", kind, "--title", title)
        card = (root / ".pal" / "tasks" / task / "card.md").read_text(encoding="utf-8")
        cwd = root / ".worktrees" / task if "工作副本: .worktrees/" in card else root
        change(cwd, f"{task}.txt", "done\n", title)
        change(cwd, "CHANGELOG.md", f"- {entry}\n", "changelog")
        if pairs:
            pal(root, "set", task, *pairs)
        pal(root, "merge", task)

    cp = project("coding-pal", "coding-pal（示例）", True,
                 [("v0.8.0", "按影响选择验证范围，复用有效结果"), ("v0.8.1", "补齐发布说明的交付状态")])
    merged(cp, "acceptance-v2", "普通需求", "验收网页 v2", "验收网页首屏先写结论和你要做的事")
    pal(cp, "new", "merge-command", "--type", "普通需求", "--title", "pal 合并命令")
    pal(cp, "set", "merge-command", "阶段=验收", "状态=等你", "影响范围=合并流程、看板",
        '等你做=打开验收网页按 3 步试一遍（约 10 分钟），满意就回复"验收通过"')
    acceptance = {  # 示例验收数据，让看板上的"打开验收网页"有真实的网页可去
        "title": "pal 合并命令（示例）", "headline": "示例：验收员查了 1 项，通过",
        "you_need_to": {"text": "这是看板示例里的验收网页，看一眼即可", "minutes": 1},
        "changes": ["合并改成一条命令"], "try": [], "issues": [], "details": "",
        "checked": [{"title": "合并后主线有任务的改动", "result": "通过", "summary": "示例数据",
                     "evidence": [{"caption": "示例证据", "file": "merge.log"}]}]}
    (cp / ".pal" / "tasks" / "merge-command" / "acceptance.json").write_text(json.dumps(acceptance, ensure_ascii=False), encoding="utf-8")
    (cp / ".pal" / "tmp" / "merge-command").mkdir(parents=True)
    (cp / ".pal" / "tmp" / "merge-command" / "merge.log").write_text("已合并 merge-command\n", encoding="utf-8")
    pal(cp, "report", "merge-command")
    pal(cp, "new", "board-page", "--type", "普通需求", "--title", "看板网页")
    pal(cp, "set", "board-page", "阶段=实现", "下一步=实现员在做第 2 组：项目看板页面；做完我跑测试并提交")
    pal(cp, "new", "readme-python", "--type", "小改动", "--title", "README 补安装前提")
    pal(cp, "set", "readme-python", "下一步=改完给你看效果")
    pal(cp, "todo", "add", "Windows 下的安装说明（10-08 提出，等 1.0 发布后再开始）")

    dp = project("design-pal", "design-pal（示例）", True,
                 [("v1.2.0", "新增效率型彩色主题「鸢尾多彩」"), ("v1.3.0", "新增后台商品列表示例")])
    cfg_path = dp / ".pal" / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["ai_cases"] = {"cost": "expensive", "run": "在临时 React 项目里让 Agent 做对话页并截图", "last_full_check": "2026-09-27"}
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    merged(dp, "chat-rules", "普通需求", "组件规则清单补对话组件", "组件规则清单加入对话消息流、代码块、步骤进度", "改到AI行为=是")
    pal(dp, "new", "agent-workbench", "--type", "普通需求", "--title", "Agent 工作台示例页")
    pal(dp, "set", "agent-workbench", "阶段=方案", "状态=等你", "改到AI行为=是",
        '等你做=看方案开头一页，回复"继续"或提意见', "影响范围=效率型组件库的组件、示例页、规则清单、画廊")
    pal(dp, "new", "readme-themes", "--type", "小改动", "--title", "README 补颜色主题")
    pal(dp, "set", "readme-themes", "阶段=验收", "状态=等你", '等你做=看 README 组件库表格里是否有 4 个颜色主题，没问题回复"可以"')
    pal(dp, "new", "vue-components", "--type", "普通需求", "--title", "Vue 版组件")
    pal(dp, "set", "vue-components", "状态=暂停", "原因=和 Agent 工作台改同一批组件，等它合并后再继续")
    pal(dp, "new", "login-demo", "--type", "普通需求", "--title", "登录页示例", "--risk", "高")
    pal(dp, "set", "login-demo", "阶段=合并", "状态=排队", "下一步=等画廊加搜索合并完")
    pal(dp, "new", "gallery-search", "--type", "小改动", "--title", "画廊加搜索")
    pal(dp, "set", "gallery-search", "阶段=合并", "下一步=正在合并")
    lock = {"task": "gallery-search", "pid": 1, "started": datetime.now().strftime("%Y-%m-%dT%H:%M")}  # 示例里模拟一个正在进行的合并
    (dp / ".pal" / "merge.lock").write_text(json.dumps(lock), encoding="utf-8")
    pal(dp, "todo", "add", "导出 Excel 报表（10-08 提出，等 Agent 工作台做完再开始）")

    lp = project("local-tool", "本机小工具（示例）", False)
    merged(lp, "tidy-downloads", "小改动", "整理下载文件夹", "按类型整理下载文件夹")
    pal(lp, "new", "photo-archive", "--type", "普通需求", "--title", "照片按日期归档")
    pal(lp, "set", "photo-archive", "阶段=实现", "下一步=实现员在写归档规则和测试")

    for root in (cp, dp, lp):
        pal(root, "board", "--hub")
    print(f"汇总首页：{out / 'home' / 'index.html'}")
    for root in (cp, dp, lp):
        print(f"项目看板：{root / '.pal' / 'board' / 'index.html'}")


if __name__ == "__main__":
    main()
