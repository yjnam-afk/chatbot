/* 팀 보드 렌더러 — 에이전트 상태/대화를 모던 카드 UI로 표시한다.
   (기존 캔버스 픽셀아트를 대체. app.js가 쓰는 API는 동일: setAgents/setState/say) */

const Office = (() => {
  const board = document.getElementById("team-board");

  const EMOJI = {
    orchestrator: "🐶",
    nlu: "🐱",
    designer: "🐰",
    writer: "🐸",
    reviewer: "🐻",
  };
  const STATE_KO = { idle: "대기", thinking: "생각 중", working: "작업 중", done: "완료", error: "오류" };
  const FLOW = ["nlu", "designer", "writer", "reviewer"];

  let els = {};

  function makeCard(a, leader) {
    const card = document.createElement("div");
    card.className = "agent-card" + (leader ? " leader" : "");
    card.dataset.state = "idle";
    card.style.setProperty("--c", a.color);

    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = EMOJI[a.id] || "🙂";

    const info = document.createElement("div");
    info.className = "info";
    info.innerHTML = `<div class="name"></div><div class="role"></div>`;
    info.querySelector(".name").textContent = a.name;
    info.querySelector(".role").textContent = a.role;

    const chip = document.createElement("div");
    chip.className = "chip";
    chip.innerHTML = `<i></i><span>대기</span>`;

    const speech = document.createElement("div");
    speech.className = "speech";
    speech.textContent = "";

    const top = document.createElement("div");
    top.className = "card-top";
    top.append(avatar, info, chip);
    card.append(top, speech);

    els[a.id] = { card, chip: chip.querySelector("span"), speech };
    return card;
  }

  function setAgents(list) {
    board.innerHTML = "";
    els = {};
    const byId = Object.fromEntries(list.map((a) => [a.id, a]));

    // 팀장 카드
    if (byId.orchestrator) board.appendChild(makeCard(byId.orchestrator, true));

    // 파이프라인 진행선
    const track = document.createElement("div");
    track.className = "flow-track";
    track.innerHTML = `<div class="flow-fill" id="flow-fill"></div>`;
    board.appendChild(track);

    // 작업 흐름 카드 4장
    const flow = document.createElement("div");
    flow.className = "flow";
    FLOW.forEach((id, i) => {
      if (i > 0) {
        const arrow = document.createElement("div");
        arrow.className = "arrow";
        arrow.textContent = "→";
        flow.appendChild(arrow);
      }
      if (byId[id]) flow.appendChild(makeCard(byId[id], false));
    });
    board.appendChild(flow);
  }

  function updateProgress() {
    const fill = document.getElementById("flow-fill");
    if (!fill) return;
    let doneCount = 0;
    for (const id of FLOW) {
      const st = els[id] && els[id].card.dataset.state;
      if (st === "done") doneCount++;
    }
    const active = FLOW.some((id) => {
      const st = els[id] && els[id].card.dataset.state;
      return st === "thinking" || st === "working";
    });
    fill.style.width = `${(doneCount / FLOW.length) * 100}%`;
    fill.classList.toggle("active", active);
  }

  function setState(id, state, activity) {
    const e = els[id];
    if (!e) return;
    // 새 턴 시작: 팀장이 일을 나누면 보드 리셋
    if (id === "orchestrator" && state === "working") {
      for (const [aid, ae] of Object.entries(els)) {
        if (aid === "orchestrator") continue;
        ae.card.dataset.state = "idle";
        ae.chip.textContent = "대기";
        ae.speech.textContent = "";
        ae.speech.classList.remove("show");
      }
    }
    e.card.dataset.state = state;
    e.chip.textContent = STATE_KO[state] || state;
    if (activity && (state === "thinking" || state === "working" || state === "done")) {
      e.chip.textContent = activity.length <= 14 ? activity : STATE_KO[state];
    }
    updateProgress();
  }

  function say(id, text) {
    const e = els[id];
    if (!e) return;
    e.speech.classList.remove("show");
    void e.speech.offsetWidth; // 애니메이션 재시작
    e.speech.textContent = text;
    e.speech.classList.add("show");
  }

  return { setAgents, setState, say, STATE_KO };
})();
