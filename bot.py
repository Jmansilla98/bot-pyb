import discord
from discord.ext import commands
import json
from aiohttp import web


WS_PORT = 8765

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# =========================
# MAP POOLS (TUS MAPAS)
# =========================
HP_MAPS  = ["Blackheart", "Colossus", "Den", "Exposure", "Scar"] 
SND_MAPS = ["Colossus", "Den", "Exposure", "Raid", "Scar"]
OVR_MAPS = ["Den", "Exposure", "Scar"]

# =========================
# FLOW (TU FLUJO ORIGINAL)
# =========================
FLOW = [
    {"mode":"HP","type":"ban","team":"A"},
    {"mode":"HP","type":"ban","team":"B"},
    {"mode":"HP","type":"pick_map","team":"A","slot":1},
    {"mode":"HP","type":"pick_side","team":"B","slot":1},
    {"mode":"HP","type":"pick_map","team":"B","slot":4},
    {"mode":"HP","type":"pick_side","team":"A","slot":4},

    {"mode":"SnD","type":"ban","team":"B"},
    {"mode":"SnD","type":"ban","team":"A"},
    {"mode":"SnD","type":"pick_map","team":"B","slot":2},
    {"mode":"SnD","type":"pick_side","team":"A","slot":2},
    {"mode":"SnD","type":"pick_map","team":"A","slot":5},
    {"mode":"SnD","type":"pick_side","team":"B","slot":5},

    {"mode":"OVR","type":"ban","team":"A"},
    {"mode":"OVR","type":"ban","team":"B"},
    {"mode":"OVR","type":"auto_decider","slot":3},
    {"mode":"OVR","type":"pick_side","team":"A","slot":3},
]

# =========================
# ESTADOS
# =========================
MATCHES = {}
WS_CLIENTS = {}

# =========================
# WEBSOCKET
# =========================
async def ws_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    channel_id = int(request.query.get("channel_id", 0))
    WS_CLIENTS[ws] = channel_id

    if channel_id in MATCHES:
        await ws.send_str(json.dumps({
            "type": "state",
            "state": MATCHES[channel_id]
        }))

    async for _ in ws:
        pass

    WS_CLIENTS.pop(ws, None)
    return ws


async def ws_broadcast(channel_id):
    for ws, ch in list(WS_CLIENTS.items()):
        if ch == channel_id:
            try:
                await ws.send_str(json.dumps({
                    "type": "state",
                    "state": MATCHES[channel_id]
                }))
            except:
                WS_CLIENTS.pop(ws, None)


async def start_ws():
    app = web.Application()
    app.router.add_get("/ws", ws_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", WS_PORT)
    await site.start()
    print(f"🟢 WS activo en ws://localhost:{WS_PORT}/ws")

# =========================
# DISCORD UI
# =========================
class PickBanView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        state = MATCHES[channel_id]
        step = FLOW[state["step"]]

        if step["type"] in ("ban", "pick_map"):
            for key, m in state["maps"].items():
                if m["mode"] == step["mode"] and m["status"] == "free":
                    self.add_item(MapButton(key, channel_id))

        elif step["type"] == "pick_side":
            self.add_item(SideButton("TEAM A", channel_id))
            self.add_item(SideButton("TEAM B", channel_id))


class MapButton(discord.ui.Button):
    def __init__(self, map_key, channel_id):
        mode, name = map_key.split("::")
        super().__init__(label=name, style=discord.ButtonStyle.secondary)
        self.map_key = map_key
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        state = MATCHES[self.channel_id]
        step = FLOW[state["step"]]

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
        await auto_decider_if_needed(state)
        await ws_broadcast(self.channel_id)

        await interaction.message.edit(
            embed=build_embed(state),
            view=PickBanView(self.channel_id) if state["step"] < len(FLOW) else None
        )


class SideButton(discord.ui.Button):
    def __init__(self, side, channel_id):
        super().__init__(label=side, style=discord.ButtonStyle.success)
        self.side = side
        self.channel_id = channel_id

    async def callback(self, interaction):
        await interaction.response.defer()
        state = MATCHES[self.channel_id]
        step = FLOW[state["step"]]

        for m in state["maps"].values():
            if m.get("slot") == step["slot"]:
                m["side"] = self.side

        state["step"] += 1
        await ws_broadcast(self.channel_id)

        await interaction.message.edit(
            embed=build_embed(state),
            view=PickBanView(self.channel_id) if state["step"] < len(FLOW) else None
        )


async def auto_decider_if_needed(state):
    if state["step"] < len(FLOW) and FLOW[state["step"]]["type"] == "auto_decider":
        for m in state["maps"].values():
            if m["mode"] == "OVR" and m["status"] == "free":
                m.update({"status": "picked", "slot": 3, "team": "DECIDER"})
        state["step"] += 1

def step_human(step):
    action_map = {
        "ban": "BANEAR MAPA",
        "pick_map": "ELEGIR MAPA",
        "pick_side": "ELEGIR BANDO",
        "auto_decider": "DECIDER AUTOMÁTICO"
    }

    return f"Turno **TEAM {step.get('team','-')}** → {action_map[step['type']]} ({step['mode']})"

# =========================
# EMBED
# =========================
def build_embed(state):
    embed = discord.Embed(title="🎮 PICK & BAN — BO5", color=0x2ecc71)

    def render(mode):
        lines = []
        for key, m in state["maps"].items():
            if m["mode"] != mode:
                continue
            name = key.split("::")[1]

            if m["status"] == "free":
                lines.append(f"▫️ {name}")
            elif m["status"] == "banned":
                lines.append(f"❌ {name} (BAN {m['team']})")
            elif m["status"] == "picked":
                txt = f"✅ {name} — MAPA {m['slot']} (Pick {m['team']})"
                if m.get("side"):
                    txt += f" | Side {m['side']}"
                lines.append(txt)

        return "\n".join(lines) or "—"

    embed.add_field(name="🟥 HP", value=render("HP"), inline=False)
    embed.add_field(name="🟦 SnD", value=render("SnD"), inline=False)
    embed.add_field(name="🟨 Overload", value=render("OVR"), inline=False)
    if state["step"] < len(FLOW):
        step = FLOW[state["step"]]
        embed.add_field(
            name="⏱️ Turno actual",
            value=step_human(step),
            inline=False
        )
    else:
        embed.add_field(
            name="⏱️ Turno actual",
            value="✅ Pick & Ban completado",
            inline=False
        )

    return embed

# =========================
# START
# =========================
@bot.command()
async def start(ctx):
    maps = {}

    for m in HP_MAPS:
        maps[f"HP::{m}"] = {"mode": "HP", "status": "free"}

    for m in SND_MAPS:
        maps[f"SnD::{m}"] = {"mode": "SnD", "status": "free"}

    for m in OVR_MAPS:
        maps[f"OVR::{m}"] = {"mode": "OVR", "status": "free"}

    MATCHES[ctx.channel.id] = {
        "step": 0,
        "maps": maps
    }

    await ctx.send(
        embed=build_embed(MATCHES[ctx.channel.id]),
        view=PickBanView(ctx.channel.id)
    )
    overlay_url = f"http://localhost:8765/overlay.html?channel_id={ctx.channel.id}"

    await ctx.send(
        f"🖥️ **Overlay para este partido:**\n{overlay_url}"
    )

# =========================
# READY
# =========================
@bot.event
async def on_ready():
    print(f"🤖 Bot listo como {bot.user}")
    await start_ws()

bot.run("MTQ0OTE0NjA4MTg2OTk1NTI1NA.GeB1YR.mthPFgJljluB8-_XiigaYYRABYp-7Yk6R6W-KI")



