/* 글루 — 문항 제출, NDJSON 스트림 파싱 → Progress/Stage/답변 카드, 결과 메타 라인.
   (서버리스 대응: 이벤트 채널과 대화 이력을 모두 클라이언트가 관리)
   대화 도크는 발주자 지시로 UI에서 제거(2026-07) — 일반 질문 답변은 무대의 답변 카드로. */

const formEl = document.getElementById("chat-form");
const inputEl = document.getElementById("chat-input");
const askBtn = document.getElementById("ask-btn");
const badgeEl = document.getElementById("mode-badge");
const resultLine = document.getElementById("result-line");

const chatCard = document.getElementById("chat-card");
const ccBody = document.getElementById("cc-body");
const stageScroll = document.getElementById("stage");

const history = []; // [{role, content}] — 클라이언트가 유지 (서버 채팅 컨텍스트용)
let kindHint = ""; // 교시형 칩 수동 선택 ("" = 자동 판별)

// ---------------------------------------------------------- 초기화
fetch("/api/agents")
  .then((r) => r.json())
  .then((data) => {
    if (data.demo) {
      badgeEl.textContent = "데모 모드 (라이브러리 적중은 실답안)";
      badgeEl.className = "badge demo";
    } else {
      badgeEl.textContent = `LIVE · ${data.provider} · ${data.model}`;
      badgeEl.className = "badge live";
    }
  })
  .catch(() => { badgeEl.textContent = "서버 연결 실패"; });

Drawer.load();

// ---------------------------------------------------------- 레일 탭
const tabProgress = document.getElementById("tab-progress");
const tabDrawer = document.getElementById("tab-drawer");
const paneProgress = document.getElementById("pane-progress");
const paneDrawer = document.getElementById("pane-drawer");

function showProgressTab() {
  tabProgress.classList.add("on");
  tabDrawer.classList.remove("on");
  paneProgress.hidden = false;
  paneDrawer.hidden = true;
}
tabProgress.onclick = showProgressTab;
tabDrawer.onclick = () => {
  tabDrawer.classList.add("on");
  tabProgress.classList.remove("on");
  paneDrawer.hidden = false;
  paneProgress.hidden = true;
};

// ---------------------------------------------------------- 교시형 칩
document.querySelectorAll(".kchip").forEach((chip) => {
  chip.onclick = () => {
    document.querySelectorAll(".kchip").forEach((c) => c.classList.remove("on"));
    chip.classList.add("on");
    kindHint = chip.dataset.kind || "";
  };
});

// ---------------------------------------------------------- 문항 입력 (textarea 3~8줄 자동 높이)
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

// ---------------------------------------------------------- 답변 카드 (일반 질문 → 무대 표시)
function showChatCard(text) {
  ccBody.textContent = text;
  chatCard.hidden = false;
  stageScroll.scrollTop = 0;
}
document.getElementById("cc-close").onclick = () => { chatCard.hidden = true; };

// ---------------------------------------------------------- 결과 메타 라인
function buildMeta(ev, secs) {
  const parts = [];
  if (ev.exam) parts.push(`${ev.exam.kind} ${ev.exam.points}점`);
  if (ev.library === true && Array.isArray(ev.matched)) {
    const names = ev.matched.map((id) => Drawer.name(id)).join(", ");
    parts.push(`라이브러리 적중 ${ev.matched.length}건 (${names})`);
  } else if (ev.library === false) {
    if (!ev.artifact) parts.push("라이브러리 미적중 · 안내");
    else parts.push(ev.demo ? "고정 데모 답안" : "미적중 · 라이브 작성");
  }
  if (ev.review && typeof ev.review.score === "number") {
    let s = `채점 ${ev.review.score}점`;
    if (ev.review.rounds > 0) s += ` (보완 ${ev.review.rounds}회)`;
    if (ev.review.score < 85) s += " · 기준 미달";
    parts.push(s);
  }
  if (ev.review && ev.review.warnings && ev.review.warnings.length) {
    parts.push(`검증 경고 ${ev.review.warnings.length}건`);
  }
  if (typeof ev.llm_calls === "number") parts.push(`LLM ${ev.llm_calls}콜`);
  parts.push(`${secs}초`);
  return parts.join(" · ");
}

// ---------------------------------------------------------- 제출 + 스트림
let t0 = 0;
let busy = false;

function handleEvent(ev) {
  if (ev.type === "agent" || ev.type === "talk") {
    Progress.onEvent(ev);
    if (ev.type === "talk") Stage.setOverlayCaption(ev.text);
    return;
  }
  if (ev.type !== "reply") return;
  const secs = ((performance.now() - t0) / 1000).toFixed(1);

  if (ev.artifact && ev.artifact.html) {
    chatCard.hidden = true;
    Progress.finishAll();
    Stage.hideOverlay();
    Stage.render(ev.artifact);
    Stage.gauge(ev.sheet);
    resultLine.textContent = buildMeta(ev, secs);
    showProgressTab();
  } else {
    // 일반 답변/미적중 안내 → 무대의 답변 카드
    if (ev.library === false) {
      // 라이브러리 미적중 안내: 진행 레일은 검색 단계 '미적중' 상태를 유지한다
      resultLine.textContent = buildMeta(ev, secs);
    } else {
      Progress.reset();
    }
    Stage.hideOverlay();
    showChatCard(ev.reply || "");
  }
  history.push({ role: "assistant", content: ev.reply || "" });
  if (history.length > 30) history.splice(0, history.length - 30);
}

async function send(text) {
  if (!text || busy) return;
  busy = true;
  askBtn.disabled = true;
  resultLine.textContent = "";
  Progress.reset();
  Stage.showOverlay("접수 중…");
  showProgressTab();
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
    Progress.failActive();
    Stage.hideOverlay();
    showChatCard("서버 오류가 발생했어요: " + err.message);
  } finally {
    busy = false;
    askBtn.disabled = false;
    inputEl.focus();
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
    showProgressTab();
    formEl.requestSubmit();
  },
};
