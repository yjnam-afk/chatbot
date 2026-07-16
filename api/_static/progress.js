/* 진행 3단계 렌더러 — NDJSON 이벤트를 스텝 상태로 매핑 (docs/front-ui-spec.md §3).
   AGENT2STEP의 에이전트 id는 _agents.py AGENTS와 동기 유지 (CLAUDE.md 규칙).
   역할 문자열은 하드코딩하지 않는다 — 표기는 /api/agents 의 role이 단일 출처. */

const Progress = (() => {
  const AGENT2STEP = { orchestrator: 1, nlu: 1, designer: 2, writer: 2, reviewer: 3 };
  const LAST_OWNER = { 1: "nlu", 2: "writer", 3: "reviewer" };

  const els = {};
  document.querySelectorAll(".step").forEach((el) => {
    els[el.dataset.step] = {
      el,
      st: el.querySelector(".st"),
      cap: el.querySelector(".cap"),
    };
  });
  const started = {}; // step -> 시작 ts
  const rework = {};  // step -> 보완 횟수 (폴백 경로 보완 루프)

  function capParts(n) {
    const cap = els[n].cap;
    let t = cap.querySelector(".t");
    if (!t) {
      t = document.createElement("span");
      t.className = "t";
      cap.appendChild(t);
    }
    return { cap, t };
  }

  function caption(n, text) {
    if (!els[n]) return;
    const { cap, t } = capParts(n);
    const keep = t.textContent;
    cap.textContent = text + " ";
    const nt = document.createElement("span");
    nt.className = "t";
    nt.textContent = keep;
    cap.appendChild(nt);
  }

  function reset() {
    for (const n of ["1", "2", "3"]) {
      const s = els[n];
      if (!s) continue;
      s.el.className = "step";
      s.st.textContent = "대기";
      s.cap.innerHTML = '&nbsp;<span class="t"></span>';
      delete started[n];
      delete rework[n];
    }
  }

  function run(n, activity) {
    const s = els[n];
    if (!s) return;
    // 앞 스텝이 아직 진행 중이면 완료 처리 (done 이벤트 유실 대비)
    for (let i = 1; i < Number(n); i++) {
      if (els[i] && els[i].el.classList.contains("run")) finish(String(i));
    }
    if (s.el.classList.contains("done")) {
      // 이미 완료된 스텝의 재작업 = 보완 루프
      rework[n] = (rework[n] || 0) + 1;
      s.el.classList.remove("done");
      caption(n, `보완 ${rework[n]}회 진행 중`);
    }
    if (!s.el.classList.contains("run")) {
      s.el.classList.add("run");
      if (!started[n]) started[n] = performance.now();
    }
    s.el.classList.remove("error");
    s.st.textContent = activity && activity.length <= 14 ? activity : "진행 중";
  }

  function finish(n) {
    const s = els[n];
    if (!s || s.el.classList.contains("done")) return;
    s.el.classList.remove("run", "error");
    s.el.classList.add("done");
    s.st.textContent = rework[n] ? `완료 · 보완 ${rework[n]}회` : "완료";
    if (started[n]) {
      const { t } = capParts(n);
      t.textContent = ((performance.now() - started[n]) / 1000).toFixed(1) + "초";
    }
  }

  function error(n, activity) {
    const s = els[n];
    if (!s) return;
    s.el.classList.remove("run", "done");
    s.el.classList.add("error");
    s.st.textContent = "오류";
    if (activity) caption(n, activity);
  }

  function finishAll() {
    for (const n of ["1", "2", "3"]) finish(n);
  }

  function failActive() {
    for (const n of ["1", "2", "3"]) {
      if (els[n] && els[n].el.classList.contains("run")) error(n, "중단됨");
    }
  }

  function onEvent(ev) {
    const n = String(AGENT2STEP[ev.agent] || "");
    if (!n) return;
    if (ev.type === "talk") {
      caption(n, ev.text);
      return;
    }
    if (ev.state === "thinking" || ev.state === "working") run(n, ev.activity);
    else if (ev.state === "done" && ev.agent === LAST_OWNER[n]) finish(n);
    else if (ev.state === "error") error(n, ev.activity);
  }

  return { reset, onEvent, finishAll, failActive };
})();
