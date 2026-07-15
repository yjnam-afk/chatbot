/* 픽셀 오피스 렌더러 — Star-Office-UI에서 영감을 받아 캔버스로 직접 그린다.
   외부 에셋 없이 fillRect 픽셀아트로 캐릭터/사무실을 렌더링한다. */

const Office = (() => {
  const canvas = document.getElementById("office");
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  const PX = 3; // 캐릭터 픽셀 크기

  let fontOK = false;
  if (document.fonts) {
    document.fonts.load('10px "Galmuri9"').then(() => { fontOK = true; });
  }
  const F9 = () => (fontOK ? '10px "Galmuri9"' : "10px sans-serif");

  // ------------------------------------------------------------ 스프라이트
  // o외곽선 H머리 S피부 e눈 B상의 b상의음영 P하의 E신발
  const HEAD = [
    "...oooooo...",
    "..oHHHHHHo..",
    ".oHHHHHHHHo.",
    ".oHHHHHHHHo.",
    ".oHSSSSSSHo.",
    ".oHSeSSeSHo.",
    ".oSSSSSSSSo.",
    "..oSSSSSSo..",
    "...oSSSSo...",
  ];
  const TORSO = [
    "..oBBBBBBo..",
    ".oBbBBBBbBo.",
    ".oBbBBBBbBo.",
    ".oSoBBBBoSo.",
    "..oBBBBBBo..",
  ];
  const LEGS = {
    stand: [
      "...oPPPPo...",
      "...oPPPPo...",
      "...oP..Po...",
      "..oEE..EEo..",
    ],
    walkA: [
      "...oPPPPo...",
      "..oPP..PPo..",
      "..oP....Po..",
      ".oEE....EEo.",
    ],
    walkB: [
      "...oPPPPo...",
      "...oPPPPo...",
      "....oPPo....",
      "...oEEEEo...",
    ],
  };
  const SPRITE_W = 12 * PX;
  const SPRITE_H = (HEAD.length + TORSO.length + 4) * PX;

  const HAIR = {
    orchestrator: "#8a5a2c",
    nlu: "#2e3f66",
    designer: "#53306e",
    writer: "#2f4a33",
    reviewer: "#8a3d55",
  };

  // ------------------------------------------------------------ 배치
  const DESKS = {
    nlu:          { x: 130, y: 128 },
    orchestrator: { x: 350, y: 128 },
    designer:     { x: 570, y: 128 },
    reviewer:     { x: 350, y: 268 },
    writer:       { x: 570, y: 268 },
  };
  const LOUNGE = [
    { x: 96,  y: 402 }, { x: 152, y: 420 }, { x: 208, y: 402 },
    { x: 264, y: 420 }, { x: 318, y: 402 },
  ];

  const BUBBLE = { thinking: "💭", working: "⚙️", done: "✅", error: "❌" };
  const STATE_KO = { idle: "휴식", thinking: "생각 중", working: "작업 중", done: "완료", error: "오류" };

  let agents = [];

  function setAgents(list) {
    agents = list.map((a, i) => ({
      ...a,
      state: "idle",
      activity: "",
      say: null, // {text, until}
      loungeSpot: LOUNGE[i % LOUNGE.length],
      x: LOUNGE[i % LOUNGE.length].x,
      y: LOUNGE[i % LOUNGE.length].y,
      facing: 1,
      moving: false,
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
      a.revertTimer = setTimeout(() => { a.state = "idle"; a.activity = ""; },
        state === "error" ? 9000 : 6000);
    }
  }

  function say(id, text) {
    const a = agents.find((a) => a.id === id);
    if (!a) return;
    a.say = { text, until: performance.now() + 6000 };
  }

  function targetOf(a) {
    if (a.state === "idle") return a.loungeSpot;
    const desk = DESKS[a.id];
    return desk ? { x: desk.x, y: desk.y - 8 } : a.loungeSpot;
  }

  // ------------------------------------------------------------ 그리기 유틸
  function px(x, y, w, h, c) { ctx.fillStyle = c; ctx.fillRect(x, y, w, h); }

  function outlined(x, y, w, h, fill, line = "#241c33") {
    px(x - 2, y - 2, w + 4, h + 4, line);
    px(x, y, w, h, fill);
  }

  function wrapText(text, maxChars) {
    const out = [];
    let cur = "";
    for (const word of String(text).split(" ")) {
      if ((cur + " " + word).trim().length > maxChars) {
        if (cur) out.push(cur.trim());
        cur = word;
        while (cur.length > maxChars) { out.push(cur.slice(0, maxChars)); cur = cur.slice(maxChars); }
      } else {
        cur = (cur + " " + word).trim();
      }
    }
    if (cur) out.push(cur);
    return out.slice(0, 3);
  }

  // ------------------------------------------------------------ 배경
  const STARS = [];
  for (let i = 0; i < 40; i++) {
    STARS.push({ x: (i * 97) % 720 + 20, y: (i * 53) % 36 + 10, s: (i % 3 === 0) ? 2 : 1 });
  }

  function drawWall(t) {
    px(0, 0, W, 64, "#55496b");
    px(0, 56, W, 8, "#3f3457");
    px(0, 54, W, 2, "#6d5f85");
    const winX = [80, 240, 400, 560];
    for (let i = 0; i < winX.length; i++) {
      const x = winX[i];
      px(x - 3, 5, 62, 46, "#2b2545");
      px(x, 8, 56, 40, "#16223f");
      for (const s of STARS) {
        if (s.x >= x + 2 && s.x < x + 52) {
          if (Math.floor(t / 700 + s.x) % 5 !== 0) px(s.x, s.y, s.s, s.s, "#cfe0ff");
        }
      }
      if (i === 2) { // 달
        px(x + 36, 14, 12, 12, "#f5e9c8");
        px(x + 40, 16, 4, 3, "#e2d3a8");
        px(x + 38, 22, 3, 2, "#e2d3a8");
      }
      px(x, 26, 56, 2, "#2b2545");
      px(x + 27, 8, 2, 40, "#2b2545");
      px(x - 4, 50, 64, 4, "#6d5f85");
    }
    // 벽시계
    const cx = 692, cy = 28;
    ctx.fillStyle = "#241c33"; ctx.beginPath(); ctx.arc(cx, cy, 15, 0, 7); ctx.fill();
    ctx.fillStyle = "#efe7d8"; ctx.beginPath(); ctx.arc(cx, cy, 12, 0, 7); ctx.fill();
    const now = new Date();
    const ma = (now.getMinutes() / 60) * Math.PI * 2 - Math.PI / 2;
    const ha = ((now.getHours() % 12) / 12 + now.getMinutes() / 720) * Math.PI * 2 - Math.PI / 2;
    ctx.strokeStyle = "#241c33"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(ha) * 6, cy + Math.sin(ha) * 6); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(ma) * 9, cy + Math.sin(ma) * 9); ctx.stroke();
  }

  function drawFloor() {
    for (let y = 64; y < H; y += 16) {
      const row = (y - 64) / 16;
      px(0, y, W, 16, row % 2 ? "#8a6647" : "#936e4e");
      px(0, y, W, 1, "#74533a");
      const off = (row % 2) * 60;
      for (let x = off; x < W; x += 120) px(x, y, 2, 16, "#74533a");
    }
  }

  function drawLounge() {
    outlined(52, 366, 320, 86, "#2e6f6b", "#245450");
    px(60, 374, 304, 70, "#38857f");
    px(60, 374, 304, 4, "#57a89f");
    px(60, 440, 304, 4, "#245450");
    // 소파
    outlined(64, 346, 128, 26, "#b85c42");
    px(64, 338, 128, 12, "#d0714f");
    px(66, 356, 60, 12, "#c96a4a");
    px(128, 356, 60, 12, "#c96a4a");
    px(56, 344, 10, 26, "#a04f38");
    px(190, 344, 10, 26, "#a04f38");
    // 커피 테이블
    outlined(232, 428, 64, 14, "#8a5f36");
    px(240, 420, 10, 8, "#efe7d8");
    px(278, 420, 10, 8, "#f2b544");
    // 화분
    px(380, 396, 22, 18, "#7a4a2c");
    px(376, 372, 30, 26, "#3f8f4f");
    px(384, 362, 16, 16, "#57b56a");
    ctx.fillStyle = "#d8f0e8"; ctx.font = F9(); ctx.textAlign = "left";
    ctx.fillText("휴게실", 62, 464);
  }

  function drawDeco() {
    // 자판기
    outlined(688, 348, 52, 92, "#c94f4f");
    px(694, 356, 28, 52, "#241c33");
    for (let r = 0; r < 3; r++)
      for (let c = 0; c < 3; c++)
        px(698 + c * 9, 362 + r * 16, 6, 10, ["#f2b544", "#5bc8f5", "#8de3a1"][(r + c) % 3]);
    px(726, 360, 8, 22, "#e8e0d0");
    px(694, 414, 28, 16, "#3a2e2e");
    // 정수기
    outlined(444, 396, 26, 44, "#e8e0d0");
    px(448, 382, 18, 18, "#7fc4ea");
    px(452, 386, 6, 8, "#b9e2f5");
    px(450, 414, 6, 6, "#5bc8f5");
    // 오른쪽 화분
    px(646, 176, 20, 16, "#7a4a2c");
    px(642, 154, 28, 24, "#3f8f4f");
    px(650, 146, 12, 12, "#57b56a");
  }

  function drawDesk(x, y, a, busy, t) {
    outlined(x - 48, y + 6, 96, 12, "#b5824f");
    px(x - 48, y + 18, 96, 14, "#8a5f36");
    px(x - 46, y + 32, 6, 8, "#6e4a28");
    px(x + 40, y + 32, 6, 8, "#6e4a28");
    // 모니터
    outlined(x - 20, y - 18, 40, 26, "#3a3352");
    if (busy) {
      px(x - 17, y - 15, 34, 20, "#0e1c33");
      const cols = ["#ffd166", "#8de3a1", "#f28ba8", "#5bc8f5"];
      for (let i = 0; i < 4; i++) {
        const wLine = 8 + ((t / 160 + i * 3) % 20);
        px(x - 14, y - 12 + i * 4, wLine, 2, cols[i % 4]);
      }
    } else {
      px(x - 17, y - 15, 34, 20, "#151226");
    }
    px(x - 4, y + 8, 8, 3, "#3a3352");
    // 키보드 + 마우스 + 머그컵
    px(x - 18, y + 9, 26, 6, "#2e2947");
    px(x - 16, y + 10, 22, 1, "#4a4370");
    px(x + 14, y + 10, 6, 5, "#2e2947");
    px(x + 30, y + 6, 9, 9, a.color);
    px(x + 39, y + 8, 3, 4, a.color);
    // 명패
    px(x - 48, y + 42, 8, 8, a.color);
    ctx.fillStyle = "#f5eeda"; ctx.font = F9(); ctx.textAlign = "left";
    ctx.fillText(`${a.name} · ${a.role}`, x - 36, y + 50);
  }

  // ------------------------------------------------------------ 캐릭터
  function drawSpriteRows(rows, ox, oy, palette, flip) {
    for (let r = 0; r < rows.length; r++) {
      const line = rows[r];
      for (let c = 0; c < line.length; c++) {
        const ch = flip ? line[line.length - 1 - c] : line[c];
        if (ch === ".") continue;
        ctx.fillStyle = palette[ch];
        ctx.fillRect(ox + c * PX, oy, PX, PX);
      }
      oy += PX;
    }
    return oy;
  }

  function shade(hex) {
    const n = parseInt(hex.slice(1), 16);
    const r = Math.max(0, (n >> 16) - 45), g = Math.max(0, ((n >> 8) & 255) - 45), b = Math.max(0, (n & 255) - 45);
    return `rgb(${r},${g},${b})`;
  }

  function drawAgent(a, t) {
    const bob = a.moving ? 0 : Math.sin(t / 480 + a.x) * 1.2;
    const ox = a.x - SPRITE_W / 2;
    const oy = a.y - SPRITE_H + bob;

    // 그림자
    ctx.globalAlpha = 0.22;
    ctx.fillStyle = "#000";
    ctx.beginPath();
    ctx.ellipse(a.x, a.y + 2, 15, 5, 0, 0, 7);
    ctx.fill();
    ctx.globalAlpha = 1;

    const palette = {
      o: "#241c33",
      H: HAIR[a.id] || "#3b2b20",
      S: "#f6cfa4",
      e: "#241c33",
      B: a.color,
      b: shade(a.color),
      P: "#3a4066",
      E: "#241c33",
    };
    const legs = a.moving
      ? (Math.floor(t / 140) % 2 ? LEGS.walkA : LEGS.walkB)
      : LEGS.stand;
    const flip = a.facing < 0;
    let yy = drawSpriteRows(HEAD, ox, oy, palette, flip);
    yy = drawSpriteRows(TORSO, ox, yy, palette, flip);
    drawSpriteRows(legs, ox, yy, palette, flip);

    // 이름표
    ctx.font = F9(); ctx.textAlign = "center";
    ctx.fillStyle = "#241c33";
    ctx.fillText(a.name, a.x + 1, a.y + 15);
    ctx.fillStyle = "#fff2d8";
    ctx.fillText(a.name, a.x, a.y + 14);

    if (a.state === "idle" && !a.say && !a.moving && Math.floor(t / 1000) % 3 === 0) {
      ctx.font = F9(); ctx.fillStyle = "#9fb4e8";
      ctx.fillText("Zzz", a.x + 18, oy - 2);
    }
  }

  // 말풍선은 항상 최상단에 그린다 (책상/다른 캐릭터에 가리지 않도록)
  function drawBubbles(t) {
    for (const a of agents) {
      // 대사 말풍선 우선
      if (a.say && performance.now() < a.say.until) {
        const lines = wrapText(a.say.text, 14);
        ctx.font = F9();
        let wMax = 0;
        for (const l of lines) wMax = Math.max(wMax, ctx.measureText(l).width);
        const bw = Math.min(200, wMax + 16), bh = lines.length * 13 + 10;
        let bx = a.x - bw / 2, by = a.y - SPRITE_H - bh - 10;
        bx = Math.max(6, Math.min(W - bw - 6, bx));
        by = Math.max(6, by);
        outlined(bx, by, bw, bh, "#fdf6e8", "#241c33");
        px(a.x - 4, by + bh + 2, 8, 3, "#241c33");
        px(a.x - 3, by + bh, 6, 3, "#fdf6e8");
        ctx.fillStyle = "#241c33"; ctx.textAlign = "left";
        lines.forEach((l, i) => ctx.fillText(l, bx + 8, by + 16 + i * 13));
        continue;
      }
      if (a.say && performance.now() >= a.say.until) a.say = null;
      // 상태 이모지 말풍선
      if (a.state !== "idle") {
        const emoji = BUBBLE[a.state] || "";
        const bx = a.x + 8, by = a.y - SPRITE_H - 28;
        outlined(bx, by, 26, 20, "#fdf6e8", "#241c33");
        px(bx + 2, by + 20, 6, 3, "#241c33");
        px(bx + 3, by + 19, 4, 3, "#fdf6e8");
        ctx.font = "13px sans-serif"; ctx.textAlign = "center";
        ctx.fillStyle = "#241c33";
        ctx.fillText(emoji, bx + 13, by + 15);
      }
    }
  }

  // ------------------------------------------------------------ 루프
  function tick(t) {
    ctx.clearRect(0, 0, W, H);
    drawFloor();
    drawWall(t);
    drawLounge();
    drawDeco();

    for (const a of agents) {
      const target = targetOf(a);
      const dx = target.x - a.x, dy = target.y - a.y;
      const dist = Math.hypot(dx, dy);
      a.moving = dist > 2;
      if (a.moving) {
        const speed = 2.4;
        a.x += (dx / dist) * Math.min(speed, dist);
        a.y += (dy / dist) * Math.min(speed, dist);
        if (dx > 0.5) a.facing = 1;
        else if (dx < -0.5) a.facing = -1;
      }
    }

    const working = agents.filter((a) => a.state !== "idle");
    const resting = agents.filter((a) => a.state === "idle");

    for (const a of working) drawAgent(a, t);
    for (const a of agents) {
      const desk = DESKS[a.id];
      if (desk) drawDesk(desk.x, desk.y, a, a.state === "working" || a.state === "thinking", t);
    }
    for (const a of resting) drawAgent(a, t);
    drawBubbles(t);

    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);

  return { setAgents, setState, say, STATE_KO };
})();
