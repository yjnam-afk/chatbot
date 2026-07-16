/* 토픽 서랍 — GET /api/library 목록을 분류 폴더 + 토픽 카드로 렌더 (front-ui-spec.md §2-3). */

const Drawer = (() => {
  const foldersEl = document.getElementById("drawer-folders");
  const listEl = document.getElementById("drawer-list");
  const searchEl = document.getElementById("drawer-search");

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
    } catch (e) {
      items = {};
    }
    renderFolders();
    renderList();
  }

  function entries() {
    let arr = Object.entries(items);
    if (filter) {
      const f = filter.toLowerCase();
      arr = arr.filter(([, t]) =>
        (t.name || "").toLowerCase().includes(f) ||
        (t.mn || "").toLowerCase().includes(f) ||
        (t.def || "").toLowerCase().includes(f));
    } else if (openFolder) {
      arr = arr.filter(([, t]) => topSeg(t.category) === openFolder);
    }
    // 풀부품 토픽 우선 정렬
    arr.sort(([ia, a], [ib, b]) => (b.full === true) - (a.full === true) || ia.localeCompare(ib));
    return arr;
  }

  function renderFolders() {
    foldersEl.innerHTML = "";
    const counts = {};
    for (const t of Object.values(items)) {
      const seg = topSeg(t.category);
      counts[seg] = (counts[seg] || 0) + 1;
    }
    for (const [seg, n] of Object.entries(counts).sort((a, b) => b[1] - a[1])) {
      const row = document.createElement("div");
      row.className = "folder" + (openFolder === seg ? " open" : "");
      const label = document.createElement("span");
      label.textContent = (openFolder === seg ? "📂 " : "📁 ") + seg;
      const num = document.createElement("span");
      num.className = "n";
      num.textContent = n;
      row.append(label, num);
      row.onclick = () => {
        openFolder = openFolder === seg ? null : seg;
        renderFolders();
        renderList();
      };
      foldersEl.appendChild(row);
    }
  }

  function renderList() {
    listEl.innerHTML = "";
    if (!Object.keys(items).length) {
      const d = document.createElement("div");
      d.className = "drawer-empty";
      d.textContent = "라이브러리 준비 중 — 토픽이 등록되면 여기서 바로 답안지를 만들 수 있어요.";
      listEl.appendChild(d);
      return;
    }
    const arr = entries().slice(0, 40);
    if (!arr.length) {
      const d = document.createElement("div");
      d.className = "drawer-empty";
      d.textContent = "검색 결과가 없어요.";
      listEl.appendChild(d);
      return;
    }
    for (const [, t] of arr) {
      const card = document.createElement("div");
      card.className = "tcard";
      const h = document.createElement("h5");
      h.textContent = t.name;
      card.appendChild(h);
      if (t.mn) {
        const mn = document.createElement("div");
        mn.className = "mn";
        mn.textContent = "암기 " + t.mn;
        card.appendChild(mn);
      }
      const p = document.createElement("p");
      p.textContent = t.def || "";
      card.appendChild(p);
      const acts = document.createElement("div");
      acts.className = "acts";
      for (const [label, pts] of [["📝 1교시형", 10], ["📄 2교시형", 25]]) {
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = label;
        b.onclick = () => window.App && window.App.ask(`${t.name}에 대하여 설명하시오 (${pts}점)`);
        acts.appendChild(b);
      }
      card.appendChild(acts);
      listEl.appendChild(card);
    }
  }

  searchEl.addEventListener("input", () => {
    filter = searchEl.value.trim();
    renderList();
  });

  return { load, name };
})();
