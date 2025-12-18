import discord
from discord.ext import commands
import asyncio
import json
from aiohttp import web
import aiohttp
import pathlib
import os

# =========================
# CONFIG
# =========================
APP_URL = os.getenv("APP_URL")
PORT = int(os.getenv("PORT", "8080"))
TOKEN = os.getenv("DISCORD_TOKEN")
BASE_DIR = pathlib.Path(__file__).parent
OVERLAY_DIR = BASE_DIR / "overlay"

TURN_TIME_SECONDS = int(os.getenv("TURN_TIME_SECONDS", "30"))
ARBITRO_ROLE_NAME = "Arbitro"

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
WS_CLIENTS = {}  # match_id -> set(ws)

HP_MAPS = ["Blackheart", "Colossus", "Den", "Exposure", "Scar"]
SND_MAPS = ["Colossus", "Den", "Exposure", "Raid", "Scar"]
OVR_MAPS = ["Den", "Exposure", "Scar"]

FLOW_BO5 = [
    {"mode": "HP", "type": "ban", "team": "A"},
    {"mode": "HP", "type": "ban", "team": "B"},
    {"mode": "HP", "type": "pick_map", "team": "A", "slot": 1},
    {"mode": "HP", "type": "pick_side", "team": "B", "slot": 1},
    {"mode": "HP", "type": "pick_map", "team": "B", "slot": 4},
    {"mode": "HP", "type": "pick_side", "team": "A", "slot": 4},
    {"mode": "SnD", "type": "ban", "team": "B"},
    {"mode": "SnD", "type": "ban", "team": "A"},
    {"mode": "SnD", "type": "pick_map", "team": "B", "slot": 2},
    {"mode": "SnD", "type": "pick_side", "team": "A", "slot": 2},
    {"mode": "SnD", "type": "pick_map", "team": "A", "slot": 5},
    {"mode": "SnD", "type": "pick_side", "team": "B", "slot": 5},
    {"mode": "OVR", "type": "ban", "team": "A"},
    {"mode": "OVR", "type": "ban", "team": "B"},
    {"mode": "OVR", "type": "auto_decider", "slot": 3},
    {"mode": "OVR", "type": "pick_side", "team": "A", "slot": 3},
]

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
# AIOHTTP + WS
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
    if not match_id:
        await ws.close()
        return ws

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

async def keep_alive():
    if not APP_URL:
        return
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await session.get(APP_URL)
            except:
                pass
            await asyncio.sleep(300)

@bot.event
async def on_ready():
    asyncio.create_task(start_web())
    asyncio.create_task(keep_alive())
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

def user_can_interact(interaction, state, step):
    if any(r.name == ARBITRO_ROLE_NAME for r in interaction.user.roles):
        return True
    if not step.get("team"):
        return False
    return any(
        r.id == state["teams"][step["team"]]["role_id"]
        for r in interaction.user.roles
    )

async def auto_decider(state):
    while state["step"] < len(state["flow"]):
        step = state["flow"][state["step"]]
        if step["type"] != "auto_decider":
            return
        free_maps = [
            k for k, m in state["maps"].items()
            if m["mode"] == step["mode"] and m["status"] == "free"
        ]
        if len(free_maps) != 1:
            return
        key = free_maps[0]
        state["maps"][key].update({
            "status": "picked",
            "team": "DECIDER",
            "slot": step["slot"]
        })
        state["step"] += 1
        state["turn_started_at"] = asyncio.get_event_loop().time()

# =========================
# UI
# =========================
class ReadyButton(discord.ui.Button):
    def __init__(self, channel_id, team):
        super().__init__(label=f"✅ {team} LISTO", style=discord.ButtonStyle.success)
        self.channel_id = channel_id
        self.team = team

    async def callback(self, interaction):
        state = MATCHES[self.channel_id]
        if not any(r.id == state["teams"][self.team]["role_id"] for r in interaction.user.roles):
            return await interaction.response.send_message("⛔ No es tu equipo", ephemeral=True)

        state["teams"][self.team]["ready"] = True
        await interaction.response.send_message("✅ Equipo listo", ephemeral=True)

        if all(t["ready"] for t in state["teams"].values()):
            await show_bo_selector(interaction.channel, self.channel_id)

class ModeButton(discord.ui.Button):
    def __init__(self, channel_id, mode):
        super().__init__(label=mode, style=discord.ButtonStyle.primary)
        self.channel_id = channel_id
        self.mode = mode

    async def callback(self, interaction):
        if not any(r.name == ARBITRO_ROLE_NAME for r in interaction.user.roles):
            return await interaction.response.send_message("⛔ Solo árbitro", ephemeral=True)

        await interaction.response.send_message(f"🎮 {self.mode} seleccionado", ephemeral=True)
        await start_pickban_flow(self.channel_id, self.mode)

async def show_bo_selector(channel, channel_id):
    view = discord.ui.View(timeout=None)
    view.add_item(ModeButton(channel_id, "BO3"))
    view.add_item(ModeButton(channel_id, "BO5"))
    await channel.send("⚖️ Árbitro: selecciona formato", view=view)

class MapButton(discord.ui.Button):
    def __init__(self, channel_id, map_key):
        super().__init__(label=map_key.split("::")[1], style=discord.ButtonStyle.secondary)
        self.channel_id = channel_id
        self.map_key = map_key

    async def callback(self, interaction):
        state = MATCHES[self.channel_id]
        step = state["flow"][state["step"]]

        if not user_can_interact(interaction, state, step):
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)

        await interaction.response.defer()

        if step["type"] == "ban":
            state["maps"][self.map_key].update({"status": "banned", "team": step["team"]})
        elif step["type"] == "pick_map":
            state["maps"][self.map_key].update({
                "status": "picked",
                "team": step["team"],
                "slot": step["slot"]
            })

        state["step"] += 1
        state["turn_started_at"] = asyncio.get_event_loop().time()
        await auto_decider(state)
        await ws_broadcast(str(self.channel_id))

        await interaction.message.edit(embed=build_embed(state), view=PickBanView(self.channel_id))

class SideButton(discord.ui.Button):
    def __init__(self, channel_id, side):
        super().__init__(label=side, style=discord.ButtonStyle.primary)
        self.channel_id = channel_id
        self.side = side

    async def callback(self, interaction):
        state = MATCHES[self.channel_id]
        step = state["flow"][state["step"]]

        if not user_can_interact(interaction, state, step):
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)

        await interaction.response.defer()

        for m in state["maps"].values():
            if m["slot"] == step["slot"]:
                m["side"] = self.side

        state["step"] += 1
        state["turn_started_at"] = asyncio.get_event_loop().time()
        await auto_decider(state)
        await ws_broadcast(str(self.channel_id))

        await interaction.message.edit(embed=build_embed(state), view=PickBanView(self.channel_id))

class PickBanView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        state = MATCHES[channel_id]

        if state["step"] >= len(state["flow"]):
            return

        step = state["flow"][state["step"]]

        if step["type"] in ("ban", "pick_map"):
            for k, m in state["maps"].items():
                if m["mode"] == step["mode"] and m["status"] == "free":
                    self.add_item(MapButton(channel_id, k))
        elif step["type"] == "pick_side":
            self.add_item(SideButton(channel_id, "JSOC"))
            self.add_item(SideButton(channel_id, "HERMANDAD"))

# =========================
# EMBED
# =========================
def describe_step(state):
    if state["step"] >= len(state["flow"]):
        return "✅ PICK & BAN FINALIZADO"

    step = state["flow"][state["step"]]
    action = step["type"].replace("_", " ").upper()
    team = step.get("team")

    turn = (
        state["teams"][team]["name"]
        if team in ("A", "B")
        else "SISTEMA"
    )

    return (
        f"**PASO {state['step'] + 1}/{len(state['flow'])}**\n"
        f"🎯 Acción: {action}\n"
        f"🕹️ Modo: {step.get('mode','')}\n"
        f"👤 Turno: {turn}"
    )

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
                lines.append(f"❌ {name} (Ban {m['team']})")
            elif m["status"] == "picked":
                side = f" · {m['side']}" if m["side"] else ""
                lines.append(f"✅ {name} — M{m['slot']} (Pick {m['team']}){side}")
            else:
                lines.append(f"⬜ {name}")

        embed.add_field(name=mode, value="\n".join(lines) or "—", inline=False)

    return embed

# =========================
# START COMMAND
# =========================
@bot.command()
async def start(ctx, teamA: discord.Role, teamB: discord.Role):
    MATCHES[ctx.channel.id] = {
        "flow": [],
        "step": 0,
        "maps": build_maps(),
        "mode": None,
        "turn_started_at": asyncio.get_event_loop().time(),
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
            description="Esperando a que ambos equipos estén listos",
            color=0x00ffcc
        ),
        view=view
    )

    await ctx.send(f"🎥 **Overlay OBS**:\n{overlay_url}")
    await ws_broadcast(str(ctx.channel.id))

async def start_pickban_flow(channel_id, mode):
    state = MATCHES[channel_id]
    state["mode"] = mode
    state["flow"] = FLOW_BO3 if mode == "BO3" else FLOW_BO5
    state["step"] = 0
    state["turn_started_at"] = asyncio.get_event_loop().time()

    for m in state["maps"].values():
        m.update({"status": "free", "team": None, "slot": None, "side": None})

    channel = bot.get_channel(channel_id)
    await channel.send(embed=build_embed(state), view=PickBanView(channel_id))
    await ws_broadcast(str(channel_id))

# =========================
# RUN
# =========================
bot.run(TOKEN)
