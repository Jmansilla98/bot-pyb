import discord
from discord.ext import commands
import asyncio
import json
from aiohttp import web
import pathlib
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# =========================
# CONFIG
# =========================
APP_URL = os.getenv("APP_URL", "").rstrip("/")  # ej: https://tuapp.fly.dev
PORT = int(os.getenv("PORT", "8080"))
TOKEN = os.getenv("DISCORD_TOKEN")

TURN_TIME_SECONDS = int(os.getenv("TURN_TIME_SECONDS", "30"))
ARBITRO_ROLE_NAME = os.getenv("ARBITRO_ROLE_NAME", "Arbitro")
LOCAL_TZ = os.getenv("LOCAL_TZ", "Europe/Madrid")

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
MATCHES = {}     # channel_id(int) -> state
WS_CLIENTS = {}  # match_id(str) -> set(ws)

HP_MAPS = ["Blackheart", "Colossus", "Den", "Exposure", "Scar"]
SND_MAPS = ["Colossus", "Den", "Exposure", "Raid", "Scar"]
OVR_MAPS = ["Den", "Exposure", "Scar"]

# =========================
# FLOWS
# =========================
FLOW_BO5 = [
    {"mode": "HP",  "type": "ban",       "team": "A"},
    {"mode": "HP",  "type": "ban",       "team": "B"},
    {"mode": "HP",  "type": "pick_map",  "team": "A", "slot": 1},
    {"mode": "HP",  "type": "pick_side", "team": "B", "slot": 1},
    {"mode": "HP",  "type": "pick_map",  "team": "B", "slot": 4},
    {"mode": "HP",  "type": "pick_side", "team": "A", "slot": 4},

    {"mode": "SnD", "type": "ban",       "team": "B"},
    {"mode": "SnD", "type": "ban",       "team": "A"},
    {"mode": "SnD", "type": "pick_map",  "team": "B", "slot": 2},
    {"mode": "SnD", "type": "pick_side", "team": "A", "slot": 2},
    {"mode": "SnD", "type": "pick_map",  "team": "A", "slot": 5},
    {"mode": "SnD", "type": "pick_side", "team": "B", "slot": 5},

    {"mode": "OVR", "type": "ban",         "team": "A"},
    {"mode": "OVR", "type": "ban",         "team": "B"},
    {"mode": "OVR", "type": "auto_decider","slot": 3},
    {"mode": "OVR", "type": "pick_side",   "team": "A", "slot": 3},
]

FLOW_BO3 = [
    {"mode": "HP",  "type": "ban",       "team": "A"},
    {"mode": "HP",  "type": "ban",       "team": "B"},
    {"mode": "HP",  "type": "pick_map",  "team": "A", "slot": 1},
    {"mode": "HP",  "type": "pick_side", "team": "B", "slot": 1},

    {"mode": "SnD", "type": "ban",       "team": "B"},
    {"mode": "SnD", "type": "ban",       "team": "A"},
    {"mode": "SnD", "type": "pick_map",  "team": "B", "slot": 2},
    {"mode": "SnD", "type": "pick_side", "team": "A", "slot": 2},

    {"mode": "OVR", "type": "ban",          "team": "A"},
    {"mode": "OVR", "type": "ban",          "team": "B"},
    {"mode": "OVR", "type": "auto_decider", "slot": 3},
    {"mode": "OVR", "type": "pick_side",    "team": "A", "slot": 3},
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
    if not match_id:
        await ws.close()
        return ws

    WS_CLIENTS.setdefault(match_id, set()).add(ws)

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
def is_arbitro(member: discord.Member) -> bool:
    return any(r.name.lower() == ARBITRO_ROLE_NAME.lower() for r in getattr(member, "roles", []))

def build_maps():
    maps = {}
    for m in HP_MAPS:
        maps[f"HP::{m}"] = {"mode": "HP", "status": "free", "team": None, "slot": None, "side": None}
    for m in SND_MAPS:
        maps[f"SnD::{m}"] = {"mode": "SnD", "status": "free", "team": None, "slot": None, "side": None}
    for m in OVR_MAPS:
        maps[f"OVR::{m}"] = {"mode": "OVR", "status": "free", "team": None, "slot": None, "side": None}
    return maps

def user_can_interact(interaction: discord.Interaction, state: dict, step: dict) -> bool:
    member = interaction.user
    if is_arbitro(member):
        return True
    if not step.get("team"):
        return False
    team_key = step["team"]  # A/B
    role_id = state["teams"][team_key]["role_id"]
    return any(r.id == role_id for r in member.roles)

def required_wins(mode: str) -> int:
    return 2 if mode == "BO3" else 3

def compute_wins_from_results(state: dict):
    wa = 0
    wb = 0
    for _, r in state.get("map_results", {}).items():
        w = (r.get("winner") or "").upper()
        if w == "A":
            wa += 1
        elif w == "B":
            wb += 1
    return wa, wb

def picked_slots(state: dict):
    return sorted({m["slot"] for m in state["maps"].values() if m.get("status") == "picked" and m.get("slot")})

def get_picked_map_label_for_slot(state: dict, slot: int):
    for k, m in state["maps"].items():
        if m.get("status") == "picked" and m.get("slot") == slot:
            return m.get("mode"), k.split("::")[1]
    return None, None

async def auto_decider(state: dict):
    # si toca auto_decider, y solo queda 1 mapa libre, lo asigna
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
        state["maps"][key].update({"status": "picked", "team": "DECIDER", "slot": step["slot"]})
        state["step"] += 1
        # epoch seconds para overlay
        state["turn_started_at"] = time.time()
        state["turn_duration"] = TURN_TIME_SECONDS

async def maybe_finish_series(channel_id: int, state: dict):
    if state.get("series_finished"):
        return

    mode = state.get("mode")
    if mode not in ("BO3", "BO5"):
        return

    need = required_wins(mode)
    wa, wb = compute_wins_from_results(state)

    if wa >= need or wb >= need:
        winner = "A" if wa > wb else "B"
        state["series_finished"] = True
        state["series_winner"] = winner
        state["series_score_str"] = f"{wa}-{wb}"

        await ws_broadcast(str(channel_id))

        ch = bot.get_channel(channel_id)
        if ch:
            await ch.send(
                f"🏆 **SERIE FINALIZADA** — Gana **{state['teams'][winner]['name']}** ({wa}-{wb})"
            )

# =========================
# EMBEDS
# =========================
def describe_step(state: dict) -> str:
    if state.get("series_finished") and state.get("series_winner") in ("A", "B"):
        w = state["series_winner"]
        return f"🏆 **GANADOR:** {state['teams'][w]['name']} ({state.get('series_score_str','')})"

    if not state.get("flow"):
        return "⏳ Esperando organización + evento. Luego: LISTO → Árbitro elige BO3/BO5."

    if state["step"] >= len(state["flow"]):
        return "✅ **PICK & BAN FINALIZADO** — Introduce resultados (árbitro)"

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

    return (
        f"**PASO {state['step'] + 1}/{len(state['flow'])}**\n"
        f"🎯 **{action}** · 🕹️ {step.get('mode','')}\n"
        f"👤 Turno: **{who}**\n"
        f"⏱️ Tiempo por turno: **{state.get('turn_duration', TURN_TIME_SECONDS)}s**"
    )

def build_embed(state: dict) -> discord.Embed:
    embed = discord.Embed(
        title=f"PICK & BAN — {state.get('mode','') or '—'}",
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
                lines.append(f"❌ {name} (Ban {m.get('team','')})")
            elif m["status"] == "picked":
                side = f" · {m['side']}" if m.get("side") else ""
                slot = f"M{m['slot']}" if m.get("slot") else "M?"
                lines.append(f"✅ {name} · {slot} (Pick {m.get('team','')}){side}")
            else:
                lines.append(f"⬜ {name}")

        embed.add_field(name=mode, value="\n".join(lines) or "—", inline=False)

    wa, wb = compute_wins_from_results(state)
    if wa or wb:
        embed.add_field(
            name="Marcador serie",
            value=f"**{state['teams']['A']['name']}** {wa} - {wb} **{state['teams']['B']['name']}**",
            inline=False
        )

    return embed

# =========================
# PLANNING (EVENT)
# =========================
async def send_match_planning_embed(channel: discord.TextChannel, channel_id: int):
    state = MATCHES[channel_id]

    embed = discord.Embed(
        title="📅 Organización del partido",
        description=(
            "Usad este mensaje para **acordar la hora del partido**.\n\n"
            "👉 Cuando tengáis una hora clara, el **árbitro** puede crear el **evento oficial** desde aquí.\n\n"
            "⚠️ El Pick & Ban **NO comenzará** hasta que el evento esté creado."
        ),
        color=0x3498db
    )
    embed.add_field(
        name="Equipos",
        value=f"🟢 **{state['teams']['A']['name']}** vs 🔵 **{state['teams']['B']['name']}**",
        inline=False
    )
    embed.add_field(
        name="Zona horaria",
        value=f"🕒 {LOCAL_TZ} (la hora que pongáis se interpreta en esa zona)",
        inline=False
    )

    view = discord.ui.View(timeout=None)
    view.add_item(CreateEventButton(channel_id))
    await channel.send(embed=embed, view=view)

class CreateEventButton(discord.ui.Button):
    def __init__(self, channel_id: int):
        super().__init__(label="📅 Crear evento del partido", style=discord.ButtonStyle.primary)
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        if not is_arbitro(interaction.user):
            return await interaction.response.send_message("⛔ Solo el árbitro puede crear el evento", ephemeral=True)
        await interaction.response.send_modal(CreateEventModal(self.channel_id))

class CreateEventModal(discord.ui.Modal, title="Crear evento del partido"):
    date = discord.ui.TextInput(label="Fecha (YYYY-MM-DD)", placeholder="2025-12-25")
    time_ = discord.ui.TextInput(label="Hora inicio (HH:MM)", placeholder="21:30")
    duration = discord.ui.TextInput(label="Duración (min)", default="90")

    def __init__(self, channel_id: int):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        state = MATCHES.get(self.channel_id)
        if not state:
            return await interaction.response.send_message("❌ No encuentro el partido en memoria.", ephemeral=True)

        try:
            tz = ZoneInfo(LOCAL_TZ)
            start_naive = datetime.fromisoformat(f"{self.date.value} {self.time_.value}")
            start = start_naive.replace(tzinfo=tz)  # aware
            mins = int(self.duration.value)
            end = start + timedelta(minutes=mins)
        except Exception:
            return await interaction.response.send_message("❌ Fecha/hora incorrectas.", ephemeral=True)

        # Discord exige aware datetimes -> OK
        event = await interaction.guild.create_scheduled_event(
            name=f"{state['teams']['A']['name']} vs {state['teams']['B']['name']}",
            description="Partido oficial con Pick & Ban",
            start_time=start,
            end_time=end,
            location=f"Canal #{interaction.channel.name}",
            entity_type=discord.EntityType.external,
            privacy_level=discord.PrivacyLevel.guild_only
        )

        state["event_created"] = True
        state["event_id"] = event.id

        await interaction.response.send_message(f"✅ Evento creado: **{event.name}**", ephemeral=True)

        # Ahora sí: botones de listo
        await send_ready_buttons(interaction.channel, self.channel_id)

# =========================
# READY + BO SELECT
# =========================
async def send_ready_buttons(channel: discord.TextChannel, channel_id: int):
    state = MATCHES.get(channel_id)
    if not state:
        return

    view = discord.ui.View(timeout=None)
    view.add_item(ReadyButton(channel_id, "A"))
    view.add_item(ReadyButton(channel_id, "B"))

    await channel.send(
        embed=discord.Embed(
            title="🎮 Pick & Ban — Preparación",
            description=(
                "✅ Cada equipo debe pulsar su botón de **LISTO**.\n"
                "⚖️ Cuando ambos estén listos, el **árbitro** elegirá **BO3 / BO5** y empezará el flujo."
            ),
            color=0x00ffcc
        ),
        view=view
    )

class ReadyButton(discord.ui.Button):
    def __init__(self, channel_id: int, team: str):
        super().__init__(label=f"✅ TEAM {team} LISTO", style=discord.ButtonStyle.success)
        self.channel_id = channel_id
        self.team = team

    async def callback(self, interaction: discord.Interaction):
        state = MATCHES.get(self.channel_id)
        if not state:
            return await interaction.response.send_message("❌ Partido no encontrado.", ephemeral=True)

        # IMPORTANT: el state debe tener role_id (aquí estaba tu crash)
        role_id = state["teams"][self.team]["role_id"]
        if not any(r.id == role_id for r in interaction.user.roles):
            return await interaction.response.send_message("⛔ No perteneces a este equipo", ephemeral=True)

        state["teams"][self.team]["ready"] = True
        await interaction.response.send_message("✅ Equipo confirmado", ephemeral=True)

        if all(t["ready"] for t in state["teams"].values()):
            await show_bo_selector(interaction.channel, self.channel_id)

async def show_bo_selector(channel: discord.TextChannel, channel_id: int):
    view = discord.ui.View(timeout=None)
    view.add_item(ModeButton(channel_id, "BO3"))
    view.add_item(ModeButton(channel_id, "BO5"))
    await channel.send("⚖️ **Árbitro:** selecciona formato", view=view)

class ModeButton(discord.ui.Button):
    def __init__(self, channel_id: int, mode: str):
        super().__init__(label=mode, style=discord.ButtonStyle.primary)
        self.channel_id = channel_id
        self.mode = mode

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not is_arbitro(interaction.user):
            return

        state = MATCHES.get(self.channel_id)
        if not state:
            return

        state["mode"] = self.mode
        state["flow"] = FLOW_BO3 if self.mode == "BO3" else FLOW_BO5
        state["step"] = 0
        state["series_finished"] = False
        state["series_winner"] = None
        state["series_score_str"] = ""
        # timer epoch
        state["turn_started_at"] = time.time()
        state["turn_duration"] = TURN_TIME_SECONDS

        await interaction.channel.send(embed=build_embed(state), view=PickBanView(self.channel_id))
        await ws_broadcast(str(self.channel_id))

# =========================
# PICK & BAN UI
# =========================
class PickBanView(discord.ui.View):
    def __init__(self, channel_id: int):
        super().__init__(timeout=None)
        state = MATCHES[channel_id]

        finished_flow = state["step"] >= len(state["flow"])

        # si terminó el flow → modo resultados secuencial (si no hay ganador)
        if finished_flow:
            if state.get("series_finished"):
                return

            slots = picked_slots(state)
            next_slot = None
            for s in slots:
                # keys pueden ir como str
                if str(s) not in state["map_results"]:
                    next_slot = s
                    break

            if next_slot is not None:
                mode, name = get_picked_map_label_for_slot(state, next_slot)
                self.add_item(ResultButton(channel_id, next_slot, mode=mode, map_name=name))
            return

        step = state["flow"][state["step"]]
        if step["type"] in ("ban", "pick_map"):
            for k, m in state["maps"].items():
                if m["mode"] == step["mode"] and m["status"] == "free":
                    self.add_item(MapButton(channel_id, k))
        elif step["type"] == "pick_side":
            self.add_item(SideButton(channel_id, "JSOC"))
            self.add_item(SideButton(channel_id, "HERMANDAD"))

class MapButton(discord.ui.Button):
    def __init__(self, channel_id: int, map_key: str):
        super().__init__(label=map_key.split("::")[1], style=discord.ButtonStyle.secondary)
        self.channel_id = channel_id
        self.map_key = map_key

    async def callback(self, interaction: discord.Interaction):
        state = MATCHES[self.channel_id]
        step = state["flow"][state["step"]]

        # permisos ANTES de defer (para evitar "interaction failed")
        if not user_can_interact(interaction, state, step):
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)

        await interaction.response.defer()

        if step["type"] == "ban":
            state["maps"][self.map_key].update({"status": "banned", "team": step["team"]})
        else:
            state["maps"][self.map_key].update({
                "status": "picked",
                "team": step["team"],
                "slot": step["slot"]
            })

        state["step"] += 1
        state["turn_started_at"] = time.time()
        state["turn_duration"] = TURN_TIME_SECONDS

        await auto_decider(state)
        await ws_broadcast(str(self.channel_id))

        await interaction.message.edit(
            embed=build_embed(state),
            view=PickBanView(self.channel_id) if state["step"] < len(state["flow"]) else PickBanView(self.channel_id)
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
            if m.get("slot") == step.get("slot"):
                m["side"] = self.side
                break

        state["step"] += 1
        state["turn_started_at"] = time.time()
        state["turn_duration"] = TURN_TIME_SECONDS

        await auto_decider(state)
        await ws_broadcast(str(self.channel_id))

        await interaction.message.edit(
            embed=build_embed(state),
            view=PickBanView(self.channel_id) if state["step"] < len(state["flow"]) else PickBanView(self.channel_id)
        )

# =========================
# RESULTS (SECUENCIAL + AUTOWIN)
# =========================
class ResultButton(discord.ui.Button):
    def __init__(self, channel_id: int, slot: int, mode=None, map_name=None):
        super().__init__(label=f"Resultado M{slot}", style=discord.ButtonStyle.success)
        self.channel_id = channel_id
        self.slot = slot
        self.mode = mode
        self.map_name = map_name

    async def callback(self, interaction: discord.Interaction):
        if not is_arbitro(interaction.user):
            return await interaction.response.send_message("⛔ Solo árbitro", ephemeral=True)

        title = f"Resultado M{self.slot}"
        if self.mode and self.map_name:
            title = f"{title} — {self.mode} · {self.map_name}"

        await interaction.response.send_modal(ResultModal(self.channel_id, self.slot, title=title))

class ResultModal(discord.ui.Modal):
    winner = discord.ui.TextInput(label="Ganador (A o B)", placeholder="A o B", max_length=1)
    score = discord.ui.TextInput(label="Marcador (ej: 250-50 / 6-3)", placeholder="250-50", max_length=20)

    def __init__(self, channel_id: int, slot: int, title="Resultado del mapa"):
        super().__init__(title=title)
        self.channel_id = channel_id
        self.slot = slot

    async def on_submit(self, interaction: discord.Interaction):
        state = MATCHES.get(self.channel_id)
        if not state:
            return await interaction.response.send_message("❌ Partido no encontrado.", ephemeral=True)

        w = (self.winner.value or "").strip().upper()
        if w not in ("A", "B"):
            return await interaction.response.send_message("⛔ Ganador inválido (A o B)", ephemeral=True)

        state["map_results"][str(self.slot)] = {
            "winner": w,
            "score": (self.score.value or "").strip()
        }

        await ws_broadcast(str(self.channel_id))
        await interaction.response.send_message("✅ Resultado guardado", ephemeral=True)

        # autowin
        await maybe_finish_series(self.channel_id, state)

        # refresca embed + siguiente botón (o ninguno si serie finalizada)
        await interaction.channel.send(embed=build_embed(state), view=PickBanView(self.channel_id))

# =========================
# START COMMAND
# =========================
@bot.command()
async def start(ctx, teamA: discord.Role, teamB: discord.Role):
    # Estado completo (NO perder role_id / ready)
    MATCHES[ctx.channel.id] = {
        "channel_id": ctx.channel.id,
        "flow": [],                  # todavía no
        "step": 0,
        "maps": build_maps(),
        "map_results": {},

        "mode": None,                # BO3/BO5 cuando el árbitro lo elija
        "event_created": False,
        "event_id": None,

        "series_finished": False,
        "series_winner": None,
        "series_score_str": "",

        # timer epoch seconds (overlay)
        "turn_started_at": time.time(),
        "turn_duration": TURN_TIME_SECONDS,

        "teams": {
            "A": {"name": teamA.name, "role_id": teamA.id, "ready": False},
            "B": {"name": teamB.name, "role_id": teamB.id, "ready": False},
        }
    }

    # 1) Planning embed + botón crear evento
    await send_match_planning_embed(ctx.channel, ctx.channel.id)

    # 2) Overlay link
    overlay_url = (
        f"{APP_URL}/overlay.html?match={ctx.channel.id}"
        if APP_URL else
        f"/overlay.html?match={ctx.channel.id}"
    )
    await ctx.send(f"🎥 **Overlay OBS:**\n{overlay_url}")

    # 3) push state
    await ws_broadcast(str(ctx.channel.id))

# =========================
# RUN
# =========================
bot.run(TOKEN)
