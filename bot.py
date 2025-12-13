import discord
from discord.ext import commands
import os
import json
import asyncio
from datetime import datetime
from pathlib import Path

# ==========================
# CONFIGURACIÓN
# ==========================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

ROL_ARBITRO = "team1"   # ⚠️ NO LO CAMBIES
OVERLAY_BASE_URL = "https://Jmansilla98.github.io/overlay-cod"

DATA_DIR = Path("matches")
DATA_DIR.mkdir(exist_ok=True)

# ==========================
# ESTADO POR CANAL
# ==========================

pyb_channels = {}

def es_arbitro(member):
    return any(r.name == ROL_ARBITRO for r in member.roles)

def needed_wins(formato):
    return 2 if formato == "bo3" else 3

# ==========================
# OVERLAY
# ==========================

def write_status(channel_id, data):
    match_dir = DATA_DIR / str(channel_id)
    match_dir.mkdir(parents=True, exist_ok=True)

    with open(match_dir / "status.json", "w") as f:
        json.dump(data, f, indent=2)

def delete_status_later(channel_id, delay=180):
    async def _delete():
        await asyncio.sleep(delay)
        path = DATA_DIR / str(channel_id) / "status.json"
        if path.exists():
            path.unlink()
    asyncio.create_task(_delete())

# ==========================
# COMANDOS
# ==========================

@bot.command()
async def setequipos(ctx, team_a: discord.Role, team_b: discord.Role):
    pyb_channels[ctx.channel.id] = {
        "team_a": team_a,
        "team_b": team_b,
        "formato": None,
        "resultados": [],
        "reclamacion": False
    }
    await ctx.send(f"✅ Equipos definidos:\n🔵 {team_a.mention}\n🔴 {team_b.mention}")

@bot.command()
async def start(ctx, formato: str):
    pyb = pyb_channels.get(ctx.channel.id)
    if not pyb:
        return await ctx.send("❌ Usa !setequipos primero")

    formato = formato.lower()
    if formato not in ["bo3", "bo5"]:
        return await ctx.send("❌ Formato inválido")

    pyb["formato"] = formato

    await ctx.send(
        "🎮 **Pick & Ban iniciado**\n"
        f"Formato: **{formato.upper()}**\n\n"
        "Cuando termine el PyB, introduce resultados con `!resultado`"
    )

@bot.command()
async def resultado(ctx, mapa: int, score_a: int, score_b: int):
    pyb = pyb_channels.get(ctx.channel.id)
    if not pyb:
        return

    if not es_arbitro(ctx.author):
        return await ctx.send("⛔ Solo árbitros")

    winner = "A" if score_a > score_b else "B"

    pyb["resultados"].append({
        "mapa": mapa,
        "a": score_a,
        "b": score_b,
        "winner": winner
    })

    wins_a = sum(1 for r in pyb["resultados"] if r["winner"] == "A")
    wins_b = sum(1 for r in pyb["resultados"] if r["winner"] == "B")

    write_status(ctx.channel.id, {
        "format": pyb["formato"].upper(),
        "mapNumber": mapa,
        "mode": "LIVE",
        "mapName": f"Mapa {mapa}",
        "seriesA": wins_a,
        "seriesB": wins_b,
        "decider": wins_a == wins_b == needed_wins(pyb["formato"]) - 1,
        "reclamacion": pyb["reclamacion"],
        "teamAName": pyb["team_a"].name,
        "teamBName": pyb["team_b"].name
    })

    await ctx.send(
        f"📝 Resultado mapa {mapa}\n"
        f"{pyb['team_a'].name} {score_a} – {score_b} {pyb['team_b'].name}"
    )

    if wins_a >= needed_wins(pyb["formato"]) or wins_b >= needed_wins(pyb["formato"]):
        await ctx.send("🏁 **Partido terminado**")
        delete_status_later(ctx.channel.id)

        overlay_a = f"{OVERLAY_BASE_URL}/pov.html?match={ctx.channel.id}&team=A"
        overlay_b = f"{OVERLAY_BASE_URL}/pov.html?match={ctx.channel.id}&team=B"

        await ctx.send(
            "🎥 **Overlays POV**\n"
            f"{pyb['team_a'].name}: {overlay_a}\n"
            f"{pyb['team_b'].name}: {overlay_b}"
        )

@bot.command()
async def reclamar(ctx):
    pyb = pyb_channels.get(ctx.channel.id)
    if not pyb:
        return
    pyb["reclamacion"] = True
    await ctx.send("🚨 **Reclamación activada**")

# ==========================
# ARRANQUE
# ==========================

# ⚠️ AQUÍ ES LO ÚNICO QUE TOCAS
# En Render o local:
# DISCORD_TOKEN = tu token nuevo

bot.run(yeq6v52Ij_dsLfPmEfnCpUs5O_cL2sfV)
