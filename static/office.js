/* 픽셀 오피스 렌더러 — Star-Office-UI에서 영감을 받아 캔버스로 직접 그린다.
   외부 에셋 없이 fillRect 픽셀아트로 캐릭터/사무실을 렌더링한다. */

const Office = (() => {
  const canvas = document.getElementById("office");
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  const PX = 3; // 캐릭터 픽셀 크기

  // 캐릭터 픽셀맵: H머리 S피부 B상의 P하의 E신발
  const SPRITE = [
    "...HHHH....",
    "..HHHHHH...",
    "..HSSSSH...",
    "..SSSSSS...",
    "...SSSS....",
    "..BBBBBB...",
    ".BBBBBBBB..",
    ".B.BBBB.B..",
    ".S.BBBB.S..",
    "...PPPP....",
    "...PPPP....",
    "...P..P....",
    "...P..P....",
    "..EE..EE...",
  ];
  const SPRITE_W = 11 * PX, SPRITE_H = SPRITE.length * PX;

  const DESKS = {
    orchestrator: { x: 350, y: 96 },
    nlu:          { x: 130, y: 96 },
    designer:     { x: 570, y: 96 },
    writer:       { x: 570, y: 250 },
    reviewer:     { x: 350, y: 250 },
  };
  const LOUNGE = [
    { x: 90,  y: 392 }, { x: 150, y: 408 }, { x: 210, y: 392 },
    { x: 270, y: 408 }, { x: 330, y: 392 },
  ];

  const BUBBLE = { idle: "💤", thinking: "💭", working: "⚙️", done: "✅", error: "❌" };
  const STATE_KO = { idle: "휴식", thinking: "생각 중", working: "작업 중", done: "완료", error: "오류" };

  let agents = []; // {id, name, role, color, x, y, state, activity, loungeSpot}

  function setAgents(list) {
    agents = list.map((a, i) => ({
      ...a,
      state: "idle",
      activity: "",
      loungeSpot: LOUNGE[i % LOUNGE.length],
      x: LOUNGE[i % LOUNGE.length].x,
      y: LOUNGE[i % LOUNGE.length].y,
      revertTimer: null,
    }));
  }

  function setState(id, state, activity) {
    const a = agents.find((a) => a.id === id);
    if (!a) return;
    a.state = state;
    a.activity = activity || "";
    if (a.revertTimer) { clearTimeout(a.revertTimer); a.revertTimer = null; }
    if (state === "done" || state === "error") {
      a.revertTimer = setTimeout(() => { a.state = "idle"; a.activity = ""; }, state === "error" ? 9000 : 4500);
    }
  }

  function targetOf(a) {
    if (a.state === "idle") return a.loungeSpot;
    const desk = DESKS[a.id];
    return desk ? { x: desk.x, y: desk.y - 6 } : a.loungeSpot;
  }

  // ---------------------------------------------------------- 배경

  function drawFloor() {
    for (let y = 0; y < H; y += 40) {
      for (let x = 0; x < W; x += 40) {
        ctx.fillStyle = ((x / 40 + y / 40) % 2 === 0) ? "#22243a" : "#1e2033";
        ctx.fillRect(x, y, 40, 40);
      }
    }
    // 벽
    ctx.fillStyle = "#2e3150";
    ctx.fillRect(0, 0, W, 34);
    ctx.fillStyle = "#3a3d61";
    ctx.fillRect(0, 30, W, 4);
    // 창문
    for (let i = 0; i < 5; i++) {
      ctx.fillStyle = "#7fb8e8";
      ctx.fillRect(60 + i * 150, 6, 46, 20);
      ctx.fillStyle = "#a8d4f5";
      ctx.fillRect(60 + i * 150, 6, 46, 8);
    }
  }

  function drawDesk(x, y, label, color, busy, t) {
    // 책상
    ctx.fillStyle = "#6b4a2f";
    ctx.fillRect(x - 42, y + 8, 84, 26);
    ctx.fillStyle = "#845c3b";
    ctx.fillRect(x - 42, y + 8, 84, 6);
    // 모니터
    ctx.fillStyle = "#15161f";
    ctx.fillRect(x - 16, y - 16, 32, 22);
    ctx.fillStyle = busy ? (Math.floor(t / 220) % 2 ? "#8fd2ff" : "#5ba8e0") : "#2a2c40";
    ctx.fillRect(x - 13, y - 13, 26, 16);
    ctx.fillStyle = "#15161f";
    ctx.fillRect(x - 3, y + 6, 6, 4);
    // 명패
    ctx.fillStyle = color;
    ctx.fillRect(x - 42, y + 34, 84, 3);
    ctx.fillStyle = "#c9cbe0";
    ctx.font = "11px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(label, x, y + 50);
  }

  function drawLounge() {
    // 러그
    ctx.fillStyle = "#33375c";
    ctx.fillRect(50, 360, 330, 84);
    ctx.fillStyle = "#3d416b";
    ctx.fillRect(58, 366, 314, 72);
    // 소파
    ctx.fillStyle = "#a8563f";
    ctx.fillRect(58, 344, 120, 22);
    ctx.fillStyle = "#c26a4f";
    ctx.fillRect(58, 340, 120, 10);
    // 화분
    ctx.fillStyle = "#3f8f4f";
    ctx.fillRect(392, 380, 18, 26);
    ctx.fillStyle = "#57b56a";
    ctx.fillRect(386, 366, 30, 20);
    ctx.fillStyle = "#7a4a2c";
    ctx.fillRect(392, 406, 18, 14);
    ctx.fillStyle = "#8b8fa8";
    ctx.font = "11px sans-serif";
    ctx.textAlign = "left";
    ctx.fillText("휴게실", 60, 456);
  }

  // ---------------------------------------------------------- 캐릭터

  function drawAgent(a, t) {
    const bob = a.moving ? Math.sin(t / 90) * 2 : Math.sin(t / 420 + a.x) * 1;
    const ox = a.x - SPRITE_W / 2;
    const oy = a.y - SPRITE_H + bob;

    const palette = {
      H: "#3b2b20",
      S: "#f0c8a0",
      B: a.color,
      P: "#39406b",
      E: "#24263c",
    };
    for (let r = 0; r < SPRITE.length; r++) {
      for (let c = 0; c < SPRITE[r].length; c++) {
        const ch = SPRITE[r][c];
        if (ch === ".") continue;
        ctx.fillStyle = palette[ch];
        ctx.fillRect(ox + c * PX, oy + r * PX, PX, PX);
      }
    }
    // 이름
    ctx.fillStyle = "#e8e9f2";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(a.name, a.x, a.y + 12);

    // 상태 말풍선
    const emoji = BUBBLE[a.state] || "";
    if (emoji && (a.state !== "idle" || Math.floor(t / 900) % 3 === 0)) {
      ctx.font = "15px sans-serif";
      ctx.fillText(emoji, a.x + 16, oy - 4);
    }
  }

  // ---------------------------------------------------------- 루프

  function tick(t) {
    ctx.clearRect(0, 0, W, H);
    drawFloor();
    drawLounge();

    // 이동 처리
    for (const a of agents) {
      const target = targetOf(a);
      const dx = target.x - a.x, dy = target.y - a.y;
      const dist = Math.hypot(dx, dy);
      a.moving = dist > 2;
      if (a.moving) {
        const speed = 2.2;
        a.x += (dx / dist) * Math.min(speed, dist);
        a.y += (dy / dist) * Math.min(speed, dist);
      }
    }

    // 근무 중인 캐릭터는 책상 뒤에 서도록 먼저 그리고, 책상을 그 위에 겹친다
    const working = agents.filter((a) => a.state !== "idle");
    const resting = agents.filter((a) => a.state === "idle");

    for (const a of working) drawAgent(a, t);
    for (const a of agents) {
      const desk = DESKS[a.id];
      if (desk) {
        drawDesk(desk.x, desk.y, `${a.name} · ${a.role}`, a.color,
          a.state === "working" || a.state === "thinking", t);
      }
    }
    for (const a of resting) drawAgent(a, t);

    // 범례
    ctx.fillStyle = "#8b8fa8";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "right";
    ctx.fillText("💤휴식  💭생각  ⚙️작업  ✅완료  ❌오류", W - 12, H - 10);

    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);

  return { setAgents, setState, STATE_KO };
})();
