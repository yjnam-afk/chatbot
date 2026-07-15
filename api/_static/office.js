/* 픽셀 사무소 렌더러 — 동물의 숲 컨셉의 야외 광장.
   외부 에셋 없이 fillRect 픽셀아트로 전부 코드 렌더링한다. */

const Office = (() => {
  const canvas = document.getElementById("office");
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  const PX = 4;

  let fontOK = false;
  if (document.fonts) {
    document.fonts.load('10px "Galmuri9"').then(() => { fontOK = true; });
  }
  const F9 = () => (fontOK ? '10px "Galmuri9"' : "10px sans-serif");

  // ------------------------------------------------------------ 팔레트
  const C = {
    grassA: "#7ebf5a",
    grassB: "#76b654",
    grassDot: "#6cab4b",
    path: "#e3cfa0",
    pathEdge: "#c9b382",
    wood: "#c98f56",
    woodDark: "#a06c3c",
    furn: "#5a4028",
    ink: "#5a4632",
  };

  // ------------------------------------------------------------ 동물 스프라이트
  // o외곽선 F털 f털음영 m주둥이 n코 e눈 B옷 b옷음영 P하의 E발
  const HEAD = [
    "...oooooo...",
    "..oFFFFFFo..",
    ".oFFFFFFFFo.",
    ".oFFFFFFFFo.",
    ".oFeFFFFeFo.",
    ".oFFmmmmFFo.",
    ".oFmmnnmmFo.",
    ".oFFmmmmFFo.",
    "..oFFFFFFo..",
    "...oFFFFo...",
  ];
  const TORSO = [
    "..oBBBBBBo..",
    ".oBbBBBBbBo.",
    ".oBbBBBBbBo.",
    ".oFoBBBBoFo.",
    "..oBBBBBBo..",
  ];
  const LEGS = {
    stand: ["...oPPPPo...", "...oPPPPo...", "...oP..Po...", "..oEE..EEo.."],
    walkA: ["...oPPPPo...", "..oPP..PPo..", "..oP....Po..", ".oEE....EEo."],
    walkB: ["...oPPPPo...", "...oPPPPo...", "....oPPo....", "...oEEEEo..."],
  };
  const SPRITE_W = 12 * PX;
  const SPRITE_H = (10 + 5 + 4) * PX;

  // 귀 오버레이 (머리 위/옆에 종별로 그린다) — [dx(칸), dy(칸), w, h] 단위: 스프라이트 픽셀
  const SPECIES = {
    dog:    { fur: "#e8b04a", ears: [[1, 1, 2, 5], [9, 1, 2, 5]], earShade: true },   // 늘어진 귀
    cat:    { fur: "#9db8d8", ears: [[2, -2, 2, 2], [8, -2, 2, 2]] },       // 뾰족 귀
    rabbit: { fur: "#c9aee6", ears: [[3, -5, 2, 6], [7, -5, 2, 6]] },                  // 긴 귀
    frog:   { fur: "#8ecf7a", bumps: [[3, -1], [7, -1]] },                             // 눈 볼록
    bear:   { fur: "#f0b0c0", ears: [[1, -1, 3, 3], [8, -1, 3, 3]] },                  // 둥근 귀
  };
  const LOOKS = {
    orchestrator: "dog",
    nlu: "cat",
    designer: "rabbit",
    writer: "frog",
    reviewer: "bear",
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
    { x: 150, y: 420 }, { x: 96, y: 448 }, { x: 208, y: 452 },
    { x: 252, y: 420 }, { x: 60, y: 420 },
  ];
  const CAMPFIRE = { x: 152, y: 448 };

  const BUBBLE = { thinking: "💭", working: "⚒️", done: "✅", error: "❌" };
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

  // ------------------------------------------------------------ 유틸
  function px(x, y, w, h, c) { ctx.fillStyle = c; ctx.fillRect(x, y, w, h); }
  function outlined(x, y, w, h, fill, line = C.furn) {
    px(x - 2, y - 2, w + 4, h + 4, line);
    px(x, y, w, h, fill);
  }
  function shadow(x, y, w) {
    ctx.globalAlpha = 0.15;
    px(x, y, w, 6, "#274018");
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

  // ------------------------------------------------------------ 배경 (잔디 광장)
  const FLOWERS = [];
  for (let i = 0; i < 14; i++) {
    FLOWERS.push({
      x: (i * 173 + 40) % (W - 40) + 20,
      y: (i * 97 + 60) % (H - 120) + 80,
      c: ["#f2c14e", "#ef8aa0", "#fdfcf7"][i % 3],
    });
  }

  function drawGrass() {
    // 동숲식 체크 잔디
    for (let y = 0; y < H; y += 24) {
      for (let x = 0; x < W; x += 24) {
        px(x, y, 24, 24, ((x + y) / 24) % 2 === 0 ? C.grassA : C.grassB);
      }
    }
    // 풀 무늬 (작은 삼각 점)
    for (let y = 12; y < H; y += 48) {
      for (let x = 12; x < W; x += 48) {
        px(x, y, 3, 2, C.grassDot);
        px(x + 1, y - 2, 1, 2, C.grassDot);
        px(x + 24, y + 24, 3, 2, C.grassDot);
        px(x + 25, y + 22, 1, 2, C.grassDot);
      }
    }
  }

  function drawPath() {
    // 책상 구역을 잇는 흙길
    ctx.globalAlpha = 0.9;
    px(60, 226, 640, 26, C.path);
    px(60, 224, 640, 2, C.pathEdge);
    px(60, 252, 640, 2, C.pathEdge);
    ctx.globalAlpha = 1;
  }

  function drawTree(x, y) {
    shadow(x - 24, y - 2, 52);
    px(x - 7, y - 26, 14, 28, "#8a5f36");
    px(x - 3, y - 26, 4, 28, "#9c7044");
    ctx.fillStyle = "#3f8f47";
    ctx.beginPath(); ctx.arc(x, y - 52, 30, 0, 7); ctx.fill();
    ctx.fillStyle = "#4fa653";
    ctx.beginPath(); ctx.arc(x - 12, y - 44, 20, 0, 7); ctx.fill();
    ctx.fillStyle = "#65bd68";
    ctx.beginPath(); ctx.arc(x + 8, y - 62, 18, 0, 7); ctx.fill();
    px(x - 16, y - 52, 6, 6, "#e85d4a");
    px(x + 10, y - 44, 6, 6, "#e85d4a");
    px(x - 2, y - 70, 6, 6, "#e85d4a");
  }

  function drawFlowers(t) {
    for (const f of FLOWERS) {
      const sway = Math.sin(t / 600 + f.x) > 0.6 ? 1 : 0;
      px(f.x + 1, f.y + 3, 2, 4, "#4c8f3c");
      px(f.x - 2 + sway, f.y - 2, 8, 5, f.c);
      px(f.x + sway, f.y - 4, 4, 9, f.c);
      px(f.x + 1 + sway, f.y - 1, 2, 3, "#f6e2a0");
    }
  }

  function drawSign() {
    shadow(28, 96, 90);
    px(56, 60, 8, 40, "#8a5f36");
    outlined(26, 32, 96, 34, "#d9a869");
    px(30, 36, 88, 4, "#e8bc80");
    ctx.fillStyle = "#4d3620"; ctx.font = F9(); ctx.textAlign = "center";
    ctx.fillText("픽셀 사무소", 74, 48);
    px(70, 52, 8, 6, "#4f9e4f");
    px(74, 50, 4, 4, "#65bd68");
    px(69, 57, 3, 2, "#3f8540");
  }

  function drawCampsite(t) {
    // 텐트
    shadow(38, 380, 110);
    ctx.fillStyle = C.furn;
    ctx.beginPath(); ctx.moveTo(40, 384); ctx.lineTo(92, 330); ctx.lineTo(144, 384); ctx.closePath(); ctx.fill();
    ctx.fillStyle = "#f2a25c";
    ctx.beginPath(); ctx.moveTo(46, 381); ctx.lineTo(92, 335); ctx.lineTo(138, 381); ctx.closePath(); ctx.fill();
    ctx.fillStyle = "#e88a3c";
    ctx.beginPath(); ctx.moveTo(70, 381); ctx.lineTo(92, 349); ctx.lineTo(114, 381); ctx.closePath(); ctx.fill();
    ctx.fillStyle = "#7a4a24";
    ctx.beginPath(); ctx.moveTo(80, 381); ctx.lineTo(92, 360); ctx.lineTo(104, 381); ctx.closePath(); ctx.fill();
    // 캠프파이어
    const f = CAMPFIRE;
    px(f.x - 16, f.y - 4, 32, 8, "#8a5f36");
    px(f.x - 12, f.y - 8, 24, 6, "#a06c3c");
    const flick = Math.floor(t / 160) % 2;
    px(f.x - 7, f.y - 22 + flick, 14, 14, "#f2903c");
    px(f.x - 4, f.y - 28 + flick * 2, 8, 12, "#f6b83c");
    px(f.x - 2, f.y - 20 + flick, 4, 8, "#fbe27a");
    // 통나무 의자
    px(196, 428, 34, 12, C.woodDark);
    px(196, 424, 34, 6, C.wood);
    px(236, 448, 34, 12, C.woodDark);
    px(236, 444, 34, 6, C.wood);
  }

  function drawPond(t) {
    const x = 640, y = 420;
    ctx.fillStyle = "#5a8ac4";
    ctx.beginPath(); ctx.ellipse(x, y, 74, 40, 0, 0, 7); ctx.fill();
    ctx.fillStyle = "#74a8d8";
    ctx.beginPath(); ctx.ellipse(x, y, 64, 32, 0, 0, 7); ctx.fill();
    const r = Math.floor(t / 500) % 3;
    ctx.strokeStyle = "rgba(255,255,255,0.5)"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.ellipse(x - 14, y - 6, 6 + r * 4, 3 + r * 2, 0, 0, 7); ctx.stroke();
    // 수련잎
    ctx.fillStyle = "#5fae57";
    ctx.beginPath(); ctx.ellipse(x + 26, y + 10, 11, 7, 0, 0, 7); ctx.fill();
    px(x + 24, y + 4, 4, 4, "#ef8aa0");
  }

  function drawButterfly(t) {
    const bx = W / 2 + Math.sin(t / 2400) * 300;
    const by = 110 + Math.sin(t / 1300) * 40 + Math.sin(t / 300) * 6;
    const flap = Math.floor(t / 120) % 2;
    px(bx - 4, by - flap * 2, 4, 4 + flap * 2, "#fdfcf7");
    px(bx + 1, by - flap * 2, 4, 4 + flap * 2, "#fdfcf7");
    px(bx, by + 1, 1, 4, "#5a4632");
  }

  function drawDesk(x, y, a, busy, t) {
    // 통나무 책상
    shadow(x - 54, y + 44, 112);
    outlined(x - 54, y + 8, 108, 14, "#d9a869");
    px(x - 54, y + 8, 108, 4, "#e8bc80");
    px(x - 54, y + 22, 108, 20, C.wood);
    px(x - 54, y + 22, 108, 3, C.woodDark);
    px(x - 50, y + 42, 8, 8, C.woodDark);
    px(x + 42, y + 42, 8, 8, C.woodDark);
    // 노트북 (크림색)
    if (busy) {
      ctx.globalAlpha = 0.18;
      px(x - 30, y - 28, 60, 42, "#bde3f5");
      ctx.globalAlpha = 1;
    }
    outlined(x - 22, y - 22, 44, 30, "#fdf6dd");
    if (busy) {
      px(x - 18, y - 18, 36, 22, "#12303f");
      const cols = ["#f2c14e", "#8ecf7a", "#ef8aa0", "#7fc4ea"];
      for (let i = 0; i < 4; i++) {
        const wLine = 8 + ((t / 150 + i * 4) % 22);
        px(x - 15, y - 15 + i * 5, wLine, 3, cols[i % 4]);
      }
    } else {
      px(x - 18, y - 18, 36, 22, "#3d4d42");
    }
    px(x - 26, y + 8, 52, 5, "#e8dcc0");
    // 소품: 머그컵 + 서류
    px(x - 44, y + 10, 11, 10, a.color);
    px(x - 33, y + 12, 4, 5, a.color);
    px(x + 30, y + 10, 16, 3, "#fdfcf7");
    px(x + 32, y + 7, 16, 3, "#f4efe2");
    // 명패
    outlined(x - 40, y + 52, 80, 14, "#fdf6dd");
    px(x - 36, y + 55, 8, 8, a.color);
    ctx.fillStyle = C.ink; ctx.font = F9(); ctx.textAlign = "left";
    ctx.fillText(a.name + " · " + a.role, x - 24, y + 63);
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

  function shade(hex, d = 42) {
    const n = parseInt(hex.slice(1), 16);
    const r = Math.max(0, (n >> 16) - d), g = Math.max(0, ((n >> 8) & 255) - d), b = Math.max(0, (n & 255) - d);
    return `rgb(${r},${g},${b})`;
  }

  function drawEars(a, ox, oy, sp) {
    const fur = sp.fur, dark = shade(fur, 30);
    if (sp.ears) {
      for (const [dx, dy, w, h] of sp.ears) {
        const ex = ox + dx * PX, ey = oy + dy * PX;
        px(ex - PX / 2, ey - PX / 2, w * PX + PX, h * PX + PX, C.furnLine || "#3d3226");
        px(ex, ey, w * PX, h * PX, sp.earShade ? dark : fur);
        if (!sp.earShade && h >= 3) px(ex + PX / 2, ey + PX, w * PX - PX, (h - 2) * PX, "#f0c8d0");
      }
    }
    if (sp.bumps) {
      for (const [dx, dy] of sp.bumps) {
        const ex = ox + dx * PX, ey = oy + dy * PX;
        px(ex - PX / 2, ey - PX / 2, 2 * PX + PX, 2 * PX + PX, "#3d3226");
        px(ex, ey, 2 * PX, 2 * PX, fur);
        px(ex + PX / 2, ey + PX / 2, PX, PX, "#3d3226");
      }
    }
  }

  function drawAgent(a, t) {
    const bob = a.moving ? 0 : Math.sin(t / 500 + a.x) * 1.4;
    const ox = a.x - SPRITE_W / 2;
    const oy = a.y - SPRITE_H + bob;

    ctx.globalAlpha = 0.2;
    ctx.fillStyle = "#274018";
    ctx.beginPath();
    ctx.ellipse(a.x, a.y + 3, 19, 6, 0, 0, 7);
    ctx.fill();
    ctx.globalAlpha = 1;

    const sp = SPECIES[LOOKS[a.id] || "dog"];
    const palette = {
      o: "#3d3226",
      F: sp.fur,
      f: shade(sp.fur, 30),
      m: "#fbf0dc",
      n: "#3d3226",
      e: "#3d3226",
      B: a.color,
      b: shade(a.color),
      P: "#8a6a4a",
      E: "#3d3226",
    };
    drawEars(a, ox, oy, sp);
    const legs = a.moving
      ? (Math.floor(t / 130) % 2 ? LEGS.walkA : LEGS.walkB)
      : LEGS.stand;
    const flip = a.facing < 0;
    let yy = drawSpriteRows(HEAD, ox, oy, palette, flip);
    yy = drawSpriteRows(TORSO, ox, yy, palette, flip);
    drawSpriteRows(legs, ox, yy, palette, flip);

    if (a.state === "idle" || a.moving) {
      ctx.font = F9(); ctx.textAlign = "center";
      ctx.fillStyle = "rgba(39,64,24,0.9)";
      ctx.fillText(a.name, a.x + 1, a.y + 19);
      ctx.fillStyle = "#ffffff";
      ctx.fillText(a.name, a.x, a.y + 18);
    }

    if (a.state === "idle" && !a.say && !a.moving && Math.floor(t / 1100) % 3 === 0) {
      ctx.font = F9(); ctx.fillStyle = "#fdf6dd";
      ctx.fillText("Zzz", a.x + 24, oy - 6);
    }
  }

  // 말풍선 (동숲식: 이름표 달린 흰 풍선) — 항상 최상단
  function drawBubbles() {
    for (const a of agents) {
      if (a.say && performance.now() < a.say.until) {
        const lines = wrapText(a.say.text, 15);
        ctx.font = F9();
        let wMax = 0;
        for (const l of lines) wMax = Math.max(wMax, ctx.measureText(l).width);
        const bw = Math.min(224, Math.max(wMax + 18, 70)), bh = lines.length * 14 + 12;
        let bx = a.x - bw / 2, by = a.y - SPRITE_H - bh - 16;
        bx = Math.max(6, Math.min(W - bw - 6, bx));
        by = Math.max(20, by);
        outlined(bx, by, bw, bh, "#fffdf4", "#5a4632");
        px(a.x - 5, by + bh + 2, 10, 4, "#5a4632");
        px(a.x - 4, by + bh, 8, 4, "#fffdf4");
        // 이름표
        const tagW = ctx.measureText(a.name).width + 14;
        outlined(bx + 6, by - 9, tagW, 13, a.color, "#5a4632");
        ctx.fillStyle = "#ffffff"; ctx.textAlign = "left";
        ctx.fillText(a.name, bx + 13, by + 2);
        ctx.fillStyle = C.ink;
        lines.forEach((l, i) => ctx.fillText(l, bx + 9, by + 17 + i * 14));
        continue;
      }
      if (a.say && performance.now() >= a.say.until) a.say = null;
      if (a.state !== "idle") {
        const emoji = BUBBLE[a.state] || "";
        const bx = a.x + 12, by = a.y - SPRITE_H - 32;
        outlined(bx, by, 28, 22, "#fffdf4", "#5a4632");
        px(bx + 3, by + 22, 7, 4, "#5a4632");
        px(bx + 4, by + 21, 5, 3, "#fffdf4");
        ctx.font = "14px sans-serif"; ctx.textAlign = "center";
        ctx.fillStyle = C.ink;
        ctx.fillText(emoji, bx + 14, by + 16);
      }
    }
  }

  // ------------------------------------------------------------ 루프
  function tick(t) {
    ctx.clearRect(0, 0, W, H);
    drawGrass();
    drawPath();
    drawPond(t);
    drawTree(438, 452);
    drawTree(716, 150);
    drawFlowers(t);
    drawSign();
    drawCampsite(t);

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
    drawButterfly(t);
    drawBubbles();

    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);

  return { setAgents, setState, say, STATE_KO };
})();
