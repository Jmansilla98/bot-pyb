import discord
from discord.ext import commands
import os
import asyncio
import json
import base64
import requests
import socket
import threading

# ==========================================================
# TCP HEALTH CHECK (KOYEB)
# ==========================================================
def run_tcp_healthcheck():
    host = "0.0.0.0"
    port = int(os.getenv("PORT", 8000))
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(5)
    while True:
        conn, _ = s.accept()
        conn.close()

# ==========================================================
# DISCORD CONFIG
# ==========================================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

ROL_ARBITRO = "Arbitro"

# ==========================================================
# GITHUB / OVERLAY
# ==========================================================
GITHUB_USER = "Jmansilla98"
GITHUB_REPO = "overlay-cod-fecod"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
MATCHES_PATH = "matches"
OVERLAY_BASE_URL = f"https://{GITHUB_USER}.github.io/{GITHUB_REPO}"

# ==========================================================
# MAPAS
# ==========================================================
MAPAS = {
    "HP": ["Blackheart", "Colossus", "Den", "Exposure", "Scar"],
    "SnD": ["Colossus", "Den", "Exposure", "Raid", "Scar"],
    "Overload": ["Den", "Exposure", "Scar"]
}

COLORES = {
    "HP": discord.Color.red(),
    "SnD": discord.Color.gold(),
    "Overload": discord.Color.purple()
}

BANDOS = ["Ataque", "Defensa"]

# ==========================================================
# FLUJO PICK & BAN (CORRECTO)
# ==========================================================
FORMATOS = {
    "bo3": [
        ("ban","HP","A"),("ban","HP","B"),("pick","HP","A"),("side","HP","B"),
        ("ban","SnD","B"),("ban","SnD","A"),("pick","SnD","B"),("side","SnD","A"),
        ("ban","Overload","A"),("ban","Overload","B"),("side","Overload","A")
    ],
    "bo5": [
        ("ban","HP","A"),("ban","HP","B"),("pick","HP","A"),("side","HP","B"),
        ("pick","HP","B"),("side","HP","A"),
        ("ban","SnD","B"),("ban","SnD","A"),("pick","SnD","B"),("side","SnD","A"),
        ("pick","SnD","A"),("side","SnD","B"),
        ("ban","Overload","A"),("ban","Overload","B"),("side","Overload","A")
    ]
}

# ==========================================================
# ESTADO POR CANAL
# ==========================================================
matches = {}

def es_arbitro(user: discord.Member) -> bool:
    return any(r.name == ROL_ARBITRO for r in user.roles)

def needed_wins(formato: str) -> int:
    return 2 if formato == "bo3" else 3

def serie_score(match):
    a = sum(1 for r in match["resultados"] if r["winner"] == "A")
    b = sum(1 for r in match["resultados"] if r["winner"] == "B")
    return a, b

# ==========================================================
# GITHUB HELPERS
# ==========================================================
def gh_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

def subir_overlay(channel_id: int, match):
    if not GITHUB_TOKEN:
        return  # sin token, no sube (sin reventar el bot)

    payload = {
        "channel": channel_id,
        "equipo_a": match["equipos"]["A"].name,
        "equipo_b": match["equipos"]["B"].name,
        "mapas": [{"modo": m, "mapa": mp} for (m, mp) in match.get("mapas_finales", [])],
        "resultados": match.get("resultados", []),
        "reclamacion": bool(match.get("reclamacion", False))
    }

    url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{MATCHES_PATH}/{channel_id}.json"
    content = base64.b64encode(json.dumps(payload, indent=2).encode()).decode()

    r = requests.get(url, headers=gh_headers(), timeout=10)
    sha = r.json().get("sha") if r.status_code == 200 else None

    body = {"message": "update overlay", "content": content}
    if sha:
        body["sha"] = sha

    requests.put(url, headers=gh_headers(), json=body, timeout=10)

# ==========================================================
# CONSTRUCCIÓN MAPAS FINALES (ORDEN RESULTADOS CORRECTO)
# ==========================================================
def construir_mapas_finales(match):
    hp_picks = [x for x in match["mapas_picked"] if x[0] == "HP"]
    snd_picks = [x for x in match["mapas_picked"] if x[0] == "SnD"]

    banned_overload = set(match["bans"]["Overload"])
    overload_restantes = [m for m in MAPAS["Overload"] if m not in banned_overload]
    overload = overload_restantes[0] if overload_restantes else MAPAS["Overload"][0]

    if match["formato"] == "bo3":
        match["mapas_finales"] = [
            hp_picks[0],
            snd_picks[0],
            ("Overload", overload)
        ]
    else:
        match["mapas_finales"] = [
            hp_picks[0],     # Mapa 1
            snd_picks[0],    # Mapa 2
            ("Overload", overload),  # Mapa 3
            hp_picks[1],     # Mapa 4
            snd_picks[1]     # Mapa 5
        ]

# ==========================================================
# EMBEDS
# ==========================================================
def embed_turno(match):
    accion, modo, equipo = match["flujo"][match["paso"]]
    e = discord.Embed(title="🎮 PICK & BAN", color=COLORES[modo])
    e.add_field(name="Acción", value=accion.upper(), inline=True)
    e.add_field(name="Modo", value=modo, inline=True)
    e.add_field(name="Turno", value=match["equipos"][equipo].mention, inline=True)
    e.add_field(
        name="Equipos",
        value=f"🔵 {match['equipos']['A'].mention}\n🔴 {match['equipos']['B'].mention}",
        inline=False
    )
    e.set_footer(text=f"Paso {match['paso']+1}/{len(match['flujo'])}")
    return e

def embed_series(match):
    a_w, b_w = serie_score(match)
    e = discord.Embed(title="📋 Mapas de la serie", color=discord.Color.blurple())
    if not match.get("mapas_finales"):
        e.description = "Aún no se han definido los mapas."
        return e
    lines = []
    for i, (modo, mapa) in enumerate(match["mapas_finales"], start=1):
        lines.append(f"**Mapa {i}:** {modo} — {mapa}")
    e.description = "\n".join(lines)
    e.add_field(name="Marcador", value=f"{match['equipos']['A'].name} **{a_w}** — **{b_w}** {match['equipos']['B'].name}", inline=False)
    return e

def embed_resultado(match, idx):
    modo, mapa = match["mapas_finales"][idx]
    e = discord.Embed(
        title=f"📝 Introducir resultado — Mapa {idx+1}",
        description=f"**{modo} — {mapa}**",
        color=COLORES[modo]
    )
    e.add_field(name=match["equipos"]["A"].name, value="—", inline=True)
    e.add_field(name=match["equipos"]["B"].name, value="—", inline=True)
    e.set_footer(text="Solo el árbitro puede introducir resultados")
    return e

def embed_final(match):
    a_w, b_w = serie_score(match)
    ganador = match["equipos"]["A"].name if a_w > b_w else match["equipos"]["B"].name
    e = discord.Embed(title=f"🏁 Serie finalizada — Gana {ganador}", color=discord.Color.green())
    e.add_field(name="Marcador", value=f"{match['equipos']['A'].name} **{a_w}** — **{b_w}** {match['equipos']['B'].name}", inline=False)
    return e

# ==========================================================
# VIEWS / BOTONES (PICK & BAN)
# ==========================================================
class MapaButton(discord.ui.Button):
    def __init__(self, mapa, modo, channel_id):
        match = matches[channel_id]
        disabled = (mapa in match["usados"][modo])
        super().__init__(label=mapa, style=discord.ButtonStyle.primary, disabled=disabled)
        self.mapa = mapa
        self.modo = modo
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        match = matches.get(self.channel_id)
        if not match:
            return await interaction.response.send_message("❌ No hay partido activo", ephemeral=True)

        accion, modo, equipo = match["flujo"][match["paso"]]

        # turno correcto
        if match["equipos"][equipo] not in interaction.user.roles:
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)

        # evitar doble click / carreras
        if self.mapa in match["usados"][modo]:
            return await interaction.response.send_message("❌ Ese mapa ya no está disponible", ephemeral=True)

        match["usados"][modo].add(self.mapa)

        if accion == "ban":
            match["bans"][modo].append(self.mapa)

        if accion == "pick":
            match["mapas_picked"].append((modo, self.mapa))

        match["paso"] += 1
        await avanzar_pyb(interaction)

class MapaView(discord.ui.View):
    def __init__(self, modo, channel_id):
        super().__init__(timeout=None)
        for m in MAPAS[modo]:
            self.add_item(MapaButton(m, modo, channel_id))

class BandoButton(discord.ui.Button):
    def __init__(self, label, channel_id):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        match = matches.get(self.channel_id)
        if not match:
            return await interaction.response.send_message("❌ No hay partido activo", ephemeral=True)

        accion, modo, equipo = match["flujo"][match["paso"]]

        if match["equipos"][equipo] not in interaction.user.roles:
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)

        # guardamos el bando elegido por mapa (opcional)
        match["sides"].append({
            "modo": modo,
            "choice": self.label,
            "by": "A" if match["equipos"]["A"] in interaction.user.roles else "B"
        })

        match["paso"] += 1
        await avanzar_pyb(interaction)

class BandoView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.add_item(BandoButton("Ataque", channel_id))
        self.add_item(BandoButton("Defensa", channel_id))

# ==========================================================
# RESULTADOS (MODAL + VIEW)
# ==========================================================
class ResultadoModal(discord.ui.Modal, title="Introducir resultado"):
    a = discord.ui.TextInput(label="Equipo A")
    b = discord.ui.TextInput(label="Equipo B")

    def __init__(self, channel_id):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        match = matches.get(self.channel_id)
        if not match:
            return await interaction.response.send_message("❌ No hay partido activo", ephemeral=True)

        if not es_arbitro(interaction.user):
            return await interaction.response.send_message("⛔ Solo árbitros", ephemeral=True)

        if not self.a.value.isdigit() or not self.b.value.isdigit():
            return await interaction.response.send_message("❌ Valores inválidos", ephemeral=True)

        ai = int(self.a.value)
        bi = int(self.b.value)
        if ai == bi:
            return await interaction.response.send_message("❌ No puede haber empate", ephemeral=True)

        idx = len(match["resultados"])
        modo, mapa = match["mapas_finales"][idx]

        match["resultados"].append({
            "map_index": idx + 1,
            "modo": modo,
            "mapa": mapa,
            "a": ai,
            "b": bi,
            "winner": "A" if ai > bi else "B"
        })

        # subir overlay tras cada resultado
        try:
            subir_overlay(self.channel_id, match)
        except Exception:
            pass

        a_w, b_w = serie_score(match)
        if a_w >= needed_wins(match["formato"]) or b_w >= needed_wins(match["formato"]):
            # final
            await interaction.response.edit_message(
                embeds=[embed_series(match), embed_final(match)],
                view=ReclamacionView(self.channel_id)
            )
            return

        # siguiente mapa
        next_idx = len(match["resultados"])
        await interaction.response.edit_message(
            embeds=[embed_series(match), embed_resultado(match, next_idx)],
            view=ResultadoView(self.channel_id)
        )

class ResultadoButton(discord.ui.Button):
    def __init__(self, channel_id):
        super().__init__(label="Introducir resultado", style=discord.ButtonStyle.success)
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        if not es_arbitro(interaction.user):
            return await interaction.response.send_message("⛔ Solo árbitros", ephemeral=True)
        await interaction.response.send_modal(ResultadoModal(self.channel_id))

class ResultadoView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.add_item(ResultadoButton(channel_id))

# ==========================================================
# RECLAMACIÓN / SUBIDA (PLACEHOLDER)
# ==========================================================
class EditarResultadosButton(discord.ui.Button):
    def __init__(self, channel_id):
        super().__init__(label="✏️ Editar resultados", style=discord.ButtonStyle.secondary)
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        match = matches.get(self.channel_id)
        if not match:
            return await interaction.response.send_message("❌ No hay partido activo", ephemeral=True)
        if not es_arbitro(interaction.user):
            return await interaction.response.send_message("⛔ Solo árbitros", ephemeral=True)

        match["resultados"].clear()
        await interaction.response.edit_message(
            embeds=[embed_series(match), embed_resultado(match, 0)],
            view=ResultadoView(self.channel_id)
        )

class SubirPartidoButton(discord.ui.Button):
    def __init__(self, channel_id):
        super().__init__(label="⬆️ Subir partido", style=discord.ButtonStyle.success)
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        if not es_arbitro(interaction.user):
            return await interaction.response.send_message("⛔ Solo árbitros", ephemeral=True)
        await interaction.response.send_message("📤 Enviado a Challonge (placeholder)")

class ReclamacionButton(discord.ui.Button):
    def __init__(self, channel_id):
        super().__init__(label="🚨 Reclamación", style=discord.ButtonStyle.danger)
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        match = matches.get(self.channel_id)
        if not match:
            return await interaction.response.send_message("❌ No hay partido activo", ephemeral=True)

        match["reclamacion"] = True
        try:
            subir_overlay(self.channel_id, match)
        except Exception:
            pass

        await interaction.response.send_message("🎫 Ticket de reclamación creado (placeholder)")

        # acciones árbitro (placeholder)
        await interaction.channel.send(
            "⚖️ Acciones del árbitro:",
            view=ArbitroAccionesView(self.channel_id)
        )

class ArbitroAccionesView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.add_item(EditarResultadosButton(channel_id))
        self.add_item(SubirPartidoButton(channel_id))

class ReclamacionView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=10)
        self.channel_id = channel_id
        self.add_item(ReclamacionButton(channel_id))

    async def on_timeout(self):
        self.clear_items()
        self.add_item(SubirPartidoButton(self.channel_id))

# ==========================================================
# FLUJO PYB
# ==========================================================
async def avanzar_pyb(interaction: discord.Interaction):
    match = matches.get(interaction.channel.id)
    if not match:
        return

    # fin de PyB
    if match["paso"] >= len(match["flujo"]):
        construir_mapas_finales(match)

        # subir overlay ya con mapas (antes de resultados)
        try:
            subir_overlay(interaction.channel.id, match)
        except Exception:
            pass

        # mandar links de overlay AL CHAT (aquí)
        await interaction.channel.send(
            f"🎥 Overlays POV:\n"
            f"A: {OVERLAY_BASE_URL}/pov.html?match={interaction.channel.id}&team=A\n"
            f"B: {OVERLAY_BASE_URL}/pov.html?match={interaction.channel.id}&team=B"
        )

        # arrancar resultados con doble embed
        await interaction.response.edit_message(
            embeds=[embed_series(match), embed_resultado(match, 0)],
            view=ResultadoView(interaction.channel.id)
        )
        return

    accion, modo, _ = match["flujo"][match["paso"]]
    view = MapaView(modo, interaction.channel.id) if accion in ("ban", "pick") else BandoView(interaction.channel.id)

    await interaction.response.edit_message(
        embed=embed_turno(match),
        view=view
    )

# ==========================================================
# COMANDO PARTIDO
# ==========================================================
@bot.command()
async def setpartido(ctx, equipo_a: discord.Role, equipo_b: discord.Role, formato: str):
    formato = formato.lower()
    if formato not in FORMATOS:
        return await ctx.send("Formato inválido (bo3/bo5)")

    matches[ctx.channel.id] = {
        "equipos": {"A": equipo_a, "B": equipo_b},
        "flujo": FORMATOS[formato],
        "formato": formato,
        "paso": 0,
        "usados": {"HP": set(), "SnD": set(), "Overload": set()},
        "bans": {"HP": [], "SnD": [], "Overload": []},
        "mapas_picked": [],
        "mapas_finales": [],
        "sides": [],
        "resultados": [],
        "reclamacion": False
    }

    accion, modo, _ = matches[ctx.channel.id]["flujo"][0]
    await ctx.send(embed=embed_turno(matches[ctx.channel.id]), view=MapaView(modo, ctx.channel.id))

# ==========================================================
# ARRANQUE
# ==========================================================
if __name__ == "__main__":
    threading.Thread(target=run_tcp_healthcheck, daemon=True).start()
    bot.run(os.getenv("DISCORD_TOKEN"))
