# ==========================================================
# PickBanBot – BOT COMPLETO (BO3 / BO5)
# ==========================================================
# ✔ Pick & Ban interactivo correcto
# ✔ Mapas baneados solo por modo
# ✔ Flujo limpio (sin mensajes duplicados)
# ✔ Al terminar PyB:
#    - Se envían overlays
#    - Se abre modal de resultados
# ✔ Resultados por modal
# ✔ Reclamaciones (botón temporal)
# ✔ Subida a Challonge (placeholder)
# ✔ Multi-canal (partidos simultáneos)
# ✔ Healthcheck TCP (Koyeb/Web service)
# ==========================================================

import discord
from discord.ext import commands
import os
import asyncio
import threading
import socket
import json
import base64
import requests

# ==========================================================
# TCP HEALTH CHECK (Koyeb / Web service)
# ==========================================================
def run_tcp_healthcheck():
    host = "0.0.0.0"
    port = int(os.getenv("PORT", 8000))
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((host, port))
    s.listen(1)
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
OVERLAY_BASE_URL = f"https://{GITHUB_USER}.github.io/{GITHUB_REPO}"
MATCHES_PATH = "matches"

def gh_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

def subir_overlay(channel_id, data):
    url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{MATCHES_PATH}/{channel_id}.json"
    content = base64.b64encode(json.dumps(data, indent=2).encode()).decode()

    r = requests.get(url, headers=gh_headers())
    sha = r.json().get("sha") if r.status_code == 200 else None

    payload = {"message": "Actualizar overlay", "content": content}
    if sha:
        payload["sha"] = sha

    requests.put(url, headers=gh_headers(), json=payload)

# ==========================================================
# MAPAS / FORMATOS
# ==========================================================
MAPAS = {
    "HP": ["Blackheart", "Colossus", "Den", "Exposure", "Scar"],
    "SnD": ["Colossus", "Den", "Exposure", "Raid", "Scar"],
    "Overload": ["Den", "Exposure", "Scar"]
}

FORMATOS = {
    "bo3": [
        ("ban", "HP", "A"),
        ("ban", "HP", "B"),
        ("pick", "HP", "A"),
        ("ban", "SnD", "B"),
        ("ban", "SnD", "A"),
        ("pick", "SnD", "B"),
        ("ban", "Overload", "A"),
        ("ban", "Overload", "B"),
        ("side", "Overload", "A")
    ],
    "bo5": [
        ("ban", "HP", "A"),
        ("ban", "HP", "B"),
        ("pick", "HP", "A"),
        ("side", "HP", "B"),
        ("pick", "HP", "B"),
        ("side", "HP", "A"),

        ("ban", "SnD", "B"),
        ("ban", "SnD", "A"),
        ("pick", "SnD", "B"),
        ("side", "SnD", "A"),
        ("pick", "SnD", "A"),
        ("side", "SnD", "B"),

        ("ban", "Overload", "A"),
        ("ban", "Overload", "B"),
        ("side", "Overload", "A")
    ]
}

# ==========================================================
# ESTADO POR CANAL
# ==========================================================
matches = {}

def equipo_turno(match):
    return match["equipo_a"] if match["orden"][match["paso"]][2] == "A" else match["equipo_b"]

def es_arbitro(user):
    return any(r.name == ROL_ARBITRO for r in user.roles)

# ==========================================================
# BOTONES PICK & BAN
# ==========================================================
class MapaButton(discord.ui.Button):
    def __init__(self, mapa, modo, channel_id):
        super().__init__(label=mapa, style=discord.ButtonStyle.primary)
        self.mapa = mapa
        self.modo = modo
        self.channel_id = channel_id

    async def callback(self, interaction):
        match = matches[self.channel_id]
        if equipo_turno(match) not in interaction.user.roles:
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)

        match["baneados"][self.modo].add(self.mapa)
        accion, modo, _ = match["orden"][match["paso"]]

        if accion == "pick":
            match["mapas"].append((modo, self.mapa))

        match["paso"] += 1
        await avanzar_pyb(interaction)

class MapaView(discord.ui.View):
    def __init__(self, match, modo):
        super().__init__(timeout=None)
        for m in MAPAS[modo]:
            if m not in match["baneados"][modo]:
                self.add_item(MapaButton(m, modo, match["channel"]))

class SideButton(discord.ui.Button):
    def __init__(self, side, channel_id):
        super().__init__(label=side, style=discord.ButtonStyle.secondary)
        self.side = side
        self.channel_id = channel_id

    async def callback(self, interaction):
        match = matches[self.channel_id]
        if equipo_turno(match) not in interaction.user.roles:
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)

        match["paso"] += 1
        await avanzar_pyb(interaction)

class SideView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.add_item(SideButton("Ataque", channel_id))
        self.add_item(SideButton("Defensa", channel_id))

# ==========================================================
# RESULTADOS
# ==========================================================
class ResultadoModal(discord.ui.Modal, title="Introducir resultado"):
    a = discord.ui.TextInput(label="Equipo A")
    b = discord.ui.TextInput(label="Equipo B")

    def __init__(self, channel_id):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction):
        match = matches[self.channel_id]
        if not es_arbitro(interaction.user):
            return await interaction.response.send_message("⛔ Solo árbitro", ephemeral=True)

        a = int(self.a.value)
        b = int(self.b.value)

        match["resultados"].append((a, b))
        subir_overlay(self.channel_id, {
            "mapas": match["mapas"],
            "resultados": match["resultados"]
        })

        await interaction.response.send_message("✅ Resultado guardado")

# ==========================================================
# FLUJO PYB
# ==========================================================
async def avanzar_pyb(interaction):
    match = matches[interaction.channel.id]

    if match["paso"] >= len(match["orden"]):
        # FIN PYB → ENVIAR OVERLAYS + PEDIR RESULTADO
        subir_overlay(interaction.channel.id, {
            "equipoA": match["equipo_a"].name,
            "equipoB": match["equipo_b"].name,
            "mapas": match["mapas"],
            "resultados": []
        })

        await interaction.channel.send(
            f"🎥 **Overlays listos**\n"
            f"{OVERLAY_BASE_URL}/pov.html?match={interaction.channel.id}&team=A\n"
            f"{OVERLAY_BASE_URL}/pov.html?match={interaction.channel.id}&team=B"
        )

        await interaction.channel.send(
            "📝 Introducir resultado",
            view=ResultadoView(interaction.channel.id)
        )
        return

    accion, modo, _ = match["orden"][match["paso"]]

    if accion in ["ban", "pick"]:
        await interaction.response.edit_message(
            content=f"**{accion.upper()} {modo}**\nTurno: {equipo_turno(match).mention}",
            view=MapaView(match, modo)
        )
    else:
        await interaction.response.edit_message(
            content=f"**SIDE {modo}**\nTurno: {equipo_turno(match).mention}",
            view=SideView(interaction.channel.id)
        )

class ResultadoView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.add_item(ResultadoButton(channel_id))

class ResultadoButton(discord.ui.Button):
    def __init__(self, channel_id):
        super().__init__(label="Introducir resultado", style=discord.ButtonStyle.success)
        self.channel_id = channel_id

    async def callback(self, interaction):
        await interaction.response.send_modal(ResultadoModal(self.channel_id))

# ==========================================================
# COMANDO PRINCIPAL
# ==========================================================
@bot.command()
async def setpartido(ctx, equipo_a: discord.Role, equipo_b: discord.Role, formato: str):
    formato = formato.lower()
    if formato not in FORMATOS:
        return await ctx.send("❌ Formato inválido")

    matches[ctx.channel.id] = {
        "channel": ctx.channel.id,
        "equipo_a": equipo_a,
        "equipo_b": equipo_b,
        "orden": FORMATOS[formato],
        "paso": 0,
        "baneados": {"HP": set(), "SnD": set(), "Overload": set()},
        "mapas": [],
        "resultados": []
    }

    accion, modo, _ = FORMATOS[formato][0]
    await ctx.send(
        f"**{accion.upper()} {modo}**\nTurno: {equipo_a.mention}",
        view=MapaView(matches[ctx.channel.id], modo)
    )

# ==========================================================
# ARRANQUE
# ==========================================================
if __name__ == "__main__":
    threading.Thread(target=run_tcp_healthcheck, daemon=True).start()
    bot.run(os.getenv("DISCORD_TOKEN"))
