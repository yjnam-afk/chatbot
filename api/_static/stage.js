/* 무대 — artifact(완결 HTML)를 iframe srcdoc으로 렌더, 분량 게이지·내보내기 (front-ui-spec.md §2-4). */

const Stage = (() => {
  const wrap = document.getElementById("stage-wrap");
  const empty = document.getElementById("stage-empty");
  const frame = document.getElementById("sheet-frame");
  const overlay = document.getElementById("stage-overlay");
  const overlayCap = document.getElementById("overlay-cap");
  const gaugeFill = document.getElementById("gauge-fill");
  const gaugeText = document.getElementById("gauge-text");
  const exportBtns = ["ex-print", "ex-pdf", "ex-html", "ex-tab"].map((id) => document.getElementById(id));

  let current = null; // {title, html}

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

  function render(art) {
    current = art;
    empty.hidden = true;
    wrap.hidden = false;
    frame.classList.add("enter");
    frame.onload = () => {
      syncHeight();
      requestAnimationFrame(() => frame.classList.remove("enter"));
    };
    frame.srcdoc = art.html;
    exportBtns.forEach((b) => (b.disabled = false));
  }

  // 분량 게이지: reply.sheet {pages, lines, target_pages} — 진행 = (22·(N−1)+M) / (22·T)
  function gauge(sheet) {
    if (!sheet || !sheet.pages) return;
    const lines = 22 * (sheet.pages - 1) + (sheet.lines || 0);
    const ratio = Math.min(1, lines / (22 * (sheet.target_pages || 3.5)));
    gaugeFill.style.width = Math.round(ratio * 100) + "%";
    gaugeText.innerHTML = "";
    const b = document.createElement("b");
    b.textContent = `${sheet.pages}쪽 ${sheet.lines}줄`;
    gaugeText.append(b, ` / 목표 ${sheet.target_pages}매`);
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
  window.addEventListener("resize", syncHeight);

  return { render, gauge, showOverlay, setOverlayCaption, hideOverlay, hasSheet: () => !!current };
})();
