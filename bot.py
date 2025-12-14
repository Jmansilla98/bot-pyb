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
# GITHUB / OVERLAY CONFIG
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

# ==========================================================
# PICK & BAN STEPS
# ==========================================================
FORMATOS = {
    "bo3": [
        ("ban", "HP", "A"),
        ("ban", "HP", "B"),
        ("pick", "HP", "A"),
        ("side", "HP", "B"),
        ("ban", "SnD", "B"),
        ("ban", "SnD", "A"),
        ("pick", "SnD", "B"),
        ("side", "SnD", "A"),
        ("ban", "Overload", "A"),
        ("ban", "Overload", "B"),
        ("side", "Overload", "A"),
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
        ("side", "Overload", "A"),
    ]
}

# ==========================================================
# STATE
# ==========================================================
matches = {}

# ==========================================================
# GITHUB
# ==========================================================
def gh_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

def subir_overlay(channel_id, data):
    url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{MATCHES_PATH}/{channel_id}.json"
    payload_data = {
        "equipos": data["equipos"],
        "mapas": data["mapas_finales"],
        "resultados": data["resultados"],
        "reclamacion": data["reclamacion"]
    }
    content = base64.b64encode(json.dumps(payload_data, indent=2).encode()).decode()
    r = requests.get(url, headers=gh_headers())
    sha = r.json().get("sha") if r.status_code == 200 else None
    payload = {"message": "Actualizar overlay", "content": content}
    if sha:
        payload["sha"] = sha
    requests.put(url, headers=gh_headers(), json=payload)

# ==========================================================
# EMBED
# ==========================================================
def embed_turno(match):
    accion, modo, equipo = FORMATOS[match["formato"]][match["paso"]]
    e = discord.Embed(
        title="🎮 PICK & BAN",
        description=f"**Acción:** {accion.upper()}\n**Modo:** {modo}",
        color=discord.Color.blurple()
    )
    e.add_field(name="Turno", value=match["equipos"][equipo].mention)
    return e

# ==========================================================
# UI
# ==========================================================
class MapaButton(discord.ui.Button):
    def __init__(self, mapa, modo, channel_id):
        super().__init__(label=mapa, style=discord.ButtonStyle.primary)
        self.mapa = mapa
        self.modo = modo
        self.channel_id = channel_id

    async def callback(self, interaction):
        match = matches[self.channel_id]
        if self.mapa in match["baneados"][self.modo]:
            return await interaction.response.send_message("Mapa baneado", ephemeral=True)

        accion, modo, equipo = FORMATOS[match["formato"]][match["paso"]]
        if accion == "ban":
            match["baneados"][modo].add(self.mapa)
        elif accion == "pick":
            match["mapas_finales"].append(f"{modo} - {self.mapa}")

        match["paso"] += 1
        await avanzar_pyb(interaction)

class MapaView(discord.ui.View):
    def __init__(self, modo, channel_id):
        super().__init__(timeout=None)
        for m in MAPAS[modo]:
            self.add_item(MapaButton(m, modo, channel_id))

class BandoButton(discord.ui.Button):
    def __init__(self, bando, channel_id):
        super().__init__(label=bando, style=discord.ButtonStyle.secondary)
        self.channel_id = channel_id

    async def callback(self, interaction):
        match = matches[self.channel_id]
        match["paso"] += 1
        await avanzar_pyb(interaction)

class BandoView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.add_item(BandoButton("Ataque", channel_id))
        self.add_item(BandoButton("Defensa", channel_id))

# ==========================================================
# FLOW
# ==========================================================
async def avanzar_pyb(interaction):
    match = matches[interaction.channel.id]

    if match["paso"] >= len(FORMATOS[match["formato"]]):
        subir_overlay(interaction.channel.id, match)
        return await interaction.response.send_message(
            "📝 Introduce resultados",
            view=ResultadoView(interaction.channel.id)
        )

    accion, modo, _ = FORMATOS[match["formato"]][match["paso"]]
    if accion in ["ban", "pick"]:
        await interaction.response.edit_message(
            embed=embed_turno(match),
            view=MapaView(modo, interaction.channel.id)
        )
    else:
        await interaction.response.edit_message(
            embed=embed_turno(match),
            view=BandoView(interaction.channel.id)
        )

# ==========================================================
# RESULTADOS
# ==========================================================
class ResultadoModal(discord.ui.Modal, title="Resultado"):
    a = discord.ui.TextInput(label="Equipo A")
    b = discord.ui.TextInput(label="Equipo B")

    def __init__(self, channel_id):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction):
        match = matches[self.channel_id]
        match["resultados"].append({"A": int(self.a.value), "B": int(self.b.value)})
        subir_overlay(self.channel_id, match)
        await interaction.response.send_message("Resultado guardado")

class ResultadoButton(discord.ui.Button):
    def __init__(self, channel_id):
        super().__init__(label="Introducir resultado", style=discord.ButtonStyle.success)
        self.channel_id = channel_id

    async def callback(self, interaction):
        await interaction.response.send_modal(ResultadoModal(self.channel_id))

class ResultadoView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.add_item(ResultadoButton(channel_id))

# ==========================================================
# COMMAND
# ==========================================================
@bot.command()
async def setpartido(ctx, team_a: discord.Role, team_b: discord.Role, formato: str):
    formato = formato.lower()
    if formato not in FORMATOS:
        return await ctx.send("Formato inválido")

    matches[ctx.channel.id] = {
        "equipos": {"A": team_a, "B": team_b},
        "formato": formato,
        "paso": 0,
        "baneados": {"HP": set(), "SnD": set(), "Overload": set()},
        "mapas_finales": [],
        "resultados": [],
        "reclamacion": False
    }

    accion, modo, _ = FORMATOS[formato][0]
    await ctx.send(
        embed=embed_turno(matches[ctx.channel.id]),
        view=MapaView(modo, ctx.channel.id)
    )

# ==========================================================
# START
# ==========================================================
if __name__ == "__main__":
    threading.Thread(target=run_tcp_healthcheck, daemon=True).start()
    bot.run(os.getenv("DISCORD_TOKEN"))
