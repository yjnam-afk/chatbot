/* 글루 — 문항/질문 제출, NDJSON 스트림 파싱 → 무대·레일(답안/서랍/대화) 갱신.
   (서버리스 대응: 이벤트 채널과 대화 이력을 모두 클라이언트가 관리)
   ui-audit.md 반영: 진행 3단계 제거 → 상태 한 줄(평시 숨김), 답안 탭(목차·두문자·
   분량·인쇄), 빈 상태 기본 탭 = 토픽 서랍. 챗봇 겸용(발주자 확정 2026-07-17):
   답안지 요청은 대화에 요약 카드 + 무대 렌더, 질문은 대화에 답변. */

const formEl = document.getElementById("chat-form");
const inputEl = document.getElementById("chat-input");
const askBtn = document.getElementById("ask-btn");
const badgeEl = document.getElementById("mode-badge");
const statusEl = document.getElementById("status-line");

const sumCard = document.getElementById("sum-card");
const sumLine = document.getElementById("sum-line");
const tocEl = document.getElementById("toc");
const mnCard = document.getElementById("mn-card");
const mnBody = document.getElementById("mn-body");

const chatLog = document.getElementById("chat-log");
const miniForm = document.getElementById("chat-mini");
const miniInput = document.getElementById("chat-mini-input");

const history = []; // [{role, content}] — 클라이언트가 유지 (서버 대화 컨텍스트용)
let kindHint = ""; // 교시형 칩 수동 선택 ("" = 자동 판별)
let lastMeta = null; // 마지막 reply (목차 카드 메타 재구성용)

// ---------------------------------------------------------- 초기화 (배지: 수험생 언어 — ui-audit)
fetch("/api/agents")
  .then((r) => r.json())
  .then((data) => {
    if (data.demo) {
      badgeEl.textContent = "등록 토픽 즉시 조립";
      badgeEl.className = "badge demo";
      badgeEl.title = "등록 토픽은 즉시 조립돼요. 미등록 토픽의 AI 예비 작성은 준비 중.";
    } else {
      badgeEl.textContent = "AI 예비 작성 연결됨";
      badgeEl.className = "badge live";
      badgeEl.title = `등록 토픽 즉시 조립 + 미등록 토픽 AI 예비 작성 (${data.provider})`;
    }
  })
  .catch(() => { badgeEl.textContent = "서버 연결 실패"; });

Drawer.load();

// ---------------------------------------------------------- 레일 탭 (기본 = 토픽 서랍)
const tabs = {
  answer: [document.getElementById("tab-answer"), document.getElementById("pane-answer")],
  drawer: [document.getElementById("tab-drawer"), document.getElementById("pane-drawer")],
  chat: [document.getElementById("tab-chat"), document.getElementById("pane-chat")],
};
function showTab(name) {
  for (const [key, [btn, pane]] of Object.entries(tabs)) {
    btn.classList.toggle("on", key === name);
    pane.hidden = key !== name;
  }
  if (name === "chat") chatLog.scrollTop = chatLog.scrollHeight;
}
for (const [key, [btn]] of Object.entries(tabs)) btn.onclick = () => showTab(key);
// 하위 호환(드로어 등 외부 호출 지점)
function showProgressTab() { showTab("answer"); }

// ---------------------------------------------------------- 상태 한 줄 (ui-audit §2)
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

// ---------------------------------------------------------- 대화 로그
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
  btn.onclick = () => { showTab("answer"); Stage.gotoPage(0); };
  div.appendChild(document.createElement("br"));
  div.appendChild(btn);
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
}

// ---------------------------------------------------------- 교시형 칩
document.querySelectorAll(".kchip").forEach((chip) => {
  chip.onclick = () => {
    document.querySelectorAll(".kchip").forEach((c) => c.classList.remove("on"));
    chip.classList.add("on");
    kindHint = chip.dataset.kind || "";
  };
});

// ---------------------------------------------------------- 문항 입력 (textarea 4~8줄 자동 높이)
function autosize() {
  inputEl.style.height = "auto";
  inputEl.style.height = inputEl.scrollHeight + 3 + "px"; // 상한은 CSS max-height가 제한
}
inputEl.addEventListener("input", autosize);
inputEl.addEventListener("keydown", (e) => {
  // Enter는 줄바꿈(문항의 가/나/다 소문항 입력용), 제출은 버튼 또는 Ctrl/Cmd+Enter
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
    e.preventDefault();
    formEl.requestSubmit();
  }
});

// ---------------------------------------------------------- 결과 요약 (수험생 언어 — "LLM N콜" 금지)
function buildMeta(ev, secs) {
  const parts = [];
  if (ev.exam) parts.push(`${ev.exam.kind} ${ev.exam.points}점`);
  if (ev.library === true && Array.isArray(ev.matched)) {
    const names = ev.matched.map((id) => Drawer.name(id)).join(", ");
    parts.push(`적중 <b>${names}</b>`);
    parts.push(ev.llm_calls === 0 ? `즉시 조립 ${secs}초` : `조립+AI 보강 ${secs}초`);
  } else if (ev.library === false && ev.artifact) {
    parts.push(ev.demo ? "고정 예시 답안" : `AI 예비 작성 ${secs}초`);
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

// ---------------------------------------------------------- 답안 탭 채우기 (iframe DOM 추출)
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
  sumCard.hidden = false;

  const mn = Stage.mnemonic();
  mnBody.innerHTML = "";
  if (mn && mn.lines.length) {
    mn.lines.forEach((l) => {
      if (!l.word && !l.exp) return;
      const w = document.createElement("span");
      w.className = "mn-word";
      w.textContent = l.word || "두문자";
      w.title = "마지막 쪽 두문자 박스로 이동";
      w.onclick = () => Stage.gotoPage(mn.page);
      const e = document.createElement("p");
      e.className = "mn-exp";
      e.textContent = l.exp;
      mnBody.append(w, e);
    });
    mnCard.hidden = false;
  } else if (lastMeta && lastMeta.sheet && Array.isArray(lastMeta.sheet.mnemonic)
             && lastMeta.sheet.mnemonic.length) {
    // 만석 시트에서 두문자 박스가 생략된 경우(_pull_tail 사다리 — 인쇄 빈 쪽 금지):
    // reply.sheet.mnemonic 데이터로 레일 카드만 렌더 (세아 반려 2026-07-19)
    lastMeta.sheet.mnemonic.forEach((l) => {
      if (!l.word && !l.exp) return;
      const w = document.createElement("span");
      w.className = "mn-word";
      w.textContent = l.word || "두문자";
      w.title = "시트가 꽉 차 답안지에는 싣지 않은 암기 보조입니다";
      const e = document.createElement("p");
      e.className = "mn-exp";
      e.textContent = l.exp;
      mnBody.append(w, e);
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
  lastMeta = ev;

  if (ev.artifact && ev.artifact.html) {
    statusHide();
    Stage.hideOverlay();
    Stage.render(ev.artifact); // onReady 훅이 목차·두문자 카드를 채운다
    Stage.gauge(ev.sheet);
    sumLine.innerHTML = buildMeta(ev, secs);
    chatAddSheetCard(ev);
    showTab("answer"); // 완성 시 답안 탭 자동 전환
  } else {
    // 질문 답변·미적중 안내 → 대화 탭
    Stage.hideOverlay();
    if (ev.library === false && !ev.artifact) {
      statusShow("미등록 토픽 — 서랍에서 등록 토픽을 확인하세요");
    } else {
      statusHide();
    }
    chatAdd("bot", ev.reply || "");
    showTab("chat");
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
  // 1.5초 넘게 걸리면(폴백 LLM 경로) 상태 한 줄 노출 (ui-audit §2)
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
  inputEl.value = "";
  autosize();
  send(text);
});

// 대화 탭 자체 입력창 — 같은 파이프라인 (질문/문제 겸용)
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
});

window.App = {
  ask(text) {
    inputEl.value = text;
    autosize();
    formEl.requestSubmit();
  },
};
