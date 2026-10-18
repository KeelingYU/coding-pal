#!/usr/bin/env python3
"""coding-pal 的程序检查：不调用模型，几十秒内跑完，每个任务都跑。

用法：
  python3 scripts/verify.py [program] [--install claude|codex|all]   程序检查（--install 另跑安装测试）
  python3 scripts/verify.py agents                                   由 skills/coding-pal/roles/ 重新生成 agents/*.md
  python3 scripts/verify.py catalog X.Y.Z --catalog <目录仓库> [--write]
      核对目录仓库（pal-plugins）两份清单是否登记了本仓库的 vX.Y.Z；--write 先把登记改成它
  python3 scripts/verify.py scenarios --platform codex|claude|both [...]
      发布前的 9 个场景，会真实调用模型；参数见 tests/scenarios/run.py -h
要能在 Python 3.9 下运行。
"""
import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.dont_write_bytecode = True
try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "coding-pal"
SKILL = PLUGIN / "skills" / "coding-pal"
BUDGETS = ROOT / "scripts" / "budgets.json"

EXPECTED = {
    "stages": {"requirements.md", "ui.md", "plan.md", "build.md", "accept.md", "small-change.md", "release.md"},
    "references": {"writing.md", "verification.md"},
    "roles": {"implementer.md", "checker.md", "reviewer.md"},
    "templates": {"requirement.md", "plan.md", "PRODUCT.md", "ARCHITECTURE.md", "DECISIONS.md", "CHANGELOG.md",
                  "AGENTS-block.md", "acceptance.json"},
}
REQUIRED = ["scripts/pal.py", "scripts/render_acceptance.py", "assets/acceptance-report/index.html",
            "assets/acceptance-report/styles.css", "assets/acceptance-report/app.js",
            "assets/board/board.html", "assets/board/board.css", "assets/board/app.js"]
CATALOGS = {"claude": ".claude-plugin/marketplace.json", "codex": ".agents/plugins/marketplace.json"}
REF = re.compile(r"(?<![\w./-])((?:stages|references|roles|templates|scripts|assets)/[A-Za-z0-9_./-]+)")
TESTS = "tests/unit"


class Report:
    def __init__(self):
        self.failed = 0

    def ok(self, text):
        print("  通过：" + text)

    def bad(self, text):
        self.failed += 1
        print("  失败：" + text)

    def note(self, text):
        print("  说明：" + text)


def load_pal():
    spec = importlib.util.spec_from_file_location("pal", SKILL / "scripts" / "pal.py")
    pal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pal)
    return pal


def skill_texts():
    """The rule text the budget covers: SKILL.md plus the stages, references, roles and templates folders."""
    files = [SKILL / "SKILL.md"] + sorted(p for d in EXPECTED for p in (SKILL / d).glob("*")
                                          if p.is_file() and not p.name.startswith("."))
    return {p.relative_to(SKILL).as_posix(): p.read_text(encoding="utf-8") for p in files if p.exists()}


def agent_text(pal, role):
    meta, body = pal.read_role(role)
    claude = pal.DEFAULT_ROLES[role]["claude"]
    head = ["---", f"name: {meta['name']}", f"description: {meta['description']}",
            f"model: {claude['model']}", f"effort: {claude['effort']}", "---"]
    return "\n".join(head) + "\n" + body


def run(args, timeout=600):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        return subprocess.run([str(a) for a in args], cwd=str(ROOT), env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 1, "", f"超过 {timeout} 秒没结束")


def last_lines(p, n=5):
    return " / ".join((p.stdout + p.stderr).strip().splitlines()[-n:])


# ---------- checks ----------

def check_structure(r, pal):
    print("[1] 插件结构和清单")
    for folder, names in sorted(EXPECTED.items()):
        have = {p.name for p in (SKILL / folder).glob("*") if p.is_file() and not p.name.startswith(".")}
        missing, extra = sorted(names - have), sorted(have - names)
        if missing or extra:
            r.bad(f"{folder}/ 缺 {'、'.join(missing) or '无'}，多 {'、'.join(extra) or '无'}（只能是设计里的这些文件）")
        else:
            r.ok(f"{folder}/ 正好 {len(names)} 个文件")
    missing = [rel for rel in REQUIRED if not (SKILL / rel).is_file()]
    if missing:
        r.bad("缺少 " + "、".join(missing))
    else:
        r.ok("pal、验收网页生成器和两套网页资源都在")
    agents = {p.name for p in (PLUGIN / "agents").glob("*.md")}
    want = {f"{role}.md" for role in pal.ROLES}
    if agents == want:
        r.ok("agents/ 有三个子代理定义")
    else:
        r.bad(f"agents/ 应该正好是 {'、'.join(sorted(want))}，实际是 {'、'.join(sorted(agents)) or '空'}")
    meta, _ = pal.parse_frontmatter((SKILL / "SKILL.md").read_text(encoding="utf-8"))
    if meta.get("name") == "coding-pal" and meta.get("description"):
        r.ok("SKILL.md 头部有 name 和 description")
    else:
        r.bad("SKILL.md 头部要有 name: coding-pal 和 description")
    manifests = {}
    for kind in (".claude-plugin", ".codex-plugin"):
        path = PLUGIN / kind / "plugin.json"
        try:
            manifests[kind] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            r.bad(f"{kind}/plugin.json 读不出来：{e}")
    if len(manifests) == 2:
        names = {m.get("name") for m in manifests.values()}
        versions = {m.get("version") for m in manifests.values()}
        if names == {"coding-pal"} and len(versions) == 1:
            r.ok(f"两份插件清单名字和版本一致（{versions.pop()}）")
        else:
            r.bad(f"两份插件清单不一致：名字 {names}，版本 {versions}")


def references_of(rel, texts):
    return {m.group(1).rstrip("./") for m in REF.finditer(texts[rel])}


def check_refs(r, texts):
    print("[2] 文件引用")
    missing = sorted(f"{rel} 引用的 {target}" for rel in texts for target in references_of(rel, texts)
                     if not (SKILL / target).exists())
    for m in missing:
        r.bad(m + " 不存在")
    if not missing:
        r.ok(f"{len(texts)} 个规则文件里引用的文件都存在")


def closure(start, texts):
    """Files read along with `start`: follow references and templates, but not other stages."""
    seen, todo = set(), list(start)
    while todo:
        rel = todo.pop()
        if rel in seen or rel not in texts:
            continue
        seen.add(rel)
        todo += [x for x in references_of(rel, texts) if not x.startswith("stages/")]
    return seen


def previous_sizes():
    """Character counts at the latest version tag, for the 'change' column."""
    tag = run(["git", "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*"]).stdout.strip()
    if not tag:
        return "", {}
    prefix = SKILL.relative_to(ROOT).as_posix() + "/"
    names = run(["git", "ls-tree", "-r", "--name-only", tag, "--", prefix]).stdout.splitlines()
    sizes = {}
    for name in names:
        rel = name[len(prefix):]
        if rel == "SKILL.md" or rel.split("/")[0] in EXPECTED:
            sizes[rel] = len(run(["git", "show", f"{tag}:{name}"]).stdout)
    return tag, sizes


def check_budget(r, texts):
    limits = json.loads(BUDGETS.read_text(encoding="utf-8"))["limits"]
    failed_before = r.failed
    tag, before = previous_sizes()
    print(f"[3] 篇幅预算（字符数{'；变化是和 ' + tag + ' 比' if tag else ''}）")
    sizes = {rel: len(t) for rel, t in texts.items()}

    def change(now, old):
        return "" if not tag else ("新增" if old is None else f"{now - old:+,}")

    for rel in sorted(sizes, key=lambda x: (x != "SKILL.md", x)):
        limit = limits["entry"] if rel == "SKILL.md" else (limits["stage"] if rel.startswith("stages/") else None)
        cap = f" / {limit:,}" if limit else ""
        print(f"    {rel:<28}{sizes[rel]:>7,}{cap:<9} {change(sizes[rel], before.get(rel))}")
        if limit and sizes[rel] > limit:
            r.bad(f"{rel} 有 {sizes[rel]:,} 字，超过上限 {limit:,}")
    total = sum(sizes.values())
    print(f"    {'合计':<26}{total:>7,} / {limits['total']:,} {change(total, sum(before.values())) if before else ''}")
    if total > limits["total"]:
        r.bad(f"技能合计 {total:,} 字，超过上限 {limits['total']:,}")
    entry = closure(["SKILL.md"] + [x for x in references_of("SKILL.md", texts) if not x.startswith("stages/")], texts)
    worst = 0
    for stage in sorted(x for x in texts if x.startswith("stages/")):
        files = entry | closure([stage], texts)
        n = sum(sizes[x] for x in files)
        worst = max(worst, n)
        if n > limits["action"]:
            r.bad(f"一次动作（{stage}）{n:,} 字，超过上限 {limits['action']:,}：{'、'.join(sorted(files))}")
    if r.failed == failed_before:
        r.ok(f"入口、每个阶段、合计和一次动作都在预算内（一次动作最多 {worst:,} 字）")
    else:
        r.note("超出时：删减或合并规则；或请用户决定提高上限，并记进 scripts/budgets.json 的 history")


def check_generated(r, pal):
    print("[4] 子代理定义与角色说明一致")
    for role in pal.ROLES:
        path = PLUGIN / "agents" / f"{role}.md"
        if path.exists() and path.read_text(encoding="utf-8") == agent_text(pal, role):
            r.ok(f"agents/{role}.md 与 roles/{role}.md 一致")
        else:
            r.bad(f"agents/{role}.md 与 roles/{role}.md 不一致：运行 python3 scripts/verify.py agents 重新生成")
        meta, body = pal.read_role(role)
        name = f"coding_pal_{role}"
        if not re.match(r"^[a-z0-9_]+$", name):
            r.bad(f"Codex 子代理名 {name} 只能用小写字母、数字和下划线")
        if tomllib is None:
            continue
        setting = {"model": "gpt-0-test", "effort": "high", "family": "test"}
        try:
            data = tomllib.loads(pal.codex_toml(role, setting))
        except tomllib.TOMLDecodeError as e:
            r.bad(f"Codex 子代理 {name} 的设置不是有效的 TOML：{e}")
            continue
        want = {"name": name, "description": meta.get("description"), "model": "gpt-0-test",
                "model_reasoning_effort": "high", "developer_instructions": body.rstrip("\n") + "\n"}
        if data == want:
            r.ok(f"Codex 子代理 {name} 的说明与 roles/{role}.md 一致")
        else:
            r.bad(f"Codex 子代理 {name} 的设置与 roles/{role}.md 不一致")
    if tomllib is None:
        r.note("当前 Python 没有 tomllib（3.11 起才有），跳过 Codex 设置的解析核对")


def check_templates(r, pal):
    print("[5] 模板和角色说明的头部格式")
    t = SKILL / "templates"
    problems = []

    def text(name):
        return (t / name).read_text(encoding="utf-8")

    for name in ("requirement.md", "plan.md"):
        if not (text(name).startswith("# ") and "{标题}" in text(name).splitlines()[0]):
            problems.append(f"templates/{name} 第一行要是带 {{标题}} 的一级标题（pal new 会替换它）")
    for name in ("PRODUCT.md", "ARCHITECTURE.md", "DECISIONS.md", "CHANGELOG.md"):
        if not text(name).startswith("# "):
            problems.append(f"templates/{name} 第一行要是一级标题")
    if not re.search(r"(?m)^## 未发布\s*$", text("CHANGELOG.md")):
        problems.append("templates/CHANGELOG.md 要有「## 未发布」一节（pal 从这里读待发布的变化）")
    block = text("AGENTS-block.md")
    if not (block.startswith(pal.BLOCK_START) and block.rstrip().endswith(pal.BLOCK_END) and "{docs}" in block):
        problems.append("templates/AGENTS-block.md 要以 coding-pal 起止标记开头和结尾，并含 {docs}")
    for role in pal.ROLES:
        meta, body = pal.parse_frontmatter((SKILL / "roles" / f"{role}.md").read_text(encoding="utf-8"))
        if meta.get("name") != role or not meta.get("description") or not body.strip():
            problems.append(f"roles/{role}.md 头部要有 name: {role} 和 description，正文不能为空")
        if ": " in meta.get("description", ""):
            problems.append(f"roles/{role}.md 的 description 不能含英文冒号加空格（会破坏子代理定义的格式）")
    for p in problems:
        r.bad(p)
    if not problems:
        r.ok("模板、看板标记和三个角色说明的格式都对")


def python39():
    for name in ("python3.9", "/usr/bin/python3"):
        exe = shutil.which(name)
        if exe and run([exe, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"]).stdout.strip() == "3.9":
            return exe
    return None


def check_tests(r):
    print("[6] 单元测试")
    pythons = [sys.executable]
    if sys.version_info[:2] != (3, 9):
        p39 = python39()
        if p39:
            pythons.append(p39)
        else:
            r.note("没找到 Python 3.9，只用当前的 Python 跑；pal 必须在 3.9 下能跑，发布前要补跑")
    for exe in pythons:
        version = run([exe, "-c", "import platform; print(platform.python_version())"]).stdout.strip()
        p = run([exe, "-m", "unittest", "discover", "-s", TESTS, "-p", "test_*.py"])
        ran = re.search(r"Ran (\d+) tests?", p.stderr)
        if p.returncode == 0:
            r.ok(f"{TESTS}（Python {version}）{ran.group(1) if ran else '?'} 个测试通过")
        else:
            r.bad(f"{TESTS}（Python {version}）没通过：{last_lines(p)}")


def check_pages(r):
    print("[7] 网页脚本语法")
    node = shutil.which("node")
    if not node:
        r.note("没装 node，跳过")
        return
    for rel in ("assets/acceptance-report/app.js", "assets/board/app.js"):
        p = run([node, "--check", SKILL / rel])
        if p.returncode == 0:
            r.ok(rel)
        else:
            r.bad(f"{rel} 有语法错误：{last_lines(p)}")


def check_claude_validate(r):
    print("[8] Claude Code 插件校验")
    claude = shutil.which("claude")
    if not claude:
        r.note("没装 claude 命令，跳过")
        return
    p = run([claude, "plugin", "validate", PLUGIN], timeout=120)
    if p.returncode == 0:
        r.ok("claude plugin validate 通过")
    else:
        r.bad(f"claude plugin validate 没通过：{last_lines(p)}")


def repo_url(value):
    """(host, owner/repo) of a git URL, so SSH and HTTPS forms of the same repository compare equal."""
    m = re.fullmatch(r"(?:[^@/]+@)?([^:/]+):(.+)", value) if "://" not in value else None
    host, path = m.groups() if m else (urlparse(value).hostname or "", urlparse(value).path)
    path = path.strip("/")
    return host.lower(), path[:-4] if path.endswith(".git") else path


def check_catalog(r, version, folder, write):
    """The catalog repository must point both platforms at this repository's vX.Y.Z (and nothing else)."""
    print(f"[目录登记] {folder} → v{version}")
    tag = f"v{version}"
    commit = run(["git", "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}"]).stdout.strip()
    if not commit:
        r.bad(f"本仓库没有标记 {tag}")
        return
    if run(["git", "merge-base", "--is-ancestor", commit, "main"]).returncode != 0:
        r.bad(f"{tag} 不在主线 main 上")
    for kind in (".claude-plugin", ".codex-plugin"):
        shown = run(["git", "show", f"{commit}:plugins/coding-pal/{kind}/plugin.json"]).stdout
        if (json.loads(shown) if shown else {}).get("version") != version:
            r.bad(f"{tag} 里的 {kind}/plugin.json 版本不是 {version}")
    origin = repo_url(run(["git", "remote", "get-url", "origin"]).stdout.strip())
    for platform, rel in CATALOGS.items():
        path = Path(folder) / rel
        data = json.loads(path.read_text(encoding="utf-8"))
        items = [x for x in data.get("plugins", []) if x.get("name") == "coding-pal"]
        if len(items) != 1:
            r.bad(f"{rel} 里 coding-pal 应该正好登记一次，实际 {len(items)} 次")
            continue
        item, source = items[0], items[0].setdefault("source", {})
        want = {"ref": tag, "sha": commit} if platform == "claude" else {"ref": commit}
        if write:
            item["version"] = version
            source.update(source="git-subdir", path="plugins/coding-pal", **want)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        wrong = [k for k, v in dict(want, version=version).items() if (item if k == "version" else source).get(k) != v]
        if source.get("source") != "git-subdir" or source.get("path") != "plugins/coding-pal":
            wrong.append("来源类型或路径")
        if repo_url(source.get("url", "")) != origin:
            wrong.append("仓库地址（应和本仓库的 origin 一致）")
        if wrong:
            r.bad(f"{rel}（{platform}）不对：{'、'.join(wrong)}")
        else:
            r.ok(f"{rel}（{platform}）登记的是 {tag}（{commit[:7]}）{'，已写入' if write else ''}")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["scenarios"]:
        return subprocess.call([sys.executable, str(ROOT / "tests" / "scenarios" / "run.py")] + argv[1:])
    parser = argparse.ArgumentParser(description="coding-pal 的程序检查（不调用模型）")
    parser.add_argument("command", nargs="?", default="program", choices=("program", "agents", "catalog"))
    parser.add_argument("version", nargs="?", help="catalog：版本号，例如 1.0.0")
    parser.add_argument("--install", choices=("claude", "codex", "all"), help="另跑插件安装测试")
    parser.add_argument("--catalog", help="catalog：目录仓库（pal-plugins）所在的文件夹")
    parser.add_argument("--write", action="store_true", help="catalog：先把两份清单的登记改成这个版本")
    a = parser.parse_args(argv)
    pal = load_pal()
    if a.command == "catalog":
        if not (a.version and re.fullmatch(r"\d+\.\d+\.\d+", a.version) and a.catalog):
            parser.error("catalog 要写版本号和 --catalog，例如：catalog 1.0.0 --catalog ../pal-plugins")
        r = Report()
        check_catalog(r, a.version, a.catalog, a.write)
        return 1 if r.failed else 0
    if a.command == "agents":
        for role in pal.ROLES:
            path = PLUGIN / "agents" / f"{role}.md"
            path.parent.mkdir(exist_ok=True)
            path.write_text(agent_text(pal, role), encoding="utf-8")
            print(f"已生成 {path.relative_to(ROOT).as_posix()}")
        return 0
    r = Report()
    texts = skill_texts()
    check_structure(r, pal)
    check_refs(r, texts)
    check_budget(r, texts)
    check_generated(r, pal)
    check_templates(r, pal)
    check_tests(r)
    check_pages(r)
    check_claude_validate(r)
    if a.install:
        print(f"[9] 安装测试（{a.install}）")
        p = run(["bash", "tests/install/run.sh", a.install], timeout=1800)
        print(p.stdout + p.stderr)
        if p.returncode != 0:
            r.bad("安装测试没通过")
    print()
    print("程序检查全部通过" if not r.failed else f"程序检查有 {r.failed} 项失败")
    return 1 if r.failed else 0


if __name__ == "__main__":
    sys.exit(main())
