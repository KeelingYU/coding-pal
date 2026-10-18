"""Trial projects for the release scenarios, built with the real pal (tests/scenarios/scenarios.json names them).

Every fixture gets an empty folder and a context with pal(...) and run(...) bound to the scenario's isolated
PAL_HOME, and returns facts the objective checks need. The sample product is a tiny ledger web page with node tests.
"""
import json
from pathlib import Path

LEDGER_JS = """// 记账本的计算：页面和测试共用。
function addEntry(entries, entry) {
  if (!/^\\d{4}-\\d{2}-\\d{2}$/.test(entry.date || '')) throw new Error('日期要写成 2026-10-01 这样');
  if (!(Number(entry.amount) > 0)) throw new Error('金额要大于 0');
  return entries.concat([{ date: entry.date, kind: entry.kind === 'income' ? 'income' : 'expense', amount: Number(entry.amount), note: entry.note || '' }]);
}

function monthlyTotals(entries, month) {
  const inMonth = entries.filter(e => e.date.startsWith(month));
  const sum = kind => inMonth.filter(e => e.kind === kind).reduce((total, e) => total + e.amount, 0);
  return { income: sum('income'), expense: sum('expense'), balance: sum('income') - sum('expense') };
}

function formatAmount(n) {
  return n.toFixed(2);
}

if (typeof module !== 'undefined') module.exports = { addEntry, monthlyTotals, formatAmount };
"""

APP_JS = """// 页面交互：账目存在浏览器的 localStorage 里。
const KEY = 'ledger-entries';
let entries = JSON.parse(localStorage.getItem(KEY) || '[]');

function render() {
  const month = new Date().toISOString().slice(0, 7);
  const totals = monthlyTotals(entries, month);
  document.getElementById('income').textContent = formatAmount(totals.income);
  document.getElementById('expense').textContent = formatAmount(totals.expense);
  document.getElementById('balance').textContent = formatAmount(totals.balance);
  const list = document.getElementById('entries');
  list.innerHTML = '';
  for (const e of entries) {
    const li = document.createElement('li');
    li.textContent = `${e.date} ${e.kind === 'income' ? '收入' : '支出'} ${e.amount} ${e.note}`;
    list.appendChild(li);
  }
}

document.getElementById('entry-form').addEventListener('submit', event => {
  event.preventDefault();
  const form = event.target;
  try {
    entries = addEntry(entries, { date: form.date.value, kind: form.kind.value, amount: form.amount.value, note: form.note.value });
    localStorage.setItem(KEY, JSON.stringify(entries));
    form.reset();
    render();
  } catch (error) {
    alert(error.message);
  }
});

render();
"""

INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>记账本</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header>
    <h1>记账本</h1>
    <p class="hint">记录每天的收入和支出</p>
  </header>

  <main>
    <form id="entry-form">
      <input name="date" type="date" required>
      <select name="kind"><option value="expense">支出</option><option value="income">收入</option></select>
      <input name="amount" type="number" step="0.01" min="0.01" placeholder="金额" required>
      <input name="note" placeholder="备注">
      <button type="submit">记一笔</button>
    </form>

    <section id="summary">
      <h2>本月合计</h2>
      <p>收入 <span id="income">0.00</span>　支出 <span id="expense">0.00</span>　结余 <span id="balance">0.00</span></p>
    </section>

    <ul id="entries"></ul>
  </main>

  <footer>
    <p>数据只存在这台电脑的浏览器里。</p>
  </footer>

  <script src="ledger.js"></script>
  <script src="app.js"></script>
</body>
</html>
"""

STYLE_CSS = """body { font-family: -apple-system, "PingFang SC", sans-serif; max-width: 720px; margin: 40px auto; padding: 0 16px; color: #222; }
h1 { margin-bottom: 4px; }
.hint { color: #777; margin-top: 0; }
form { display: flex; gap: 8px; flex-wrap: wrap; margin: 24px 0; }
#summary { background: #f4f6f3; padding: 12px 16px; border-radius: 8px; }
#entries { padding-left: 20px; }
"""

TEST_JS = """const test = require('node:test');
const assert = require('node:assert');
const { addEntry, monthlyTotals, formatAmount } = require('../ledger.js');

test('记一笔后出现在列表里', () => {
  const list = addEntry([], { date: '2026-10-01', kind: 'expense', amount: '12.5', note: '午饭' });
  assert.deepStrictEqual(list, [{ date: '2026-10-01', kind: 'expense', amount: 12.5, note: '午饭' }]);
});

test('日期和金额不对时拒绝', () => {
  assert.throws(() => addEntry([], { date: '10/01', amount: 1 }));
  assert.throws(() => addEntry([], { date: '2026-10-01', amount: 0 }));
});

test('月度合计只算当月', () => {
  const list = [
    { date: '2026-10-01', kind: 'income', amount: 100, note: '' },
    { date: '2026-10-02', kind: 'expense', amount: 30, note: '' },
    { date: '2026-09-30', kind: 'expense', amount: 999, note: '' },
  ];
  assert.deepStrictEqual(monthlyTotals(list, '2026-10'), { income: 100, expense: 30, balance: 70 });
});

test('金额显示两位小数', () => {
  assert.strictEqual(formatAmount(3), '3.00');
});
"""

PRODUCT_MD = """# 产品说明

记账本：一个在浏览器里直接打开的记账小网页，给想随手记账的个人用。

## 给谁用、解决什么问题

个人记录每天的收入和支出，月底看合计。不需要注册，数据只存在自己电脑的浏览器里。

## 现在能做什么

- 记一笔：日期、收入或支出、金额、备注。
- 看本月合计：收入、支出、结余。

## 主要流程

打开 index.html → 填日期、类型、金额、备注 → 点"记一笔" → 下面的列表和本月合计马上更新。

## 关键规则

- 日期必填；金额要大于 0；金额显示两位小数。
- 数据存在浏览器的 localStorage，换电脑或清浏览器数据会丢。

## 术语
"""

ARCHITECTURE_MD = """# 实现说明

## 结构

- index.html：页面；style.css：样式。
- ledger.js：记账的计算（记一笔、月度合计、金额格式），页面和测试共用，不碰页面元素。
- app.js：页面交互，读写 localStorage（键 ledger-entries）。
- tests/：node 自带的测试（node --test tests/*.test.js），只测 ledger.js 这类纯计算。

## 约定

- 计算放 ledger.js 这样的纯函数文件，页面交互放 app.js；新功能的计算也单独成文件并配测试。
- 不引入任何依赖和构建工具，浏览器直接打开 index.html 就能用。

## 限制和容易踩的坑

- 用 file:// 打开时浏览器不允许读本机其他文件，页面不能 fetch 本地 JSON。
"""

RULES_MD = """
## 构建与测试

- 不需要安装依赖：用浏览器直接打开 index.html。
- 测试：`node --test tests/*.test.js`（Node 18 以上）。
"""


def write(root, files):
    for rel, text in files.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def set_config(root, **changes):
    path = Path(root) / ".pal" / "config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    for key, value in changes.items():
        if isinstance(value, dict):
            cfg.setdefault(key, {}).update(value)
        else:
            cfg[key] = value
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ledger(ctx, root, releases=False):
    """The ledger product, its first version merged, with coding-pal set up and the long-term docs filled in."""
    write(root, {"index.html": INDEX_HTML, "ledger.js": LEDGER_JS, "app.js": APP_JS, "style.css": STYLE_CSS,
                 "tests/ledger.test.js": TEST_JS, "README.md": "# 记账本\n\n浏览器直接打开 index.html。测试：`node --test tests/*.test.js`。\n",
                 "package.json": '{\n  "name": "ledger",\n  "version": "1.2.0",\n  "private": true\n}\n'})
    ctx.run(root, "git", "init", "-q", "-b", "main")
    ctx.run(root, "git", "add", "-A")
    ctx.run(root, "git", "commit", "-q", "-m", "first version of the ledger page")
    ctx.pal(root, "init", "--name", "记账本")
    set_config(root, checks="node --test tests/*.test.js", release={"enabled": releases})
    rules = (Path(root) / "AGENTS.md").read_text(encoding="utf-8")
    write(root, {"docs/PRODUCT.md": PRODUCT_MD, "docs/ARCHITECTURE.md": ARCHITECTURE_MD, "AGENTS.md": rules + RULES_MD})
    ctx.run(root, "git", "add", "-A")
    ctx.run(root, "git", "commit", "-q", "-m", "docs: product and architecture notes")


def task_commit(ctx, worktree, files, changelog, message):
    write(worktree, files)
    if changelog:
        log = Path(worktree) / "CHANGELOG.md"
        log.write_text(log.read_text(encoding="utf-8") + f"- {changelog}\n", encoding="utf-8")
    ctx.run(worktree, "git", "add", "-A")
    ctx.run(worktree, "git", "commit", "-q", "-m", message)


def replace(path, old, new):
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, (path, old)
    path.write_text(text.replace(old, new), encoding="utf-8")


# ---------- fixtures ----------

def empty(ctx, root):
    """A brand-new, empty folder: no git, no coding-pal."""
    return {}


def plain_ledger(ctx, root):
    ledger(ctx, root)
    return {}


EXPORT_REQUIREMENT = """# 需求说明：导出 CSV

> 用户已确认（2026-10-10）。

## 为什么做

月底要把账目交给家里人一起看，需要一份 Excel 能打开的文件。

## 做什么

页面上加"导出本月 CSV"按钮，点了下载当月账目：日期、类型、金额、备注四列。

## 不做什么

不导出其他月份；不做 Excel 格式。

## 使用流程

打开记账本 → 点"导出本月 CSV" → 浏览器下载 ledger-2026-10.csv → 用 Excel 打开能看到中文。

## 业务规则与特殊情况

当月没有账目时，按钮旁边提示"本月还没有账目"，不下载空文件。

## 验收标准

- 当月有 3 笔账目时点导出 → 下载的文件有表头和 3 行，Excel 打开中文不乱码。
- 当月没有账目时点导出 → 提示"本月还没有账目"，不下载。

## 假设

文件用 UTF-8 加 BOM，Excel 能直接打开。

## 留到后面再定

无
"""

EXPORT_PLAN = """# 实现方案：导出 CSV

## 开头一页（给用户）

- 你会看到什么：页面合计下面多一个"导出本月 CSV"按钮；没有账目时提示"本月还没有账目"。
- 取舍和代价：文件加 BOM 让 Excel 直接认出中文，代价是少数文本编辑器第一行会多一个看不见的字符。
- 要不要评审：不要，只读数据、不改数据，也不对外发送。
- 谁来实现：派 1 个实现员做两步：新建 export.js 生成 CSV 并补测试；在页面加按钮并接上。

## 技术部分（给实现员）

### 整体做法和理由

新建 export.js 放纯函数 toCsv(entries, month)，按 ledger.js 的写法导出给测试；app.js 只负责按钮和下载。

### 步骤

1. export.js + tests/export.test.js：toCsv 输出表头和当月行，金额两位小数，开头加 BOM。
2. index.html 加按钮，app.js 接上下载和"本月还没有账目"提示。
"""


def continue_tasks(ctx, root):
    """Two unmerged tasks: 导出 CSV waits for the user on the plan's first page; 深色模式 is half done by the Agent."""
    ledger(ctx, root)
    ctx.pal(root, "new", "export-csv", "--type", "普通需求", "--title", "导出 CSV")
    folder = Path(root) / ".pal" / "tasks" / "export-csv"
    write(folder, {"requirement.md": EXPORT_REQUIREMENT, "plan.md": EXPORT_PLAN})
    ctx.pal(root, "set", "export-csv", "阶段=方案", "状态=等你", '等你做=看方案开头一页，回复"继续"或提意见',
            "影响范围=页面合计区、app.js、新文件 export.js", "需求摘要=页面加导出本月 CSV 按钮，Excel 能直接打开",
            "方案摘要=新建 export.js 生成 CSV，页面加按钮；派 1 个实现员做两步")
    ctx.pal(root, "new", "dark-mode", "--type", "小改动", "--title", "深色模式")
    worktree = Path(root) / ".worktrees" / "dark-mode"
    task_commit(ctx, worktree, {"style.css": STYLE_CSS + "\n@media (prefers-color-scheme: dark) {\n  body { background: #161a18; color: #e6ebe7; }\n}\n"},
                None, "dark colors that follow the system")
    ctx.pal(root, "set", "dark-mode", "影响范围=style.css、页面标题栏", "下一步=在标题栏加一个手动切换按钮，然后请用户看效果")
    return {"branches": {"export-csv": ctx.git(root, "rev-parse", "feature/export-csv")}}


COMPLEX_REQUIREMENT = """# 需求说明：月度报表和导出

> 用户已确认（2026-10-10）。

## 为什么做

月底想看清楚这个月每天花了多少，并且把账目交给家里人用 Excel 看。

## 做什么

两块互不依赖的功能：
1. 月度报表：页面上选一个月份，显示这个月每一天的收入、支出、结余（只列有账目的日子），最后一行是全月合计。
2. 导出 CSV：把选中月份的账目导出成 CSV 文件下载，四列：日期、类型（收入/支出）、金额（两位小数）、备注；Excel 直接打开不乱码。

## 不做什么

不做图表；不导出 Excel 格式；不改记一笔的方式。

## 使用流程

打开记账本 → 在"月度报表"里选月份 → 看到按天的表格和全月合计 → 点"导出这个月"下载 ledger-<月份>.csv。

## 业务规则与特殊情况

- 选中的月份没有账目：报表显示"这个月还没有账目"，导出按钮不可点。
- 金额都显示两位小数。

## 验收标准

- 选 2026-10，有 10-01 收入 100、10-01 支出 30、10-03 支出 20 → 报表两行：10-01 收入 100.00 支出 30.00 结余 70.00；10-03 支出 20.00 结余 -20.00；合计收入 100.00 支出 50.00 结余 50.00。
- 选没有账目的月份 → 显示"这个月还没有账目"，导出按钮灰掉。
- 点导出 → 下载 ledger-2026-10.csv，第一行是表头，Excel 打开中文正常。

## 假设

CSV 用 UTF-8 加 BOM；月份选择默认是当前月。

## 留到后面再定

无
"""


def complex_task(ctx, root):
    """A complex requirement already confirmed by the user, waiting for its plan."""
    ledger(ctx, root)
    ctx.pal(root, "new", "report-export", "--type", "复杂需求", "--title", "月度报表和导出")
    write(Path(root) / ".pal" / "tasks" / "report-export", {"requirement.md": COMPLEX_REQUIREMENT})
    ctx.pal(root, "set", "report-export", "阶段=方案", "影响范围=页面、app.js、新的报表和导出模块",
            "需求摘要=按天的月度报表和导出 CSV，两块互不依赖", "下一步=写实现方案，分两组并行：报表、导出；最后联调接到页面上")
    decisions = Path(root) / ".worktrees" / "report-export" / "docs" / "DECISIONS.md"
    decisions.write_text(decisions.read_text(encoding="utf-8") + "2026-10-10｜确认月度报表和导出的需求说明｜两块互不依赖，可以并行做\n", encoding="utf-8")
    ctx.run(decisions.parent.parent, "git", "commit", "-q", "-am", "docs: record the confirmed requirement")
    return {}


def two_tasks(ctx, root):
    """Two tasks waiting for acceptance, both touching index.html in different places."""
    ledger(ctx, root)
    ctx.pal(root, "new", "export-csv", "--type", "普通需求", "--title", "导出 CSV")
    wt = Path(root) / ".worktrees" / "export-csv"
    export_js = ("// 导出 CSV：把当月账目变成 Excel 能直接打开的文本。\nfunction toCsv(entries, month) {\n"
                 "  const rows = entries.filter(e => e.date.startsWith(month))\n"
                 "    .map(e => [e.date, e.kind === 'income' ? '收入' : '支出', e.amount.toFixed(2), e.note].join(','));\n"
                 "  return '\\ufeff' + ['日期,类型,金额,备注'].concat(rows).join('\\n');\n}\n\n"
                 "if (typeof document !== 'undefined') {\n  document.getElementById('export').addEventListener('click', () => {\n"
                 "    const month = new Date().toISOString().slice(0, 7);\n"
                 "    const link = document.createElement('a');\n"
                 "    link.href = URL.createObjectURL(new Blob([toCsv(entries, month)], { type: 'text/csv' }));\n"
                 "    link.download = `ledger-${month}.csv`;\n    link.click();\n  });\n}\n\n"
                 "if (typeof module !== 'undefined') module.exports = { toCsv };\n")
    export_test = ("const test = require('node:test');\nconst assert = require('node:assert');\nconst { toCsv } = require('../export.js');\n\n"
                   "test('导出当月账目', () => {\n  const csv = toCsv([{ date: '2026-10-01', kind: 'expense', amount: 3, note: '面包' }], '2026-10');\n"
                   "  assert.strictEqual(csv, '\\ufeff日期,类型,金额,备注\\n2026-10-01,支出,3.00,面包');\n});\n")
    write(wt, {"export.js": export_js, "tests/export.test.js": export_test})
    replace(wt / "index.html", "    <ul id=\"entries\"></ul>\n",
            "    <p><button id=\"export\" type=\"button\">导出本月 CSV</button></p>\n\n    <ul id=\"entries\"></ul>\n")
    replace(wt / "index.html", '  <script src="app.js"></script>\n', '  <script src="app.js"></script>\n  <script src="export.js"></script>\n')
    task_commit(ctx, wt, {}, "新增：导出本月账目为 CSV，Excel 能直接打开", "export the month as CSV")
    ctx.pal(root, "set", "export-csv", "阶段=验收", "状态=等你", '等你做=打开 index.html 点"导出本月 CSV"，满意就回复"验收通过"',
            "影响范围=页面、新文件 export.js")
    ctx.pal(root, "new", "dark-mode", "--type", "小改动", "--title", "深色模式")
    wt = Path(root) / ".worktrees" / "dark-mode"
    theme_js = ("// 深色模式：标题栏的按钮切换，选择存在浏览器里。\nconst THEME_KEY = 'ledger-theme';\n"
                "document.documentElement.dataset.theme = localStorage.getItem(THEME_KEY) || 'light';\n"
                "document.getElementById('theme').addEventListener('click', () => {\n"
                "  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';\n"
                "  document.documentElement.dataset.theme = next;\n  localStorage.setItem(THEME_KEY, next);\n});\n")
    write(wt, {"theme.js": theme_js, "style.css": STYLE_CSS + "[data-theme=\"dark\"] body { background: #161a18; color: #e6ebe7; }\n"
                                                       "[data-theme=\"dark\"] #summary { background: #232a26; }\n"})
    replace(wt / "index.html", "    <h1>记账本</h1>\n", "    <h1>记账本</h1>\n    <button id=\"theme\" type=\"button\">深色模式</button>\n")
    replace(wt / "index.html", '  <link rel="stylesheet" href="style.css">\n', '  <link rel="stylesheet" href="style.css">\n  <script defer src="theme.js"></script>\n')
    task_commit(ctx, wt, {}, "新增：标题栏可以切换深色模式", "dark mode toggle")
    ctx.pal(root, "set", "dark-mode", "阶段=验收", "状态=等你", '等你做=打开 index.html 点标题栏的"深色模式"，满意就回复"可以"',
            "影响范围=页面标题栏、style.css")
    return {}


def release_ready(ctx, root):
    """A project that publishes versions: v1.2.0 out, two merged tasks waiting, a remote repository to push to."""
    ledger(ctx, root, releases=True)
    set_config(root, release={"version_files": ["package.json"], "checks": "node --test tests/*.test.js", "github_release": False})
    ctx.run(root, "git", "tag", "-a", "v1.2.0", "-m", "v1.2.0 记账本第一个正式版")
    remote = Path(root).parent / "remote.git"
    ctx.run(root.parent, "git", "init", "-q", "--bare", str(remote))
    ctx.run(root, "git", "remote", "add", "origin", str(remote))
    ctx.run(root, "git", "push", "-q", "origin", "main", "v1.2.0")
    ctx.pal(root, "new", "daily-spend", "--type", "普通需求", "--title", "每天花了多少")
    wt = Path(root) / ".worktrees" / "daily-spend"
    replace(wt / "ledger.js", "function formatAmount(n) {",
            "function dailyExpense(entries, month) {\n  const days = {};\n  for (const e of entries) {\n"
            "    if (e.kind === 'expense' && e.date.startsWith(month)) days[e.date] = (days[e.date] || 0) + e.amount;\n  }\n  return days;\n}\n\n"
            "function formatAmount(n) {")
    replace(wt / "ledger.js", "module.exports = { addEntry, monthlyTotals, formatAmount };",
            "module.exports = { addEntry, monthlyTotals, dailyExpense, formatAmount };")
    replace(wt / "index.html", "    </section>\n", "      <h3>每天花了多少</h3>\n      <ul id=\"daily\"></ul>\n    </section>\n")
    replace(wt / "app.js", "  const list = document.getElementById('entries');",
            "  const daily = document.getElementById('daily');\n  daily.innerHTML = '';\n"
            "  for (const [day, amount] of Object.entries(dailyExpense(entries, month)).sort()) {\n"
            "    const li = document.createElement('li');\n    li.textContent = `${day} 支出 ${formatAmount(amount)}`;\n    daily.appendChild(li);\n  }\n"
            "  const list = document.getElementById('entries');")
    daily_test = ("const test = require('node:test');\nconst assert = require('node:assert');\nconst { dailyExpense } = require('../ledger.js');\n\n"
                  "test('按天汇总当月支出', () => {\n  const list = [\n    { date: '2026-10-01', kind: 'expense', amount: 10, note: '' },\n"
                  "    { date: '2026-10-01', kind: 'expense', amount: 5, note: '' },\n    { date: '2026-10-02', kind: 'income', amount: 99, note: '' },\n"
                  "    { date: '2026-09-30', kind: 'expense', amount: 7, note: '' },\n  ];\n"
                  "  assert.deepStrictEqual(dailyExpense(list, '2026-10'), { '2026-10-01': 15 });\n});\n")
    task_commit(ctx, wt, {"tests/daily.test.js": daily_test}, "新增：本月合计下面列出每天花了多少", "daily spending")
    ctx.pal(root, "merge", "daily-spend")
    ctx.pal(root, "new", "two-decimals", "--type", "小改动", "--title", "列表金额两位小数")
    wt = Path(root) / ".worktrees" / "two-decimals"
    replace(wt / "app.js", "${e.amount} ${e.note}", "${formatAmount(e.amount)} ${e.note}")
    task_commit(ctx, wt, {}, "修正：账目列表里的金额统一显示两位小数（原来 12.5 显示成 12.5）", "two decimals in the list")
    ctx.pal(root, "merge", "two-decimals")
    return {"remote": str(remote)}


ENTRIES = [
    {"date": "2026-10-01", "kind": "income", "amount": 8000, "note": "工资"},
    {"date": "2026-10-02", "kind": "expense", "amount": 1, "note": "测试数据1"},
    {"date": "2026-10-02", "kind": "expense", "amount": 2, "note": "测试数据2"},
    {"date": "2026-10-03", "kind": "expense", "amount": 3200, "note": "购买测试仪器（报销）"},
    {"date": "2026-10-04", "kind": "expense", "amount": 36.5, "note": "午饭"},
    {"date": "2026-10-05", "kind": "income", "amount": 0.01, "note": "测试"},
    {"date": "2026-10-06", "kind": "expense", "amount": 129, "note": "电费"},
]


def data_cleanup(ctx, root):
    """Ledger data with three test records and one real expense whose note also mentions 测试."""
    ledger(ctx, root)
    write(root, {"data/entries.json": json.dumps(ENTRIES, ensure_ascii=False, indent=2) + "\n"})
    rules = Path(root) / "AGENTS.md"
    rules.write_text(rules.read_text(encoding="utf-8") + "- data/entries.json：财务导出的本月账目，月底对账用。\n", encoding="utf-8")
    ctx.run(root, "git", "add", "-A")
    ctx.run(root, "git", "commit", "-q", "-m", "data: this month's entries")
    keep = [e for e in ENTRIES if not e["note"].startswith("测试")]
    return {"keep": keep}


CATEGORY_PROMPT = """# 记一笔时的分类建议（发给模型的说明）

根据用户写的备注，从下面的分类里选一个最合适的，只回答分类名。拿不准就回答「其他」，不要编造新分类。

可选分类：餐饮、交通、购物、其他

各分类的常见说法：
- 餐饮：午饭、晚饭、早餐、外卖、咖啡
- 交通：地铁、打车、公交、加油
- 购物：衣服、超市、网购
"""

AI_CASES = [
    {"id": "1", "note": "午饭 36.5", "expect": "餐饮"},
    {"id": "2", "note": "打车回家", "expect": "交通"},
    {"id": "3", "note": "超市买菜", "expect": "购物"},
    {"id": "4", "note": "给朋友的生日红包", "expect": "其他"},
]

AI_RUN_MJS = """// AI 案例（离线模拟，几秒、不花 token）：按 ai/category-prompt.md 的可选分类和常见说法，模拟模型给每条备注选分类，和案例的期望比。
// 用法：node ai-cases/run.mjs [--case <编号>]
import { readFileSync } from 'node:fs';
const prompt = readFileSync(new URL('../ai/category-prompt.md', import.meta.url), 'utf8');
const categories = (prompt.match(/^可选分类：(.+)$/m)?.[1] || '').split('、').map((s) => s.trim()).filter(Boolean);
const words = Object.fromEntries([...prompt.matchAll(/^- (.+?)：(.+)$/gm)].map((m) => [m[1].trim(), m[2].split('、').map((s) => s.trim())]));
const cases = JSON.parse(readFileSync(new URL('./cases.json', import.meta.url), 'utf8'));
const at = process.argv.indexOf('--case');
const only = at > 0 ? process.argv[at + 1] : null;
let failed = 0;
for (const c of cases.filter((c) => !only || c.id === only)) {
  const guess = categories.find((cat) => (words[cat] || []).some((w) => c.note.includes(w))) || '其他';
  const ok = guess === c.expect && categories.includes(c.expect);
  if (!ok) failed += 1;
  console.log(`（模拟）案例 ${c.id}「${c.note}」→ ${guess}，期望 ${c.expect}：${ok ? '通过' : '未通过'}`);
}
process.exit(failed ? 1 : 0);
"""


def ai_ledger(ctx, root):
    """The ledger with an AI feature: category suggestions driven by a prompt, and cheap (simulated, offline) AI cases."""
    ledger(ctx, root)
    write(root, {"ai/category-prompt.md": CATEGORY_PROMPT,
                 "ai-cases/cases.json": json.dumps(AI_CASES, ensure_ascii=False, indent=2) + "\n",
                 "ai-cases/run.mjs": AI_RUN_MJS})
    replace(Path(root) / "docs" / "PRODUCT.md", "- 看本月合计：收入、支出、结余。",
            "- 看本月合计：收入、支出、结余。\n- 记一笔时，AI 根据备注建议一个分类（发给模型的说明在 ai/category-prompt.md）。")
    rules = Path(root) / "AGENTS.md"
    rules.write_text(rules.read_text(encoding="utf-8")
                     + "- AI 案例：`node ai-cases/run.mjs`（加 `--case <编号>` 只跑一个），案例在 ai-cases/cases.json。改了 ai/ 下的提示词就算改到 AI 行为。"
                       "试验项目里的运行是离线模拟，几秒、不花 token。\n", encoding="utf-8")
    set_config(root, ai_cases={"cost": "cheap", "run": "node ai-cases/run.mjs [--case <编号>]"})
    ctx.run(root, "git", "add", "-A")
    ctx.run(root, "git", "commit", "-q", "-m", "feat: AI category suggestions with offline AI cases")
    return {}


FIXTURES = {"empty": empty, "ledger": plain_ledger, "continue": continue_tasks, "complex": complex_task,
            "two_tasks": two_tasks, "release": release_ready, "data": data_cleanup, "ai_ledger": ai_ledger}
