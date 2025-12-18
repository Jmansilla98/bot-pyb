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
APP_URL = os.getenv("APP_URL", "").rstrip("/")
PORT = int(os.getenv("PORT", "8080"))
TOKEN = os.getenv("DISCORD_TOKEN")
BASE_DIR = pathlib.Path(__file__).parent
OVERLAY_DIR = BASE_DIR / "overlay"

CLAIM_TIME_SECONDS = int(os.getenv("CLAIM_TIME_SECONDS", "5"))
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
WS_CLIENTS = {}

HP_MAPS = ["Blackheart", "Colossus", "Den", "Exposure", "Scar"]
SND_MAPS = ["Colossus", "Den", "Exposure", "Raid", "Scar"]
OVR_MAPS = ["Den", "Exposure", "Scar"]

# =========================
# FLOWS
# =========================
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

def user_can_interact(interaction, state, step):
    if any(r.name == ARBITRO_ROLE_NAME for r in interaction.user.roles):
        return True
    if not step.get("team"):
        return False
    role_id = state["teams"][step["team"]]["role_id"]
    return any(r.id == role_id for r in interaction.user.roles)

# =========================
# START COMMAND
# =========================
@bot.command()
async def start(ctx, teamA: discord.Role, teamB: discord.Role):
    channel_id = ctx.channel.id

    MATCHES[channel_id] = {
        "channel_id": channel_id,
        "channel_name": ctx.channel.name,
        "flow": [],
        "step": 0,
        "maps": build_maps(),
        "map_results": {},
        "mode": None,
        "phase": "waiting_accept",
        "teams": {
            "A": {"name": teamA.name, "role_id": teamA.id},
            "B": {"name": teamB.name, "role_id": teamB.id},
        }
    }

    overlay_url = f"{APP_URL}/overlay.html?match={channel_id}"

    await ctx.send(
        embed=discord.Embed(
            title="🎮 Pick & Bans",
            description=f"{teamA.name} vs {teamB.name}",
            color=0x00ffcc
        )
    )
    await ctx.send(f"🎥 **Overlay OBS**:\n{overlay_url}")
# =========================
# UI
# =========================
class AcceptButton(discord.ui.Button):
    def __init__(self, channel_id, team):
        super().__init__(label=f"Aceptar TEAM {team}", style=discord.ButtonStyle.primary)
        self.channel_id = channel_id
        self.team = team

    async def callback(self, interaction):
        state = MATCHES[self.channel_id]
        role_id = state["teams"][self.team]["role_id"]

        if not any(r.id == role_id for r in interaction.user.roles):
            return await interaction.response.send_message("⛔ No es tu equipo", ephemeral=True)

        state["teams"][self.team]["accepted"] = True
        await interaction.response.send_message("✅ Equipo aceptado", ephemeral=True)

        if all(t["accepted"] for t in state["teams"].values()):
            await show_bo_selector(interaction.channel, self.channel_id)

async def show_bo_selector(channel, channel_id):
    view = discord.ui.View(timeout=None)
    view.add_item(ModeButton(channel_id, "BO3"))
    view.add_item(ModeButton(channel_id, "BO5"))
    await channel.send("⚖️ Árbitro: selecciona formato", view=view)

class ModeButton(discord.ui.Button):
    def __init__(self, channel_id, mode):
        super().__init__(label=mode, style=discord.ButtonStyle.success)
        self.channel_id = channel_id
        self.mode = mode

    async def callback(self, interaction):
        if not any(r.name == ARBITRO_ROLE_NAME for r in interaction.user.roles):
            return await interaction.response.send_message("⛔ Solo árbitro", ephemeral=True)
        await interaction.response.send_message(f"🎮 {self.mode} seleccionado", ephemeral=True)
        await start_pickban_flow(self.channel_id, self.mode)

async def start_pickban_flow(channel_id, mode):
    state = MATCHES[channel_id]
    state["flow"] = FLOW_BO3 if mode == "BO3" else FLOW_BO5
    state["step"] = 0
    state["mode"] = mode
    state["phase"] = "pickban"

    for m in state["maps"].values():
        m.update({"status": "free", "team": None, "side": None, "slot": None})

    channel = bot.get_channel(channel_id)
    await channel.send(embed=build_embed(state), view=PickBanView(channel_id))
    await ws_broadcast(str(channel_id))

# =========================
# PICK & BAN VIEW
# =========================
class PickBanView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        state = MATCHES[channel_id]

        if state["step"] >= len(state["flow"]):
            for slot in sorted(
                m["slot"] for m in state["maps"].values() if m["status"] == "picked"
            ):
                self.add_item(ResultButton(channel_id, slot))

            

        step = state["flow"][state["step"]]

        if step["type"] in ("ban", "pick_map"):
            for k, m in state["maps"].items():
                if m["mode"] == step["mode"] and m["status"] == "free":
                    self.add_item(MapButton(channel_id, k))
        elif step["type"] == "pick_side":
            self.add_item(SideButton(channel_id, "ATK"))
            self.add_item(SideButton(channel_id, "DEF"))

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
        await auto_decider(state)
        await ws_broadcast(str(self.channel_id))
        await interaction.message.edit(embed=build_embed(state), view=PickBanView(self.channel_id))

class ResultButton(discord.ui.Button):
    def __init__(self, channel_id, slot):
        super().__init__(label=f"Resultado M{slot}", style=discord.ButtonStyle.success)
        self.channel_id = channel_id
        self.slot = slot

    async def callback(self, interaction):
        if not any(r.name == ARBITRO_ROLE_NAME for r in interaction.user.roles):
            return await interaction.response.send_message("⛔ Solo árbitro", ephemeral=True)
        await interaction.response.send_modal(ResultModal(self.channel_id, self.slot))

class ResultModal(discord.ui.Modal, title="Resultado del mapa"):
    winner = discord.ui.TextInput(label="Ganador (A o B)")
    score = discord.ui.TextInput(label="Marcador")

    def __init__(self, channel_id, slot):
        super().__init__()
        self.channel_id = channel_id
        self.slot = slot

    async def on_submit(self, interaction):
        state = MATCHES[self.channel_id]
        state["map_results"][self.slot] = {
            "winner": self.winner.value.upper(),
            "score": self.score.value
        }

        await ws_broadcast(str(self.channel_id))
        await interaction.response.send_message("✅ Resultado guardado", ephemeral=True)

        # ¿Hay ganador de serie?
        wins_a = sum(1 for r in state["map_results"].values() if r["winner"] == "A")
        wins_b = sum(1 for r in state["map_results"].values() if r["winner"] == "B")
        needed = 2 if state["mode"] == "BO3" else 3

        if wins_a == needed or wins_b == needed:
            winner = state["teams"]["A"]["name"] if wins_a > wins_b else state["teams"]["B"]["name"]

            await interaction.channel.send(
                embed=discord.Embed(
                    title="🏆 RESULTADO FINAL",
                    description=f"**{winner}** gana la serie {wins_a}-{wins_b}",
                    color=0x00ff88
                )
            )

            send_match_to_sheets(state)


# =========================
# EMBED
# =========================
def build_embed(state):
    embed = discord.Embed(
        title=f"PICK & BAN — {state.get('mode', '')}",
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
                side = f" — Side {m['side']}" if m["side"] else ""
                lines.append(f"✅ {name} — M{m['slot']} (Pick {m['team']}){side}")
            else:
                lines.append(f"⬜ {name}")

        embed.add_field(name=mode, value="\n".join(lines) or "—", inline=False)

    return embed

# =========================
# RUN
# =========================
bot.run(TOKEN)
