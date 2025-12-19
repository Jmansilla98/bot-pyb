const params = new URLSearchParams(window.location.search);
const match = params.get("match");

const teamAEl = document.getElementById("teamA");
const teamBEl = document.getElementById("teamB");
const estadoEl = document.getElementById("estado");
const modeEl = document.getElementById("mode");
const mapsEl = document.getElementById("maps");
const finalTop = document.getElementById("final-maps-top");
const finalCenter = document.getElementById("final-center");

let turnStart = null;     // epoch seconds
let turnDuration = 30;
let timerRAF = null;

const wsProto = location.protocol === "https:" ? "wss" : "ws";
const ws = new WebSocket(`${wsProto}://${location.host}/ws?match=${match}`);

ws.onmessage = (ev) => {
  const data = JSON.parse(ev.data);
  if (data.type !== "state") return;
  render(data.state);
};

/* =========================
   HELPERS
========================= */
function mapToImage(name) {
  return name
    .toLowerCase()
    .replace(/\s+/g, "")
    .replace(/[^a-z0-9]/g, "");
}

function teamToLogo(name) {
  // tu convención actual: reemplaza espacios por "_" y lo pasas a lower
  return name.replace(/\s+/g, "_");
}

function getResultForSlot(state, slot) {
  if (!state || !state.map_results) return null;
  // en JSON los keys pueden venir como string "1"
  return state.map_results[slot] || state.map_results[String(slot)] || null;
}

function startTimer(team) {
  cancelAnimationFrame(timerRAF);

  const el = team === "A" ? teamAEl : teamBEl;
  const other = team === "A" ? teamBEl : teamAEl;

  el.classList.add("timed");
  other.classList.remove("timed");

  function tick() {
    if (!turnStart) return;

    // turnStart viene en epoch seconds (time.time()), así que usamos Date.now()
    const now = Date.now() / 1000;
    const elapsed = now - turnStart;
    const progress = Math.min(elapsed / turnDuration, 1);

    el.style.setProperty("--timer-progress", `${100 - progress * 100}%`);

    if (progress < 1) {
      timerRAF = requestAnimationFrame(tick);
    }
  }

  tick();
}

/* =========================
   RENDER
========================= */
function render(state) {
  // timer
  if (state.turn_started_at) {
    turnStart = state.turn_started_at;
    turnDuration = state.turn_duration || 30;
  }

  const teamAName = state.teams.A.name;
  const teamBName = state.teams.B.name;

  teamAEl.innerHTML = `
    <img class="team-logo left" src="/static/logos/${teamToLogo(teamAName.toLowerCase())}.webp" />
    <span class="team-name">${teamAName}</span>
  `;

  teamBEl.innerHTML = `
    <span class="team-name">${teamBName}</span>
    <img class="team-logo right" src="/static/logos/${teamToLogo(teamBName.toLowerCase())}.webp" />
  `;

  // RESET GLOW
  teamAEl.classList.remove("active");
  teamBEl.classList.remove("active");

  const step = state.flow[state.step];
  const finished = state.step >= state.flow.length;

  if (step?.team === "A") {
    teamAEl.classList.add("active");
    startTimer("A");
  }
  if (step?.team === "B") {
    teamBEl.classList.add("active");
    startTimer("B");
  }

  modeEl.textContent = step?.mode || "";
  estadoEl.textContent = step
    ? `${step.type.replace("_", " ").toUpperCase()} · TEAM ${step.team || ""}`
    : "FINALIZADO";

  /* =========================
     MAPAS PICKED (orden por slot)
  ========================= */
  const picked = Object.entries(state.maps)
    .filter(([_, m]) => m.status === "picked")
    .sort((a, b) => a[1].slot - b[1].slot);

  /* =========================
     TOP MAPS (mientras NO ha terminado el flujo)
     + ahora pinta score si existe
  ========================= */
  finalTop.innerHTML = "";
  if (!finished) {
    picked.forEach(([key, m]) => {
      const name = key.split("::")[1];
      const img = mapToImage(name);
      const res = getResultForSlot(state, m.slot);

      const div = document.createElement("div");
      div.className = "final-map";
      div.innerHTML = `
        <div class="map-img" style="background-image:url('/static/maps/${img}.jpg')"></div>
        ${res?.score ? `<div class="score-badge">${res.score}</div>` : ""}
        <div class="label">
          M${m.slot} · ${m.mode}<br>
          Pick ${m.team}${m.side ? " · " + m.side : ""}
        </div>
      `;
      finalTop.appendChild(div);
    });
  }

  /* =========================
     ACTIVE MODE MAPS
     + ahora pinta score si existe y el mapa tiene slot
  ========================= */
  mapsEl.innerHTML = "";
  const activeMode = step?.mode;

  Object.entries(state.maps)
    .filter(([_, m]) => m.mode === activeMode)
    .forEach(([key, m]) => {
      const name = key.split("::")[1];
      const img = mapToImage(name);
      const res = m.slot ? getResultForSlot(state, m.slot) : null;

      const card = document.createElement("div");
      card.className = "map-card";
      if (m.status === "picked") card.classList.add("pick");
      if (m.status === "banned") card.classList.add("banned");

      card.innerHTML = `
        <div class="map-img" style="background-image:url('/static/maps/${img}.jpg')"></div>
        <div class="map-overlay"></div>
        ${res?.score ? `<div class="score-badge">${res.score}</div>` : ""}
        <div class="map-info">
          <div class="map-name">${name}</div>
          <div class="map-meta">
            ${m.status.toUpperCase()} · TEAM ${m.team || ""}
            ${m.side ? " · " + m.side : ""}
          </div>
        </div>
      `;
      mapsEl.appendChild(card);
    });

  /* =========================
     FINAL CENTER (cuando TERMINA el flujo)
     + ahora pinta score encima
  ========================= */
  if (finished) {
    finalCenter.classList.remove("hidden");
    finalCenter.innerHTML = "";

    picked.forEach(([key, m]) => {
      const name = key.split("::")[1];
      const img = mapToImage(name);
      const res = getResultForSlot(state, m.slot);

      const div = document.createElement("div");
      div.className = "final-map big";
      div.innerHTML = `
        <div class="map-img" style="background-image:url('/static/maps/${img}.jpg')"></div>
        ${res?.score ? `<div class="score-badge">${res.score}</div>` : ""}
        <div class="label">
          MAP ${m.slot} · ${m.mode}<br>
          ${state.teams[m.team]?.name || m.team}
          ${m.side ? " · " + m.side : ""}
        </div>
      `;
      finalCenter.appendChild(div);
    });
  } else {
    finalCenter.classList.add("hidden");
  }
}
