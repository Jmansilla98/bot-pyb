import discord
from discord.ext import commands
import os
import json
import base64
import requests
import socket
import threading

# ==========================================================
# TCP SERVER (SOLO PARA HEALTH CHECK – KOYEB)
# ==========================================================
def run_tcp_healthcheck():
    host = "0.0.0.0"
    port = int(os.getenv("PORT", 8000))

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((host, port))
    s.listen(1)

    while True:
        conn, addr = s.accept()
        conn.close()

# ==========================================================
# DISCORD CONFIG
# ==========================================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

ROL_ARBITRO = "team1"

# ==========================================================
# GITHUB / OVERLAY CONFIG
# ==========================================================
GITHUB_USER = "Jmansilla98"
GITHUB_REPO = "overlay-cod-fecod"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

OVERLAY_BASE_URL = f"https://{GITHUB_USER}.github.io/{GITHUB_REPO}"
MATCHES_PATH = "matches"

# ==========================================================
# MAPAS Y FORMATOS
# ==========================================================
MAPAS = {
    "HP": ["Blackheart", "Colossus", "Den", "Exposure", "Scar"],
    "SnD": ["Colossus", "Den", "Exposure", "Raid", "Scar"],
    "Overload": ["Den", "Exposure", "Scar"]
}

FORMATOS = {
    "bo3": ["HP", "SnD", "Overload"],
    "bo5": ["HP", "SnD", "Overload", "HP", "SnD"]
}

# ==========================================================
# ESTADO PARTIDOS (POR CANAL)
# ==========================================================
matches = {}

def is_ref(user):
    return any(r.name == ROL_ARBITRO for r in user.roles)

# ==========================================================
# GITHUB HELPERS
# ==========================================================
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

    payload = {
        "message": "Actualizar overlay",
        "content": content
    }
    if sha:
        payload["sha"] = sha

    requests.put(url, headers=gh_headers(), json=payload)

# ==========================================================
# PICK & BAN BUTTONS
# ==========================================================
class PickButton(discord.ui.Button):
    def __init__(self, mapa, modo, channel_id):
        super().__init__(
            label=f"{modo} · {mapa}",
            style=discord.ButtonStyle.primary
        )
        self.mapa = mapa
        self.modo = modo
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        match = matches.get(self.channel_id)
        if not match:
            return await interaction.response.send_message(
                "❌ No hay partido activo",
                ephemeral=True
            )

        if self.mapa in match["mapas"]:
            return await interaction.response.send_message(
                "❌ Ese mapa ya ha sido seleccionado",
                ephemeral=True
            )

        match["mapas"].append(self.mapa)

        # Si ya se han seleccionado todos los mapas
        if len(match["mapas"]) >= len(FORMATOS[match["formato"]]):
            subir_overlay(self.channel_id, build_overlay_data(match))
            return await interaction.response.edit_message(
                content="✅ **Pick & Ban terminado**",
                view=None
            )

        # Siguiente modo
        siguiente_modo = FORMATOS[match["formato"]][len(match["mapas"])]

        subir_overlay(self.channel_id, build_overlay_data(match))

        await interaction.response.edit_message(
            content=f"🗺️ **{self.mapa}** seleccionado\n➡️ Siguiente modo: **{siguiente_modo}**",
            view=PickView(siguiente_modo, self.channel_id)
        )

class PickView(discord.ui.View):
    def __init__(self, modo, channel_id):
        super().__init__(timeout=None)
        for m in MAPAS[modo]:
            self.add_item(PickButton(m, modo, channel_id))

# ==========================================================
# OVERLAY DATA BUILDER
# ==========================================================
def build_overlay_data(match):
    return {
        "equipoA": match["equipoA"].name,
        "equipoB": match["equipoB"].name,
        "formato": match["formato"],
        "mapas": match["mapas"],
        "scoreA": match["scoreA"],
        "scoreB": match["scoreB"],
        "reclamacion": match["reclamacion"]
    }

# ==========================================================
# COMANDO PRINCIPAL
# ==========================================================
@bot.command()
async def setpartido(ctx, equipoA: discord.Role, equipoB: discord.Role, formato: str):
    formato = formato.lower()
    if formato not in FORMATOS:
        return await ctx.send("❌ Formato inválido (usa bo3 o bo5)")

    matches[ctx.channel.id] = {
        "equipoA": equipoA,
        "equipoB": equipoB,
        "formato": formato,
        "mapas": [],
        "scoreA": 0,
        "scoreB": 0,
        "reclamacion": False
    }

    primer_modo = FORMATOS[formato][0]

    subir_overlay(ctx.channel.id, build_overlay_data(matches[ctx.channel.id]))

    await ctx.send(
        f"🎮 **Pick & Ban iniciado ({formato.upper()})**\n"
        f"🔵 {equipoA.mention} vs 🔴 {equipoB.mention}\n\n"
        f"➡️ Modo inicial: **{primer_modo}**",
        view=PickView(primer_modo, ctx.channel.id)
    )

# ==========================================================
# RESULTADOS
# ==========================================================
@bot.command()
async def resultado(ctx, a: int, b: int):
    match = matches.get(ctx.channel.id)
    if not match:
        return await ctx.send("❌ No hay partido activo")

    if a == b:
        return await ctx.send("❌ No puede haber empate")

    if a > b:
        match["scoreA"] += 1
    else:
        match["scoreB"] += 1

    subir_overlay(ctx.channel.id, build_overlay_data(match))
    await ctx.send("✅ Resultado guardado")

# ==========================================================
# RECLAMACIÓN
# ==========================================================
@bot.command()
async def reclamar(ctx):
    match = matches.get(ctx.channel.id)
    if not match:
        return await ctx.send("❌ No hay partido activo")

    match["reclamacion"] = True
    subir_overlay(ctx.channel.id, build_overlay_data(match))
    await ctx.send("🚨 **Reclamación registrada**")

# ==========================================================
# ARRANQUE
# ==========================================================
if __name__ == "__main__":
    threading.Thread(target=run_tcp_healthcheck, daemon=True).start()
    bot.run(os.getenv("DISCORD_TOKEN"))
