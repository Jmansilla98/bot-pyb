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
APP_URL = os.getenv("APP_URL", "").rstrip("/")  # ej: https://tu-app.fly.dev
PORT = int(os.getenv("PORT", "8080"))
TOKEN = os.getenv("DISCORD_TOKEN")

TURN_TIME_SECONDS = int(os.getenv("TURN_TIME_SECONDS", "30"))
ARBITRO_ROLE_NAME = os.getenv("ARBITRO_ROLE_NAME", "Arbitro")

BASE_DIR = pathlib.Path(__file__).parent
OVERLAY_DIR = BASE_DIR / "overlay"

# =========================
# DISCORD
# =========================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# =========================
# STATE
# =========================
MATCHES = {}     # channel_id(int) -> state
WS_CLIENTS = {}  # match_id(str) -> set(WebSocketResponse)

HP_MAPS = ["Blackheart", "Colossus", "Den", "Exposure", "Scar"]
SND_MAPS = ["Colossus", "Den", "Exposure", "Raid", "Scar"]
OVR_MAPS = ["Den", "Exposure", "Scar"]

SERIES_CONFIG = {
    "BO3": {"maps": 3, "wins": 2},
    "BO5": {"maps": 5, "wins": 3},
    "BO7": {"maps": 7, "wins": 4},
}

# =========================
# FLOWS
# =========================
BASE_FLOW = [
    {"mode": "HP",  "type": "ban",        "team": "A"},
    {"mode": "HP",  "type": "ban",        "team": "B"},
    {"mode": "HP",  "type": "pick_map",   "team": "A", "slot": 1},
    {"mode": "HP",  "type": "pick_side",  "team": "B", "slot": 1},

    {"mode": "SnD", "type": "ban",        "team": "B"},
    {"mode": "SnD", "type": "ban",        "team": "A"},
    {"mode": "SnD", "type": "pick_map",   "team": "B", "slot": 2},
    {"mode": "SnD", "type": "pick_side",  "team": "A", "slot": 2},

    {"mode": "OVR", "type": "ban",        "team": "A"},
    {"mode": "OVR", "type": "pick_map",  "team": "B", "slot": 3},
    {"mode": "OVR", "type": "pick_side",  "team": "A", "slot": 3},
]

def build_flow(mode: str):
    if mode == "BO3":
        return list(BASE_FLOW)

    if mode == "BO5":
        return list(BASE_FLOW) + [
            {"mode": "HP",  "type": "pick_map",  "team": "B", "slot": 4},
            {"mode": "HP",  "type": "pick_side", "team": "A", "slot": 4},
            {"mode": "SnD", "type": "pick_map",  "team": "A", "slot": 5},
            {"mode": "SnD", "type": "pick_side", "team": "B", "slot": 5},
        ]

    if mode == "BO7":
        return list(BASE_FLOW) + [
            {"mode": "HP",  "type": "pick_map",  "team": "B", "slot": 4},
            {"mode": "HP",  "type": "pick_side", "team": "A", "slot": 4},
            {"mode": "SnD", "type": "pick_map",  "team": "A", "slot": 5},
            {"mode": "SnD", "type": "pick_side", "team": "B", "slot": 5},
            {"mode": "OVR",  "type": "pick_map",  "team": "A", "slot": 6},
            {"mode": "OVR",  "type": "pick_side", "team": "B", "slot": 6},
            {"mode": "SnD", "type": "pick_map",  "team": "B", "slot": 7},
            {"mode": "SnD", "type": "pick_side", "team": "A", "slot": 7},
        ]

    return list(BASE_FLOW)

# =========================
# WEB + WS
# =========================
app = web.Application()
app.router.add_static("/static/", OVERLAY_DIR)
routes = web.RouteTableDef()

@routes.get("/overlay.html")
async def overlay(request):
    return web.FileResponse(OVERLAY_DIR / "overlay.html")

@routes.get("/arbitro")
async def arbitro_panel(request):
    # Necesitas overlay/arbitro.html + overlay/arbitro.js
    return web.FileResponse(OVERLAY_DIR / "arbitro.html")

@routes.get("/api/matches")
async def api_matches(request):
    data = []
    for cid, s in MATCHES.items():
        if not s.get("flow"):
            status = "Sin comenzar"
        elif s.get("series_finished"):
            status = "Finalizado"
        else:
            status = "En curso"

        data.append({
            "match_id": cid,
            "teams": f"{s['teams']['A']['name']} vs {s['teams']['B']['name']}",
            "mode": s.get("mode"),
            "status": status,
            "results": s.get("map_results", {}),
            "series_winner": s.get("series_winner"),
            "series_score": s.get("series_score", {"A": 0, "B": 0}),
        })
    return web.json_response(data)

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
def build_maps():
    maps = {}
    for m in HP_MAPS:
        maps[f"HP::{m}"] = {"mode": "HP", "status": "free", "team": None, "slot": None, "side": None}
    for m in SND_MAPS:
        maps[f"SnD::{m}"] = {"mode": "SnD", "status": "free", "team": None, "slot": None, "side": None}
    for m in OVR_MAPS:
        maps[f"OVR::{m}"] = {"mode": "OVR", "status": "free", "team": None, "slot": None, "side": None}
    return maps

def is_arbitro(member: discord.Member) -> bool:
    return any(r.name.lower() == ARBITRO_ROLE_NAME.lower() for r in getattr(member, "roles", []))

def user_can_interact(interaction: discord.Interaction, state: dict, step: dict) -> bool:
    if is_arbitro(interaction.user):
        return True
    team = step.get("team")
    if team not in ("A", "B"):
        return False
    role_id = state["teams"][team]["role_id"]
    return any(r.id == role_id for r in interaction.user.roles)

def series_wins_needed(mode: str) -> int:
    return SERIES_CONFIG.get(mode, {"wins": 2})["wins"]

def compute_wins_from_results(state: dict):
    a = b = 0
    for res in state.get("map_results", {}).values():
        w = (res.get("winner") or "").upper()
        if w == "A":
            a += 1
        elif w == "B":
            b += 1
    return a, b

def picked_slots(state: dict):
    return sorted({m["slot"] for m in state["maps"].values() if m.get("status") == "picked" and m.get("slot")})

def map_for_slot(state: dict, slot: int):
    for k, m in state["maps"].items():
        if m.get("status") == "picked" and m.get("slot") == slot:
            return (m.get("mode"), k.split("::")[1], m.get("team"), m.get("side"))
    return (None, None, None, None)

async def auto_decider(state: dict):
    # Si el step actual es auto_decider y queda 1 mapa libre, lo mete como picked
    while state["step"] < len(state["flow"]):
        step = state["flow"][state["step"]]
        if step["type"] != "auto_decider":
            return

        free_maps = [k for k, m in state["maps"].items()
                    if m["mode"] == step["mode"] and m["status"] == "free"]
        if len(free_maps) != 1:
            return

        key = free_maps[0]
        state["maps"][key].update({
            "status": "picked",
            "team": "DECIDER",
            "slot": step["slot"],
            "side": None
        })
        state["step"] += 1
        state["turn_started_at"] = time.time()
        state["turn_duration"] = TURN_TIME_SECONDS

async def maybe_finish_series(channel_id: int, state: dict):
    if state.get("series_finished"):
        return
    mode = state.get("mode")
    if mode not in SERIES_CONFIG:
        return

    wins_a, wins_b = compute_wins_from_results(state)
    need = series_wins_needed(mode)

    if wins_a >= need or wins_b >= need:
        winner = "A" if wins_a > wins_b else "B"
        state["series_finished"] = True
        state["series_winner"] = winner
        state["series_score"] = {"A": wins_a, "B": wins_b}

        await ws_broadcast(str(channel_id))

        ch = bot.get_channel(channel_id)
        if ch:
            await ch.send(
                f"🏆 **SERIE FINALIZADA** — Gana **{state['teams'][winner]['name']}** "
                f"({wins_a}-{wins_b})"
            )

# =========================
# EMBEDS (BONITOS)
# =========================
def planning_embed(state: dict) -> discord.Embed:
    e = discord.Embed(
        title="📅 Organización del partido",
        description=(
            "Este mensaje es para **concretar la hora** del partido.\n\n"
            "✅ Cuando lo tengáis claro, el **árbitro** pulsa **Crear evento**.\n"
            "⛔ El Pick & Ban **no comenzará** hasta que el evento esté creado."
        ),
        color=0x0AA3FF
    )
    e.add_field(
        name="Enfrentamiento",
        value=f"🟢 **{state['teams']['A']['name']}**  vs  🔵 **{state['teams']['B']['name']}**",
        inline=False
    )
    e.set_footer(text="Circuito • Panel árbitro /arbitro • Overlay sincronizado")
    return e

def describe_step(state: dict) -> str:
    if state.get("series_finished"):
        w = state.get("series_winner")
        if w in ("A", "B"):
            a = state.get("series_score", {}).get("A", 0)
            b = state.get("series_score", {}).get("B", 0)
            return f"🏆 **GANADOR:** {state['teams'][w]['name']} ({a}-{b})"
        return "🏁 Serie finalizada"

    if not state.get("flow"):
        return "⏳ **Pendiente:** crear evento + equipos listos"

    if state["step"] >= len(state["flow"]):
        return "✅ **PICK & BAN FINALIZADO** — Introduce resultados (árbitro)"

    step = state["flow"][state["step"]]
    action = {
        "ban": "BANEAR MAPA",
        "pick_map": "ELEGIR MAPA",
        "pick_side": "ELEGIR LADO",
        "auto_decider": "DECIDER AUTOMÁTICO",
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
        f"🎯 **{action}** · 🕹️ **{step.get('mode','')}**\n"
        f"👤 Turno: **{who}**\n"
        f"⏱️ Tiempo por turno: **{state.get('turn_duration', TURN_TIME_SECONDS)}s**"
    )

def build_pickban_embed(state: dict) -> discord.Embed:
    color = 0x00FFC6
    e = discord.Embed(
        title=f"🎮 PICK & BAN — {state.get('mode','')}",
        description=describe_step(state),
        color=color
    )

    # mapas por modo
    for mode in ["HP", "SnD", "OVR"]:
        lines = []
        for k, m in state["maps"].items():
            if m["mode"] != mode:
                continue
            name = k.split("::")[1]
            if m["status"] == "banned":
                lines.append(f"❌ {name} · Ban {m.get('team','')}")
            elif m["status"] == "picked":
                side = f" · {m['side']}" if m.get("side") else ""
                lines.append(f"✅ {name} · M{m.get('slot')} · Pick {m.get('team','')}{side}")
            else:
                lines.append(f"⬜ {name}")
        e.add_field(name=mode, value="\n".join(lines) or "—", inline=False)

    # marcador serie (si hay resultados)
    wa, wb = compute_wins_from_results(state)
    if wa or wb:
        e.add_field(
            name="Marcador serie",
            value=f"🟢 **{state['teams']['A']['name']}** {wa} - {wb} **{state['teams']['B']['name']}** 🔵",
            inline=False
        )

    e.set_footer(text="Overlay sincronizado en tiempo real • Resultados secuenciales • Autowin activo")
    return e

# =========================
# UI: EVENTO + READY
# =========================
class CreateEventButton(discord.ui.Button):
    def __init__(self, channel_id: int):
        super().__init__(label="📅 Crear evento (árbitro)", style=discord.ButtonStyle.primary)
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        if not is_arbitro(interaction.user):
            return await interaction.response.send_message("⛔ Solo el árbitro puede crear el evento.", ephemeral=True)
        await interaction.response.send_modal(CreateEventModal(self.channel_id))

class CreateEventModal(discord.ui.Modal, title="Crear evento del partido"):
    date = discord.ui.TextInput(label="Fecha (YYYY-MM-DD)", placeholder="2025-12-25")
    time = discord.ui.TextInput(label="Hora inicio (HH:MM)", placeholder="21:30")
    duration = discord.ui.TextInput(label="Duración (minutos)", default="90")

    def __init__(self, channel_id: int):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        state = MATCHES[self.channel_id]

        # Parse Europe/Madrid -> UTC aware (Discord lo exige)
        try:
            local_dt = datetime.fromisoformat(f"{self.date.value} {self.time.value}")
            minutes = int(self.duration.value)
        except:
            return await interaction.response.send_message("❌ Fecha/hora inválida.", ephemeral=True)

        # Intentar zoneinfo Europe/Madrid
        try:
            from zoneinfo import ZoneInfo
            madrid = ZoneInfo("Europe/Madrid")
            start_local = local_dt.replace(tzinfo=madrid)
            start_utc = start_local.astimezone(timezone.utc)
        except:
            # fallback: asume que lo metes ya en UTC
            start_utc = local_dt.replace(tzinfo=timezone.utc)

        end_utc = start_utc + timedelta(minutes=minutes)

        # Crear evento externo
        await interaction.guild.create_scheduled_event(
            name=f"{state['teams']['A']['name']} vs {state['teams']['B']['name']}",
            description="Partido oficial (Pick & Ban + resultados en directo)",
            start_time=start_utc,
            end_time=end_utc,
            entity_type=discord.EntityType.external,
            location=f"Canal #{interaction.channel.name}",
            privacy_level=discord.PrivacyLevel.guild_only
        )

        await interaction.response.send_message("✅ Evento creado. Ahora salen los botones de LISTO.", ephemeral=True)
        await send_ready_buttons(interaction.channel, self.channel_id)

async def send_ready_buttons(channel: discord.TextChannel, channel_id: int):
    state = MATCHES[channel_id]
    view = discord.ui.View(timeout=None)
    view.add_item(ReadyButton(channel_id, "A"))
    view.add_item(ReadyButton(channel_id, "B"))

    e = discord.Embed(
        title="✅ Confirmación de equipos",
        description=(
            "Cada equipo debe pulsar su botón de **LISTO**.\n"
            "Cuando ambos estén listos, el **árbitro** seleccionará BO3/BO5/BO7."
        ),
        color=0x00FFC6
    )
    e.add_field(name="Equipos", value=f"🟢 {state['teams']['A']['name']}  vs  🔵 {state['teams']['B']['name']}", inline=False)

    await channel.send(embed=e, view=view)

class ReadyButton(discord.ui.Button):
    def __init__(self, channel_id: int, team: str):
        super().__init__(label=f"✅ TEAM {team} LISTO", style=discord.ButtonStyle.success)
        self.channel_id = channel_id
        self.team = team

    async def callback(self, interaction: discord.Interaction):
        state = MATCHES[self.channel_id]

        # permisos antes de responder lento
        role_id = state["teams"][self.team]["role_id"]
        if not any(r.id == role_id for r in interaction.user.roles):
            return await interaction.response.send_message("⛔ No perteneces a este equipo.", ephemeral=True)

        state["teams"][self.team]["ready"] = True
        await interaction.response.send_message("✅ Equipo confirmado.", ephemeral=True)

        if all(t["ready"] for t in state["teams"].values()):
            await show_mode_selector(interaction.channel, self.channel_id)

async def show_mode_selector(channel: discord.TextChannel, channel_id: int):
    view = discord.ui.View(timeout=None)
    view.add_item(ModeButton(channel_id, "BO3"))
    view.add_item(ModeButton(channel_id, "BO5"))
    view.add_item(ModeButton(channel_id, "BO7"))
    await channel.send("⚖️ **Árbitro:** selecciona formato", view=view)

class ModeButton(discord.ui.Button):
    def __init__(self, channel_id: int, mode: str):
        super().__init__(label=mode, style=discord.ButtonStyle.primary)
        self.channel_id = channel_id
        self.mode = mode

    async def callback(self, interaction: discord.Interaction):
        if not is_arbitro(interaction.user):
            return await interaction.response.send_message("⛔ Solo árbitro.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        state = MATCHES[self.channel_id]
        state["mode"] = self.mode
        state["flow"] = build_flow(self.mode)
        state["step"] = 0
        state["turn_started_at"] = time.time()
        state["turn_duration"] = TURN_TIME_SECONDS
        state["series_finished"] = False
        state["series_winner"] = None
        state["series_score"] = {"A": 0, "B": 0}
        state["map_results"] = {}

        await auto_decider(state)

        await interaction.channel.send(
            embed=build_pickban_embed(state),
            view=PickBanView(self.channel_id)
        )
        await ws_broadcast(str(self.channel_id))

# =========================
# PICK & BAN VIEW
# =========================
class PickBanView(discord.ui.View):
    def __init__(self, channel_id: int):
        super().__init__(timeout=None)
        state = MATCHES[channel_id]

        if state.get("series_finished"):
            # sin botones
            return

        # Si flujo terminado -> resultados secuenciales
        if state.get("flow") and state["step"] >= len(state["flow"]):
            next_slot = self._next_pending_result_slot(state)
            if next_slot is not None:
                mode, name, _, _ = map_for_slot(state, next_slot)
                self.add_item(ResultButton(channel_id, next_slot, mode=mode, map_name=name))
            return

        # Si aún no hay flow, no mostrar nada
        if not state.get("flow"):
            return

        step = state["flow"][state["step"]]

        if step["type"] in ("ban", "pick_map"):
            for k, m in state["maps"].items():
                if m["mode"] == step["mode"] and m["status"] == "free":
                    self.add_item(MapButton(channel_id, k))

        elif step["type"] == "pick_side":
            self.add_item(SideButton(channel_id, "JSOC"))
            self.add_item(SideButton(channel_id, "HERMANDAD"))

    def _next_pending_result_slot(self, state: dict):
        slots = picked_slots(state)
        for s in slots:
            if str(s) not in state.get("map_results", {}):
                return s
        return None

class MapButton(discord.ui.Button):
    def __init__(self, channel_id: int, map_key: str):
        super().__init__(label=map_key.split("::")[1], style=discord.ButtonStyle.secondary)
        self.channel_id = channel_id
        self.map_key = map_key

    async def callback(self, interaction: discord.Interaction):
        state = MATCHES[self.channel_id]
        step = state["flow"][state["step"]]

        # permisos primero
        if not user_can_interact(interaction, state, step):
            return await interaction.response.send_message("⛔ No es tu turno.", ephemeral=True)

        await interaction.response.defer()

        if step["type"] == "ban":
            state["maps"][self.map_key].update({"status": "banned", "team": step["team"]})
        else:
            state["maps"][self.map_key].update({
                "status": "picked",
                "team": step["team"],
                "slot": step["slot"],
                "side": None
            })

        state["step"] += 1
        state["turn_started_at"] = time.time()
        state["turn_duration"] = TURN_TIME_SECONDS

        await auto_decider(state)
        await ws_broadcast(str(self.channel_id))

        await interaction.message.edit(
            embed=build_pickban_embed(state),
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
            return await interaction.response.send_message("⛔ No es tu turno.", ephemeral=True)

        await interaction.response.defer()

        slot = step.get("slot")
        for m in state["maps"].values():
            if m.get("slot") == slot:
                m["side"] = self.side

        state["step"] += 1
        state["turn_started_at"] = time.time()
        state["turn_duration"] = TURN_TIME_SECONDS

        await auto_decider(state)
        await ws_broadcast(str(self.channel_id))

        await interaction.message.edit(
            embed=build_pickban_embed(state),
            view=PickBanView(self.channel_id)
        )

# =========================
# RESULTS (SECUENCIAL + AUTOWIN)
# =========================
class ResultButton(discord.ui.Button):
    def __init__(self, channel_id: int, slot: int, mode: str = None, map_name: str = None):
        # Botón “pro”: etiqueta con mapa + modo
        label = f"M{slot} {mode or ''} • {map_name or ''}".strip()
        if len(label) > 80:
            label = f"M{slot} Resultado"
        super().__init__(label=label, style=discord.ButtonStyle.success)
        self.channel_id = channel_id
        self.slot = slot
        self.mode = mode
        self.map_name = map_name

    async def callback(self, interaction: discord.Interaction):
        if not is_arbitro(interaction.user):
            return await interaction.response.send_message("⛔ Solo árbitro.", ephemeral=True)

        title = f"Resultado M{self.slot}"
        if self.mode and self.map_name:
            title = f"{title} — {self.mode} · {self.map_name}"

        await interaction.response.send_modal(ResultModal(self.channel_id, self.slot, title=title))

class ResultModal(discord.ui.Modal):
    winner = discord.ui.TextInput(label="Ganador (A o B)", placeholder="A o B", max_length=1)
    score = discord.ui.TextInput(label="Marcador (ej: 250-50 / 6-3)", placeholder="250-50", max_length=20)

    def __init__(self, channel_id: int, slot: int, title: str):
        super().__init__(title=title)
        self.channel_id = channel_id
        self.slot = slot

    async def on_submit(self, interaction: discord.Interaction):
        state = MATCHES[self.channel_id]

        w = (self.winner.value or "").strip().upper()
        if w not in ("A", "B"):
            return await interaction.response.send_message("⛔ Ganador inválido (A o B).", ephemeral=True)

        state["map_results"][str(self.slot)] = {
            "winner": w,
            "score": (self.score.value or "").strip()
        }

        # recalcular marcador real desde results (robusto)
        wa, wb = compute_wins_from_results(state)
        state["series_score"] = {"A": wa, "B": wb}

        await ws_broadcast(str(self.channel_id))
        await interaction.response.send_message("✅ Resultado guardado.", ephemeral=True)

        # autowin
        await maybe_finish_series(self.channel_id, state)

        # refrescar panel (embed + siguiente botón resultado si toca)
        try:
            await interaction.channel.send(
                embed=build_pickban_embed(state),
                view=PickBanView(self.channel_id)
            )
        except:
            pass

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
        "series_winner": None,
        "series_score": {"A": 0, "B": 0},
        "turn_started_at": time.time(),
        "turn_duration": TURN_TIME_SECONDS,
    }

    state = MATCHES[ctx.channel.id]

    # Embed planificación + botón crear evento
    view = discord.ui.View(timeout=None)
    view.add_item(CreateEventButton(ctx.channel.id))
    await ctx.send(embed=planning_embed(state), view=view)

    # Overlay URL
    overlay_url = (
        f"{APP_URL}/overlay.html?match={ctx.channel.id}"
        if APP_URL else
        f"/overlay.html?match={ctx.channel.id}"
    )
    await ctx.send(f"🎥 **Overlay OBS:**\n{overlay_url}")

    await ws_broadcast(str(ctx.channel.id))

# =========================
# RUN
# =========================
bot.run(TOKEN)
