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
# TCP SERVER (SOLO PARA HEALTH CHECK)
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
# ESTADO DE PARTIDOS (POR CANAL)
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
# BOTONES PICK & BAN
# ==========================================================
class PickButton(discord.ui.Button):
    def __init__(self, mapa, modo, channel_id):
        super().__init__(label=mapa, style=discord.ButtonStyle.primary)
        self.mapa = mapa
        self.modo = modo
        self.channel_id = channel_id

    async def callback(self, interaction):
        match = matches[self.channel_id]

        if self.mapa in match["mapas"]:
            return await interaction.response.send_message(
                "❌ Mapa ya seleccionado",
                ephemeral=True
            )

        match["mapas"].append(f"{self.modo} - {self.mapa}")

        if len(match["mapas"]) >= len(FORMATOS[match["formato"]]):
            await interaction.response.send_message("✅ Pick & Ban terminado")
            return

        siguiente_modo = FORMATOS[match["formato"]][len(match["mapas"])]
        await interaction.response.send_message(
            f"🗺️ {self.mapa} seleccionado\n➡️ Siguiente modo: **{siguiente_modo}**",
            view=PickView(siguiente_modo, self.channel_id)
        )

class PickView(discord.ui.View):
    def __init__(self, modo, channel_id):
        super().__init__(timeout=None)
        for m in MAPAS[modo]:
            self.add_item(PickButton(m, modo, channel_id))

# ==========================================================
# COMANDOS
# ==========================================================
@bot.command()
async def start(ctx, formato: str):
    formato = formato.lower()
    if formato not in FORMATOS:
        return await ctx.send("❌ Formato inválido")

    matches[ctx.channel.id] = {
        "formato": formato,
        "mapas": [],
        "scoreA": 0,
        "scoreB": 0,
        "reclamacion": False
    }

    primer_modo = FORMATOS[formato][0]
    await ctx.send(
        f"🎮 Pick & Ban iniciado ({formato.upper()})",
        view=PickView(primer_modo, ctx.channel.id)
    )

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

    data = {
        "mapas": match["mapas"],
        "scoreA": match["scoreA"],
        "scoreB": match["scoreB"],
        "reclamacion": match["reclamacion"]
    }

    subir_overlay(ctx.channel.id, data)
    await ctx.send("✅ Resultado guardado y overlay actualizado")

@bot.command()
async def reclamar(ctx):
    match = matches.get(ctx.channel.id)
    if not match:
        return await ctx.send("❌ No hay partido activo")

    match["reclamacion"] = True
    subir_overlay(ctx.channel.id, match)
    await ctx.send("🚨 Reclamación registrada")

# ==========================================================
# ARRANQUE
# ==========================================================
if __name__ == "__main__":
    # Arrancar TCP health check
    threading.Thread(target=run_tcp_healthcheck, daemon=True).start()

    # Arrancar bot Discord
    bot.run(os.getenv("DISCORD_TOKEN"))
