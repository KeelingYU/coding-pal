"""Build the acceptance page demo with the real generator: the stage 1 board as the sample task, with real evidence.

Usage: python3 scripts/sample_report.py [out_dir]     default out_dir: <system temp>/pal-report-sample
Evidence: real pal output from the sample board, a real unit test run, and Chrome screenshots when Chrome can run
(outside the Claude Code sandbox); without Chrome the screenshots fall back to text evidence.
Prints the page to open in Chrome.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "plugins" / "coding-pal" / "skills" / "coding-pal"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def run(args, cwd=REPO, env=None):
    p = subprocess.run([str(a) for a in args], cwd=str(cwd), env=env, capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        sys.exit(f"失败：{' '.join(map(str, args))}\n{p.stdout}{p.stderr}")
    return p


def line_of(lines, words, start=1):
    """1-based number of the first line at or after `start` that contains `words`."""
    for n in range(start, len(lines) + 1):
        if words in lines[n - 1]:
            return n
    sys.exit(f"示例证据里找不到：{words}")


def screenshot(url, path, profile):
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or (CHROME if Path(CHROME).exists() else None)
    if not chrome:
        return False
    try:
        p = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                            "--no-default-browser-check", f"--user-data-dir={profile}", "--window-size=1280,900",
                            "--virtual-time-budget=3000", f"--screenshot={path}", url], capture_output=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return p.returncode == 0 and path.exists() and path.stat().st_size > 0


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(tempfile.gettempdir()) / "pal-report-sample").resolve()
    shutil.rmtree(out, ignore_errors=True)
    evidence = out / "evidence"
    evidence.mkdir(parents=True)
    board_dir = out / "board"
    run([sys.executable, REPO / "scripts" / "sample_board.py", board_dir])
    project = board_dir / "design-pal"
    board_page = project / ".pal" / "board" / "index.html"
    hub_page = board_dir / "home" / "index.html"
    env = dict(os.environ, PAL_HOME=str(board_dir / "home"), CODEX_HOME=str(board_dir / "codex"), PYTHONDONTWRITEBYTECODE="1")

    status = run([sys.executable, SKILL / "scripts" / "pal.py", "status"], cwd=project, env=env).stdout
    (evidence / "pal-status.txt").write_text(status, encoding="utf-8")
    status_lines = status.splitlines()
    tests = run([sys.executable, "-m", "unittest", "discover", "-s", "tests/unit", "-v"],
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1")).stderr
    (evidence / "unit-tests.log").write_text(tests, encoding="utf-8")
    test_lines = tests.splitlines()
    shutil.copy(project / ".pal" / "board" / "board.js", evidence / "board.js")
    board_lines = (evidence / "board.js").read_text(encoding="utf-8").splitlines()
    shutil.copy(board_dir / "home" / "projects.js", evidence / "projects.js")

    profile = out / "chrome-profile"
    shots = {name: screenshot(page.as_uri(), evidence / f"{name}.png", profile)
             for name, page in (("board", board_page), ("hub", hub_page))}
    shutil.rmtree(profile, ignore_errors=True)

    waiting = line_of(status_lines, "等你处理（")
    merging = line_of(status_lines, "合并（同一时间只合并一个）")
    test_release = line_of(test_lines, "test_recent_releases_carry_a_title")
    test_total = line_of(test_lines, "Ran ")
    releases = line_of(board_lines, '"releases"')
    second_title = line_of(board_lines, '"title"', line_of(board_lines, '"title"', releases) + 1)
    first_cases = [
        {"caption": "pal status 把等你处理的 2 个任务列在最前，并写着要你做什么", "file": "pal-status.txt",
         "highlight": [waiting, waiting + 2]},
    ]
    if shots["board"]:
        first_cases.insert(0, {"caption": "看板页面最上面就是\"等你处理\"，两个任务各写着你要做什么", "file": "board.png"})
    hub_evidence = ([{"caption": "汇总首页有 3 个示例项目的卡片，写着各自等你处理的数量", "file": "hub.png"}] if shots["hub"] else
                    [{"caption": "汇总首页读取的项目登记里有 3 个示例项目", "file": "projects.js"}])
    data = {
        "title": "看板网页（示例）",
        "headline": "示例检查了 5 项：4 项通过，1 项要你亲眼看",
        "you_need_to": {"text": "按\"请你试\"的 3 件事各看一眼；想重新生成看板，在终端运行 `pal board`", "minutes": 5},
        "changes": [
            "新增项目看板：等你处理的任务排在最前，写明你要做什么",
            "新增汇总首页：所有项目的待处理事项在一页里",
            "左侧栏列出所有项目和任务，点一下就跳过去",
            "右上角可以切换跟随系统、亮色、暗色",
        ],
        "try": [
            {"title": "看等你处理的任务",
             "steps": [f"打开 [示例看板]({board_page.as_uri()})", "看最上面的\"等你处理\""],
             "expect": "有 2 个任务：Agent 工作台示例页、README 补颜色主题，各写着你要做什么和继续用语",
             "fail": "等你处理是空的，或没写你要做什么",
             "images": [{"caption": "应该看到的样子", "file": "board.png"}] if shots["board"] else []},
            {"title": "切换亮暗",
             "steps": ["点右上角的\"暗色\"", "按 Cmd+R 刷新页面"],
             "expect": "整页变暗，文字都看得清；刷新后仍是暗色",
             "fail": "有区域还是白底、文字看不清，或刷新后变回亮色"},
            {"title": "从汇总首页进项目",
             "steps": [f"打开 [汇总首页]({hub_page.as_uri()})", "点 design-pal（示例）的卡片"],
             "expect": "进入 design-pal 的看板，等你处理里有 2 个任务",
             "fail": "卡片点不动，或进去后是空的",
             "images": [{"caption": "汇总首页应该看到的样子", "file": "hub.png"}] if shots["hub"] else []},
        ],
        "checked": [
            {"title": "等你处理的任务排在最前，写明你要做什么", "result": "通过",
             "summary": "design-pal 示例里 2 个等你的任务排在最前，各写着要你做什么", "evidence": first_cases},
            {"title": "合并区写明同一时间只合并一个", "result": "通过",
             "summary": "写明正在合并哪个、谁在排队",
             "evidence": [{"caption": "正在合并\"画廊加搜索\"，\"登录页示例\"在排队", "file": "pal-status.txt",
                           "highlight": [merging, merging]}]},
            {"title": "汇总首页列出所有项目", "result": "通过", "summary": "3 个示例项目都有卡片",
             "evidence": hub_evidence},
            {"title": "最近发布显示版本号和标题", "result": "通过", "summary": "每个版本都带一句标题，没有标题时取变更记录的第一条",
             "evidence": [
                 {"caption": "看板数据里每个最近发布都有版本号、日期和标题", "file": "board.js",
                  "highlight": [releases, second_title]},
                 {"caption": "专门检查发布标题的测试通过，全部单元测试也都通过", "file": "unit-tests.log",
                  "highlight": [test_release, test_release]},
                 {"caption": "全部单元测试的汇总", "file": "unit-tests.log", "highlight": [test_total, test_total + 2]},
             ]},
            {"title": "暗色下文字都看得清", "result": "无法验证", "summary": "对比度和观感要人眼判断，放进了请你试第 2 项",
             "evidence": []},
        ],
        "issues": [
            {"what": "看板不会自己弹出来", "impact": "沙箱拦下打开浏览器时，要你在终端运行一条打开命令",
             "plan": "pal 会把那条命令打印出来，Agent 照着给你"},
        ],
        "details": ("这是第 3 阶段的示例验收页，不是真实任务的验收。\n"
                    "示例项目由 scripts/sample_board.py 用真实的 pal 生成；pal status 输出和单元测试日志是生成时实际运行的结果；"
                    + ("截图由 Chrome 无界面模式拍摄。" if all(shots.values()) else "这次没能截图，图片证据换成了文字证据。")),
    }
    data_file = out / "acceptance.json"
    data_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.path.insert(0, str(SKILL / "scripts"))
    sys.dont_write_bytecode = True
    import render_acceptance
    commit = run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
    page = render_acceptance.generate(data_file, evidence, out / "report", project="coding-pal（示例数据）",
                                      revision=f"rebuild/1.0 @ {commit}")
    if not all(shots.values()):
        print("没能截图（Chrome 可能被沙箱拦下），图片证据换成了文字证据。")
    print(f"示例验收页：{page}")


if __name__ == "__main__":
    main()
