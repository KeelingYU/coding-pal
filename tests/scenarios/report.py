#!/usr/bin/env python3
"""把各场景的评判结果做成发布验收页。

用法：python3 tests/scenarios/report.py <结果文件夹> <页面数据.json>
页面数据写首屏、请你试、已知问题等（格式同技能的 templates/acceptance.json；checked 只写主 Agent 自查的项，标题注明"自查"）。
checked 由各场景的 verdict.json 生成：每个场景一项，证据是评判引用的对话片段（前后各留 40 行的节选，关键行标出）。
各场景的轮数、用时和用量追加到"技术细节"。网页生成到 <结果文件夹>/report/。
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins" / "coding-pal" / "skills" / "coding-pal" / "scripts"))
sys.dont_write_bytecode = True
import render_acceptance  # noqa: E402

PLATFORMS = {"codex": "Codex", "claude": "Claude Code"}
CONTEXT = 40


def excerpt(folder, name, source, lines, serial):
    """Copy the cited lines with some context into excerpts/, return (file, highlight) relative to the results folder."""
    text = (folder / name / source).read_text(encoding="utf-8").splitlines()
    start, end = max(1, int(lines[0])), min(len(text), int(lines[-1]))
    end = min(end, start + render_acceptance.HIGHLIGHT_MAX - 1)
    first, last = max(1, start - CONTEXT), min(len(text), end + CONTEXT)
    target = folder / "excerpts" / f"{name}-{serial}.txt"
    target.parent.mkdir(exist_ok=True)
    target.write_text("\n".join(text[first - 1:last]) + "\n", encoding="utf-8")
    return target.relative_to(folder).as_posix(), [start - first + 1, end - first + 1]


def usage_line(meta):
    usage = meta.get("usage") or {}
    tokens = usage.get("total_tokens") or sum(v for k, v in usage.items() if k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                                                                     "cache_creation_input_tokens"))
    money = f"，约 {meta['cost_usd']} 美元" if meta.get("cost_usd") else ""
    return (f"场景 {meta['id']} {meta['title']} · {PLATFORMS[meta['platform']]}：{meta.get('turns', 0)} 轮，"
            f"约 {round((meta.get('seconds') or 0) / 60)} 分钟，约 {tokens / 10000:.0f} 万 token{money}；{meta.get('stop', '')}")


def main():
    folder, page = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
    data = json.loads(page.read_text(encoding="utf-8"))
    checked, usage = [], []
    order = lambda p: (int(p.parent.name.split("-")[1]), list(PLATFORMS).index(p.parent.name.split("-")[0]))
    for verdict_path in sorted(folder.glob("*-*/verdict.json"), key=order):
        name = verdict_path.parent.name
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        meta = json.loads((verdict_path.parent / "meta.json").read_text(encoding="utf-8"))
        evidence = []
        for item in verdict["items"]:
            for cited in item.get("evidence", [])[:1]:
                file, highlight = excerpt(folder, name, cited["file"], cited["lines"], len(evidence) + 1)
                mark = "" if item["result"] == "通过" else f"（{item['result']}）"
                said = re.sub(r"^这说明[了：:，, ]*", "", cited["caption"])  # 网页会在前面加"这说明："
                evidence.append({"caption": f"第 {item['rubric']} 条{mark}：{said}"[:render_acceptance.LIMITS["caption"]],
                                 "file": file, "highlight": highlight})
        checked.append({"title": f"场景 {meta['id']} {meta['title']} · {PLATFORMS[meta['platform']]}", "result": verdict["result"],
                        "summary": verdict["summary"][:render_acceptance.LIMITS["summary"]], "evidence": evidence})
        usage.append(usage_line(meta))
    data["checked"] = data.get("checked", []) + checked  # 页面数据里可以先写主 Agent 自查的项
    data["details"] = (data.get("details", "").rstrip() + "\n\n各场景的轮数、用时和用量：\n" + "\n".join(usage)).strip()
    out = folder / "acceptance.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    commit = __import__("subprocess").run(["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT), capture_output=True, text=True).stdout.strip()
    try:
        result = render_acceptance.generate(out, folder, folder / "report", project="coding-pal 发布前场景测试",
                                            revision=f"rebuild/1.0 @ {commit}")
    except render_acceptance.ReportError as e:
        sys.exit(f"生成失败：{e}")
    print(f"已生成发布验收页：{result}")


if __name__ == "__main__":
    main()
