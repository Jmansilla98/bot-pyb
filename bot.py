import discord
from discord.ext import commands
import asyncio
import json
from aiohttp import web
import aiohttp
import pathlib
import os
from google_sheets import send_match_to_sheets

# =========================
# CONFIG
# =========================
APP_URL = os.getenv("APP_URL")
PORT = int(os.getenv("PORT", "8080"))
TOKEN = os.getenv("DISCORD_TOKEN")
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
# AIOHTTP APP
# =========================
app = web.Application()
# sirve JS / CSS / imágenes
app.router.add_static("/static/", OVERLAY_DIR)
routes = web.RouteTableDef()

@routes.get("/")
async def index(request):
    return web.FileResponse("overlay.html")

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
    print(f"🟢 Overlay conectado al match {match_id}")

    if int(match_id) in MATCHES:
        await ws_broadcast(match_id)

    try:
        async for _ in ws:
            pass
    finally:
        WS_CLIENTS[match_id].discard(ws)
        print(f"🔴 Overlay desconectado del match {match_id}")

    return ws

app.add_routes(routes)

# =========================
# WEBSOCKET BROADCAST
# =========================
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

# =========================
# WEB START
# =========================
async def start_web():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"🌐 Web + WS activos en puerto {PORT}")

# =========================
# KEEP ALIVE
# =========================
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

# =========================
# DISCORD EVENTS
# =========================
@bot.event
async def on_ready():
    asyncio.create_task(start_web())
    asyncio.create_task(keep_alive())
    print("🤖 Bot listo (Cloud)")

# =========================
# COMANDOS
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

async def auto_decider(state):
    while state["step"] < len(state["flow"]):
        step = state["flow"][state["step"]]
        if step["type"] != "auto_decider":
            return
        free_maps = [k for k, m in state["maps"].items() if m["mode"] == step["mode"] and m["status"] == "free"]
        if len(free_maps) != 1:
            return
        key = free_maps[0]
        state["maps"][key].update({"status": "picked", "team": "DECIDER", "slot": step["slot"]})
        state["step"] += 1

# =========================
# UI
# =========================
class MapButton(discord.ui.Button):
    def __init__(self, channel_id, map_key):
        self.channel_id = channel_id
        self.map_key = map_key
        super().__init__(label=map_key.split("::")[1], style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        state = MATCHES[self.channel_id]
        step = state["flow"][state["step"]]

    # ⛔ PERMISOS ANTES DE RESPONDER
        if not user_can_interact(interaction, state, step):
            await interaction.response.send_message(
                "⛔ No es tu turno.",
                ephemeral=True
            )
            return
    # ✅ Ahora sí, defer
        await interaction.response.defer()

    # ---- lógica existente ----
        if step["type"] == "ban":
            state["maps"][self.map_key].update({
                "status": "banned",
                "team": step["team"]
            })
        elif step["type"] == "pick_map":
            state["maps"][self.map_key].update({
                "status": "picked",
                "team": step["team"],
                "slot": step["slot"]
            })

        state["step"] += 1
        await auto_decider(state)
        await ws_broadcast(str(self.channel_id))

        await interaction.message.edit(
            embed=build_embed(state),
            view=PickBanView(self.channel_id) if state["step"] < len(state["flow"]) else None
        )



class SideButton(discord.ui.Button):
    def __init__(self, channel_id, side):
        self.channel_id = channel_id
        self.side = side
        super().__init__(label=side, style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        state = MATCHES[self.channel_id]
        step = state["flow"][state["step"]]

        if not user_can_interact(interaction, state, step):
            await interaction.response.send_message(
                "⛔ No es tu turno.",
                ephemeral=True
            )
            return

        await interaction.response.defer()

        for m in state["maps"].values():
            if m["slot"] == step.get("slot"):
                m["side"] = self.side
                break

        state["step"] += 1
        await auto_decider(state)
        await ws_broadcast(str(self.channel_id))

        await interaction.message.edit(
            embed=build_embed(state),
            view=PickBanView(self.channel_id) if state["step"] < len(state["flow"]) else None
        )



class PickBanView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        state = MATCHES[channel_id]

        # ✅ si ya terminó, no construyas botones
        if state["step"] >= len(state["flow"]):
            if state.get("sheets_exported"):
                send_match_to_sheets(state)
            state["sheets_exported"] = True

            return

        step = state["flow"][state["step"]]

        if step["type"] in ("ban", "pick_map"):
            for k, m in state["maps"].items():
                if m["mode"] == step["mode"] and m["status"] == "free":
                    self.add_item(MapButton(channel_id, k))

        elif step["type"] == "pick_side":
            self.add_item(SideButton(channel_id, "JSOC"))
            self.add_item(SideButton(channel_id, "HERMANDAD"))


def build_embed(state):
    embed = discord.Embed(title=f"PICK & BAN — {state['series']}", color=0x2ecc71)

    for mode in ["HP", "SnD", "OVR"]:
        lines = []
        for k, m in state["maps"].items():
            if m["mode"] != mode:
                continue
            name = k.split("::")[1]

            if m["status"] == "banned":
                lines.append(f"❌ {name} (Ban {m['team']})")

            elif m["status"] == "picked":
                side = f" — Side {m['side']}" if m["side"] else ""
                slot = f"M{m['slot']}" if m["slot"] else "M?"
                lines.append(f"✅ {name} — {slot} (Pick {m['team']}){side}")

            else:
                lines.append(f"⬜ {name}")

        embed.add_field(name=mode, value="\n".join(lines) or "—", inline=False)

    return embed
def user_can_interact(interaction: discord.Interaction, state: dict, step: dict) -> bool:
    member = interaction.user

    # 👑 Árbitro puede siempre
    if any(role.name.lower() == "arbitro" for role in member.roles):
        return True

    # Si el step no tiene team (auto_decider, etc)
    if not step.get("team"):
        return False

    team_key = step["team"]  # "A" o "B"
    team_role_id = state["teams"][team_key]["role_id"]

    return any(role.id == team_role_id for role in member.roles)

async def keep_alive():
    if not APP_URL:
        return
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await session.get(APP_URL)
            except:
                pass
            await asyncio.sleep(300)  # cada 5 minutos

@bot.command()
async def start(ctx, series: str, teamA: discord.Role, teamB: discord.Role):
    series = series.upper()
    flow = FLOW_BO5 if series == "BO5" else FLOW_BO3

    MATCHES[ctx.channel.id] = {
        "series": series,
        "flow": flow,
        "step": 0,
        "maps": build_maps(),
        "teams": {
            "A": {"name": teamA.name, "logo": f"{teamA.name}.png", "role_id": teamA.id},
            "B": {"name": teamB.name, "logo": f"{teamB.name}.png", "role_id": teamB.id},
        }
    }
    overlay_url = f"https://bot-pyb.fly.dev/overlay.html?match={ctx.channel.id}"
    await ws_broadcast(str(ctx.channel.id))
    await ctx.send("Pick & Ban iniciado.")
    await ctx.send(
        embed=build_embed(MATCHES[ctx.channel.id]),
        view=PickBanView(ctx.channel.id)
    )
    await ctx.send(
        f"🎥 **Overlay OBS**:\n{overlay_url}"
    )

# =========================
# RUN
# =========================
bot.run(TOKEN)
