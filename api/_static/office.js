/* 픽셀 오피스 렌더러 — 밝고 아기자기한 오피스 디오라마.
   외부 에셋 없이 fillRect 픽셀아트로 전부 코드 렌더링한다. */

const Office = (() => {
  const canvas = document.getElementById("office");
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  const PX = 4; // 캐릭터 픽셀 크기

  let fontOK = false;
  if (document.fonts) {
    document.fonts.load('10px "Galmuri9"').then(() => { fontOK = true; });
  }
  const F9 = () => (fontOK ? '10px "Galmuri9"' : "10px sans-serif");

  // ------------------------------------------------------------ 팔레트
  const C = {
    wall: "#efe3c8",
    wallTop: "#f7eeda",
    wallShade: "#ddcba6",
    baseboard: "#cbb28a",
    floorA: "#e2c294",
    floorB: "#dab88a",
    seam: "#c39e6e",
    furn: "#5a4632",     // 가구 외곽선
    ink: "#463930",      // 어두운 텍스트
    charLine: "#332a38", // 캐릭터 외곽선
  };

  // ------------------------------------------------------------ 스프라이트
  // o외곽선 H머리 S피부 e눈 r볼터치 B상의 b상의음영 P하의 E신발
  const HEAD_SHORT = [
    "...oooooo...",
    "..oHHHHHHo..",
    ".oHHHHHHHHo.",
    ".oHHHHHHHHo.",
    ".oHSSSSSSHo.",
    ".oHSeSSeSHo.",
    ".oHSSSSSSHo.",
    ".oSrSSSSrSo.",
    "..oSSSSSSo..",
    "...oSSSSo...",
  ];
  const HEAD_LONG = [
    "...oooooo...",
    "..oHHHHHHo..",
    ".oHHHHHHHHo.",
    ".oHHHHHHHHo.",
    ".oHSSSSSSHo.",
    ".oHSeSSeSHo.",
    ".oHSSSSSSHo.",
    ".oHrSSSSrHo.",
    ".oHSSSSSSHo.",
    ".oHoSSSSoHo.",
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
  const SPRITE_H = (10 + 5 + 4) * PX;

  const LOOKS = {
    orchestrator: { hair: "#8a5a2c", long: false },
    nlu:          { hair: "#31456e", long: false },
    designer:     { hair: "#6a4088", long: true },
    writer:       { hair: "#3c5a40", long: false },
    reviewer:     { hair: "#a04e66", long: true },
  };

  // ------------------------------------------------------------ 배치
  const DESKS = {
    nlu:          { x: 115, y: 175 },
    orchestrator: { x: 350, y: 175 },
    designer:     { x: 585, y: 175 },
    reviewer:     { x: 280, y: 316 },
    writer:       { x: 520, y: 316 },
  };
  const LOUNGE = [
    { x: 225, y: 428 }, { x: 118, y: 450 }, { x: 182, y: 452 },
    { x: 262, y: 460 }, { x: 86, y: 432 },
  ];

  const BUBBLE = { thinking: "💭", working: "⚙️", done: "✅", error: "❌" };
  const STATE_KO = { idle: "휴식", thinking: "생각 중", working: "작업 중", done: "완료", error: "오류" };

  let agents = [];

  function setAgents(list) {
    agents = list.map((a, i) => ({
      ...a,
      state: "idle",
      activity: "",
      say: null,
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
    return desk ? { x: desk.x, y: desk.y } : a.loungeSpot;
  }

  // ------------------------------------------------------------ 그리기 유틸
  function px(x, y, w, h, c) { ctx.fillStyle = c; ctx.fillRect(x, y, w, h); }

  function outlined(x, y, w, h, fill, line = C.furn) {
    px(x - 2, y - 2, w + 4, h + 4, line);
    px(x, y, w, h, fill);
  }

  function shadow(x, y, w) {
    ctx.globalAlpha = 0.13;
    px(x, y, w, 6, "#3b2a18");
    ctx.globalAlpha = 1;
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
  function drawRoom(t) {
    // 벽 + 바닥
    px(0, 0, W, 96, C.wall);
    px(0, 0, W, 8, C.wallTop);
    px(0, 84, W, 12, C.baseboard);
    px(0, 82, W, 2, C.wallShade);
    for (let y = 96; y < H; y += 20) {
      const row = (y - 96) / 20;
      px(0, y, W, 20, row % 2 ? C.floorA : C.floorB);
      px(0, y, W, 1, C.seam);
      const off = (row % 2) * 70;
      for (let x = off; x < W; x += 140) px(x, y, 2, 20, C.seam);
    }

    // 창문 3개 (낮 하늘 + 구름)
    for (const wx of [70, 335, 600]) {
      px(wx - 4, 8, 98, 66, "#fbf6ea");
      px(wx - 6, 6, 102, 4, C.wallShade);
      px(wx, 12, 90, 56, "#a5d8f2");
      px(wx, 12, 90, 18, "#c2e6f8");
      // 구름
      const cx = wx + 8 + ((t / 300) % 110) - 20;
      ctx.save(); ctx.beginPath(); ctx.rect(wx, 12, 90, 56); ctx.clip();
      px(cx, 24, 26, 8, "#ffffff"); px(cx + 5, 20, 14, 6, "#ffffff");
      px(cx - 46, 42, 20, 6, "#f2fbff");
      ctx.restore();
      // 창살
      px(wx + 43, 12, 4, 56, "#fbf6ea");
      px(wx, 38, 90, 4, "#fbf6ea");
      px(wx - 4, 70, 98, 5, "#e8dcc2");
    }

    // 화이트보드
    outlined(196, 22, 108, 46, "#fdfcf7");
    px(200, 30, 56, 4, "#e06a5a");
    px(200, 40, 72, 3, "#8aa8d8");
    px(200, 48, 44, 3, "#8aa8d8");
    px(200, 56, 62, 3, "#a8cf9a");
    px(240, 70, 20, 5, "#d8cba8");

    // 액자 2개
    outlined(480, 26, 30, 24, "#fdf8ec");
    px(484, 34, 22, 12, "#a5d8f2");
    px(488, 30, 8, 8, "#f2c14e");
    outlined(524, 30, 24, 20, "#fdf8ec");
    px(528, 34, 16, 12, "#f0a8b8");

    // 벽시계
    const cx = 706, cy = 40;
    ctx.fillStyle = C.furn; ctx.beginPath(); ctx.arc(cx, cy, 17, 0, 7); ctx.fill();
    ctx.fillStyle = "#fdfcf7"; ctx.beginPath(); ctx.arc(cx, cy, 13, 0, 7); ctx.fill();
    const now = new Date();
    const ma = (now.getMinutes() / 60) * Math.PI * 2 - Math.PI / 2;
    const ha = ((now.getHours() % 12) / 12 + now.getMinutes() / 720) * Math.PI * 2 - Math.PI / 2;
    ctx.strokeStyle = C.ink; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(ha) * 6, cy + Math.sin(ha) * 6); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(ma) * 10, cy + Math.sin(ma) * 10); ctx.stroke();
  }

  function drawLounge() {
    // 러그
    outlined(44, 388, 264, 82, "#9cc4dd", "#7ba6c4");
    px(52, 396, 248, 66, "#b3d5e8");
    px(52, 396, 248, 3, "#cde6f2");
    px(52, 459, 248, 3, "#7ba6c4");
    // 소파
    shadow(54, 424, 140);
    outlined(58, 400, 132, 24, "#e8896a");
    px(58, 390, 132, 14, "#f09a78");
    px(62, 408, 60, 12, "#f0926f");
    px(126, 408, 60, 12, "#f0926f");
    px(50, 396, 10, 28, "#d97a5c");
    px(188, 396, 10, 28, "#d97a5c");
    // 커피 테이블
    shadow(216, 452, 70);
    outlined(214, 436, 68, 14, "#caa06b");
    px(222, 428, 10, 9, "#fdfcf7");
    px(258, 428, 10, 9, "#f2c14e");
    // 스탠드 조명
    px(310, 380, 4, 54, C.furn);
    px(296, 366, 32, 18, "#f6d98a");
    px(298, 368, 28, 6, "#fbe8b4");
    px(302, 434, 20, 5, C.furn);
  }

  function drawDeco() {
    // 자판기
    shadow(682, 434, 62);
    outlined(684, 352, 56, 88, "#e06a5a");
    px(690, 360, 30, 50, "#463930");
    for (let r = 0; r < 3; r++)
      for (let c = 0; c < 3; c++)
        px(694 + c * 9, 366 + r * 15, 6, 9, ["#f2c14e", "#8ec9e8", "#a8cf9a"][(r + c) % 3]);
    px(724, 364, 10, 24, "#fdf3e0");
    px(690, 416, 30, 14, "#5a4632");
    // 정수기
    shadow(628, 436, 34);
    outlined(630, 394, 30, 44, "#fdf8ec");
    px(635, 378, 20, 20, "#9ed2ef");
    px(639, 382, 7, 9, "#cdeaf8");
    px(637, 412, 7, 7, "#6db4dd");
    // 화분 (오른쪽)
    shadow(586, 436, 30);
    px(588, 414, 26, 22, "#b06a3c");
    px(584, 386, 34, 30, "#5fa86a");
    px(592, 376, 18, 18, "#7cc487");
    // 화분 (창가)
    px(320, 96, 22, 6, C.wallShade);
    px(324, 80, 14, 16, "#7cc487");
    px(326, 88, 10, 10, "#5fa86a");
  }

  function drawDesk(x, y, a, busy, t) {
    // 바닥 매트
    ctx.globalAlpha = 0.25;
    px(x - 62, y - 10, 124, 26, "#c9a06a");
    ctx.globalAlpha = 1;
    // 책상
    shadow(x - 56, y + 46, 116);
    outlined(x - 56, y + 8, 112, 14, "#f0d7ae");
    px(x - 56, y + 22, 112, 22, "#caa06b");
    px(x - 56, y + 22, 112, 3, "#b78e58");
    px(x + 26, y + 26, 24, 14, "#b78e58");   // 서랍
    px(x + 34, y + 31, 8, 3, "#8a6a42");
    px(x - 52, y + 44, 8, 8, "#8a6a42");     // 다리
    px(x + 44, y + 44, 8, 8, "#8a6a42");
    // 모니터
    if (busy) { // 화면 빛
      ctx.globalAlpha = 0.18;
      px(x - 34, y - 32, 68, 48, "#8ec9e8");
      ctx.globalAlpha = 1;
    }
    outlined(x - 26, y - 26, 52, 34, "#4a4458");
    if (busy) {
      px(x - 22, y - 22, 44, 26, "#123048");
      const cols = ["#f2c14e", "#a8cf9a", "#f0a8b8", "#8ec9e8"];
      for (let i = 0; i < 4; i++) {
        const wLine = 10 + ((t / 150 + i * 4) % 26);
        px(x - 18, y - 18 + i * 5, wLine, 3, cols[i % 4]);
      }
    } else {
      px(x - 22, y - 22, 44, 26, "#2b2836");
    }
    px(x - 5, y + 8, 10, 4, "#4a4458");
    // 키보드 + 마우스 + 머그컵 + 서류
    px(x - 24, y + 11, 30, 7, "#5c5670");
    px(x - 21, y + 13, 24, 1, "#8a84a0");
    px(x + 12, y + 12, 7, 6, "#5c5670");
    px(x - 44, y + 10, 11, 10, a.color);
    px(x - 33, y + 12, 4, 5, a.color);
    px(x + 30, y + 10, 16, 3, "#fdfcf7");
    px(x + 32, y + 7, 16, 3, "#f4efe2");
    // 명패
    outlined(x - 34, y + 52, 68, 14, "#fdf8ec");
    px(x - 30, y + 55, 8, 8, a.color);
    ctx.fillStyle = C.ink; ctx.font = F9(); ctx.textAlign = "left";
    ctx.fillText(a.name + " · " + a.role, x - 18, y + 63);
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
    const r = Math.max(0, (n >> 16) - 42), g = Math.max(0, ((n >> 8) & 255) - 42), b = Math.max(0, (n & 255) - 42);
    return `rgb(${r},${g},${b})`;
  }

  function drawAgent(a, t) {
    const bob = a.moving ? 0 : Math.sin(t / 500 + a.x) * 1.4;
    const ox = a.x - SPRITE_W / 2;
    const oy = a.y - SPRITE_H + bob;

    ctx.globalAlpha = 0.18;
    ctx.fillStyle = "#3b2a18";
    ctx.beginPath();
    ctx.ellipse(a.x, a.y + 3, 19, 6, 0, 0, 7);
    ctx.fill();
    ctx.globalAlpha = 1;

    const look = LOOKS[a.id] || { hair: "#3b2b20", long: false };
    const palette = {
      o: C.charLine,
      H: look.hair,
      S: "#f8d0a8",
      e: "#332a38",
      r: "#f2a58c",
      B: a.color,
      b: shade(a.color),
      P: "#46507a",
      E: "#332a38",
    };
    const legs = a.moving
      ? (Math.floor(t / 130) % 2 ? LEGS.walkA : LEGS.walkB)
      : LEGS.stand;
    const flip = a.facing < 0;
    let yy = drawSpriteRows(look.long ? HEAD_LONG : HEAD_SHORT, ox, oy, palette, flip);
    yy = drawSpriteRows(TORSO, ox, yy, palette, flip);
    drawSpriteRows(legs, ox, yy, palette, flip);

    // 이름표 (휴게실/이동 중에만 — 책상에서는 명패가 있음)
    if (a.state === "idle" || a.moving) {
      ctx.font = F9(); ctx.textAlign = "center";
      ctx.fillStyle = "rgba(70,57,48,0.85)";
      ctx.fillText(a.name, a.x, a.y + 18);
    }

    if (a.state === "idle" && !a.say && !a.moving && Math.floor(t / 1100) % 3 === 0) {
      ctx.font = F9(); ctx.fillStyle = "#7f96c4";
      ctx.fillText("Zzz", a.x + 24, oy - 4);
    }
  }

  // 말풍선은 항상 최상단에
  function drawBubbles() {
    for (const a of agents) {
      if (a.say && performance.now() < a.say.until) {
        const lines = wrapText(a.say.text, 15);
        ctx.font = F9();
        let wMax = 0;
        for (const l of lines) wMax = Math.max(wMax, ctx.measureText(l).width);
        const bw = Math.min(220, wMax + 18), bh = lines.length * 14 + 12;
        let bx = a.x - bw / 2, by = a.y - SPRITE_H - bh - 12;
        bx = Math.max(6, Math.min(W - bw - 6, bx));
        by = Math.max(6, by);
        outlined(bx, by, bw, bh, "#ffffff", C.charLine);
        px(a.x - 5, by + bh + 2, 10, 4, C.charLine);
        px(a.x - 4, by + bh, 8, 4, "#ffffff");
        ctx.fillStyle = C.ink; ctx.textAlign = "left";
        lines.forEach((l, i) => ctx.fillText(l, bx + 9, by + 17 + i * 14));
        continue;
      }
      if (a.say && performance.now() >= a.say.until) a.say = null;
      if (a.state !== "idle") {
        const emoji = BUBBLE[a.state] || "";
        const bx = a.x + 12, by = a.y - SPRITE_H - 30;
        outlined(bx, by, 28, 22, "#ffffff", C.charLine);
        px(bx + 3, by + 22, 7, 4, C.charLine);
        px(bx + 4, by + 21, 5, 3, "#ffffff");
        ctx.font = "14px sans-serif"; ctx.textAlign = "center";
        ctx.fillStyle = C.ink;
        ctx.fillText(emoji, bx + 14, by + 16);
      }
    }
  }

  // ------------------------------------------------------------ 루프
  function tick(t) {
    ctx.clearRect(0, 0, W, H);
    drawRoom(t);
    drawLounge();
    drawDeco();

    for (const a of agents) {
      const target = targetOf(a);
      const dx = target.x - a.x, dy = target.y - a.y;
      const dist = Math.hypot(dx, dy);
      a.moving = dist > 2;
      if (a.moving) {
        const speed = 2.6;
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
    drawBubbles();

    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);

  return { setAgents, setState, say, STATE_KO };
})();
