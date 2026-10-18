#!/usr/bin/env bash
# 插件安装测试：在隔离配置里安装 coding-pal，检查装了什么、没装什么、装好的 pal 能不能跑。不调用模型。
# 用法：tests/install/run.sh [claude|codex|all] [--local | 目录仓库来源]
#   默认从远端目录仓库 KeelingYU/pal-plugins 安装：发布后核对（私有仓库沿用本机 Git 登录）。
#   --local：把本仓库 plugins/coding-pal 里已登记到 Git 的文件（含未提交的修改）放进临时的本机目录再安装：开发中、发布前核对。
set -uo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
platforms="${1:-all}"; [ "$platforms" = all ] && platforms="claude codex"
source="${2:-KeelingYU/pal-plugins}"
codex_bin="${CODEX_BIN:-/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex}"  # 桌面版自带的更新
[ -x "$codex_bin" ] || codex_bin=codex
work="$(mktemp -d)"
fail=0
ok()  { echo "  通过：$1"; }
bad() { echo "  失败：$1"; fail=1; }

if [ "$source" = --local ]; then
  source="$work/catalog"
  mkdir -p "$source/.claude-plugin" "$source/.agents/plugins" "$source/plugins"
  (cd "$root" && git ls-files -z -- plugins/coding-pal | tar --null -T - -cf -) | tar -xf - -C "$source"
  printf '%s\n' '{"name": "pal-plugins", "owner": {"name": "KeelingYU"}, "plugins": [{"name": "coding-pal", "source": "./plugins/coding-pal"}]}' \
    > "$source/.claude-plugin/marketplace.json"
  printf '%s\n' '{"name": "pal-plugins", "plugins": [{"name": "coding-pal", "source": {"source": "local", "path": "./plugins/coding-pal"}, "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Developer Tools"}]}' \
    > "$source/.agents/plugins/marketplace.json"
  want_dir() { echo "$source/plugins/coding-pal"; }
else
  # 目录仓库登记的开发仓库提交（Claude Code 清单里是 sha，Codex 清单里 ref 就是提交号），取出那一版的插件文件夹
  want_dir() {
    local sha; sha="$(python3 -c "import json,sys;s=json.load(open(sys.argv[1]))['plugins'][0]['source'];print(s.get('sha') or s['ref'])" "$1")"
    if [ ! -d "$work/want-$sha" ]; then
      mkdir -p "$work/want-$sha" && git -C "$root" archive "$sha" plugins/coding-pal | tar -x -C "$work/want-$sha" || { echo ""; return; }
    fi
    echo "$work/want-$sha/plugins/coding-pal"
  }
fi

# 装好的插件和应该装的逐文件一致（忽略平台和系统加的文件）；技能引用的文件都在；装好的 pal 能在 Python 3.9 起跑
check_plugin() {  # 装好的插件根目录 应该装的插件文件夹
  if [ -z "$2" ]; then bad "取不到应该装的那一版插件"; return; fi
  if diff -r -x .in_use -x .git -x .DS_Store -x .cc-writes "$2" "$1" >"$work/diff.txt"; then
    ok "装好的插件和应该装的逐文件一致"
  else
    bad "装好的插件和应该装的不一致：$(head -5 "$work/diff.txt" | tr '\n' ' ')"
  fi
  local skill="$1/skills/coding-pal" miss=""
  for r in $(grep -rhoE '(stages|references|roles|templates|scripts|assets)/[A-Za-z0-9_./-]+\.(md|json|py|html|css|js)' "$skill" | sort -u); do
    [ -e "$skill/$r" ] || miss="$miss $r"
  done
  [ -z "$miss" ] && ok "技能引用的文件都在" || bad "技能引用的文件缺失：$miss"
  for py in python3 /usr/bin/python3; do
    command -v "$py" >/dev/null || continue
    if PYTHONDONTWRITEBYTECODE=1 "$py" "$skill/scripts/pal.py" -h >/dev/null 2>"$work/pal.txt"; then
      ok "装好的 pal 能运行（$("$py" -c 'import platform;print("Python " + platform.python_version())')）"
    else
      bad "装好的 pal 运行失败（$py）：$(tail -2 "$work/pal.txt" | tr '\n' ' ')"
    fi
  done
}

# 隔离目录里不能有开发仓库的其他内容（过程文件、测试、作者工具）
check_nothing_else() {  # 隔离根目录 目录仓库副本（本机目录时为空）
  local hits
  hits="$(find "$1" \( -name HANDOFF.md -o -name SPEC.md -o -name verify.py -o -name budgets.json -o -name 'sample_*.py' -o -name 'test_*.py' -o -name browser-cases.md -o -name .pal \) 2>/dev/null)"
  [ -z "$hits" ] && ok "没有装进开发仓库的过程文件、测试和作者工具" || bad "装进了不该有的文件：$(echo "$hits" | head -5 | tr '\n' ' ')"
  while read -r g; do
    case "$(git -C "$(dirname "$g")" remote get-url origin 2>/dev/null)" in *coding-pal*) bad "下载了开发仓库的版本历史：$g" ;; esac
  done < <(find "$1" -name .git -type d 2>/dev/null)
  [ -n "$2" ] || return 0
  local extra; extra="$(ls -A "$2" | grep -vxE '\.git|\.claude-plugin|\.agents|README\.md|README\.zh-CN\.md|LICENSE|\.gitignore')"
  [ -z "$extra" ] && ok "目录仓库副本里只有清单、说明和许可证" || bad "目录仓库副本里有多余内容：$extra"
}

# 安装前后，已有项目的文件不变
make_project() { local p="$work/project-$1"; mkdir -p "$p/docs" && echo "# 已有项目" > "$p/AGENTS.md" && echo "需求" > "$p/docs/PRODUCT.md"; echo "$p"; }
snapshot() { (cd "$1" && find . -type f -exec shasum {} \; | sort); }

for p in $platforms; do
  echo "[$p]"
  iso="$work/$p"; mkdir -p "$iso"
  proj="$(make_project "$p")"; before="$(snapshot "$proj")"
  case "$p" in
    claude)
      (cd "$proj" && CLAUDE_CONFIG_DIR="$iso" claude plugin marketplace add "$source" >"$work/$p.log" 2>&1 \
        && CLAUDE_CONFIG_DIR="$iso" claude plugin install coding-pal@pal-plugins >>"$work/$p.log" 2>&1) \
        || { bad "安装命令失败：$(tail -3 "$work/$p.log" | tr '\n' ' ')"; continue; }
      manifest="$(find "$iso/plugins/marketplaces" -path '*/.claude-plugin/marketplace.json' 2>/dev/null | head -1)"
      mkt="${manifest%/.claude-plugin/marketplace.json}" ;;
    codex)
      [ -f "$HOME/.codex/auth.json" ] && ln -s "$HOME/.codex/auth.json" "$iso/auth.json"
      (cd "$proj" && CODEX_HOME="$iso" "$codex_bin" plugin marketplace add "$source" >"$work/$p.log" 2>&1 \
        && CODEX_HOME="$iso" "$codex_bin" plugin add coding-pal@pal-plugins >>"$work/$p.log" 2>&1) \
        || { bad "安装命令失败：$(tail -3 "$work/$p.log" | tr '\n' ' ')"; continue; }
      manifest="$(find "$iso" -path '*/pal-plugins/.agents/plugins/marketplace.json' 2>/dev/null | head -1)"
      mkt="${manifest%/.agents/plugins/marketplace.json}" ;;
  esac
  ok "安装命令成功（$source）"
  installed="$(find "$iso" -path '*/skills/coding-pal/SKILL.md' 2>/dev/null | grep -v "^${mkt:-/nonexistent}/" | head -1)"
  if [ -z "$installed" ]; then bad "找不到装好的技能"; continue; fi
  check_plugin "$(dirname "$(dirname "$(dirname "$installed")")")" "$(want_dir "${manifest:-$source/.claude-plugin/marketplace.json}")"
  check_nothing_else "$iso" "$mkt"
  [ "$before" = "$(snapshot "$proj")" ] && ok "已有项目的文件没有变化" || bad "已有项目的文件被改动"
done

echo
if [ "$fail" = 0 ]; then echo "安装测试全部通过"; rm -rf "$work"
else echo "安装测试有失败项，现场保留在：$work"; fi
exit "$fail"
