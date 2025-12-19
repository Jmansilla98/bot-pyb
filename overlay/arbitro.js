async function loadMatches() {
  const res = await fetch("/api/matches");
  const matches = await res.json();

  const tbody = document.getElementById("matches");
  tbody.innerHTML = "";

  matches.forEach(m => {
    const tr = document.createElement("tr");

    const results = Object.entries(m.results || {})
      .sort((a,b) => a[0] - b[0])
      .map(([slot, r]) => `M${slot}: ${r.score} (${r.winner})`)
      .join("<br>") || "—";

    tr.innerHTML = `
      <td>${m.teams}</td>
      <td>${m.mode || "—"}</td>
      <td class="status ${m.status.replace(" ", "").toLowerCase()}">${m.status}</td>
      <td>${results}</td>
    `;

    tbody.appendChild(tr);
  });
}

// refresco automático
setInterval(loadMatches, 2000);
loadMatches();
