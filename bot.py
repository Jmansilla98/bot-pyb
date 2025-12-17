import discord
from discord.ext import commands
import asyncio
import json
from aiohttp import web
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
from datetime import datetime


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SPREADSHEET_ID = "14LZWTAdbXpppVpJMlvhp7CmgThD-6SAdaeTbw_Fl14A"
RANGE = "Matches!A:H"

creds = Credentials.from_service_account_file(
    "credentials.json", scopes=SCOPES
)
sheets_service = build("sheets", "v4", credentials=creds)


# =========================
# DISCORD BOT
# =========================
intents = discord.Intents.default()
intents.message_content = True

MATCHES = {}
WS_CLIENTS = {}  # match_id -> set(websocket)

# =========================
# MAP POOLS
# =========================
HP_MAPS = ["Blackheart", "Colossus", "Den", "Exposure", "Scar"]
SND_MAPS = ["Colossus", "Den", "Exposure", "Raid", "Scar"]
OVR_MAPS = ["Den", "Exposure", "Scar"]

# =========================
# FLOWS
# =========================
FLOW_BO5 = [
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

FLOW_BO3 = [
    {"mode":"HP","type":"ban","team":"A"},
    {"mode":"HP","type":"ban","team":"B"},
    {"mode":"HP","type":"pick_map","team":"A","slot":1},
    {"mode":"HP","type":"pick_side","team":"B","slot":1},

    {"mode":"SnD","type":"ban","team":"B"},
    {"mode":"SnD","type":"ban","team":"A"},
    {"mode":"SnD","type":"pick_map","team":"B","slot":2},
    {"mode":"SnD","type":"pick_side","team":"A","slot":2},

    {"mode":"OVR","type":"ban","team":"A"},
    {"mode":"OVR","type":"ban","team":"B"},
    {"mode":"OVR","type":"auto_decider","slot":3},
    {"mode":"OVR","type":"pick_side","team":"A","slot":3},
]

def append_match_to_sheet(channel, state):
    def fmt_maps():
        return " | ".join(
            f"M{m['slot']} {k.split('::')[1]} ({m['mode']}) "
            f"[{m['team']} {m['side'] or ''}]"
            for k, m in state["maps"].items()
            if m["slot"]
        )

    def fmt_bans():
        return " | ".join(
            f"{k.split('::')[1]} ({m['mode']}) Ban {m['team']}"
            for k, m in state["maps"].items()
            if m["status"] == "banned"
        )

    values = [[
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        channel.name,
        str(channel.id),
        state["series"],
        state["teams"]["A"]["name"],
        state["teams"]["B"]["name"],
        fmt_maps(),
        fmt_bans()
    ]]

    body = {"values": values}

    sheets_service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=RANGE,
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body=body
    ).execute()

    print("📊 Partido añadido a Google Sheets")

# =========================
# WEBSOCKET SERVER
# =========================
async def ws_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    match_id = request.query.get("match")
    if not match_id:
        await ws.close()
        return ws

    if match_id not in WS_CLIENTS:
        WS_CLIENTS[match_id] = set()

    WS_CLIENTS[match_id].add(ws)
    print(f"🟢 Overlay conectado al match {match_id}")

    try:
        async for _ in ws:
            pass
    finally:
        WS_CLIENTS[match_id].remove(ws)
        print(f"🔴 Overlay desconectado del match {match_id}")

    return ws


async def ws_broadcast(match_id):
    if match_id not in WS_CLIENTS:
        return

    state = MATCHES.get(int(match_id))
    if not state:
        return

    payload = json.dumps({
        "type": "state",      # compatibilidad total
        "state": state
    }, default=str)

    dead = set()
    for ws in WS_CLIENTS[match_id]:
        try:
            await ws.send_str(payload)
        except:
            dead.add(ws)

    WS_CLIENTS[match_id] -= dead


async def start_ws_server():
    app = web.Application()
    app.router.add_get("/ws", ws_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", 8765)
    await site.start()
    print("🟢 WS activo en ws://localhost:8765/ws")

# =========================
# BOT CLASS
# =========================
class PickBanBot(commands.Bot):
    async def setup_hook(self):
        await start_ws_server()

bot = PickBanBot(command_prefix="!", intents=intents)

# =========================
# UTILIDADES
# =========================
def build_maps():
    maps = {}
    for m in HP_MAPS:
        maps[f"HP::{m}"] = {"mode":"HP","status":"free","team":None,"slot":None,"side":None}
    for m in SND_MAPS:
        maps[f"SnD::{m}"] = {"mode":"SnD","status":"free","team":None,"slot":None,"side":None}
    for m in OVR_MAPS:
        maps[f"OVR::{m}"] = {"mode":"OVR","status":"free","team":None,"slot":None,"side":None}
    return maps


async def auto_decider(state):
    while state["step"] < len(state["flow"]):
        step = state["flow"][state["step"]]

        if step["type"] != "auto_decider":
            return
        
        free_maps = [
            k for k,m in state["maps"].items()
            if m["mode"] == step["mode"] and m["status"] == "free"
        ]

        if len(free_maps) != 1:
            return

        key = free_maps[0]
        state["maps"][key].update({
            "status":"picked",
            "team":"DECIDER",
            "slot":step["slot"]
        })

        state["step"] += 1

# =========================
# DISCORD UI
# =========================
class MapButton(discord.ui.Button):
    def __init__(self, channel_id, map_key):
        self.channel_id = channel_id
        self.map_key = map_key
        super().__init__(label=map_key.split("::")[1], style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        state = MATCHES[self.channel_id]
        step = state["flow"][state["step"]]

        if step["type"] == "ban":
            state["maps"][self.map_key].update({"status":"banned","team":step["team"]})
        elif step["type"] == "pick_map":
            state["maps"][self.map_key].update({
                "status":"picked",
                "team":step["team"],
                "slot":step["slot"]
            })

        state["step"] += 1
        await auto_decider(state)
        await ws_broadcast(str(self.channel_id))

        await interaction.response.edit_message(
            embed=build_embed(state),
            view=PickBanView(self.channel_id) if state["step"] < len(state["flow"]) else None
        )


class SideButton(discord.ui.Button):
    def __init__(self, channel_id, side):
        self.channel_id = channel_id
        self.side = side
        super().__init__(label=side, style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        state = MATCHES[self.channel_id]
        step = state["flow"][state["step"]]

        for m in state["maps"].values():
            if m["slot"] == step["slot"]:
                m["side"] = self.side
                break

        state["step"] += 1
        if state["step"] >= len(state["flow"]):
            append_match_to_sheet(interaction.channel, state)

            await interaction.message.edit(
                embed=build_embed(state),
                view=None
            )
            return
        await auto_decider(state)
        await ws_broadcast(str(self.channel_id))

        await interaction.response.edit_message(
            embed=build_embed(state),
            view=PickBanView(self.channel_id) if state["step"] < len(state["flow"]) else None
        )


class PickBanView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        state = MATCHES[channel_id]
        step = state["flow"][state["step"]]

        if step["type"] in ("ban","pick_map"):
            for k,m in state["maps"].items():
                if m["mode"] == step["mode"] and m["status"] == "free":
                    self.add_item(MapButton(channel_id, k))

        elif step["type"] == "pick_side":
            self.add_item(SideButton(channel_id, "JSOC"))
            self.add_item(SideButton(channel_id, "HERMANDAD"))

# =========================
# EMBED
# =========================
def build_embed(state):
    embed = discord.Embed(
        title=f"PICK & BAN — {state['series']}",
        color=0x2ecc71
    )

    def render_mode(mode):
        out = []
        for k,m in state["maps"].items():
            if m["mode"] != mode:
                continue
            name = k.split("::")[1]
            if m["status"] == "banned":
                out.append(f"❌ {name} (Ban {m['team']})")
            elif m["status"] == "picked":
                side = f" — Side {m['side']}" if m["side"] else ""
                out.append(f"✅ {name} — M{m['slot']} (Pick {m['team']}){side}")
            else:
                out.append(f"⬜ {name}")
        return "\n".join(out) or "—"

    embed.add_field(name="HP", value=render_mode("HP"), inline=False)
    embed.add_field(name="SnD", value=render_mode("SnD"), inline=False)
    embed.add_field(name="OVR", value=render_mode("OVR"), inline=False)

    if state["step"] < len(state["flow"]):
        

        s = state["flow"][state["step"]]
        embed.set_footer(text=f"{s['mode']} — {s['type']} — Team {s.get('team','')}")
    else:
        embed.set_footer(text="Pick & Ban completado")

    return embed

# =========================
# COMMAND
# =========================
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
        "A": {
            "name": teamA.name,
            "logo": f"{teamA.name}.png"
        },
        "B": {
            "name": teamB.name,
            "logo": f"{teamB.name}.png"
        }
    }

}


    await ws_broadcast(str(ctx.channel.id))

    await ctx.send(
        embed=build_embed(MATCHES[ctx.channel.id]),
        view=PickBanView(ctx.channel.id)
    )

    await ctx.send(f"Overlay: http://localhost/overlay.html?match={ctx.channel.id}")


bot.run("MTQ0OTE0NjA4MTg2OTk1NTI1NA.GeB1YR.mthPFgJljluB8-_XiigaYYRABYp-7Yk6R6W-KI")




