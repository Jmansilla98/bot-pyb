# ===========================
# bot.py — OVERLAY ESTABLE
# ===========================

import discord
from discord.ext import commands
import os, json, base64, requests, socket, threading

# ===========================
# TCP HEALTH CHECK (KOYEB)
# ===========================
def run_tcp_healthcheck():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("0.0.0.0", int(os.getenv("PORT", 8000))))
    s.listen(1)
    while True:
        c, _ = s.accept()
        c.close()

# ===========================
# DISCORD
# ===========================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ===========================
# GITHUB
# ===========================
GITHUB_USER = "Jmansilla98"
GITHUB_REPO = "Overlay-cod-fecod"
MATCHES_PATH = "matches"
TOKEN = os.getenv("GITHUB_TOKEN")

OVERLAY_BASE = f"https://{GITHUB_USER}.github.io/{GITHUB_REPO}"

def gh_headers():
    return {
        "Authorization": f"token {TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

def subir_overlay(channel_id, payload):
    url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{MATCHES_PATH}/{channel_id}.json"
    content = base64.b64encode(json.dumps(payload, indent=2).encode()).decode()

    r = requests.get(url, headers=gh_headers())
    sha = r.json().get("sha") if r.status_code == 200 else None

    body = {"message": "update overlay", "content": content}
    if sha:
        body["sha"] = sha

    requests.put(url, headers=gh_headers(), json=body)

# ===========================
# MAPAS
# ===========================
MAPAS = {
    "HP": ["Blackheart", "Colossus", "Den", "Exposure", "Scar"],
    "SnD": ["Colossus", "Den", "Exposure", "Raid", "Scar"],
    "Overload": ["Den", "Exposure", "Scar"]
}

FLUJO_BO5 = [
    ("ban","HP","A"),("ban","HP","B"),
    ("pick","HP","A"),("side","HP","B"),
    ("pick","HP","B"),("side","HP","A"),

    ("ban","SnD","B"),("ban","SnD","A"),
    ("pick","SnD","B"),("side","SnD","A"),
    ("pick","SnD","A"),("side","SnD","B"),

    ("ban","Overload","A"),("ban","Overload","B"),
    ("side","Overload","A")
]

# ===========================
# ESTADO
# ===========================
matches = {}

def estado_overlay(match):
    return {
        "equipoA": match["equipoA"],
        "equipoB": match["equipoB"],
        "estado": "Pick & Ban",
        "mapas": match["mapas"]
    }

# ===========================
# BOTONES
# ===========================
class MapaButton(discord.ui.Button):
    def __init__(self, mapa, modo, cid):
        super().__init__(label=mapa, style=discord.ButtonStyle.primary)
        self.mapa, self.modo, self.cid = mapa, modo, cid

    async def callback(self, i):
        m = matches[self.cid]
        accion, modo, eq = m["flujo"][m["paso"]]

        entry = {
            "modo": modo,
            "mapa": self.mapa,
            "estado": accion,
            "equipo": eq,
            "bando": None
        }

        m["mapas"].append(entry)
        m["paso"] += 1

        subir_overlay(self.cid, estado_overlay(m))
        await avanzar(i)

class BandoButton(discord.ui.Button):
    def __init__(self, label, cid):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.cid = cid

    async def callback(self, i):
        m = matches[self.cid]
        m["mapas"][-1]["bando"] = self.label
        m["paso"] += 1

        subir_overlay(self.cid, estado_overlay(m))
        await avanzar(i)

class MapaView(discord.ui.View):
    def __init__(self, modo, cid):
        super().__init__(timeout=None)
        for m in MAPAS[modo]:
            self.add_item(MapaButton(m, modo, cid))

class BandoView(discord.ui.View):
    def __init__(self, cid):
        super().__init__(timeout=None)
        self.add_item(BandoButton("Ataque", cid))
        self.add_item(BandoButton("Defensa", cid))

# ===========================
# FLUJO
# ===========================
async def avanzar(i):
    m = matches[i.channel.id]

    if m["paso"] >= len(m["flujo"]):
        await i.response.edit_message(content="✅ Pick & Ban finalizado")
        return

    accion, modo, _ = m["flujo"][m["paso"]]
    view = MapaView(modo, i.channel.id) if accion in ("ban","pick") else BandoView(i.channel.id)
    await i.response.edit_message(content=f"{accion.upper()} {modo}", view=view)

# ===========================
# COMANDO
# ===========================
@bot.command()
async def setpartido(ctx, teamA: discord.Role, teamB: discord.Role):
    cid = ctx.channel.id

    matches[cid] = {
        "equipoA": teamA.name,
        "equipoB": teamB.name,
        "flujo": FLUJO_BO5,
        "paso": 0,
        "mapas": []
    }

    subir_overlay(cid, estado_overlay(matches[cid]))

    await ctx.send(
        f"🎥 Overlay:\n{OVERLAY_BASE}/?match={cid}",
        view=MapaView("HP", cid)
    )

# ===========================
# RUN
# ===========================
if __name__ == "__main__":
    threading.Thread(target=run_tcp_healthcheck, daemon=True).start()
    bot.run(os.getenv("DISCORD_TOKEN"))
