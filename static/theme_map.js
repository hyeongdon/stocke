const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function fetchJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
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
  const d = await fetchJSON("/theme-map/tags?limit=200");
  const items = d.items || [];
  $("tagHint").textContent = `${items.length}개`;
  if (!items.length) {
    $("tagBody").innerHTML = `<div class="empty"><span class="ico">📭</span>태그가 없습니다. 스냅샷 갱신을 눌러주세요.</div>`;
    return;
  }
  $("tagBody").innerHTML = `<div class="theme-list">${
    items.map((it) => `
      <div class="theme-item ${currentTagId === it.id ? "active" : ""}" data-tag-id="${it.id}">
        <div><b>${esc(it.name_ko)}</b></div>
        <div class="meta">edge ${it.edge_count} · ${esc(it.tag_key)}</div>
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
        <div><b>${esc(it.stock_name)}</b> <span class="hint">${esc(it.stock_code)}</span></div>
        <div class="meta">${esc(it.role || "member")} · ${esc(it.source || "")}</div>
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
        <div><b>${esc(it.stock_name)}</b> <span class="hint">${esc(it.stock_code)}</span></div>
        <div class="meta">${esc(it.role || "member")} · ${esc(it.source || "")}</div>
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
          <div><b>${esc(it.tag_name)}</b> <span class="hint">${esc(it.tag_type)}</span>${it.source === "manual" ? ' <span class="cov-chip manual">수동</span>' : ""}</div>
          <div class="meta">${esc(it.source)} · ${esc(it.role || "")}</div>
        </div>
      `).join("")
    }</div>
  `;
  await loadArticlesByStock(code);
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
    const d = await fetchJSON("/theme-map/refresh?top_n=0", { method: "POST" });
    toast(`갱신 완료: 테마 ${d.themes} · 매핑 ${d.edges} · 키워드 ${d.keywords}`);
    await loadAll();
  } catch (e) {
    toast(`갱신 실패: ${e.message}`, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "스냅샷 갱신";
  }
}

async function loadAll() {
  await Promise.all([loadKeywords(), loadTags(), loadCoverage(false)]);
  $("lastUpdated").textContent = new Date().toLocaleTimeString("ko-KR");
}

document.addEventListener("DOMContentLoaded", async () => {
  $("btnRefreshSnapshot").onclick = refreshSnapshot;
  $("btnStockLookup").onclick = lookupStockTags;
  $("stockCodeInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") lookupStockTags();
  });
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
