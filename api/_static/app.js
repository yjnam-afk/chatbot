/* 글루 — 리디자인 ② (다크 사이드바, 시각 기준 re2.html).
   문항 제출 → NDJSON 스트림 → 무대 렌더 + 사이드바 "이번 답안"(목차·두문자·분량·인쇄).
   질문 슬라이드 패널(챗 겸용 — 발주자 확정, 평시 숨김), 최근 답안지(localStorage
   최근 10건 — 서버 무상태 유지). progress.js 상태 한 줄·stage.js 렌더 재사용. */

const formEl = document.getElementById("chat-form");
const inputEl = document.getElementById("chat-input");
const askBtn = document.getElementById("ask-btn");
const badgeEl = document.getElementById("mode-badge");
const statusEl = document.getElementById("status-line");

const nowSec = document.getElementById("now-sec");
const sumLine = document.getElementById("sum-line");
const tocEl = document.getElementById("toc");
const mnCard = document.getElementById("mn-card");
const mnBody = document.getElementById("mn-body");
const recentEl = document.getElementById("recent-list");

const sideEl = document.getElementById("side");
const scrimEl = document.getElementById("scrim");
const chatPanel = document.getElementById("chat-panel");
const chatToggle = document.getElementById("chat-toggle");
const chatLog = document.getElementById("chat-log");
const miniForm = document.getElementById("chat-mini");
const miniInput = document.getElementById("chat-mini-input");

const history = []; // [{role, content}] — 클라이언트가 유지 (서버 대화 컨텍스트용)
let kindHint = ""; // 교시형 칩 수동 선택 ("" = 자동 판별)
let lastMeta = null; // 마지막 reply (두문자 폴백·최근 저장용)

// ---------------------------------------------------------- 초기화 (수험생 언어 — 내부 용어 0)
fetch("/api/agents")
  .then((r) => r.json())
  .then((data) => {
    badgeEl.textContent = data.demo
      ? "등록 토픽 즉시 작성 · 미등록 AI 작성은 준비 중"
      : "등록 토픽 즉시 작성 + 미등록 AI 작성";
  })
  .catch(() => { badgeEl.textContent = "서버 연결 실패"; });

Drawer.load();

// ---------------------------------------------------------- 사이드바 (모바일 햄버거)
document.getElementById("side-toggle").onclick = () => {
  sideEl.classList.toggle("open");
  scrimEl.hidden = !sideEl.classList.contains("open");
};
scrimEl.onclick = () => {
  sideEl.classList.remove("open");
  closeChat();
  scrimEl.hidden = true;
};

// ---------------------------------------------------------- 질문 슬라이드 패널
function openChat() {
  chatPanel.hidden = false;
  requestAnimationFrame(() => chatPanel.classList.add("open"));
  chatToggle.classList.add("on");
  chatLog.scrollTop = chatLog.scrollHeight;
  miniInput.focus();
}
function closeChat() {
  chatPanel.classList.remove("open");
  chatToggle.classList.remove("on");
  setTimeout(() => { if (!chatPanel.classList.contains("open")) chatPanel.hidden = true; }, 220);
}
chatToggle.onclick = () => (chatPanel.classList.contains("open") ? closeChat() : openChat());
document.getElementById("chat-close").onclick = closeChat;

// ---------------------------------------------------------- 새 답안지 (빈 상태 복귀)
document.getElementById("new-btn").onclick = () => {
  document.getElementById("stage-wrap").hidden = true;
  document.getElementById("stage-empty").hidden = false;
  document.getElementById("page-nav").hidden = true;
  nowSec.hidden = true;
  inputEl.value = "";
  autosize();
  sideEl.classList.remove("open");
  scrimEl.hidden = true;
  inputEl.focus();
};

// ---------------------------------------------------------- 최근 답안지 (localStorage ≤10건)
const RECENT_KEY = "gisulsa.recent.v1";
function recentAll() {
  try { return JSON.parse(localStorage.getItem(RECENT_KEY)) || []; }
  catch (e) { return []; }
}
function recentSave(ev) {
  if (!ev.artifact || !ev.artifact.html) return;
  const item = {
    ts: Date.now(),
    title: ev.artifact.title || (ev.exam && ev.exam.topic) || "답안지",
    artifact: ev.artifact,
    sheet: ev.sheet || null,
    exam: ev.exam || null,
    matched: ev.matched || [],
    library: ev.library,
    review: ev.review ? { warnings: ev.review.warnings || [], score: ev.review.score } : null,
  };
  let arr = recentAll().filter((r) => r.title !== item.title); // 같은 제목은 최신으로 교체
  arr.unshift(item);
  arr = arr.slice(0, 10); // 용량 고려 최근 10건 제한
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(arr)); }
  catch (e) { try { localStorage.setItem(RECENT_KEY, JSON.stringify(arr.slice(0, 5))); } catch (e2) { /* 저장 불가 시 무시 */ } }
  renderRecent();
}
function renderRecent() {
  const arr = recentAll();
  recentEl.innerHTML = "";
  if (!arr.length) {
    const d = document.createElement("div");
    d.className = "side-empty";
    d.textContent = "아직 없어요 — 첫 답안지를 만들어 보세요";
    recentEl.appendChild(d);
    return;
  }
  arr.forEach((r) => {
    const div = document.createElement("div");
    div.className = "ritem";
    const nm = document.createElement("span");
    nm.className = "nm";
    nm.textContent = r.title;
    const pg = document.createElement("small");
    pg.textContent = r.sheet ? `${r.sheet.pages}쪽` : "";
    div.append(nm, pg);
    div.onclick = () => {
      applyReply(r, null); // 저장분 재렌더 (서버 호출 없음)
      sideEl.classList.remove("open");
      scrimEl.hidden = true;
    };
    recentEl.appendChild(div);
  });
}
renderRecent();

// ---------------------------------------------------------- 상태 한 줄 (progress.js 재사용)
let slowTimer = 0;
function statusShow(text, isErr) {
  statusEl.textContent = text;
  statusEl.classList.toggle("err", !!isErr);
  statusEl.hidden = false;
}
function statusHide() {
  statusEl.hidden = true;
  clearTimeout(slowTimer);
  slowTimer = 0;
}

// ---------------------------------------------------------- 대화 로그 (질문 패널)
function chatAdd(role, text) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  div.textContent = text;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
  return div;
}
function chatAddSheetCard(ev) {
  const div = document.createElement("div");
  div.className = "msg bot";
  const title = (ev.artifact && ev.artifact.title) || (ev.exam && ev.exam.topic) || "답안지";
  const pages = ev.sheet ? `${ev.sheet.pages}쪽` : "";
  div.textContent = `『${title}』 답안지 완성 (${ev.exam ? ev.exam.kind + " " + ev.exam.points + "점 · " : ""}${pages})`;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "view-btn";
  btn.textContent = "답안지 보기";
  btn.onclick = () => { closeChat(); Stage.gotoPage(0); };
  div.appendChild(document.createElement("br"));
  div.appendChild(btn);
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
}

// ---------------------------------------------------------- 교시형 칩 (입력 포커스 시 노출)
document.querySelectorAll(".kchip").forEach((chip) => {
  chip.addEventListener("mousedown", (e) => e.preventDefault()); // 입력 포커스 유지
  chip.onclick = () => {
    document.querySelectorAll(".kchip").forEach((c) => c.classList.remove("on"));
    chip.classList.add("on");
    kindHint = chip.dataset.kind || "";
    inputEl.focus();
  };
});

// ---------------------------------------------------------- 입력 한 줄 (포커스 확장·제출 후 요약 잔존)
function autosize() {
  inputEl.style.height = "44px";
  inputEl.style.height = Math.min(180, inputEl.scrollHeight + 2) + "px";
}
inputEl.addEventListener("input", autosize);
inputEl.addEventListener("focus", autosize);
inputEl.addEventListener("blur", () => { inputEl.style.height = "44px"; inputEl.scrollTop = 0; });
inputEl.addEventListener("keydown", (e) => {
  // Enter는 줄바꿈(가/나/다 소문항 입력), 제출은 작성 버튼 또는 Ctrl/Cmd+Enter
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
    e.preventDefault();
    formEl.requestSubmit();
  }
});

// ---------------------------------------------------------- 결과 요약 (수험생 언어)
function buildMeta(ev, secs) {
  const parts = [];
  if (ev.exam) parts.push(`${ev.exam.kind} ${ev.exam.points}점`);
  if (ev.library === true && Array.isArray(ev.matched)) {
    const names = ev.matched.map((id) => Drawer.name(id)).join(", ");
    parts.push(`적중 <b>${names}</b>`);
    if (secs !== null) parts.push(ev.llm_calls === 0 ? `즉시 작성 ${secs}초` : `작성+AI 보강 ${secs}초`);
  } else if (ev.library === false && ev.artifact) {
    parts.push(ev.demo ? "고정 예시 답안" : (secs !== null ? `AI 예비 작성 ${secs}초` : "AI 예비 작성"));
  }
  if (ev.review && typeof ev.review.score === "number") {
    let s = `채점 ${ev.review.score}점`;
    if (ev.review.rounds > 0) s += ` (보완 ${ev.review.rounds}회)`;
    if (ev.review.score < 85) s += " · 기준 미달";
    parts.push(s);
  }
  if (ev.review && ev.review.warnings && ev.review.warnings.length) {
    parts.push(`형식 경고 ${ev.review.warnings.length}건`);
  }
  return parts.join(" · ");
}

// ---------------------------------------------------------- 이번 답안 채우기 (iframe DOM 추출)
Stage.onReady = () => {
  const toc = Stage.outline();
  tocEl.innerHTML = "";
  toc.forEach((item) => {
    const b = document.createElement("button");
    b.type = "button";
    const n = document.createElement("span"); n.className = "n"; n.textContent = item.n;
    const pg = document.createElement("span"); pg.className = "pg"; pg.textContent = `${item.page + 1}쪽`;
    b.append(n, document.createTextNode(item.label), pg);
    b.onclick = () => Stage.gotoPage(item.page);
    tocEl.appendChild(b);
  });
  nowSec.hidden = false;

  const mn = Stage.mnemonic();
  mnBody.innerHTML = "";
  function mnLine(word, exp, title, page) {
    const w = document.createElement("span");
    w.className = "mn-word";
    w.textContent = word || "두문자";
    w.title = title;
    if (page !== null) w.onclick = () => Stage.gotoPage(page);
    const e = document.createElement("p");
    e.className = "mn-exp";
    e.textContent = exp;
    mnBody.append(w, e);
  }
  if (mn && mn.lines.length) {
    mn.lines.forEach((l) => {
      if (l.word || l.exp) mnLine(l.word, l.exp, "마지막 쪽 두문자 박스로 이동", mn.page);
    });
    mnCard.hidden = false;
  } else if (lastMeta && lastMeta.sheet && Array.isArray(lastMeta.sheet.mnemonic)
             && lastMeta.sheet.mnemonic.length) {
    // 만석 시트에서 두문자 박스가 생략된 경우 — reply 데이터로 카드만 렌더
    lastMeta.sheet.mnemonic.forEach((l) => {
      if (l.word || l.exp) mnLine(l.word, l.exp, "시트가 꽉 차 답안지에는 싣지 않은 암기 보조입니다", null);
    });
    const note = document.createElement("p");
    note.className = "mn-exp";
    note.style.opacity = ".62";
    note.textContent = "※ 시트 생략(만석) — 화면 전용";
    mnBody.append(note);
    mnCard.hidden = false;
  } else {
    mnCard.hidden = true;
  }
};

// ---------------------------------------------------------- reply 반영 (스트림·최근 재렌더 공용)
function applyReply(ev, secs) {
  lastMeta = ev;
  statusHide();
  Stage.hideOverlay();
  Stage.render(ev.artifact); // onReady 훅이 목차·두문자를 채운다
  Stage.gauge(ev.sheet);
  sumLine.innerHTML = buildMeta(ev, secs);
}

// ---------------------------------------------------------- 제출 + 스트림
let t0 = 0;
let busy = false;

function handleEvent(ev) {
  if (ev.type === "agent" || ev.type === "talk") {
    Progress.onEvent(ev);
    if (ev.type === "talk") Stage.setOverlayCaption(ev.text);
    if (!statusEl.hidden && !Progress.hasError()) statusShow("작성 중 — " + Progress.caption());
    return;
  }
  if (ev.type !== "reply") return;
  const secs = ((performance.now() - t0) / 1000).toFixed(1);

  if (ev.artifact && ev.artifact.html) {
    applyReply(ev, secs);
    recentSave(ev);
    chatAddSheetCard(ev);
  } else {
    // 질문 답변·미적중 안내 → 질문 패널
    lastMeta = ev;
    Stage.hideOverlay();
    if (ev.library === false && !ev.artifact) {
      statusShow("미등록 토픽 — 왼쪽 서랍에서 등록 토픽을 확인하세요");
    } else {
      statusHide();
    }
    chatAdd("bot", ev.reply || "");
    openChat();
  }
  history.push({ role: "assistant", content: ev.reply || "" });
  if (history.length > 30) history.splice(0, history.length - 30);
}

async function send(text) {
  if (!text || busy) return;
  busy = true;
  askBtn.disabled = true;
  Progress.reset();
  statusHide();
  chatAdd("user", text);
  Stage.showOverlay("접수 중…");
  slowTimer = setTimeout(() => {
    if (busy) statusShow("작성 중 — " + (Progress.caption() || "접수"));
  }, 1500);
  t0 = performance.now();

  try {
    const body = { message: text, history: history.slice() };
    if (kindHint) body.kind = kindHint;
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    history.push({ role: "user", content: text });

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 1);
        if (line) handleEvent(JSON.parse(line));
      }
    }
    if (buf.trim()) handleEvent(JSON.parse(buf.trim()));
  } catch (err) {
    Stage.hideOverlay();
    statusShow("⚠ 서버 오류 — 다시 시도해 주세요 (" + err.message + ")", true);
    chatAdd("bot", "서버 오류가 발생했어요: " + err.message);
  } finally {
    busy = false;
    askBtn.disabled = false;
    clearTimeout(slowTimer);
  }
}

formEl.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = inputEl.value.trim();
  if (!text) return;
  // 제출 후에도 입력 요약 잔존 (한 줄로 접힘) — 문항 수정·재작성 용이
  inputEl.blur();
  send(text);
});

// 질문 패널 자체 입력창 — 같은 파이프라인 (질문/문제 겸용)
miniForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = miniInput.value.trim();
  if (!text) return;
  miniInput.value = "";
  send(text);
});

// ---------------------------------------------------------- 예시 버튼 · 단축키 · 전역
document.querySelectorAll(".ex-btn").forEach((b) => {
  b.onclick = () => window.App.ask(b.textContent);
});

window.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement !== inputEl &&
      !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) {
    e.preventDefault();
    inputEl.focus();
  }
  if (e.key === "Escape") closeChat();
});

window.App = {
  ask(text) {
    inputEl.value = text;
    send(text);
  },
};
