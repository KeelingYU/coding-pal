#!/usr/bin/env python3
"""coding-pal 发布前场景测试：9 个场景 × 2 个平台，在临时试验项目里无人值守运行。会真实调用模型、消耗额度。

用法（一般经 python3 scripts/verify.py scenarios 调用）：
  run.py --platform codex|claude|both [--only 1,3] [--jobs 4] [--out 目录] [--effort high] [--force] [--dry-run]
  run.py --clean [--out 目录]          删掉这批结果对应的试验项目（Claude Code 的会话记录一并清除）
  run.py --judge [--platform ...] [--only ...] [--jobs N]   用 Codex 的验收员按 judge.md 评判已跑完的场景
每个场景的结果在 <out>/<平台>-<场景>/：transcript.md（对话和操作，"（模拟用户）"开头的是事先写好的回答）、
state.md（结束时的项目状态和程序核对）、rubric.md（评分要点）、meta.json（轮数、用时、用量）、raw/（原始事件）。
由验收员按评分要点判断，不用文字匹配。试验项目放在系统临时目录：放进本仓库的话，Claude Code 会读到本仓库的项目规则。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PLUGIN = ROOT / "plugins" / "coding-pal"
SKILL = PLUGIN / "skills" / "coding-pal"
PAL = SKILL / "scripts" / "pal.py"
sys.path.insert(0, str(HERE))
from fixtures import FIXTURES  # noqa: E402

CODEX = os.environ.get("CODEX_BIN") or next((p for p in ("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex",)
                                             if os.access(p, os.X_OK)), "codex")
NOTE = ("\n\n（测试说明：之后的用户回复是事先写好的模拟回答，以\"（模拟用户）\"开头。"
        "需要看页面时用无界面浏览器截图或读页面内容，不要用 open 等命令在真人的浏览器里打开页面。）")
DEFAULT_RULES = [
    {"stage": "需求", "doc": False, "say": "（模拟用户）复述没问题，就按这个写需求说明吧。"},
    {"stage": "需求", "say": "（模拟用户）需求说明看过了，确认。"},
    {"stage": "界面", "say": "（模拟用户）演示页看过了，就按这个做。"},
    {"stage": "方案", "say": "（模拟用户）方案开头一页看过了，没有异议，继续。"},
    {"stage": "验收", "type": "小改动", "say": "（模拟用户）可以。"},
    {"stage": "验收", "say": "（模拟用户）验收通过。"},
    {"say": "（模拟用户）按你的推荐来，继续。"},
]
TURN_TIMEOUT = 60 * 60
PRINT = threading.Lock()
LIMITED = {}  # 平台 → 额度用完的提示；之后的场景不再开跑


def say(text):
    with PRINT:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {text}", flush=True)


class Ctx:
    """Runs commands for a fixture with the scenario's isolated environment."""

    def __init__(self, env):
        self.env = env

    def run(self, cwd, *args):
        p = subprocess.run([str(a) for a in args], cwd=str(cwd), env=self.env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if p.returncode != 0:
            raise RuntimeError(f"搭试验项目失败：{' '.join(map(str, args))}\n{p.stdout}{p.stderr}")
        return p.stdout.strip()

    def pal(self, cwd, *args):
        return self.run(cwd, sys.executable, PAL, *args)

    def git(self, cwd, *args):
        return self.run(cwd, "git", *args)


def quiet(cwd, *args, env=None):
    p = subprocess.run([str(a) for a in args], cwd=str(cwd), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout + p.stderr).strip()


# ---------- isolated environment ----------

def clean_env(base):
    """Only what a fresh terminal would have: no desktop-app or Claude Code session variables."""
    bin_dir = base / "bin"
    bin_dir.mkdir()
    for name in ("open", "xdg-open"):  # 试验里不在真人的浏览器里弹页面
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\necho \"（场景测试环境：没有打开浏览器）$*\"\n", encoding="utf-8")
        stub.chmod(0o755)
    env = {k: os.environ[k] for k in ("HOME", "USER", "LOGNAME", "SHELL", "TMPDIR") if k in os.environ}
    env.update(PATH=f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}", LANG="en_US.UTF-8", TERM="dumb",
               PAL_HOME=str(base / "pal-home"), PYTHONDONTWRITEBYTECODE="1")
    return env


def plugin_copy(target):
    """The plugin files tracked by git, as they are in the working tree (uncommitted edits included)."""
    files = subprocess.run(["git", "ls-files", "-z", "--", "plugins/coding-pal"], cwd=str(ROOT), capture_output=True).stdout
    for rel in filter(None, files.decode().split("\0")):
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dest)


def codex_home(base, env, effort):
    """A Codex config of its own: the user's login and model list, the dev plugin from a local catalog, no personal rules."""
    home = base / "codex-home"
    home.mkdir()
    real = Path.home() / ".codex"
    (home / "auth.json").symlink_to(real / "auth.json")
    if (real / "models_cache.json").exists():
        shutil.copy2(real / "models_cache.json", home / "models_cache.json")
    model = next((line for line in (real / "config.toml").read_text(encoding="utf-8").splitlines()
                  if line.strip().startswith("model ") or line.strip().startswith("model=")), 'model = "gpt-6-astra"')
    (home / "config.toml").write_text(f"{model}\nmodel_reasoning_effort = \"{effort}\"\n", encoding="utf-8")
    catalog = base / "catalog"
    plugin_copy(catalog)
    manifest = catalog / ".agents" / "plugins" / "marketplace.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"name": "coding-pal-dev", "plugins": [{
        "name": "coding-pal", "source": {"source": "local", "path": "./plugins/coding-pal"},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Developer Tools"}]}), encoding="utf-8")
    env = dict(env, CODEX_HOME=str(home))
    for args in (["plugin", "marketplace", "add", str(catalog)], ["plugin", "add", "coding-pal@coding-pal-dev"]):
        code, text = quiet(base, CODEX, *args, env=env)
        if code != 0:
            raise RuntimeError(f"Codex 装开发版失败：{text[-500:]}")
    return home


# ---------- one turn ----------

def clip(text, limit=200):
    text = str(text).replace("\r", "")
    return text if len(text) <= limit else text[:limit] + "…"


def output_lines(text, n=3):
    lines = [l for l in str(text or "").splitlines() if l.strip()]
    shown = [clip(l) for l in lines[:n]]
    return shown + ([f"…（共 {len(lines)} 行）"] if len(lines) > n else [])


def codex_turn(project, env, message, session, raw):
    args = [CODEX, "exec"] + (["resume"] if session else []) + ["--json", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox"]
    args += [session, message] if session else ["-C", str(project), message]
    code, lines = stream(args, project, env, raw)
    events, final, usage, thread, error = [], "", {}, session, ""
    for d in lines:
        kind, item = d.get("type"), d.get("item") or {}
        if kind == "thread.started":
            thread = d.get("thread_id") or thread
        elif kind == "turn.completed":
            for k, v in (d.get("usage") or {}).items():
                usage[k] = usage.get(k, 0) + (v or 0)
        elif kind in ("error", "turn.failed"):
            events.append(("错误", clip(json.dumps(d, ensure_ascii=False), 600), []))
            error = json.dumps(d, ensure_ascii=False)
        elif kind == "item.completed":
            t = item.get("type")
            if t == "agent_message":
                final = item.get("text", "")
                events.append(("Agent", final, []))
            elif t == "command_execution":
                events.append(("命令", f"$ {clip(item.get('command'), 300)}　（退出码 {item.get('exit_code')}）",
                               output_lines(item.get("aggregated_output"))))
            elif t == "file_change":
                events.append(("改文件", "、".join(f"{c.get('kind', '')} {c.get('path', '')}" for c in item.get("changes", [])), []))
            elif t in ("reasoning", "todo_list"):
                continue
            else:
                events.append((t or "事件", clip(json.dumps(item, ensure_ascii=False), 500), []))
    return {"code": code, "session": thread, "events": events, "final": final, "usage": usage, "error": error if not final else ""}


def claude_turn(project, env, message, session, raw, model, effort):
    args = ["claude", "-p", message, "--output-format", "stream-json", "--verbose", "--plugin-dir", str(PLUGIN),
            "--setting-sources", "project,local", "--permission-mode", "bypassPermissions", "--model", model, "--effort", effort]
    if session:
        args += ["--resume", session]
    code, lines = stream(args, project, env, raw)
    events, final, usage, cost, sid, pending, error = [], "", {}, 0.0, session, {}, ""
    for d in lines:
        kind = d.get("type")
        content = d["message"].get("content") if isinstance(d.get("message"), dict) else None
        if kind == "system" and d.get("subtype") == "init":
            sid = d.get("session_id") or sid
        elif kind == "system" and d.get("subtype") == "permission_denied":
            who = "子代理" if d.get("agent_id") else "主 Agent"
            events.append(("权限被拒", f"{who}的 {d.get('tool_name', '')} 被拒：{clip(d.get('decision_reason', ''), 120)}", []))
        elif kind == "assistant" and isinstance(content, list):
            for block in content:
                if block.get("type") == "text" and block.get("text", "").strip():
                    events.append(("Agent", block["text"], []))
                elif block.get("type") == "tool_use":
                    pending[block.get("id")] = []
                    events.append(("操作", tool_label(block.get("name"), block.get("input") or {}), pending[block.get("id")]))
        elif kind == "user" and isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_result" and block.get("tool_use_id") in pending:
                    result = block.get("content")
                    text = result if isinstance(result, str) else "\n".join(c.get("text", "") for c in result or [] if isinstance(c, dict))
                    pending[block["tool_use_id"]] += (["（出错）"] if block.get("is_error") else []) + output_lines(text)
        elif kind == "result":  # 子代理在后台跑时，一轮里会有好几条，费用和用量都是累计值，以最后一条为准
            final = d.get("result") or final
            sid = d.get("session_id") or sid
            cost = d.get("total_cost_usd") or cost
            usage = {k: v for k, v in (d.get("usage") or {}).items() if isinstance(v, (int, float))} or usage
            if d.get("is_error") or d.get("subtype") != "success":
                events.append(("错误", clip(d.get("result") or d.get("subtype"), 600), []))
                error = d.get("result") or d.get("subtype") or "出错"
    return {"code": code, "session": sid, "events": events, "final": "" if error else final, "usage": usage,
            "cost_usd": round(cost, 4), "error": error}


def tool_label(name, data):
    if name == "Bash":
        return f"$ {clip(data.get('command'), 300)}"
    if name in ("Agent", "Task"):
        extra = "，".join(f"{k}={data[k]}" for k in ("model", "effort") if data.get(k))
        return f"派子代理 {data.get('subagent_type', '（默认）')}{'（' + extra + '）' if extra else ''}：{clip(data.get('description', ''), 80)}"
    if name == "Skill":
        return f"用技能 {data.get('skill', '')}"
    verbs = {"Read": "读", "Write": "写", "Edit": "改", "NotebookEdit": "改"}
    if name in verbs:
        return f"{verbs[name]} {data.get('file_path', '')}"
    return f"{name} {clip(json.dumps(data, ensure_ascii=False), 200)}"


def stream(args, cwd, env, raw):
    """Run one non-interactive turn, keep the raw JSON lines, return (exit code, parsed events)."""
    lines = []
    with open(raw, "w", encoding="utf-8") as keep, open(raw.with_suffix(".stderr.txt"), "w", encoding="utf-8") as errors:
        p = subprocess.Popen([str(a) for a in args], cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8", errors="replace")
        timer = threading.Timer(TURN_TIMEOUT, p.kill)
        timer.start()
        try:
            for line in p.stdout:
                keep.write(line)
                try:
                    lines.append(json.loads(line))
                except ValueError:
                    if line.strip():
                        lines.append({"type": "error", "text": line.strip()})
            code = p.wait()
        finally:
            timer.cancel()
    if code != 0:
        tail = raw.with_suffix(".stderr.txt").read_text(encoding="utf-8", errors="replace").strip().splitlines()[-5:]
        lines.append({"type": "error", "text": "退出码 %d：%s" % (code, " / ".join(tail))})
    return code, lines


# ---------- state between turns ----------

def cards(project):
    out = []
    for card in sorted((project / ".pal" / "tasks").glob("*/card.md")):
        text = card.read_text(encoding="utf-8")
        head = {"id": card.parent.name}
        for line in text.split("---")[1].splitlines() if text.startswith("---") else []:
            if ":" in line:
                k, v = line.split(":", 1)
                head[k.strip()] = v.strip()
        out.append(head)
    return out


def requirement_filled(project, card):
    path = project / ".pal" / "tasks" / card["id"] / "requirement.md"
    if not path.exists():
        return False
    template = (SKILL / "templates" / "requirement.md").read_text(encoding="utf-8").replace("{标题}", card.get("标题", card["id"]))
    return path.read_text(encoding="utf-8").strip() != template.strip()


def snapshot(project, facts):
    state = {"cards": cards(project), "merged": [], "remote_tags": [], "tags": [], "worktrees": [], "main": ""}
    if (project / ".git").exists():
        state["main"] = quiet(project, "git", "log", "-1", "--format=%h %s", "main")[1]
        state["tags"] = quiet(project, "git", "tag")[1].split()
        state["worktrees"] = [l[9:] for l in quiet(project, "git", "worktree", "list", "--porcelain")[1].splitlines() if l.startswith("worktree ")][1:]
    merged = project / ".pal" / "merged.json"
    if merged.exists():
        state["merged"] = [m.get("task") for m in json.loads(merged.read_text(encoding="utf-8"))]
    if facts.get("remote"):
        state["remote_tags"] = [l.split("refs/tags/")[-1] for l in quiet(project, "git", "ls-remote", "--tags", facts["remote"])[1].splitlines()
                                if "^{}" not in l]
    return state


def stopped(scn, state, turn):
    if scn["stop"] == "merged":
        return bool(state["merged"]) and not [c for c in state["cards"] if c.get("状态") != "待发布"]
    if scn["stop"] == "acceptance":
        return any(c.get("阶段") == "验收" and c.get("状态") == "等你" for c in state["cards"])
    if scn["stop"] == "released":
        return any(t != "v1.2.0" for t in state["remote_tags"])
    return False


def reply(scn, state, turn, fired, project):
    waiting = [c for c in state["cards"] if c.get("状态") == "等你"]
    pool = waiting or state["cards"]
    focus = max(pool, key=lambda c: c.get("更新", "")) if pool else None
    stage = focus.get("阶段") if focus else "需求"
    kind = focus.get("类型") if focus else ""
    doc = requirement_filled(project, focus) if focus else False
    for n, rule in enumerate(scn.get("rules", []) + DEFAULT_RULES):
        key = (n < len(scn.get("rules", [])), n)
        if rule.get("once") and key in fired:
            continue
        stages = rule.get("stage")
        if stages and stage not in (stages if isinstance(stages, list) else [stages]):
            continue
        if "turn" in rule and rule["turn"] != turn:
            continue
        if "type" in rule and rule["type"] != kind:
            continue
        if "doc" in rule and rule["doc"] != doc:
            continue
        fired.add(key)
        return rule["say"]
    return DEFAULT_RULES[-1]["say"]


# ---------- the end state and what a program can check ----------

def tests_pass(folder):
    if not (folder / "tests").is_dir():
        return "没有测试"
    code, text = quiet(folder, "node", "--test", *sorted(str(p) for p in folder.glob("tests/*.test.js")))
    summary = [l.lstrip("ℹ# ").strip() for l in text.splitlines() if l.lstrip("ℹ# ").startswith(("pass ", "fail "))]
    return ("通过" if code == 0 else "没通过") + f"（{'，'.join(summary)}）"


def objective(scn, project, facts, history):
    """Facts a program can establish for the judge; the judge still decides against the rubric."""
    lines = []
    if not (project / ".git").exists():
        return ["项目还不是 git 仓库"]
    main_files = quiet(project, "git", "ls-tree", "-r", "--name-only", "main")[1].splitlines()
    lines.append(f"主线测试：{tests_pass(project)}")
    lines.append(f"主文件夹未提交的改动：{quiet(project, 'git', 'status', '--porcelain')[1] or '无'}")
    lines.append(f"工作副本：{'、'.join(Path(w).name for w in snapshot(project, facts)['worktrees']) or '无'}")
    lines.append(f"分支：{quiet(project, 'git', 'branch', '--format=%(refname:short)')[1].replace(chr(10), '、')}")
    tmp = project / ".pal" / "tmp"
    lines.append(f".pal/tmp 下：{'、'.join(sorted(p.name for p in tmp.iterdir())) if tmp.is_dir() else '无'}")
    sid = scn["id"]
    if sid == "1":
        product = [f for f in main_files if f.endswith((".html", ".js", ".css", ".py")) and not f.startswith(".pal/")]
        untracked = quiet(project, "git", "ls-files", "--others", "--exclude-standard")[1].splitlines()
        product += [f for f in untracked if f.endswith((".html", ".js", ".css", ".py"))]
        lines.append(f"产品代码文件：{'、'.join(product) or '无'}")
    if sid == "2":
        now = quiet(project, "git", "rev-parse", "feature/export-csv")[1]
        lines.append(f"导出 CSV 的分支{'没有' if now == facts['branches']['export-csv'] else '有'}新的提交")
    if sid == "3":
        page = quiet(project, "git", "show", "main:index.html")[1]
        lines.append(f"主线 index.html：title {'是' if '<title>我的记账本</title>' in page else '不是'}「我的记账本」，"
                     f"大标题 {'是' if '<h1>我的记账本</h1>' in page else '不是'}「我的记账本」")
    if sid == "5":
        worktree = project / ".worktrees" / "report-export"
        if worktree.is_dir():
            lines.append(f"任务工作副本里的测试：{tests_pass(worktree)}")
            log = quiet(worktree, "git", "log", "--reverse", "--date=format:%H:%M:%S", "--format=@@%h %ad %s", "--name-only", "main..HEAD")[1]
            lines.append("任务分支的提交（时间、说明）和各自改的文件，从早到晚：")
            lines += ["  " + l for l in log.splitlines() if l.strip()]
    if sid == "6":
        page = quiet(project, "git", "show", "main:index.html")[1]
        yes = {True: "在", False: "不在"}
        lines.append(f"主线 index.html：导出按钮{yes['id=' + chr(34) + 'export' + chr(34) in page]}，"
                     f"深色模式按钮{yes['id=' + chr(34) + 'theme' + chr(34) in page]}")
        lines.append(f"主线上的 export.js：{yes['export.js' in main_files]}；theme.js：{yes['theme.js' in main_files]}")
        lines.append("主线最近的提交：")
        lines += ["  " + l for l in quiet(project, "git", "log", "--format=%h %s", "-8", "main")[1].splitlines()]
    if sid == "7":
        first = history[0]["state"] if history else {}
        lines.append(f"第 1 轮结束时：本机标记 {'、'.join(first.get('tags', [])) or '无'}；远程标记 {'、'.join(first.get('remote_tags', [])) or '无'}")
        final = snapshot(project, facts)
        lines.append(f"结束时：本机标记 {'、'.join(final['tags'])}；远程标记 {'、'.join(final['remote_tags'])}")
        remote_main = quiet(project, "git", "ls-remote", facts["remote"], "refs/heads/main")[1].split()[:1]
        same = remote_main == [quiet(project, "git", "rev-parse", "main")[1]]
        lines.append(f"远程主线和本机主线：{'一致' if same else '不一致'}")
        version = json.loads(quiet(project, "git", "show", "main:package.json")[1]).get("version")
        lines.append(f"package.json 版本：{version}")
        log = quiet(project, "git", "show", "main:CHANGELOG.md")[1]
        lines.append("CHANGELOG：")
        lines += ["  " + l for l in log.splitlines()[:16]]
    if sid == "8":
        code, text = quiet(project, "git", "show", "main:data/entries.json")
        try:
            now = json.loads(text) if code == 0 else None
        except ValueError:
            now = None
        verdict = f"正好剩下应保留的 {len(facts['keep'])} 条" if now == facts["keep"] else "和应保留的不一致：" + clip(text, 800)
        lines.append(f"主线 data/entries.json：{verdict}")
    if sid == "9":
        prompt = quiet(project, "git", "show", "main:ai/category-prompt.md")[1]
        cases = quiet(project, "git", "show", "main:ai-cases/cases.json")[1]
        lines.append(f"主线提示词的可选分类里{'有' if re.search(r'^可选分类：.*医疗', prompt, re.M) else '没有'}「医疗」；"
                     f"AI 案例里{'有' if '医疗' in cases else '没有'}医疗相关的案例")
        code, out = quiet(project, "node", "ai-cases/run.mjs")
        lines.append(f"在主线上跑全部 AI 案例：{'全部通过' if code == 0 else '有未通过'}")
        lines += ["  " + l for l in out.splitlines()[:10]]
        merged = project / ".pal" / "merged.json"
        flagged = any(m.get("ai") for m in json.loads(merged.read_text(encoding="utf-8"))) if merged.exists() else False
        lines.append(f"合并记录里这个任务标了改到 AI 行为：{'是' if flagged else '否'}")
    return lines


def hm(stamp):
    """'2026-10-11T18:21:37.694Z' -> '11:21:37' in local time."""
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
    except (ValueError, AttributeError):
        return "?"


def report_lines(text, n=3):
    lines = [clip(l, 160) for l in str(text or "").splitlines() if l.strip()]
    return lines[:n] + (["…"] if len(lines) > n else [])


def codex_subagents(home):
    """Each Codex sub-agent: role, the model and effort it really ran with, when it ran, and its final report."""
    rows = []
    for path in sorted((home / "sessions").rglob("*.jsonl")) if (home / "sessions").is_dir() else []:
        meta, context, last, report = {}, {}, "", ""
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            payload = d.get("payload") or {}
            last = d.get("timestamp") or last
            if d.get("type") == "session_meta" and not meta:
                meta = payload
            if d.get("type") == "turn_context":  # 子代理会话开头是继承来的主会话记录，以最后一次为准
                context = payload
            if d.get("type") == "response_item" and payload.get("type") == "message" and payload.get("role") == "assistant":
                report = " ".join(c.get("text", "") for c in payload.get("content", []) if isinstance(c, dict)) or report
        if not meta.get("agent_role"):
            continue
        rows.append(f"  {meta['agent_role']}（{meta.get('agent_nickname', '')}）：{context.get('model', '?')} · "
                    f"{context.get('effort') or context.get('reasoning_effort') or '?'}；{hm(meta.get('timestamp'))}–{hm(last)}")
        rows += [f"    汇报：{l}" for l in report_lines(report)]
    return rows


def claude_subagents(project):
    """Each Claude Code sub-agent: type, requested and actual model, effort, when it ran, its task and final report."""
    folder = Path.home() / ".claude" / "projects" / re.sub(r"[/_.]", "-", str(project))
    rows = []
    for path in sorted(folder.glob("*/subagents/*.jsonl"), key=lambda f: f.stat().st_mtime):
        meta_path = path.with_suffix(".meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        lines = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                lines.append(json.loads(line))
            except ValueError:
                continue
        said = [d for d in lines if d.get("type") == "assistant"]
        model = next((d["message"].get("model") for d in said if (d.get("message") or {}).get("model")), "?")
        effort = next((d.get("effort") for d in said if d.get("effort")), "?")
        texts = lambda d: " ".join(c.get("text", "") for c in (d.get("message") or {}).get("content", []) if isinstance(c, dict) and c.get("type") == "text")
        task = next((d["message"]["content"] for d in lines if d.get("type") == "user" and isinstance((d.get("message") or {}).get("content"), str)), "")
        report = next((texts(d) for d in reversed(said) if texts(d).strip()), "")
        rows.append(f"  {meta.get('agentType', '?')}（派发时要求 {meta.get('model', '默认')} · {meta.get('effort', '默认')}）：实际 {model} · {effort}；"
                    f"{hm(lines[0].get('timestamp') if lines else '')}–{hm(lines[-1].get('timestamp') if lines else '')}")
        rows.append(f"    任务：{clip(task.replace(chr(10), ' '), 160)}")
        rows += [f"    汇报：{l}" for l in report_lines(report)]
    return rows


def codex_usage(home):
    total = {}
    for path in (home / "sessions").rglob("*.jsonl") if (home / "sessions").is_dir() else []:
        last = None
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if '"token_count"' in line:
                try:
                    last = json.loads(line)["payload"]["info"]["total_token_usage"]
                except (ValueError, KeyError, TypeError):
                    pass
        for k, v in (last or {}).items():
            if isinstance(v, int):
                total[k] = total.get(k, 0) + v
    return total


# ---------- one scenario ----------

def write_rubric(out, scn):
    (out / "rubric.md").write_text("\n".join([f"# 场景 {scn['id']} · {scn['title']}：评分要点", "", f"看什么：{scn['look']}", ""] +
                                             [f"{n}. {r}" for n, r in enumerate(scn["rubric"], 1)]) + "\n", encoding="utf-8")


def write_results(out, scn, platform, history, state_lines, meta):
    base = meta["base"]  # 已解析成 /private/var/...，命令输出里也可能是 /var/...
    short = lambda text: str(text).replace(base, "〈试验〉").replace(base[len("/private"):] if base.startswith("/private/") else base, "〈试验〉")
    t = [f"# 场景 {scn['id']} · {scn['title']} · {'Codex' if platform == 'codex' else 'Claude Code'}", "",
         f"看什么：{scn['look']}。\"（模拟用户）\"开头的是事先写好的模拟回答，不是真人。", ""]
    for h in history:
        t += [f"## 第 {h['turn']} 轮", "", f"【用户】{h['user']}", ""]
        for kind, text, extra in h["events"]:
            body = str(text).splitlines() or [""]
            t.append(f"【{kind}】{body[0]}")
            t += [f"　　{l}" for l in body[1:]]
            t += [f"　　│ {l}" for l in extra or []]
        cs = h["state"]["cards"]
        t += ["", "【这一轮结束时的任务卡片】" + ("无" if not cs else "")]
        t += [f"　　{c.get('标题', c['id'])}：{c.get('类型', '')}·{c.get('阶段', '')}阶段·{c.get('状态', '')}"
              f"{'；等你做：' + c['等你做'] if c.get('等你做') else ''}{'；下一步：' + c['下一步'] if c.get('下一步') else ''}" for c in cs]
        t += [f"【用时】{h['seconds'] // 60} 分 {h['seconds'] % 60} 秒；退出码 {h['code']}", ""]
    head = f"〈试验〉指这个场景的临时文件夹：{base}，项目在其中的 project 文件夹。\n\n"
    lines = short("\n".join(t[4:]))
    (out / "transcript.md").write_text("\n".join(t[:4]) + "\n" + head + lines + "\n", encoding="utf-8")
    (out / "state.md").write_text(f"# 场景 {scn['id']} 结束时的状态（程序核对，供验收员参考）\n\n" + head + short("\n".join(state_lines)) + "\n",
                                  encoding="utf-8")
    write_rubric(out, scn)
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_scenario(platform, scn, opts):
    name = f"{platform}-{scn['id']}"
    out = opts.out / name
    meta_path = out / "meta.json"
    if meta_path.exists():
        old = json.loads(meta_path.read_text(encoding="utf-8"))
        if old.get("done") and not opts.force:
            say(f"{name} 已有结果，跳过（重跑加 --force）")
            return
        if Path(old.get("base", "")).name.startswith("pal-scenario-"):  # 上次的试验项目，重跑前删掉
            shutil.rmtree(old["base"], ignore_errors=True)
    shutil.rmtree(out, ignore_errors=True)
    (out / "raw").mkdir(parents=True)
    base = Path(tempfile.mkdtemp(prefix=f"pal-scenario-{name}-")).resolve()
    project = base / "project"
    project.mkdir()
    env = clean_env(base)
    meta = {"platform": platform, "id": scn["id"], "title": scn["title"], "base": str(base), "project": str(project),
            "started": datetime.now().isoformat(timespec="seconds"), "effort": opts.effort, "done": False}
    if platform == "codex":
        home = codex_home(base, env, opts.effort)
        env["CODEX_HOME"] = str(home)
        meta["model"] = (home / "config.toml").read_text(encoding="utf-8").splitlines()[0]
    else:
        meta["model"] = opts.claude_model
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    if platform in LIMITED:
        raise RuntimeError(f"{'Codex' if platform == 'codex' else 'Claude Code'} 的额度用完了（{LIMITED[platform]}），这个场景没跑；额度恢复后重跑")
    ctx = Ctx(env)
    facts = FIXTURES[scn["fixture"]](ctx, project)
    if platform == "codex" and scn["fixture"] != "empty":
        ctx.pal(project, "setup", "--platform", "codex")
    say(f"{name} 试验项目已搭好：{project}")
    if opts.dry_run:
        meta.update(done=True, dry_run=True, state=snapshot(project, facts))
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    history, fired, session, message, usage, cost = [], set(), None, scn["first"] + NOTE, {}, 0.0
    reason = f"到了最多 {scn['max_turns']} 轮"
    for turn in range(1, scn["max_turns"] + 1):
        started = time.time()
        raw = out / "raw" / f"turn-{turn}.jsonl"
        if platform == "codex":
            r = codex_turn(project, env, message, session, raw)
        else:
            r = claude_turn(project, env, message, session, raw, opts.claude_model, opts.effort)
        session = r["session"] or session
        cost += r.get("cost_usd", 0)
        for k, v in r["usage"].items():
            usage[k] = usage.get(k, 0) + v
        state = snapshot(project, facts)
        history.append({"turn": turn, "user": message, "events": r["events"], "final": r["final"], "code": r["code"],
                        "seconds": int(time.time() - started), "state": state})
        focus = next((c for c in state["cards"] if c.get("状态") == "等你"), state["cards"][0] if state["cards"] else {})
        say(f"{name} 第 {turn} 轮完成（{history[-1]['seconds'] // 60} 分钟，退出码 {r['code']}）"
            f"{'：' + focus.get('阶段', '') + '阶段·' + focus.get('状态', '') if focus else ''}")
        if r["error"] or (r["code"] != 0 and not r["final"]):
            reason = f"第 {turn} 轮出错，没走完：{clip(r['error'] or '退出码 ' + str(r['code']), 200)}。按环境问题处理，恢复后重跑这个场景"
            if "limit" in (r["error"] or "").lower():
                LIMITED[platform] = clip(r["error"], 200)
            break
        if stopped(scn, state, turn):
            reason = {"merged": "任务已合并", "acceptance": "任务到了验收、等用户", "released": "已发布到远程仓库"}[scn["stop"]]
            break
        if turn < scn["max_turns"]:
            message = reply(scn, state, turn + 1, fired, project)
    lines = [f"结束原因：{reason}", ""] + objective(scn, project, facts, history)
    if platform == "codex":
        agents = codex_subagents(Path(env["CODEX_HOME"]))
    else:
        agents = claude_subagents(project)
    lines += ["", "子代理（身份、实际用的模型和档位、起止时间、最后的汇报）：" + ("" if agents else "无")] + agents
    if platform == "codex":
        usage = codex_usage(Path(env["CODEX_HOME"])) or usage
    if (project / ".pal" / "config.json").exists():
        lines += ["", "结束时 pal status：", "```"] + [quiet(project, sys.executable, PAL, "status", env=env)[1], "```"]
    meta.update(done=True, finished=datetime.now().isoformat(timespec="seconds"), turns=len(history), stop=reason,
                seconds=sum(h["seconds"] for h in history), usage=usage, cost_usd=round(cost, 4) if cost else None, session=session)
    write_results(out, scn, platform, history, lines, meta)
    say(f"{name} 完成：{reason}；结果在 {out}")


# ---------- judging ----------

JUDGE = ("你是 coding-pal 的验收员，只读评判，不改任何项目文件。先完整读评判说明 {guide}，严格照做。\n"
         "要评判的场景结果就在当前文件夹（rubric.md、transcript.md、state.md、meta.json、raw/）。试验项目的路径在 meta.json 的 project；"
         "transcript.md 里的〈试验〉指 meta.json 的 base 文件夹。{extra}\n"
         "唯一允许写的文件：当前文件夹里的 verdict.json。写完回复一行：场景名、结论、未通过和无法验证的条数。")
JUDGE_EXTRA = {
    "codex": "子代理的情况看 state.md 的\"子代理\"一节（原始会话在 base 下的 codex-home/sessions/）；transcript.md 里的 collab_tool_call 只是主 Agent 等待子代理的记录。",
    "claude": "子代理的情况看 state.md 的\"子代理\"一节；transcript.md 里的\"派子代理\"是主 Agent 派发时传的参数。",
}


def judge_one(folder, home, effort):
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    scn = {s["id"]: s for s in json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))["scenarios"]}[meta["id"]]
    write_rubric(folder, scn)  # 按最新的评分要点评判
    prompt = JUDGE.format(guide=HERE / "judge.md", extra=JUDGE_EXTRA[meta["platform"]])
    scratch = Path(tempfile.mkdtemp(prefix="pal-judge-", dir=home.parent))  # 和评判用的配置一起删
    env = dict(clean_env(scratch), CODEX_HOME=str(home))
    args = [CODEX, "exec", "--json", "--skip-git-repo-check", "-s", "workspace-write", "-C", str(folder),
            "-c", f"model_reasoning_effort=\"{effort}\"", prompt]
    code, _ = stream(args, folder, env, folder / "judge-raw.jsonl")
    try:
        verdict = json.loads((folder / "verdict.json").read_text(encoding="utf-8"))
        say(f"{folder.name} 评判：{verdict['result']}（{sum(1 for i in verdict['items'] if i['result'] != '通过')} 条没通过或无法验证）")
    except (OSError, ValueError, KeyError) as e:
        raise RuntimeError(f"{folder.name} 的评判没写出有效的 verdict.json（退出码 {code}）：{e}")


def judge(opts, names):
    """Judge finished scenarios with a Codex checker (separate quota from the Claude Code runs)."""
    base = Path(tempfile.mkdtemp(prefix="pal-judge-home-"))
    home = base / "codex-home"
    home.mkdir()
    real = Path.home() / ".codex"
    (home / "auth.json").symlink_to(real / "auth.json")
    model = next((line for line in (real / "config.toml").read_text(encoding="utf-8").splitlines()
                  if line.strip().startswith(("model ", "model="))), 'model = "gpt-6-astra"')
    (home / "config.toml").write_text(model + "\n", encoding="utf-8")
    folders = [opts.out / n for n in names if (opts.out / n / "meta.json").exists()]
    folders = [f for f in folders if json.loads((f / "meta.json").read_text(encoding="utf-8")).get("done")
               and (opts.force or not (f / "verdict.json").exists())]
    say(f"开始评判：{'、'.join(f.name for f in folders) or '没有要评判的'}，同时 {opts.jobs} 个")
    failed = []
    with ThreadPoolExecutor(max_workers=max(1, opts.jobs)) as pool:
        for folder, future in [(f, pool.submit(judge_one, f, home, opts.judge_effort)) for f in folders]:
            try:
                future.result()
            except Exception as e:
                failed.append(folder.name)
                say(str(e))
    shutil.rmtree(base, ignore_errors=True)
    return 1 if failed else 0


def clean(out_dir):
    for meta_path in sorted(out_dir.glob("*/meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        base = Path(meta.get("base", ""))
        if meta.get("platform") == "claude" and meta.get("project"):
            for folder in [meta["project"]] + [str(p) for p in (Path(meta["project"]) / ".worktrees").glob("*")]:
                quiet(ROOT, "claude", "purge", "-y", folder)
        if base.name.startswith("pal-scenario-") and base.exists():
            shutil.rmtree(base, ignore_errors=True)
            say(f"已删除试验项目 {base}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="coding-pal 发布前场景测试（会真实调用模型）")
    parser.add_argument("--platform", choices=("codex", "claude", "both"), default="both")
    parser.add_argument("--only", help="只跑这些场景，按写的顺序开跑，例如 4,5,1")
    parser.add_argument("--jobs", type=int, default=4, help="同时跑几个场景")
    parser.add_argument("--out", type=Path, help="结果文件夹，默认 .pal/tmp/release-<插件版本>")
    parser.add_argument("--effort", default="high", help="主会话的推理档位")
    parser.add_argument("--claude-model", default="opus", help="Claude Code 主会话的模型")
    parser.add_argument("--force", action="store_true", help="已有结果也重跑")
    parser.add_argument("--dry-run", action="store_true", help="只搭试验项目，不调用模型")
    parser.add_argument("--clean", action="store_true", help="删掉这批结果对应的试验项目")
    parser.add_argument("--judge", action="store_true", help="用 Codex 的验收员评判已跑完的场景，写 verdict.json")
    parser.add_argument("--judge-effort", default="high", help="评判用的推理档位")
    opts = parser.parse_args(argv)
    version = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    opts.out = (opts.out or ROOT / ".pal" / "tmp" / f"release-{version}").resolve()
    if opts.clean:
        clean(opts.out)
        return 0
    if opts.judge:
        platforms = ["codex", "claude"] if opts.platform == "both" else [opts.platform]
        ids = opts.only.split(",") if opts.only else [s["id"] for s in json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))["scenarios"]]
        return judge(opts, [f"{p}-{i}" for p in platforms for i in ids])
    catalog = json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))["scenarios"]
    by_id = {s["id"]: s for s in catalog}
    chosen = [by_id[i] for i in opts.only.split(",")] if opts.only else catalog  # 按 --only 的顺序开跑，长的写在前面
    platforms = ["codex", "claude"] if opts.platform == "both" else [opts.platform]
    if "claude" in platforms and not opts.dry_run:
        scratch = Path(tempfile.mkdtemp(prefix="pal-auth-"))
        code, text = quiet(ROOT, "claude", "auth", "status", env=clean_env(scratch))
        shutil.rmtree(scratch, ignore_errors=True)
        if '"loggedIn": true' not in text:
            print("Claude Code 命令行还没登录：请用户在终端运行 claude auth login，再重跑。")
            return 5
    opts.out.mkdir(parents=True, exist_ok=True)
    say(f"开始：{'、'.join(platforms)} × 场景 {'、'.join(s['id'] for s in chosen)}，同时跑 {opts.jobs} 个；结果在 {opts.out}")
    jobs = [(p, s) for p in platforms for s in chosen]
    with ThreadPoolExecutor(max_workers=max(1, opts.jobs)) as pool:
        futures = {pool.submit(run_scenario, p, s, opts): f"{p}-{s['id']}" for p, s in jobs}
        failed = []
        for future, name in futures.items():
            try:
                future.result()
            except Exception as e:  # 一个场景出错不影响其他场景
                failed.append(name)
                say(f"{name} 没跑完：{type(e).__name__}: {e}")
    say("全部结束" + (f"；没跑完的：{'、'.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
