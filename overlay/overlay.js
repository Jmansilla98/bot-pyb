const params = new URLSearchParams(window.location.search);
const match = params.get("match");

const teamAEl = document.getElementById("teamA");
const teamBEl = document.getElementById("teamB");
const estadoEl = document.getElementById("estado");
const modeEl = document.getElementById("mode");
const mapsEl = document.getElementById("maps");

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
  modeEl.textContent = step?.mode || "";
  estadoEl.textContent = step
    ? `${step.type} — TEAM ${step.team || ""}`
    : "FINALIZADO";

  mapsEl.innerHTML = "";

  const activeMode = step?.mode;
  const maps = Object.entries(state.maps)
    .filter(([_, m]) => m.mode === activeMode);

  maps.forEach(([key, m]) => {
    const name = key.split("::")[1];

    const card = document.createElement("div");
    card.className = "map-card";
    if (m.status) card.classList.add(m.status);
    if (m.slot === 3) card.classList.add("big");
    const imgName = name.charAt(0).toLowerCase() + name.slice(1);
    card.innerHTML = `
      <div class="map-img" style="background-image:url('/static/maps/${imgName}.jpg')"></div>
      <div class="map-overlay"></div>
      <div class="map-info">
        <div class="map-name">${name}</div>
        <div class="map-meta">
          ${m.status.toUpperCase()} — TEAM ${(m.team) || ""}
          ${m.side ? ` — ${m.side}` : ""}
        </div>
      </div>
    `;

    mapsEl.appendChild(card);
  });
}
