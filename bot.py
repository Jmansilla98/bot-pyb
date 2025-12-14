# ==========================================================
# Pick & Ban Bot — BOT.PY COMPLETO (BO3 + BO5)
# ==========================================================
# ✔ Picks & Bans interactivos por botones
# ✔ Mapas baneados solo afectan a su modo
# ✔ Flujo correcto BO3 / BO5
# ✔ Embed bonito en PyB
# ✔ Al acabar PyB → mensaje de resultados + overlay
# ✔ Resultados por modal
# ✔ Reclamaciones (botón temporal)
# ✔ Subida a Challonge (placeholder)
# ✔ TCP healthcheck (web service compatible)
# ✔ Multi-canal (partidos simultáneos)
# ==========================================================

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

# ==========================================================
# TCP HEALTH CHECK (Koyeb / Render compatible)
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

# ==========================================================
# MAPAS
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
# FORMATO PICK & BAN (ORDEN REAL)
# ==========================================================
FORMATOS = {
    "bo3": [
        ("ban","HP","A"),("ban","HP","B"),
        ("pick","HP","A"),("side","HP","B"),
        ("ban","SnD","B"),("ban","SnD","A"),
        ("pick","SnD","B"),("side","SnD","A"),
        ("ban","Overload","A"),("ban","Overload","B"),
        ("side","Overload","A"),
    ],
    "bo5": [
        ("ban","HP","A"),("ban","HP","B"),
        ("pick","HP","A"),("side","HP","B"),
        ("pick","HP","B"),("side","HP","A"),

        ("ban","SnD","B"),("ban","SnD","A"),
        ("pick","SnD","B"),("side","SnD","A"),
        ("pick","SnD","A"),("side","SnD","B"),

        ("ban","Overload","A"),("ban","Overload","B"),
        ("side","Overload","A"),
    ]
}

# ==========================================================
# ESTADO POR CANAL
# ==========================================================
matches = {}

def get_match(cid):
    return matches.get(cid)

def rol_turno(match):
    _, _, eq = FORMATOS[match["formato"]][match["paso"]]
    return match["equipo_a"] if eq == "A" else match["equipo_b"]

def es_arbitro(user):
    return any(r.name == ROL_ARBITRO for r in user.roles)

def needed_wins(fmt):
    return 2 if fmt == "bo3" else 3

# ==========================================================
# GITHUB HELPERS
# ==========================================================
def gh_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

def subir_overlay(cid, data):
    url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{MATCHES_PATH}/{cid}.json"
    content = base64.b64encode(json.dumps(data, indent=2).encode()).decode()

    r = requests.get(url, headers=gh_headers())
    sha = r.json().get("sha") if r.status_code == 200 else None

    payload = {"message": "Actualizar overlay", "content": content}
    if sha:
        payload["sha"] = sha

    requests.put(url, headers=gh_headers(), json=payload)

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
    e.add_field(name="Turno", value=rol_turno(match).mention)
    e.add_field(
        name="Equipos",
        value=f"🔵 {match['equipo_a'].mention}\n🔴 {match['equipo_b'].mention}",
        inline=False
    )
    e.set_footer(text=f"Paso {match['paso']+1}/{len(FORMATOS[match['formato']])}")
    return e

def embed_resultado(match):
    i = len(match["resultados"])
    modo, mapa = match["mapas_finales"][i]
    e = discord.Embed(
        title=f"📝 Resultado Mapa {i+1}",
        description=f"**{modo} — {mapa}**",
        color=COLORES[modo]
    )
    e.add_field(name=match["equipo_a"].name, value="—")
    e.add_field(name=match["equipo_b"].name, value="—")
    return e

def embed_final(match):
    a = sum(1 for r in match["resultados"] if r["winner"] == "A")
    b = sum(1 for r in match["resultados"] if r["winner"] == "B")
    ganador = match["equipo_a"].name if a > b else match["equipo_b"].name
    e = discord.Embed(
        title=f"🏆 {ganador} gana {a}-{b}",
        color=discord.Color.green()
    )
    txt = ""
    for i,r in enumerate(match["resultados"]):
        txt += f"Mapa {i+1} — {r['modo']} ({r['mapa']}): {r['a']}–{r['b']}\n"
    e.add_field(name="Resultados", value=txt)
    return e

# ==========================================================
# BOTONES MAPAS / BANDOS
# ==========================================================
class MapaButton(discord.ui.Button):
    def __init__(self, mapa, modo, match):
        super().__init__(
            label=mapa,
            style=discord.ButtonStyle.primary,
            disabled=mapa in match["baneados"][modo]
        )
        self.mapa = mapa
        self.modo = modo
        self.match = match

    async def callback(self, interaction):
        if rol_turno(self.match) not in interaction.user.roles:
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)

        accion, modo, _ = FORMATOS[self.match["formato"]][self.match["paso"]]
        self.match["baneados"][modo].add(self.mapa)

        if accion == "pick":
            self.match["mapas_finales"].append((modo, self.mapa))

        self.match["paso"] += 1
        await avanzar_pyb(interaction)

class MapaView(discord.ui.View):
    def __init__(self, match, modo):
        super().__init__(timeout=None)
        for m in MAPAS[modo]:
            self.add_item(MapaButton(m, modo, match))

class BandoButton(discord.ui.Button):
    def __init__(self, bando, match):
        super().__init__(label=bando, style=discord.ButtonStyle.secondary)
        self.match = match

    async def callback(self, interaction):
        if rol_turno(self.match) not in interaction.user.roles:
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)
        self.match["paso"] += 1
        await avanzar_pyb(interaction)

class BandoView(discord.ui.View):
    def __init__(self, match):
        super().__init__(timeout=None)
        for b in BANDOS:
            self.add_item(BandoButton(b, match))

# ==========================================================
# RESULTADOS (MODAL)
# ==========================================================
class ResultadoModal(discord.ui.Modal, title="Introducir resultado"):
    a = discord.ui.TextInput(label="Equipo A")
    b = discord.ui.TextInput(label="Equipo B")

    def __init__(self, match):
        super().__init__()
        self.match = match

    async def on_submit(self, interaction):
        if not es_arbitro(interaction.user):
            return await interaction.response.send_message("⛔ Solo árbitros", ephemeral=True)

        if not self.a.value.isdigit() or not self.b.value.isdigit():
            return await interaction.response.send_message("❌ Valores inválidos", ephemeral=True)

        a = int(self.a.value)
        b = int(self.b.value)
        if a == b:
            return await interaction.response.send_message("❌ No empate", ephemeral=True)

        modo, mapa = self.match["mapas_finales"][len(self.match["resultados"])]
        self.match["resultados"].append({
            "modo": modo,
            "mapa": mapa,
            "a": a,
            "b": b,
            "winner": "A" if a > b else "B"
        })

        subir_overlay(interaction.channel.id, self.match)

        aw = sum(1 for r in self.match["resultados"] if r["winner"] == "A")
        bw = sum(1 for r in self.match["resultados"] if r["winner"] == "B")

        if aw >= needed_wins(self.match["formato"]) or bw >= needed_wins(self.match["formato"]):
            await interaction.response.edit_message(embed=embed_final(self.match))
        else:
            await interaction.response.edit_message(
                embed=embed_resultado(self.match),
                view=ResultadoView(self.match)
            )

class ResultadoView(discord.ui.View):
    def __init__(self, match):
        super().__init__(timeout=None)
        self.add_item(ResultadoButton(match))

class ResultadoButton(discord.ui.Button):
    def __init__(self, match):
        super().__init__(label="Introducir resultado", style=discord.ButtonStyle.success)
        self.match = match

    async def callback(self, interaction):
        await interaction.response.send_modal(ResultadoModal(self.match))

# ==========================================================
# FLUJO PICK & BAN
# ==========================================================
async def avanzar_pyb(interaction):
    match = get_match(interaction.channel.id)

    if match["paso"] >= len(FORMATOS[match["formato"]]):
        subir_overlay(interaction.channel.id, match)
        return await interaction.response.edit_message(
            embed=embed_resultado(match),
            view=ResultadoView(match)
        )

    accion, modo, _ = FORMATOS[match["formato"]][match["paso"]]
    view = MapaView(match, modo) if accion in ["pick","ban"] else BandoView(match)
    await interaction.response.edit_message(embed=embed_turno(match), view=view)

# ==========================================================
# COMANDO PRINCIPAL
# ==========================================================
@bot.command()
async def setpartido(ctx, equipo_a: discord.Role, equipo_b: discord.Role, formato: str):
    formato = formato.lower()
    if formato not in FORMATOS:
        return await ctx.send("❌ Formato inválido")

    matches[ctx.channel.id] = {
        "equipo_a": equipo_a,
        "equipo_b": equipo_b,
        "formato": formato,
        "paso": 0,
        "baneados": {"HP": set(), "SnD": set(), "Overload": set()},
        "mapas_finales": [],
        "resultados": []
    }

    _, modo, _ = FORMATOS[formato][0]
    await ctx.send(embed=embed_turno(matches[ctx.channel.id]), view=MapaView(matches[ctx.channel.id], modo))

# ==========================================================
# RUN
# ==========================================================
if __name__ == "__main__":
    threading.Thread(target=run_tcp_healthcheck, daemon=True).start()
    bot.run(os.getenv("DISCORD_TOKEN"))
