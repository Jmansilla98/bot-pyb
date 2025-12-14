# =========================
# PickBanBot – bot.py
# =========================

import discord
from discord.ext import commands
import os
import asyncio
import json
import base64
import requests
import socket
import threading
import re

# ==========================================================
# TCP SERVER (HEALTH CHECK TCP PARA KOYEB)
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

# ==========================================================
# MAPAS
# ==========================================================
MAPAS = {
    "HP": ["Blackheart", "Colossus", "Den", "Exposure", "Scar"],
    "SnD": ["Colossus", "Den", "Exposure", "Raid", "Scar"],
    "Overload": ["Den", "Exposure", "Scar"]
}

# ==========================================================
# FORMATOS PYB
# ==========================================================
FORMATOS = {
    "bo5": [
        ("ban","HP","A"), "#",
        ("ban","HP","B"), "#",
        ("pick","HP","A"), "#",
        ("side","HP","B"), "#",
        ("pick","HP","B"), "#",
        ("side","HP","A"), "#",
        ("ban","SnD","B"), "#",
        ("ban","SnD","A"), "#",
        ("pick","SnD","B"), "#",
        ("side","SnD","A"), "#",
        ("pick","SnD","A"), "#",
        ("side","SnD","B"), "#",
        ("ban","Overload","A"), "#",
        ("ban","Overload","B"), "#",
        ("side","Overload","A")
    ],
    "bo3": [
        ("ban","HP","A"), "#",
        ("ban","HP","B"), "#",
        ("pick","HP","A"), "#",
        ("side","HP","B"), "#",
        ("ban","SnD","B"), "#",
        ("ban","SnD","A"), "#",
        ("pick","SnD","B"), "#",
        ("side","SnD","A"), "#",
        ("ban","Overload","A"), "#",
        ("ban","Overload","B"), "#",
        ("side","Overload","A")
    ]
}

# ==========================================================
# ESTADO PARTIDOS POR CANAL
# ==========================================================
matches = {}

def es_arbitro(user):
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

    payload = {"message": "Actualizar overlay", "content": content}
    if sha:
        payload["sha"] = sha

    requests.put(url, headers=gh_headers(), json=payload)

# ==========================================================
# EMBEDS
# ==========================================================
def embed_pyb(match):
    paso = match["paso"]
    accion, modo, equipo = match["formato"][paso]
    turno = match["equipo_a"] if equipo == "A" else match["equipo_b"]

    e = discord.Embed(
        title=f"{accion.upper()} {modo}",
        color=discord.Color.blurple()
    )
    e.add_field(name="Turno", value=turno.mention, inline=False)
    e.add_field(
        name="Equipos",
        value=f"🔵 {match['equipo_a'].mention}\n🔴 {match['equipo_b'].mention}",
        inline=False
    )
    e.set_footer(text=f"Paso {paso+1}/{len(match['formato'])}")
    return e

def embed_resultado(match):
    idx = len(match["resultados"])
    modo, mapa = match["mapas_finales"][idx]
    e = discord.Embed(
        title=f"📝 Resultado Mapa {idx+1}",
        description=f"{modo} — {mapa}",
        color=discord.Color.orange()
    )
    e.add_field(name=match["equipo_a"].name, value="—", inline=True)
    e.add_field(name=match["equipo_b"].name, value="—", inline=True)
    e.set_footer(text="Usa !resultado A B")
    return e

# ==========================================================
# BOTONES MAPAS
# ==========================================================
class MapaButton(discord.ui.Button):
    def __init__(self, mapa, modo, channel_id):
        super().__init__(
            label=mapa,
            style=discord.ButtonStyle.primary,
            disabled=False
        )
        self.mapa = mapa
        self.modo = modo
        self.channel_id = channel_id

    async def callback(self, interaction):
        match = matches[self.channel_id]
        accion, modo, equipo = match["formato"][match["paso"]]
        turno = match["equipo_a"] if equipo == "A" else match["equipo_b"]

        if turno not in interaction.user.roles:
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)

        if self.mapa in match["usados"][modo]:
            return await interaction.response.send_message("❌ Mapa ya baneado", ephemeral=True)

        match["usados"][modo].add(self.mapa)

        if accion == "pick":
            match["mapas_finales"].append((modo, self.mapa))

        match["paso"] += 1
        await avanzar_pyb(interaction)

class MapaView(discord.ui.View):
    def __init__(self, modo, channel_id):
        super().__init__(timeout=None)
        for m in MAPAS[modo]:
            self.add_item(MapaButton(m, modo, channel_id))

# ==========================================================
# BOTONES BANDOS
# ==========================================================
class BandoButton(discord.ui.Button):
    def __init__(self, bando, channel_id):
        super().__init__(label=bando, style=discord.ButtonStyle.secondary)
        self.channel_id = channel_id

    async def callback(self, interaction):
        match = matches[self.channel_id]
        _, _, equipo = match["formato"][match["paso"]]
        turno = match["equipo_a"] if equipo == "A" else match["equipo_b"]

        if turno not in interaction.user.roles:
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)

        match["paso"] += 1
        await avanzar_pyb(interaction)

class BandoView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.add_item(BandoButton("Ataque", channel_id))
        self.add_item(BandoButton("Defensa", channel_id))

# ==========================================================
# FLUJO PYB
# ==========================================================
async def avanzar_pyb(interaction):
    match = matches[interaction.channel.id]

    if match["paso"] >= len(match["formato"]):
        await iniciar_resultados(interaction.channel)
        return

    accion, modo, _ = match["formato"][match["paso"]]
    if accion in ["ban", "pick"]:
        view = MapaView(modo, interaction.channel.id)
    else:
        view = BandoView(interaction.channel.id)

    await interaction.response.edit_message(
        embed=embed_pyb(match),
        view=view
    )

# ==========================================================
# RESULTADOS
# ==========================================================
@bot.command()
async def resultado(ctx, a: int, b: int):
    match = matches.get(ctx.channel.id)
    if not match:
        return

    if a == b:
        return await ctx.send("❌ No puede haber empate")

    idx = len(match["resultados"])
    modo, mapa = match["mapas_finales"][idx]
    winner = "A" if a > b else "B"

    match["resultados"].append({
        "modo": modo,
        "mapa": mapa,
        "a": a,
        "b": b,
        "winner": winner
    })

    overlay_data = {
        "equipo_a": match["equipo_a"].name,
        "equipo_b": match["equipo_b"].name,
        "mapas": match["mapas_finales"],
        "resultados": match["resultados"]
    }
    subir_overlay(ctx.channel.id, overlay_data)

    wins_a = sum(1 for r in match["resultados"] if r["winner"] == "A")
    wins_b = sum(1 for r in match["resultados"] if r["winner"] == "B")
    objetivo = 3 if match["tipo"] == "bo5" else 2

    if wins_a >= objetivo or wins_b >= objetivo:
        msg = await ctx.send("🏁 Partido finalizado. ¿Reclamación?")
        await asyncio.sleep(5)
        await msg.edit(content="📤 Partido enviado a Challonge (placeholder)")
        return

    await ctx.send(embed=embed_resultado(match))

async def iniciar_resultados(channel):
    match = matches[channel.id]
    match["mapas_finales"] = match["mapas_finales"]
    match["resultados"] = []

    overlay_data = {
        "equipo_a": match["equipo_a"].name,
        "equipo_b": match["equipo_b"].name,
        "mapas": match["mapas_finales"],
        "resultados": []
    }
    subir_overlay(channel.id, overlay_data)

    await channel.send(embed=embed_resultado(match))

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
        "formato": FORMATOS[formato],
        "tipo": formato,
        "paso": 0,
        "usados": {"HP": set(), "SnD": set(), "Overload": set()},
        "mapas_finales": [],
        "resultados": []
    }

    await ctx.send(
        embed=embed_pyb(matches[ctx.channel.id]),
        view=MapaView(FORMATOS[formato][0][1], ctx.channel.id)
    )

# ==========================================================
# ARRANQUE
# ==========================================================
if __name__ == "__main__":
    threading.Thread(target=run_tcp_healthcheck, daemon=True).start()
    bot.run(os.getenv("DISCORD_TOKEN"))
