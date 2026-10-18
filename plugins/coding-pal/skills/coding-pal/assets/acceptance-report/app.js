'use strict';
(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const node = (tag, className = '', text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const icon = name => {
    const image = node('img', 'ui-icon');
    image.src = `icons/${name}.svg`;
    image.alt = '';
    image.setAttribute('aria-hidden', 'true');
    return image;
  };
  const results = {
    '通过': {className: 'pass', icon: 'check', color: '#739881'},
    '未通过': {className: 'fail', icon: 'circle-alert', color: '#c78b7c'},
    '无法验证': {className: 'unverified', icon: 'circle-help', color: '#d0b77e'}
  };
  let report;
  try {
    report = JSON.parse($('#report-data').textContent);
    for (const key of ['changes', 'try', 'checked', 'issues']) if (!Array.isArray(report[key])) throw new Error(key);
    for (const item of report.checked) if (!Object.hasOwn(results, item.result) || !Array.isArray(item.evidence)) throw new Error('checked');
  } catch {
    for (const selector of ['.overview', '.changes', '#try', '.items-section']) $(selector).hidden = true;
    $('#report-title').textContent = '暂时无法读取这个验收网页';
    $('#headline').textContent = '请让 Agent 重新运行 pal report 生成。';
    return;
  }
  let filter = 'all', activeItem = null, returnFocus = null, returnScroll = {x: 0, y: 0}, bodyOverflow = '';
  const viewer = {images: [], index: 0, returnTo: null};
  const needsAttention = item => item.result !== '通过';
  const orderedItems = [...report.checked].sort((a, b) => Number(needsAttention(b)) - Number(needsAttention(a)));
  function localURL(value) {
    if (typeof value !== 'string' || !value) return '';
    try {
      const decoded = decodeURIComponent(value);
      if (/[:\\\u0000-\u001f\u007f]/.test(decoded) || decoded.startsWith('/') || decoded.split('/').includes('..')) return '';
      return value;
    } catch { return ''; }
  }
  // [文字](网址) 显示成在新标签页打开的文字链接，只认 file、http、https；其余照原样显示为文字。
  function linkedText(target, value) {
    let last = 0;
    for (const match of value.matchAll(/\[([^\[\]\n]+)\]\(((?:file|https?):\/\/[^()\s]+)\)/g)) {
      target.append(document.createTextNode(value.slice(last, match.index)));
      const link = node('a', 'text-link entry-link', match[1]);
      link.href = match[2];
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.append(icon('arrow-up-right'));
      target.append(link);
      last = match.index + match[0].length;
    }
    target.append(document.createTextNode(value.slice(last)));
  }
  // 反引号里的内容显示成可复制的命令，其余按 linkedText 显示。
  function richText(target, value) {
    const parts = String(value).split('`');
    parts.forEach((part, index) => {
      const closed = index % 2 === 1 && index < parts.length - 1;
      if (!closed) {
        if (index % 2 === 1) target.append(document.createTextNode('`' + part));
        else linkedText(target, part);
        return;
      }
      const long = part.length > 40;  // 长路径和命令单独占一行，免得在句子里折成好几行
      const code = node('code', long ? 'copyable wide' : 'copyable', part);
      const button = node('button', 'copy-button', '复制');
      button.type = 'button';
      button.setAttribute('aria-label', `复制 ${part}`);
      button.onclick = () => copy(part, button);
      if (!long) {
        target.append(code, button);
        return;
      }
      const line = node('span', 'code-line');
      line.append(code, button);
      target.append(line);
    });
  }
  async function copy(value, button) {
    let done = false;
    try { await navigator.clipboard.writeText(value); done = true; } catch {}
    if (!done) {
      const area = node('textarea');
      area.value = value;
      area.setAttribute('readonly', '');
      area.style.position = 'fixed';
      area.style.opacity = '0';
      document.body.append(area);
      area.select();
      try { done = document.execCommand('copy'); } catch {}
      area.remove();
    }
    button.textContent = done ? '已复制' : '请手动复制';
    setTimeout(() => { button.textContent = '复制'; }, 1600);
  }
  function badge(item) {
    return node('span', `status-label ${results[item.result].className}`, item.result);
  }
  function evidenceLabel(item) {
    return item.evidence.length ? `${item.evidence.length} 份证据` : '没有证据';
  }
  function renderReport() {
    document.title = `${report.title} · 验收网页`;
    $('#project-name').textContent = report.project || '';
    $('#report-title').textContent = report.title;
    $('#headline').textContent = report.headline;
    richText($('#need-text'), report.you_need_to.text);
    $('#need-time').textContent = `预计约 ${report.you_need_to.minutes} 分钟`;
    $('#try-link').hidden = !report.try.length;
    $('#checked-count').textContent = report.checked.length;
    $('#item-count').textContent = String(report.checked.length);
    const track = $('#status-track'), legend = $('#status-legend');
    for (const [name, result] of Object.entries(results)) {
      const count = report.checked.filter(item => item.result === name).length;
      if (!count) continue;
      const cell = node('span', `track-cell ${result.className}`);
      cell.style.flexGrow = count;
      track.append(cell);
      const label = node('span', 'legend-item');
      const dot = node('i');
      dot.style.backgroundColor = result.color;
      label.append(dot, document.createTextNode(name), node('b', '', count));
      legend.append(label);
    }
    for (const change of report.changes) $('#changes').append(node('li', '', change));
    $('#footer-meta').textContent = [report.revision && `检查对象 ${report.revision}`, report.generated && `生成于 ${report.generated}`].filter(Boolean).join(' · ');
    renderTry();
    renderItems();
    renderIssues();
    $('#details').hidden = !report.details.trim();
    $('#details-text').textContent = report.details;
  }
  function thumbnail(image, collection) {
    const figure = node('figure', 'shot');
    const button = node('button', 'screenshot-button');
    button.type = 'button';
    button.setAttribute('aria-label', `放大看：${image.caption}`);
    const picture = node('img');
    picture.alt = image.caption;
    picture.loading = 'lazy';
    picture.onerror = () => button.replaceWith(node('p', 'missing-evidence', `图片 ${image.name} 没能显示；请让 Agent 重新生成网页。`));
    picture.src = localURL(image.url);
    const tip = node('span', 'image-tip');
    tip.append(icon('maximize'), document.createTextNode('放大'));
    button.append(picture, tip);
    button.onclick = () => openViewer(collection, collection.indexOf(image), button);
    figure.append(button);
    return figure;
  }
  function renderTry() {
    const list = $('#try-list');
    $('#try-count').textContent = report.try.length ? String(report.try.length) : '';
    if (!report.try.length) {
      list.append(node('p', 'empty-note', '这次不需要你亲手试，看下面的检查结果即可。'));
      return;
    }
    report.try.forEach((entry, index) => {
      const card = node('article', 'try-card');
      const head = node('div', 'try-head');
      head.append(node('span', 'try-number', String(index + 1)), node('h3', '', entry.title));
      const steps = node('ol', 'try-steps');
      for (const step of entry.steps) {
        const li = node('li');
        richText(li, step);
        steps.append(li);
      }
      card.append(head, steps);
      for (const [label, value, className] of [['应该看到', entry.expect, 'expectation'], ['出现这些情况，算没做对', entry.fail, 'expectation failure']]) {
        const block = node('div', className);
        block.append(node('strong', '', label), node('p', '', value));
        card.append(block);
      }
      if (entry.images.length) {
        const gallery = node('div', 'try-images');
        for (const image of entry.images) {
          const figure = thumbnail(image, entry.images);
          figure.append(node('figcaption', '', image.caption));
          gallery.append(figure);
        }
        card.append(gallery);
      }
      list.append(card);
    });
  }
  function visibleItems() {
    return orderedItems.filter(item => filter === 'all' || needsAttention(item));
  }
  function renderItems() {
    $$('.filter').forEach(button => {
      const kind = button.dataset.filter;
      $('span', button).textContent = orderedItems.filter(item => kind === 'all' || needsAttention(item)).length;
      button.classList.toggle('active', filter === kind);
      button.setAttribute('aria-pressed', String(filter === kind));
    });
    const list = $('#items-list');
    list.replaceChildren();
    for (const item of visibleItems()) {
      const result = results[item.result];
      const button = node('button', 'item-row');
      button.setAttribute('aria-haspopup', 'dialog');
      const symbol = node('span', `status-icon ${result.className}`);
      symbol.append(icon(result.icon));
      const content = node('span');
      content.append(node('span', 'item-title', item.title), node('span', 'item-description', item.summary));
      const meta = node('span', 'item-meta');
      meta.append(badge(item), node('span', 'evidence-label', evidenceLabel(item)));
      const arrow = node('span', 'row-arrow');
      arrow.append(icon('chevron-right'));
      button.append(symbol, content, meta, arrow);
      button.onclick = () => openDrawer(item, button);
      list.append(button);
    }
    if (!list.children.length) list.append(node('p', 'empty-list', '没有需关注的项；切换到"全部"看所有检查结果。'));
  }
  function renderIssues() {
    $('#issues').hidden = !report.issues.length;
    for (const issue of report.issues) {
      const card = node('div', 'issue');
      card.append(node('h3', '', issue.what));
      for (const [label, value] of [['对你的影响', issue.impact], ['打算怎么办', issue.plan]]) {
        const paragraph = node('p');
        paragraph.append(node('strong', '', `${label}：`), document.createTextNode(value));
        card.append(paragraph);
      }
      $('#issues-list').append(card);
    }
  }
  function openDrawer(item, trigger) {
    if (!activeItem) {
      returnFocus = trigger;
      returnScroll = {x: scrollX, y: scrollY};
      bodyOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
      $('#page').inert = true;
    }
    activeItem = item;
    $('#drawer').hidden = false;
    $('#drawer-backdrop').hidden = false;
    const visible = visibleItems(), index = visible.indexOf(item);
    $('#drawer-index').textContent = `第 ${index + 1} / ${visible.length} 项`;
    $('#drawer-title').textContent = item.title;
    $('#drawer-summary').textContent = item.summary;
    $('#drawer-badge').replaceChildren(badge(item));
    $('#prev-item').disabled = index <= 0;
    $('#next-item').disabled = index >= visible.length - 1;
    renderEvidence();
    $('.drawer-body').scrollTop = 0;
    $('#close-drawer').focus({preventScroll: true});
  }
  function closeDrawer() {
    if (!activeItem) return;
    $('#drawer').hidden = true;
    $('#drawer-backdrop').hidden = true;
    $('#page').inert = false;
    document.body.style.overflow = bodyOverflow;
    activeItem = null;
    (returnFocus?.isConnected ? returnFocus : $('.filter.active')).focus({preventScroll: true});
    window.scrollTo(returnScroll.x, returnScroll.y);
  }
  function linesBlock(lines, from, to, mark) {
    const pre = node('pre');
    pre.tabIndex = 0;
    for (let number = from; number <= to; number++) {
      const marked = mark && number >= mark[0] && number <= mark[1];
      const row = node('span', marked ? 'line marked' : 'line');
      row.append(node('span', 'ln', String(number)), document.createTextNode(lines[number - 1]));
      pre.append(row);
    }
    return pre;
  }
  function textEvidence(evidence) {
    const lines = evidence.lines, [start, end] = evidence.highlight, total = lines.length;
    const whole = start === 1 && end === total;
    const wrap = node('div');
    const terminal = node('div', 'terminal');
    const range = start === end ? `第 ${start} 行` : `第 ${start}–${end} 行`;
    terminal.append(node('div', 'terminal-bar', whole ? `${evidence.name} · 共 ${total} 行` : `${evidence.name} · 关键行：${range}（共 ${total} 行）`));
    const shown = linesBlock(lines, start, end, null);
    shown.setAttribute('aria-label', `${evidence.name} 的关键行`);
    terminal.append(shown);
    wrap.append(terminal);
    if (!whole) {
      const details = node('details', 'full-text');
      details.append(node('summary', '', `查看全文（${total} 行，关键行已标出）`));
      details.addEventListener('toggle', () => {
        if (details.open && !$('pre', details)) details.append(linesBlock(lines, 1, total, [start, end]));
      });
      wrap.append(details);
    }
    return wrap;
  }
  function renderEvidence() {
    const panel = $('#evidence-panel');
    panel.replaceChildren();
    $('#evidence-count').textContent = activeItem.evidence.length || '';
    if (!activeItem.evidence.length) {
      panel.append(node('p', 'empty-note', '这一项没有证据；原因见上面的说明。'));
      return;
    }
    const images = activeItem.evidence.filter(evidence => evidence.kind === 'image');
    for (const evidence of activeItem.evidence) {
      const section = node('section', 'evidence-section');
      section.append(node('p', 'evidence-caption', evidence.caption));
      if (evidence.kind === 'image') section.append(thumbnail(evidence, images));
      else if (evidence.kind === 'text') section.append(textEvidence(evidence));
      const url = localURL(evidence.url);
      if (url) {
        const link = node('a', 'text-link', evidence.kind === 'file' ? `打开文件 ${evidence.name}` : '打开原文件');
        link.href = url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        if (url.endsWith('.download')) link.download = evidence.name;
        link.append(icon('arrow-up-right'));
        const links = node('div', 'file-links');
        links.append(link);
        section.append(links);
      }
      panel.append(section);
    }
  }
  function openViewer(collection, index, trigger) {
    viewer.images = collection;
    viewer.index = index;
    viewer.returnTo = trigger;
    $('.viewer-canvas').classList.remove('original');
    $('#zoom-image').textContent = '查看原尺寸';
    renderViewer();
    $('#image-viewer').showModal();
    $('#close-viewer').focus();
  }
  function renderViewer() {
    const image = viewer.images[viewer.index], canvas = $('.viewer-canvas');
    canvas.replaceChildren();
    $('#viewer-title').textContent = image.caption;
    $('#viewer-position').textContent = `${viewer.index + 1} / ${viewer.images.length}`;
    $('#previous-image').disabled = viewer.index === 0;
    $('#next-image').disabled = viewer.index === viewer.images.length - 1;
    const picture = node('img');
    picture.alt = image.caption;
    picture.src = localURL(image.url);
    canvas.append(picture);
    canvas.scrollTop = 0;
    canvas.scrollLeft = 0;
  }
  function switchImage(direction) {
    const next = viewer.index + direction;
    if (next < 0 || next >= viewer.images.length) return;
    viewer.index = next;
    renderViewer();
  }
  $('#previous-image').onclick = () => switchImage(-1);
  $('#next-image').onclick = () => switchImage(1);
  $('#close-viewer').onclick = () => $('#image-viewer').close();
  $('#zoom-image').onclick = () => {
    const original = $('.viewer-canvas').classList.toggle('original');
    $('#zoom-image').textContent = original ? '适应窗口' : '查看原尺寸';
  };
  $('#image-viewer').addEventListener('close', () => { if (viewer.returnTo?.isConnected) viewer.returnTo.focus({preventScroll: true}); });
  $('#close-drawer').onclick = closeDrawer;
  $('#drawer-backdrop').onclick = closeDrawer;
  for (const [selector, direction] of [['#prev-item', -1], ['#next-item', 1]]) {
    $(selector).onclick = () => {
      const visible = visibleItems(), next = visible[visible.indexOf(activeItem) + direction];
      if (next) openDrawer(next);
    };
  }
  $$('.filter').forEach(button => {
    button.onclick = () => {
      const toolbar = $('.list-toolbar');
      const wasSticky = toolbar.getBoundingClientRect().top <= 0;
      const previousCount = visibleItems().length;
      filter = button.dataset.filter;
      renderItems();
      if (wasSticky && visibleItems().length < previousCount) $('.items-section').scrollIntoView({block: 'start'});
    };
  });
  $$('.report-guide').forEach(detail => detail.addEventListener('toggle', () => {
    $('.guide-toggle', detail).firstChild.textContent = detail.open ? '收起 ' : '展开查看 ';
  }));
  document.addEventListener('keydown', event => {
    if ($('#image-viewer').open) {
      if (['ArrowLeft', 'ArrowRight'].includes(event.key)) {
        event.preventDefault();
        switchImage(event.key === 'ArrowLeft' ? -1 : 1);
      }
      return;
    }
    if (!activeItem) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      closeDrawer();
    }
    if (event.key === 'Tab') {
      const focusable = $$('button,a[href],summary,[tabindex="0"]', $('#drawer')).filter(element => !element.disabled && element.getClientRects().length);
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  renderReport();
})();
