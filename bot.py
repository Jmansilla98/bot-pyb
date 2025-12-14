import discord
from discord.ext import commands
import os
import asyncio
import re
import json
import base64
import requests
from aiohttp import web

# ==========================================================
# CONFIGURACIÓN GENERAL
# ==========================================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

ROL_ARBITRO = "team1"

# ==========================================================
# HEALTH CHECK (WEB SERVICE)
# ==========================================================
async def healthcheck(request):
    return web.Response(text="OK")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()

# ==========================================================
# OVERLAY / GITHUB CONFIG
# ==========================================================
GITHUB_USER = "Jmansilla98"
GITHUB_REPO = "overlay-cod-fecod"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

OVERLAY_BASE_URL = f"https://{GITHUB_USER}.github.io/{GITHUB_REPO}"
MATCHES_PATH = "matches"
GITHUB_API = "https://api.github.com"

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

def rol_turno(pyb):
    _, _, eq = FORMATOS[pyb["formato"]][pyb["paso"]]
    return pyb["equipo_a"] if eq == "A" else pyb["equipo_b"]

# ==========================================================
# GITHUB HELPERS
# ==========================================================
def gh_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

def subir_json(channel_id, data):
    path = f"{MATCHES_PATH}/{channel_id}.json"
    url = f"{GITHUB_API}/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{path}"
    content = base64.b64encode(json.dumps(data, indent=2).encode()).decode()

    r = requests.get(url, headers=gh_headers())
    sha = r.json().get("sha") if r.status_code == 200 else None

    payload = {"message": "Actualizar overlay", "content": content}
    if sha:
        payload["sha"] = sha

    requests.put(url, headers=gh_headers(), json=payload)

def actualizar_overlay(pyb, channel_id):
    data = {
        "equipo_a": pyb["equipo_a"].name,
        "equipo_b": pyb["equipo_b"].name,
        "mapas": pyb["mapas_finales"],
        "reclamacion": pyb.get("reclamacion", False)
    }
    subir_json(channel_id, data)

# ==========================================================
# PICK & BAN INTERACTIVO
# ==========================================================
class MapaButton(discord.ui.Button):
    def __init__(self, mapa, pyb, modo):
        super().__init__(label=mapa, style=discord.ButtonStyle.primary)
        self.mapa = mapa
        self.pyb = pyb
        self.modo = modo

    async def callback(self, interaction):
        if rol_turno(self.pyb) not in interaction.user.roles:
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)

        accion, modo, _ = FORMATOS[self.pyb["formato"]][self.pyb["paso"]]

        if accion == "pick":
            self.pyb["mapas_finales"].append((modo, self.mapa))

        self.pyb["paso"] += 1
        actualizar_overlay(self.pyb, interaction.channel.id)

        if self.pyb["paso"] >= len(FORMATOS[self.pyb["formato"]]):
            return await interaction.response.edit_message(
                content="✅ Pick & Ban finalizado",
                view=None
            )

        await interaction.response.edit_message(
            content=f"Turno de {rol_turno(self.pyb).mention}",
            view=MapaView(self.pyb, modo)
        )

class MapaView(discord.ui.View):
    def __init__(self, pyb, modo):
        super().__init__(timeout=None)
        for m in MAPAS[modo]:
            self.add_item(MapaButton(m, pyb, modo))

# ==========================================================
# COMANDOS
# ==========================================================
@bot.command()
async def setequipos(ctx, equipo_a: discord.Role, equipo_b: discord.Role):
    pyb_channels[ctx.channel.id] = {
        "equipo_a": equipo_a,
        "equipo_b": equipo_b,
        "formato": None,
        "paso": 0,
        "mapas_finales": [],
        "reclamacion": False
    }
    await ctx.send("✅ Equipos definidos")

@bot.command()
async def startpyb(ctx, formato: str):
    pyb = get_pyb(ctx.channel.id)
    if not pyb:
        return await ctx.send("❌ Usa !setequipos primero")

    pyb["formato"] = formato
    pyb["paso"] = 0
    pyb["mapas_finales"] = []

    accion, modo, _ = FORMATOS[formato][0]
    await ctx.send(
        f"🎮 Pick & Ban iniciado – Turno de {rol_turno(pyb).mention}",
        view=MapaView(pyb, modo)
    )

@bot.command()
async def reclamar(ctx):
    pyb = get_pyb(ctx.channel.id)
    pyb["reclamacion"] = True
    actualizar_overlay(pyb, ctx.channel.id)
    await ctx.send("🚨 Reclamación registrada")

# ==========================================================
# ARRANQUE
# ==========================================================
@bot.event
async def on_ready():
    await start_webserver()
    print(f"Bot conectado como {bot.user}")

bot.run(os.getenv("DISCORD_TOKEN"))
