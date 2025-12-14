import discord
from discord.ext import commands
import os
import json
import base64
import requests
import socket
import threading
import asyncio

# ==========================================================
# TCP HEALTH CHECK (KOYEB)
# ==========================================================
def run_tcp_healthcheck():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("0.0.0.0", int(os.getenv("PORT", 8000))))
    s.listen(1)
    while True:
        conn, _ = s.accept()
        conn.close()

# ==========================================================
# DISCORD CONFIG
# ==========================================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

ROL_ARBITRO = "Arbitro"

# ==========================================================
# GITHUB / OVERLAY
# ==========================================================
GITHUB_USER = "Jmansilla98"
GITHUB_REPO = "overlay-cod-fecod"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
MATCHES_PATH = "matches"

OVERLAY_BASE = f"https://{GITHUB_USER}.github.io/{GITHUB_REPO}"

# ==========================================================
# MAPAS
# ==========================================================
MAPAS = {
    "HP": ["Blackheart", "Colossus", "Den", "Exposure", "Scar"],
    "SnD": ["Colossus", "Den", "Exposure", "Raid", "Scar"],
    "Overload": ["Den", "Exposure", "Scar"]
}

COLORES = {
    "HP": discord.Color.red(),
    "SnD": discord.Color.gold(),
    "Overload": discord.Color.purple()
}

FORMATOS = {
    "bo3": [
        ("ban","HP","A"),("ban","HP","B"),("pick","HP","A"),("side","HP","B"),
        ("ban","SnD","B"),("ban","SnD","A"),("pick","SnD","B"),("side","SnD","A"),
        ("ban","Overload","A"),("ban","Overload","B"),("side","Overload","A")
    ],
    "bo5": [
        ("ban","HP","A"),("ban","HP","B"),("pick","HP","A"),("side","HP","B"),
        ("pick","HP","B"),("side","HP","A"),
        ("ban","SnD","B"),("ban","SnD","A"),("pick","SnD","B"),("side","SnD","A"),
        ("pick","SnD","A"),("side","SnD","B"),
        ("ban","Overload","A"),("ban","Overload","B"),("side","Overload","A")
    ]
}

# ==========================================================
# ESTADO POR CANAL
# ==========================================================
matches = {}

def es_arbitro(user):
    return any(r.name == ROL_ARBITRO for r in user.roles)

# ==========================================================
# GITHUB HELPERS
# ==========================================================
def gh_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

def subir_overlay(channel_id, match):
    data = {
        "equipo_a": match["equipos"]["A"],
        "equipo_b": match["equipos"]["B"],
        "mapas": match["mapas_finales"],
        "resultados": match["resultados"],
        "reclamacion": match.get("reclamacion", False)
    }

    path = f"{MATCHES_PATH}/{channel_id}.json"
    url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{path}"
    content = base64.b64encode(json.dumps(data, indent=2).encode()).decode()

    r = requests.get(url, headers=gh_headers())
    sha = r.json().get("sha") if r.status_code == 200 else None

    body = {"message": "update match", "content": content}
    if sha:
        body["sha"] = sha

    requests.put(url, headers=gh_headers(), json=body)

# ==========================================================
# EMBEDS
# ==========================================================
def embed_turno(match):
    accion, modo, eq = match["flujo"][match["paso"]]
    e = discord.Embed(title="🎮 PICK & BAN", color=COLORES[modo])
    e.add_field(name="Acción", value=accion.upper())
    e.add_field(name="Modo", value=modo)
    e.add_field(name="Turno", value=match["equipos"][eq])
    e.set_footer(text=f"Paso {match['paso']+1}/{len(match['flujo'])}")
    return e

def embed_lista_mapas(match):
    txt = ""
    for i,(m,mp) in enumerate(match["mapas_finales"], start=1):
        txt += f"**Mapa {i}** — {m} · {mp}\n"
    return discord.Embed(
        title="🗺️ Mapas de la serie",
        description=txt,
        color=discord.Color.blurple()
    )

def embed_resultado(match, idx):
    modo, mapa = match["mapas_finales"][idx]
    e = discord.Embed(
        title=f"📝 Resultado Mapa {idx+1}",
        description=f"{modo} — {mapa}",
        color=COLORES[modo]
    )
    e.add_field(name=match["equipos"]["A"], value="—")
    e.add_field(name=match["equipos"]["B"], value="—")
    return e

# ==========================================================
# BOTONES MAPA
# ==========================================================
class MapaButton(discord.ui.Button):
    def __init__(self, mapa, modo, cid):
        disabled = mapa in matches[cid]["usados"][modo]
        super().__init__(label=mapa, style=discord.ButtonStyle.primary, disabled=disabled)
        self.mapa = mapa
        self.modo = modo
        self.cid = cid

    async def callback(self, interaction):
        match = matches[self.cid]
        accion, modo, _ = match["flujo"][match["paso"]]
        match["usados"][modo].add(self.mapa)
        if accion == "pick":
            match["mapas_picked"].append((modo, self.mapa))
        match["paso"] += 1
        await avanzar_pyb(interaction)

class MapaView(discord.ui.View):
    def __init__(self, modo, cid):
        super().__init__(timeout=None)
        for m in MAPAS[modo]:
            self.add_item(MapaButton(m, modo, cid))

# ==========================================================
# BOTONES BANDO
# ==========================================================
class BandoButton(discord.ui.Button):
    def __init__(self, label, cid):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.cid = cid

    async def callback(self, interaction):
        matches[self.cid]["paso"] += 1
        await avanzar_pyb(interaction)

class BandoView(discord.ui.View):
    def __init__(self, cid):
        super().__init__(timeout=None)
        self.add_item(BandoButton("Ataque", cid))
        self.add_item(BandoButton("Defensa", cid))

# ==========================================================
# RESULTADOS
# ==========================================================
class ResultadoModal(discord.ui.Modal, title="Resultado"):
    a = discord.ui.TextInput(label="Equipo A")
    b = discord.ui.TextInput(label="Equipo B")

    def __init__(self, cid):
        super().__init__()
        self.cid = cid

    async def on_submit(self, interaction):
        match = matches[self.cid]
        a,b = int(self.a.value), int(self.b.value)
        match["resultados"].append({"a":a,"b":b})
        idx = len(match["resultados"])
        if idx < len(match["mapas_finales"]):
            await interaction.response.edit_message(
                embeds=[embed_lista_mapas(match), embed_resultado(match, idx)],
                view=ResultadoView(self.cid)
            )
        else:
            subir_overlay(self.cid, match)
            await interaction.response.edit_message(
                content=f"🎥 Overlay: {OVERLAY_BASE}/{MATCHES_PATH}/{self.cid}.json",
                view=ReclamacionView(self.cid)
            )

class ResultadoView(discord.ui.View):
    def __init__(self, cid):
        super().__init__(timeout=None)
        self.add_item(ResultadoButton(cid))

class ResultadoButton(discord.ui.Button):
    def __init__(self, cid):
        super().__init__(label="Introducir resultado", style=discord.ButtonStyle.success)
        self.cid = cid

    async def callback(self, interaction):
        await interaction.response.send_modal(ResultadoModal(self.cid))

# ==========================================================
# RECLAMACIÓN
# ==========================================================
class ReclamacionView(discord.ui.View):
    def __init__(self, cid):
        super().__init__(timeout=5)
        self.cid = cid
        self.add_item(ReclamacionButton(cid))

    async def on_timeout(self):
        self.clear_items()
        self.add_item(SubirButton(self.cid))

class ReclamacionButton(discord.ui.Button):
    def __init__(self, cid):
        super().__init__(label="🚨 Reclamación", style=discord.ButtonStyle.danger)
        self.cid = cid

    async def callback(self, interaction):
        matches[self.cid]["reclamacion"] = True
        await interaction.response.send_message("🎫 Ticket creado")

class SubirButton(discord.ui.Button):
    def __init__(self, cid):
        super().__init__(label="⬆️ Subir partido", style=discord.ButtonStyle.success)
        self.cid = cid

    async def callback(self, interaction):
        await interaction.response.send_message("📤 Enviado a Challonge (placeholder)")

# ==========================================================
# FLUJO PYB
# ==========================================================
async def avanzar_pyb(interaction):
    match = matches[interaction.channel.id]
    if match["paso"] >= len(match["flujo"]):
        # ORDEN CORRECTO RESULTADOS
        if match["formato"] == "bo3":
            match["mapas_finales"] = [
                match["mapas_picked"][0],
                match["mapas_picked"][1],
                ("Overload", next(m for m in MAPAS["Overload"] if m not in [x[1] for x in match["mapas_picked"]]))
            ]
        else:
            match["mapas_finales"] = [
                match["mapas_picked"][0],
                match["mapas_picked"][1],
                ("Overload", next(m for m in MAPAS["Overload"] if m not in [x[1] for x in match["mapas_picked"]])),
                match["mapas_picked"][2],
                match["mapas_picked"][3]
            ]

        await interaction.response.edit_message(
            embeds=[embed_lista_mapas(match), embed_resultado(match,0)],
            view=ResultadoView(interaction.channel.id)
        )
        return

    accion, modo, _ = match["flujo"][match["paso"]]
    view = MapaView(modo, interaction.channel.id) if accion in ("ban","pick") else BandoView(interaction.channel.id)
    await interaction.response.edit_message(embed=embed_turno(match), view=view)

# ==========================================================
# COMANDO
# ==========================================================
@bot.command()
async def setpartido(ctx, equipo_a: discord.Role, equipo_b: discord.Role, formato: str):
    formato = formato.lower()
    matches[ctx.channel.id] = {
        "equipos": {"A": equipo_a.name, "B": equipo_b.name},
        "formato": formato,
        "flujo": FORMATOS[formato],
        "paso": 0,
        "usados": {"HP": set(), "SnD": set(), "Overload": set()},
        "mapas_picked": [],
        "mapas_finales": [],
        "resultados": []
    }
    await ctx.send(embed=embed_turno(matches[ctx.channel.id]),
                   view=MapaView(FORMATOS[formato][0][1], ctx.channel.id))

# ==========================================================
# RUN
# ==========================================================
if __name__ == "__main__":
    threading.Thread(target=run_tcp_healthcheck, daemon=True).start()
    bot.run(os.getenv("DISCORD_TOKEN"))
