/* 채팅 UI — /api/chat NDJSON 스트림에서 에이전트 상태 + 최종 답변 수신.
   (서버리스 환경 대응: 이벤트 채널과 대화 이력을 모두 클라이언트가 관리) */

const messagesEl = document.getElementById("messages");
const formEl = document.getElementById("chat-form");
const inputEl = document.getElementById("chat-input");
const sendBtn = formEl.querySelector("button");
const badgeEl = document.getElementById("mode-badge");
const logEl = document.getElementById("activity-log");

const history = []; // [{role, content}] — 클라이언트가 유지
let agentNames = {};

function addMessage(role, text, meta) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  if (meta) {
    const m = document.createElement("span");
    m.className = "meta";
    m.textContent = meta;
    div.appendChild(m);
  }
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

function addLog(agentId, state, activity) {
  const name = agentNames[agentId] || agentId;
  const line = document.createElement("div");
  const stateKo = Office.STATE_KO[state] || state;
  line.innerHTML = `<b>${name}</b> · ${stateKo}${activity ? " — " + activity : ""}`;
  logEl.appendChild(line);
  while (logEl.children.length > 60) logEl.removeChild(logEl.firstChild);
  logEl.scrollTop = logEl.scrollHeight;
}

// 에이전트 명단 + 모드 로드
fetch("/api/agents")
  .then((r) => r.json())
  .then((data) => {
    Office.setAgents(data.agents);
    for (const a of data.agents) agentNames[a.id] = `${a.name}(${a.role})`;
    if (data.demo) {
      badgeEl.textContent = "데모 모드 (LLM 키 없음)";
      badgeEl.className = "badge demo";
    } else {
      badgeEl.textContent = `LIVE · ${data.provider} · ${data.model}`;
      badgeEl.className = "badge live";
    }
  })
  .catch(() => { badgeEl.textContent = "서버 연결 실패"; });

function handleEvent(ev, ui) {
  if (ev.type === "agent") {
    Office.setState(ev.agent, ev.state, ev.activity);
    addLog(ev.agent, ev.state, ev.activity);
  } else if (ev.type === "reply") {
    ui.typing.remove();
    let meta = "";
    if (ev.nlu) meta = `의도: ${ev.nlu.intent} · 감정: ${ev.nlu.sentiment}`;
    if (ev.review) meta += (meta ? " · " : "") + `검수: ${ev.review.approved ? "승인" : "수정됨"}`;
    addMessage("bot", ev.reply, meta || null);
    history.push({ role: "assistant", content: ev.reply });
    if (history.length > 30) history.splice(0, history.length - 30);
  }
}

formEl.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = inputEl.value.trim();
  if (!text) return;
  inputEl.value = "";
  sendBtn.disabled = true;
  addMessage("user", text);
  const ui = { typing: addMessage("bot typing", "에이전트 팀이 작업 중이에요…") };

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, history: history.slice() }),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    history.push({ role: "user", content: text });

    // NDJSON 스트림 파싱
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
        if (line) handleEvent(JSON.parse(line), ui);
      }
    }
    if (buf.trim()) handleEvent(JSON.parse(buf.trim()), ui);
  } catch (err) {
    ui.typing.remove();
    addMessage("bot", "서버 오류가 발생했어요: " + err.message);
  } finally {
    sendBtn.disabled = false;
    inputEl.focus();
  }
});

addMessage(
  "bot",
  "안녕하세요! 왼쪽 픽셀 오피스에서 다섯 에이전트가 일하는 모습을 보면서 대화해 보세요. 🙌"
);
