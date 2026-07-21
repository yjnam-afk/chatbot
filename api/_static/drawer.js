/* 토픽 서랍 — 다크 사이드바 컴팩트 목록 (리디자인 ② — re2.html).
   폴더(분류) 접기/펼치기 + 토픽 행 클릭 = 바로 작성(2교시형), 보조 [10점] 버튼 = 1교시형.
   GET /api/library 1회 로딩. 브랜드 아래 통계 한 줄도 여기서 채운다. */

const Drawer = (() => {
  const foldersEl = document.getElementById("drawer-folders");
  const listEl = document.getElementById("drawer-list");
  const searchEl = document.getElementById("drawer-search");
  const statsEl = document.getElementById("side-stats");

  let items = {}; // id -> {name, category, mn, def, full}
  let openFolder = null;
  let filter = "";

  function topSeg(category) {
    const seg = String(category || "").split(">")[0].trim();
    return seg || "기타";
  }

  function name(id) {
    return (items[id] && items[id].name) || id;
  }

  async function load() {
    try {
      const r = await fetch("/api/library");
      if (!r.ok) throw new Error("HTTP " + r.status);
      const data = await r.json();
      items = data.items || {};
      if (statsEl) statsEl.textContent = `토픽 ${data.topics || 0} · 즉시 작성 ${data.full_parts || 0}`;
    } catch (e) {
      items = {};
      if (statsEl) statsEl.textContent = "서버 연결 실패";
    }
    render();
  }

  function ask(t, pts) {
    if (window.App) window.App.ask(`${t.name}에 대하여 ${pts === 10 ? "약술" : "설명"}하시오 (${pts}점)`);
  }

  function row(t) {
    const div = document.createElement("div");
    div.className = "trow";
    div.title = t.full ? (t.def || "") + " — 클릭하면 바로 작성" : (t.def || "") + " — 요약본 (핵심 부품 준비 중)";
    const dot = document.createElement("span");
    dot.className = "full-dot" + (t.full ? "" : " stub");
    const nm = document.createElement("span");
    nm.className = "nm";
    nm.textContent = t.name;
    const b10 = document.createElement("button");
    b10.type = "button";
    b10.className = "b10";
    b10.textContent = "10점";
    b10.title = "1교시형(용어)으로 작성";
    b10.onclick = (e) => { e.stopPropagation(); ask(t, 10); };
    div.append(dot, nm, b10);
    div.onclick = () => ask(t, 25);
    return div;
  }

  function sortEntries(arr) {
    // 즉시 작성(풀부품) 우선, 같은 등급은 id 순
    return arr.sort(([ia, a], [ib, b]) => (b.full === true) - (a.full === true) || ia.localeCompare(ib));
  }

  function render() {
    foldersEl.innerHTML = "";
    listEl.innerHTML = "";
    if (!Object.keys(items).length) {
      const d = document.createElement("div");
      d.className = "side-empty";
      d.textContent = "라이브러리 준비 중 — 토픽이 등록되면 여기서 바로 답안지를 만들 수 있어요.";
      listEl.appendChild(d);
      return;
    }

    if (filter) {
      // 검색 모드: 폴더 없이 전체에서 필터
      const f = filter.toLowerCase();
      const arr = sortEntries(Object.entries(items).filter(([, t]) =>
        (t.name || "").toLowerCase().includes(f) ||
        (t.mn || "").toLowerCase().includes(f) ||
        (t.def || "").toLowerCase().includes(f))).slice(0, 30);
      if (!arr.length) {
        const d = document.createElement("div");
        d.className = "side-empty";
        d.textContent = "검색 결과가 없어요.";
        listEl.appendChild(d);
        return;
      }
      arr.forEach(([, t]) => listEl.appendChild(row(t)));
      return;
    }

    // 폴더 모드: 분류 접기/펼치기, 펼친 폴더 아래에 토픽 행
    const groups = {};
    for (const [id, t] of Object.entries(items)) {
      const seg = topSeg(t.category);
      (groups[seg] = groups[seg] || []).push([id, t]);
    }
    for (const [seg, arr] of Object.entries(groups).sort((a, b) => b[1].length - a[1].length)) {
      const rowEl = document.createElement("div");
      rowEl.className = "folder" + (openFolder === seg ? " open" : "");
      const label = document.createElement("span");
      label.textContent = (openFolder === seg ? "📂 " : "📁 ") + seg;
      const num = document.createElement("span");
      num.className = "n";
      num.textContent = arr.length;
      rowEl.append(label, num);
      rowEl.onclick = () => {
        openFolder = openFolder === seg ? null : seg;
        render();
      };
      foldersEl.appendChild(rowEl);
      if (openFolder === seg) {
        sortEntries(arr).slice(0, 40).forEach(([, t]) => foldersEl.appendChild(row(t)));
      }
    }
    const hint = document.createElement("div");
    hint.className = "drawer-hint";
    hint.textContent = "● 즉시 작성 · ○ 요약본 — 토픽을 클릭하면 바로 작성돼요";
    listEl.appendChild(hint);
  }

  searchEl.addEventListener("input", () => {
    filter = searchEl.value.trim();
    render();
  });

  return { load, name };
})();
