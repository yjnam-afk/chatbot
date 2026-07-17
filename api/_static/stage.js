/* 무대 — artifact(완결 HTML)를 iframe srcdoc으로 렌더, 분량 게이지·내보내기 (front-ui-spec.md §2-4). */

const Stage = (() => {
  const stageEl = document.getElementById("stage");
  const wrap = document.getElementById("stage-wrap");
  const empty = document.getElementById("stage-empty");
  const frame = document.getElementById("sheet-frame");
  const overlay = document.getElementById("stage-overlay");
  const overlayCap = document.getElementById("overlay-cap");
  const gaugeFill = document.getElementById("gauge-fill");
  const gaugeText = document.getElementById("gauge-text");
  const exportBtns = ["ex-print", "ex-pdf", "ex-html", "ex-tab"].map((id) => document.getElementById(id));
  const navEl = document.getElementById("page-nav");
  const pnLabel = document.getElementById("pn-label");
  const pnPrev = document.getElementById("pn-prev");
  const pnNext = document.getElementById("pn-next");

  let current = null; // {title, html}
  let pageTops = []; // 무대 스크롤 좌표계의 각 쪽 시작 위치
  let curPage = 0;

  function blobUrl(art) {
    return URL.createObjectURL(new Blob([art.html], { type: "text/html" }));
  }

  function download(art) {
    const a = document.createElement("a");
    a.href = blobUrl(art);
    a.download = (art.title || "답안지").replace(/[\\/:*?"<>|]/g, "_") + ".html";
    a.click();
  }

  function syncHeight() {
    try {
      const d = frame.contentDocument;
      if (d && d.documentElement) {
        frame.style.height = d.documentElement.scrollHeight + "px";
      }
    } catch (e) { /* sandbox 접근 실패 시 기본 높이 유지 */ }
  }

  // ---- 쪽 이동 네비 (무대는 뷰포트에 가둬 내부 스크롤 — 발주자 피드백 2026-07)
  function buildPageNav() {
    try {
      const d = frame.contentDocument;
      const pages = d ? Array.from(d.querySelectorAll(".page")) : [];
      if (!pages.length) {
        navEl.hidden = true;
        return;
      }
      const base = wrap.offsetTop;
      pageTops = pages.map((p) => base + p.offsetTop);
      navEl.hidden = false;
      updatePageNav();
    } catch (e) {
      navEl.hidden = true;
    }
  }

  function updatePageNav() {
    if (navEl.hidden || !pageTops.length) return;
    const pos = stageEl.scrollTop + 60;
    let i = 0;
    for (let k = 0; k < pageTops.length; k++) {
      if (pageTops[k] <= pos) i = k;
    }
    curPage = i;
    pnLabel.textContent = `${i + 1} / ${pageTops.length} 쪽`;
    pnPrev.disabled = i === 0;
    pnNext.disabled = i >= pageTops.length - 1;
  }

  function gotoPage(i) {
    if (i < 0 || i >= pageTops.length) return;
    stageEl.scrollTo({ top: Math.max(0, pageTops[i] - 14), behavior: "smooth" });
  }

  pnPrev.onclick = () => gotoPage(curPage - 1);
  pnNext.onclick = () => gotoPage(curPage + 1);
  stageEl.addEventListener("scroll", () => requestAnimationFrame(updatePageNav));

  function render(art) {
    current = art;
    empty.hidden = true;
    wrap.hidden = false;
    frame.classList.add("enter");
    frame.onload = () => {
      syncHeight();
      buildPageNav();
      requestAnimationFrame(() => frame.classList.remove("enter"));
    };
    frame.srcdoc = art.html;
    stageEl.scrollTop = 0;
    exportBtns.forEach((b) => (b.disabled = false));
  }

  // 분량 게이지: reply.sheet {pages, lines, target_pages} — 진행 = (22·(N−1)+M) / (22·T)
  function gauge(sheet) {
    if (!sheet || !sheet.pages) return;
    const lines = 22 * (sheet.pages - 1) + (sheet.lines || 0);
    const ratio = Math.min(1, lines / (22 * (sheet.target_pages || 3.5)));
    gaugeFill.style.width = Math.round(ratio * 100) + "%";
    // 목표 대비 크게 미달(뼈대 요약본 등)이면 앰버로 강조
    gaugeFill.classList.toggle("low", ratio < 0.7);
    gaugeText.innerHTML = "";
    const b = document.createElement("b");
    b.textContent = `${sheet.pages}쪽 ${sheet.lines}줄`;
    gaugeText.append(b, ` / 목표 ${sheet.target_pages}매${ratio < 0.7 ? " · 목표 미달" : ""}`);
  }

  function showOverlay(caption) {
    if (wrap.hidden) return; // 답안지가 없으면 빈 용지가 곧 대기 화면
    overlay.hidden = false;
    overlayCap.textContent = caption || "작성 중…";
  }
  function setOverlayCaption(caption) {
    if (!overlay.hidden && caption) overlayCap.textContent = caption;
  }
  function hideOverlay() {
    overlay.hidden = true;
  }

  function print() {
    if (!current) return;
    try {
      frame.contentWindow.focus();
      frame.contentWindow.print();
    } catch (e) {
      window.open(blobUrl(current));
    }
  }

  document.getElementById("ex-print").onclick = print;
  document.getElementById("ex-pdf").onclick = print; // 인쇄 다이얼로그에서 "PDF로 저장"
  document.getElementById("ex-html").onclick = () => current && download(current);
  document.getElementById("ex-tab").onclick = () => current && window.open(blobUrl(current));

  window.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "p" && current) {
      e.preventDefault();
      print();
    }
  });
  window.addEventListener("resize", () => {
    syncHeight();
    buildPageNav();
  });

  return { render, gauge, showOverlay, setOverlayCaption, hideOverlay, hasSheet: () => !!current };
})();
