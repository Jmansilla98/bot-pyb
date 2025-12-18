const params = new URLSearchParams(window.location.search);
const match = params.get("match");

const teamAEl = document.getElementById("teamA");
const teamBEl = document.getElementById("teamB");
const estadoEl = document.getElementById("estado");
const modeEl = document.getElementById("mode");
const mapsEl = document.getElementById("maps");
const finalTop = document.getElementById("final-maps-top");
const finalCenter = document.getElementById("final-center");

const wsProto = location.protocol === "https:" ? "wss" : "ws";
const ws = new WebSocket(`${wsProto}://${location.host}/ws?match=${match}`);

ws.onmessage = (ev) => {
  const data = JSON.parse(ev.data);
  if (data.type !== "state") return;
  render(data.state);
};

/* =========================
   MAP NAME → IMAGE SLUG
========================= */
function mapToImage(name) {
  return name
    .toLowerCase()
    .replace(/\s+/g, "")
    .replace(/[^a-z0-9]/g, "");
}

function teamToLogo(name) {
  return name
    .replace(/\s+/g, "_"); // para espacios en URL
}


function render(state) {
  const teamAName = state.teams.A.name;
  const teamBName = state.teams.B.name;

  teamAEl.innerHTML = `
    <img class="team-logo left" src="/static/logos/${teamToLogo(teamAName.toLowerCase())}.webp" />
    <span>${teamAName}</span>
  `;

  teamBEl.innerHTML = `
    <span>${teamBName}</span>
    <img class="team-logo right" src="/static/logos/${teamToLogo(teamBName.toLowerCase())}.webp" />
  `;


  // RESET GLOW
  teamAEl.classList.remove("active");
  teamBEl.classList.remove("active");

  const step = state.flow[state.step];
  const finished = state.step >= state.flow.length;

  if (step?.team === "A") teamAEl.classList.add("active");
  if (step?.team === "B") teamBEl.classList.add("active");

  modeEl.textContent = step?.mode || "";
  estadoEl.textContent = step
    ? `${step.type.replace("_", " ").toUpperCase()} · TEAM ${step.team || ""}`
    : "FINALIZADO";

  /* =========================
     MAPAS PICKED
  ========================= */
  const picked = Object.entries(state.maps)
    .filter(([_, m]) => m.status === "picked")
    .sort((a, b) => a[1].slot - b[1].slot);

  /* =========================
     TOP MAPS
  ========================= */
  finalTop.innerHTML = "";
  if (!finished) {
    picked.forEach(([key, m]) => {
      const name = key.split("::")[1];
      const img = mapToImage(name);

      const div = document.createElement("div");
      div.className = "final-map";
      div.innerHTML = `
        <div class="map-img" style="background-image:url('/static/maps/${img}.jpg')"></div>
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
  ========================= */
  mapsEl.innerHTML = "";
  const activeMode = step?.mode;

  Object.entries(state.maps)
    .filter(([_, m]) => m.mode === activeMode)
    .forEach(([key, m]) => {
      const name = key.split("::")[1];
      const img = mapToImage(name);

      const card = document.createElement("div");
      card.className = "map-card";
      if (m.status === "picked") card.classList.add("pick");
      if (m.status === "banned") card.classList.add("banned");

      card.innerHTML = `
        <div class="map-img" style="background-image:url('/static/maps/${img}.jpg')"></div>
        <div class="map-overlay"></div>
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
     FINAL CENTER
  ========================= */
  if (finished) {
    finalCenter.classList.remove("hidden");
    finalCenter.innerHTML = "";

    picked.forEach(([key, m]) => {
      const name = key.split("::")[1];
      const img = mapToImage(name);

      const div = document.createElement("div");
      div.className = "final-map big";
      div.innerHTML = `
        <div class="map-img" style="background-image:url('/static/maps/${img}.jpg')"></div>
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
