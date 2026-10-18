#!/usr/bin/env python3
"""从验收数据生成本机验收网页，只用 Python 标准库。

用法：python3 render_acceptance.py --data <acceptance.json> --evidence-root <证据文件夹> --output <输出文件夹>
      [--project <项目名>] [--revision <检查对象>]
通常由 pal report <任务> 调用；数据格式见 templates/acceptance.json，证据路径都相对证据文件夹。
"""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import sys
import tempfile
from urllib.parse import urlparse
from urllib.request import url2pathname
import zlib


ASSETS = Path(__file__).resolve().parents[1] / "assets" / "acceptance-report"
MARKER = ".coding-pal-report"  # 只覆盖带这个标记的输出文件夹
RESULTS = ("通过", "未通过", "无法验证")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
TEXT_SUFFIXES = {".txt", ".log", ".out", ".md", ".json", ".csv", ".tsv", ".yaml", ".yml", ".xml", ".ini", ".toml",
                 ".py", ".sh", ".js", ".mjs", ".ts", ".css", ".html", ".htm", ".svg", ".diff", ".patch"}
ACTIVE_SUFFIXES = {".html", ".htm", ".svg", ".xhtml", ".js", ".mjs"}  # 原文件改成下载，不能在网页里执行
SHORT_TEXT = 15        # 文本证据超过这么多行，要用 highlight 指出关键行
HIGHLIGHT_MAX = 20     # 关键行最多这么多行
MAX_TEXT_BYTES = 1_000_000
LIMITS = {"title": 40, "headline": 80, "need": 100, "change": 60, "try_title": 40, "step": 200, "expect": 150,
          "checked_title": 80, "summary": 100, "caption": 100, "what": 100, "impact": 150, "plan": 150}
LINK = re.compile(r"\[([^\[\]\n]+)\]\(([^()\s]+)\)")  # [文字](网址)：网页上是可点的文字链接
CODE = re.compile(r"`([^`\n]+)`")
PAGE_IN_CODE = re.compile(r"(?:https?|file)://\S+|\S+\.html?(?:[#?]\S*)?")  # 要打开的网页，要写成链接
ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ReportError(ValueError):
    """验收数据或证据有问题，要改正后重新生成。"""


def fail(where, message):
    raise ReportError(f"{where}：{message}" if where else message)


def text(value, where, limit=None, empty_ok=False):
    if not isinstance(value, str):
        fail(where, "要写成文字")
    if not empty_ok and not value.strip():
        fail(where, "不能为空")
    if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        fail(where, "含有无效字符")
    if limit and len(value) > limit:
        fail(where, f"有 {len(value)} 字，最多 {limit} 字；多的内容放进 details")
    return value


def rich(value, where, limit):
    """你要做的事、请你试的步骤：可以写链接和反引号命令。按显示出来的字数算长度，链接要能打开。"""
    text(value, where)
    for _, url in LINK.findall(value):
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            continue
        if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
            fail(where, f"链接 {url} 要写成 file:///完整路径 或 http(s):// 网址")
        if not Path(url2pathname(parsed.path)).exists():
            fail(where, f"链接 {url} 指向的文件不存在")
    for code in CODE.findall(LINK.sub("", value)):
        if PAGE_IN_CODE.fullmatch(code.strip()):
            fail(where, f"`{code}` 是要打开的网页，写成链接 [文字](file:///完整路径)，网页上点了在新标签页打开")
    return text(LINK.sub(r"\1", value), where, limit)


def listed(value, where, low, high=None):
    if not isinstance(value, list):
        fail(where, "要写成列表 [...]")
    if len(value) < low:
        fail(where, f"至少要有 {low} 条")
    if high is not None and len(value) > high:
        fail(where, f"有 {len(value)} 条，最多 {high} 条；其余放进 details")
    return value


def fields(value, where, required, optional=()):
    if not isinstance(value, dict):
        fail(where, "要写成 {...}")
    missing = [k for k in required if k not in value]
    if missing:
        fail(where, "缺少 " + "、".join(missing))
    extra = [k for k in value if k not in required and k not in optional]
    if extra:
        fail(where, "不认识的字段 " + "、".join(extra))


def safe_source(root, value, where):
    text(value, where)
    path = PurePosixPath(value)
    if (path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value or "\x00" in value
            or not path.parts):
        fail(where, f"{value} 要写成证据文件夹里的相对路径")
    target = root.joinpath(*path.parts)
    try:
        target.resolve().relative_to(root)
    except (ValueError, RuntimeError):
        fail(where, f"{value} 指到了证据文件夹外面")
    return target


def validate(data, root):
    fields(data, "", ("title", "headline", "you_need_to", "changes", "try", "checked", "issues", "details"))
    text(data["title"], "title", LIMITS["title"])
    text(data["headline"], "headline", LIMITS["headline"])
    need = data["you_need_to"]
    fields(need, "you_need_to", ("text", "minutes"))
    rich(need["text"], "you_need_to.text", LIMITS["need"])
    if type(need["minutes"]) is not int or not 1 <= need["minutes"] <= 120:
        fail("you_need_to.minutes", "要写 1 到 120 之间的整数（预计分钟数）")
    for n, change in enumerate(listed(data["changes"], "changes", 1, 5), 1):
        text(change, f"changes 第 {n} 条", LIMITS["change"])
    for n, entry in enumerate(listed(data["try"], "try", 0, 5), 1):
        where = f"try 第 {n} 项"
        fields(entry, where, ("title", "steps", "expect", "fail"), ("images",))
        text(entry["title"], where + " 的 title", LIMITS["try_title"])
        for m, step in enumerate(listed(entry["steps"], where + " 的 steps", 1, 3), 1):
            rich(step, f"{where} 的第 {m} 步", LIMITS["step"])
        text(entry["expect"], where + " 的 expect", LIMITS["expect"])
        text(entry["fail"], where + " 的 fail", LIMITS["expect"])
        for m, image in enumerate(listed(entry.get("images", []), where + " 的 images", 0, 3), 1):
            evidence(image, f"{where} 的第 {m} 张图", root, image_only=True)
    for n, item in enumerate(listed(data["checked"], "checked", 1), 1):
        where = f"checked 第 {n} 项"
        fields(item, where, ("title", "result", "summary", "evidence"))
        text(item["title"], where + " 的 title", LIMITS["checked_title"])
        if item["result"] not in RESULTS:
            fail(where + " 的 result", "只能是 " + "、".join(RESULTS))
        text(item["summary"], where + " 的 summary", LIMITS["summary"])
        listed(item["evidence"], where + " 的 evidence", 0 if item["result"] == "无法验证" else 1)
        for m, entry in enumerate(item["evidence"], 1):
            evidence(entry, f"{where} 的第 {m} 份证据", root)
    for n, issue in enumerate(listed(data["issues"], "issues", 0, 3), 1):
        where = f"issues 第 {n} 条"
        fields(issue, where, ("what", "impact", "plan"))
        for key in ("what", "impact", "plan"):
            text(issue[key], f"{where} 的 {key}", LIMITS[key])
    text(data["details"], "details", empty_ok=True)


def evidence(entry, where, root, image_only=False):
    if not isinstance(entry, dict):
        fail(where, "要写成 {...}")
    if not isinstance(entry.get("caption"), str) or not entry["caption"].strip():
        fail(where, "缺少 caption：写一句\"这说明了什么\"")
    fields(entry, where, ("caption", "file"), () if image_only else ("highlight",))
    text(entry["caption"], where + " 的 caption", LIMITS["caption"])
    source = safe_source(root, entry["file"], where + " 的 file")
    if not source.is_file():
        fail(where, f"找不到证据文件 {entry['file']}")
    if image_only and source.suffix.lower() not in IMAGE_SUFFIXES:
        fail(where, "只能是 PNG、JPEG 或 WebP 图片")
    if "highlight" in entry:
        h = entry["highlight"]
        if not (isinstance(h, list) and len(h) == 2 and all(type(x) is int for x in h) and 1 <= h[0] <= h[1]):
            fail(where + " 的 highlight", "要写成 [起始行, 结束行]，例如 [12, 14]")
        if h[1] - h[0] + 1 > HIGHLIGHT_MAX:
            fail(where + " 的 highlight", f"最多标 {HIGHLIGHT_MAX} 行关键行")
        if source.suffix.lower() not in TEXT_SUFFIXES:
            fail(where + " 的 highlight", "只能用在文本证据（日志、命令输出等）上")


def image_valid(raw, suffix):
    """检查格式、边界与关键结构；完整解码仍由浏览器验证。"""
    try:
        if suffix == ".png":
            if raw[:8] != b"\x89PNG\r\n\x1a\n":
                return False
            pos, seen_header, image_data = 8, False, []
            while pos + 12 <= len(raw):
                size = struct.unpack(">I", raw[pos:pos + 4])[0]
                kind = raw[pos + 4:pos + 8]
                payload = raw[pos + 8:pos + 8 + size]
                end = pos + size + 12
                if end > len(raw) or zlib.crc32(kind + payload) != struct.unpack(">I", raw[end - 4:end])[0]:
                    return False
                if not seen_header:
                    if kind != b"IHDR" or size != 13 or not all(struct.unpack(">II", payload[:8])):
                        return False
                    seen_header = True
                if kind == b"IDAT":
                    image_data.append(payload)
                if kind == b"IEND":
                    if not image_data or size != 0 or end != len(raw):
                        return False
                    decoder = zlib.decompressobj()
                    for part in image_data:
                        # 分块解压只检查完整性，避免为大图一次性分配解压后的全部像素。
                        while part:
                            decoder.decompress(part, 1024 * 1024)
                            part = decoder.unconsumed_tail
                    return decoder.eof and not decoder.unused_data
                pos = end
            return False
        if suffix in (".jpg", ".jpeg"):
            if not raw.startswith(b"\xff\xd8") or not raw.endswith(b"\xff\xd9"):
                return False
            pos, frame = 2, False
            while pos + 4 <= len(raw):
                if raw[pos] != 255:
                    return False
                while raw[pos] == 255:
                    pos += 1
                marker = raw[pos]
                pos += 1
                size = struct.unpack(">H", raw[pos:pos + 2])[0]
                if size < 2 or pos + size > len(raw):
                    return False
                if marker in (0xC0, 0xC1, 0xC2):
                    frame = size >= 8 and all(struct.unpack(">HH", raw[pos + 3:pos + 7]))
                if marker == 0xDA:
                    return frame and pos + size < len(raw) - 2
                pos += size
            return False
        if suffix == ".webp":
            if raw[:4] != b"RIFF" or raw[8:12] != b"WEBP" or len(raw) != int.from_bytes(raw[4:8], "little") + 8:
                return False
            pos, image = 12, False
            while pos + 8 <= len(raw):
                kind, size = raw[pos:pos + 4], int.from_bytes(raw[pos + 4:pos + 8], "little")
                content = raw[pos + 8:pos + 8 + size]
                if len(content) != size:
                    return False
                image |= (kind == b"VP8 " and size >= 10 and content[3:6] == b"\x9d\x01\x2a") or (kind == b"VP8L" and size >= 5 and content[0] == 47) or (kind == b"ANMF" and size >= 24 and content[16:20] in (b"VP8 ", b"VP8L", b"ALPH"))
                pos += 8 + size + size % 2
            return image and pos == len(raw)
    except (IndexError, ValueError, struct.error, zlib.error):
        return False
    return False


def sniff(raw, suffix):
    """截图工具可能给内容与扩展名不一致的文件，按实际格式处理。"""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if raw.startswith(b"\xff\xd8"):
        return ".jpg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    return suffix


def copy_evidence(entry, where, root, folder, serial):
    """把证据复制进网页的 evidence/ 文件夹，返回网页要用的数据。"""
    source = safe_source(root, entry["file"], where)
    raw = source.read_bytes()
    suffix = source.suffix.lower()
    shown = {"caption": entry["caption"], "name": source.name}
    if suffix in IMAGE_SUFFIXES:
        suffix = sniff(raw, suffix)
        if not image_valid(raw, suffix):
            fail(where, f"{entry['file']} 是损坏的图片，或不是 PNG、JPEG、WebP")
        shown["kind"] = "image"
    elif suffix in TEXT_SUFFIXES:
        if len(raw) > MAX_TEXT_BYTES:
            fail(where, f"{entry['file']} 太大（超过 1 MB）；把关键部分另存成一个文件")
        lines = CONTROL.sub("", ANSI.sub("", raw.decode("utf-8", errors="replace"))).splitlines() or [""]
        start, end = entry.get("highlight", [1, len(lines)])
        if "highlight" not in entry and len(lines) > SHORT_TEXT:
            fail(where, f"{entry['file']} 有 {len(lines)} 行，要加 highlight 指出关键行，例如 [12, 14]")
        if end > len(lines):
            fail(where + " 的 highlight", f"{entry['file']} 只有 {len(lines)} 行")
        shown.update(kind="text", lines=lines, highlight=[start, end])
    else:
        shown["kind"] = "file"
    stored = suffix if re.fullmatch(r"\.[a-z0-9]{1,12}", suffix) else ".bin"
    if suffix in ACTIVE_SUFFIXES:
        stored += ".download"
    shown["url"] = f"evidence/{serial}{stored}"
    (folder / f"{serial}{stored}").write_bytes(raw)
    return shown


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail("", f"字段 {key} 重复出现")
        result[key] = value
    return result


def load(data_path):
    try:
        raw = Path(data_path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        fail("", "验收数据要用 UTF-8 保存")
    try:
        return json.loads(raw, object_pairs_hook=unique_object,
                          parse_constant=lambda value: fail("", f"JSON 里不能写 {value}"))
    except json.JSONDecodeError as e:
        fail("", f"不是有效的 JSON（第 {e.lineno} 行第 {e.colno} 列）：{e.msg}")


def publish(staging, output):
    """用新生成的文件夹替换输出文件夹；只替换以前生成的网页，不碰别的内容。"""
    if not output.exists():
        os.replace(staging, output)
        return
    if output.is_symlink() or not output.is_dir() or (any(output.iterdir()) and not (output / MARKER).exists()):
        fail("", f"{output} 已有别的内容，不是验收网页；换一个输出文件夹")
    old = Path(tempfile.mkdtemp(prefix=".acceptance-old-", dir=output.parent))
    os.replace(output, old / "page")
    try:
        os.replace(staging, output)
    except BaseException:
        os.replace(old / "page", output)
        raise
    finally:
        shutil.rmtree(old, ignore_errors=True)


def generate(data_path, evidence_root, output, project="", revision="", assets=ASSETS):
    """生成网页，返回 index.html 的位置；数据或证据有问题时抛出 ReportError，输出文件夹保持原样。"""
    root = Path(evidence_root).resolve()
    output = Path(output).absolute()
    if not root.is_dir():
        fail("", f"证据文件夹 {root} 不存在")
    data = load(data_path)
    validate(data, root)
    assets = Path(assets)
    template = (assets / "index.html").read_text(encoding="utf-8")
    if template.count("__REPORT_DATA__") != 1:
        fail("", "网页模板坏了：数据标记要有且只有一个；重新安装 coding-pal")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".acceptance-build-", dir=output.parent))
    try:
        for path in assets.iterdir():
            if path.name != "index.html":
                (shutil.copytree if path.is_dir() else shutil.copy2)(path, staging / path.name)
        folder = staging / "evidence"
        folder.mkdir()
        serial = 0
        shown = {key: data[key] for key in ("title", "headline", "you_need_to", "changes", "issues", "details")}
        shown.update(project=project, revision=revision, generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
                     schema=2, checked=[])
        shown["try"] = []
        for n, entry in enumerate(data["try"], 1):
            images = []
            for m, image in enumerate(entry.get("images", []), 1):
                serial += 1
                images.append(copy_evidence(image, f"try 第 {n} 项的第 {m} 张图", root, folder, serial))
            shown["try"].append(dict(entry, images=images))
        for n, item in enumerate(data["checked"], 1):
            materials = []
            for m, entry in enumerate(item["evidence"], 1):
                serial += 1
                materials.append(copy_evidence(entry, f"checked 第 {n} 项的第 {m} 份证据", root, folder, serial))
            shown["checked"].append(dict(item, evidence=materials))
        payload = json.dumps(shown, ensure_ascii=False)
        payload = (payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
                   .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
        (staging / "index.html").write_text(template.replace("__REPORT_DATA__", payload), encoding="utf-8")
        (staging / MARKER).write_text("coding-pal 生成的验收网页，重新生成时会整体替换。\n", encoding="utf-8")
        publish(staging, output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return output / "index.html"


def main():
    parser = argparse.ArgumentParser(description="生成本机验收网页")
    parser.add_argument("--data", required=True, help="验收数据 acceptance.json")
    parser.add_argument("--evidence-root", required=True, help="证据文件夹，数据里的证据路径都相对它")
    parser.add_argument("--output", required=True, help="输出文件夹，已有的验收网页会被替换")
    parser.add_argument("--project", default="")
    parser.add_argument("--revision", default="", help="检查对象，例如 分支 @ 提交")
    args = parser.parse_args()
    try:
        page = generate(args.data, args.evidence_root, args.output, args.project, args.revision)
    except (OSError, ReportError) as error:
        print("生成失败：" + str(error), file=sys.stderr)
        return 1
    print("已生成验收网页：" + str(page))
    return 0


if __name__ == "__main__":
    sys.exit(main())
