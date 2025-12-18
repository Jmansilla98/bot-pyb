import discord
from discord.ext import commands
import asyncio
import json
from aiohttp import web
import aiohttp
import pathlib
import os
from datetime import datetime, timezone

from google_sheets import send_match_to_sheets

# =========================
# CONFIG
# =========================
APP_URL = os.getenv("APP_URL")  # ej: https://bot-pyb.fly.dev
PORT = int(os.getenv("PORT", "8080"))
TOKEN = os.getenv("DISCORD_TOKEN")
BASE_DIR = pathlib.Path(__file__).parent
OVERLAY_DIR = BASE_DIR / "overlay"

CLAIM_TIME_SECONDS = int(os.getenv("CLAIM_TIME_SECONDS", "5"))
ARBITRO_ROLE_NAME = os.getenv("ARBITRO_ROLE_NAME", "Arbitro")

# Challonge
CHALLONGE_API_KEY = os.getenv("CHALLONGE_API_KEY")
CHALLONGE_TOURNAMENT_ID = os.getenv("CHALLONGE_TOURNAMENT_ID")

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

    # Enviar state actual al conectar
    if match_id.isdigit() and int(match_id) in MATCHES:
        await ws_broadcast(match_id)

    try:
        async for _ in ws:
            pass
    finally:
        WS_CLIENTS.get(match_id, set()).discard(ws)

    return ws

app.add_routes(routes)

async def ws_broadcast(match_id: str):
    if not str(match_id).isdigit():
        return
    state = MATCHES.get(int(match_id))
    if not state:
        return

    payload = json.dumps({"type": "state", "state": state})
    for ws in list(WS_CLIENTS.get(str(match_id), [])):
        try:
            await ws.send_str(payload)
        except:
            WS_CLIENTS.get(str(match_id), set()).discard(ws)

async def start_web():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"🌐 Web/WS en :{PORT}")

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

def user_is_arbitro(member: discord.Member) -> bool:
    return any(r.name.lower() == ARBITRO_ROLE_NAME.lower() for r in getattr(member, "roles", []))

def user_can_interact(interaction: discord.Interaction, state: dict, step: dict) -> bool:
    member = interaction.user
    if user_is_arbitro(member):
        return True
    if not step.get("team"):
        return False
    role_id = state["teams"][step["team"]]["role_id"]
    return any(r.id == role_id for r in member.roles)

def wins_required(state: dict) -> int:
    mode = state.get("mode") or state.get("series")
    return 3 if mode == "BO5" else 2

def compute_wins(state: dict) -> tuple[int, int]:
    a = sum(1 for r in state.get("map_results", {}).values() if r.get("winner") == "A")
    b = sum(1 for r in state.get("map_results", {}).values() if r.get("winner") == "B")
    return a, b

def series_winner(state: dict) -> str | None:
    a, b = compute_wins(state)
    need = wins_required(state)
    if a >= need:
        return "A"
    if b >= need:
        return "B"
    return None

def overlay_base_url() -> str:
    if APP_URL:
        return APP_URL.rstrip("/")
    # fallback local (por si ejecutas local)
    return f"http://localhost:{PORT}"

def create_initial_embed(teamA: str, teamB: str) -> discord.Embed:
    return discord.Embed(
        title="🎮 Pick & Bans",
        description=(f"**{teamA}** vs **{teamB}**\n\n"
                     "Ambos equipos deben **aceptar** para habilitar la selección BO3/BO5 por árbitro."),
        color=0x00ffcc
    )

# =========================
# CHALLONGE REPORT (real)
# =========================
async def report_to_challonge(state: dict):
    if state.get("challonge_reported"):
        return

    if not CHALLONGE_API_KEY or not CHALLONGE_TOURNAMENT_ID:
        print("[CHALLONGE] No configurado (API_KEY o TOURNAMENT_ID)")
        return

    challonge_data = state.get("challonge")
    if not challonge_data:
        print("[CHALLONGE] No hay state['challonge'] (match_id/participants)")
        return

    winner_team = series_winner(state)
    if not winner_team:
        print("[CHALLONGE] No hay ganador aún, no se reporta")
        return

    match_id = challonge_data["match_id"]
    wins_a, wins_b = compute_wins(state)
    scores_csv = f"{wins_a}-{wins_b}"

    winner_id = challonge_data["playerA_id"] if winner_team == "A" else challonge_data["playerB_id"]
    url = f"https://api.challonge.com/v1/tournaments/{CHALLONGE_TOURNAMENT_ID}/matches/{match_id}.json"

    payload = {
        "api_key": CHALLONGE_API_KEY,
        "match": {"scores_csv": scores_csv, "winner_id": winner_id}
    }

    async with aiohttp.ClientSession() as session:
        async with session.put(url, data=payload) as resp:
            text = await resp.text()
            if resp.status == 200:
                state["challonge_reported"] = True
                print(f"[CHALLONGE] OK {scores_csv} -> match {match_id}")
            else:
                print(f"[CHALLONGE] ERROR {resp.status}: {text}")

# =========================
# SHEETS EXPORT (only once, only after series has winner)
# =========================
def export_to_sheets_if_ready(state: dict):
    if state.get("sheets_exported"):
        return

    w = series_winner(state)
    if not w:
        return  # aún no hay ganador de serie

    # Asegurar claves mínimas que tu google_sheets.py exige
    state.setdefault("channel_id", state.get("channel_id"))
    state.setdefault("channel_name", state.get("channel_name"))
    state.setdefault("series", state.get("mode") or state.get("series") or "")
    state.setdefault("created_at", state.get("created_at"))

    try:
        send_match_to_sheets(state)
        state["sheets_exported"] = True
        print("[SHEETS] Export OK")
    except KeyError as e:
        print(f"[SHEETS] Falta clave: {e}")
    except Exception as e:
        print(f"[SHEETS] ERROR: {e}")

# =========================
# CLAIMS WINDOW
# =========================
async def open_claims_if_needed(state: dict, channel_id: int):
    # Solo si hay ganador
    if not series_winner(state):
        return
    if state.get("claim_open") or state.get("claim_finished"):
        return

    state["claim_open"] = True
    await ws_broadcast(str(channel_id))

    asyncio.create_task(claim_countdown(state, channel_id))

async def claim_countdown(state: dict, channel_id: int):
    await asyncio.sleep(CLAIM_TIME_SECONDS)

    state["claim_open"] = False
    state["claim_finished"] = True

    # Export Sheets (solo una vez) + Challonge (solo una vez)
    export_to_sheets_if_ready(state)
    await report_to_challonge(state)

    await ws_broadcast(str(channel_id))

# =========================
# UI - BUTTONS / MODALS
# =========================
class AcceptButton(discord.ui.Button):
    def __init__(self, channel_id: int, team: str):
        super().__init__(label=f"Aceptar TEAM {team}", style=discord.ButtonStyle.primary)
        self.channel_id = channel_id
        self.team = team

    async def callback(self, interaction: discord.Interaction):
        state = MATCHES[self.channel_id]
        role_id = state["teams"][self.team]["role_id"]

        if not any(r.id == role_id for r in interaction.user.roles):
            return await interaction.response.send_message("⛔ No es tu equipo", ephemeral=True)

        state["teams"][self.team]["accepted"] = True
        await interaction.response.send_message("✅ Equipo aceptado", ephemeral=True)
        await ws_broadcast(str(self.channel_id))

        if all(t["accepted"] for t in state["teams"].values()):
            await show_bo_selector(interaction.channel, self.channel_id)

async def show_bo_selector(channel: discord.TextChannel, channel_id: int):
    view = discord.ui.View(timeout=None)
    view.add_item(ModeButton(channel_id, "BO3"))
    view.add_item(ModeButton(channel_id, "BO5"))
    await channel.send("⚖️ **Árbitro:** selecciona el formato", view=view)

class ModeButton(discord.ui.Button):
    def __init__(self, channel_id: int, mode: str):
        super().__init__(label=mode, style=discord.ButtonStyle.success)
        self.channel_id = channel_id
        self.mode = mode

    async def callback(self, interaction: discord.Interaction):
        if not user_is_arbitro(interaction.user):
            return await interaction.response.send_message("⛔ Solo árbitro", ephemeral=True)

        await interaction.response.send_message(f"🎮 {self.mode} seleccionado", ephemeral=True)
        await start_pickban_flow(self.channel_id, self.mode)

async def start_pickban_flow(channel_id: int, mode: str):
    state = MATCHES[channel_id]

    state["flow"] = FLOW_BO3 if mode == "BO3" else FLOW_BO5
    state["step"] = 0
    state["mode"] = mode
    state["series"] = mode
    state["phase"] = "pickban"

    for m in state["maps"].values():
        m.update({"status": "free", "team": None, "side": None, "slot": None})

    channel = bot.get_channel(channel_id)
    if not channel:
        return

    # Guardar el message_id principal para editarlo siempre
    msg = await channel.send(embed=build_embed(state), view=PickBanView(channel_id))
    state["pb_message_id"] = msg.id

    await ws_broadcast(str(channel_id))

class MapButton(discord.ui.Button):
    def __init__(self, channel_id: int, map_key: str):
        super().__init__(label=map_key.split("::")[1], style=discord.ButtonStyle.secondary)
        self.channel_id = channel_id
        self.map_key = map_key

    async def callback(self, interaction: discord.Interaction):
        state = MATCHES[self.channel_id]
        step = state["flow"][state["step"]]

        if not user_can_interact(interaction, state, step):
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)

        await interaction.response.defer()

        if step["type"] == "ban":
            state["maps"][self.map_key].update({"status": "banned", "team": step["team"]})
        elif step["type"] == "pick_map":
            state["maps"][self.map_key].update({"status": "picked", "team": step["team"], "slot": step["slot"]})

        state["step"] += 1
        await auto_decider(state)

        await ws_broadcast(str(self.channel_id))

        await interaction.message.edit(
            embed=build_embed(state),
            view=PickBanView(self.channel_id)
        )

class SideButton(discord.ui.Button):
    def __init__(self, channel_id: int, side: str):
        super().__init__(label=side, style=discord.ButtonStyle.primary)
        self.channel_id = channel_id
        self.side = side

    async def callback(self, interaction: discord.Interaction):
        state = MATCHES[self.channel_id]
        step = state["flow"][state["step"]]

        if not user_can_interact(interaction, state, step):
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)

        await interaction.response.defer()

        for m in state["maps"].values():
            if m["slot"] == step["slot"]:
                m["side"] = self.side
                break

        state["step"] += 1
        await auto_decider(state)

        await ws_broadcast(str(self.channel_id))

        await interaction.message.edit(
            embed=build_embed(state),
            view=PickBanView(self.channel_id)
        )

class ResultButton(discord.ui.Button):
    def __init__(self, channel_id: int, slot: int):
        super().__init__(label=f"Resultado M{slot}", style=discord.ButtonStyle.success)
        self.channel_id = channel_id
        self.slot = slot

    async def callback(self, interaction: discord.Interaction):
        if not user_is_arbitro(interaction.user):
            return await interaction.response.send_message("⛔ Solo árbitro", ephemeral=True)
        await interaction.response.send_modal(ResultModal(self.channel_id, self.slot))

class ResultModal(discord.ui.Modal, title="Resultado del mapa"):
    winner = discord.ui.TextInput(label="Ganador (A o B)", placeholder="A o B", max_length=1)
    score = discord.ui.TextInput(label="Marcador", placeholder="250-50 / 6-3 / 3-1", max_length=10)

    def __init__(self, channel_id: int, slot: int):
        super().__init__()
        self.channel_id = channel_id
        self.slot = slot

    async def on_submit(self, interaction: discord.Interaction):
        state = MATCHES[self.channel_id]

        w = (self.winner.value or "").upper().strip()
        if w not in ("A", "B"):
            return await interaction.response.send_message("⛔ Ganador inválido (A o B)", ephemeral=True)

        state.setdefault("map_results", {})
        state["map_results"][self.slot] = {"winner": w, "score": (self.score.value or "").strip()}

        # Actualizar embed principal con resultados (sin mandar mensaje nuevo)
        try:
            await interaction.response.send_message(f"✅ Resultado M{self.slot} guardado", ephemeral=True)
        except:
            pass

        await ws_broadcast(str(self.channel_id))

        # Editar el mensaje principal del pick&ban si lo tenemos guardado
        pb_id = state.get("pb_message_id")
        if pb_id:
            try:
                msg = await interaction.channel.fetch_message(pb_id)
                await msg.edit(embed=build_embed(state), view=PickBanView(self.channel_id))
            except:
                pass

        # Si hay ganador -> abrir reclamaciones (timeout) y luego challonge/sheets
        await open_claims_if_needed(state, self.channel_id)

class ClaimButton(discord.ui.Button):
    def __init__(self, channel_id: int):
        super().__init__(label="🚨 Reclamación", style=discord.ButtonStyle.danger)
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        state = MATCHES[self.channel_id]
        if not state.get("claim_open"):
            return await interaction.response.send_message("⛔ Reclamaciones cerradas", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await trigger_ticket_king(interaction, state)

class PickBanView(discord.ui.View):
    def __init__(self, channel_id: int):
        super().__init__(timeout=None)
        state = MATCHES[channel_id]

        # Si aún no se eligió BO, no hay botones aquí
        if not state.get("flow"):
            return

        # Si terminó el flow -> botones de resultados + reclamación si está abierta
        if state["step"] >= len(state["flow"]):
            picked_slots = sorted({m["slot"] for m in state["maps"].values() if m["status"] == "picked" and m.get("slot")})
            for slot in picked_slots:
                self.add_item(ResultButton(channel_id, slot))
            if state.get("claim_open"):
                self.add_item(ClaimButton(channel_id))
            return

        step = state["flow"][state["step"]]

        if step["type"] in ("ban", "pick_map"):
            for k, m in state["maps"].items():
                if m["mode"] == step["mode"] and m["status"] == "free":
                    self.add_item(MapButton(channel_id, k))
        elif step["type"] == "pick_side":
            self.add_item(SideButton(channel_id, "ATK"))
            self.add_item(SideButton(channel_id, "DEF"))

def build_embed(state: dict) -> discord.Embed:
    title_mode = state.get("mode") or ""
    a_w, b_w = compute_wins(state)
    need = wins_required(state)
    w = series_winner(state)

    status_line = f"Marcador serie: **{a_w}-{b_w}** (al mejor de {need*2-1})"
    if w:
        status_line += f"\n🏆 Ganador provisional: **TEAM {w}**"
    if state.get("claim_open"):
        status_line += f"\n⏳ Reclamaciones abiertas: {CLAIM_TIME_SECONDS}s"

    embed = discord.Embed(
        title=f"PICK & BAN — {title_mode}",
        description=status_line,
        color=0x2ecc71
    )

    for mode in ["HP", "SnD", "OVR"]:
        lines = []
        # ordenar por nombre para bans/free, pero picked por slot
        items = [(k, m) for k, m in state["maps"].items() if m["mode"] == mode]

        # primero picked por slot, luego el resto
        picked = sorted([x for x in items if x[1]["status"] == "picked"], key=lambda x: x[1].get("slot") or 99)
        other = [x for x in items if x[1]["status"] != "picked"]
        ordered = picked + other

        for k, m in ordered:
            name = k.split("::")[1]

            if m["status"] == "banned":
                lines.append(f"❌ {name} (Ban {m['team']})")

            elif m["status"] == "picked":
                side = f" — Side {m['side']}" if m.get("side") else ""
                slot = f"M{m['slot']}" if m.get("slot") else "M?"
                score_txt = ""
                res = state.get("map_results", {}).get(m.get("slot"))
                if res and res.get("score"):
                    score_txt = f" — **{res['score']}**"
                lines.append(f"✅ {name} — {slot} (Pick {m['team']}){side}{score_txt}")

            else:
                lines.append(f"⬜ {name}")

        embed.add_field(name=mode, value="\n".join(lines) or "—", inline=False)

    return embed

async def trigger_ticket_king(interaction: discord.Interaction, state: dict):
    await interaction.channel.send(
        "🚨 **RECLAMACIÓN ABIERTA**\n"
        f"{state['teams']['A']['name']} vs {state['teams']['B']['name']}\n"
        "Motivo: Disputa de resultado"
    )
    await interaction.followup.send("✅ Ticket enviado", ephemeral=True)

# =========================
# START COMMAND
# =========================
@bot.command()
async def start(ctx, teamA: discord.Role, teamB: discord.Role):
    channel_id = ctx.channel.id
    channel_name = getattr(ctx.channel, "name", str(channel_id))

    MATCHES[channel_id] = {
        # claves que tu google_sheets.py pide
        "channel_id": channel_id,
        "channel_name": channel_name,
        "created_at": datetime.now(timezone.utc).isoformat(),

        "flow": [],
        "step": 0,
        "maps": build_maps(),
        "map_results": {},

        "claim_open": False,
        "claim_finished": False,

        "challonge_ready": False,
        "challonge_reported": False,

        # BO3/BO5
        "mode": None,
        "series": None,
        "phase": "waiting_accept",

        # Teams
        "teams": {
            "A": {"name": teamA.name, "role_id": teamA.id, "accepted": False},
            "B": {"name": teamB.name, "role_id": teamB.id, "accepted": False},
        },

        # challonge (se rellena por tu bot challonge, si lo conectas)
        # "challonge": {"match_id": "...", "playerA_id": "...", "playerB_id": "..."}
    }

    overlay_url = f"{overlay_base_url()}/overlay.html?match={channel_id}"

    view = discord.ui.View(timeout=None)
    view.add_item(AcceptButton(channel_id, "A"))
    view.add_item(AcceptButton(channel_id, "B"))

    await ctx.send(embed=create_initial_embed(teamA.name, teamB.name), view=view)
    await ctx.send(f"🎥 **Overlay OBS**:\n{overlay_url}")

    await ws_broadcast(str(channel_id))

# =========================
# RUN
# =========================
bot.run(TOKEN)
