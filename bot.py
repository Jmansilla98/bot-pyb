import discord
from discord.ext import commands
import os
import asyncio
import socket
import threading

# ==========================================================
# TCP HEALTH CHECK (KOYEB)
# ==========================================================
def run_tcp_healthcheck():
    host = "0.0.0.0"
    port = int(os.getenv("PORT", 8000))
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((host, port))
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
# MAPAS Y FORMATOS
# ==========================================================
MAPAS = {
    "HP": ["Blackheart", "Colossus", "Den", "Exposure", "Scar"],
    "SnD": ["Colossus", "Den", "Exposure", "Raid", "Scar"],
    "Overload": ["Den", "Exposure", "Scar"]
}

# BO5 fijo
ORDEN_BO5 = ["HP", "SnD", "Overload", "HP", "SnD"]

# ==========================================================
# ESTADO POR CANAL
# ==========================================================
partidos = {}

def es_arbitro(user):
    return any(r.name == ROL_ARBITRO for r in user.roles)

# ==========================================================
# PICK & BAN FLUJO
# ==========================================================
def flujo_pyb():
    return [
        ("ban","HP","A"), ("ban","HP","B"),
        ("pick","HP","A"), ("side","HP","B"),
        ("pick","HP","B"), ("side","HP","A"),

        ("ban","SnD","B"), ("ban","SnD","A"),
        ("pick","SnD","B"), ("side","SnD","A"),
        ("pick","SnD","A"), ("side","SnD","B"),

        ("ban","Overload","A"), ("ban","Overload","B"),
        ("side","Overload","A"),
    ]

# ==========================================================
# BOTONES MAPAS
# ==========================================================
class MapaButton(discord.ui.Button):
    def __init__(self, mapa, modo, canal):
        super().__init__(label=mapa, style=discord.ButtonStyle.primary)
        self.mapa = mapa
        self.modo = modo
        self.canal = canal

    async def callback(self, interaction):
        p = partidos[self.canal]
        accion, modo, equipo = p["flujo"][p["paso"]]

        if self.mapa in p["baneados"][modo]:
            return await interaction.response.send_message("Mapa baneado", ephemeral=True)

        if accion == "ban":
            p["baneados"][modo].append(self.mapa)
        elif accion == "pick":
            p["mapas"].append((modo, self.mapa))

        p["paso"] += 1
        await avanzar_pyb(interaction)

class MapaView(discord.ui.View):
    def __init__(self, modo, canal):
        super().__init__(timeout=None)
        for m in MAPAS[modo]:
            self.add_item(MapaButton(m, modo, canal))

# ==========================================================
# BOTONES BANDOS
# ==========================================================
class BandoButton(discord.ui.Button):
    def __init__(self, bando, canal):
        super().__init__(label=bando, style=discord.ButtonStyle.secondary)
        self.canal = canal

    async def callback(self, interaction):
        partidos[self.canal]["paso"] += 1
        await avanzar_pyb(interaction)

class BandoView(discord.ui.View):
    def __init__(self, canal):
        super().__init__(timeout=None)
        for b in ["Ataque", "Defensa"]:
            self.add_item(BandoButton(b, canal))

# ==========================================================
# AVANZAR PYB
# ==========================================================
async def avanzar_pyb(interaction):
    p = partidos[interaction.channel.id]

    if p["paso"] >= len(p["flujo"]):
        await interaction.response.send_message("✅ Pick & Ban finalizado. Introducir resultados.")
        await interaction.channel.send(view=ResultadoView(interaction.channel.id))
        return

    accion, modo, _ = p["flujo"][p["paso"]]
    view = MapaView(modo, interaction.channel.id) if accion in ["ban","pick"] else BandoView(interaction.channel.id)
    await interaction.response.send_message(f"🔹 {accion.upper()} {modo}", view=view)

# ==========================================================
# RESULTADOS
# ==========================================================
class ResultadoModal(discord.ui.Modal, title="Resultado"):
    a = discord.ui.TextInput(label="Equipo A")
    b = discord.ui.TextInput(label="Equipo B")

    def __init__(self, canal):
        super().__init__()
        self.canal = canal

    async def on_submit(self, interaction):
        if not es_arbitro(interaction.user):
            return await interaction.response.send_message("Solo árbitro", ephemeral=True)

        a = int(self.a.value)
        b = int(self.b.value)
        if a == b:
            return await interaction.response.send_message("No empate", ephemeral=True)

        p = partidos[self.canal]
        modo, mapa = p["mapas"][len(p["resultados"])]

        p["resultados"].append({
            "modo": modo,
            "mapa": mapa,
            "A": a,
            "B": b
        })

        if len(p["resultados"]) == 3:
            await interaction.response.send_message("🏁 Partido terminado", view=ReclamacionView(self.canal))
        else:
            await interaction.response.send_message("Resultado guardado")

class ResultadoButton(discord.ui.Button):
    def __init__(self, canal):
        super().__init__(label="Introducir resultado", style=discord.ButtonStyle.success)
        self.canal = canal

    async def callback(self, interaction):
        await interaction.response.send_modal(ResultadoModal(self.canal))

class ResultadoView(discord.ui.View):
    def __init__(self, canal):
        super().__init__(timeout=None)
        self.add_item(ResultadoButton(canal))

# ==========================================================
# RECLAMACIONES
# ==========================================================
class ReclamacionView(discord.ui.View):
    def __init__(self, canal):
        super().__init__(timeout=5)
        self.canal = canal
        self.add_item(ReclamacionButton(canal))

    async def on_timeout(self):
        await self.message.edit(view=SubirView(self.canal))

class ReclamacionButton(discord.ui.Button):
    def __init__(self, canal):
        super().__init__(label="🚨 Reclamación", style=discord.ButtonStyle.danger)
        self.canal = canal

    async def callback(self, interaction):
        await interaction.response.send_message("🚨 Reclamación abierta (Ticket King aquí)")
        await interaction.channel.send(view=EditarView(self.canal))

class EditarView(discord.ui.View):
    def __init__(self, canal):
        super().__init__(timeout=None)
        self.add_item(EditarButton(canal))

class EditarButton(discord.ui.Button):
    def __init__(self, canal):
        super().__init__(label="Editar resultado", style=discord.ButtonStyle.secondary)
        self.canal = canal

    async def callback(self, interaction):
        partidos[self.canal]["resultados"].pop()
        await interaction.response.send_message("Resultado eliminado, reintroducir")
        await interaction.channel.send(view=ResultadoView(self.canal))

class SubirView(discord.ui.View):
    def __init__(self, canal):
        super().__init__(timeout=None)
        self.add_item(SubirButton(canal))

class SubirButton(discord.ui.Button):
    def __init__(self, canal):
        super().__init__(label="⬆️ Subir partido", style=discord.ButtonStyle.success)
        self.canal = canal

    async def callback(self, interaction):
        await interaction.response.send_message("[LOG] Partido enviado a Challonge")

# ==========================================================
# COMANDO PRINCIPAL
# ==========================================================
@bot.command()
async def setpartido(ctx, equipoA: discord.Role, equipoB: discord.Role, formato: str):
    partidos[ctx.channel.id] = {
        "equipoA": equipoA,
        "equipoB": equipoB,
        "formato": formato,
        "flujo": flujo_pyb(),
        "paso": 0,
        "mapas": [],
        "baneados": {"HP": [], "SnD": [], "Overload": []},
        "resultados": []
    }

    await ctx.send(
        f"🎮 Partido iniciado {equipoA.name} vs {equipoB.name}",
        view=MapaView("HP", ctx.channel.id)
    )

# ==========================================================
# ARRANQUE
# ==========================================================
if __name__ == "__main__":
    threading.Thread(target=run_tcp_healthcheck, daemon=True).start()
    bot.run(os.getenv("DISCORD_TOKEN"))
