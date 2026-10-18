"""Unit tests for the acceptance page generator (run: python3 -m unittest discover -s tests/unit). Must pass on Python 3.9."""
import base64
import copy
import importlib.util
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "plugins" / "coding-pal" / "skills" / "coding-pal"
SCRIPT = SKILL / "scripts" / "render_acceptance.py"
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("render_acceptance", SCRIPT)
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


def png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x10\x20\x30")) + chunk(b"IEND", b""))


def sample(board_url):
    return {
        "title": "看板网页", "headline": "验收员查了 2 项：1 项通过，1 项要你亲眼看",
        "you_need_to": {"text": "打开看板看一遍", "minutes": 3},
        "changes": ["新增项目看板，等你处理的任务排在最前"],
        "try": [{"title": "看等你处理的任务", "steps": [f"打开 [看板]({board_url})", "看最上面一栏"],
                 "expect": "最上面是等你处理", "fail": "顺序是乱的",
                 "images": [{"caption": "应该长这样", "file": "shot.png"}]}],
        "checked": [
            {"title": "等你处理的任务排在最前", "result": "通过", "summary": "两个等你的任务都在最前",
             "evidence": [{"caption": "截图里等你处理在最上面", "file": "shot.png"},
                          {"caption": "看板的测试全部通过", "file": "test.log", "highlight": [18, 19]}]},
            {"title": "暗色下看得清", "result": "无法验证", "summary": "要人眼判断，放进了请你试", "evidence": []},
        ],
        "issues": [], "details": "",
    }


class RendererTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="report-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "evidence"
        self.root.mkdir()
        (self.root / "shot.png").write_bytes(png())
        (self.root / "test.log").write_text("\n".join(f"line {n}" for n in range(1, 21)), encoding="utf-8")
        self.board = self.tmp / "board" / "index.html"
        self.board.parent.mkdir()
        self.board.write_text("<!doctype html>", encoding="utf-8")
        self.data = sample(self.board.as_uri())
        self.out = self.tmp / "report"

    def generate(self, data=None, out=None):
        path = self.tmp / "acceptance.json"
        path.write_text(json.dumps(self.data if data is None else data, ensure_ascii=False), encoding="utf-8")
        return renderer.generate(path, self.root, out or self.out, project="示例项目", revision="feature/x @ abc1234")

    def embedded(self):
        page = (self.out / "index.html").read_text(encoding="utf-8")
        raw = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', page, re.S).group(1)
        return raw, json.loads(raw)

    def rejects(self, change, words):
        data = copy.deepcopy(self.data)
        change(data)
        with self.assertRaises(renderer.ReportError) as caught:
            self.generate(data)
        self.assertIn(words, str(caught.exception))
        self.assertFalse(self.out.exists())

    def test_page_carries_the_content_and_copies_evidence(self):
        page = self.generate()
        self.assertEqual(page, self.out / "index.html")
        _, data = self.embedded()
        self.assertEqual((data["project"], data["title"], data["revision"]), ("示例项目", "看板网页", "feature/x @ abc1234"))
        shot, log = data["checked"][0]["evidence"]
        self.assertEqual(shot["kind"], "image")
        self.assertEqual((self.out / shot["url"]).read_bytes(), png())
        self.assertEqual((log["kind"], log["highlight"], len(log["lines"]), log["lines"][17]), ("text", [18, 19], 20, "line 18"))
        self.assertEqual(data["try"][0]["images"][0]["kind"], "image")
        self.assertEqual(data["checked"][1]["evidence"], [])
        for name in ("styles.css", "app.js", "icons/check.svg", renderer.MARKER):
            self.assertTrue((self.out / name).exists(), name)
        html = page.read_text(encoding="utf-8")
        self.assertNotIn("__REPORT_DATA__", html)
        self.assertNotRegex(html, r'(?:src|href)=["\']https?://')

    def test_first_screen_lists_and_steps_have_limits(self):
        self.rejects(lambda d: d.update(headline="长" * 81), "headline")
        self.rejects(lambda d: d.update(title="长" * 41), "title")
        self.rejects(lambda d: d["you_need_to"].update(text="长" * 101), "you_need_to.text")
        self.rejects(lambda d: d.update(changes=["变化"] * 6), "最多 5 条")
        self.rejects(lambda d: d.update(changes=[]), "至少要有 1 条")
        self.rejects(lambda d: d["you_need_to"].update(minutes=0), "minutes")
        self.rejects(lambda d: d["you_need_to"].update(minutes=True), "minutes")
        self.rejects(lambda d: d["try"][0].update(steps=["一", "二", "三", "四"]), "最多 3 条")
        self.rejects(lambda d: d.update(issues=[{"what": "w", "impact": "i", "plan": "p"}] * 4), "最多 3 条")
        self.rejects(lambda d: d.pop("details"), "缺少 details")
        self.rejects(lambda d: d.update(extra=1), "不认识的字段 extra")

    def test_evidence_needs_a_caption_and_a_real_file(self):
        self.rejects(lambda d: d["checked"][0]["evidence"][0].pop("caption"), "caption")
        self.rejects(lambda d: d["checked"][0]["evidence"][0].update(caption=" "), "这说明了什么")
        self.rejects(lambda d: d["try"][0]["images"][0].pop("caption"), "caption")
        self.rejects(lambda d: d["checked"][0]["evidence"][0].update(file="missing.png"), "找不到证据文件")
        self.rejects(lambda d: d["checked"][0].update(evidence=[]), "至少要有 1 条")
        self.rejects(lambda d: d["checked"][1].update(result="待定"), "只能是 通过、未通过、无法验证")
        self.rejects(lambda d: d.update(checked=[]), "至少要有 1 条")
        self.rejects(lambda d: d["try"][0]["images"][0].update(file="test.log"), "图片")

    def test_long_logs_need_key_lines(self):
        (self.root / "long.log").write_text("\n".join("x" for _ in range(40)), encoding="utf-8")

        def no_key_lines(d):
            entry = d["checked"][0]["evidence"][1]
            entry.pop("highlight")
            entry["file"] = "long.log"
        self.rejects(no_key_lines, "highlight")
        self.rejects(lambda d: d["checked"][0]["evidence"][1].update(highlight=[18, 25]), "只有 20 行")
        self.rejects(lambda d: d["checked"][0]["evidence"][1].update(highlight=[1, 30]), "最多标 20 行")
        self.rejects(lambda d: d["checked"][0]["evidence"][1].update(highlight=[3]), "[起始行, 结束行]")
        self.rejects(lambda d: d["checked"][0]["evidence"][0].update(highlight=[1, 2]), "文本证据")
        (self.root / "short.log").write_text("ok\nall good\n", encoding="utf-8")
        self.data["checked"][0]["evidence"][1] = {"caption": "短日志不用标关键行", "file": "short.log"}
        self.generate()
        self.assertEqual(self.embedded()[1]["checked"][0]["evidence"][1]["highlight"], [1, 2])

    def test_paths_stay_inside_the_evidence_folder(self):
        outside = self.tmp / "secret.txt"
        outside.write_text("私人内容", encoding="utf-8")
        (self.root / "link.txt").symlink_to(outside)
        for path in ("../secret.txt", str(outside), "C:\\secret.txt", "link.txt", "a/../../secret.txt", "file:///x.png"):
            with self.subTest(path=path):
                self.rejects(lambda d: d["checked"][0]["evidence"][0].update(file=path), "证据文件夹")
        self.assertEqual(outside.read_text(encoding="utf-8"), "私人内容")

    def test_images_are_checked(self):
        (self.root / "broken.png").write_bytes(b"\x89PNG\r\n\x1a\nbroken")
        self.rejects(lambda d: d["checked"][0]["evidence"][0].update(file="broken.png"), "损坏")
        jpeg = base64.b64decode('/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCdAAr/2Q==')
        webp = base64.b64decode('UklGRiIAAABXRUJQVlA4IBYAAAAwAQCdASoBAAEADsD+JaQAA3AAAAAA')
        for suffix, content in ((".png", png()), (".jpeg", jpeg), (".webp", webp)):
            self.assertTrue(renderer.image_valid(content, suffix), suffix)
            self.assertFalse(renderer.image_valid(content[:-3], suffix), suffix)
        damaged = bytearray(png())
        damaged[45] ^= 1
        self.assertFalse(renderer.image_valid(bytes(damaged), ".png"))
        (self.root / "really-jpeg.png").write_bytes(jpeg)
        self.data["checked"][0]["evidence"][0]["file"] = "really-jpeg.png"
        self.generate()
        self.assertTrue(self.embedded()[1]["checked"][0]["evidence"][0]["url"].endswith(".jpg"))

    def test_text_stays_text_and_active_files_become_downloads(self):
        attack = '</script><script>alert(1)</script><img src=x onerror=alert(1)>&'
        (self.root / "page.html").write_text(attack + "\n第二行\n", encoding="utf-8")
        self.data["checked"][0]["evidence"][1] = {"caption": attack + "\u2028", "file": "page.html"}
        self.generate()
        raw, data = self.embedded()
        for bad in ("</script", "<img", "\u2028"):
            self.assertNotIn(bad, raw)
        entry = data["checked"][0]["evidence"][1]
        self.assertEqual((entry["kind"], entry["lines"]), ("text", [attack, "第二行"]))
        self.assertTrue(entry["url"].endswith(".html.download"))
        self.assertEqual(entry["caption"], attack + "\u2028")

    def test_regenerating_replaces_only_a_generated_page(self):
        self.generate()
        (self.out / "stale.txt").write_text("旧文件", encoding="utf-8")
        self.data["headline"] = "改过的结论"
        self.generate()
        self.assertEqual(self.embedded()[1]["headline"], "改过的结论")
        self.assertFalse((self.out / "stale.txt").exists())
        notes = self.tmp / "notes"
        notes.mkdir()
        (notes / "keep.txt").write_text("我的文件", encoding="utf-8")
        with self.assertRaises(renderer.ReportError):
            self.generate(out=notes)
        self.assertEqual((notes / "keep.txt").read_text(encoding="utf-8"), "我的文件")

    def test_failed_generation_keeps_the_previous_page(self):
        self.generate()
        before = (self.out / "index.html").read_text(encoding="utf-8")
        self.data["changes"] = []
        with self.assertRaises(renderer.ReportError):
            self.generate()
        self.assertEqual((self.out / "index.html").read_text(encoding="utf-8"), before)
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name.startswith(".acceptance")], [])

    def test_bad_json_is_explained(self):
        path = self.tmp / "bad.json"
        for content, words in (('{"title": "a", "title": "b"}', "重复"), ("{oops", "不是有效的 JSON"), ('{"x": NaN}', "NaN")):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(renderer.ReportError) as caught:
                    renderer.generate(path, self.root, self.out)
                self.assertIn(words, str(caught.exception))

    def test_entry_links_must_open_and_pages_are_not_left_as_code(self):
        set_step = lambda step: (lambda d: d["try"][0]["steps"].__setitem__(0, step))
        missing = (self.tmp / "board" / "missing.html").as_uri()
        self.rejects(set_step(f"打开 [看板]({missing})"), "指向的文件不存在")
        for url in ("javascript:void0", "board/index.html", "file://server/share/a.html", "http:index.html"):
            with self.subTest(url=url):
                self.rejects(set_step(f"打开 [看板]({url})"), "file:///完整路径")
        for code in (self.board.as_uri() + "#agent", str(self.board) + "#agent", "board/index.html", "http://127.0.0.1:5173"):
            with self.subTest(code=code):
                self.rejects(lambda d: d["you_need_to"].update(text=f"打开 `{code}` 看一遍"), "写成链接")
        long_link = f"再打开 [同一页的列表]({self.board.as_uri()}#{'x' * 250})"  # 网址很长，显示出来的字很少
        self.data["try"][0]["steps"][1] = long_link
        self.data["you_need_to"]["text"] = f"在终端运行 `npm test`，再打开 [看板]({self.board.as_uri()})"
        self.generate()
        shown = self.embedded()[1]
        self.assertEqual((shown["try"][0]["steps"][1], shown["you_need_to"]["text"]),
                         (long_link, self.data["you_need_to"]["text"]))

    def test_shipped_template_passes_once_its_files_exist(self):
        for name in ("expected.png", "screenshot.png"):
            (self.root / name).write_bytes(png())
        page = self.tmp / "page"
        (page / "index.html").parent.mkdir()
        (page / "index.html").write_text("<!doctype html>", encoding="utf-8")
        template = (SKILL / "templates" / "acceptance.json").read_text(encoding="utf-8")
        self.assertIn("file:///完整路径", template)
        data = self.tmp / "template.json"
        data.write_text(template.replace("file:///完整路径", page.as_uri()), encoding="utf-8")
        renderer.generate(data, self.root, self.out)
        self.assertTrue((self.out / "index.html").exists())

    def test_command_line(self):
        data = self.tmp / "acceptance.json"
        data.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        args = [sys.executable, str(SCRIPT), "--data", str(data), "--evidence-root", str(self.root), "--output", str(self.out)]
        ok = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn("已生成验收网页", ok.stdout)
        data.write_text("{}", encoding="utf-8")
        bad = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("生成失败：缺少 title", bad.stderr)


if __name__ == "__main__":
    unittest.main()
