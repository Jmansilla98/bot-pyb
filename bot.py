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
# SERIES CONFIG
# =========================
SERIES_CONFIG = {
    "BO3": {"maps": 3, "wins": 2},
    "BO5": {"maps": 5, "wins": 3},
    "BO7": {"maps": 7, "wins": 4},
}

# =========================
# FLOWS
# =========================
BASE_FLOW = [
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

def build_flow(mode):
    if mode == "BO3":
        return BASE_FLOW
    if mode == "BO5":
        return BASE_FLOW + [
            {"mode": "HP", "type": "pick_map", "team": "B", "slot": 4},
            {"mode": "HP", "type": "pick_side", "team": "A", "slot": 4},
            {"mode": "SnD", "type": "pick_map", "team": "A", "slot": 5},
            {"mode": "SnD", "type": "pick_side", "team": "B", "slot": 5},
        ]
    if mode == "BO7":
        return BASE_FLOW + [
            {"mode": "HP", "type": "pick_map", "team": "B", "slot": 4},
            {"mode": "HP", "type": "pick_side", "team": "A", "slot": 4},
            {"mode": "SnD", "type": "pick_map", "team": "A", "slot": 5},
            {"mode": "SnD", "type": "pick_side", "team": "B", "slot": 5},
            {"mode": "HP", "type": "pick_map", "team": "A", "slot": 6},
            {"mode": "HP", "type": "pick_side", "team": "B", "slot": 6},
            {"mode": "SnD", "type": "pick_map", "team": "B", "slot": 7},
            {"mode": "SnD", "type": "pick_side", "team": "A", "slot": 7},
        ]
    return BASE_FLOW

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

@routes.get("/api/matches")
async def api_matches(request):
    data = []
    for cid, s in MATCHES.items():
        status = (
            "Sin comenzar" if not s["flow"]
            else "En curso" if not s["series_finished"]
            else "Finalizado"
        )
        data.append({
            "match_id": cid,
            "teams": f"{s['teams']['A']['name']} vs {s['teams']['B']['name']}",
            "mode": s["mode"],
            "status": status,
            "results": s["map_results"]
        })
    return web.json_response(data)

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
def is_arbitro(member):
    return any(r.name.lower() == ARBITRO_ROLE_NAME.lower() for r in member.roles)

def build_maps():
    maps = {}
    for m in HP_MAPS:
        maps[f"HP::{m}"] = {"mode": "HP", "status": "free"}
    for m in SND_MAPS:
        maps[f"SnD::{m}"] = {"mode": "SnD", "status": "free"}
    for m in OVR_MAPS:
        maps[f"OVR::{m}"] = {"mode": "OVR", "status": "free"}
    return maps

# =========================
# EMBEDS
# =========================
def planning_embed(state):
    e = discord.Embed(
        title="📅 Organización del partido",
        description=(
            "Este espacio sirve para **acordar la hora** del partido.\n\n"
            "Cuando esté claro, el **árbitro** debe crear el evento oficial.\n\n"
            "⛔ El Pick & Ban no comenzará hasta entonces."
        ),
        color=0x0aa3ff
    )
    e.add_field(
        name="Enfrentamiento",
        value=f"🟢 **{state['teams']['A']['name']}** vs 🔵 **{state['teams']['B']['name']}**",
        inline=False
    )
    return e

def pickban_embed(state):
    e = discord.Embed(
        title=f"🎮 Pick & Ban — {state['mode']}",
        description=f"Paso {state['step'] + 1}/{len(state['flow'])}",
        color=0x00ffc6
    )
    return e

# =========================
# START COMMAND
# =========================
@bot.command()
async def start(ctx, teamA: discord.Role, teamB: discord.Role):
    MATCHES[ctx.channel.id] = {
        "channel_id": ctx.channel.id,
        "teams": {
            "A": {"name": teamA.name, "role_id": teamA.id, "ready": False},
            "B": {"name": teamB.name, "role_id": teamB.id, "ready": False},
        },
        "maps": build_maps(),
        "flow": [],
        "step": 0,
        "mode": None,
        "map_results": {},
        "series_finished": False,
    }

    state = MATCHES[ctx.channel.id]

    view = discord.ui.View(timeout=None)
    view.add_item(CreateEventButton(ctx.channel.id))

    await ctx.send(embed=planning_embed(state), view=view)

    overlay_url = f"{APP_URL}/overlay.html?match={ctx.channel.id}"
    await ctx.send(f"🎥 **Overlay OBS:**\n{overlay_url}")

    await ws_broadcast(str(ctx.channel.id))

# =========================
# EVENT + READY
# =========================
class CreateEventButton(discord.ui.Button):
    def __init__(self, channel_id):
        super().__init__(label="📅 Crear evento", style=discord.ButtonStyle.primary)
        self.channel_id = channel_id

    async def callback(self, interaction):
        if not is_arbitro(interaction.user):
            return await interaction.response.send_message("⛔ Solo árbitro", ephemeral=True)
        await interaction.response.send_modal(CreateEventModal(self.channel_id))

class CreateEventModal(discord.ui.Modal, title="Crear evento"):
    date = discord.ui.TextInput(label="Fecha YYYY-MM-DD")
    time = discord.ui.TextInput(label="Hora HH:MM")

    def __init__(self, channel_id):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction):
        state = MATCHES[self.channel_id]

        start = datetime.fromisoformat(
            f"{self.date.value} {self.time.value}"
        ).replace(tzinfo=timezone.utc)

        await interaction.guild.create_scheduled_event(
            name=f"{state['teams']['A']['name']} vs {state['teams']['B']['name']}",
            start_time=start,
            end_time=start + timedelta(hours=2),
            entity_type=discord.EntityType.external,
            location=f"Canal #{interaction.channel.name}",
            privacy_level=discord.PrivacyLevel.guild_only
        )

        await interaction.response.send_message("✅ Evento creado", ephemeral=True)
        await send_ready(interaction.channel, self.channel_id)

async def send_ready(channel, channel_id):
    v = discord.ui.View(timeout=None)
    v.add_item(ReadyButton(channel_id, "A"))
    v.add_item(ReadyButton(channel_id, "B"))
    await channel.send("⏳ **Equipos, confirmaos como listos**", view=v)

class ReadyButton(discord.ui.Button):
    def __init__(self, channel_id, team):
        super().__init__(label=f"✅ TEAM {team} LISTO", style=discord.ButtonStyle.success)
        self.channel_id = channel_id
        self.team = team

    async def callback(self, interaction):
        state = MATCHES[self.channel_id]

        if not any(r.id == state["teams"][self.team]["role_id"] for r in interaction.user.roles):
            return await interaction.response.send_message("⛔ No es tu equipo", ephemeral=True)

        state["teams"][self.team]["ready"] = True
        await interaction.response.send_message("✅ Confirmado", ephemeral=True)

        if all(t["ready"] for t in state["teams"].values()):
            await show_modes(interaction.channel, self.channel_id)

async def show_modes(channel, channel_id):
    v = discord.ui.View(timeout=None)
    for m in ["BO3", "BO5", "BO7"]:
        v.add_item(ModeButton(channel_id, m))
    await channel.send("⚖️ Árbitro: selecciona formato", view=v)

class ModeButton(discord.ui.Button):
    def __init__(self, channel_id, mode):
        super().__init__(label=mode, style=discord.ButtonStyle.primary)
        self.channel_id = channel_id
        self.mode = mode

    async def callback(self, interaction):
        if not is_arbitro(interaction.user):
            return

        state = MATCHES[self.channel_id]
        state["mode"] = self.mode
        state["flow"] = build_flow(self.mode)
        state["step"] = 0

        await interaction.channel.send(embed=pickban_embed(state))
        await ws_broadcast(str(self.channel_id))

# =========================
# RUN
# =========================
bot.run(TOKEN)
