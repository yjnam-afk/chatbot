/* 진행 캡션 공급자 — NDJSON 이벤트를 "현재 단계 — 활동" 한 줄 문구로 매핑.
   진행 3단계 카드 UI는 제거(ui-audit §1) — 이 모듈은 상태 한 줄(app.js)과
   무대 오버레이 캡션의 공급자로만 남는다 (다인 판정: 이벤트 매핑 존치).
   AGENT2STEP의 에이전트 id는 _agents.py AGENTS와 동기 유지 (CLAUDE.md 규칙).
   역할 문자열은 하드코딩하지 않는다 — 표기는 /api/agents 의 role이 단일 출처. */

const Progress = (() => {
  const AGENT2STEP = { orchestrator: 1, nlu: 1, designer: 2, writer: 2, reviewer: 3 };
  const STEP_NAME = { 1: "토픽 검색", 2: "조립·집필", 3: "검증" };

  let step = 0;      // 현재 진행 단계 (1~3)
  let text = "";     // 최근 활동/대사
  let errored = false;

  function reset() {
    step = 0;
    text = "";
    errored = false;
  }

  function onEvent(ev) {
    const n = AGENT2STEP[ev.agent] || 0;
    if (ev.type === "talk") {
      if (!errored && ev.text) text = ev.text;
      return;
    }
    if (ev.state === "thinking" || ev.state === "working") {
      step = Math.max(step, n);
      if (ev.activity) text = ev.activity;
    } else if (ev.state === "error") {
      errored = true;
      text = ev.activity || "오류";
    }
  }

  // 상태 한 줄용 문구: "작성 중 — {단계}: {활동}"
  function caption() {
    if (!step && !text) return "";
    const name = STEP_NAME[step] || "";
    return name ? `${name} — ${text}` : text;
  }

  function hasError() { return errored; }

  return { reset, onEvent, caption, hasError };
})();
