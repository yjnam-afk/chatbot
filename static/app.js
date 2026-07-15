/* 채팅 UI + SSE 연결 */

const messagesEl = document.getElementById("messages");
const formEl = document.getElementById("chat-form");
const inputEl = document.getElementById("chat-input");
const sendBtn = formEl.querySelector("button");
const badgeEl = document.getElementById("mode-badge");
const logEl = document.getElementById("activity-log");

const sessionId = "s-" + Math.random().toString(36).slice(2, 10);
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

// 에이전트 명단 로드
fetch("/api/agents")
  .then((r) => r.json())
  .then((data) => {
    Office.setAgents(data.agents);
    for (const a of data.agents) agentNames[a.id] = `${a.name}(${a.role})`;
    if (data.demo) {
      badgeEl.textContent = "데모 모드 (API 키 없음)";
      badgeEl.className = "badge demo";
    } else {
      badgeEl.textContent = `LIVE · ${data.model}`;
      badgeEl.className = "badge live";
    }
  });

// SSE로 에이전트 상태 수신
const es = new EventSource("/api/events");
es.onmessage = (e) => {
  const ev = JSON.parse(e.data);
  if (ev.type === "agent") {
    Office.setState(ev.agent, ev.state, ev.activity);
    addLog(ev.agent, ev.state, ev.activity);
  }
};

// 채팅 전송
formEl.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = inputEl.value.trim();
  if (!text) return;
  inputEl.value = "";
  sendBtn.disabled = true;
  addMessage("user", text);
  const typing = addMessage("bot typing", "에이전트 팀이 작업 중이에요…");

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message: text }),
    });
    const data = await res.json();
    typing.remove();
    let meta = "";
    if (data.nlu) meta = `의도: ${data.nlu.intent} · 감정: ${data.nlu.sentiment}`;
    if (data.review) meta += (meta ? " · " : "") + `검수: ${data.review.approved ? "승인" : "수정됨"}`;
    addMessage("bot", data.reply, meta || null);
  } catch (err) {
    typing.remove();
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
