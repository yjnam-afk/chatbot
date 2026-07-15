/* 채팅 UI — /api/chat NDJSON 스트림에서 에이전트 상태/대화 + 최종 결과(작업물) 수신.
   (서버리스 대응: 이벤트 채널과 대화 이력을 모두 클라이언트가 관리) */

const messagesEl = document.getElementById("messages");
const formEl = document.getElementById("chat-form");
const inputEl = document.getElementById("chat-input");
const sendBtn = formEl.querySelector("button");
const badgeEl = document.getElementById("mode-badge");
const logEl = document.getElementById("activity-log");

const pmModal = document.getElementById("preview-modal");
const pmTitle = document.getElementById("pm-title");
const pmFrame = document.getElementById("pm-frame");

const history = []; // [{role, content}] — 클라이언트가 유지
let agentNames = {};
let agentColors = {};

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

function pushLog(line) {
  logEl.appendChild(line);
  while (logEl.children.length > 80) logEl.removeChild(logEl.firstChild);
  logEl.scrollTop = logEl.scrollHeight;
}

function addLog(agentId, state, activity) {
  const line = document.createElement("div");
  line.className = "log-status";
  const stateKo = Office.STATE_KO[state] || state;
  line.innerHTML = `<b style="color:${agentColors[agentId] || "#8a745c"}">${agentNames[agentId] || agentId}</b> · ${stateKo}${activity ? " — " + activity : ""}`;
  pushLog(line);
}

function addTalk(agentId, text) {
  const line = document.createElement("div");
  line.className = "log-talk";
  line.innerHTML = `<b style="color:${agentColors[agentId] || "#5a4632"}">${agentNames[agentId] || agentId}</b> 💬 <span></span>`;
  line.querySelector("span").textContent = text;
  pushLog(line);
}

// ---------------------------------------------------------- 작업물(artifact)
function artifactBlobUrl(art) {
  return URL.createObjectURL(new Blob([art.html], { type: "text/html" }));
}

function downloadArtifact(art) {
  const a = document.createElement("a");
  a.href = artifactBlobUrl(art);
  a.download = (art.title || "작업물").replace(/[\\/:*?"<>|]/g, "_") + ".html";
  a.click();
}

function openPreview(art) {
  pmTitle.textContent = "🎁 " + (art.title || "미리보기");
  pmFrame.srcdoc = art.html;
  pmModal.hidden = false;
  document.getElementById("pm-open").onclick = () => window.open(artifactBlobUrl(art));
  document.getElementById("pm-down").onclick = () => downloadArtifact(art);
}

document.getElementById("pm-close").onclick = () => {
  pmModal.hidden = true;
  pmFrame.srcdoc = "";
};
pmModal.addEventListener("click", (e) => {
  if (e.target === pmModal) document.getElementById("pm-close").onclick();
});

function addArtifactCard(art) {
  const div = document.createElement("div");
  div.className = "msg bot artifact";
  const title = document.createElement("div");
  title.className = "art-title";
  title.textContent = "🎁 " + (art.title || "작업물") + " 완성!";
  const actions = document.createElement("div");
  actions.className = "art-actions";
  const bPrev = document.createElement("button");
  bPrev.className = "art-btn";
  bPrev.textContent = "▶ 미리보기";
  bPrev.onclick = () => openPreview(art);
  const bOpen = document.createElement("button");
  bOpen.className = "art-btn secondary";
  bOpen.textContent = "새 탭";
  bOpen.onclick = () => window.open(artifactBlobUrl(art));
  const bDown = document.createElement("button");
  bDown.className = "art-btn secondary";
  bDown.textContent = "다운로드";
  bDown.onclick = () => downloadArtifact(art);
  actions.append(bPrev, bOpen, bDown);
  div.append(title, actions);
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  // 완성되면 자동으로 미리보기를 열어준다
  openPreview(art);
}

// ---------------------------------------------------------- 초기화
fetch("/api/agents")
  .then((r) => r.json())
  .then((data) => {
    Office.setAgents(data.agents);
    for (const a of data.agents) {
      agentNames[a.id] = a.name;
      agentColors[a.id] = a.color;
    }
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
  } else if (ev.type === "talk") {
    Office.say(ev.agent, ev.text);
    addTalk(ev.agent, ev.text);
  } else if (ev.type === "reply") {
    ui.typing.remove();
    let meta = "";
    if (ev.plan && ev.plan.type === "build") meta = `기획: ${ev.plan.title || "-"}`;
    if (ev.review) meta += (meta ? " · " : "") + `QA: ${ev.review.approved ? "통과" : "이슈 있음"}`;
    addMessage("bot", ev.reply, meta || null);
    if (ev.artifact && ev.artifact.html) addArtifactCard(ev.artifact);
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
  const ui = { typing: addMessage("bot typing", "메이커 팀이 작업 중이에요") };

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, history: history.slice() }),
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
  "🍃 픽셀 사무소에 어서 오세요! 저희는 의뢰를 받으면 진짜 동작하는 웹앱을 만들어드리는 5인 메이커 팀이에요.\n\n" +
    "🐶 코디 — 팀장. 일을 나눠주고 마무리해요\n" +
    "🐱 누리 — 기획자. 의뢰를 요구사항으로 정리해요\n" +
    "🐰 다인 — 디자이너. 화면과 스타일을 설계해요\n" +
    "🐸 로운 — 개발자. 실제 코드를 짜요\n" +
    "🐻 세아 — QA. 테스트하고 승인해요\n\n" +
    '예시: "테트리스 게임 만들어줘", "뽀모도로 타이머 만들어줘", "고양이 카페 랜딩페이지 만들어줘"\n' +
    "일반 질문도 얼마든지 환영이에요!"
);
