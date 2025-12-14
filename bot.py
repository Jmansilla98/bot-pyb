import discord
from discord.ext import commands
import os
import asyncio
import re
import json
import base64
import requests

# ==========================================================
# CONFIGURACIÓN GENERAL
# ==========================================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

ROL_ARBITRO = "team1"

# ==========================================================
# OVERLAY / GITHUB CONFIG
# ==========================================================
GITHUB_USER = "Jmansilla98"
GITHUB_REPO = "overlay-cod-fecod"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

OVERLAY_BASE_URL = f"https://{GITHUB_USER}.github.io/{GITHUB_REPO}"
MATCHES_PATH = "matches"
HISTORY_PATH = "history"

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
# FORMATOS PICK & BAN
# ==========================================================
FORMATOS = {
    "bo3": [
        ("ban","HP","A"),("ban","HP","B"),("pick","HP","A"),("side","HP","B"),
        ("ban","SnD","B"),("ban","SnD","A"),("pick","SnD","B"),("side","SnD","A"),
        ("ban","Overload","A"),("ban","Overload","B"),("side","Overload","A"),
    ],
    "bo5": [
        ("ban","HP","A"),("ban","HP","B"),("pick","HP","A"),("side","HP","B"),
        ("pick","HP","B"),("side","HP","A"),
        ("ban","SnD","B"),("ban","SnD","A"),("pick","SnD","B"),("side","SnD","A"),
        ("pick","SnD","A"),("side","SnD","B"),
        ("ban","Overload","A"),("ban","Overload","B"),("side","Overload","A"),
    ]
}

# ==========================================================
# ESTADO POR CANAL
# ==========================================================
pyb_channels = {}

def get_pyb(cid):
    return pyb_channels.get(cid)

def es_arbitro(user):
    return ROL_ARBITRO in [r.name for r in user.roles]

def needed_wins(formato):
    return 2 if formato == "bo3" else 3

# ==========================================================
# GITHUB HELPERS
# ==========================================================
def github_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

def subir_json(path, data, message):
    url = f"{GITHUB_API}/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{path}"
    content = base64.b64encode(json.dumps(data, indent=2).encode()).decode()

    r = requests.get(url, headers=github_headers())
    sha = r.json().get("sha") if r.status_code == 200 else None

    payload = {
        "message": message,
        "content": content
    }
    if sha:
        payload["sha"] = sha

    requests.put(url, headers=github_headers(), json=payload)

def borrar_archivo(path):
    url = f"{GITHUB_API}/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{path}"
    r = requests.get(url, headers=github_headers())
    if r.status_code != 200:
        return
    sha = r.json()["sha"]
    requests.delete(url, headers=github_headers(), json={
        "message": "Eliminar overlay activo",
        "sha": sha
    })

# ==========================================================
# OVERLAY STATE
# ==========================================================
def actualizar_overlay(pyb, channel_id, terminado=False):
    a_wins = sum(1 for r in pyb["resultados"] if r["winner"] == "A")
    b_wins = sum(1 for r in pyb["resultados"] if r["winner"] == "B")

    data = {
        "channel": channel_id,
        "equipo_a": pyb["equipo_a"].name,
        "equipo_b": pyb["equipo_b"].name,
        "score_a": a_wins,
        "score_b": b_wins,
        "mapa_actual": None,
        "reclamacion": pyb.get("reclamacion", False),
        "finalizado": terminado
    }

    if not terminado and len(pyb["resultados"]) < len(pyb["mapas_finales"]):
        modo, mapa = pyb["mapas_finales"][len(pyb["resultados"])]
        data["mapa_actual"] = f"{modo} - {mapa}"

    subir_json(
        f"{MATCHES_PATH}/{channel_id}.json",
        data,
        "Actualizar estado del partido"
    )

async def limpiar_overlay(channel_id):
    await asyncio.sleep(180)
    borrar_archivo(f"{MATCHES_PATH}/{channel_id}.json")

# ==========================================================
# RESULTADOS
# ==========================================================
class ResultadoModal(discord.ui.Modal, title="Introducir resultado"):
    res_a = discord.ui.TextInput(label="Equipo A", max_length=4)
    res_b = discord.ui.TextInput(label="Equipo B", max_length=4)

    def __init__(self, pyb):
        super().__init__()
        self.pyb = pyb

    async def on_submit(self, interaction):
        if not es_arbitro(interaction.user):
            return await interaction.response.send_message("⛔ Solo árbitros", ephemeral=True)

        if not self.res_a.value.isdigit() or not self.res_b.value.isdigit():
            return await interaction.response.send_message("❌ Números inválidos", ephemeral=True)

        a = int(self.res_a.value)
        b = int(self.res_b.value)
        if a == b:
            return await interaction.response.send_message("❌ No empate", ephemeral=True)

        idx = len(self.pyb["resultados"])
        modo, mapa = self.pyb["mapas_finales"][idx]

        self.pyb["resultados"].append({
            "modo": modo,
            "mapa": mapa,
            "a": a,
            "b": b,
            "winner": "A" if a > b else "B"
        })

        actualizar_overlay(self.pyb, interaction.channel.id)

        a_w = sum(1 for r in self.pyb["resultados"] if r["winner"] == "A")
        b_w = sum(1 for r in self.pyb["resultados"] if r["winner"] == "B")

        if a_w >= needed_wins(self.pyb["formato"]) or b_w >= needed_wins(self.pyb["formato"]):
            actualizar_overlay(self.pyb, interaction.channel.id, terminado=True)
            asyncio.create_task(limpiar_overlay(interaction.channel.id))

            await interaction.response.send_message(
                f"🏆 Serie finalizada {a_w}-{b_w}\n\n"
                f"🎥 POV A:\n{OVERLAY_BASE_URL}/pov.html?match={interaction.channel.id}&team=A\n\n"
                f"🎥 POV B:\n{OVERLAY_BASE_URL}/pov.html?match={interaction.channel.id}&team=B"
            )
        else:
            await interaction.response.send_message("✅ Resultado guardado")

# ==========================================================
# COMANDOS
# ==========================================================
@bot.command()
async def setequipos(ctx, equipo_a: discord.Role, equipo_b: discord.Role):
    pyb_channels[ctx.channel.id] = {
        "equipo_a": equipo_a,
        "equipo_b": equipo_b,
        "formato": None,
        "mapas_finales": [],
        "resultados": []
    }
    await ctx.send("✅ Equipos definidos")

@bot.command()
async def startpyb(ctx, formato: str):
    pyb = get_pyb(ctx.channel.id)
    if not pyb:
        return await ctx.send("❌ Usa !setequipos primero")

    pyb["formato"] = formato.lower()
    pyb["mapas_finales"] = [
        ("HP","Blackheart"),
        ("SnD","Raid"),
        ("Overload","Exposure")
    ] if formato.lower() == "bo3" else [
        ("HP","Blackheart"),
        ("SnD","Raid"),
        ("Overload","Exposure"),
        ("HP","Den"),
        ("SnD","Scar")
    ]

    actualizar_overlay(pyb, ctx.channel.id)
    await ctx.send("🎮 Pick & Ban iniciado")

@bot.command()
async def resultado(ctx):
    pyb = get_pyb(ctx.channel.id)
    if not pyb:
        return
    await ctx.send_modal(ResultadoModal(pyb))

# ==========================================================
# RUN
# ==========================================================
bot.run(os.getenv("DISCORD_TOKEN"))
