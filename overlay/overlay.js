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

function render(state) {
  teamAEl.textContent = state.teams.A.name;
  teamBEl.textContent = state.teams.B.name;

  const step = state.flow[state.step];
  const finished = state.step >= state.flow.length;

  modeEl.textContent = step?.mode || "";
  estadoEl.textContent = step
    ? `${step.type.toUpperCase()} — TEAM ${step.team || ""}`
    : "FINALIZADO";

  // =========================
  // PICKED MAPS (ORDERED)
  // =========================
  const picked = Object.entries(state.maps)
    .filter(([_, m]) => m.status === "picked")
    .sort((a, b) => a[1].slot - b[1].slot);

  // =========================
  // FINAL MAPS TOP
  // =========================
  finalTop.innerHTML = "";

  if (!finished) {
    picked.forEach(([key, m]) => {
      const name = key.split("::")[1];
      const img = name.charAt(0).toLowerCase() + name.slice(1);

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

  // =========================
  // ACTIVE MODE MAPS
  // =========================
  mapsEl.innerHTML = "";

  const activeMode = step?.mode;

  Object.entries(state.maps)
    .filter(([_, m]) => m.mode === activeMode)
    .forEach(([key, m]) => {
      const name = key.split("::")[1];
      const img = name.charAt(0).toLowerCase() + name.slice(1);

      const card = document.createElement("div");
      card.className = "map-card";
      if (m.status) card.classList.add(m.status);
      if (m.slot === 3) card.classList.add("big");

      card.innerHTML = `
        <div class="map-img" style="background-image:url('/static/maps/${img}.jpg')"></div>
        <div class="map-overlay"></div>
        <div class="map-info">
          <div class="map-name">${name}</div>
          <div class="map-meta">
            ${m.status.toUpperCase()} — TEAM ${m.team || ""}
            ${m.side ? " · " + m.side : ""}
          </div>
        </div>
      `;

      mapsEl.appendChild(card);
    });

  // =========================
  // FINAL CENTER
  // =========================
  if (finished) {
    finalCenter.classList.remove("hidden");
    finalCenter.innerHTML = "";

    picked.forEach(([key, m]) => {
      const name = key.split("::")[1];
      const img = name.charAt(0).toLowerCase() + name.slice(1);

      const div = document.createElement("div");
      div.className = "final-map";
      div.innerHTML = `
        <div class="map-img" style="background-image:url('/static/maps/${img}.jpg')"></div>
        <div class="label">
          M${m.slot} · ${m.mode}<br>
          Pick ${m.team}${m.side ? " · " + m.side : ""}
        </div>
      `;
      finalCenter.appendChild(div);
    });

  } else {
    finalCenter.classList.add("hidden");
  }
}
