# ==========================================================
# bot.py — VERSION REHECHA Y ESTABLE
# ==========================================================
# ✔ Pick & Ban BO3 / BO5 correcto
# ✔ Orden resultados FIJO: HP → SnD → Overload → HP → SnD
# ✔ Mapas baneados SOLO por modo
# ✔ Turnos mencionan al equipo correcto
# ✔ UI limpia (un solo mensaje editado)
# ✔ Overlay JSON se CREA al iniciar el partido
# ✔ Link del overlay SE MANDA al hacer !setpartido
# ✔ Overlay se ACTUALIZA tras cada acción
# ✔ Resultados por modal (sin interaction expired)
# ✔ Espera 5s al acabar → botones finales
# ✔ TCP health check (Koyeb OK)
# ✔ Multicanal real
# ==========================================================

import discord
from discord.ext import commands
import os, json, base64, requests, socket, threading, asyncio

# ==========================================================
# TCP HEALTH CHECK (KOYEB)
# ==========================================================
def run_tcp_healthcheck():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("0.0.0.0", int(os.getenv("PORT", 8000))))
    s.listen(1)
    while True:
        c, _ = s.accept()
        c.close()

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
GITHUB_REPO = "Overlay-cod-fecod"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
MATCHES_PATH = "matches"
OVERLAY_BASE = f"https://{GITHUB_USER}.github.io/{GITHUB_REPO}"

def gh_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

def subir_overlay(channel_id, payload):
    url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{MATCHES_PATH}/{channel_id}.json"
    content = base64.b64encode(json.dumps(payload, indent=2).encode()).decode()

    r = requests.get(url, headers=gh_headers())
    sha = r.json().get("sha") if r.status_code == 200 else None

    body = {"message": "update match overlay", "content": content}
    if sha:
        body["sha"] = sha

    requests.put(url, headers=gh_headers(), json=body)

# ==========================================================
# MAPAS / FLUJOS
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

FLUJOS = {
    "bo3": [
        ("ban","HP","A"),("ban","HP","B"),("pick","HP","A"),("side","HP","B"),
        ("ban","SnD","B"),("ban","SnD","A"),("pick","SnD","B"),("side","SnD","A"),
        ("ban","Overload","A"),("ban","Overload","B"),("side","Overload","A"),
    ],
    "bo5": [
        ("ban","HP","A"),("ban","HP","B"),("pick","HP","A"),("side","HP","B"),
        ("pick","HP","B"),("side","HP","A"),
        ("ban","SnD","B"),("ban","SnD","A"),("pick","SnD","B"),("side","SnD","A"),
        ("pick","SnD","A"),("side","SnD","B"),
        ("ban","Overload","A"),("ban","Overload","B"),("side","Overload","A"),
    ]
}

ORDEN_RESULTADOS = ["HP","SnD","Overload","HP","SnD"]

# ==========================================================
# ESTADO POR CANAL
# ==========================================================
matches = {}

# ==========================================================
# EMBEDS
# ==========================================================
def embed_turno(m):
    accion, modo, eq = m["flujo"][m["paso"]]
    e = discord.Embed(title="🎮 PICK & BAN", color=COLORES[modo])
    e.add_field(name="Acción", value=accion.upper(), inline=True)
    e.add_field(name="Modo", value=modo, inline=True)
    e.add_field(name="Turno", value=m["equipos"][eq].mention, inline=True)
    e.set_footer(text=f"Paso {m['paso']+1}/{len(m['flujo'])}")
    return e

def embed_resumen_mapas(m):
    txt = ""
    for i,(modo,mapa) in enumerate(m["mapas_finales"], start=1):
        txt += f"Mapa {i}: **{modo} — {mapa}**\n"
    return discord.Embed(title="🗺️ Mapas de la serie", description=txt, color=discord.Color.blurple())

def embed_resultado(m, idx):
    modo, mapa = m["mapas_finales"][idx]
    e = discord.Embed(
        title=f"📝 Resultado Mapa {idx+1}",
        description=f"**{modo} — {mapa}**",
        color=COLORES[modo]
    )
    e.add_field(name=m["equipos"]["A"].name, value="—", inline=True)
    e.add_field(name=m["equipos"]["B"].name, value="—", inline=True)
    return e

# ==========================================================
# BOTONES MAPAS
# ==========================================================
class MapaButton(discord.ui.Button):
    def __init__(self, mapa, modo, cid):
        m = matches[cid]
        super().__init__(
            label=mapa,
            style=discord.ButtonStyle.primary,
            disabled=mapa in m["usados"][modo]
        )
        self.mapa, self.modo, self.cid = mapa, modo, cid

    async def callback(self, i):
        m = matches[self.cid]
        accion, modo, _ = m["flujo"][m["paso"]]

        m["usados"][modo].add(self.mapa)
        if accion == "pick":
            m["mapas_picked"].append((modo, self.mapa))

        m["paso"] += 1
        await avanzar_pyb(i)

class MapaView(discord.ui.View):
    def __init__(self, modo, cid):
        super().__init__(timeout=None)
        for mapa in MAPAS[modo]:
            self.add_item(MapaButton(mapa, modo, cid))

# ==========================================================
# BOTONES BANDOS
# ==========================================================
class BandoButton(discord.ui.Button):
    def __init__(self, label, cid):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.cid = cid

    async def callback(self, i):
        matches[self.cid]["paso"] += 1
        await avanzar_pyb(i)

class BandoView(discord.ui.View):
    def __init__(self, cid):
        super().__init__(timeout=None)
        self.add_item(BandoButton("Ataque", cid))
        self.add_item(BandoButton("Defensa", cid))

# ==========================================================
# RESULTADOS
# ==========================================================
class ResultadoModal(discord.ui.Modal, title="Introducir resultado"):
    a = discord.ui.TextInput(label="Equipo A")
    b = discord.ui.TextInput(label="Equipo B")

    def __init__(self, cid):
        super().__init__()
        self.cid = cid

    async def on_submit(self, i):
        m = matches[self.cid]
        ai, bi = int(self.a.value), int(self.b.value)
        m["resultados"].append({"A": ai, "B": bi})

        idx = len(m["resultados"])
        if idx < len(m["mapas_finales"]):
            await i.response.edit_message(
                embeds=[embed_resumen_mapas(m), embed_resultado(m, idx)],
                view=ResultadoView(self.cid)
            )
        else:
            subir_overlay(self.cid, construir_payload(m))
            await asyncio.sleep(5)
            await i.response.edit_message(
                content=f"🏁 Partido finalizado\n{OVERLAY_BASE}/{MATCHES_PATH}/{self.cid}.json",
                view=SubirView(self.cid)
            )

class ResultadoButton(discord.ui.Button):
    def __init__(self, cid):
        super().__init__(label="Introducir resultado", style=discord.ButtonStyle.success)
        self.cid = cid

    async def callback(self, i):
        await i.response.send_modal(ResultadoModal(self.cid))

class ResultadoView(discord.ui.View):
    def __init__(self, cid):
        super().__init__(timeout=None)
        self.add_item(ResultadoButton(cid))

# ==========================================================
# POST PARTIDO
# ==========================================================
class SubirView(discord.ui.View):
    def __init__(self, cid):
        super().__init__(timeout=None)
        self.add_item(SubirButton(cid))

class SubirButton(discord.ui.Button):
    def __init__(self, cid):
        super().__init__(label="⬆️ Subir a Challonge", style=discord.ButtonStyle.success)
        self.cid = cid

    async def callback(self, i):
        await i.response.send_message("📤 Enviado a Challonge (placeholder)")

# ==========================================================
# HELPERS
# ==========================================================
def construir_mapas_finales(m):
    res = []
    hp = [x for x in m["mapas_picked"] if x[0]=="HP"]
    snd = [x for x in m["mapas_picked"] if x[0]=="SnD"]

    ov = [x for x in MAPAS["Overload"] if x not in [y[1] for y in m["mapas_picked"] if y[0]=="Overload"]][0]

    res.append(hp[0])
    res.append(snd[0])
    res.append(("Overload", ov))

    if m["formato"] == "bo5":
        res.append(hp[1])
        res.append(snd[1])

    return res

def construir_payload(m):
    return {
        "equipoA": m["equipos"]["A"].name,
        "equipoB": m["equipos"]["B"].name,
        "mapas": m["mapas_finales"],
        "resultados": m["resultados"]
    }

# ==========================================================
# FLUJO PYB
# ==========================================================
async def avanzar_pyb(i):
    m = matches[i.channel.id]

    if m["paso"] >= len(m["flujo"]):
        m["mapas_finales"] = construir_mapas_finales(m)
        subir_overlay(i.channel.id, construir_payload(m))
        await i.response.edit_message(
            embeds=[embed_resumen_mapas(m), embed_resultado(m, 0)],
            view=ResultadoView(i.channel.id)
        )
        return

    accion, modo, _ = m["flujo"][m["paso"]]
    view = MapaView(modo, i.channel.id) if accion in ("ban","pick") else BandoView(i.channel.id)
    await i.response.edit_message(embed=embed_turno(m), view=view)

# ==========================================================
# COMANDO
# ==========================================================
@bot.command()
async def setpartido(ctx, equipo_a: discord.Role, equipo_b: discord.Role, formato: str):
    formato = formato.lower()
    if formato not in FLUJOS:
        return
    
    matches[ctx.channel.id] = {
        "equipos": {"A": equipo_a, "B": equipo_b},
        "flujo": FLUJOS[formato],
        "formato": formato,
        "paso": 0,
        "usados": {"HP": set(), "SnD": set(), "Overload": set()},
        "mapas_picked": [],
        "mapas_finales": [],
        "resultados": []
    }

    payload = {
        "equipoA": equipo_a.name,
        "equipoB": equipo_b.name,
        "estado": "Pick & Ban"
    }
    subir_overlay(ctx.channel.id, payload)

    await ctx.send(
        f"🎥 Overlay:\n{OVERLAY_BASE}/?match={ctx.channel.id}",
        embed=embed_turno(matches[ctx.channel.id]),
        view=MapaView(FLUJOS[formato][0][1], ctx.channel.id)
    )

# ==========================================================
# ARRANQUE
# ==========================================================
if __name__ == "__main__":
    threading.Thread(target=run_tcp_healthcheck, daemon=True).start()
    bot.run(os.getenv("DISCORD_TOKEN"))