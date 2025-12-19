const matchesEl = document.getElementById("matches");
const lastUpdateEl = document.getElementById("last-update");

async function loadMatches() {
  try {
    const res = await fetch("/api/matches");
    const matches = await res.json();

    matchesEl.innerHTML = "";

    matches.forEach(m => {
      const card = document.createElement("div");
      card.className = "match-card";

      const statusClass =
        m.status === "En curso" ? "status-live" :
        m.status === "Finalizado" ? "status-finished" :
        "status-pending";

      const results = Object.keys(m.results || {}).length
        ? Object.entries(m.results).map(([k, r]) =>
            `<div class="map-result">M${k}: <b>${r.winner}</b> (${r.score})</div>`
          ).join("")
        : "<div class='map-result empty'>Sin resultados</div>";

      card.innerHTML = `
        <div class="match-header">
          <div class="teams">${m.teams}</div>
          <div class="mode">${m.mode || "—"}</div>
        </div>

        <div class="status ${statusClass}">
          ${m.status}
        </div>

        <div class="results">
          ${results}
        </div>
      `;

      matchesEl.appendChild(card);
    });

    lastUpdateEl.textContent = "Última actualización: " + new Date().toLocaleTimeString();
  } catch (e) {
    lastUpdateEl.textContent = "Error al cargar partidos";
  }
}

// refresco automático
loadMatches();
setInterval(loadMatches, 3000);
