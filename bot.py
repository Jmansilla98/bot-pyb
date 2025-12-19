import discord
from discord.ext import commands
import asyncio
import json
from aiohttp import web
import aiohttp
import pathlib
import os
import time

# =========================
# CONFIG
# =========================
APP_URL = os.getenv("APP_URL")
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

FLOW_BO3 = [
    {"mode": "HP", "type": "ban", "team": "A"},
    {"mode": "HP", "type": "ban", "team": "B"},
    {"mode": "HP", "type": "pick_map", "team": "A", "slot": 1},
    {"mode": "HP", "type": "pick_side", "team": "B", "slot": 1},
    {"mode": "SnD", "type": "ban", "team": "B"},
    {"mode": "SnD", "type": "ban", "team": "A"},
    {"mode": "SnD", "type": "pick_map", "team": "B", "slot": 2},
    {"mode": "SnD", "type": "pick_side", "team": "A", "slot": 2},
    {"mode": "OVR", "type": "auto_decider", "slot": 3},
]

FLOW_BO5 = FLOW_BO3 + [
    {"mode": "HP", "type": "pick_map", "team": "B", "slot": 4},
    {"mode": "HP", "type": "pick_side", "team": "A", "slot": 4},
    {"mode": "SnD", "type": "pick_map", "team": "A", "slot": 5},
    {"mode": "SnD", "type": "pick_side", "team": "B", "slot": 5},
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
            WS_CLIENTS[match_id].discard(ws)

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

def required_wins(mode):
    return 2 if mode == "BO3" else 3

def check_series_winner(state):
    wins = {"A": 0, "B": 0}
    for r in state["map_results"].values():
        wins[r["winner"]] += 1

    for team, w in wins.items():
        if w >= required_wins(state["mode"]):
            state["winner"] = team
            return True
    return False

# =========================
# START COMMAND
# =========================
@bot.command()
async def start(ctx, teamA: discord.Role, teamB: discord.Role):
    MATCHES[ctx.channel.id] = {
        "flow": [],
        "step": 0,
        "maps": build_maps(),
        "map_results": {},
        "mode": None,
        "winner": None,
        "turn_started_at": time.time(),
        "turn_duration": TURN_TIME_SECONDS,
        "teams": {
            "A": {"name": teamA.name, "role_id": teamA.id, "ready": False},
            "B": {"name": teamB.name, "role_id": teamB.id, "ready": False},
        }
    }

    overlay_url = f"{APP_URL}/overlay.html?match={ctx.channel.id}"

    view = discord.ui.View(timeout=None)
    view.add_item(ReadyButton(ctx.channel.id, "A"))
    view.add_item(ReadyButton(ctx.channel.id, "B"))

    await ctx.send(
        embed=discord.Embed(
            title="🎮 Pick & Bans",
            description="Ambos equipos deben confirmar que están listos",
            color=0x00ffcc
        ),
        view=view
    )
    await ctx.send(f"🎥 **Overlay OBS:**\n{overlay_url}")
    await ws_broadcast(str(ctx.channel.id))

# =========================
# READY + MODE
# =========================
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
            await show_bo_selector(interaction.channel, self.channel_id)

async def show_bo_selector(channel, channel_id):
    view = discord.ui.View(timeout=None)
    view.add_item(ModeButton(channel_id, "BO3"))
    view.add_item(ModeButton(channel_id, "BO5"))
    await channel.send("⚖️ Árbitro: selecciona formato", view=view)

class ModeButton(discord.ui.Button):
    def __init__(self, channel_id, mode):
        super().__init__(label=mode, style=discord.ButtonStyle.primary)
        self.channel_id = channel_id
        self.mode = mode

    async def callback(self, interaction):
        if not any(r.name.lower() == ARBITRO_ROLE_NAME.lower() for r in interaction.user.roles):
            return await interaction.response.send_message("⛔ Solo árbitro", ephemeral=True)

        state = MATCHES[self.channel_id]
        state["mode"] = self.mode
        state["flow"] = FLOW_BO3 if self.mode == "BO3" else FLOW_BO5
        state["step"] = 0
        state["turn_started_at"] = time.time()

        await interaction.response.send_message(f"🎮 {self.mode} seleccionado", ephemeral=True)
        await interaction.channel.send(
            embed=discord.Embed(title="Pick & Ban iniciado"),
            view=PickBanView(self.channel_id)
        )
        await ws_broadcast(str(self.channel_id))

# =========================
# RESULTS UI
# =========================
class PickBanView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        state = MATCHES[channel_id]

        # Mostrar botones de resultado SOLO en orden
        picked_slots = sorted(
            m["slot"] for m in state["maps"].values()
            if m["status"] == "picked"
        )

        next_slot = len(state["map_results"]) + 1

        if next_slot in picked_slots and not state.get("winner"):
            self.add_item(ResultButton(channel_id, next_slot))

class ResultButton(discord.ui.Button):
    def __init__(self, channel_id, slot):
        super().__init__(label=f"Resultado M{slot}", style=discord.ButtonStyle.success)
        self.channel_id = channel_id
        self.slot = slot

    async def callback(self, interaction):
        if not any(r.name.lower() == ARBITRO_ROLE_NAME.lower() for r in interaction.user.roles):
            return await interaction.response.send_message("⛔ Solo árbitro", ephemeral=True)

        await interaction.response.send_modal(ResultModal(self.channel_id, self.slot))

class ResultModal(discord.ui.Modal):
    def __init__(self, channel_id, slot):
        super().__init__(title=f"Resultado M{slot}")
        self.channel_id = channel_id
        self.slot = slot

        self.score_a = discord.ui.TextInput(label="Score TEAM A")
        self.score_b = discord.ui.TextInput(label="Score TEAM B")
        self.add_item(self.score_a)
        self.add_item(self.score_b)

    async def on_submit(self, interaction):
        state = MATCHES[self.channel_id]

        a = int(self.score_a.value)
        b = int(self.score_b.value)

        winner = "A" if a > b else "B"
        state["map_results"][self.slot] = {
            "winner": winner,
            "score": f"{a}-{b}"
        }

        if check_series_winner(state):
            await interaction.channel.send(
                f"🏆 **GANADOR: {state['teams'][state['winner']]['name']}**"
            )

        state["turn_started_at"] = time.time()
        await ws_broadcast(str(self.channel_id))
        await interaction.response.send_message("✅ Resultado guardado", ephemeral=True)


# =========================
# EMBED
# =========================
def build_embed(state):
    embed = discord.Embed(
        title=f"PICK & BAN — {state.get('mode','')}",
        description=describe_step(state),
        color=0x2ecc71
    )

    for mode in ["HP", "SnD", "OVR"]:
        lines = []
        for k, m in state["maps"].items():
            if m["mode"] != mode:
                continue
            name = k.split("::")[1]
            if m["status"] == "banned":
                lines.append(f"❌ {name}")
            elif m["status"] == "picked":
                lines.append(f"✅ {name} · M{m['slot']} ({m['team']})")
            else:
                lines.append(f"⬜ {name}")
        embed.add_field(name=mode, value="\n".join(lines) or "—", inline=False)

    return embed

def describe_step(state):
    if state["step"] >= len(state["flow"]):
        return "✅ **PICK & BAN FINALIZADO**"

    step = state["flow"][state["step"]]
    action = {
        "ban": "BANEAR MAPA",
        "pick_map": "PICK DE MAPA",
        "pick_side": "ELEGIR LADO",
        "auto_decider": "DECIDER AUTOMÁTICO"
    }.get(step["type"], step["type"])

    team = step.get("team")
    if team == "A":
        who = state["teams"]["A"]["name"]
    elif team == "B":
        who = state["teams"]["B"]["name"]
    else:
        who = "SISTEMA"

    return f"**PASO {state['step'] + 1}/{len(state['flow'])}**\n🎯 {action}\n👤 Turno: **{who}**"


# =========================
# RUN
# =========================
bot.run(TOKEN)
