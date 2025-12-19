import discord
from discord.ext import commands
import asyncio
import json
from aiohttp import web
import pathlib
import os
import time

# =========================
# CONFIG
# =========================
APP_URL = os.getenv("APP_URL", "").rstrip("/")  # ej: https://bot-pyb...fly.dev
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
MATCHES = {}     # channel_id(int) -> state
WS_CLIENTS = {}  # match_id(str) -> set(ws)

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

    if int(match_id) in MATCHES:
        await ws_broadcast(match_id)

    try:
        async for _ in ws:
            pass
    finally:
        WS_CLIENTS.get(match_id, set()).discard(ws)

    return ws

app.add_routes(routes)



async def start_web():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()


@routes.get("/arbitro")
async def arbitro_panel(request):
    return web.FileResponse(OVERLAY_DIR / "arbitro.html")

@routes.get("/api/matches")
async def api_matches(request):
    data = []

    for match_id, state in MATCHES.items():
        teams = state["teams"]
        results = state.get("map_results", {})

        # Estado del partido
        if not state["flow"]:
            status = "Sin comenzar"
        elif state["step"] < len(state["flow"]):
            status = "En curso"
        else:
            status = "Finalizado"

        data.append({
            "match_id": match_id,
            "teams": f"{teams['A']['name']} vs {teams['B']['name']}",
            "mode": state.get("mode"),
            "status": status,
            "results": results
        })

    return web.json_response(data)


async def send_match_planning_embed(channel: discord.TextChannel, state: dict):
    embed = discord.Embed(
        title="📅 Organización del partido",
        description=(
            "Usad este mensaje para **acordar la hora del partido**.\n\n"
            "👉 Cuando tengáis una hora clara, el **árbitro** puede crear el evento "
            "oficial del partido desde aquí.\n\n"
            "⚠️ El Pick & Ban **NO comenzará** hasta que esto esté claro."
        ),
        color=0x3498db
    )

    embed.add_field(
        name="Equipos",
        value=f"🟢 **{state['teams']['A']['name']}** vs 🔵 **{state['teams']['B']['name']}**",
        inline=False
    )

    view = discord.ui.View(timeout=None)
    view.add_item(CreateEventButton(channel.id))

    await channel.send(embed=embed, view=view)

class CreateEventButton(discord.ui.Button):
    def __init__(self, channel_id):
        super().__init__(
            label="📅 Crear evento del partido",
            style=discord.ButtonStyle.primary
        )
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        if not any(r.name.lower() == ARBITRO_ROLE_NAME.lower() for r in interaction.user.roles):
            return await interaction.response.send_message(
                "⛔ Solo el árbitro puede crear el evento",
                ephemeral=True
            )

        await interaction.response.send_modal(CreateEventModal(self.channel_id))


class CreateEventModal(discord.ui.Modal, title="Crear evento del partido"):
    date = discord.ui.TextInput(
        label="Fecha (YYYY-MM-DD)",
        placeholder="2025-02-01"
    )
    time = discord.ui.TextInput(
        label="Hora inicio (HH:MM)",
        placeholder="21:30"
    )
    duration = discord.ui.TextInput(
        label="Duración (minutos)",
        placeholder="90",
        default="90"
    )

    def __init__(self, channel_id):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        state = MATCHES[self.channel_id]

        from datetime import datetime, timedelta

        try:
            start = datetime.fromisoformat(f"{self.date.value} {self.time.value}")
            duration = int(self.duration.value)
            end = start + timedelta(minutes=duration)
        except Exception:
            return await interaction.response.send_message(
                "❌ Fecha u hora incorrectas",
                ephemeral=True
            )

        guild = interaction.guild
        channel = interaction.channel

        event = await guild.create_scheduled_event(
            name=f"{state['teams']['A']['name']} vs {state['teams']['B']['name']}",
            description="Partido oficial con Pick & Ban",
            start_time=start,
            end_time=end,
            location=f"Canal #{channel.name}",
            entity_type=discord.EntityType.external,
            privacy_level=discord.PrivacyLevel.guild_only
        )

        await interaction.response.send_message(
            f"✅ Evento creado correctamente:\n📅 **{event.name}**",
            ephemeral=True
        )

        # AHORA sí mostramos los botones de LISTO
        await send_ready_buttons(channel, state)


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

def user_can_interact(interaction, state, step):
    if is_arbitro(interaction.user):
        return True
    if not step.get("team"):
        return False
    role_id = state["teams"][step["team"]]["role_id"]
    return any(r.id == role_id for r in interaction.user.roles)

def required_wins_for_mode(mode: str) -> int:
    # mode es "BO3" o "BO5"
    return 2 if mode == "BO3" else 3

def compute_series_wins(state):
    wins_a = 0
    wins_b = 0
    for _, res in state.get("map_results", {}).items():
        w = (res.get("winner") or "").upper()
        if w == "A":
            wins_a += 1
        elif w == "B":
            wins_b += 1
    return wins_a, wins_b

def picked_slots(state):
    return sorted({m["slot"] for m in state["maps"].values() if m.get("status") == "picked" and m.get("slot")})

def get_picked_map_label_for_slot(state, slot: int):
    # devuelve (mode, mapName) o (None, None)
    for k, m in state["maps"].items():
        if m.get("status") == "picked" and m.get("slot") == slot:
            return m.get("mode"), k.split("::")[1]
    return None, None

async def maybe_finish_series(channel_id: int, state: dict):
    if state.get("series_finished"):
        return

    mode = state.get("mode")
    if mode not in ("BO3", "BO5"):
        return

    need = required_wins_for_mode(mode)
    wins_a, wins_b = compute_series_wins(state)

    if wins_a >= need or wins_b >= need:
        winner_key = "A" if wins_a > wins_b else "B"
        state["series_finished"] = True
        state["series_winner"] = winner_key
        state["series_score"] = f"{wins_a}-{wins_b}"

        await ws_broadcast(str(channel_id))

        channel = bot.get_channel(channel_id)
        if channel:
            await channel.send(
                f"🏆 **SERIE FINALIZADA** — Gana **{state['teams'][winner_key]['name']}** "
                f"({state['series_score']})"
            )

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
        state["turn_started_at"] = time.time()  # epoch seconds, estable con overlay

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

# =========================
# EMBED
# =========================
def describe_step(state):
    if state.get("series_finished"):
        w = state.get("series_winner")
        if w in ("A", "B"):
            return f"🏆 **GANADOR:** {state['teams'][w]['name']} ({state.get('series_score','')})"
        return "🏁 Serie finalizada"

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
                lines.append(f"❌ {name} (Ban {m.get('team','')})")
            elif m["status"] == "picked":
                side = f" · {m['side']}" if m.get("side") else ""
                lines.append(f"✅ {name} · M{m['slot']} (Pick {m.get('team','')}){side}")
            else:
                lines.append(f"⬜ {name}")

        embed.add_field(name=mode, value="\n".join(lines) or "—", inline=False)

    # marcador serie si hay resultados
    wins_a, wins_b = compute_series_wins(state)
    if wins_a or wins_b:
        embed.add_field(
            name="Marcador serie",
            value=f"**{state['teams']['A']['name']}** {wins_a} - {wins_b} **{state['teams']['B']['name']}**",
            inline=False
        )

    return embed

async def send_ready_buttons(channel, state):
    view = discord.ui.View(timeout=None)
    view.add_item(ReadyButton(state["channel_id"], "A"))
    view.add_item(ReadyButton(state["channel_id"], "B"))

    await channel.send(
        embed=discord.Embed(
            title="🎮 Pick & Ban",
            description="Cuando ambos equipos estén listos, el árbitro elegirá BO3 o BO5",
            color=0x00ffcc
        ),
        view=view
    )

# =========================
# START COMMAND
# =========================
@bot.command()
async def start(ctx, teamA: discord.Role, teamB: discord.Role):
    MATCHES[ctx.channel.id] = {
        "channel_id": ctx.channel.id,
        "flow": [],
        "step": 0,
        "maps": build_maps(),
        "map_results": {},
        "mode": None,
        "series_finished": False,
        "series_score": {"A": 0, "B": 0},
        "series_winner": None,
        "turn_started_at": time.time(),        # epoch seconds
        "turn_duration": TURN_TIME_SECONDS,
        "teams": {
            "A": {"name": teamA.name, "role_id": teamA.id, "ready": False},
            "B": {"name": teamB.name, "role_id": teamB.id, "ready": False},
        }
    }

    overlay_url = f"{APP_URL}/overlay.html?match={ctx.channel.id}" if APP_URL else f"/overlay.html?match={ctx.channel.id}"

    
    await send_match_planning_embed(ctx.channel, MATCHES[ctx.channel.id])

    

    await ctx.send(f"🎥 **Overlay OBS:**\n{overlay_url}")
    await ws_broadcast(str(ctx.channel.id))

# =========================
# READY / BO SELECT
# =========================
class ReadyButton(discord.ui.Button):
    def __init__(self, channel_id, team):
        super().__init__(label=f"✅ TEAM {team} LISTO", style=discord.ButtonStyle.success)
        self.channel_id = channel_id
        self.team = team

    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True)

        state = MATCHES[self.channel_id]
        if not any(r.id == state["teams"][self.team]["role_id"] for r in interaction.user.roles):
            return

        state["teams"][self.team]["ready"] = True

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
        await interaction.response.defer(ephemeral=True)

        if not is_arbitro(interaction.user):
            return

        state = MATCHES[self.channel_id]
        state["mode"] = self.mode
        state["flow"] = FLOW_BO3 if self.mode == "BO3" else FLOW_BO5
        state["step"] = 0
        state["turn_started_at"] = time.time()

        await interaction.channel.send(
            embed=build_embed(state),
            view=PickBanView(self.channel_id)
        )
        await ws_broadcast(str(self.channel_id))

# =========================
# PICK & BAN VIEW
# =========================
class PickBanView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        state = MATCHES[channel_id]

        finished_flow = state["step"] >= len(state["flow"])
        if finished_flow:
            # si ya hay ganador, no muestres más botones
            if state.get("series_finished"):
                return

            # mostrar botones de resultados, pero solo el siguiente pendiente
            slots = picked_slots(state)
            next_slot = None
            for s in slots:
                if str(s) not in state["map_results"] and s not in state["map_results"]:
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
    def __init__(self, channel_id, map_key):
        super().__init__(label=map_key.split("::")[1], style=discord.ButtonStyle.secondary)
        self.channel_id = channel_id
        self.map_key = map_key

    async def callback(self, interaction):
        # importante: primero permisos, luego defer (si no, Discord puede dar “interacción fallida”)
        state = MATCHES[self.channel_id]
        step = state["flow"][state["step"]]

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
            if m.get("slot") == step.get("slot"):
                m["side"] = self.side

        state["step"] += 1
        state["turn_started_at"] = time.time()
        state["turn_duration"] = TURN_TIME_SECONDS

        await auto_decider(state)
        await ws_broadcast(str(self.channel_id))
        await interaction.message.edit(embed=build_embed(state), view=PickBanView(self.channel_id))

# =========================
# RESULTS
# =========================
class ResultButton(discord.ui.Button):
    def __init__(self, channel_id, slot, mode=None, map_name=None):
        label_top = f"{mode or ''} · {map_name or ''}".strip(" ·")
        label = f"Resultado M{slot}"
        super().__init__(label=label, style=discord.ButtonStyle.success)
        self.channel_id = channel_id
        self.slot = slot
        self.mode = mode
        self.map_name = map_name
        # “texto encima” no existe como tal en botones Discord, pero lo metemos en el modal title
        self._label_top = label_top

    async def callback(self, interaction):
        if not is_arbitro(interaction.user):
            return await interaction.response.send_message("⛔ Solo árbitro", ephemeral=True)

        title = f"Resultado M{self.slot}"
        if self._label_top:
            title = f"{title} — {self._label_top}"
        await interaction.response.send_modal(ResultModal(self.channel_id, self.slot, title=title))

class ResultModal(discord.ui.Modal):
    winner = discord.ui.TextInput(label="Ganador (A o B)", placeholder="A o B", max_length=1)
    score = discord.ui.TextInput(label="Marcador (ej: 250-50 / 6-3)", placeholder="250-50", max_length=20)

    def __init__(self, channel_id, slot, title="Resultado del mapa"):
        super().__init__(title=title)
        self.channel_id = channel_id
        self.slot = slot

    async def on_submit(self, interaction):
        state = MATCHES[self.channel_id]

        w = (self.winner.value or "").strip().upper()
        if w not in ("A", "B"):
            return await interaction.response.send_message("⛔ Ganador inválido (A o B)", ephemeral=True)

        # guarda (la key puede viajar como string en JSON, está OK)
        state["map_results"][str(self.slot)] = {
            "winner": w,
            "score": (self.score.value or "").strip()
        }
        state["series_score"][w] += 1

        needed = required_wins_for_mode(state["mode"])

        if state["series_score"][w] >= needed:
            state["series_winner"] = w
        await ws_broadcast(str(self.channel_id))
        if state["series_winner"]:
            team_name = state["teams"][w]["name"]
            await interaction.channel.send(
                f"🏆 **GANADOR DE LA SERIE:** {team_name} "
            f"({state['series_score']['A']}–{state['series_score']['B']})"
            )
        await interaction.response.send_message("✅ Resultado guardado", ephemeral=True)

        # autowinner
        await maybe_finish_series(self.channel_id, state)

        # refresca el “panel” (embed + siguiente botón de resultado si toca)
        try:
            # intenta editar el mensaje original si existe (si el árbitro está usando el mismo embed)
            # si no, al menos re-postea uno nuevo como fallback
            await interaction.channel.send(embed=build_embed(state), view=PickBanView(self.channel_id))
        except:
            pass

# =========================
# RUN
# =========================
bot.run(TOKEN)
