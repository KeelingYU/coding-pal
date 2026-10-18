"""Unit tests for pal (run: python3 -m unittest discover -s tests/unit). Must pass on Python 3.9."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PAL = REPO / "plugins" / "coding-pal" / "skills" / "coding-pal" / "scripts" / "pal.py"


class PalCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pal-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home, self.codex = self.tmp / "home", self.tmp / "codex"
        self.env = dict(os.environ, PAL_HOME=str(self.home), CODEX_HOME=str(self.codex), PYTHONDONTWRITEBYTECODE="1",
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")

    def git(self, cwd, *args):
        p = subprocess.run(["git"] + list(args), cwd=str(cwd), env=self.env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.strip()

    def repo(self, name="proj", files=None):
        root = self.tmp / name
        root.mkdir()
        self.git(root, "init", "-q", "-b", "main")
        for rel, text in (files if files is not None else {"app.txt": "app\n"}).items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
        if files is None or files:
            self.git(root, "add", "-A")
            self.git(root, "commit", "-q", "-m", "start")
        return root

    def pal(self, cwd, *args, code=0):
        p = subprocess.run([sys.executable, str(PAL)] + list(args) + ["--json"], cwd=str(cwd), env=self.env,
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(p.returncode, code, p.stdout + p.stderr)
        return json.loads(p.stdout)

    def ready(self, releases=False, **cfg):
        root = self.repo()
        self.pal(root, "init")
        path = root / ".pal" / "config.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(cfg)
        data["release"]["enabled"] = releases
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return root

    def merged_log(self, root):
        return json.loads((root / ".pal" / "merged.json").read_text(encoding="utf-8"))

    def card(self, root, task):
        text = (root / ".pal" / "tasks" / task / "card.md").read_text(encoding="utf-8")
        return dict(re.findall(r"^([^:\n]+): (.*)$", text, re.M))

    def commit(self, cwd, rel, text, message="change"):
        (Path(cwd) / rel).write_text(text, encoding="utf-8")
        self.git(cwd, "add", "-A")
        self.git(cwd, "commit", "-q", "-m", message)

    def board(self, root):
        text = (root / ".pal" / "board" / "board.js").read_text(encoding="utf-8")
        return json.loads(text[text.index("push(") + 5:text.rindex(");")])


class InitTests(PalCase):
    def test_init_keeps_existing_rules_and_is_idempotent(self):
        root = self.repo(files={"app.txt": "app\n", "AGENTS.md": "# 规则\n\n自己的内容\n", "CLAUDE.md": "旧说明\n"})
        out = self.pal(root, "init", "--name", "演示")
        agents = (root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("自己的内容", agents)
        self.assertIn("docs/PRODUCT.md", agents)
        self.assertTrue((root / "CLAUDE.md").read_text(encoding="utf-8").startswith("@AGENTS.md\n"))
        self.assertIn(".pal/", (root / ".gitignore").read_text(encoding="utf-8"))
        for rel in ("docs/PRODUCT.md", "docs/ARCHITECTURE.md", "docs/DECISIONS.md", "CHANGELOG.md"):
            self.assertTrue((root / rel).exists(), rel)
        self.assertEqual(self.git(root, "log", "-1", "--format=%s"), "chore: set up coding-pal")
        self.assertEqual(self.git(root, "status", "--porcelain"), "")
        self.assertEqual(json.loads((self.home / "projects.json").read_text(encoding="utf-8"))[0]["name"], "演示")
        self.assertIn("AGENTS.md", out["touched"])
        commits = self.git(root, "rev-list", "--count", "HEAD")
        self.pal(root, "init")
        self.assertEqual((root / "AGENTS.md").read_text(encoding="utf-8").count("coding-pal:start"), 1)
        self.assertEqual(self.git(root, "rev-list", "--count", "HEAD"), commits)

    def test_init_in_plain_folder_creates_repository(self):
        root = self.tmp / "fresh"
        root.mkdir()
        out = self.pal(root, "init")
        self.assertEqual(self.git(root, "log", "-1", "--format=%s"), "chore: set up coding-pal")
        self.assertTrue(any(line.startswith("发布版本：不要") for line in out["lines"]))
        self.assertIn("不发布版本", self.pal(root, "status")["lines"][0])

    def test_init_guesses_releasing_from_a_remote(self):
        root = self.repo()
        self.git(root, "remote", "add", "origin", "https://example.com/proj.git")
        out = self.pal(root, "init")
        self.assertTrue(any(line.startswith("发布版本：要") for line in out["lines"]))
        self.assertTrue(json.loads((root / ".pal/config.json").read_text(encoding="utf-8"))["release"]["enabled"])

    def test_init_does_not_commit_files_with_unrelated_edits(self):
        root = self.repo(files={"app.txt": "app\n", "AGENTS.md": "# 规则\n"})
        (root / "AGENTS.md").write_text("# 规则\n用户没提交的改动\n", encoding="utf-8")
        out = self.pal(root, "init")
        self.assertTrue(any("AGENTS.md" in w for w in out["warnings"]))
        self.assertEqual(self.git(root, "log", "-1", "--format=%s"), "start")

    def test_commands_need_init(self):
        root = self.repo()
        out = self.pal(root, "status", code=5)
        self.assertIn("pal init", out["next"])


class TaskTests(PalCase):
    def test_requirement_gets_branch_worktree_and_documents(self):
        root = self.ready()
        out = self.pal(root, "new", "board-page", "--type", "普通需求", "--title", "看板网页")
        wt = root / ".worktrees" / "board-page"
        self.assertTrue(wt.is_dir())
        self.assertEqual(out["branch"], "feature/board-page")
        card = self.card(root, "board-page")
        self.assertEqual((card["标题"], card["阶段"], card["状态"]), ("看板网页", "需求", "Agent 在做"))
        self.assertIn("看板网页", (root / ".pal/tasks/board-page/requirement.md").read_text(encoding="utf-8"))
        self.assertTrue((root / ".pal/tasks/board-page/plan.md").exists())
        self.assertEqual(self.board(root)["tasks"][0]["resume"], "继续 看板网页")
        self.pal(root, "new", "board-page", "--type", "普通需求", code=5)
        self.pal(root, "new", "Bad_Name", "--type", "普通需求", code=5)

    def test_only_the_first_version_is_built_in_the_main_folder(self):
        root = self.repo(files={})
        self.pal(root, "init")
        self.assertEqual(self.pal(root, "new", "first", "--type", "普通需求")["worktree"], "主文件夹")
        out = self.pal(root, "new", "second", "--type", "小改动", code=5)
        self.assertIn("first", out["error"])
        self.assertIn("pal todo add", out["next"])
        self.pal(root, "drop", "first")
        self.commit(root, "app.txt", "app\n")
        self.assertEqual(self.pal(root, "new", "typo", "--type", "小改动")["worktree"], ".worktrees/typo")
        self.assertTrue((root / ".worktrees" / "typo").is_dir())
        self.pal(root, "new", "x", "--type", "小改动", "--no-worktree", code=5)

    def test_set_validates_and_clears_stale_fields(self):
        root = self.ready()
        self.pal(root, "new", "t", "--type", "小改动")
        self.pal(root, "set", "t", "状态=等你", code=5)
        self.pal(root, "set", "t", "阶段=方案", code=5)
        self.pal(root, "set", "t", "暂停=是", code=5)
        self.pal(root, "set", "t", "状态=等你", "等你做=看效果，回复\"可以\"")
        self.assertEqual(self.card(root, "t")["等你做"], "看效果，回复\"可以\"")
        self.pal(root, "set", "t", "状态=进行中", code=5)
        self.pal(root, "set", "t", "状态=排队", code=5)
        self.pal(root, "set", "t", "状态=Agent在做", "下一步=改第二处")
        self.assertEqual((self.card(root, "t")["状态"], self.card(root, "t")["等你做"]), ("Agent 在做", ""))
        self.pal(root, "set", "t", "状态=暂停", code=5)
        self.pal(root, "set", "t", "状态=暂停", "原因=和别的任务改同一处")
        out = self.pal(root, "set", "t", "下一步=等对方")
        self.assertTrue(any("暂停" in w for w in out["warnings"]))
        self.pal(root, "set", "t", "状态=Agent 在做")
        self.assertNotIn("原因", self.card(root, "t"))
        self.pal(root, "set", "t", "阶段=合并", "状态=排队")
        self.pal(root, "set", "t", "阶段=发布", code=5)

    def test_status_lists_waiting_first_with_todos_and_unreleased(self):
        root = self.ready(releases=True)
        self.pal(root, "new", "a", "--type", "普通需求", "--title", "甲")
        self.pal(root, "new", "b", "--type", "普通需求", "--title", "乙")
        self.pal(root, "set", "a", "状态=等你", "等你做=确认需求说明")
        self.pal(root, "todo", "add", "以后做", "导出")
        self.commit(root, "CHANGELOG.md", "# 变更记录\n\n## 未发布\n- 新增导出\n\n## 0.1.0 - 2026-10-01\n- 首版\n")
        out = self.pal(root, "status")
        self.assertTrue(out["releases"])
        self.assertIn("要发布版本", out["lines"][0])
        self.assertEqual(out["lines"][1], "等你处理（1）")
        self.assertIn("甲", out["lines"][2])
        self.assertEqual(out["todos"][0]["text"], "以后做 导出")
        self.assertEqual(out["unreleased"], ["新增导出"])
        self.pal(root, "todo", "done", "1")
        self.assertEqual(self.pal(root, "todo", "list")["todos"], [])
        self.pal(root, "todo", "done", "3", code=5)

    def test_path_points_into_worktree_unless_docs_are_ignored(self):
        root = self.ready()
        self.pal(root, "new", "t", "--type", "普通需求")
        wt = root / ".worktrees" / "t"
        self.assertEqual(self.pal(root, "path", "product", "t")["path"], (wt / "docs/PRODUCT.md").as_posix())
        self.assertEqual(self.pal(wt, "path", "changelog")["path"], (wt / "CHANGELOG.md").as_posix())
        self.assertEqual(self.pal(root, "path", "product")["path"], (root / "docs/PRODUCT.md").as_posix())
        self.assertEqual(self.pal(wt, "path", "tmp", "t")["path"], (root / ".pal/tmp/t").as_posix())

    def test_private_docs_stay_in_main_folder(self):
        root = self.repo(files={"app.txt": "app\n", ".gitignore": "private/\n"})
        self.pal(root, "init", "--docs-dir", "private")
        self.assertTrue((root / "private/PRODUCT.md").exists())
        self.assertEqual(self.git(root, "ls-files", "private"), "")
        self.pal(root, "new", "t", "--type", "普通需求")
        wt = root / ".worktrees" / "t"
        self.assertEqual(self.pal(wt, "path", "decisions")["path"], (root / "private/DECISIONS.md").as_posix())
        self.assertEqual(self.pal(wt, "path", "changelog")["path"], (wt / "CHANGELOG.md").as_posix())


class MergeTests(PalCase):
    def task_with_change(self, root, task="t", rel="feature.txt", text="new\n", changelog="- 新功能\n"):
        self.pal(root, "new", task, "--type", "普通需求")
        wt = root / ".worktrees" / task
        (wt / rel).write_text(text, encoding="utf-8")
        if changelog:
            (wt / "CHANGELOG.md").write_text((wt / "CHANGELOG.md").read_text(encoding="utf-8") + changelog, encoding="utf-8")
        self.git(wt, "add", "-A")
        self.git(wt, "commit", "-q", "-m", "task work")
        return wt

    def test_merge_lands_on_main_and_cleans_up(self):
        root = self.ready(checks="test -f feature.txt")
        wt = self.task_with_change(root)
        (root / ".pal/tmp/t").mkdir(parents=True)
        (root / ".pal/reports/t").mkdir(parents=True)
        out = self.pal(root, "merge", "t")
        self.assertEqual((root / "feature.txt").read_text(encoding="utf-8"), "new\n")
        self.assertFalse(wt.exists())
        self.assertEqual(self.git(root, "branch", "--list", "feature/t"), "")
        self.assertFalse((root / ".pal/tmp/t").exists() or (root / ".pal/reports/t").exists())
        self.assertFalse((root / ".pal/tasks/t").exists())
        self.assertEqual((out["overlap"], out["changelog_entry"], out["released_later"]), ([], True, False))
        self.assertFalse((root / ".pal/merge.lock").exists())
        self.assertEqual(self.merged_log(root)[0]["task"], "t")
        self.assertEqual(self.board(root)["recent_merges"][0]["task"], "t")
        self.assertIn("已经", self.pal(root, "set", "t", "下一步=无")["lines"][0])  # 合并后再改卡片：提示已合并，不报错

    def test_releasing_project_moves_merged_task_to_release_stage(self):
        root = self.ready(releases=True)
        self.task_with_change(root)
        self.assertEqual(self.board(root)["tasks"][0]["stages"][-1], "发布")
        self.pal(root, "merge", "t")
        card = self.card(root, "t")
        self.assertEqual((card["阶段"], card["状态"]), ("发布", "待发布"))
        status = self.pal(root, "status")
        self.assertEqual(status["unreleased"], ["新功能"])
        self.assertTrue(any(line.startswith("待发布（1）") for line in status["lines"]))
        self.pal(root, "merge", "t", code=5)

    def test_conflict_is_rolled_back(self):
        root = self.ready()
        wt = self.task_with_change(root, rel="app.txt", text="task\n")
        self.commit(root, "app.txt", "main\n")
        out = self.pal(root, "merge", "t", code=2)
        self.assertEqual(out["conflicts"], ["app.txt"])
        self.assertEqual((root / "app.txt").read_text(encoding="utf-8"), "main\n")
        self.assertEqual(self.git(wt, "status", "--porcelain"), "")
        self.assertFalse((root / ".pal/merge.lock").exists())

    def test_overlap_and_missing_changelog_are_reported(self):
        root = self.ready()
        self.commit(root, "list.txt", "1\n2\n3\n4\n5\n6\n7\n8\n")
        wt = self.task_with_change(root, rel="list.txt", text="1 task\n2\n3\n4\n5\n6\n7\n8\n", changelog="")
        self.commit(root, "list.txt", "1\n2\n3\n4\n5\n6\n7\n8 main\n")
        out = self.pal(root, "merge", "t")
        self.assertEqual(out["overlap"], ["list.txt"])
        self.assertFalse(out["changelog_entry"])
        self.assertEqual((root / "list.txt").read_text(encoding="utf-8"), "1 task\n2\n3\n4\n5\n6\n7\n8 main\n")
        self.assertFalse(wt.exists())

    def test_overlap_survives_a_manual_sync(self):
        root = self.ready()
        self.commit(root, "list.txt", "1\n2\n3\n4\n5\n6\n7\n8\n")
        wt = self.task_with_change(root, rel="list.txt", text="1 task\n2\n3\n4\n5\n6\n7\n8\n")
        self.commit(root, "list.txt", "1\n2\n3\n4\n5\n6\n7\n8 main\n")
        self.git(wt, "merge", "-q", "--no-edit", "main")
        self.assertEqual(self.pal(root, "merge", "t")["overlap"], ["list.txt"])

    def test_failed_checks_leave_main_alone(self):
        root = self.ready(checks="echo broken && exit 3")
        wt = self.task_with_change(root)
        out = self.pal(root, "merge", "t", code=4)
        self.assertIn("broken", out["checks_output"])
        card = self.card(root, "t")
        self.assertEqual((card["阶段"], card["状态"], card["下一步"]), ("合并", "Agent 在做", "项目检查没通过：修好后重新合并"))
        self.assertFalse((root / "feature.txt").exists())
        self.assertTrue(wt.exists())
        self.assertFalse((root / ".pal/merge.lock").exists())

    def test_busy_lock_queues_and_stale_lock_is_cleared(self):
        root = self.ready()
        self.task_with_change(root)
        lock = root / ".pal" / "merge.lock"
        lock.write_text(json.dumps({"task": "other", "pid": os.getpid(), "started": "x"}), encoding="utf-8")
        out = self.pal(root, "merge", "t", code=3)
        self.assertEqual(out["merging"], "other")
        card = self.card(root, "t")
        self.assertEqual((card["阶段"], card["状态"]), ("合并", "排队"))
        self.assertEqual(self.pal(root, "status")["queue"], ["t"])
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        lock.write_text(json.dumps({"task": "other", "pid": dead.pid, "started": "x"}), encoding="utf-8")
        self.pal(root, "board")
        self.assertEqual(self.board(root)["merging"], "")
        self.pal(root, "merge", "t")
        self.assertEqual(self.merged_log(root)[-1]["task"], "t")

    def test_merge_refuses_unsaved_work(self):
        root = self.ready()
        wt = self.task_with_change(root)
        (wt / "draft.txt").write_text("x", encoding="utf-8")
        self.assertIn("draft.txt", self.pal(root, "merge", "t", code=5)["error"])
        (wt / "draft.txt").unlink()
        (root / "app.txt").write_text("edited\n", encoding="utf-8")
        self.assertIn("app.txt", self.pal(root, "merge", "t", code=5)["error"])

    def test_first_version_done_on_main_is_marked_merged(self):
        root = self.repo(files={})
        self.pal(root, "init")
        self.pal(root, "new", "first", "--type", "普通需求")
        self.commit(root, "app.txt", "app\n")
        self.pal(root, "merge", "first")
        self.assertFalse((root / ".pal/tasks/first").exists())
        self.assertEqual(self.merged_log(root)[0]["title"], "first")
        self.assertEqual(self.pal(root, "new", "next", "--type", "小改动")["worktree"], ".worktrees/next")

    def test_drop_keeps_unmerged_work_in_backup_branch(self):
        root = self.ready()
        wt = self.task_with_change(root)
        (wt / "draft.txt").write_text("unsaved\n", encoding="utf-8")
        out = self.pal(root, "drop", "t")
        backup = out["kept"][0]
        self.assertTrue(backup.startswith("backup/t-"))
        self.assertEqual(self.git(root, "show", f"{backup}:draft.txt"), "unsaved")
        self.assertFalse(wt.exists() or (root / ".pal/tasks/t").exists())
        self.assertEqual(self.git(root, "branch", "--list", "feature/t"), "")

    def test_parallel_changelog_entries_both_survive(self):
        root = self.ready()
        self.task_with_change(root, "a", "a.txt", changelog="- 新增导出\n")
        self.task_with_change(root, "b", "b.txt", changelog="- 新增搜索\n")
        self.pal(root, "merge", "a")
        self.assertEqual(self.pal(root, "merge", "b")["overlap"], [])  # both added a CHANGELOG line: not worth a re-check
        log = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("- 新增导出\n", log)
        self.assertIn("- 新增搜索\n", log)
        self.assertNotIn("<<<<<<<", log)
        self.assertEqual(self.merged_log(root)[-1]["task"], "b")

    def test_merge_waits_for_a_release(self):
        root = self.ready(releases=True)
        self.task_with_change(root)
        lock = {"task": "发布", "pid": os.getpid(), "started": "2026-10-12T10:00", "release": True}
        (root / ".pal/merge.lock").write_text(json.dumps(lock), encoding="utf-8")
        out = self.pal(root, "merge", "t", code=3)
        self.assertIn("发布版本", out["error"])
        self.assertEqual(self.board(root)["merging"], "")


class ReleaseTests(PalCase):
    def project(self, remote=False):
        root = self.ready(releases=True)
        path = root / ".pal" / "config.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["release"].update(version_files=["package.json", "VERSION"])
        path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        self.commit(root, "package.json", '{\n  "name": "demo",\n  "version": "1.2.0",\n  "dependencies": {"x": "1.2.0"}\n}\n')
        self.commit(root, "VERSION", "1.2.0\n")
        self.git(root, "tag", "-a", "v1.2.0", "-m", "v1.2.0 首版")
        if remote:
            self.git(self.tmp, "init", "-q", "--bare", "remote.git")
            self.git(root, "remote", "add", "origin", str(self.tmp / "remote.git"))
            self.git(root, "push", "-q", "origin", "main", "v1.2.0")
        return root

    def merged_task(self, root, task, line):
        self.pal(root, "new", task, "--type", "小改动", "--title", line)
        wt = root / ".worktrees" / task
        (wt / f"{task}.txt").write_text("x\n", encoding="utf-8")
        log = wt / "CHANGELOG.md"
        log.write_text(log.read_text(encoding="utf-8") + f"- {line}\n", encoding="utf-8")
        self.git(wt, "add", "-A")
        self.git(wt, "commit", "-q", "-m", task)
        self.pal(root, "merge", task)

    def notes(self, root, text="# v1.3.0：新增导出\n\n这次可以导出待办。\n\n## 新增\n- 导出待办\n"):
        path = root / ".pal" / "tmp" / "release" / "notes.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return str(path)

    def fake_gh(self):
        """A stand-in for the gh command: keeps releases in a JSON file; FAKE_GH_FAIL=1 makes 'release create' fail."""
        folder = self.tmp / "bin"
        folder.mkdir()
        script = folder / "gh"
        script.write_text(f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
store = Path({str(self.tmp / 'gh.json')!r})
data = json.loads(store.read_text()) if store.exists() else {{}}
args = sys.argv[1:]
tag = args[2]
if args[:2] == ["release", "view"]:
    if tag not in data:
        sys.exit("release not found")
    print(json.dumps(data[tag]))
elif args[:2] == ["release", "create"]:
    if os.environ.get("FAKE_GH_FAIL"):
        sys.exit("HTTP 502: Bad Gateway")
    title = args[args.index("--title") + 1]
    body = Path(args[args.index("--notes-file") + 1]).read_text()
    data[tag] = {{"tagName": tag, "name": title, "body": body, "isDraft": False, "url": "https://example.com/" + tag}}
    store.write_text(json.dumps(data))
""", encoding="utf-8")
        script.chmod(0o755)
        self.env["PATH"] = str(folder) + os.pathsep + self.env["PATH"]
        return self.tmp / "gh.json"

    def test_plan_lists_what_goes_out_and_what_waits(self):
        root = self.project()
        self.merged_task(root, "export", "新增导出")
        self.merged_task(root, "search", "新增搜索")
        self.pal(root, "new", "later", "--type", "小改动", "--title", "稍后")
        self.pal(root, "set", "later", "阶段=合并", "状态=排队")
        plan = self.pal(root, "release", "--plan")
        self.assertEqual(sorted(plan["tasks"]), ["export", "search"])
        self.assertEqual(plan["unreleased"], ["新增导出", "新增搜索"])
        self.assertEqual(plan["next"], {"major": "2.0.0", "minor": "1.3.0", "patch": "1.2.1"})
        self.assertEqual((plan["last"]["tag"], plan["last"]["title"]), ("v1.2.0", "首版"))
        self.assertEqual(plan["queued"], ["稍后"])

    def test_release_bumps_tags_pushes_and_clears_released_tasks(self):
        root = self.project(remote=True)
        self.merged_task(root, "export", "新增导出")
        out = self.pal(root, "release", "--level", "minor", "--notes", self.notes(root), "--push")
        self.assertEqual((out["tag"], out["title"], out["tasks"]), ("v1.3.0", "v1.3.0：新增导出", ["export"]))
        package = json.loads((root / "package.json").read_text(encoding="utf-8"))
        self.assertEqual((package["version"], package["dependencies"]["x"]), ("1.3.0", "1.2.0"))
        self.assertEqual((root / "VERSION").read_text(encoding="utf-8"), "1.3.0\n")
        log = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertRegex(log, r"## 未发布\n\n## 1\.3\.0 - \d{4}-\d{2}-\d{2}\n- 新增导出\n")
        message = self.git(root, "tag", "-l", "--format=%(contents)", "v1.3.0").splitlines()
        self.assertEqual((message[0], message[-2:]), ("v1.3.0：新增导出", ["## 新增", "- 导出待办"]))
        self.assertEqual(self.git(root, "status", "--porcelain"), "")
        self.assertFalse((root / ".pal/tasks/export").exists() or (root / ".pal/tmp/release").exists())
        board = self.board(root)
        self.assertEqual((board["tasks"], board["unreleased"]), ([], []))
        self.assertEqual((board["releases"][0]["tag"], board["releases"][0]["title"]), ("v1.3.0", "新增导出"))
        remote = self.tmp / "remote.git"
        self.assertEqual(self.git(remote, "rev-parse", "main"), self.git(root, "rev-parse", "main"))
        self.assertEqual(self.git(remote, "rev-parse", "v1.3.0^{commit}"), self.git(root, "rev-parse", "main"))
        notes = self.notes(root)
        self.assertIn("未发布", self.pal(root, "release", "--level", "patch", "--notes", notes, code=5)["error"])

    def test_interrupted_release_resumes_without_a_second_bump(self):
        root = self.project()
        store = self.fake_gh()
        self.merged_task(root, "export", "新增导出")
        notes = self.notes(root)
        self.env["FAKE_GH_FAIL"] = "1"
        out = self.pal(root, "release", "--level", "minor", "--notes", notes, "--gh", code=5)
        self.assertIn("502", out["error"])
        self.assertTrue((root / ".pal/tasks/export").exists())
        del self.env["FAKE_GH_FAIL"]
        out = self.pal(root, "release", "--level", "minor", "--notes", notes, "--gh")
        self.assertEqual(out["tag"], "v1.3.0")
        self.assertEqual(self.git(root, "tag", "-l", "v1.*"), "v1.2.0\nv1.3.0")
        self.assertEqual(self.git(root, "log", "--format=%s").count("release: v1.3.0"), 1)
        saved = json.loads(store.read_text(encoding="utf-8"))["v1.3.0"]
        self.assertEqual((saved["name"], saved["body"]), ("v1.3.0：新增导出", "这次可以导出待办。\n\n## 新增\n- 导出待办\n"))
        self.assertEqual(out["release_url"], "https://example.com/v1.3.0")
        self.assertFalse((root / ".pal/tasks/export").exists())

    def test_release_checks_its_preconditions(self):
        root = self.project()
        notes = self.notes(root)
        self.assertIn("未发布", self.pal(root, "release", "--level", "patch", "--notes", notes, code=5)["error"])
        self.commit(root, "CHANGELOG.md", (root / "CHANGELOG.md").read_text(encoding="utf-8") + "- 修正排序\n")
        self.assertIn("三段", self.pal(root, "release", "--version", "2.0", "--notes", notes, code=5)["error"])
        (root / "VERSION").write_text("dirty\n", encoding="utf-8")
        self.assertIn("未提交", self.pal(root, "release", "--level", "patch", "--notes", notes, code=5)["error"])
        self.git(root, "checkout", "--", "VERSION")
        out = self.pal(root, "release", "--version", "2.0.0", "--notes", notes)
        self.assertEqual((out["tag"], out["title"]), ("v2.0.0", "v2.0.0 v1.3.0：新增导出"))
        other = self.repo("other")
        self.pal(other, "init")
        self.assertIn("不发布", self.pal(other, "release", "--plan", code=5)["error"])


class ReportTests(PalCase):
    def test_report_builds_the_page_from_the_task_folder(self):
        root = self.ready()
        self.pal(root, "new", "t", "--type", "普通需求", "--title", "示例")
        out = self.pal(root, "report", "t", code=5)
        self.assertIn("acceptance.json", out["error"])
        data = {"title": "示例", "headline": "验收员查了 1 项，通过", "you_need_to": {"text": "看一眼", "minutes": 1},
                "changes": ["多了一页"], "try": [], "issues": [], "details": "",
                "checked": [{"title": "页面能打开", "result": "通过", "summary": "打开正常",
                             "evidence": [{"caption": "打开时没有报错", "file": "open.log"}]}]}
        acceptance = root / ".pal/tasks/t/acceptance.json"
        acceptance.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.assertIn("open.log", self.pal(root, "report", "t", code=5)["error"])
        (root / ".pal/tmp/t/open.log").write_text("ok\n", encoding="utf-8")
        out = self.pal(root, "report", "t")
        page = root / ".pal/reports/t/index.html"
        self.assertEqual(out["page"], page.as_posix())
        self.assertIn("feature/t @ ", page.read_text(encoding="utf-8"))
        data["changes"] = []
        acceptance.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        out = self.pal(root, "report", "t", code=5)
        self.assertIn("changes", out["error"])
        self.assertIn("pal report t", out["next"])
        self.assertTrue(page.exists())

    def test_board_links_the_page_only_during_acceptance(self):
        root = self.ready()
        self.pal(root, "new", "t", "--type", "小改动", "--title", "示例")
        self.pal(root, "set", "t", "阶段=验收")
        data = {"title": "示例", "headline": "通过", "you_need_to": {"text": "看一眼", "minutes": 1},
                "changes": ["多了一页"], "try": [], "issues": [], "details": "",
                "checked": [{"title": "页面能打开", "result": "通过", "summary": "打开正常",
                             "evidence": [{"caption": "打开时没有报错", "file": "open.log"}]}]}
        (root / ".pal/tasks/t/acceptance.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        (root / ".pal/tmp/t").mkdir(parents=True)
        (root / ".pal/tmp/t/open.log").write_text("ok\n", encoding="utf-8")
        self.assertEqual(self.board(root)["tasks"][0]["report"], "")
        self.pal(root, "report", "t")
        self.assertEqual(self.board(root)["tasks"][0]["report"], (root / ".pal/reports/t/index.html").as_uri())
        self.pal(root, "set", "t", "阶段=改动")
        self.assertEqual(self.board(root)["tasks"][0]["report"], "")


class SetupTests(PalCase):
    def models(self, slugs, hidden=()):
        self.codex.mkdir(exist_ok=True)
        models = [{"slug": s, "visibility": "hide" if s in hidden else "list"} for s in list(slugs) + list(hidden)]
        (self.codex / "models_cache.json").write_text(json.dumps({"models": models}), encoding="utf-8")

    def test_setup_writes_codex_agents_for_latest_family_model(self):
        self.models(["gpt-6-sol", "gpt-6.1-sol", "gpt-5.6-sol", "gpt-6-astra"], hidden=["gpt-7-sol"])
        self.pal(self.tmp, "setup")
        implementer = (self.codex / "agents" / "coding_pal_implementer.toml").read_text(encoding="utf-8")
        checker = (self.codex / "agents" / "coding_pal_checker.toml").read_text(encoding="utf-8")
        self.assertIn('name = "coding_pal_implementer"', implementer)
        self.assertIn('model = "gpt-6.1-sol"', implementer)
        self.assertIn('model_reasoning_effort = "medium"', implementer)
        self.assertIn('model = "gpt-6-astra"', checker)
        self.assertIn('developer_instructions = """', checker)
        for path in (self.home / "config.json", self.home / "projects.json", self.home / "index.html"):
            self.assertTrue(path.exists(), path)
        try:
            import tomllib
        except ImportError:
            return
        self.assertEqual(tomllib.loads(implementer)["name"], "coding_pal_implementer")

    def test_missing_family_inherits_and_new_model_is_flagged(self):
        self.models(["gpt-6-astra"])
        self.pal(self.tmp, "setup", "--platform", "codex")
        self.assertNotIn("model =", (self.codex / "agents" / "coding_pal_implementer.toml").read_text(encoding="utf-8"))
        self.assertEqual(self.pal(self.tmp, "models")["codex_agents"], "ok")
        self.models(["gpt-6-astra", "gpt-6.2-sol"])
        self.assertEqual(self.pal(self.tmp, "models")["codex_agents"], "outdated")
        root = self.ready()
        self.assertTrue(any("Codex" in h for h in self.pal(root, "status")["hints"]))


class BoardTests(PalCase):
    def test_project_page_and_hub_read_live_data(self):
        root = self.ready()
        self.pal(root, "new", "t", "--type", "普通需求", "--title", "看板 <b>")
        self.pal(root, "set", "t", "状态=等你", "等你做=看页面")
        data = self.board(root)
        self.assertEqual((data["tasks"][0]["status"], data["tasks"][0]["waiting_for"]), ("等你", "看页面"))
        page = (root / ".pal/board/index.html").read_text(encoding="utf-8")
        self.assertIn('<script src="board.js"></script>', page)
        self.assertIn((self.home / "projects.js").as_uri(), page)
        self.assertIn("location.reload()", page)
        self.assertNotIn("/*__APP__*/", page)
        registry = (self.home / "projects.js").read_text(encoding="utf-8")
        self.assertIn((root / ".pal/board/board.js").as_uri(), registry)
        hub = (self.home / "index.html").read_text(encoding="utf-8")
        self.assertIn("window.PAL_HUB", hub)
        self.assertIn((self.home / "projects.js").as_uri(), hub)
        self.pal(root, "board", "--hub")

    def test_recent_releases_carry_a_title(self):
        root = self.ready(releases=True)
        self.git(root, "tag", "-a", "v1.0.0", "-m", "v1.0.0：首版上线")
        self.commit(root, "CHANGELOG.md", "# 变更记录\n\n## 未发布\n\n## 1.1.0 - 2026-10-01\n- 新增导出\n- 修正排序\n")
        self.git(root, "tag", "v1.1.0")
        self.git(root, "tag", "not-a-version")
        self.pal(root, "board")
        releases = self.board(root)["releases"]
        self.assertEqual([(r["tag"], r["title"]) for r in releases], [("v1.1.0", "新增导出"), ("v1.0.0", "首版上线")])


if __name__ == "__main__":
    unittest.main()
