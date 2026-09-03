const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function fetchJSON(url, opts) {
  const timeoutMs = (opts && opts.timeoutMs) || 0;
  const { timeoutMs: _t, ...rest } = opts || {};
  if (!timeoutMs) {
    const r = await fetch(url, rest);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const r = await fetch(url, Object.assign({ signal: ctrl.signal }, rest));
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  } finally {
    clearTimeout(timer);
  }
}

function parseNum(v) {
  if (v === null || v === undefined) return 0;
  if (typeof v === "number") return v;
  const s = String(v).trim();
  if (!s) return 0;
  const sign = s[0] === "-" ? -1 : 1;
  if (/^-?\d+\.\d+$/.test(s.replace(/,/g, ""))) {
    return sign * parseFloat(s.replace(/[^0-9.]/g, ""));
  }
  const cleaned = s.replace(/[^0-9]/g, "").replace(/^0+(?=\d)/, "") || "0";
  return sign * parseFloat(cleaned);
}
function num(v) { return Math.round(parseNum(v)).toLocaleString("ko-KR"); }
function numFixed(v, digits) {
  const n = parseNum(v);
  return n.toLocaleString("ko-KR", {
    minimumFractionDigits: digits ?? 0,
    maximumFractionDigits: digits ?? 0,
  });
}
function emptyRow(msg, ico) {
  return `<div class="empty"><span class="ico">${ico || "📭"}</span>${esc(msg)}</div>`;
}
function fmtEokOrJo(v) {
  if (v === null || v === undefined || v === "") return "-";
  const n = parseNum(v);
  if (Math.abs(n) >= 10000) return numFixed(n / 10000, 2) + "조";
  return numFixed(n, 1) + "억";
}

let _themeFlowItems = [];
let _themeFlowSelected = null;
let _themeFlowResizeTimer = null;

function themeFlowColor(changeRate) {
  const c = parseNum(changeRate);
  const t = Math.max(-6, Math.min(6, c)) / 6;
  if (t >= 0) {
    const light = 52 - t * 10;
    return `hsl(0 82% ${light.toFixed(1)}%)`;
  }
  const light = 55 - (-t) * 10;
  return `hsl(214 86% ${light.toFixed(1)}%)`;
}

function squarifyLayout(items, x0, y0, w, h) {
  const total = items.reduce((s, it) => s + Math.max(0, parseNum(it.value)), 0);
  if (!total || w <= 0 || h <= 0) return [];
  const nodes = items.map((it) => ({
    ...it,
    value: Math.max(0, parseNum(it.value)),
  })).filter((it) => it.value > 0);
  const out = [];
  let i = 0;
  let x = x0;
  let y = y0;
  let rw = w;
  let rh = h;
  let rem = total;

  function worst(row, len, remArea) {
    if (!row.length) return Infinity;
    const s = row.reduce((a, b) => a + b.value, 0);
    const maxV = Math.max(...row.map((b) => b.value));
    const minV = Math.min(...row.map((b) => b.value));
    const scale = remArea / rem;
    const r = s * scale;
    if (r <= 0) return Infinity;
    return Math.max((len * len * maxV * scale) / (r * r), (r * r) / (len * len * minV * scale));
  }

  function layoutRow(row, horizontal) {
    const s = row.reduce((a, b) => a + b.value, 0);
    const frac = s / rem;
    if (horizontal) {
      const rowH = rh * frac;
      let cx = x;
      row.forEach((node) => {
        const tw = rw * (node.value / s);
        out.push({ ...node, x: cx, y, w: tw, h: rowH });
        cx += tw;
      });
      y += rowH;
      rh -= rowH;
    } else {
      const rowW = rw * frac;
      let cy = y;
      row.forEach((node) => {
        const th = rh * (node.value / s);
        out.push({ ...node, x, y: cy, w: rowW, h: th });
        cy += th;
      });
      x += rowW;
      rw -= rowW;
    }
    rem -= s;
  }

  while (i < nodes.length) {
    const horizontal = rw >= rh;
    const len = horizontal ? rw : rh;
    const row = [nodes[i]];
    i += 1;
    while (i < nodes.length) {
      const next = nodes[i];
      const w1 = worst(row, len, rem);
      const w2 = worst(row.concat([next]), len, rem);
      if (w2 <= w1) {
        row.push(next);
        i += 1;
      } else break;
    }
    layoutRow(row, horizontal);
  }
  return out;
}

function renderThemeFlowDetail(item) {
  const el = $("themeFlowDetail");
  if (!el) return;
  if (!item) {
    el.innerHTML = '<span class="muted">타일을 클릭하면 구성 종목이 표시됩니다.</span>';
    return;
  }
  const chg = parseNum(item.avg_change_rate);
  const chgCls = chg > 0 ? "up" : (chg < 0 ? "down" : "flat");
  const chgTxt = (chg > 0 ? "+" : "") + numFixed(chg, 2) + "%";
  const stocks = item.top_stocks || [];
  const rows = stocks.map((s, idx) => {
    const sc = parseNum(s.change_rate);
    const scCls = sc > 0 ? "up" : (sc < 0 ? "down" : "flat");
    return `<tr>
      <td class="num">${idx + 1}</td>
      <td>${esc(s.stock_name || "-")} <span class="muted">${esc(s.stock_code || "")}</span></td>
      <td class="num">${fmtEokOrJo(s.trade_amount_eok)}</td>
      <td class="num ${scCls}">${(sc > 0 ? "+" : "") + numFixed(sc, 2)}%</td>
    </tr>`;
  }).join("");
  el.innerHTML = `
    <div class="tf-head">
      <strong>#${num(item.rank)} ${esc(item.theme || "-")}</strong>
      <span>대금 <b>${fmtEokOrJo(item.trade_amount_eok)}</b></span>
      <span>종목 ${num(item.stock_count)}</span>
      <span class="${chgCls}">평균등락 ${esc(chgTxt)}</span>
    </div>
    ${stocks.length ? `<table class="tbl"><thead><tr><th>#</th><th>종목</th><th class="num">대금</th><th class="num">등락</th></tr></thead><tbody>${rows}</tbody></table>` : '<div class="muted">구성 종목 없음</div>'}
  `;
}

function renderThemeFlowMap(items) {
  const map = $("themeFlowMap");
  if (!map) return;
  const data = (items || []).filter((it) => parseNum(it.trade_amount_eok || it.trade_amount) > 0);
  if (!data.length) {
    map.innerHTML = emptyRow("테마 대금 데이터가 없습니다. 새로고침을 눌러 주세요.", "🗺️");
    renderThemeFlowDetail(null);
    return;
  }
  map.innerHTML = data.map((it) => {
    const chg = parseNum(it.avg_change_rate);
    const chgTxt = (chg > 0 ? "+" : "") + numFixed(chg, 1) + "%";
    const title = `${it.theme || "-"} · ${chgTxt} · 중복합산 대금 ${fmtEokOrJo(it.trade_amount_eok)} · 종목 ${it.stock_count || 0}`;
    return `<button type="button" class="theme-tile" data-theme="${esc(it.theme || "")}" title="${esc(title)}"
      style="background:${themeFlowColor(chg)};">
      <span class="tt-name">${esc(it.theme || "-")}</span>
      <span class="tt-change">${esc(chgTxt)}</span>
      <span class="tt-meta">${fmtEokOrJo(it.trade_amount_eok)}</span>
    </button>`;
  }).join("");

  map.querySelectorAll(".theme-tile").forEach((btn) => {
    btn.onclick = () => {
      const name = btn.getAttribute("data-theme");
      const item = (_themeFlowItems || []).find((r) => String(r.theme) === String(name));
      _themeFlowSelected = name;
      map.querySelectorAll(".theme-tile").forEach((b) => b.classList.toggle("is-active", b === btn));
      renderThemeFlowDetail(item || null);
    };
  });

  if (_themeFlowSelected) {
    const prev = (_themeFlowItems || []).find((r) => String(r.theme) === String(_themeFlowSelected));
    if (prev) {
      const sel = (window.CSS && CSS.escape)
        ? CSS.escape(_themeFlowSelected)
        : String(_themeFlowSelected).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
      const btn = map.querySelector(`.theme-tile[data-theme="${sel}"]`);
      if (btn) btn.classList.add("is-active");
      renderThemeFlowDetail(prev);
      return;
    }
  }
  renderThemeFlowDetail(null);
}

async function loadThemeTradeFlow(rebuild = false) {
  const map = $("themeFlowMap");
  const hint = $("themeFlowHint");
  if (!map) return;
  if (rebuild) map.innerHTML = '<div class="skeleton">테마 데이터 새로고침 중...</div>';
  try {
    const sortBy = $("themeFlowSort")?.value || "trade_amount";
    const qs = new URLSearchParams({ sort_by: sortBy });
    if (rebuild) qs.set("rebuild", "1");
    const d = await fetchJSON(`/themes/trade-amount-map?${qs.toString()}`, { timeoutMs: 120000 });
    if (!d.success) {
      map.innerHTML = emptyRow(d.error || "테마 대금 맵 조회 실패", "⚠️");
      if (hint) hint.textContent = "";
      return;
    }
    const items = d.items || [];
    _themeFlowItems = items;
    const cacheTag = d.cached ? "캐시" : "실시간";
    const sortTag = d.sort_by === "change_rate" ? "등락률순" : "거래대금순";
    const built = d.built_at ? new Date(d.built_at + (String(d.built_at).endsWith("Z") ? "" : "Z")).toLocaleTimeString("ko-KR") : "-";
    if (hint) {
      hint.textContent = `${items.length}테마 · ${sortTag} · 종목 ${num(d.stock_universe || 0)} · 전체 소속테마 중복합산 · ${cacheTag} ${built}`;
    }
    renderThemeFlowMap(items);
  } catch (e) {
    map.innerHTML = emptyRow("테마 대금 맵을 불러오지 못했습니다.", "⚠️");
    if (hint) hint.textContent = "";
  }
}

function bindThemeFlowResize() {
  window.addEventListener("resize", () => {
    if (!_themeFlowItems.length) return;
    clearTimeout(_themeFlowResizeTimer);
    _themeFlowResizeTimer = setTimeout(() => renderThemeFlowMap(_themeFlowItems), 120);
  });
}

function toast(msg, err = false) {
  const t = $("toast");
  if (!t) return;
  t.textContent = msg;
  t.className = `toast show${err ? " err" : ""}`;
  setTimeout(() => t.className = "toast", 2200);
}

let currentTagId = null;
let coverageOffset = 0;
const COVERAGE_PAGE = 100;

function fmtPct(v) {
  const n = Number(v);
  return Number.isFinite(n) ? `${n}%` : "-";
}

function sourceChipClass(source) {
  const s = String(source || "");
  if (s === "naver_theme") return "src-n";
  if (s === "kiwoom_theme") return "src-k";
  if (s === "alphasquare_theme") return "src-as";
  if (s === "manual") return "manual";
  if (s.startsWith("news")) return "src-news";
  return "";
}

function renderSourceChip(it) {
  const short = it.source_short || it.source_label || it.source || "";
  const cls = sourceChipClass(it.source);
  return `<span class="cov-chip src-chip ${cls}" title="${esc(it.source_label || it.source || "")}">${esc(short)}</span>`;
}

function renderReason(reason) {
  const t = String(reason || "").trim();
  if (!t) return "";
  return `<div class="reason-text" title="${esc(t)}">${esc(t)}</div>`;
}

function renderKeyPoint(kp) {
  const t = String(kp || "").trim();
  if (!t) return "";
  return `<div class="keypoint-text" title="${esc(t)}">KEY · ${esc(t)}</div>`;
}

function renderTagChips(names, kind) {
  const arr = Array.isArray(names) ? names.filter(Boolean) : [];
  if (!arr.length) {
    return `<span class="cov-empty">-</span>`;
  }
  const limit = kind === "keyword" ? 3 : 6;
  const shown = arr.slice(0, limit);
  const more = arr.length - shown.length;
  const wrapClass = kind === "keyword" ? "cov-tags cov-tags--keyword" : "cov-tags cov-tags--theme";
  return `<div class="${wrapClass}">${shown.map((n) => `<span class="cov-chip ${kind}">${esc(n)}</span>`).join("")}${more > 0 ? `<span class="cov-chip">+${more}</span>` : ""}</div>`;
}

function renderCoverageStats(data) {
  const s = data.summary || {};
  const src = data.universe_source === "fundamental_snapshot"
    ? `기준일 ${data.as_of_date || "-"} · 펀더멘털 스냅샷`
    : "매핑·뉴스 등장 종목 합집합 (펀더멘털 배치 권장)";
  $("coverageHint").textContent = src;
  $("coverageStats").innerHTML = `
    <div class="coverage-stat"><div class="label">전체 종목</div><div class="value">${esc(s.total || 0)}</div><div class="sub">KOSPI ${esc(s.kospi || 0)} · KOSDAQ ${esc(s.kosdaq || 0)}</div></div>
    <div class="coverage-stat"><div class="label">테마 태그</div><div class="value">${esc(s.theme_mapped || 0)}</div><div class="sub">${fmtPct(s.coverage_theme_pct)}</div></div>
    <div class="coverage-stat"><div class="label">키워드 태그</div><div class="value">${esc(s.keyword_mapped || 0)}</div><div class="sub">${fmtPct(s.coverage_keyword_pct)} · 추출된 키워드</div></div>
    <div class="coverage-stat"><div class="label">뉴스 조회</div><div class="value">${esc(s.news_scanned || 0)}</div><div class="sub">${fmtPct(s.coverage_news_scanned_pct)} · 기사 없음 포함</div></div>
    <div class="coverage-stat"><div class="label">기사 있음</div><div class="value">${esc(s.news_with_article || 0)}</div><div class="sub">${fmtPct(s.coverage_news_article_pct)}</div></div>
    <div class="coverage-stat"><div class="label">미매핑 (테마·키워드)</div><div class="value">${esc(s.unmapped_any || 0)}</div><div class="sub">테마 ${esc(s.unmapped_theme || 0)} · 키워드 ${esc(s.unmapped_keyword || 0)}</div></div>
  `;
}

function renderCoverageTable(data) {
  const items = data.items || [];
  const filtered = Number(data.filtered_total || 0);
  const offset = Number(data.offset || 0);
  const limit = Number(data.limit || COVERAGE_PAGE);
  const pageStart = filtered ? offset + 1 : 0;
  const pageEnd = Math.min(offset + limit, filtered);
  $("coveragePageInfo").textContent = filtered
    ? `${pageStart}-${pageEnd} / ${filtered}`
    : "0건";
  $("btnCoveragePrev").disabled = offset <= 0;
  $("btnCoverageNext").disabled = offset + limit >= filtered;

  if (!items.length) {
    $("coverageTable").innerHTML = `<div class="empty"><span class="ico">✅</span>조건에 맞는 종목이 없습니다.</div>`;
    return;
  }

  $("coverageTable").innerHTML = `
    <table class="coverage-table">
      <colgroup>
        <col class="col-code">
        <col class="col-name">
        <col class="col-market">
        <col>
        <col class="col-keyword">
      </colgroup>
      <thead>
        <tr>
          <th class="col-code">코드</th>
          <th class="col-name">종목명</th>
          <th class="col-market">시장</th>
          <th>테마</th>
          <th class="col-keyword">키워드</th>
        </tr>
      </thead>
      <tbody>
        ${items.map((it) => `
          <tr data-stock-code="${esc(it.stock_code)}" data-stock-name="${esc(it.stock_name)}">
            <td class="col-code">${esc(it.stock_code)}</td>
            <td class="col-name" title="${esc(it.stock_name)}">${esc(it.stock_name)}</td>
            <td class="col-market"><span class="cov-market">${esc(it.market || "-")}</span></td>
            <td class="cov-cell-theme">${renderTagChips(it.themes, "theme")}</td>
            <td class="cov-cell-keyword">${renderTagChips(it.keywords, "keyword")}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
  document.querySelectorAll(".coverage-table tr[data-stock-code]").forEach((row) => {
    let clickTimer = null;
    row.onclick = () => {
      const code = row.getAttribute("data-stock-code") || "";
      clearTimeout(clickTimer);
      clickTimer = window.setTimeout(async () => {
        await selectStock(code);
      }, 220);
    };
    row.ondblclick = (e) => {
      e.preventDefault();
      clearTimeout(clickTimer);
      fillManualMappingForm(
        row.getAttribute("data-stock-code") || "",
        row.getAttribute("data-stock-name") || "",
      );
    };
  });
}

async function loadCoverage(resetOffset = true) {
  if (resetOffset) coverageOffset = 0;
  const market = $("coverageMarket")?.value || "all";
  const gap = $("coverageGap")?.value || "any";
  const q = ($("coverageSearch")?.value || "").trim();
  const params = new URLSearchParams({
    market,
    gap,
    q,
    limit: String(COVERAGE_PAGE),
    offset: String(coverageOffset),
  });
  try {
    const d = await fetchJSON(`/theme-map/coverage?${params.toString()}`);
    renderCoverageStats(d);
    renderCoverageTable(d);
  } catch (e) {
    $("coverageStats").innerHTML = `<div class="empty"><span class="ico">⚠</span>커버리지 조회 실패</div>`;
    $("coverageTable").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

function renderArticles(targetId, items, emptyMsg, title) {
  const el = $(targetId);
  if (!el) return;
  const arr = Array.isArray(items) ? items : [];
  if (!arr.length) {
    el.innerHTML = `<div class="empty"><span class="ico">📰</span>${esc(emptyMsg || "기사가 없습니다.")}</div>`;
    return;
  }
  el.innerHTML = `
    ${title ? `<div class="kv-mini"><span><b>${esc(title)}</b></span><span>${arr.length}건</span></div>` : ""}
    <div class="theme-list">
      ${arr.map((it) => `
        <div class="article-item">
          <a href="${esc(it.url)}" target="_blank" rel="noopener noreferrer">${esc(it.title)}</a>
          <div class="meta">${esc(it.published_at || "-")}${it.stock_code ? ` · ${esc(it.stock_code)}` : ""}</div>
        </div>
      `).join("")}
    </div>
  `;
}

async function selectStock(code) {
  const normalized = String(code || "").replace(/\D/g, "").padStart(6, "0").slice(-6);
  if (!/^\d{6}$/.test(normalized)) return;
  $("stockCodeInput").value = normalized;
  await lookupStockTags();
  document.querySelector(".stock-search-card")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function fillManualMappingForm(code, name) {
  const normalized = String(code || "").replace(/\D/g, "").padStart(6, "0").slice(-6);
  if (!/^\d{6}$/.test(normalized)) return;
  $("manualStockCode").value = normalized;
  $("manualStockName").value = name || "";
  $("manualTagType").value = "theme";
  $("manualTagName").value = "";
  document.querySelector(".manual-map-card")?.scrollIntoView({ behavior: "smooth", block: "start" });
  window.setTimeout(() => $("manualTagName")?.focus(), 200);
}

function bindStockClickHandlers(container, onSelect) {
  if (!container) return;
  container.querySelectorAll("[data-stock-code]").forEach((el) => {
    el.onclick = async (e) => {
      e.stopPropagation();
      const code = el.getAttribute("data-stock-code") || "";
      await onSelect(code);
    };
  });
}

async function loadArticlesByStock(stockCode) {
  if (!stockCode) return;
  try {
    const d = await fetchJSON(`/theme-map/stocks/${encodeURIComponent(stockCode)}/articles?limit=30`);
    renderArticles("stockArticleBody", d.items || [], `${stockCode} 당일 기사 없음`, `${stockCode} 근거 기사`);
  } catch (e) {
    renderArticles("stockArticleBody", [], "종목 기사 조회 실패");
  }
}

async function loadArticlesByKeyword(keyword) {
  if (!keyword) return;
  try {
    const d = await fetchJSON(`/theme-map/keywords/${encodeURIComponent(keyword)}/articles?limit=30`);
    renderArticles("stockArticleBody", d.items || [], `${keyword} 근거 기사 없음`, `${keyword} 근거 기사`);
  } catch (e) {
    renderArticles("stockArticleBody", [], "키워드 기사 조회 실패");
  }
}

async function loadKeywords() {
  const d = await fetchJSON("/theme-map/keywords/today?limit=20");
  const items = d.items || [];
  $("kwHint").textContent = `${items.length}개`;
  if (!items.length) {
    $("keywordBody").innerHTML = `<div class="empty"><span class="ico">🧪</span>데이터가 없습니다. 스냅샷 갱신을 눌러주세요.</div>`;
    return;
  }
  $("keywordBody").innerHTML = `<div class="theme-list">${
    items.map((it) => {
      const trend = (it.trend_label || "flat").toLowerCase();
      const delta = Number(it.delta_vs_prev || 0);
      const deltaTxt = delta > 0 ? `+${delta}` : `${delta}`;
      return `<div class="theme-item" data-keyword="${esc(it.keyword)}">
        <div><b>${esc(it.keyword)}</b><span class="pill-trend ${trend}">${esc(it.trend_label || "flat")}</span></div>
        <div class="meta">언급 ${it.mention_count} · 연결종목 ${it.stock_count} · Δ ${deltaTxt}</div>
      </div>`;
    }).join("")
  }</div>`;
  document.querySelectorAll("[data-keyword]").forEach((el) => {
    el.onclick = async () => {
      const kw = el.getAttribute("data-keyword") || "";
      await loadStocksByKeyword(kw);
      await loadArticlesByKeyword(kw);
    };
  });
}

async function loadTags() {
  const source = $("tagSourceFilter")?.value || "";
  const qs = new URLSearchParams({ limit: "200" });
  if (source) qs.set("source", source);
  const d = await fetchJSON(`/theme-map/tags?${qs}`);
  const items = d.items || [];
  $("tagHint").textContent = `${items.length}개`;
  if (!items.length) {
    $("tagBody").innerHTML = `<div class="empty"><span class="ico">📭</span>태그가 없습니다. 스냅샷 갱신을 눌러주세요.</div>`;
    return;
  }
  $("tagBody").innerHTML = `<div class="theme-list">${
    items.map((it) => `
      <div class="theme-item ${currentTagId === it.id ? "active" : ""}" data-tag-id="${it.id}">
        <div><b>${esc(it.name_ko)}</b> ${renderSourceChip(it)}</div>
        <div class="meta">edge ${it.edge_count} · ${esc(it.source_label || it.source || "")}</div>
        ${renderKeyPoint(it.key_point)}
      </div>
    `).join("")
  }</div>`;
  document.querySelectorAll("[data-tag-id]").forEach((el) => {
    el.onclick = async () => {
      currentTagId = Number(el.dataset.tagId);
      await loadTags();
      await loadStocksByTag(currentTagId);
    };
  });
}

async function loadStocksByTag(tagId) {
  const d = await fetchJSON(`/theme-map/tags/${tagId}/stocks?limit=200`);
  const items = d.items || [];
  $("stockHint").textContent = `${items.length}개`;
  if (!items.length) {
    $("stocksByTagBody").innerHTML = `<div class="empty"><span class="ico">🧭</span>해당 태그 종목이 없습니다.</div>`;
    return;
  }
  $("stocksByTagBody").innerHTML = `<div class="theme-list">${
    items.map((it) => `
      <div class="theme-item" data-stock-code="${esc(it.stock_code)}">
        <div><b>${esc(it.stock_name)}</b> <span class="hint">${esc(it.stock_code)}</span> ${renderSourceChip(it)}</div>
        <div class="meta">${esc(it.role || "member")} · ${esc(it.source_label || it.source || "")}</div>
        ${renderReason(it.reason)}
      </div>
    `).join("")
  }</div>`;
  bindStockClickHandlers($("stocksByTagBody"), selectStock);
}

async function loadStocksByKeyword(keyword) {
  if (!keyword) return;
  const d = await fetchJSON(`/theme-map/keywords/${encodeURIComponent(keyword)}/stocks?limit=200`);
  const items = d.items || [];
  $("stockHint").textContent = `키워드: ${keyword} · ${items.length}개`;
  if (!items.length) {
    $("stocksByTagBody").innerHTML = `<div class="empty"><span class="ico">🧭</span>해당 키워드 연결 종목이 없습니다.</div>`;
    return;
  }
  $("stocksByTagBody").innerHTML = `<div class="theme-list">${
    items.map((it) => `
      <div class="theme-item" data-stock-code="${esc(it.stock_code)}">
        <div><b>${esc(it.stock_name)}</b> <span class="hint">${esc(it.stock_code)}</span> ${renderSourceChip(it)}</div>
        <div class="meta">${esc(it.role || "member")} · ${esc(it.source_label || it.source || "")}</div>
        ${renderReason(it.reason)}
      </div>
    `).join("")
  }</div>`;
  bindStockClickHandlers($("stocksByTagBody"), selectStock);
}

async function lookupStockTags() {
  const raw = ($("stockCodeInput").value || "").trim();
  const code = raw.replace(/\D/g, "").padStart(6, "0").slice(-6);
  if (!/^\d{6}$/.test(code)) {
    toast("종목코드 6자리를 입력해주세요.", true);
    return;
  }
  $("stockCodeInput").value = code;
  const d = await fetchJSON(`/theme-map/stocks/${code}/tags?limit=100`);
  const items = d.items || [];
  if (!items.length) {
    $("tagsByStockBody").innerHTML = `<div class="empty"><span class="ico">🔎</span>${esc(code)} 매핑이 아직 없습니다.</div>`;
    await loadArticlesByStock(code);
    return;
  }
  const name = items[0].stock_name || code;
  $("tagsByStockBody").innerHTML = `
    <div class="kv-mini"><span><b>${esc(name)}</b></span><span>${esc(code)}</span><span>태그 ${items.length}개</span></div>
    <div class="theme-list">${
      items.map((it) => `
        <div class="theme-item">
          <div><b>${esc(it.tag_name)}</b> <span class="hint">${esc(it.tag_type)}</span> ${renderSourceChip(it)}</div>
          <div class="meta">${esc(it.source_label || it.source || "")} · ${esc(it.role || "")}</div>
          ${renderReason(it.reason)}
          ${renderKeyPoint(it.key_point)}
        </div>
      `).join("")
    }</div>
  `;
  await loadArticlesByStock(code);
}

async function loadSourceCross() {
  const body = $("crossBody");
  if (!body) return;
  body.innerHTML = "불러오는 중...";
  try {
    const d = await fetchJSON("/theme-map/source-cross");
    if (!d.ok) {
      body.innerHTML = `<div class="empty"><span class="ico">⚠</span>${esc(d.error || "리포트 없음")}</div>`;
      return;
    }
    const s = d.stocks || {};
    const c = d.coverage_pct || {};
    const ov = d.name_overlap || {};
    $("crossHint").textContent = `기준일 ${d.biz_date || "-"}`;
    body.innerHTML = `
      <div class="coverage-stats">
        <div class="coverage-stat"><div class="label">네이버 종목</div><div class="value">${esc(s.naver || 0)}</div><div class="sub">${fmtPct(c.naver_of_union)} of union</div></div>
        <div class="coverage-stat"><div class="label">키움 종목</div><div class="value">${esc(s.kiwoom || 0)}</div><div class="sub">${fmtPct(c.kiwoom_of_union)} of union</div></div>
        <div class="coverage-stat"><div class="label">알파스퀘어 종목</div><div class="value">${esc(s.alphasquare || 0)}</div><div class="sub">${fmtPct(c.alphasquare_of_union)} of union</div></div>
        <div class="coverage-stat"><div class="label">합집합</div><div class="value">${esc(s.union || 0)}</div><div class="sub">3소스</div></div>
        <div class="coverage-stat"><div class="label">3소스 교집합</div><div class="value">${esc(s.all_three || 0)}</div><div class="sub">N∩K∩AS</div></div>
        <div class="coverage-stat"><div class="label">AS만 보유</div><div class="value">${esc(s.alphasquare_only || 0)}</div><div class="sub">네이버 갭 메움 ${esc(s.alphasquare_fills_naver_gap || 0)}</div></div>
      </div>
      <div class="cross-overlap hint" style="margin-top:8px;">
        테마명 일치(교집합 종목 기준):
        N∩K ${esc((ov.naver_kiwoom || {}).share_pct ?? "-")}% ·
        N∩AS ${esc((ov.naver_alphasquare || {}).share_pct ?? "-")}% ·
        K∩AS ${esc((ov.kiwoom_alphasquare || {}).share_pct ?? "-")}%
      </div>
    `;
  } catch (e) {
    body.innerHTML = `<div class="empty"><span class="ico">⚠</span>${esc(e.message)}</div>`;
  }
}

async function submitManualMapping() {
  const code = ($("manualStockCode").value || "").replace(/\D/g, "").padStart(6, "0").slice(-6);
  const tagName = ($("manualTagName").value || "").trim();
  const tagType = $("manualTagType").value || "theme";
  const stockName = ($("manualStockName").value || "").trim();
  if (!/^\d{6}$/.test(code)) {
    toast("종목코드 6자리를 입력해주세요.", true);
    return;
  }
  if (!tagName) {
    toast("테마/키워드명을 입력해주세요.", true);
    return;
  }
  try {
    const d = await fetchJSON("/theme-map/manual", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        stock_code: code,
        stock_name: stockName || null,
        tag_name: tagName,
        tag_type: tagType,
      }),
    });
    toast(`${d.stock_name || code} → ${d.tag_name} (${d.tag_type}) 추가`);
    $("manualStockCode").value = code;
    $("stockCodeInput").value = code;
    $("manualTagName").value = "";
    await Promise.all([lookupStockTags(), loadTags(), loadCoverage(false)]);
  } catch (e) {
    toast(`수동 매핑 실패: ${e.message}`, true);
  }
}

function showBulkResult(msg, isErr = false) {
  const el = $("manualBulkResult");
  if (!el) return;
  el.hidden = false;
  el.className = `manual-bulk-result${isErr ? " err" : ""}`;
  el.textContent = msg;
}

async function submitManualBulk() {
  const btn = $("btnManualBulk");
  const fileInput = $("manualBulkFile");
  const text = ($("manualBulkText").value || "").trim();
  const tagType = $("manualBulkTagType").value || "theme";
  const file = fileInput?.files?.[0];

  if (!file && !text) {
    toast("텍스트를 붙여넣거나 파일을 선택하세요.", true);
    return;
  }

  btn.disabled = true;
  btn.textContent = "저장 중...";
  try {
    let d;
    if (file) {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("tag_type", tagType);
      const r = await fetch("/theme-map/manual/upload", { method: "POST", body: fd });
      const body = await r.json().catch(async () => ({ detail: await r.text() }));
      if (!r.ok) {
        const detail = body?.detail;
        const msg = typeof detail === "object"
          ? (detail.message || JSON.stringify(detail))
          : (detail || r.statusText);
        throw new Error(msg);
      }
      d = body;
    } else {
      d = await fetchJSON("/theme-map/manual/bulk", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, tag_type: tagType }),
      });
    }

    const parseErrs = Array.isArray(d.parse_errors) ? d.parse_errors : [];
    const saveErrs = Array.isArray(d.errors) ? d.errors : [];
    const summary = [
      `저장 ${d.edge_count || 0}건 (신규 ${d.added || 0} · 갱신 ${d.updated || 0})`,
      `종목 ${d.stock_count || 0} · 테마 ${d.tag_count || 0}`,
      parseErrs.length ? `파싱 스킵 ${parseErrs.length}` : null,
      saveErrs.length ? `저장 오류 ${saveErrs.length}` : null,
    ].filter(Boolean).join(" · ");

    const detailLines = [
      ...parseErrs.slice(0, 8).map((e) => `L${e.line}: ${e.error}${e.raw ? ` (${e.raw})` : ""}`),
      ...saveErrs.slice(0, 8).map((e) => `${e.stock_code || ""} ${e.theme || ""}: ${e.error}`),
    ];
    showBulkResult([summary, ...detailLines].join("\n"), parseErrs.length + saveErrs.length > 0 && !(d.edge_count > 0));
    toast(summary);
    if (fileInput) fileInput.value = "";
    if ($("manualBulkFileName")) $("manualBulkFileName").textContent = "파일 없음";
    await Promise.all([loadTags(), loadCoverage(false)]);
  } catch (e) {
    showBulkResult(`실패: ${e.message}`, true);
    toast(`일괄 저장 실패: ${e.message}`, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "일괄 저장";
  }
}

async function refreshSnapshot() {
  const btn = $("btnRefreshSnapshot");
  btn.disabled = true;
  btn.textContent = "갱신 중...";
  try {
    const qs = new URLSearchParams({
      top_n: "0",
      include_kiwoom: "true",
      include_alphasquare: "true",
      include_news_keywords: "true",
    });
    const d = await fetchJSON(`/theme-map/refresh?${qs}`, { method: "POST" });
    const asOk = d.alphasquare_ok;
    const kwOk = d.kiwoom_ok;
    const extra = [
      asOk == null ? null : `AS ${asOk ? "ok" : "fail"}`,
      kwOk == null ? null : `K ${kwOk ? "ok" : "fail"}`,
    ].filter(Boolean).join(" · ");
    toast(
      `갱신 완료: 테마 ${d.themes} · 매핑 ${d.edges} · 키워드 ${d.keywords}` +
        (extra ? ` (${extra})` : "")
    );
    await loadAll();
  } catch (e) {
    toast(`갱신 실패: ${e.message}`, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "스냅샷 갱신";
  }
}

async function loadAll() {
  await Promise.all([
    loadThemeTradeFlow(false),
    loadKeywords(),
    loadTags(),
    loadCoverage(false),
    loadSourceCross(),
  ]);
  $("lastUpdated").textContent = new Date().toLocaleTimeString("ko-KR");
}

document.addEventListener("DOMContentLoaded", async () => {
  $("btnRefreshSnapshot").onclick = refreshSnapshot;
  if ($("themeFlowRefresh")) $("themeFlowRefresh").onclick = () => loadThemeTradeFlow(true);
  if ($("themeFlowSort")) $("themeFlowSort").onchange = () => loadThemeTradeFlow(false);
  bindThemeFlowResize();
  $("btnStockLookup").onclick = lookupStockTags;
  $("stockCodeInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") lookupStockTags();
  });
  $("tagSourceFilter")?.addEventListener("change", () => loadTags());
  $("btnCrossReload")?.addEventListener("click", () => loadSourceCross());
  $("btnCoverageReload").onclick = () => loadCoverage(true);
  $("coverageMarket").onchange = () => loadCoverage(true);
  $("coverageGap").onchange = () => loadCoverage(true);
  $("coverageSearch").addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadCoverage(true);
  });
  $("btnCoveragePrev").onclick = () => {
    coverageOffset = Math.max(0, coverageOffset - COVERAGE_PAGE);
    loadCoverage(false);
  };
  $("btnCoverageNext").onclick = () => {
    coverageOffset += COVERAGE_PAGE;
    loadCoverage(false);
  };
  $("btnManualMap").onclick = submitManualMapping;
  $("manualTagName").addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitManualMapping();
  });
  $("btnManualBulk").onclick = submitManualBulk;
  $("manualBulkFile")?.addEventListener("change", () => {
    const f = $("manualBulkFile")?.files?.[0];
    $("manualBulkFileName").textContent = f ? f.name : "파일 없음";
  });
  await loadAll();
});
