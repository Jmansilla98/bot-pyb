import discord
from discord.ext import commands
import os
import asyncio
import json
import base64
import requests
import re

# ==========================================================
# CONFIGURACIÓN GENERAL
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
GITHUB_API = "https://api.github.com"

# ==========================================================
# MAPAS / BANDOS
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
matches = {}

def get_match(cid):
    return matches.get(cid)

def es_arbitro(user):
    return ROL_ARBITRO in [r.name for r in user.roles]

def equipo_turno(match):
    _, _, eq = FORMATOS[match["formato"]][match["paso"]]
    return match["equipo_a"] if eq == "A" else match["equipo_b"]

# ==========================================================
# GITHUB HELPERS
# ==========================================================
def gh_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

def subir_json(path, data):
    url = f"{GITHUB_API}/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{path}"
    content = base64.b64encode(json.dumps(data, indent=2).encode()).decode()

    r = requests.get(url, headers=gh_headers())
    sha = r.json().get("sha") if r.status_code == 200 else None

    payload = {
        "message": "update overlay",
        "content": content
    }
    if sha:
        payload["sha"] = sha

    requests.put(url, headers=gh_headers(), json=payload)

# ==========================================================
# OVERLAY
# ==========================================================
def actualizar_overlay(match, channel_id, final=False):
    data = {
        "equipo_a": match["equipo_a"].name,
        "equipo_b": match["equipo_b"].name,
        "score_a": sum(1 for r in match["resultados"] if r["winner"] == "A"),
        "score_b": sum(1 for r in match["resultados"] if r["winner"] == "B"),
        "mapa_actual": None,
        "reclamacion": match["reclamacion"],
        "finalizado": final
    }

    if not final and len(match["resultados"]) < len(match["mapas_finales"]):
        modo, mapa = match["mapas_finales"][len(match["resultados"])]
        data["mapa_actual"] = f"{modo} - {mapa}"

    subir_json(f"{MATCHES_PATH}/{channel_id}.json", data)

# ==========================================================
# EMBEDS
# ==========================================================
def embed_turno(match):
    accion, modo, _ = FORMATOS[match["formato"]][match["paso"]]
    e = discord.Embed(
        title="🎮 PICK & BAN",
        description=f"**{accion.upper()} — {modo}**",
        color=COLORES[modo]
    )
    e.add_field(name="Turno", value=equipo_turno(match).mention)
    return e

def embed_resultado(match):
    i = len(match["resultados"])
    modo, mapa = match["mapas_finales"][i]
    e = discord.Embed(
        title=f"📝 Resultado Mapa {i+1}",
        description=f"{modo} — {mapa}",
        color=COLORES[modo]
    )
    return e

# ==========================================================
# BOTONES MAPAS
# ==========================================================
class MapaButton(discord.ui.Button):
    def __init__(self, mapa, match, modo):
        super().__init__(label=mapa, style=discord.ButtonStyle.primary)
        self.mapa = mapa
        self.match = match
        self.modo = modo

    async def callback(self, interaction):
        if equipo_turno(self.match) not in interaction.user.roles:
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)

        accion, _, _ = FORMATOS[self.match["formato"]][self.match["paso"]]
        self.match["usados"][self.modo].add(self.mapa)

        if accion == "pick":
            self.match["mapas_finales"].append((self.modo, self.mapa))

        self.match["paso"] += 1
        await avanzar_pyb(interaction)

class MapaView(discord.ui.View):
    def __init__(self, match, modo):
        super().__init__(timeout=None)
        for m in MAPAS[modo]:
            if m not in match["usados"][modo]:
                self.add_item(MapaButton(m, match, modo))

# ==========================================================
# BOTÓN RECLAMACIÓN
# ==========================================================
class ReclamacionButton(discord.ui.Button):
    def __init__(self, match):
        super().__init__(label="🚨 Reclamación", style=discord.ButtonStyle.danger)
        self.match = match

    async def callback(self, interaction):
        self.match["reclamacion"] = True
        actualizar_overlay(self.match, interaction.channel.id)
        await interaction.response.send_message("🚨 Reclamación registrada")

class ReclamacionView(discord.ui.View):
    def __init__(self, match):
        super().__init__(timeout=None)
        self.add_item(ReclamacionButton(match))

# ==========================================================
# RESULTADOS
# ==========================================================
class ResultadoModal(discord.ui.Modal, title="Resultado"):
    a = discord.ui.TextInput(label="Equipo A")
    b = discord.ui.TextInput(label="Equipo B")

    def __init__(self, match):
        super().__init__()
        self.match = match

    async def on_submit(self, interaction):
        a = int(self.a.value)
        b = int(self.b.value)
        modo, mapa = self.match["mapas_finales"][len(self.match["resultados"])]

        self.match["resultados"].append({
            "modo": modo,
            "mapa": mapa,
            "winner": "A" if a > b else "B"
        })

        actualizar_overlay(self.match, interaction.channel.id)

        await interaction.response.send_message("✅ Resultado guardado")

# ==========================================================
# FLUJO PICK & BAN
# ==========================================================
async def avanzar_pyb(interaction):
    match = get_match(interaction.channel.id)

    if match["paso"] >= len(FORMATOS[match["formato"]]):
        actualizar_overlay(match, interaction.channel.id)
        return await interaction.response.send_message(
            embed=embed_resultado(match),
            view=ReclamacionView(match)
        )

    accion, modo, _ = FORMATOS[match["formato"]][match["paso"]]
    await interaction.response.edit_message(
        embed=embed_turno(match),
        view=MapaView(match, modo)
    )

# ==========================================================
# COMANDOS
# ==========================================================
@bot.command()
async def setequipos(ctx, equipo_a: discord.Role, equipo_b: discord.Role):
    matches[ctx.channel.id] = {
        "equipo_a": equipo_a,
        "equipo_b": equipo_b,
        "formato": None,
        "paso": 0,
        "usados": {"HP": set(), "SnD": set(), "Overload": set()},
        "mapas_finales": [],
        "resultados": [],
        "reclamacion": False
    }
    await ctx.send("✅ Equipos definidos")

@bot.command()
async def startpyb(ctx, formato: str):
    match = get_match(ctx.channel.id)
    if not match:
        return await ctx.send("❌ Usa !setequipos primero")

    match["formato"] = formato.lower()
    match["paso"] = 0

    _, modo, _ = FORMATOS[formato.lower()][0]
    await ctx.send(
        embed=embed_turno(match),
        view=MapaView(match, modo)
    )

@bot.command()
async def resultado(ctx):
    match = get_match(ctx.channel.id)
    if not match:
        return
    await ctx.send_modal(ResultadoModal(match))

# ==========================================================
# RUN
# ==========================================================
bot.run(os.getenv("DISCORD_TOKEN"))
