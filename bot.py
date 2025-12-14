import discord
from discord.ext import commands
import os
import asyncio
import socket
import threading
import re

# ==========================================================
# TCP HEALTH CHECK (KOYEB WEB SERVICE)
# ==========================================================
def tcp_healthcheck():
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
ROL_ARBITRO = "team1"

# ==========================================================
# MAPAS
# ==========================================================
MAPAS = {
    "HP": ["Blackheart", "Colossus", "Den", "Exposure", "Scar"],
    "SnD": ["Colossus", "Den", "Exposure", "Raid", "Scar"],
    "Overload": ["Den", "Exposure", "Scar"]
}

BANDOS = ["Ataque", "Defensa"]

# ==========================================================
# ESTADO POR CANAL
# ==========================================================
partidos = {}

def es_arbitro(user):
    return any(r.name == ROL_ARBITRO for r in user.roles)

# ==========================================================
# PICK & BAN STEPS (BO5)
# ==========================================================
PICKBAN_BO5 = [
    ("ban","HP","A"), ("ban","HP","B"),
    ("pick","HP","A"), ("side","HP","B"),
    ("pick","HP","B"), ("side","HP","A"),

    ("ban","SnD","B"), ("ban","SnD","A"),
    ("pick","SnD","B"), ("side","SnD","A"),
    ("pick","SnD","A"), ("side","SnD","B"),

    ("ban","Overload","A"), ("ban","Overload","B"),
    ("side","Overload","A")
]

# ==========================================================
# EMBED TURNO
# ==========================================================
def embed_turno(p):
    accion, modo, eq = PICKBAN_BO5[p["paso"]]
    equipo = p["teamA"] if eq == "A" else p["teamB"]
    e = discord.Embed(
        title="🎮 PICK & BAN",
        description=f"**Acción:** {accion.upper()}\n**Modo:** {modo}\n**Turno:** {equipo.mention}",
        color=discord.Color.blurple()
    )
    return e

# ==========================================================
# BOTONES MAPAS
# ==========================================================
class MapaButton(discord.ui.Button):
    def __init__(self, mapa, modo, cid):
        super().__init__(label=mapa, style=discord.ButtonStyle.primary)
        self.mapa = mapa
        self.modo = modo
        self.cid = cid

    async def callback(self, interaction):
        p = partidos[self.cid]
        accion, modo, _ = PICKBAN_BO5[p["paso"]]

        if accion == "ban":
            p["baneados"][modo].add(self.mapa)
        elif accion == "pick":
            p["mapas"].append((modo, self.mapa))

        p["paso"] += 1
        await avanzar(interaction)

class MapaView(discord.ui.View):
    def __init__(self, modo, cid):
        super().__init__(timeout=None)
        for m in MAPAS[modo]:
            self.add_item(MapaButton(m, modo, cid))

# ==========================================================
# BOTONES BANDOS
# ==========================================================
class BandoButton(discord.ui.Button):
    def __init__(self, bando, cid):
        super().__init__(label=bando, style=discord.ButtonStyle.secondary)
        self.cid = cid

    async def callback(self, interaction):
        partidos[self.cid]["paso"] += 1
        await avanzar(interaction)

class BandoView(discord.ui.View):
    def __init__(self, cid):
        super().__init__(timeout=None)
        for b in BANDOS:
            self.add_item(BandoButton(b, cid))

# ==========================================================
# RESULTADOS MODAL
# ==========================================================
class ResultadoModal(discord.ui.Modal, title="Resultado"):
    a = discord.ui.TextInput(label="Equipo A")
    b = discord.ui.TextInput(label="Equipo B")

    def __init__(self, cid):
        super().__init__()
        self.cid = cid

    async def on_submit(self, interaction):
        p = partidos[self.cid]
        if not re.fullmatch(r"\d+", self.a.value) or not re.fullmatch(r"\d+", self.b.value):
            return await interaction.response.send_message("❌ Solo números", ephemeral=True)

        a, b = int(self.a.value), int(self.b.value)
        if a == b:
            return await interaction.response.send_message("❌ No empate", ephemeral=True)

        p["resultados"].append((a, b))

        winsA = sum(1 for x,y in p["resultados"] if x > y)
        winsB = sum(1 for x,y in p["resultados"] if y > x)

        if winsA == 3 or winsB == 3:
            await interaction.response.send_message(
                "🏁 Serie finalizada",
                view=ReclamacionView(self.cid)
            )
        else:
            await interaction.response.send_message("✅ Resultado guardado")

# ==========================================================
# RECLAMACIONES
# ==========================================================
class ReclamacionButton(discord.ui.Button):
    def __init__(self, cid):
        super().__init__(label="🚨 Reclamación", style=discord.ButtonStyle.danger)
        self.cid = cid

    async def callback(self, interaction):
        await interaction.response.send_message(
            "⚖️ Reclamación registrada",
            view=ArbitroView(self.cid)
        )

class ReclamacionView(discord.ui.View):
    def __init__(self, cid):
        super().__init__(timeout=10)
        self.add_item(ReclamacionButton(cid))

class ArbitroView(discord.ui.View):
    def __init__(self, cid):
        super().__init__(timeout=None)
        self.cid = cid
        self.add_item(EditarResultadoButton(cid))
        self.add_item(SubirPartidoButton())

class EditarResultadoButton(discord.ui.Button):
    def __init__(self, cid):
        super().__init__(label="✏️ Editar resultado", style=discord.ButtonStyle.secondary)
        self.cid = cid

    async def callback(self, interaction):
        partidos[self.cid]["resultados"].pop()
        await interaction.response.send_modal(ResultadoModal(self.cid))

class SubirPartidoButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="⬆️ Subir partido", style=discord.ButtonStyle.success)

    async def callback(self, interaction):
        print("[CHALLONGE] Partido enviado")
        await interaction.response.send_message("✅ Partido enviado")

# ==========================================================
# FLUJO PICK & BAN
# ==========================================================
async def avanzar(interaction):
    p = partidos[interaction.channel.id]
    if p["paso"] >= len(PICKBAN_BO5):
        return await interaction.response.send_message("🎯 Pick & Ban completado\nUsa !resultado")

    accion, modo, _ = PICKBAN_BO5[p["paso"]]
    view = MapaView(modo, interaction.channel.id) if accion in ["pick","ban"] else BandoView(interaction.channel.id)
    await interaction.response.edit_message(embed=embed_turno(p), view=view)

# ==========================================================
# COMANDOS
# ==========================================================
@bot.command()
async def setpartido(ctx, teamA: discord.Role, teamB: discord.Role, formato: str):
    if formato.lower() != "bo5":
        return await ctx.send("❌ Solo BO5 soportado ahora")

    partidos[ctx.channel.id] = {
        "teamA": teamA,
        "teamB": teamB,
        "paso": 0,
        "baneados": {"HP":set(),"SnD":set(),"Overload":set()},
        "mapas": [],
        "resultados": []
    }

    await ctx.send(embed=embed_turno(partidos[ctx.channel.id]),
                   view=MapaView("HP", ctx.channel.id))

@bot.command()
async def resultado(ctx):
    await ctx.send_modal(ResultadoModal(ctx.channel.id))

# ==========================================================
# RUN
# ==========================================================
if __name__ == "__main__":
    threading.Thread(target=tcp_healthcheck, daemon=True).start()
    bot.run(os.getenv("DISCORD_TOKEN"))
