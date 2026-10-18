(function () {
  "use strict";
  var THEME_KEY = "pal-theme", SCROLL_KEY = "pal-scroll", SIDE_KEY = "pal-side", CLOSED_KEY = "pal-side-closed";
  // 图标来自 Lucide（ISC 许可）：chevron-right、chevrons-left、chevrons-right、arrow-up-right
  var ICONS = { fold: '<path d="m9 18 6-6-6-6"/>', hide: '<path d="m11 17-5-5 5-5"/><path d="m18 17-5-5 5-5"/>',
    show: '<path d="m6 17 5-5-5-5"/><path d="m13 17 5-5-5-5"/>', open: '<path d="M7 7h10v10"/><path d="M7 17 17 7"/>' };
  var hub = window.PAL_HUB || null;
  var current = window.PAL_CURRENT || "";
  var registered = window.PAL_REGISTERED || [];
  var app = document.getElementById("app");
  var ORDER = ["等你", "Agent 在做", "排队", "暂停", "待发布"];
  var STATUS_CLASS = { "等你": "s-wait", "Agent 在做": "s-run", "排队": "s-queue", "暂停": "s-pause", "待发布": "s-ship" };

  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function when(t) { return t ? String(t).replace(/^\d{4}-/, "").replace("T", " ") : ""; }
  function newestFirst(a, b) { return a.updated < b.updated ? 1 : a.updated > b.updated ? -1 : 0; }
  function byStatus(a, b) { return ORDER.indexOf(a.status) - ORDER.indexOf(b.status) || newestFirst(a, b); }
  function count(tasks, statuses) { return tasks.filter(function (t) { return statuses.indexOf(t.status) >= 0; }).length; }
  function titleOf(p, id) { var t = p.tasks.filter(function (x) { return x.id === id; })[0]; return t ? t.title : id; }
  function icon(name) { return '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">' + ICONS[name] + "</svg>"; }
  function load(key, fallback) { try { var v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; } }
  function save(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* 存不了就只在本次打开时有效 */ } }
  function narrow() { return window.matchMedia("(max-width: 960px)").matches; }

  // 每个项目的数据在它自己的 board.js 里；projects.js 登记了本机所有项目，侧边栏靠它把其他项目也读进来。
  function allProjects() {
    var seen = {}, loaded = [], ordered = [];
    (window.PAL_PROJECTS || []).forEach(function (p) { if (p && !seen[p.path]) { seen[p.path] = true; loaded.push(p); } });
    registered.forEach(function (r) { loaded.forEach(function (p) { if (p.path === r.path) ordered.push(p); }); });
    loaded.forEach(function (p) { if (ordered.indexOf(p) < 0) ordered.push(p); });
    return ordered;
  }
  function loadRegistered(done) {
    var have = (window.PAL_PROJECTS || []).map(function (p) { return p.path; });
    var todo = registered.filter(function (r) { return r.data && have.indexOf(r.path) < 0; });
    var left = todo.length, finished = false;
    function finish() { if (!finished) { finished = true; done(); } }
    if (!left) return finish();
    todo.forEach(function (r) {
      var s = document.createElement("script");
      s.src = r.data;
      s.onload = s.onerror = function () { if (--left === 0) finish(); };
      document.head.appendChild(s);
    });
    setTimeout(finish, 2500);
  }

  function themeMode() { var t = document.documentElement.getAttribute("data-theme"); return t === "light" || t === "dark" ? t : "system"; }
  function setTheme(mode) {
    try { if (mode === "system") localStorage.removeItem(THEME_KEY); else localStorage.setItem(THEME_KEY, mode); } catch (e) { /* 存不了就只对当前页面生效 */ }
    if (mode === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", mode);
    Array.prototype.forEach.call(document.querySelectorAll("[data-theme-choice]"), function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-theme-choice") === mode));
    });
  }

  function typeTags(t) { return '<span class="tag">' + esc(t.type) + "</span>" + (t.risk === "高" ? '<span class="tag risk">高风险</span>' : ""); }
  function statusChip(t) { return '<span class="status ' + (STATUS_CLASS[t.status] || "") + '">' + esc(t.status) + "</span>"; }
  function badge(n) { return n ? '<span class="badge" title="等你处理">' + n + "</span>" : ""; }
  // 验收阶段的任务生成过验收网页时，直接链过去（新标签页打开，看板留在原处）。
  function reportLink(t) {
    return t.report ? ' <a class="report-link" href="' + esc(t.report) + '" target="_blank" rel="noopener">' + icon("open") + "打开验收网页</a>" : "";
  }
  function track(t) {
    var stages = t.stages || [], at = stages.indexOf(t.stage);
    return '<div class="track" role="img" aria-label="阶段：' + esc(stages.join("、")) + "，现在在" + esc(t.stage) + '">' +
      stages.map(function (s, i) {
        return '<div class="step ' + (i < at ? "done" : i === at ? "now" : "") + '"><i></i><span>' + esc(s) + "</span></div>";
      }).join("") + "</div>";
  }
  function section(title, n, body) {
    return '<section><div class="section-title"><h2>' + esc(title) + '</h2><span class="count">' + n + " 项</span></div>" + body + "</section>";
  }
  function panel(title, body) { return '<div class="panel"><h3>' + esc(title) + "</h3>" + body + "</div>"; }
  function list(items, render, empty) { return items.length ? "<ul>" + items.map(render).join("") + "</ul>" : '<p class="none">' + empty + "</p>"; }

  function topbar(sub) {
    var mode = themeMode();
    return '<header class="topbar"><span class="brand"><span class="brand-mark">c<span>p</span></span>coding-pal<span class="brand-sub">' + esc(sub) +
      '</span></span><div class="top-right"><div class="theme" role="group" aria-label="显示模式">' +
      [["system", "跟随系统"], ["light", "亮色"], ["dark", "暗色"]].map(function (m) {
        return '<button type="button" data-theme-choice="' + m[0] + '" aria-pressed="' + (mode === m[0]) + '">' + m[1] + "</button>";
      }).join("") + '</div><span class="live"><span class="dot"></span>本机 · 每 20 秒自动刷新</span></div></header>';
  }
  function heading(eyebrow, title, meta) {
    return '<div class="heading"><div><div class="eyebrow">' + esc(eyebrow) + "</div><h1>" + esc(title) + '</h1></div><div class="meta">' + meta + "</div></div>";
  }
  function footer(source) {
    return "<footer><span>由 pal 自动生成，不要手改；有变化时 Agent 会更新。</span><span>" + esc(source) + "</span></footer>";
  }

  function sidebar(projects, activePath, hubUrl) {
    var closed = load(CLOSED_KEY, []);
    var waitingTotal = projects.reduce(function (n, p) { return n + count(p.tasks, ["等你"]); }, 0);
    var html = '<a class="side-all' + (hub ? " on" : "") + '" href="' + esc(hubUrl) + '"><span>所有项目</span>' + badge(waitingTotal) + "</a>" +
      '<div class="side-label">项目</div>' + projects.map(function (p) {
        var isClosed = closed.indexOf(p.path) >= 0;
        return '<div class="side-proj' + (p.path === activePath ? " on" : "") + (isClosed ? " closed" : "") + '"><div class="side-row">' +
          '<button class="fold" type="button" data-fold="' + esc(p.path) + '" aria-expanded="' + !isClosed + '" aria-label="收起或展开 ' + esc(p.name) +
          ' 的任务">' + icon("fold") + '</button><a class="side-name" href="' + esc(p.board) + '"><span>' + esc(p.name) + "</span>" +
          badge(count(p.tasks, ["等你"])) + '</a></div><div class="side-tasks">' + p.tasks.slice().sort(byStatus).map(function (t) {
            return '<a class="side-task" href="' + esc(p.board) + "#task-" + esc(t.id) + '"><span>' + esc(t.title) + "</span>" + statusChip(t) + "</a>";
          }).join("") + "</div></div>";
      }).join("");
    var missing = registered.filter(function (r) { return !projects.some(function (p) { return p.path === r.path; }); });
    if (missing.length) html += '<div class="side-missing">没读到：' + missing.map(function (r) { return esc(r.name); }).join("、") + "</div>";
    return '<aside class="side-wrap" aria-label="项目和任务"><div class="side-head"><span>项目和任务</span>' +
      '<button class="side-toggle" type="button" data-side-toggle aria-expanded="true" title="收起侧边栏">' + icon("hide") + "</button></div>" +
      '<nav class="side">' + html + "</nav></aside>";
  }
  function setSide(collapsed, remember) {
    var layout = document.querySelector(".layout"), button = document.querySelector("[data-side-toggle]");
    if (!layout || !button) return;
    layout.classList.toggle("side-collapsed", collapsed);
    button.setAttribute("aria-expanded", String(!collapsed));
    button.title = collapsed ? "展开侧边栏" : "收起侧边栏";
    button.innerHTML = icon(collapsed ? "show" : "hide");
    if (remember) save(SIDE_KEY, collapsed);
  }
  function toggleProject(button) {
    var path = button.getAttribute("data-fold"), group = button.closest(".side-proj");
    var closed = load(CLOSED_KEY, []).filter(function (p) { return p !== path; });
    var nowClosed = !group.classList.contains("closed");
    group.classList.toggle("closed", nowClosed);
    button.setAttribute("aria-expanded", String(!nowClosed));
    save(CLOSED_KEY, nowClosed ? closed.concat([path]) : closed);
  }

  function waitingCards(items, withProject) {
    if (!items.length) return '<p class="quiet">现在没有等你处理的事。</p>';
    return '<div class="waiting">' + items.map(function (t) {
      return '<article class="wait-card"><div class="wait-title">' + (withProject ? '<span class="tag proj">' + esc(t.project) + "</span>" : "") +
        '<a href="' + esc(t.link) + '">' + esc(t.title) + "</a>" + typeTags(t) + '<span class="stage">' + esc(t.stage) + "阶段</span></div>" +
        '<div class="resume">' + (withProject ? "在 " + esc(t.project) + " 的对话里说" : "回到对话说") + " <code>" + esc(t.resume) +
        '</code><button class="copy" type="button" data-copy="' + esc(t.resume) + '">复制</button></div><p class="wait-do"><b>你要做：</b>' +
        esc(t.waiting_for) + reportLink(t) + "</p></article>";
    }).join("") + "</div>";
  }

  function taskRow(t, p) {
    var note = "";
    if (t.status === "等你") note = "<b>等你：</b>" + esc(t.waiting_for);
    else if (t.status === "暂停") note = "<b>暂停原因：</b>" + esc(t.reason);
    else if (t.status === "排队") note = p.merging ? "<b>排队：</b>等「" + esc(titleOf(p, p.merging)) + "」合并完再合并"
      : "<b>排队：</b>现在没有正在合并的任务，回到这个任务的对话说「" + esc(t.resume) + "」就会合并";
    else if (t.status === "待发布") note = "<b>已合并：</b>你说「发布」时，和其他待发布的任务一起发布";
    else if (p.merging === t.id) note = "<b>正在合并：</b>一般不到 1 分钟";
    else if (t.next) note = "<b>下一步：</b>" + esc(t.next);
    var where = t.status === "待发布" ? "已合并到主线" : t.worktree === "主文件夹" ? "在主文件夹改" : t.branch;
    return '<article class="task" id="task-' + esc(t.id) + '"><div><div class="task-title"><span>' + esc(t.title) + "</span>" + typeTags(t) + statusChip(t) +
      "</div>" + track(t) + (note || t.report ? '<p class="task-note">' + note + reportLink(t) + "</p>" : "") + '</div><div class="task-side"><span>' + esc(where) +
      "</span><span>更新于 " + esc(when(t.updated)) + "</span></div></article>";
  }

  function mergeBar(p) {
    if (!p.merging && !p.queue.length) return "";
    var queued = p.queue.map(function (id) { return esc(titleOf(p, id)); }).join("、");
    if (p.merging) {
      return '<div class="notice"><b>合并</b>　同一时间只合并一个任务。正在合并：' + esc(titleOf(p, p.merging)) + (queued ? "；排队：" + queued : "") + "。</div>";
    }
    return '<div class="notice warn"><b>合并</b>　现在没有正在合并的任务。排队的' + queued + "，回到它的对话说「继续」就会合并。</div>";
  }

  function panels(p) {
    var html = panel("待开始", p.todos.length ? "<ol>" + p.todos.map(function (t) {
      return "<li>" + esc(t.text) + " <small>" + esc(when(t.date)) + "</small></li>";
    }).join("") + "</ol>" : '<p class="none">没有。你说"以后再做"的事会记在这里。</p>');
    if (p.releases_enabled) {
      var ship = p.tasks.filter(function (t) { return t.status === "待发布"; });
      html += panel("待发布", list(p.unreleased, function (u) { return "<li>" + esc(u) + "</li>"; }, "没有待发布的变化。") +
        (ship.length ? "<p>合并了、还没发布：" + ship.map(function (t) { return esc(t.title); }).join("、") + "</p>" : ""));
      html += panel("最近发布", list(p.releases, function (r) {
        return "<li><b>" + esc(r.tag) + "</b> " + esc(r.title) + " <small>" + esc(r.date) + "</small></li>";
      }, "还没有发布过。"));
    } else {
      html += panel("最近合并", list(p.recent_merges, function (m) {
        return "<li>" + esc(m.title) + " <small>" + esc(when(m.merged)) + "</small></li>";
      }, "还没有合并过任务。"));
    }
    return '<section class="panels">' + html + "</section>";
  }

  function renderProject(p) {
    var tasks = p.tasks.slice().sort(byStatus);
    var waiting = tasks.filter(function (t) { return t.status === "等你"; }).map(function (t) { return Object.assign({ link: "#task-" + t.id }, t); });
    var html = heading(p.name, "任务看板", esc(p.path) + "<br>更新于 " + esc(when(p.updated))) + mergeBar(p);
    if (p.ai_check && p.ai_check.merged_since) {
      html += '<div class="notice warn"><b>AI 全面检查</b>　上次 ' + esc(p.ai_check.last_full_check || "还没做过") + "，之后合并了 " +
        p.ai_check.merged_since + ' 个改到 AI 行为的任务。需要时在对话里说"做一次全面检查"。</div>';
    }
    html += section("等你处理", waiting.length, waitingCards(waiting, false));
    html += section("任务", tasks.length, tasks.length ? '<div class="tasks">' + tasks.map(function (t) { return taskRow(t, p); }).join("") + "</div>"
      : '<p class="quiet">没有进行中的任务。在对话里说要做什么，就会出现在这里。</p>');
    return html + panels(p) + footer("数据：" + p.path + "/.pal/board/board.js");
  }

  function projectCard(p) {
    var waiting = count(p.tasks, ["等你"]), paused = count(p.tasks, ["暂停"]);
    var stats = [["Agent 在做", count(p.tasks, ["Agent 在做"])], ["排队", count(p.tasks, ["排队"])]];
    if (p.releases_enabled) stats.push(["待发布", count(p.tasks, ["待发布"])]);
    stats.push(["待开始", p.todos.length]);
    return '<a class="project" href="' + esc(p.board) + '"><h3>' + esc(p.name) + '</h3><p class="path" title="' + esc(p.path) + '">' + esc(p.path) +
      '</p><div class="wait-line' + (waiting ? " hot" : "") + '"><span class="dot"></span>' + (waiting ? waiting + " 件等你处理" : "没有等你处理的事") +
      '</div><dl class="stats">' + stats.map(function (s) {
        return '<div class="' + (s[1] ? "" : "zero") + '"><dt>' + s[0] + "</dt><dd>" + s[1] + "</dd></div>";
      }).join("") + '</dl><p class="card-foot">更新于 ' + esc(when(p.updated)) + (paused ? " · 另有 " + paused + " 个暂停" : "") +
      (p.releases_enabled ? "" : " · 不发布版本") + "</p></a>";
  }

  function renderHub(projects) {
    var waiting = [];
    projects.forEach(function (p) {
      p.tasks.forEach(function (t) { if (t.status === "等你") waiting.push(Object.assign({ project: p.name, link: p.board + "#task-" + t.id }, t)); });
    });
    waiting.sort(newestFirst);
    var html = heading("本机汇总", "所有项目", projects.length + " 个项目 · 数据直接读取各项目的看板");
    html += section("等你处理", waiting.length, waitingCards(waiting, true));
    html += section("项目", projects.length, projects.length ? '<div class="projects">' + projects.map(projectCard).join("") + "</div>"
      : '<p class="quiet">还没有接入的项目。在项目里让 Agent 运行 pal init。</p>');
    return html + footer("登记更新于 " + when(hub.updated));
  }

  function render() {
    var projects = allProjects();
    var cur = hub ? null : projects.filter(function (p) { return p.path === current; })[0] || projects[0] || null;
    var hubUrl = hub ? location.href.split("#")[0] : cur ? cur.hub : "";
    var main = hub ? renderHub(projects) : cur ? renderProject(cur)
      : '<p class="quiet">没有读到看板数据（board.js）。在项目里让 Agent 运行 pal board 重新生成。</p>';
    app.innerHTML = topbar(hub ? "所有项目" : "任务看板") + '<div class="layout">' + sidebar(projects, cur ? cur.path : "", hubUrl) +
      '<main class="main" id="main"><div class="shell">' + main + "</div></main></div>";
    setSide(narrow() || load(SIDE_KEY, false) === true, false);
    var nav = performance.getEntriesByType ? performance.getEntriesByType("navigation")[0] : null;
    var saved = null;
    try { saved = JSON.parse(sessionStorage.getItem(SCROLL_KEY)); sessionStorage.removeItem(SCROLL_KEY); } catch (e) { /* 没有存储就不恢复位置 */ }
    // 从侧边栏点进来的任务：标出来；页面是脚本画的，浏览器自带的跳转定位不认，要自己滚过去。
    var target = location.hash ? document.getElementById(decodeURIComponent(location.hash.slice(1))) : null;
    if (target) target.classList.add("hit");
    if (nav && nav.type === "reload" && saved) {
      window.scrollTo(0, saved.page || 0);
      document.getElementById("main").scrollTop = saved.main || 0;
      document.querySelector(".side-wrap").scrollTop = saved.side || 0;
    } else if (target) target.scrollIntoView();
  }

  function copyText(text, button) {
    function done() { button.textContent = "已复制"; setTimeout(function () { button.textContent = "复制"; }, 1500); }
    function fallback() {
      var area = document.createElement("textarea");
      area.value = text; area.style.position = "fixed"; area.style.opacity = "0";
      document.body.appendChild(area); area.select();
      try { if (document.execCommand("copy")) done(); } catch (e) { /* 复制不了就让用户手动选中 */ }
      area.remove();
    }
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, fallback);
    else fallback();
  }
  document.addEventListener("click", function (e) {
    var el = e.target.closest ? e.target.closest("button.copy, [data-theme-choice], [data-side-toggle], [data-fold]") : null;
    if (!el) return;
    if (el.hasAttribute("data-theme-choice")) setTheme(el.getAttribute("data-theme-choice"));
    else if (el.hasAttribute("data-side-toggle")) setSide(!document.querySelector(".layout").classList.contains("side-collapsed"), !narrow());
    else if (el.hasAttribute("data-fold")) toggleProject(el);
    else copyText(el.getAttribute("data-copy"), el);
  });
  setTimeout(function () {
    var main = document.getElementById("main"), side = document.querySelector(".side-wrap");
    try {
      sessionStorage.setItem(SCROLL_KEY, JSON.stringify({ page: window.scrollY, main: main ? main.scrollTop : 0, side: side ? side.scrollTop : 0 }));
    } catch (e) { /* 刷新后回到顶部 */ }
    location.reload();
  }, 20000);

  loadRegistered(render);
})();
