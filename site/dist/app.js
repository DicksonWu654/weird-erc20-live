/* Weird ERC-20 Live: theme, evidence drawer, census glyphs, matrix explorer. No dependencies. */
(function () {
  'use strict';
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var CHAIN_NAMES = { ethereum: 'Ethereum', base: 'Base', arbitrum: 'Arbitrum', bsc: 'BNB Chain', polygon: 'Polygon', optimism: 'Optimism' };
  var SCAN = { ethereum: 'https://etherscan.io/address/', base: 'https://basescan.org/address/', arbitrum: 'https://arbiscan.io/address/',
    bsc: 'https://bscscan.com/address/', polygon: 'https://polygonscan.com/address/', optimism: 'https://optimistic.etherscan.io/address/' };

  /* theme ------------------------------------------------------------------- */
  var root = document.documentElement;
  var mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  function isDark() { var t = root.dataset.theme; return t ? t === 'dark' : !!(mq && mq.matches); }
  function syncThemeLabel() {
    var b = $('#theme'); if (!b) return;
    var dark = isDark();
    $('.theme-tx', b).textContent = dark ? 'Light' : 'Dark';
    b.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
  }
  $('#theme').addEventListener('click', function () {
    var next = isDark() ? 'light' : 'dark';
    root.dataset.theme = next;
    try { localStorage.setItem('wel-theme', next); } catch (e) { /* storage unavailable */ }
    syncThemeLabel(); drawGlyphs();
  });
  if (mq && mq.addEventListener) mq.addEventListener('change', function () { syncThemeLabel(); drawGlyphs(); });
  syncThemeLabel();

  function css(name) { return getComputedStyle(root).getPropertyValue(name).trim(); }
  function numText(id) { var el = document.getElementById(id); return el ? parseFloat(el.textContent.replace(/,/g, '')) : NaN; }

  /* census glyphs ------------------------------------------------------------ */
  function drawGlyphs() {
    var stamp = css('--stamp'), rule = css('--rule'), flag = css('--flag');
    $$('.dots').forEach(function (box) {
      var yes = numText(box.dataset.yesFrom), n = numText(box.dataset.nFrom);
      if (!(n > 0)) return;
      var c = box.querySelector('canvas') || box.appendChild(document.createElement('canvas'));
      var W = box.clientWidth, H = box.clientHeight, dpr = window.devicePixelRatio || 1;
      c.width = Math.round(W * dpr); c.height = Math.round(H * dpr);
      var g = c.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
      var cols = Math.ceil(Math.sqrt(n * W / H)), rows = Math.ceil(n / cols);
      var s = Math.min(W / cols, H / rows), r = Math.max(0.6, s * 0.36);
      for (var i = 0; i < n; i++) {
        var col = i % cols, row = Math.floor(i / cols);
        g.fillStyle = i < yes ? (box.classList.contains('small') ? flag : stamp) : rule;
        g.beginPath(); g.arc(col * s + s / 2, row * s + s / 2, i < yes ? r * 1.15 : r, 0, Math.PI * 2); g.fill();
      }
    });
    $$('.crosses').forEach(function (box) {
      var k = numText(box.dataset.countFrom); if (!(k > 0)) return;
      var c = box.querySelector('canvas') || box.appendChild(document.createElement('canvas'));
      var W = box.clientWidth, H = box.clientHeight, dpr = window.devicePixelRatio || 1;
      c.width = Math.round(W * dpr); c.height = Math.round(H * dpr);
      var g = c.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
      var perRow = Math.ceil(k / 2), s = Math.min(W / perRow, H / 2);
      g.strokeStyle = flag; g.lineWidth = Math.max(1.4, s * 0.12); g.lineCap = 'round';
      for (var i = 0; i < k; i++) {
        var x = (i % perRow) * s + s / 2, y = Math.floor(i / perRow) * s + s / 2, h = s * 0.28;
        g.beginPath(); g.moveTo(x - h, y - h); g.lineTo(x + h, y + h); g.moveTo(x + h, y - h); g.lineTo(x - h, y + h); g.stroke();
      }
    });
    $$('.pb-fill').forEach(function (el) {
      var v = numText(el.dataset.pctFrom); if (v >= 0) el.style.width = v + '%';
    });
  }
  var rt; window.addEventListener('resize', function () { clearTimeout(rt); rt = setTimeout(drawGlyphs, 120); });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(drawGlyphs);
  drawGlyphs();

  /* evidence drawer ------------------------------------------------------------- */
  var EMBED = {};
  try { EMBED = JSON.parse($('#ev-embed').textContent); } catch (e) { EMBED = {}; }
  var drawer = $('#drawer'), lastFocus = null;

  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text != null) e.textContent = text;
    return e;
  }
  function row(dl, k, v, cls) {
    if (v == null || v === '') return;
    dl.appendChild(el('dt', null, k));
    var d = el('dd', cls ? { 'class': cls } : null);
    if (v instanceof Node) d.appendChild(v); else d.textContent = String(v);
    dl.appendChild(d);
  }
  var PLAIN = { multisig_no_timelock: 'multisig, no timelock', not_applicable: 'none', eoa: 'one key', eoa_7702: 'one key (7702)',
    no_known_path: 'no known path', timelock: 'timelock', governor_dao: 'DAO vote', frozen: 'frozen', l1_alias: 'L1 bridge',
    unresolved: 'unresolved', contract_other_or_unresolved: 'other contract', authority_unverified: 'unverified', renounced: 'renounced', no_row: 'no data' };
  function plain(v) { return Object.prototype.hasOwnProperty.call(PLAIN, v) ? PLAIN[v] : v; }
  var STATUS_WORD = { quirk: 'yes', pass: 'no', unprobeable: 'could not test', meta: 'value' };

  function openEvidence(rec) {
    var body = $('#dr-body'); body.textContent = '';
    var col = rec.column, cell = rec.cell, ev = rec.evidence || {}, fw = rec.forward || {};
    $('#dr-kicker').textContent = rec.symbol + ' on ' + CHAIN_NAMES[rec.chain] + ', rank band ' + rec.rank_band;
    $('#dr-h').textContent = col.title;
    body.appendChild(el('p', { 'class': 'def' }, col.label));
    body.appendChild(el('span', { 'class': 'stat s-' + cell[0] }, STATUS_WORD[cell[0]] || cell[0]));
    var dl = el('dl');
    row(dl, 'decoded', cell[1] || '(empty)', 'decoded');
    if (cell[3]) row(dl, 'source status', cell[3] === 'y' ? 'yes' : cell[3]);
    row(dl, 'call ids', cell[2] || 'none (derived from the path record)');
    row(dl, 'test family', col.family_title);
    row(dl, 'kind', col.kind);
    row(dl, 'evidence', col.src === 'control' ? 'control-path classifier' : 'behaviour probe');
    row(dl, 'block', ev.block != null ? Number(ev.block).toLocaleString('en-US') : null);
    row(dl, 'retrieved', ev.retrieved_at);
    row(dl, 'raw pointer', Array.isArray(ev.raw) ? ev.raw.join(', ') : ev.raw);
    row(dl, 'envelope sha256', ev.sha256);
    var a = el('a', { href: SCAN[rec.chain] + rec.token, target: '_blank', rel: 'noopener' }, rec.token);
    row(dl, 'token', a);
    if (rec.name) row(dl, 'name', rec.name);
    body.appendChild(dl);

    var ids = String(cell[2] || '').split(',').filter(Boolean);
    var hashes = Object.assign({}, (ev.call_hashes || {}), (fw && fw.call_hashes) || {});
    var keys = Object.keys(hashes);
    if (keys.length) {
      body.appendChild(el('h4', null, 'Saved call digests (sha256)'));
      var ul = el('ul', { 'class': 'hashes' });
      keys.sort(function (x, y) { return (ids.indexOf(x) < 0) - (ids.indexOf(y) < 0); });
      keys.forEach(function (k) {
        var li = el('li', ids.indexOf(k) >= 0 ? { 'class': 'hit' } : null);
        li.appendChild(el('span', null, k)); li.appendChild(el('span', null, hashes[k])); ul.appendChild(li);
      });
      body.appendChild(ul);
      if (fw && fw.raw) body.appendChild(el('p', { 'class': 'def' }, 'Control-path calls: ' + [].concat(fw.raw).join(', ')));
    }
    if (ev.decoded && Object.keys(ev.decoded).length) {
      body.appendChild(el('h4', null, 'Decoded record summary'));
      var pre = el('pre', { 'class': 'mono' }, JSON.stringify(ev.decoded, null, 1));
      pre.style.whiteSpace = 'pre-wrap'; pre.style.margin = '0';
      body.appendChild(pre);
    }
    lastFocus = document.activeElement;
    drawer.hidden = false;
    $('#dr-close').focus();
  }
  function closeDrawer() { drawer.hidden = true; if (lastFocus && lastFocus.focus) lastFocus.focus(); }
  $('#dr-close').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !drawer.hidden) closeDrawer(); });
  document.addEventListener('click', function (e) {
    var t = e.target.closest('[data-ev]');
    if (!t || t.closest('#xp-grid')) return;
    var rec = EMBED[t.getAttribute('data-ev')];
    if (rec) { e.preventDefault(); openEvidence(rec); }
  });

  /* matrix explorer --------------------------------------------------------------- */
  var HEADLINE = ['ret_missing_transfer', 'false_on_failure', 'approve_race_guard', 'fee_on_transfer_observed', 'balance_drift_7d',
    'share_accounting_capability', 'uint96_limit', 'tf_owner_no_allowance', 'permissioned_transfer', 'pause_capability',
    'blocklist_capability', 'sanction_block_ronin', 'sanction_block_tornado', 'hook_callbacks_observed', 'decimals',
    'is_proxy', 'upgradeable', 'upgrade_authority_class', 'upgrade_single_eoa', 'owner_single_eoa', 'mint_capability'];
  var ROW_H = 30, CELL_W = 34, WIDE_W = 148, LABEL_W = 168, OVERSCAN = 8;
  var cache = {}, state = { chain: 'ethereum', data: null, cols: [], rows: [], cur: { r: 0, c: 0 } };
  var wrap = $('#xp-wrap'), grid = $('#xp-grid'), head = $('#xp-head'), bodyEl = $('#xp-body'), status = $('#xp-status');
  var famSel = $('#xp-fam'), rankSel = $('#xp-rank'), q = $('#xp-q'), only = $('#xp-only');

  function load(chain) {
    if (cache[chain]) return Promise.resolve(cache[chain]);
    status.textContent = 'Loading ' + CHAIN_NAMES[chain] + ' matrix…';
    return fetch('data/matrix-' + chain + '.json').then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (d) {
      d.colIndex = {}; d.columns.forEach(function (c, i) { d.colIndex[c.id] = i; });
      d.rows.forEach(function (r) { r._q = (r.symbol + ' ' + r.name + ' ' + r.token).toLowerCase(); });
      cache[chain] = d; return d;
    });
  }

  function fillFamilies(d) {
    var keep = famSel.value || 'headline';
    famSel.textContent = '';
    famSel.appendChild(el('option', { value: 'headline' }, 'Headline tests'));
    var seen = [];
    d.columns.forEach(function (c) { if (c.family !== 'meta' && seen.indexOf(c.family_title) < 0) seen.push(c.family_title); });
    seen.forEach(function (f) { famSel.appendChild(el('option', { value: 'f:' + f }, f)); });
    famSel.appendChild(el('option', { value: 'all' }, 'All tests'));
    famSel.value = keep;
    if (famSel.value !== keep) famSel.value = 'headline';
    var bands = [];
    d.rows.forEach(function (r) { if (bands.indexOf(r.rank_band) < 0) bands.push(r.rank_band); });
    var keepR = rankSel.value; rankSel.textContent = '';
    rankSel.appendChild(el('option', { value: '' }, 'All bands'));
    bands.forEach(function (b) { rankSel.appendChild(el('option', { value: b }, b === 'extras' ? 'Extras' : 'Rank ' + b)); });
    rankSel.value = bands.indexOf(keepR) >= 0 ? keepR : '';
  }

  function pickColumns(d) {
    var v = famSel.value, idx = [];
    if (v === 'headline') HEADLINE.forEach(function (id) { if (id in d.colIndex) idx.push(d.colIndex[id]); });
    else if (v === 'all') idx = d.columns.map(function (_, i) { return i; });
    else d.columns.forEach(function (c, i) { if ('f:' + c.family_title === v) idx.push(i); });
    // the harness bookkeeping column (how a balance was funded) is not a token behaviour
    return idx.filter(function (i) { return d.columns[i].family !== 'meta'; });
  }

  function apply() {
    var d = state.data; if (!d) return;
    state.cols = pickColumns(d);
    var term = q.value.trim().toLowerCase(), band = rankSel.value, needHit = only.checked, cols = state.cols;
    state.rows = d.rows.filter(function (r) {
      if (band && r.rank_band !== band) return false;
      if (term && r._q.indexOf(term) < 0) return false;
      if (needHit) {
        for (var k = 0; k < cols.length; k++) if (r.cells[cols[k]][0] === 'quirk') return true;
        return false;
      }
      return true;
    });
    state.cur.r = Math.min(state.cur.r, Math.max(0, state.rows.length - 1));
    state.cur.c = Math.min(state.cur.c, Math.max(0, cols.length - 1));
    renderHead(); renderBody(true);
    var hits = 0;
    state.rows.forEach(function (r) { for (var k = 0; k < cols.length; k++) if (r.cells[cols[k]][0] === 'quirk') { hits++; break; } });
    status.textContent = state.rows.length.toLocaleString('en-US') + ' of ' + d.rows.length.toLocaleString('en-US') +
      ' tokens on ' + CHAIN_NAMES[state.chain] + ', ' + cols.length + ' tests shown, ' + hits.toLocaleString('en-US') +
      ' with at least one yes. Arrow keys move, Enter opens evidence.';
    grid.setAttribute('aria-rowcount', String(state.rows.length + 1));
    grid.setAttribute('aria-colcount', String(cols.length + 1));
  }

  function colW(c) { return c.kind === 'meta' ? WIDE_W : CELL_W; }
  function renderHead() {
    var d = state.data; head.textContent = '';
    var corner = el('div', { 'class': 'corner', role: 'columnheader' }, 'Token');
    corner.style.height = '164px'; head.appendChild(corner);
    var prevFam = null, width = LABEL_W;
    state.cols.forEach(function (ci) {
      var c = d.columns[ci];
      var h = el('div', { 'class': 'hc' + (c.kind === 'meta' ? ' wide' : '') + (prevFam !== null && c.family !== prevFam ? ' fam-start' : ''), role: 'columnheader', title: c.label });
      h.appendChild(el('span', null, c.title)); head.appendChild(h); prevFam = c.family; width += colW(c);
    });
    head.style.width = width + 'px';
    bodyEl.style.width = width + 'px';
    state.width = width;
  }

  var lastRange = '';
  function renderBody(force) {
    var d = state.data; if (!d) return;
    var n = state.rows.length;
    bodyEl.style.height = Math.max(n * ROW_H, 60) + 'px';
    var headH = head.offsetHeight || 164;
    var top = Math.max(0, wrap.scrollTop - headH);
    var first = Math.max(0, Math.floor(top / ROW_H) - OVERSCAN);
    var last = Math.min(n, Math.ceil((top + wrap.clientHeight) / ROW_H) + OVERSCAN);
    var key = first + ':' + last;
    if (!force && key === lastRange) return;
    lastRange = key;
    var frag = document.createDocumentFragment();
    if (!n) {
      frag.appendChild(el('div', { 'class': 'empty' }, 'No tokens match. Clear the search or switch the rank band to see more.'));
    }
    for (var i = first; i < last; i++) frag.appendChild(makeRow(state.rows[i], i));
    bodyEl.textContent = ''; bodyEl.appendChild(frag);
    markCursor(false);
  }

  function makeRow(r, i) {
    var d = state.data;
    var rowEl = el('div', { 'class': 'gr', role: 'row', 'aria-rowindex': String(i + 2) });
    rowEl.style.top = (i * ROW_H) + 'px'; rowEl.style.width = state.width + 'px';
    var rc = el('div', { 'class': 'rc', role: 'rowheader' });
    rc.appendChild(el('b', null, r.symbol)); rc.appendChild(el('small', null, r.rank_band + ' ' + r.token.slice(0, 10) + '…'));
    rc.title = r.name + ' ' + r.token;
    rowEl.appendChild(rc);
    var prevFam = null;
    state.cols.forEach(function (ci, k) {
      var c = d.columns[ci], cell = r.cells[ci];
      var b = el('button', {
        type: 'button', tabindex: '-1', role: 'gridcell', id: 'xc-' + i + '-' + k,
        'class': 'c s-' + cell[0] + (c.kind === 'meta' ? ' wide' : '') + (prevFam !== null && c.family !== prevFam ? ' fam-start' : ''),
        'data-r': String(i), 'data-k': String(k),
        'aria-label': r.symbol + ', ' + c.title + ': ' + (c.kind === 'meta' ? (plain(cell[1]) || 'empty') : (STATUS_WORD[cell[0]] || cell[0]))
      });
      if (c.kind === 'meta') b.textContent = plain(cell[1] || '');
      prevFam = c.family;
      rowEl.appendChild(b);
    });
    return rowEl;
  }

  function recordFor(i, k) {
    var d = state.data, r = state.rows[i], ci = state.cols[k], c = d.columns[ci];
    return { chain: state.chain, token: r.token, symbol: r.symbol, name: r.name, rank_band: r.rank_band,
      column: c, cell: r.cells[ci], evidence: r.evidence[c.src], forward: r.evidence.forward };
  }

  function markCursor(scroll) {
    $$('.c.cur', bodyEl).forEach(function (x) { x.classList.remove('cur'); });
    var id = 'xc-' + state.cur.r + '-' + state.cur.c, cell = document.getElementById(id);
    if (cell && document.activeElement === grid) cell.classList.add('cur');
    grid.setAttribute('aria-activedescendant', cell ? id : '');
    if (scroll) {
      var headH = head.offsetHeight || 164, y = state.cur.r * ROW_H + headH;
      if (y - headH < wrap.scrollTop) wrap.scrollTop = y - headH;
      else if (y + ROW_H > wrap.scrollTop + wrap.clientHeight) wrap.scrollTop = y + ROW_H - wrap.clientHeight;
      var x = LABEL_W; for (var k = 0; k < state.cur.c; k++) x += colW(state.data.columns[state.cols[k]]);
      var w = colW(state.data.columns[state.cols[state.cur.c]]);
      if (x - LABEL_W < wrap.scrollLeft) wrap.scrollLeft = x - LABEL_W;
      else if (x + w > wrap.scrollLeft + wrap.clientWidth) wrap.scrollLeft = x + w - wrap.clientWidth;
      renderBody(false);
      var again = document.getElementById(id); if (again && document.activeElement === grid) again.classList.add('cur');
    }
  }

  wrap.addEventListener('scroll', function () { window.requestAnimationFrame(function () { renderBody(false); }); }, { passive: true });
  bodyEl.addEventListener('click', function (e) {
    var b = e.target.closest('.c'); if (!b) return;
    state.cur.r = +b.dataset.r; state.cur.c = +b.dataset.k;
    openEvidence(recordFor(state.cur.r, state.cur.c));
  });
  grid.addEventListener('focus', function () { markCursor(true); });
  grid.addEventListener('blur', function () { $$('.c.cur', bodyEl).forEach(function (x) { x.classList.remove('cur'); }); });
  grid.addEventListener('keydown', function (e) {
    if (!state.rows.length) return;
    var r = state.cur.r, c = state.cur.c, maxR = state.rows.length - 1, maxC = state.cols.length - 1;
    var page = Math.max(1, Math.floor(wrap.clientHeight / ROW_H) - 2);
    switch (e.key) {
      case 'ArrowDown': r = Math.min(maxR, r + 1); break;
      case 'ArrowUp': r = Math.max(0, r - 1); break;
      case 'ArrowRight': c = Math.min(maxC, c + 1); break;
      case 'ArrowLeft': c = Math.max(0, c - 1); break;
      case 'PageDown': r = Math.min(maxR, r + page); break;
      case 'PageUp': r = Math.max(0, r - page); break;
      case 'Home': c = 0; if (e.ctrlKey) r = 0; break;
      case 'End': c = maxC; if (e.ctrlKey) r = maxR; break;
      case 'Enter': case ' ': e.preventDefault(); openEvidence(recordFor(r, c)); return;
      default: return;
    }
    e.preventDefault(); state.cur.r = r; state.cur.c = c; markCursor(true);
  });

  function selectChain(chain) {
    state.chain = chain;
    $$('.chains button').forEach(function (b) { b.setAttribute('aria-selected', String(b.dataset.chain === chain)); });
    return load(chain).then(function (d) {
      if (state.chain !== chain) return;
      state.data = d; fillFamilies(d); wrap.scrollTop = 0; apply();
    }).catch(function (err) {
      status.textContent = 'Could not load the ' + CHAIN_NAMES[chain] + ' matrix (' + err.message + '). Download it from the list below instead.';
    });
  }
  $$('.chains button').forEach(function (b) { b.addEventListener('click', function () { selectChain(b.dataset.chain); }); });
  $('.chains').addEventListener('keydown', function (e) {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    var bs = $$('.chains button'), i = bs.indexOf(document.activeElement); if (i < 0) return;
    var n = bs[(i + (e.key === 'ArrowRight' ? 1 : bs.length - 1)) % bs.length]; n.focus(); n.click();
  });
  var qt; q.addEventListener('input', function () { clearTimeout(qt); qt = setTimeout(apply, 120); });
  famSel.addEventListener('change', apply); rankSel.addEventListener('change', apply); only.addEventListener('change', apply);

  var started = false;
  function start() { if (started) return; started = true; selectChain('ethereum'); }
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (es) { if (es.some(function (x) { return x.isIntersecting; })) { start(); io.disconnect(); } }, { rootMargin: '900px 0px' });
    io.observe($('#explore'));
  } else { start(); }
  if (location.hash === '#explore') start();
  window.addEventListener('hashchange', function () { if (location.hash === '#explore') start(); });
})();
