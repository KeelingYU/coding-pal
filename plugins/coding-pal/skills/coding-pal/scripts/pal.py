#!/usr/bin/env python3
"""pal: coding-pal's helper for the mechanical steps (tasks, board, merge, release, setup).

Usage: python3 pal.py <command> [options] [--json]
Needs Python 3.9+ and git; standard library only.
Exit codes: 0 ok, 1 unexpected error, 2 merge conflict, 3 merge queued, 4 project checks failed, 5 precondition not met.
"""
import sys

if sys.version_info < (3, 9):
    sys.stdout.write("pal 需要 Python 3.9 以上，当前是 %d.%d。\n下一步：安装新版 Python（macOS 可运行 xcode-select --install）。\n" % sys.version_info[:2])
    sys.exit(5)

import argparse
import importlib.util
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import time
from datetime import date, datetime
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_DIR / "templates"
BOARD_ASSETS = SKILL_DIR / "assets" / "board"
ROLES_DIR = SKILL_DIR / "roles"

TYPES = ("小改动", "普通需求", "复杂需求")
RISKS = ("普通", "高")
BASE_STAGES = {"小改动": ("改动", "验收", "合并"), "普通需求": ("需求", "界面", "方案", "实现", "验收", "合并")}
BASE_STAGES["复杂需求"] = BASE_STAGES["普通需求"]
RELEASE_STAGE = "发布"
DOING, WAITING, PAUSED, QUEUED, TO_RELEASE = "Agent 在做", "等你", "暂停", "排队", "待发布"
STATUSES = (DOING, WAITING, PAUSED, QUEUED, TO_RELEASE)
STATUS_STAGE = {QUEUED: "合并", TO_RELEASE: RELEASE_STAGE}  # statuses that exist in one stage only
FIELDS = ("标题", "类型", "风险", "阶段", "状态", "等你做", "下一步", "原因", "影响范围", "改到AI行为", "分支", "工作副本", "更新")
BODY_FIELDS = ("需求摘要", "方案摘要")
AUTO_FIELDS = ("更新",)
MAIN_FOLDER = "主文件夹"
BRANCH_PREFIX = {"小改动": "change", "普通需求": "feature", "复杂需求": "feature"}
TASK_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
LONG_DOCS = {"product": "PRODUCT.md", "architecture": "ARCHITECTURE.md", "decisions": "DECISIONS.md"}
INIT_MESSAGE = "chore: set up coding-pal"
BLOCK_START, BLOCK_END = "<!-- coding-pal:start -->", "<!-- coding-pal:end -->"

ROLES = ("implementer", "checker", "reviewer")
ROLE_LABELS = {"implementer": "实现员", "checker": "验收员", "reviewer": "评审员"}
DEFAULT_ROLES = {
    "implementer": {"claude": {"model": "sonnet", "effort": "medium"}, "codex": {"family": "sol", "effort": "medium"}},
    "checker": {"claude": {"model": "opus", "effort": "high"}, "codex": {"family": "astra", "effort": "high"}},
    "reviewer": {"claude": {"model": "opus", "effort": "high"}, "codex": {"family": "astra", "effort": "high"}},
}
DEFAULTS = {
    "schema": 1, "project": "", "main_branch": "main", "docs_dir": "docs", "checks": "",
    "auto_merge": True, "parallel_max": 3,
    "release": {"version_files": [], "tag_prefix": "v", "checks": "", "github_release": True, "post_release": ""},
    "ai_cases": {"cost": "none", "run": "", "last_full_check": ""},
    "roles": DEFAULT_ROLES,
}
PERMISSION_SIGNS = ("Operation not permitted", "Permission denied", "unable to create directory",
                    "could not lock config file", "Read-only file system")


class PalError(Exception):
    def __init__(self, code, what, next_step, data=None):
        super().__init__(what)
        self.code, self.what, self.next_step, self.data = code, what, next_step, data or {}


class Out:
    def __init__(self):
        self.lines, self.data, self.warnings = [], {}, []

    def line(self, text=""):
        self.lines.append(text)

    def warn(self, text):
        self.warnings.append(text)
        self.lines.append("注意：" + text)


# ---------- small helpers ----------

def now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


def posix(path):
    return Path(path).as_posix()


def pal_command(args=None):
    parts = [sys.executable, str(Path(__file__).resolve())] + list(sys.argv[1:] if args is None else args)
    return " ".join(shlex.quote(p) for p in parts)


def permission_error(target):
    return PalError(5, f"没有权限写 {posix(target)}（可能被沙箱拦下）。",
                    f"请授权在沙箱外运行这条命令（Codex 里请求提权），或请用户在终端运行：{pal_command()}")


def write(path, text):
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except PermissionError:
        raise permission_error(path)


def load_json(path, default):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise PalError(5, f"{posix(path)} 不是有效的 JSON：{e}", "改正这个文件后重试。")


def write_json(path, data):
    write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def deep_merge(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def pal_home():
    return Path(os.environ.get("PAL_HOME") or Path.home() / ".coding-pal").resolve()


def codex_home():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def git(args, cwd, check=True, input=None):
    try:
        p = subprocess.run(["git"] + list(args), cwd=str(cwd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", input=input)
    except FileNotFoundError:
        raise PalError(5, "没找到 git。", "先安装 git（macOS 可运行 xcode-select --install），再重试。")
    if check and p.returncode != 0:
        raise git_error(args, p)
    return p


def git_error(args, p):
    msg = (p.stderr or p.stdout).strip()
    last = msg.splitlines()[-1] if msg else f"退出码 {p.returncode}"
    if "tell me who you are" in msg or "unable to auto-detect email" in msg:
        return PalError(5, "git 还没设置用户名和邮箱，没法提交。",
                        "请用户运行：git config --global user.name \"名字\" 和 git config --global user.email \"邮箱\"，再重试。")
    if any(s in msg for s in PERMISSION_SIGNS) and "File exists" not in msg:
        return PalError(5, f"git {args[0]} 被拦下：{last}（多半是沙箱不允许改 .git）。",
                        f"请授权在沙箱外运行这条命令（Codex 里请求提权），或请用户在终端运行：{pal_command()}")
    return PalError(1, f"git {' '.join(args)} 失败：{last}", "看上面的错误；需要的话按同样步骤手动完成。")


def git_lines(args, cwd):
    return [l for l in git(args, cwd).stdout.splitlines() if l.strip()]


def is_ignored(root, rel):
    return git(["check-ignore", "-q", "--", rel], root, check=False).returncode == 0


def branch_exists(root, name):
    return git(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"], root, check=False).returncode == 0


def dirty_files(cwd, untracked=False):
    args = ["status", "--porcelain"] + ([] if untracked else ["--untracked-files=no"])
    return [l[3:] for l in git(args, cwd).stdout.splitlines() if l.strip()]


def current_branch(cwd):
    return git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd, check=False).stdout.strip()


def git_toplevel(cwd):
    p = git(["rev-parse", "--show-toplevel"], cwd, check=False)
    return Path(p.stdout.strip()).resolve() if p.returncode == 0 else None


def main_root(cwd):
    top = git_toplevel(cwd)
    if top is None:
        return None
    common = Path(git(["rev-parse", "--git-common-dir"], cwd).stdout.strip())
    common = (Path(cwd) / common).resolve() if not common.is_absolute() else common.resolve()
    return common.parent if common.name == ".git" else top


# ---------- project ----------

class Project:
    def __init__(self, root, cwd):
        self.root, self.cwd = root, cwd
        self.pal = root / ".pal"
        top = git_toplevel(cwd)
        self.task_here = top.name if top and top.parent == root / ".worktrees" else None
        self.project_cfg = load_json(self.pal / "config.json", {})
        self.cfg = deep_merge(deep_merge(DEFAULTS, load_json(pal_home() / "config.json", {})), self.project_cfg)
        self.cfg["docs_dir"] = str(self.cfg.get("docs_dir") or "docs").strip("/") or "docs"

    @property
    def name(self):
        return self.cfg.get("project") or self.root.name

    @property
    def releases(self):
        """Projects that publish versions get a final 发布 stage; set release.enabled, otherwise: has a git remote."""
        flag = self.cfg["release"].get("enabled")
        return flag if isinstance(flag, bool) else bool(git_lines(["remote"], self.root))

    @property
    def main(self):
        return self.cfg.get("main_branch") or "main"

    def task_dir(self, task):
        return self.pal / "tasks" / task


def load_project(require_init=True):
    cwd = Path.cwd().resolve()
    root = main_root(cwd)
    if root is None:
        raise PalError(5, f"这里不是 git 仓库：{posix(cwd)}", "需求类任务先运行 pal init 接入项目；小改动可以直接做。")
    p = Project(root, cwd)
    if require_init and not (p.pal / "config.json").exists():
        raise PalError(5, f"项目 {root.name} 还没接入 coding-pal。", "需求类任务先运行 pal init；小改动可以直接做。")
    return p


# ---------- cards ----------

def split_kv(line):
    m = re.match(r"\s*([^:：]+?)\s*[:：]\s?(.*)$", line)
    return (m.group(1).strip(), m.group(2).strip()) if m else (None, None)


def parse_card(text):
    head, body = {}, {}
    lines = text.splitlines()
    rest = lines
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), len(lines))
        for line in lines[1:end]:
            k, v = split_kv(line)
            if k:
                head[k] = v
        rest = lines[end + 1:]
    for line in rest:
        k, v = split_kv(line)
        if k in BODY_FIELDS:
            body[k] = v
    return head, body


def format_card(head, body):
    keys = [k for k in FIELDS if k in head and (head[k] or k not in ("原因",))]
    keys += [k for k in head if k not in FIELDS]
    out = ["---"] + [f"{k}: {head[k]}" for k in keys] + ["---"]
    out += [f"{k}: {body.get(k, '')}" for k in BODY_FIELDS]
    return "\n".join(out) + "\n"


def read_card(p, task):
    path = p.task_dir(task) / "card.md"
    if not path.exists():
        raise PalError(5, f"没有任务 {task}。", "用 pal status 看现有任务；新任务用 pal new 建。")
    return parse_card(path.read_text(encoding="utf-8"))


def save_card(p, task, head, body):
    head["更新"] = now()
    write(p.task_dir(task) / "card.md", format_card(head, body))


def all_cards(p):
    tasks_dir = p.pal / "tasks"
    cards = []
    if tasks_dir.is_dir():
        for d in sorted(tasks_dir.iterdir()):
            if (d / "card.md").is_file():
                head, body = parse_card((d / "card.md").read_text(encoding="utf-8"))
                cards.append((d.name, head, body))
    return cards


def stages_for(kind, releases):
    return BASE_STAGES.get(kind, BASE_STAGES["普通需求"]) + ((RELEASE_STAGE,) if releases else ())


def validate_card(head, releases):
    problems = []
    kind, status = head.get("类型"), head.get("状态")
    if kind not in TYPES:
        problems.append(f"类型只能是 {'、'.join(TYPES)}")
    elif head.get("阶段") not in stages_for(kind, releases):
        problems.append(f"{kind}的阶段只能是 {'、'.join(stages_for(kind, releases))}")
    if head.get("风险", "普通") not in RISKS:
        problems.append(f"风险只能是 {'、'.join(RISKS)}")
    if status not in STATUSES:
        problems.append(f"状态只能是 {'、'.join(STATUSES)}")
    elif status in STATUS_STAGE and head.get("阶段") != STATUS_STAGE[status]:
        problems.append(f"状态 {status} 只在{STATUS_STAGE[status]}阶段出现")
    if head.get("改到AI行为", "否") not in ("是", "否"):
        problems.append("改到AI行为只能是 是 或 否")
    if status == WAITING and not head.get("等你做"):
        problems.append("状态是 等你 时，要写 等你做（用户要做什么）")
    if status == PAUSED and not head.get("原因"):
        problems.append("状态是 暂停 时，要写 原因")
    return problems


def task_worktree(p, head):
    wt = head.get("工作副本", "")
    return None if not wt or wt == MAIN_FOLDER else (p.root / wt).resolve()


def paused_notice(p, task, out):
    try:
        head, _ = read_card(p, task)
    except PalError:
        return
    if head.get("状态") == PAUSED:
        out.warn(f"任务 {task} 已被标为暂停，原因：{head.get('原因', '（没写）')}（{head.get('更新', '')}）")


# ---------- changelog, todos, releases ----------

def changelog_unreleased(text):
    items, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            title = line[3:].strip().lower()
            inside = title.startswith("未发布") or title.startswith("unreleased")
            continue
        if inside and re.match(r"\s*[-*] ", line):
            items.append(re.sub(r"^\s*[-*] ", "", line).strip())
    return items


def read_changelog(root, rev=None):
    if rev:
        p = git(["show", f"{rev}:CHANGELOG.md"], root, check=False)
        return p.stdout if p.returncode == 0 else ""
    path = root / "CHANGELOG.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def read_todos(p):
    path = p.pal / "todo.md"
    todos = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"- (\d{4}-\d{2}-\d{2})｜(.*)$", line)
            if m:
                todos.append({"n": len(todos) + 1, "date": m.group(1), "text": m.group(2)})
    return todos


def write_todos(p, todos):
    lines = ["# 待开始", "", "用户提过、还没开始做的事。"] + [""] + [f"- {t['date']}｜{t['text']}" for t in todos]
    write(p.pal / "todo.md", "\n".join(lines) + "\n")


def read_merged(p):
    return load_json(p.pal / "merged.json", [])


def log_merge(p, task, head):
    entries = read_merged(p)
    entries.append({"task": task, "title": head.get("标题") or task, "type": head.get("类型", ""),
                    "ai": head.get("改到AI行为") == "是", "merged": now()})
    write_json(p.pal / "merged.json", entries)


def changelog_versions(text):
    versions, current = {}, None
    for line in text.splitlines():
        m = re.match(r"##\s*\[?v?(\d+\.\d+\.\d+)", line)
        if m:
            current = versions.setdefault(m.group(1), [])
        elif line.startswith("## "):
            current = None
        elif current is not None and re.match(r"\s*[-*] ", line):
            current.append(re.sub(r"^\s*[-*] ", "", line).strip())
    return versions


def version_tags(p):
    """Version tags, newest version first: [{tag, version, date, subject}] (subject only for annotated tags)."""
    prefix = p.cfg["release"].get("tag_prefix", "v")
    rows = git_lines(["for-each-ref", "--sort=-v:refname",
                      "--format=%(refname:short)%1f%(objecttype)%1f%(creatordate:short)%1f%(contents:subject)", "refs/tags"], p.root)
    out = []
    for row in rows:
        tag, kind, day, subject = (row.split("\x1f") + ["", "", "", ""])[:4]
        if re.match(rf"^{re.escape(prefix)}\d+\.\d+\.\d+$", tag):
            out.append({"tag": tag, "version": tag[len(prefix):], "date": day, "subject": subject.strip() if kind == "tag" else ""})
    return out


def recent_releases(p, limit=3):
    """Newest version tags with a title: the annotated tag's subject, else the version's first CHANGELOG line."""
    versions = changelog_versions(read_changelog(p.root))
    out = []
    for t in version_tags(p)[:limit]:
        title = re.sub(rf"^(?:{re.escape(t['tag'])}|{re.escape(t['version'])})\s*[:：·—-]*\s*", "", t["subject"])
        out.append({"tag": t["tag"], "date": t["date"], "title": title or (versions.get(t["version"]) or [""])[0]})
    return out


def has_product_files(p):
    own = {".gitignore", ".gitattributes", "AGENTS.md", "CLAUDE.md", "CHANGELOG.md"}
    own |= {f"{p.cfg['docs_dir']}/{name}" for name in LONG_DOCS.values()}
    files = git(["ls-files"], p.root, check=False).stdout.splitlines()
    return any(f not in own for f in files)


# ---------- board ----------

def file_uri(path):
    return Path(path).resolve().as_uri()


def inline_json(data):
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def read_lock(path):
    try:
        info = json.loads(Path(path).read_text(encoding="utf-8"))
        return info if isinstance(info, dict) else {}
    except (OSError, ValueError):
        return {}


def lock_info(p):
    path = p.pal / "merge.lock"
    if not path.exists():
        return None
    info = read_lock(path)
    return None if lock_is_stale(info, path) else info


def board_data(p):
    releases = p.releases
    lock = lock_info(p)
    tasks = []
    for task, head, body in all_cards(p):
        kind = head.get("类型", "普通需求")
        title = head.get("标题") or task
        report = p.pal / "reports" / task / "index.html"
        tasks.append({
            "id": task, "title": title, "type": kind, "risk": head.get("风险", "普通"),
            "stage": head.get("阶段", ""), "status": head.get("状态", ""), "waiting_for": head.get("等你做", ""),
            "next": head.get("下一步", ""), "reason": head.get("原因", ""), "scope": head.get("影响范围", ""),
            "ai": head.get("改到AI行为") == "是", "branch": head.get("分支", ""), "worktree": head.get("工作副本", ""),
            "updated": head.get("更新", ""), "resume": f"继续 {title}", "stages": list(stages_for(kind, releases)),
            "requirement": body.get("需求摘要", ""), "plan": body.get("方案摘要", ""),
            "report": file_uri(report) if head.get("阶段") == "验收" and report.is_file() else "",
        })
    tasks.sort(key=lambda t: t["updated"], reverse=True)
    queue = sorted((t for t in tasks if t["status"] == QUEUED), key=lambda t: t["updated"])
    merged = sorted(read_merged(p), key=lambda m: m.get("merged", ""))
    ai = None
    cost = p.cfg["ai_cases"].get("cost", "none")
    if cost in ("cheap", "expensive"):
        last = p.cfg["ai_cases"].get("last_full_check", "")
        ai = {"cost": cost, "last_full_check": last,
              "merged_since": sum(1 for m in merged if m.get("ai") and m.get("merged", "") > last)}
    return {
        "schema": 1, "name": p.name, "path": posix(p.root), "updated": now(),
        "board": file_uri(p.pal / "board" / "index.html"), "hub": file_uri(pal_home() / "index.html"),
        "releases_enabled": releases, "tasks": tasks, "merging": lock.get("task", "") if lock and not lock.get("release") else "",
        "queue": [t["id"] for t in queue], "todos": read_todos(p),
        "unreleased": changelog_unreleased(read_changelog(p.root)) if releases else [],
        "releases": recent_releases(p) if releases else [],
        "recent_merges": [] if releases else merged[-3:][::-1], "ai_check": ai,
    }


def render_page(title, data_scripts):
    tpl = (BOARD_ASSETS / "board.html").read_text(encoding="utf-8")
    css = (BOARD_ASSETS / "board.css").read_text(encoding="utf-8")
    app = (BOARD_ASSETS / "app.js").read_text(encoding="utf-8")
    return (tpl.replace("__TITLE__", title).replace("/*__CSS__*/", css)
            .replace("<!--__DATA__-->", data_scripts).replace("/*__APP__*/", app))


def refresh_board(p):
    data = board_data(p)
    folder = p.pal / "board"
    write(folder / "board.js", "(window.PAL_PROJECTS = window.PAL_PROJECTS || []).push(" +
          json.dumps(data, ensure_ascii=False, indent=1) + ");\n")
    scripts = [f"<script>window.PAL_CURRENT = {inline_json(posix(p.root))};</script>", '<script src="board.js"></script>',
               f'<script src="{file_uri(pal_home() / "projects.js")}"></script>']
    write(folder / "index.html", render_page(f"{p.name} · 任务看板", "\n".join(scripts)))
    return folder / "index.html"


def write_hub():
    """Hub page plus projects.js, the registry every board page reads to show all projects in its sidebar."""
    home = pal_home()
    entries = [dict(r, data=file_uri(Path(r["path"]) / ".pal" / "board" / "board.js"),
                    board=file_uri(Path(r["path"]) / ".pal" / "board" / "index.html"))
               for r in load_json(home / "projects.json", [])]
    write(home / "projects.js", "window.PAL_REGISTERED = " + inline_json(entries) + ";\n")
    scripts = [f"<script>window.PAL_HUB = {inline_json({'updated': now()})};</script>",
               f'<script src="{file_uri(home / "projects.js")}"></script>']
    write(home / "index.html", render_page("所有项目 · coding-pal", "\n".join(scripts)))
    return home / "index.html"


def register_project(p):
    home = pal_home()
    registered = [r for r in load_json(home / "projects.json", []) if r.get("path") != posix(p.root)]
    registered.append({"name": p.name, "path": posix(p.root)})
    write_json(home / "projects.json", registered)
    return write_hub()


def open_page(path, out):
    opener = {"darwin": ["open"], "win32": ["cmd", "/c", "start", ""]}.get(sys.platform, ["xdg-open"])
    try:
        ok = subprocess.run(opener + [str(path)], capture_output=True).returncode == 0
    except OSError:
        ok = False
    if ok:
        out.line(f"已在浏览器打开：{posix(path)}")
    else:
        out.line("没能打开浏览器（可能被沙箱拦下），请用户运行：")
        out.line(f"  {' '.join(opener)} {shlex.quote(posix(path))}")


# ---------- roles and models ----------

def read_role(role):
    path = ROLES_DIR / f"{role}.md"
    if not path.exists():
        raise PalError(1, f"技能里缺少角色说明 {posix(path)}。", "重新安装 coding-pal 插件。")
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    return meta, body


def parse_frontmatter(text):
    meta = {}
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            for line in text[4:end].splitlines():
                k, v = split_kv(line)
                if k:
                    meta[k] = v
            text = text[end + 5:]
    return meta, text.lstrip("\n")


def codex_models():
    cache = load_json(codex_home() / "models_cache.json", {})
    return [m["slug"] for m in cache.get("models", []) if m.get("slug") and m.get("visibility") != "hide"]


def latest_in_family(slugs, family):
    best, best_ver = None, ()
    for slug in slugs:
        m = re.match(rf"^gpt-(\d+(?:\.\d+)*)-{re.escape(family or '')}$", slug)
        if m:
            ver = tuple(int(x) for x in m.group(1).split("."))
            if ver > best_ver:
                best, best_ver = slug, ver
    return best


def codex_role(role, roles):
    rc = roles[role]["codex"]
    return {"model": rc.get("model") or latest_in_family(codex_models(), rc.get("family")),
            "effort": rc.get("effort", ""), "family": rc.get("family", "")}


def toml_str(s):
    return json.dumps(s, ensure_ascii=False)


def codex_toml(role, setting):
    meta, body = read_role(role)
    lines = ["# 由 coding-pal 的 pal setup 生成，不要手改；改模型或档位后重新运行 pal setup。",
             f"name = {toml_str('coding_pal_' + role)}",
             f"description = {toml_str(meta.get('description', ROLE_LABELS[role]))}"]
    if setting["model"]:
        lines.append(f"model = {toml_str(setting['model'])}")
    if setting["effort"]:
        lines.append(f"model_reasoning_effort = {toml_str(setting['effort'])}")
    body = body.replace("\\", "\\\\").replace('"""', '""\\"')
    lines.append('developer_instructions = """\n' + body.rstrip("\n") + '\n"""')
    return "\n".join(lines) + "\n"


def codex_agent_path(role):
    return codex_home() / "agents" / f"coding_pal_{role}.toml"


def global_roles():
    return deep_merge(DEFAULTS, load_json(pal_home() / "config.json", {}))["roles"]


def codex_agents_state():
    """'ok' / 'outdated' / 'missing' for the TOML files pal setup writes."""
    roles = global_roles()
    states = []
    for role in ROLES:
        path = codex_agent_path(role)
        if not path.exists():
            states.append("missing")
        else:
            states.append("ok" if path.read_text(encoding="utf-8") == codex_toml(role, codex_role(role, roles)) else "outdated")
    return "missing" if "missing" in states else ("outdated" if "outdated" in states else "ok")


# ---------- commands ----------

def cmd_setup(a, out):
    out.line(f"Python {platform.python_version()}（{sys.executable}）")
    home = pal_home()
    if not (home / "config.json").exists():
        write_json(home / "config.json", {"schema": 1, "auto_merge": True, "parallel_max": 3, "roles": DEFAULT_ROLES})
    if not (home / "projects.json").exists():
        write_json(home / "projects.json", [])
    write_hub()
    out.line(f"已准备 {posix(home)}（全局设置、项目登记、汇总首页 index.html）")
    if a.platform in ("codex", "both"):
        if a.platform == "both" and not codex_home().exists():
            out.line("没找到 Codex 配置目录，跳过 Codex 子代理设置。")
        else:
            roles = global_roles()
            written = []
            for role in ROLES:
                setting = codex_role(role, roles)
                write(codex_agent_path(role), codex_toml(role, setting))
                model = setting["model"] or f"继承主会话（没找到 {setting['family']} 系列）"
                written.append(f"  {ROLE_LABELS[role]}：{model} · {setting['effort']}")
            out.line(f"已写 Codex 子代理设置：{posix(codex_home() / 'agents')}")
            out.lines += written
    if a.platform in ("claude", "both"):
        out.line("Claude Code：子代理随插件提供，不需要额外设置。")
    out.data["home"] = posix(home)


def cmd_init(a, out):
    cwd = Path.cwd().resolve()
    if main_root(cwd) is None:
        p_init = git(["init", "-b", a.main_branch or "main"], cwd, check=False)
        if p_init.returncode != 0:
            reason = " ".join((p_init.stderr or "").strip().splitlines()[-1:])
            raise PalError(5, f"这个文件夹还不是 git 仓库，新建仓库被拦下：{reason}",
                           "请授权在沙箱外运行这条命令（Codex 里请求提权），或请用户在终端运行：git init -b main，再运行 pal init。")
        out.line("已新建 git 仓库")
    root = main_root(cwd)
    before = dirty_files(root)
    p = Project(root, cwd)
    created = []
    if not (p.pal / "config.json").exists():
        branch = a.main_branch or current_branch(root) or "main"
        cfg = {"schema": 1, "project": a.name or root.name, "main_branch": branch, "docs_dir": a.docs_dir or "docs",
               "checks": "", "release": dict(DEFAULTS["release"], enabled=bool(git_lines(["remote"], root))),
               "ai_cases": DEFAULTS["ai_cases"]}
        write_json(p.pal / "config.json", cfg)
        p = Project(root, cwd)
        out.line(f"已建 .pal/config.json（主线 {p.main}，长期文档目录 {p.cfg['docs_dir']}）")
        out.line(f"发布版本：{'要' if p.releases else '不要'}（按{'有' if p.releases else '没有'}远程仓库先定的）。"
                 "请和用户确认；不对就改 .pal/config.json 的 release.enabled")
    else:
        if a.name or a.docs_dir:
            p.project_cfg.update({k: v for k, v in (("project", a.name), ("docs_dir", a.docs_dir)) if v})
            write_json(p.pal / "config.json", p.project_cfg)
            p = Project(root, cwd)
        out.line("已有 .pal/config.json，保持不变")
    here = current_branch(root)
    if here and here != p.main:
        raise PalError(5, f"主文件夹现在在分支 {here}，不是主线 {p.main}。", f"先切回主线（git switch {p.main}），或用 --main-branch {here} 说明主线就是它。")

    docs = p.cfg["docs_dir"]
    touched = []
    block = (TEMPLATES / "AGENTS-block.md").read_text(encoding="utf-8").replace("{docs}", docs).strip()
    agents = root / "AGENTS.md"
    old = agents.read_text(encoding="utf-8") if agents.exists() else ""
    if BLOCK_START in old and BLOCK_END in old:
        new = old[:old.index(BLOCK_START)] + block + old[old.index(BLOCK_END) + len(BLOCK_END):]
    elif old:
        new = old.rstrip("\n") + "\n\n" + block + "\n"
    else:
        new = "# 项目规则\n\n" + block + "\n"
    if new != old:
        write(agents, new)
        touched.append("AGENTS.md")
    claude = root / "CLAUDE.md"
    if claude.exists():
        text = claude.read_text(encoding="utf-8")
        if "@AGENTS.md" not in [l.strip() for l in text.splitlines()]:
            write(claude, "@AGENTS.md\n\n" + text)
            touched.append("CLAUDE.md")
    ignore = root / ".gitignore"
    lines = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
    missing = [x for x in (".pal/", ".worktrees/") if x not in [l.strip() for l in lines]]
    if missing:
        write(ignore, "\n".join(lines + missing) + "\n")
        touched.append(".gitignore")
    attributes = root / ".gitattributes"  # parallel tasks each add lines under 「未发布」: keep both sides instead of a conflict
    lines = attributes.read_text(encoding="utf-8").splitlines() if attributes.exists() else []
    if not any(l.split()[:1] == ["CHANGELOG.md"] for l in lines):
        write(attributes, "\n".join(lines + ["CHANGELOG.md merge=union"]) + "\n")
        touched.append(".gitattributes")
    for template, target in [(f, f"{docs}/{f}") for f in LONG_DOCS.values()] + [("CHANGELOG.md", "CHANGELOG.md")]:
        if not (root / target).exists():
            write(root / target, (TEMPLATES / template).read_text(encoding="utf-8"))
            created.append(target)
    touched += created
    for t in touched:
        out.line(f"已更新 {t}" if t not in created else f"已建 {t}")

    to_commit = [t for t in touched if not is_ignored(root, t)]
    if to_commit:
        clash = [t for t in to_commit if t in before and t not in created]
        if clash:
            out.warn(f"{'、'.join(clash)} 原本就有未提交的改动，没有自动提交 coding-pal 的改动；请检查后一起提交。")
        else:
            git(["add", "--"] + to_commit, root)
            git(["commit", "-q", "-m", INIT_MESSAGE, "--"] + to_commit, root)
            out.line(f"已提交：{'、'.join(to_commit)}")

    try:
        hub = register_project(p)
        out.line(f"已登记到汇总首页：{posix(hub)}")
    except PalError as e:
        out.warn(f"没能登记到汇总首页（{e.what}）。下一步：{e.next_step}")
    board = refresh_board(p)
    out.line(f"看板：{posix(board)}")
    memory_tips(p, out)
    out.data.update({"root": posix(root), "board": posix(board), "touched": touched})


def memory_tips(p, out):
    settings = p.root / ".claude" / "settings.local.json"
    try:
        current = load_json(settings, {})
    except PalError:
        current = {}
    if current.get("autoMemoryEnabled") is not False:
        out.line("记忆设置（问用户，推荐关闭，免得旧习惯带进新会话）：")
        if settings.exists():
            out.line(f"  Claude Code：在 {posix(settings)} 里加 \"autoMemoryEnabled\": false（需要用户自己改）")
        else:
            out.line("  Claude Code：请用户在终端运行：")
            out.line(f"    mkdir -p {shlex.quote(posix(settings.parent))} && "
                     f"printf '{{\\n  \"autoMemoryEnabled\": false\\n}}\\n' > {shlex.quote(posix(settings))}")
    config = codex_home() / "config.toml"
    if config.exists():
        text = config.read_text(encoding="utf-8", errors="replace")
        if not re.search(r"(?m)^\s*(memories|use_memories)\s*=\s*false", text):
            out.line("  Codex：只能全局关闭记忆：桌面版设置里关闭，或在 ~/.codex/config.toml 的 [features] 写 memories = false")


def cmd_new(a, out):
    p = load_project()
    task = a.task
    if not TASK_NAME.match(task):
        raise PalError(5, f"任务名 {task} 不合规。", "只用小写英文字母、数字和连字符，最长 40 个字符，例如 agent-workbench。")
    if p.task_dir(task).exists():
        raise PalError(5, f"任务 {task} 已经存在。", "换一个名字，或用 pal status 看它的状态。")
    kind, risk = a.type, a.risk
    on_main = [t for t, h, _ in all_cards(p) if h.get("状态") != TO_RELEASE and task_worktree(p, h) is None]
    if on_main:
        raise PalError(5, f"任务 {on_main[0]} 正在主文件夹里做（项目的第一个版本），它合并前不能开新任务。",
                       f"把新的事记进待开始（pal todo add \"...\"），或并进任务 {on_main[0]}。")
    unborn = git(["rev-parse", "--verify", "--quiet", f"refs/heads/{p.main}"], p.root, check=False).returncode != 0
    use_wt = not unborn and has_product_files(p)  # only the first version is built in the main folder
    if use_wt:
        branch = a.branch or f"{BRANCH_PREFIX[kind]}/{task}"
        rel = f".worktrees/{task}"
        if branch_exists(p.root, branch):
            raise PalError(5, f"分支 {branch} 已经存在。", "换一个任务名，或用 --branch 指定别的分支名。")
        if (p.root / rel).exists():
            raise PalError(5, f"{rel} 已经存在。", "换一个任务名，或先确认那个文件夹还要不要。")
        if not is_ignored(p.root, rel):
            raise PalError(5, ".worktrees/ 没有被 git 忽略。", "先运行 pal init（会把 .pal/ 和 .worktrees/ 加进 .gitignore）。")
        git(["worktree", "add", "-q", "-b", branch, rel, p.main], p.root)
    else:
        branch, rel = p.main, MAIN_FOLDER
    head = {"标题": a.title or task, "类型": kind, "风险": risk, "阶段": stages_for(kind, p.releases)[0], "状态": DOING,
            "等你做": "", "下一步": "", "影响范围": "", "改到AI行为": "否", "分支": branch, "工作副本": rel}
    save_card(p, task, head, {})
    if kind != "小改动":
        for name in ("requirement.md", "plan.md"):
            text = (TEMPLATES / name).read_text(encoding="utf-8").replace("{标题}", head["标题"])
            write(p.task_dir(task) / name, text)
    board = refresh_board(p)
    out.line(f"已建任务 {task}（{kind}{'，高风险' if risk == '高' else ''}）：{head['标题']}")
    out.line(f"  任务文件夹：{posix(p.task_dir(task))}")
    out.line(f"  分支：{branch}　工作副本：{rel if rel == MAIN_FOLDER else posix(p.root / rel)}")
    out.line(f"  临时文件放：{posix(p.pal / 'tmp' / task)}")
    out.line(f"看板已更新：{posix(board)}")
    out.data.update({"task": task, "branch": branch, "worktree": rel, "task_dir": posix(p.task_dir(task))})


def cmd_set(a, out):
    p = load_project()
    merged = [m for m in read_merged(p) if m.get("task") == a.task]
    if merged and not (p.task_dir(a.task) / "card.md").exists():
        out.line(f"任务 {a.task} 已经在 {merged[-1].get('merged', '')} 合并，任务结束了，不用再更新卡片。")
        out.data.update({"task": a.task, "merged": merged[-1].get("merged", "")})
        return
    head, body = read_card(p, a.task)
    changes = {}
    for pair in a.pairs:
        if "=" in pair:
            k, v = (x.strip() for x in pair.split("=", 1))
        else:
            k, v = split_kv(pair)
        if not k:
            raise PalError(5, f"看不懂 {pair}。", "写成 键=值，例如 状态=等你 等你做=看需求说明。")
        if k in AUTO_FIELDS or (k not in FIELDS and k not in BODY_FIELDS):
            raise PalError(5, f"不能设置 {k}。", f"能设置的有：{'、'.join([f for f in FIELDS if f not in AUTO_FIELDS] + list(BODY_FIELDS))}。")
        if k == "状态":
            v = {s.replace(" ", ""): s for s in STATUSES}.get(v.replace(" ", ""), v)
        changes[k] = v
    old_status = head.get("状态")
    for k, v in changes.items():
        (body if k in BODY_FIELDS else head)[k] = v
    if head.get("状态") != WAITING and "等你做" not in changes:
        head["等你做"] = ""
    if head.get("状态") != PAUSED and "原因" not in changes:
        head.pop("原因", None)
    problems = validate_card(head, p.releases)
    if problems:
        raise PalError(5, f"没有更新 {a.task}：{'；'.join(problems)}。", "改正后重新运行 pal set。")
    if old_status == PAUSED and head.get("状态") == PAUSED and "原因" not in changes:
        paused_notice(p, a.task, out)
    save_card(p, a.task, head, body)
    refresh_board(p)
    summary = "，".join(f"{k}={v}" for k, v in changes.items())
    out.line(f"已更新 {a.task}：{summary}")
    out.data.update({"task": a.task, "card": head})


def cmd_status(a, out):
    p = load_project()
    data = board_data(p)
    refresh_board(p)
    head = f"项目 {p.name}（{posix(p.root)}）· {'要发布版本' if p.releases else '不发布版本'}"
    if p.task_here:
        out.line(f"{head}，当前在任务 {p.task_here} 的工作副本里")
        paused_notice(p, p.task_here, out)
    else:
        out.line(head)
    groups = {s: [t for t in data["tasks"] if t["status"] == s] for s in STATUSES}

    def tag(t):
        return f"［{t['type']}·{t['stage']}阶段{'·高风险' if t['risk'] == '高' else ''}］"

    out.line(f"等你处理（{len(groups[WAITING])}）")
    for t in groups[WAITING]:
        out.line(f"  · {t['title']}{tag(t)}：{t['waiting_for']}　（{t['id']}）")
    out.line(f"Agent 在做（{len(groups[DOING])}）")
    for t in groups[DOING]:
        out.line(f"  · {t['title']}{tag(t)}{'　下一步：' + t['next'] if t['next'] else ''}　（{t['id']}）")
    titles = {t["id"]: t["title"] for t in data["tasks"]}
    if data["merging"] or data["queue"]:
        now_merging = f"正在合并 {titles.get(data['merging'], data['merging'])}" if data["merging"] else "现在没有正在合并的任务"
        queued = "、".join(titles.get(q, q) for q in data["queue"])
        out.line(f"合并（同一时间只合并一个）：{now_merging}；排队 {len(data['queue'])} 个{'（' + queued + '）' if queued else ''}")
    if groups[PAUSED]:
        out.line(f"暂停（{len(groups[PAUSED])}）")
        for t in groups[PAUSED]:
            out.line(f"  · {t['title']}：{t['reason']}　（{t['id']}）")
    if p.releases:
        ship = groups[TO_RELEASE]
        out.line(f"待发布（{len(ship)}）{'：' + '、'.join(t['title'] for t in ship) if ship else ''}"
                 f"{'；CHANGELOG「未发布」' + str(len(data['unreleased'])) + ' 条' if data['unreleased'] else ''}")
    out.line(f"待开始（{len(data['todos'])}）")
    for t in data["todos"]:
        out.line(f"  {t['n']}. {t['date']}｜{t['text']}")
    hints = []
    if codex_home().exists() and any(codex_agent_path(r).exists() for r in ROLES) and codex_agents_state() == "outdated":
        hints.append("Codex 子代理设置不是最新（可能有新模型）：运行 pal models 查看，pal setup --platform codex 更新")
    ai = data["ai_check"]
    if ai and ai["merged_since"]:
        hints.append(f"AI 全面检查：上次 {ai['last_full_check'] or '还没做过'}，之后合并了 {ai['merged_since']} 个改到 AI 行为的任务")
    for h in hints:
        out.line("提示：" + h)
    out.line(f"看板：{posix(p.pal / 'board' / 'index.html')}")
    out.data.update({"project": p.name, "root": posix(p.root), "releases": p.releases, "task_here": p.task_here, "tasks": data["tasks"],
                     "merging": data["merging"], "queue": data["queue"], "todos": data["todos"],
                     "unreleased": data["unreleased"], "hints": hints})


def cmd_todo(a, out):
    p = load_project()
    todos = read_todos(p)
    if a.action == "add":
        if not a.text:
            raise PalError(5, "待开始的内容是空的。", "写成 pal todo add \"以后要做的事\"。")
        todos.append({"n": len(todos) + 1, "date": date.today().isoformat(), "text": " ".join(a.text)})
        write_todos(p, todos)
        out.line(f"已记到待开始（第 {len(todos)} 条）：{todos[-1]['text']}")
    elif a.action == "done":
        try:
            n = int(a.text[0])
            item = todos.pop(n - 1)
        except (IndexError, ValueError):
            raise PalError(5, "待开始里没有这个序号。", "先运行 pal todo list 看序号。")
        write_todos(p, todos)
        out.line(f"已从待开始移除：{item['text']}")
    else:
        for t in todos:
            out.line(f"{t['n']}. {t['date']}｜{t['text']}")
        if not todos:
            out.line("待开始是空的。")
    refresh_board(p)
    out.data["todos"] = read_todos(p)


def lock_is_stale(info, path):
    pid = info.get("pid")
    age = time.time() - path.stat().st_mtime
    if not isinstance(pid, int) or pid <= 0:
        return age > 60
    if os.name == "nt":
        return age > 2 * 3600
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def acquire_lock(p, task, wait_minutes, release=False):
    """Take the project's merge lock (releases take it too); returns None, or the holder's info when still busy."""
    path = p.pal / "merge.lock"
    deadline = time.time() + wait_minutes * 60
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            info = read_lock(path)
            if lock_is_stale(info, path):
                path.unlink(missing_ok=True)
                continue
            if time.time() >= deadline:
                return info
            time.sleep(5)
            continue
        except PermissionError:
            raise permission_error(path)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"task": task, "pid": os.getpid(), "started": now(), "release": release}, f, ensure_ascii=False)
        return None


def cmd_merge(a, out):
    p = load_project()
    task = a.task
    head, body = read_card(p, task)
    paused_notice(p, task, out)
    if head.get("状态") == TO_RELEASE:
        raise PalError(5, f"任务 {task} 已经合并过，在等发布。", "要发布时让用户说\"发布\"；用 pal status 看其他任务。")
    here = current_branch(p.root)
    if here != p.main:
        raise PalError(5, f"主文件夹现在在分支 {here or '（游离状态）'}，不在主线 {p.main} 上。", f"先在主文件夹切回主线：git switch {p.main}，再重试。")
    dirty = dirty_files(p.root)
    if dirty:
        raise PalError(5, f"主文件夹有未提交的改动：{'、'.join(dirty[:5])}{' 等' if len(dirty) > 5 else ''}。", "先提交或撤回这些改动，再重试。")
    wt = task_worktree(p, head)
    branch = head.get("分支", "")
    on_main = wt is None or branch in ("", p.main)
    if not on_main:
        if not wt.is_dir() or not branch_exists(p.root, branch):
            raise PalError(5, f"找不到任务 {task} 的工作副本 {posix(wt)} 或分支 {branch}。", "检查卡片里的分支和工作副本是否写对（pal set 可改）。")
        wt_dirty = dirty_files(wt, untracked=True)
        if wt_dirty:
            raise PalError(5, f"工作副本里还有未提交的改动：{'、'.join(wt_dirty[:5])}{' 等' if len(wt_dirty) > 5 else ''}。", "先在工作副本里提交（或撤回）这些改动，再重试。")

    busy = acquire_lock(p, task, a.wait)
    head.pop("原因", None)
    if busy is not None:
        other = "发布版本" if busy.get("release") else "合并 " + busy.get("task", "别的任务")
        head.update({"阶段": "合并", "状态": QUEUED, "等你做": "", "下一步": f"等{other}完"})
        save_card(p, task, head, body)
        refresh_board(p)
        raise PalError(3, f"正在{other}（{busy.get('started', '')} 开始），同一时间只合并一个任务，{task} 已排队。",
                       f"等它完成后重新运行 pal merge {task}，或加 --wait 10 最多自动等 10 分钟。", {"merging": busy.get("task", "")})
    head.update({"阶段": "合并", "状态": DOING, "等你做": "", "下一步": "正在合并"})
    save_card(p, task, head, body)
    refresh_board(p)
    overlap, changelog_ok = [], True
    try:
        if on_main:
            run_checks(p, p.root, task)
        else:
            own = git_lines(["rev-list", "--first-parent", f"{p.main}..{branch}"], p.root)
            start = git(["rev-parse", f"{own[-1]}^"], p.root).stdout.strip() if own else p.main
            main_changed = set(git_lines(["diff", "--name-only", f"{start}..{p.main}"], p.root))
            task_changed = set(git_lines(["diff", "--name-only", f"{p.main}...{branch}"], p.root))
            overlap = sorted((main_changed & task_changed) - {"CHANGELOG.md"})  # every task adds a line there; that never needs a re-check
            added = set(changelog_unreleased(read_changelog(p.root, branch))) - set(changelog_unreleased(read_changelog(p.root, p.main)))
            changelog_ok = bool(added)
            if git(["merge-base", "--is-ancestor", p.main, branch], p.root, check=False).returncode != 0:
                m = git(["merge", "--no-edit", "-q", p.main], wt, check=False)
                if m.returncode != 0:
                    conflicts = git_lines(["diff", "--name-only", "--diff-filter=U"], wt)
                    git(["merge", "--abort"], wt, check=False)
                    if not conflicts:
                        raise git_error(["merge", p.main], m)
                    raise PalError(2, f"把主线合进 {task} 时有冲突：{'、'.join(conflicts)}。已撤回这次合并，主线没有变化。",
                                   f"在工作副本里运行 git merge {p.main}，解决冲突并提交后，重新运行 pal merge {task}。",
                                   {"conflicts": conflicts})
                out.line(f"已把主线最新内容合进 {branch}")
            run_checks(p, wt, task)
            ff = git(["merge", "--ff-only", "-q", branch], p.root, check=False)
            if ff.returncode != 0:
                raise PalError(5, f"没能把 {branch} 合进主线：{' '.join((ff.stderr or ff.stdout).strip().splitlines()[-1:])}",
                               f"主线可能在合并期间又有了新提交，或主文件夹有挡路的文件；处理后重新运行 pal merge {task}。")
            git(["worktree", "remove", "--force", str(wt)], p.root)
            git(["branch", "-d", branch], p.root, check=False)
            if branch_exists(p.root, branch):
                out.warn(f"分支 {branch} 没删掉，可以之后手动删除。")
    except Exception as e:
        (p.pal / "merge.lock").unlink(missing_ok=True)
        head["下一步"] = {2: "合并有冲突：在工作副本里解决后重新合并", 4: "项目检查没通过：修好后重新合并"}.get(
            getattr(e, "code", 1), "合并没完成：看对话里的说明")
        save_card(p, task, head, body)
        refresh_board(p)
        raise
    finally:
        (p.pal / "merge.lock").unlink(missing_ok=True)
    for sub in ("tmp", "reports"):
        shutil.rmtree(p.pal / sub / task, ignore_errors=True)
    log_merge(p, task, head)
    if p.releases:
        head.update({"阶段": RELEASE_STAGE, "状态": TO_RELEASE, "等你做": "", "下一步": ""})
        save_card(p, task, head, body)
    else:
        shutil.rmtree(p.task_dir(task), ignore_errors=True)
    refresh_board(p)
    out.line(f"已合并 {task}{'（本来就在主线上改的）' if on_main else '：' + branch + ' → ' + p.main}。这是本机合并。")
    if not on_main:
        out.line("已删除工作副本、任务分支和这个任务的临时文件、验收网页。")
    out.line("任务进入发布阶段，等用户说\"发布\"时和其他待发布的任务一起发布。" if p.releases
             else "这个项目不发布版本，任务已完成，看板上记在\"最近合并\"。")
    if overlap:
        out.warn(f"主线在本任务开始后也改过这些文件：{'、'.join(overlap)}。判断已验收的效果要不要复查，并告诉用户。")
    if not changelog_ok:
        out.warn("本任务没有在 CHANGELOG「未发布」里加条目，记得补上用户看得懂的变化。")
    out.data.update({"task": task, "overlap": overlap, "changelog_entry": changelog_ok, "released_later": p.releases})


def run_checks(p, cwd, task):
    cmd = p.cfg.get("checks", "")
    if not cmd:
        return
    proc = subprocess.run(cmd, shell=True, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-30:])
        raise PalError(4, f"项目检查没通过（{cmd}，退出码 {proc.returncode}），主线没有变化：\n{tail}",
                       f"修好后重新运行 pal merge {task}。", {"checks_output": tail})


def cmd_drop(a, out):
    p = load_project()
    task = a.task
    head, _ = read_card(p, task)
    wt = task_worktree(p, head)
    branch = head.get("分支", "")
    kept = []
    if wt is not None and branch not in ("", p.main):
        if wt.is_dir():
            if dirty_files(wt, untracked=True):
                git(["add", "-A"], wt)
                git(["commit", "-q", "-m", f"wip({task}): saved by pal drop"], wt)
                out.line("工作副本里未提交的改动已先提交保存")
            git(["worktree", "remove", "--force", str(wt)], p.root)
        if branch_exists(p.root, branch):
            unique = int(git(["rev-list", "--count", f"{p.main}..{branch}"], p.root).stdout.strip() or 0)
            if unique:
                backup = f"backup/{task}-{date.today().strftime('%Y%m%d')}"
                if branch_exists(p.root, backup):
                    backup += datetime.now().strftime("-%H%M%S")
                git(["branch", backup, branch], p.root)
                kept.append(backup)
            git(["branch", "-D", branch], p.root, check=False)
    elif wt is None:
        out.line("这个任务是直接在主线上改的：已提交到主线的改动还在，需要撤回的话另外处理。")
    for sub in ("tmp", "reports", "tasks"):
        shutil.rmtree(p.pal / sub / task, ignore_errors=True)
    refresh_board(p)
    out.line(f"已放弃任务 {task}：删除了工作副本、任务分支、任务文件夹和临时文件。")
    out.line(f"保留了：备份分支 {'、'.join(kept)}（含没合并的提交，可随时找回）" if kept else "保留了：无（没有主线以外的提交）")
    out.data.update({"task": task, "kept": kept})


# ---------- release ----------

LEVELS = ("major", "minor", "patch")
VERSION_KEY = re.compile(r"""(["']?)(?:__)?version(?:__)?\1\s*[:=]\s*["'](\d+\.\d+\.\d+)["']""")
BARE_VERSION = re.compile(r"\s*v?(\d+\.\d+\.\d+)\s*")


def file_version(text):
    """(version, span) of the first version setting in a version file, or (None, None)."""
    m = VERSION_KEY.search(text)
    if m:
        return m.group(2), m.span(2)
    m = BARE_VERSION.fullmatch(text)
    return (m.group(1), m.span(1)) if m else (None, None)


def bump(version, level):
    major, minor, patch = (int(x) for x in version.split("."))
    return {"major": f"{major + 1}.0.0", "minor": f"{major}.{minor + 1}.0", "patch": f"{major}.{minor}.{patch + 1}"}[level]


def current_version(p):
    """The version to count from: newest version tag, else the first version file, else 0.0.0."""
    tags = version_tags(p)
    if tags:
        return tags[0]["version"]
    for rel in p.cfg["release"].get("version_files") or []:
        path = p.root / rel
        found = file_version(path.read_text(encoding="utf-8"))[0] if path.is_file() else None
        if found:
            return found
    return "0.0.0"


def release_tasks(p):
    return [(task, head.get("标题") or task) for task, head, _ in all_cards(p) if head.get("状态") == TO_RELEASE]


def read_notes(path):
    path = Path(path)
    if not path.is_file():
        raise PalError(5, f"找不到发布说明 {posix(path)}。", "先把发布说明写进这个文件（第一行是标题），再重跑。")
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    title = re.sub(r"^#+\s*", "", lines[0]).strip() if lines else ""
    if not title:
        raise PalError(5, f"发布说明 {posix(path)} 是空的。", "第一行写标题（版本号＋主要变化），下面写摘要和分类的变化，再重跑。")
    return title, "\n".join(lines[1:]).strip()


def release_changelog(text, version):
    """Move what is under 「未发布」 to a new '## version - date' section; the 未发布 heading stays, empty."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith("## ") and re.match(r"(未发布|unreleased)", l[3:].strip(), re.I)), None)
    if start is None:
        raise PalError(5, "CHANGELOG.md 里没有「## 未发布」一节。", "补上这一节和这次的变化，提交后再重跑。")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    items = [l for l in lines[start + 1:end] if l.strip()]
    section = [lines[start], "", f"## {version} - {date.today().isoformat()}"] + items + [""]
    return "\n".join(lines[:start] + section + lines[end:]).rstrip("\n") + "\n"


def release_plan(p, out):
    current = current_version(p)
    last = recent_releases(p, 1)
    tasks = release_tasks(p)
    unreleased = changelog_unreleased(read_changelog(p.root))
    lock = lock_info(p)
    merging = lock.get("task", "") if lock and not lock.get("release") else ""
    queued = [head.get("标题") or task for task, head, _ in all_cards(p) if head.get("状态") == QUEUED]
    remotes = git_lines(["remote"], p.root)
    rc = p.cfg["release"]
    state = load_json(p.pal / "tmp" / "release" / "state.json", None)
    if state:
        out.warn(f"上次发布 {state['tag']} 没做完：重跑上次那条 pal release 命令会接着做，跳过已完成的步骤。")
    out.line(f"待发布的任务（{len(tasks)}）{'：' + '、'.join(title for _, title in tasks) if tasks else ''}")
    out.line(f"CHANGELOG「未发布」（{len(unreleased)} 条）")
    out.lines += [f"  - {u}" for u in unreleased]
    if not unreleased:
        out.warn("CHANGELOG「未发布」是空的：发布前要写上用户看得懂的变化。")
    out.line(f"上次发布：{last[0]['tag']}（{last[0]['date']}）{last[0]['title']}" if last else f"还没发布过，从 {current} 算起")
    nxt = {level: bump(current, level) for level in LEVELS}
    out.line(f"这次的版本号：只有兼容的修正 → {nxt['patch']}；有兼容的新能力 → {nxt['minor']}；有不兼容的变化 → {nxt['major']}")
    if merging or queued:
        names = ([f"正在合并的{merging}"] if merging else []) + [f"排队的{q}" for q in queued]
        out.line(f"这次不含：{'、'.join(names)}（还没合并；要带上就等它合并完再发布）")
    out.line(f"发布检查：{rc.get('checks') or '没有设置（.pal/config.json 的 release.checks）'}")
    out.line(f"发布说明写到：{posix(p.pal / 'tmp' / 'release' / 'notes.md')}（第一行是标题）")
    out.line(("有远程仓库：发布时加 --push" + ("，要建 GitHub Release 再加 --gh" if rc.get("github_release") else ""))
             if remotes else "没有远程仓库：只在本机提交和打标记")
    out.data.update({"tasks": [t for t, _ in tasks], "unreleased": unreleased, "current": current, "last": last[0] if last else None,
                     "next": nxt, "merging": merging, "queued": queued, "checks": rc.get("checks", ""), "remote": bool(remotes),
                     "github_release": bool(rc.get("github_release")), "resume": state})


def release_commit(p, version, tag, out):
    """Set the version files, move CHANGELOG「未发布」into the version and commit; reuse the commit an earlier run made."""
    subject = f"release: {tag}"
    for row in git_lines(["log", "-n", "30", "--format=%H%x1f%s", p.main], p.root):
        sha, s = row.split("\x1f", 1)
        if s == subject:
            out.line(f"发布提交上次已经做好（{sha[:7]}）")
            return sha, False
    changed = {}
    for rel in p.cfg["release"].get("version_files") or []:
        path = p.root / rel
        if not path.is_file():
            raise PalError(5, f"找不到版本文件 {rel}。", "改正 .pal/config.json 的 release.version_files 后重跑。")
        text = path.read_text(encoding="utf-8")
        found, span = file_version(text)
        if not found:
            raise PalError(5, f"在 {rel} 里找不到版本号。",
                           "版本文件里要有 \"version\": \"1.2.3\" 或 version = \"1.2.3\" 这样的一行，或整个文件就是版本号；改好后重跑。")
        changed[rel] = (text, text[:span[0]] + version + text[span[1]:])
    text = read_changelog(p.root)
    changed["CHANGELOG.md"] = (text, release_changelog(text, version))
    try:
        for rel, (_, new) in changed.items():
            write(p.root / rel, new)
        git(["add", "--"] + list(changed), p.root)
        git(["commit", "-q", "-m", subject, "--"] + list(changed), p.root)
    except Exception:
        for rel, (old, _) in changed.items():
            write(p.root / rel, old)
        git(["reset", "-q", "--"] + list(changed), p.root, check=False)
        raise
    files = [rel for rel in changed if rel != "CHANGELOG.md"]
    out.line((f"已改版本号（{'、'.join(files)}），" if files else "") + f"已把 CHANGELOG「未发布」归入 {version}，已提交")
    return git(["rev-parse", "HEAD"], p.root).stdout.strip(), True


def github_release(p, tag, title, body, out):
    """Create the GitHub Release unless it exists, then read it back; never overwrites what is already there."""
    if not shutil.which("gh"):
        raise PalError(5, "没找到 gh（GitHub 命令行工具），没法建 GitHub Release。", "请用户安装 gh 并登录（gh auth login），再重跑同一条命令。")

    def gh(*args):
        return subprocess.run(["gh"] + list(args), cwd=str(p.root), capture_output=True, text=True, encoding="utf-8", errors="replace")

    def failed(what, proc):
        last = " ".join((proc.stderr or proc.stdout).strip().splitlines()[-1:])
        return PalError(5, f"{what}：{last}",
                        f"确认 gh 已登录、标记已推送（加 --push）后重跑同一条命令；被沙箱拦下时请授权在沙箱外运行，或请用户在终端运行：{pal_command()}")

    fields = ["--json", "tagName,name,body,isDraft,url"]
    view = gh("release", "view", tag, *fields)
    if view.returncode != 0:
        notes = p.pal / "tmp" / "release" / "github-notes.md"
        write(notes, body + "\n")
        made = gh("release", "create", tag, "--verify-tag", "--title", title, "--notes-file", str(notes))
        if made.returncode != 0:
            raise failed("没能建 GitHub Release", made)
        view = gh("release", "view", tag, *fields)
        if view.returncode != 0:
            raise failed("建好了 GitHub Release，但读不回来", view)
        out.line(f"已建 GitHub Release {tag}")
    else:
        out.line(f"GitHub Release {tag} 已经有了，没有覆盖")
    info = json.loads(view.stdout)
    if info.get("name") != title or (info.get("body") or "").strip() != body.strip() or info.get("isDraft"):
        out.warn(f"读回的 GitHub Release 和发布说明不一致（可能有人手动改过），没有覆盖，请核对：{info.get('url', '')}")
    else:
        out.line(f"已读回核对，标题和正文一致：{info.get('url', '')}")
    return info.get("url", "")


def cmd_release(a, out):
    p = load_project()
    if not p.releases:
        raise PalError(5, "这个项目设置为不发布版本。", "要发布的话，先和用户确认，再把 .pal/config.json 的 release.enabled 改成 true。")
    if a.plan:
        return release_plan(p, out)
    if bool(a.level) == bool(a.version) or not a.notes:
        raise PalError(5, "正式发布要写 --level（或 --version）和 --notes。",
                       "先运行 pal release --plan 定好版本、写好发布说明，再运行 pal release --level minor --notes <说明文件>。")
    if a.version and not re.fullmatch(r"\d+\.\d+\.\d+", a.version):
        raise PalError(5, f"版本号 {a.version} 要写成三段数字，例如 2.0.0。", "改正后重跑。")
    title, body = read_notes(a.notes)
    here = current_branch(p.root)
    if here != p.main:
        raise PalError(5, f"主文件夹现在在分支 {here or '（游离状态）'}，不在主线 {p.main} 上。", f"先在主文件夹切回主线：git switch {p.main}，再重试。")
    busy = acquire_lock(p, "发布", a.wait, release=True)
    if busy is not None:
        other = "发布版本" if busy.get("release") else "合并 " + busy.get("task", "别的任务")
        raise PalError(3, f"正在{other}（{busy.get('started', '')} 开始），等它完成再发布。", "重跑同一条命令；可以加 --wait 20 多等一会儿。")
    try:
        release_steps(p, a, title, body, out)
    finally:
        (p.pal / "merge.lock").unlink(missing_ok=True)


def release_steps(p, a, title, body, out):
    dirty = dirty_files(p.root)
    if dirty:
        raise PalError(5, f"主文件夹有未提交的改动：{'、'.join(dirty[:5])}{' 等' if len(dirty) > 5 else ''}。", "先提交或撤回这些改动，再重试。")
    folder = p.pal / "tmp" / "release"
    state = load_json(folder / "state.json", None)
    if state:
        version, tag = state["version"], state["tag"]
        out.line(f"接着做上次没做完的发布 {tag}")
    else:
        if not changelog_unreleased(read_changelog(p.root)):
            raise PalError(5, "CHANGELOG「未发布」是空的，没有可发布的变化。", "在 CHANGELOG.md 的「未发布」写上用户看得懂的变化并提交到主线，再重跑。")
        current = current_version(p)
        version = a.version or bump(current, a.level)
        tag = p.cfg["release"].get("tag_prefix", "v") + version
        if git(["rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"], p.root, check=False).returncode == 0:
            raise PalError(5, f"标记 {tag} 已经存在。", "运行 pal release --plan 看上次的版本，换一个版本号。")
        state = {"version": version, "tag": tag, "tasks": [t for t, _ in release_tasks(p)], "done": []}
        write_json(folder / "state.json", state)
        out.line(f"发布 {tag}（上次是 {current}）")
    if version not in title:
        title = f"{tag} {title}"
    if git(["rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"], p.root, check=False).returncode == 0:
        out.line(f"标记 {tag} 已经有了，跳过改版本号、归入变更记录和打标记")
    else:
        sha, made = release_commit(p, version, tag, out)
        if made:  # tasks merged after an interrupted run went into this commit too
            state["tasks"] = [t for t, _ in release_tasks(p)]
            write_json(folder / "state.json", state)
        # verbatim：发布说明里 "## 新增" 这样的标题不能被 git 当成注释删掉
        git(["tag", "-a", "--cleanup=verbatim", tag, "-F", "-", sha], p.root, input=title + ("\n\n" + body if body else "") + "\n")
        out.line(f"已打标记 {tag}：{title}")
    if a.push:
        remotes = git_lines(["remote"], p.root)
        if not remotes:
            raise PalError(5, "没有远程仓库，没法推送。", "去掉 --push 只在本机发布，或先请用户设置远程仓库。")
        remote = "origin" if "origin" in remotes else remotes[0]
        git(["push", "-q", remote, f"refs/heads/{p.main}", f"refs/tags/{tag}"], p.root)
        out.line(f"已把主线和标记 {tag} 推送到 {remote}")
    else:
        out.line("没有推送（没加 --push）：只在本机")
    url = github_release(p, tag, title, body, out) if a.gh else ""
    cmd = p.cfg["release"].get("post_release", "")
    if cmd and "post_release" not in state["done"]:
        proc = subprocess.run(cmd, shell=True, cwd=str(p.root), env=dict(os.environ, PAL_VERSION=version, PAL_TAG=tag),
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-30:])
            raise PalError(4, f"发布后的命令没成功（{cmd}，退出码 {proc.returncode}）：\n{tail}", "修好后重跑同一条发布命令，已完成的步骤会跳过。")
        state["done"].append("post_release")
        write_json(folder / "state.json", state)
        out.line(f"已运行发布后的命令：{cmd}")
    titles = []
    for task in state["tasks"]:
        if (p.task_dir(task) / "card.md").exists() and read_card(p, task)[0].get("状态") == TO_RELEASE:
            titles.append(read_card(p, task)[0].get("标题") or task)
            shutil.rmtree(p.task_dir(task), ignore_errors=True)
    shutil.rmtree(folder, ignore_errors=True)
    refresh_board(p)
    out.line(f"已发布 {tag}{'，包含：' + '、'.join(titles) if titles else ''}。这些任务已从任务列表移到\"最近发布\"。")
    out.line(f"发布说明已写进标记 {tag}（git show {tag} 可看）{'和 GitHub Release' if url else ''}；临时文件夹里的说明文件已删除，不要再把它当链接给用户。")
    out.data.update({"version": version, "tag": tag, "title": title, "pushed": bool(a.push), "release_url": url, "tasks": state["tasks"]})


def cmd_board(a, out):
    p = load_project()
    board = refresh_board(p)
    out.line(f"看板：{posix(board)}")
    target = board
    if a.hub:
        try:
            target = register_project(p)
            out.line(f"汇总首页：{posix(target)}")
        except PalError as e:
            out.warn(f"没能更新汇总首页（{e.what}）。下一步：{e.next_step}")
    if a.open:
        open_page(target, out)
    out.data["board"] = posix(board)


def load_renderer():
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("render_acceptance", Path(__file__).resolve().parent / "render_acceptance.py")
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    return renderer


def cmd_report(a, out):
    p = load_project()
    task = a.task
    head, _ = read_card(p, task)
    data = p.task_dir(task) / "acceptance.json"
    if not data.exists():
        raise PalError(5, f"还没有验收数据 {posix(data)}。",
                       f"照 {posix(TEMPLATES / 'acceptance.json')} 写好它，再运行 pal report {task}。")
    evidence = p.pal / "tmp" / task
    try:
        evidence.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        raise permission_error(evidence)
    wt = task_worktree(p, head) or p.root
    commit = git(["rev-parse", "--short", "HEAD"], wt, check=False).stdout.strip() if wt.is_dir() else ""
    revision = " @ ".join(x for x in (head.get("分支", ""), commit) if x)
    renderer = load_renderer()
    try:
        page = renderer.generate(data, evidence, p.pal / "reports" / task, project=p.name, revision=revision)
    except renderer.ReportError as e:
        raise PalError(5, f"验收数据有问题，没有生成网页：{e}",
                       f"改正 {posix(data)}（证据文件放在 {posix(evidence)}）后，重新运行 pal report {task}。")
    except PermissionError as e:
        raise permission_error(Path(e.filename or p.pal / "reports"))
    refresh_board(p)
    out.line(f"已生成验收网页：{posix(page)}")
    out.line("先自己打开核对一遍，再把位置和\"你要做的事\"告诉用户。")
    out.data.update({"task": task, "page": posix(page)})


def cmd_path(a, out):
    p = load_project()
    kind = a.kind
    if kind in ("task", "tmp"):
        if not a.task:
            raise PalError(5, f"pal path {kind} 要写任务名。", f"例如：pal path {kind} agent-workbench。")
        path = p.task_dir(a.task) if kind == "task" else p.pal / "tmp" / a.task
    elif kind == "board":
        path = p.pal / "board" / "index.html"
    else:
        rel = "CHANGELOG.md" if kind == "changelog" else f"{p.cfg['docs_dir']}/{LONG_DOCS[kind]}"
        base = p.root
        task = a.task or p.task_here
        if task and (p.task_dir(task) / "card.md").exists():
            wt = task_worktree(p, read_card(p, task)[0])
            if wt is not None and wt.is_dir() and not is_ignored(p.root, rel):
                base = wt
        path = base / rel
    out.line(posix(path))
    out.data["path"] = posix(path)


def cmd_models(a, out):
    try:
        roles = load_project(require_init=False).cfg["roles"]
    except PalError:
        roles = global_roles()
    out.line("角色　　Claude Code　　　Codex")
    table = {}
    for role in ROLES:
        cc = roles[role]["claude"]
        cx = codex_role(role, roles)
        cx_model = cx["model"] or f"继承主会话（没找到 {cx['family']} 系列）"
        out.line(f"{ROLE_LABELS[role]}　{cc.get('model', 'inherit')} · {cc.get('effort', '')}　　{cx_model} · {cx['effort']}")
        table[role] = {"claude": cc, "codex": cx}
    state = codex_agents_state() if codex_home().exists() else "no-codex"
    out.line({"ok": "Codex 子代理设置：已是最新",
              "outdated": "Codex 子代理设置：需要更新（运行 pal setup --platform codex）",
              "missing": "Codex 子代理设置：还没设置（运行 pal setup --platform codex）",
              "no-codex": "没找到 Codex 配置目录"}[state])
    if roles != global_roles():
        out.line("本项目改过角色设置：派发子代理时由主 Agent 按上表传入模型和档位。")
    out.data.update({"roles": table, "codex_agents": state})


# ---------- entry ----------

class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise PalError(5, f"用法不对：{message}", f"运行 {self.prog} -h 查看用法。")


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="输出 JSON")
    parser = Parser(prog="pal", description="coding-pal 的机械步骤小工具", parents=[common])
    sub = parser.add_subparsers(dest="command", parser_class=Parser)
    s = sub.add_parser("setup", parents=[common], help="本机第一次使用")
    s.add_argument("--platform", choices=("claude", "codex", "both"), default="both")
    s = sub.add_parser("init", parents=[common], help="项目第一次使用")
    s.add_argument("--name")
    s.add_argument("--docs-dir")
    s.add_argument("--main-branch")
    s = sub.add_parser("new", parents=[common], help="新建任务")
    s.add_argument("task")
    s.add_argument("--type", choices=TYPES, required=True)
    s.add_argument("--title")
    s.add_argument("--risk", choices=RISKS, default="普通")
    s.add_argument("--branch")
    s = sub.add_parser("set", parents=[common], help="更新任务卡片")
    s.add_argument("task")
    s.add_argument("pairs", nargs="+")
    sub.add_parser("status", parents=[common], help="看当前任务")
    s = sub.add_parser("todo", parents=[common], help="待办")
    s.add_argument("action", choices=("add", "done", "list"))
    s.add_argument("text", nargs="*")
    s = sub.add_parser("merge", parents=[common], help="合并已验收的任务")
    s.add_argument("task")
    s.add_argument("--wait", type=float, default=0, help="被占用时最多等几分钟")
    s = sub.add_parser("drop", parents=[common], help="放弃任务")
    s.add_argument("task")
    s = sub.add_parser("board", parents=[common], help="重新生成看板")
    s.add_argument("--open", action="store_true")
    s.add_argument("--hub", action="store_true")
    s = sub.add_parser("release", parents=[common], help="发布版本")
    s.add_argument("--plan", action="store_true", help="只看这次会发布什么")
    s.add_argument("--level", choices=LEVELS)
    s.add_argument("--version", help="项目有自己的版本规则时，直接写版本号，例如 2.0.0")
    s.add_argument("--notes", help="发布说明文件，第一行是标题")
    s.add_argument("--push", action="store_true", help="推送主线和标记")
    s.add_argument("--gh", action="store_true", help="建 GitHub Release 并读回核对")
    s.add_argument("--wait", type=float, default=10, help="有任务正在合并时最多等几分钟")
    s = sub.add_parser("report", parents=[common], help="生成验收网页")
    s.add_argument("task")
    s = sub.add_parser("path", parents=[common], help="打印文件位置")
    s.add_argument("kind", choices=("product", "architecture", "decisions", "changelog", "task", "tmp", "board"))
    s.add_argument("task", nargs="?")
    sub.add_parser("models", parents=[common], help="子代理的模型和档位")
    return parser


COMMANDS = {"setup": cmd_setup, "init": cmd_init, "new": cmd_new, "set": cmd_set, "status": cmd_status,
            "todo": cmd_todo, "merge": cmd_merge, "drop": cmd_drop, "release": cmd_release, "board": cmd_board, "report": cmd_report,
            "path": cmd_path, "models": cmd_models}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    argv = sys.argv[1:] if argv is None else argv
    as_json = "--json" in argv
    out = Out()
    try:
        args = build_parser().parse_args(argv)
        if not args.command:
            raise PalError(5, "没有指定命令。", "运行 pal -h 查看能用的命令。")
        COMMANDS[args.command](args, out)
    except PalError as e:
        if as_json:
            print(json.dumps({"ok": False, "code": e.code, "error": e.what, "next": e.next_step, **e.data,
                              "warnings": out.warnings}, ensure_ascii=False))
        else:
            print("\n".join(out.lines + [e.what, "下一步：" + e.next_step]))
        return e.code
    except Exception as e:  # unexpected: say what happened and how to continue
        what = f"pal 出了意外错误：{type(e).__name__}: {e}"
        nxt = "把这条错误告诉开发者；同样的步骤可以先手动完成。"
        print(json.dumps({"ok": False, "code": 1, "error": what, "next": nxt}, ensure_ascii=False) if as_json
              else f"{what}\n下一步：{nxt}")
        return 1
    if as_json:
        print(json.dumps({"ok": True, **out.data, "warnings": out.warnings, "lines": out.lines}, ensure_ascii=False))
    else:
        print("\n".join(out.lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
