const params = new URLSearchParams(window.location.search);
const match = params.get("match");

const teamAEl = document.getElementById("teamA");
const teamBEl = document.getElementById("teamB");
const estadoEl = document.getElementById("estado");
const mapasEl = document.getElementById("maps");

if (!match) {
  estadoEl.textContent = "Falta ?match=";
  throw new Error("Missing match param");
}

// 🔥 WebSocket Fly / local compatible
const wsProto = location.protocol === "https:" ? "wss" : "ws";
const wsUrl = `${wsProto}://${location.host}/ws?match=${encodeURIComponent(match)}`;

console.log("WS:", wsUrl);

const ws = new WebSocket(wsUrl);

ws.onopen = () => console.log("WS open");
ws.onclose = () => console.log("WS close");
ws.onerror = (e) => console.log("WS error", e);

ws.onmessage = (ev) => {
  const data = JSON.parse(ev.data);
  if (data.type !== "state") return;
  render(data.state);
};

function render(state) {
  // Teams
  teamAEl.textContent = state.teams?.A?.name || "TEAM A";
  teamBEl.textContent = state.teams?.B?.name || "TEAM B";

  // Estado actual
  const step = state.flow[state.step];
  estadoEl.textContent = step
    ? `${step.type} ${step.mode || ""}`.toUpperCase()
    : "FINALIZADO";

  mapasEl.innerHTML = "";

  Object.entries(state.maps).forEach(([key, m]) => {
    const mapName = key.split("::")[1];

    const card = document.createElement("div");
    card.classList.add("map-card");

    if (m.status === "banned") card.classList.add("ban");
    if (m.status === "picked") card.classList.add("pick");

    card.innerHTML = `
      <div class="map-img" style="background-image:url('/static/maps/${mapName.toLowerCase}.jpg')"></div>
      <div class="map-name">${mapName}</div>
      ${m.side ? `<div class="map-side">${m.side}</div>` : ""}
    `;

    mapasEl.appendChild(card);
  });
}
