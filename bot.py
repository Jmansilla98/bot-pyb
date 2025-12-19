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
        WS_CLIENTS.get(match_id, set()).discard(ws)

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

async def send_ready_buttons(channel: discord.TextChannel, state: dict):
    view = discord.ui.View(timeout=None)
    view.add_item(ReadyButton(channel.id, "A"))
    view.add_item(ReadyButton(channel.id, "B"))

    await channel.send(
        embed=discord.Embed(
            title="🎮 Pick & Ban",
            description=(
                "Cuando **ambos equipos** estén listos, el **árbitro** elegirá "
                "si la serie es **BO3 o BO5** y comenzará el Pick & Ban."
            ),
            color=0x00ffcc
        ),
        view=view
    )



class ReadyButton(discord.ui.Button):
    def __init__(self, channel_id: int, team: str):
        super().__init__(
            label=f"✅ TEAM {team} LISTO",
            style=discord.ButtonStyle.success
        )
        self.channel_id = channel_id
        self.team = team

    async def callback(self, interaction: discord.Interaction):
        state = MATCHES[self.channel_id]

        if not any(r.id == state["teams"][self.team]["role_id"] for r in interaction.user.roles):
            return await interaction.response.send_message(
                "⛔ No perteneces a este equipo",
                ephemeral=True
            )

        state["teams"][self.team]["ready"] = True
        await interaction.response.send_message("✅ Equipo confirmado", ephemeral=True)

        if all(t["ready"] for t in state["teams"].values()):
            await show_bo_selector(interaction.channel, self.channel_id)

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
# EVENTO – UI
# =========================
class CreateEventButton(discord.ui.Button):
    def __init__(self, channel_id):
        super().__init__(label="📅 Crear evento del partido", style=discord.ButtonStyle.primary)
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        if not is_arbitro(interaction.user):
            return await interaction.response.send_message(
                "⛔ Solo el árbitro puede crear el evento",
                ephemeral=True
            )
        await interaction.response.send_modal(CreateEventModal(self.channel_id))

class CreateEventModal(discord.ui.Modal, title="Crear evento del partido"):
    date = discord.ui.TextInput(label="Fecha (YYYY-MM-DD)", placeholder="2025-02-01")
    time = discord.ui.TextInput(label="Hora (HH:MM)", placeholder="21:30")
    duration = discord.ui.TextInput(label="Duración (min)", default="90")

    def __init__(self, channel_id):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        state = MATCHES[self.channel_id]
        try:
            start = datetime.fromisoformat(
                f"{self.date.value} {self.time.value}"
            ).replace(tzinfo=timezone.utc)

            end = start + timedelta(minutes=int(self.duration.value))
        except:
            return await interaction.response.send_message(
                "❌ Fecha u hora incorrecta",
                ephemeral=True
            )

        event = await interaction.guild.create_scheduled_event(
            name=f"{state['teams']['A']['name']} vs {state['teams']['B']['name']}",
            start_time=start,
            end_time=end,
            entity_type=discord.EntityType.external,
            location=f"Canal #{interaction.channel.name}",
            privacy_level=discord.PrivacyLevel.guild_only
        )

        await interaction.response.send_message(
            f"✅ Evento creado: **{event.name}**",
            ephemeral=True
        )
        # 👉 Ahora sí, mostrar botones de LISTO
        await send_ready_buttons(interaction.channel, state)


# =========================
# START
# =========================
@bot.command()
async def start(ctx, teamA: discord.Role, teamB: discord.Role):
    MATCHES[ctx.channel.id] = {
        "teams": {
            "A": {"name": teamA.name},
            "B": {"name": teamB.name},
        },
        "maps": build_maps(),
        "flow": FLOW_BO3,
        "step": 0,
    }

    embed = discord.Embed(
        title="📅 Organización del partido",
        description=(
            "Usad este mensaje para **acordar la hora**.\n\n"
            "👉 El árbitro debe crear el **evento oficial** antes de empezar."
        ),
        color=0x3498db
    )

    embed.add_field(
        name="Equipos",
        value=f"🟢 {teamA.name} vs 🔵 {teamB.name}",
        inline=False
    )

    view = discord.ui.View(timeout=None)
    view.add_item(CreateEventButton(ctx.channel.id))

    await ctx.send(embed=embed, view=view)

    overlay_url = f"{APP_URL}/overlay.html?match={ctx.channel.id}"
    await ctx.send(f"🎥 **Overlay OBS:**\n{overlay_url}")

    await ws_broadcast(str(ctx.channel.id))

# =========================
# RUN
# =========================
bot.run(TOKEN)
