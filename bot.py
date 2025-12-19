import discord
from discord.ext import commands
import asyncio
import json
from aiohttp import web
import pathlib
import os
import time
from datetime import datetime, timedelta, timezone

# =========================
# CONFIG
# =========================
APP_URL = os.getenv("APP_URL", "").rstrip("/")
PORT = int(os.getenv("PORT", "8080"))
TOKEN = os.getenv("DISCORD_TOKEN")

TURN_TIME_SECONDS = int(os.getenv("TURN_TIME_SECONDS", "30"))
ARBITRO_ROLE_NAME = "Arbitro"

BASE_DIR = pathlib.Path(__file__).parent
OVERLAY_DIR = BASE_DIR / "overlay"

# =========================
# DISCORD
# =========================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# =========================
# STATE
# =========================
MATCHES = {}
WS_CLIENTS = {}

HP_MAPS = ["Blackheart", "Colossus", "Den", "Exposure", "Scar"]
SND_MAPS = ["Colossus", "Den", "Exposure", "Raid", "Scar"]
OVR_MAPS = ["Den", "Exposure", "Scar"]

# =========================
# FLOWS
# =========================
FLOW_BO3 = [
    {"mode": "HP", "type": "ban", "team": "A"},
    {"mode": "HP", "type": "ban", "team": "B"},
    {"mode": "HP", "type": "pick_map", "team": "A", "slot": 1},
    {"mode": "HP", "type": "pick_side", "team": "B", "slot": 1},
    {"mode": "SnD", "type": "ban", "team": "B"},
    {"mode": "SnD", "type": "ban", "team": "A"},
    {"mode": "SnD", "type": "pick_map", "team": "B", "slot": 2},
    {"mode": "SnD", "type": "pick_side", "team": "A", "slot": 2},
    {"mode": "OVR", "type": "ban", "team": "A"},
    {"mode": "OVR", "type": "ban", "team": "B"},
    {"mode": "OVR", "type": "auto_decider", "slot": 3},
    {"mode": "OVR", "type": "pick_side", "team": "A", "slot": 3},
]

FLOW_BO5 = [
    *FLOW_BO3[:4],
    {"mode": "HP", "type": "pick_map", "team": "B", "slot": 4},
    {"mode": "HP", "type": "pick_side", "team": "A", "slot": 4},
    *FLOW_BO3[4:8],
    {"mode": "SnD", "type": "pick_map", "team": "A", "slot": 5},
    {"mode": "SnD", "type": "pick_side", "team": "B", "slot": 5},
    *FLOW_BO3[8:]
]

# =========================
# WEB + WS
# =========================
app = web.Application()
app.router.add_static("/static/", OVERLAY_DIR)
routes = web.RouteTableDef()

@routes.get("/overlay.html")
async def overlay(request):
    return web.FileResponse(OVERLAY_DIR / "overlay.html")

@routes.get("/ws")
async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    match_id = request.query.get("match")

    WS_CLIENTS.setdefault(match_id, set()).add(ws)
    if int(match_id) in MATCHES:
        await ws_broadcast(match_id)

    try:
        async for _ in ws:
            pass
    finally:
        WS_CLIENTS[match_id].discard(ws)

    return ws

app.add_routes(routes)

async def ws_broadcast(match_id):
    state = MATCHES.get(int(match_id))
    if not state:
        return

    payload = json.dumps({"type": "state", "state": state})
    for ws in list(WS_CLIENTS.get(match_id, [])):
        try:
            await ws.send_str(payload)
        except:
            WS_CLIENTS.get(match_id, set()).discard(ws)

async def start_web():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

@bot.event
async def on_ready():
    asyncio.create_task(start_web())
    print("🤖 Bot listo")

# =========================
# HELPERS
# =========================
def build_maps():
    maps = {}
    for m in HP_MAPS:
        maps[f"HP::{m}"] = {"mode": "HP", "status": "free", "team": None, "slot": None, "side": None}
    for m in SND_MAPS:
        maps[f"SnD::{m}"] = {"mode": "SnD", "status": "free", "team": None, "slot": None, "side": None}
    for m in OVR_MAPS:
        maps[f"OVR::{m}"] = {"mode": "OVR", "status": "free", "team": None, "slot": None, "side": None}
    return maps

def is_arbitro(member):
    return any(r.name.lower() == ARBITRO_ROLE_NAME.lower() for r in member.roles)

def required_wins(mode):
    return 2 if mode == "BO3" else 3

def compute_wins(state):
    a = b = 0
    for r in state["map_results"].values():
        if r["winner"] == "A":
            a += 1
        elif r["winner"] == "B":
            b += 1
    return a, b

async def check_autowin(channel_id, state):
    if state.get("series_finished"):
        return

    wins_a, wins_b = compute_wins(state)
    need = required_wins(state["mode"])

    if wins_a >= need or wins_b >= need:
        winner = "A" if wins_a > wins_b else "B"
        state["series_finished"] = True
        state["series_winner"] = winner

        await ws_broadcast(str(channel_id))

        channel = bot.get_channel(channel_id)
        if channel:
            await channel.send(
                f"🏆 **SERIE FINALIZADA** — Gana **{state['teams'][winner]['name']}** "
                f"({wins_a}-{wins_b})"
            )

@bot.command()
async def start(ctx, teamA: discord.Role, teamB: discord.Role):
    MATCHES[ctx.channel.id] = {
        "channel_id": ctx.channel.id,
        "flow": [],
        "step": 0,
        "maps": build_maps(),
        "map_results": {},
        "mode": None,
        "series_finished": False,
        "series_winner": None,
        "series_score": {"A": 0, "B": 0},
        "turn_started_at": time.time(),
        "turn_duration": TURN_TIME_SECONDS,
        "teams": {
            "A": {"name": teamA.name, "role_id": teamA.id, "ready": False},
            "B": {"name": teamB.name, "role_id": teamB.id, "ready": False},
        }
    }

    state = MATCHES[ctx.channel.id]

    # 1️⃣ EMBED DE ORGANIZACIÓN (ESTO ES LO QUE NO SE ENVIABA)
    await send_match_planning_embed(ctx.channel, state)

    # 2️⃣ OVERLAY
    overlay_url = (
        f"{APP_URL}/overlay.html?match={ctx.channel.id}"
        if APP_URL else
        f"/overlay.html?match={ctx.channel.id}"
    )

    await ctx.send(f"🎥 **Overlay OBS:**\n{overlay_url}")

    await ws_broadcast(str(ctx.channel.id))


# =========================
# RESULTS
# =========================
class ResultModal(discord.ui.Modal, title="Resultado del mapa"):
    winner = discord.ui.TextInput(label="Ganador (A o B)", max_length=1)
    score = discord.ui.TextInput(label="Marcador", placeholder="250-50 / 6-3")

    def __init__(self, channel_id, slot):
        super().__init__()
        self.channel_id = channel_id
        self.slot = slot

    async def on_submit(self, interaction):
        state = MATCHES[self.channel_id]
        w = self.winner.value.upper()

        if w not in ("A", "B"):
            return await interaction.response.send_message("⛔ Ganador inválido", ephemeral=True)

        state["map_results"][str(self.slot)] = {
            "winner": w,
            "score": self.score.value
        }

        await ws_broadcast(str(self.channel_id))
        await interaction.response.send_message("✅ Resultado guardado", ephemeral=True)
        await check_autowin(self.channel_id, state)

# =========================
# RUN
# =========================
bot.run(TOKEN)
