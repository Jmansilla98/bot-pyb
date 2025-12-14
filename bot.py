import discord
from discord.ext import commands
import os
import asyncio
import re
import json
import base64
import requests
import socket
import threading
from typing import Dict, Any, List, Optional

# ==========================================================
# TCP SERVER (SOLO PARA HEALTH CHECK EN KOYEB WEB SERVICE)
# - Koyeb hace healthcheck al puerto $PORT (normalmente 8000)
# - Aquí abrimos el puerto y aceptamos conexiones TCP.
# ==========================================================
def run_tcp_healthcheck():
    host = "0.0.0.0"
    port = int(os.getenv("PORT", 8000))

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(5)

    while True:
        conn, addr = s.accept()
        conn.close()

# ==========================================================
# CONFIGURACIÓN DISCORD
# ==========================================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

ROL_ARBITRO = "team1"

# ==========================================================
# CONFIG OVERLAY / GITHUB
# ==========================================================
GITHUB_USER = "Jmansilla98"
GITHUB_REPO = "overlay-cod-fecod"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # obligatorio si quieres overlay

OVERLAY_BASE_URL = f"https://{GITHUB_USER}.github.io/{GITHUB_REPO}"
MATCHES_PATH = "matches"

GITHUB_API = "https://api.github.com"

# ==========================================================
# MAPAS / BANDOS / COLORES
# ==========================================================
MAPAS = {
    "HP": ["Blackheart", "Colossus", "Den", "Exposure", "Scar"],
    "SnD": ["Colossus", "Den", "Exposure", "Raid", "Scar"],
    "Overload": ["Den", "Exposure", "Scar"]
}
BANDOS = ["Ataque", "Defensa"]

COLORES = {
    "HP": discord.Color.red(),
    "SnD": discord.Color.gold(),
    "Overload": discord.Color.purple()
}

# ==========================================================
# FORMATO PICK & BAN (TU REGLA)
# BO5:
# HP: ban A, ban B, pick A (map1), side B (map1), pick B (map4), side A (map4)
# SnD: ban B, ban A, pick B (map2), side A (map2), pick A (map5), side B (map5)
# Overload: ban A, ban B, (restante = map3), side A (map3)
#
# BO3:
# HP: ban A, ban B, pick A (map1), side B (map1)
# SnD: ban B, ban A, pick B (map2), side A (map2)
# Overload: ban A, ban B, (restante = map3), side A (map3)
# ==========================================================
FORMATOS = {
    "bo3": [
        ("ban",  "HP",       "A"),
        ("ban",  "HP",       "B"),
        ("pick", "HP",       "A"),   # Map 1
        ("side", "HP",       "B"),   # side map 1

        ("ban",  "SnD",      "B"),
        ("ban",  "SnD",      "A"),
        ("pick", "SnD",      "B"),   # Map 2
        ("side", "SnD",      "A"),   # side map 2

        ("ban",  "Overload", "A"),
        ("ban",  "Overload", "B"),
        ("auto", "Overload", None),  # Map 3 restante
        ("side", "Overload", "A"),   # side map 3
    ],
    "bo5": [
        ("ban",  "HP",       "A"),
        ("ban",  "HP",       "B"),
        ("pick", "HP",       "A"),   # Map 1
        ("side", "HP",       "B"),   # side map 1
        ("pick", "HP",       "B"),   # Map 4
        ("side", "HP",       "A"),   # side map 4

        ("ban",  "SnD",      "B"),
        ("ban",  "SnD",      "A"),
        ("pick", "SnD",      "B"),   # Map 2
        ("side", "SnD",      "A"),   # side map 2
        ("pick", "SnD",      "A"),   # Map 5
        ("side", "SnD",      "B"),   # side map 5

        ("ban",  "Overload", "A"),
        ("ban",  "Overload", "B"),
        ("auto", "Overload", None),  # Map 3 restante
        ("side", "Overload", "A"),   # side map 3
    ]
}

# ==========================================================
# ESTADO DE PARTIDOS (POR CANAL)
# ==========================================================
matches: Dict[int, Dict[str, Any]] = {}

def get_match(cid: int) -> Optional[Dict[str, Any]]:
    return matches.get(cid)

def is_ref(user: discord.Member) -> bool:
    return any(r.name == ROL_ARBITRO for r in user.roles)

def role_for_team(match: Dict[str, Any], team_key: str) -> discord.Role:
    return match["equipo_a"] if team_key == "A" else match["equipo_b"]

def current_step(match: Dict[str, Any]):
    steps = FORMATOS[match["formato"]]
    return steps[match["paso"]]

def current_turn_role(match: Dict[str, Any]) -> Optional[discord.Role]:
    accion, modo, eq = current_step(match)
    if eq is None:
        return None
    return role_for_team(match, eq)

def needed_wins(formato: str) -> int:
    return 2 if formato == "bo3" else 3

def serie_score(match: Dict[str, Any]):
    a_wins = sum(1 for r in match["resultados"] if r["winner"] == "A")
    b_wins = sum(1 for r in match["resultados"] if r["winner"] == "B")
    return a_wins, b_wins

# ==========================================================
# GITHUB HELPERS (OVERLAY)
# ==========================================================
def gh_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

def subir_overlay(channel_id: int, data: Dict[str, Any]):
    if not GITHUB_TOKEN:
        return  # overlay desactivado si no hay token

    url = f"{GITHUB_API}/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{MATCHES_PATH}/{channel_id}.json"
    content = base64.b64encode(json.dumps(data, indent=2).encode()).decode()

    r = requests.get(url, headers=gh_headers(), timeout=10)
    sha = r.json().get("sha") if r.status_code == 200 else None

    payload = {
        "message": "Actualizar overlay",
        "content": content
    }
    if sha:
        payload["sha"] = sha

    requests.put(url, headers=gh_headers(), json=payload, timeout=10)

def overlay_payload(match: Dict[str, Any], channel_id: int) -> Dict[str, Any]:
    a_w, b_w = serie_score(match)

    # mapa actual de resultados (si ya terminó pyb)
    mapa_actual = None
    if match["fase"] == "RESULTADOS":
        idx = len(match["resultados"])
        if idx < len(match["mapas_serie"]):
            mm = match["mapas_serie"][idx]
            mapa_actual = f"{mm['modo']} - {mm['mapa']}"

    return {
        "channel": str(channel_id),
        "formato": match["formato"],
        "equipo_a": match["equipo_a"].name,
        "equipo_b": match["equipo_b"].name,
        "scoreA": a_w,
        "scoreB": b_w,
        "fase": match["fase"],
        "pyb_step": match["paso"],
        "pyb_action": (current_step(match)[0] if match["fase"] == "PYB" else None),
        "pyb_mode": (current_step(match)[1] if match["fase"] == "PYB" else None),
        "reclamacion": match.get("reclamacion", False),
        "mapa_actual": mapa_actual,
        "mapas_serie": match.get("mapas_serie", []),
    }

def push_overlay(match: Dict[str, Any], channel_id: int):
    data = overlay_payload(match, channel_id)
    subir_overlay(channel_id, data)

# ==========================================================
# EMBEDS (BONITOS)
# ==========================================================
def action_label(accion: str) -> str:
    if accion == "ban": return "BAN"
    if accion == "pick": return "PICK"
    if accion == "side": return "BANDO"
    if accion == "auto": return "AUTO"
    return accion.upper()

def embed_pyb(match: Dict[str, Any]) -> discord.Embed:
    accion, modo, _ = current_step(match)
    turno = current_turn_role(match)
    color = COLORES.get(modo, discord.Color.blurple())

    e = discord.Embed(title="🎮 PICK & BAN", color=color)
    e.add_field(name="Acción", value=action_label(accion), inline=True)
    e.add_field(name="Modo", value=modo, inline=True)
    e.add_field(name="Turno", value=turno.mention if turno else "—", inline=True)
    e.add_field(
        name="Equipos",
        value=f"🔵 {match['equipo_a'].mention}\n🔴 {match['equipo_b'].mention}",
        inline=False
    )

    # Resumen parcial
    picks = []
    for i, mm in enumerate(match["mapas_serie"], start=1):
        side = f" — **{mm['bando']}**" if mm.get("bando") else ""
        picks.append(f"Mapa {i}: **{mm['modo']}** · {mm['mapa']}{side}")
    e.add_field(name="Serie (parcial)", value="\n".join(picks) if picks else "—", inline=False)

    steps_total = len(FORMATOS[match["formato"]])
    e.set_footer(text=f"Paso {match['paso']+1}/{steps_total} · Formato {match['formato'].upper()}")
    return e

def embed_resultados(match: Dict[str, Any]) -> discord.Embed:
    idx = len(match["resultados"])
    if idx >= len(match["mapas_serie"]):
        e = discord.Embed(title="📝 Resultados", description="No hay más mapas.", color=discord.Color.green())
        return e

    mm = match["mapas_serie"][idx]
    e = discord.Embed(
        title=f"📝 Resultado Mapa {idx+1}",
        description=f"**{mm['modo']} — {mm['mapa']}**",
        color=COLORES.get(mm["modo"], discord.Color.blurple())
    )
    e.add_field(name=match["equipo_a"].name, value="—", inline=True)
    e.add_field(name=match["equipo_b"].name, value="—", inline=True)
    e.set_footer(text="Usa !resultado A B (solo árbitro recomendado)")
    return e

def embed_final(match: Dict[str, Any]) -> discord.Embed:
    a_w, b_w = serie_score(match)
    ganador = match["equipo_a"].name if a_w > b_w else match["equipo_b"].name
    e = discord.Embed(title=f"🏆 {ganador} gana {a_w}-{b_w}", color=discord.Color.green())

    texto = []
    for i, r in enumerate(match["resultados"], start=1):
        texto.append(
            f"**Mapa {i} — {r['modo']} ({r['mapa']})**\n"
            f"{match['equipo_a'].name} **{r['a']}** — **{r['b']}** {match['equipo_b'].name}"
        )
    e.add_field(name="Resultados", value="\n\n".join(texto) if texto else "—", inline=False)
    return e

# ==========================================================
# BOTONES: MAPAS
# ==========================================================
class MapButton(discord.ui.Button):
    def __init__(self, mapa: str, match: Dict[str, Any], modo: str):
        disabled = mapa in match["usados"][modo]
        super().__init__(label=mapa, style=discord.ButtonStyle.primary, disabled=disabled)
        self.mapa = mapa
        self.match = match
        self.modo = modo

    async def callback(self, interaction: discord.Interaction):
        # validar turno
        turno = current_turn_role(self.match)
        if turno and turno not in interaction.user.roles:
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)

        accion, modo, _ = current_step(self.match)
        if modo != self.modo or accion not in ("ban", "pick"):
            return await interaction.response.send_message("❌ Acción inválida para este botón", ephemeral=True)

        # aplicar
        self.match["usados"][modo].add(self.mapa)

        if accion == "pick":
            # asignar número de mapa según el modo y la etapa
            # Orden final: BO3 -> HP(1), SnD(2), Overload(3)
            # BO5 -> HP(1), SnD(2), Overload(3), HP(4), SnD(5)
            self.match["mapas_serie"].append({"modo": modo, "mapa": self.mapa, "bando": None})

        # avanzar paso
        self.match["paso"] += 1
        await avanzar_pyb(interaction)

class MapView(discord.ui.View):
    def __init__(self, match: Dict[str, Any], modo: str):
        super().__init__(timeout=None)
        for m in MAPAS[modo]:
            self.add_item(MapButton(m, match, modo))

# ==========================================================
# BOTONES: BANDOS
# ==========================================================
class SideButton(discord.ui.Button):
    def __init__(self, bando: str, match: Dict[str, Any]):
        super().__init__(label=bando, style=discord.ButtonStyle.secondary)
        self.bando = bando
        self.match = match

    async def callback(self, interaction: discord.Interaction):
        turno = current_turn_role(self.match)
        if turno and turno not in interaction.user.roles:
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)

        accion, modo, _ = current_step(self.match)
        if accion != "side":
            return await interaction.response.send_message("❌ No toca elegir bando", ephemeral=True)

        # ¿a qué mapa se aplica el bando?
        # Regla: el bando aplica al ÚLTIMO mapa "de ese modo" que se ha añadido en mapas_serie,
        # excepto Overload, que se añade por AUTO antes del side.
        # Así que tomamos el último mapa cuyo modo == modo (o el último en general si Overload).
        target_index = None
        for i in range(len(self.match["mapas_serie"]) - 1, -1, -1):
            if self.match["mapas_serie"][i]["modo"] == modo:
                target_index = i
                break

        if target_index is None:
            return await interaction.response.send_message("❌ No hay mapa para asignar bando", ephemeral=True)

        self.match["mapas_serie"][target_index]["bando"] = self.bando

        self.match["paso"] += 1
        await avanzar_pyb(interaction)

class SideView(discord.ui.View):
    def __init__(self, match: Dict[str, Any]):
        super().__init__(timeout=None)
        for b in BANDOS:
            self.add_item(SideButton(b, match))

# ==========================================================
# FLUJO PYB
# ==========================================================
def aplicar_auto_overload(match: Dict[str, Any]):
    # tras 2 bans overload, se elige el mapa restante
    usados = match["usados"]["Overload"]
    restantes = [m for m in MAPAS["Overload"] if m not in usados]
    if not restantes:
        # fallback: si por error están todos usados, mete el primero
        mapa = MAPAS["Overload"][0]
    else:
        mapa = restantes[0]

    # añadir como mapa 3 (en el orden actual de construcción, es el siguiente)
    match["mapas_serie"].append({"modo": "Overload", "mapa": mapa, "bando": None})

async def avanzar_pyb(interaction: discord.Interaction):
    match = get_match(interaction.channel.id)
    if not match:
        return await interaction.response.send_message("❌ No hay partido en este canal", ephemeral=True)

    steps = FORMATOS[match["formato"]]

    # Si terminamos pasos -> pasar a resultados
    if match["paso"] >= len(steps):
        match["fase"] = "RESULTADOS"
        push_overlay(match, interaction.channel.id)
        return await interaction.response.edit_message(embed=embed_resultados(match), view=None)

    accion, modo, _ = current_step(match)

    # AUTO Overload: crear mapa restante y avanzar automáticamente al siguiente paso (side)
    if accion == "auto" and modo == "Overload":
        aplicar_auto_overload(match)
        match["paso"] += 1  # pasamos al "side overload"
        push_overlay(match, interaction.channel.id)

        # ahora mostramos bando
        return await interaction.response.edit_message(embed=embed_pyb(match), view=SideView(match))

    # Mostrar vista correspondiente
    if accion in ("ban", "pick"):
        view = MapView(match, modo)
    elif accion == "side":
        view = SideView(match)
    else:
        view = None

    push_overlay(match, interaction.channel.id)
    await interaction.response.edit_message(embed=embed_pyb(match), view=view)

# ==========================================================
# COMANDOS
# ==========================================================
@bot.command()
async def setpartido(ctx, equipo_a: discord.Role, equipo_b: discord.Role, formato: str):
    """
    Uso:
      !setpartido @TeamA @TeamB bo3
      !setpartido @TeamA @TeamB bo5
    Arranca PyB inmediatamente con botones.
    """
    formato = formato.lower().strip()
    if formato not in FORMATOS:
        return await ctx.send("❌ Formato inválido. Usa bo3 o bo5.")

    matches[ctx.channel.id] = {
        "equipo_a": equipo_a,
        "equipo_b": equipo_b,
        "formato": formato,
        "fase": "PYB",
        "paso": 0,
        "usados": {"HP": set(), "SnD": set(), "Overload": set()},
        "mapas_serie": [],      # lista final en orden real de la serie
        "resultados": [],       # resultados por mapa
        "reclamacion": False,
    }

    match = matches[ctx.channel.id]
    push_overlay(match, ctx.channel.id)

    # Mostrar primera acción con botones
    accion, modo, _ = current_step(match)
    view = MapView(match, modo) if accion in ("ban", "pick") else SideView(match)

    await ctx.send(embed=embed_pyb(match), view=view)

@bot.command()
async def reclamar(ctx):
    match = get_match(ctx.channel.id)
    if not match:
        return await ctx.send("❌ No hay partido activo en este canal.")
    match["reclamacion"] = True
    push_overlay(match, ctx.channel.id)
    await ctx.send("🚨 Reclamación registrada. Un árbitro debe revisar la incidencia.")

@bot.command()
async def resultado(ctx, a: int, b: int):
    """
    Registra resultado del mapa actual en fase RESULTADOS.
    (Recomendado que solo lo use el árbitro, pero no lo bloqueo por si queréis rapidez)
    """
    match = get_match(ctx.channel.id)
    if not match:
        return await ctx.send("❌ No hay partido activo en este canal.")
    if match["fase"] != "RESULTADOS":
        return await ctx.send("❌ Aún no ha terminado el Pick & Ban.")

    if is_ref(ctx.author) is False:
        # No lo bloqueo al 100% por si queréis, pero puedes descomentar para bloquear:
        # return await ctx.send("⛔ Solo árbitro puede meter resultados.")
        pass

    if a == b:
        return await ctx.send("❌ No puede haber empate.")

    idx = len(match["resultados"])
    if idx >= len(match["mapas_serie"]):
        return await ctx.send("❌ Ya no hay más mapas.")

    mm = match["mapas_serie"][idx]
    modo = mm["modo"]

    # Validaciones que pediste (opcionales, pero útiles)
    if modo == "HP":
        if not (0 <= a <= 250 and 0 <= b <= 250):
            return await ctx.send("❌ En HP los valores deben estar entre 0 y 250.")
        if a == 250 and b == 250:
            return await ctx.send("❌ En HP solo un equipo puede llegar a 250.")
    elif modo == "SnD":
        if not (0 <= a <= 6 and 0 <= b <= 6):
            return await ctx.send("❌ En SnD los valores deben estar entre 0 y 6.")
    elif modo == "Overload":
        if a < 0 or b < 0:
            return await ctx.send("❌ En Overload los valores deben ser positivos.")

    winner = "A" if a > b else "B"
    match["resultados"].append({
        "modo": modo,
        "mapa": mm["mapa"],
        "a": a,
        "b": b,
        "winner": winner
    })

    push_overlay(match, ctx.channel.id)

    a_w, b_w = serie_score(match)
    if a_w >= needed_wins(match["formato"]) or b_w >= needed_wins(match["formato"]):
        push_overlay(match, ctx.channel.id)
        await ctx.send(embed=embed_final(match))
        await ctx.send(
            f"🎥 POV A:\n{OVERLAY_BASE_URL}/pov.html?match={ctx.channel.id}&team=A\n\n"
            f"🎥 POV B:\n{OVERLAY_BASE_URL}/pov.html?match={ctx.channel.id}&team=B"
        )
        return

    await ctx.send(embed=embed_resultados(match))

# ==========================================================
# ARRANQUE
# ==========================================================
if __name__ == "__main__":
    # Arrancar TCP health check (Koyeb)
    threading.Thread(target=run_tcp_healthcheck, daemon=True).start()

    # Arrancar bot Discord
    bot.run(os.getenv("DISCORD_TOKEN"))
