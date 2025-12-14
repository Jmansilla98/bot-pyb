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
# MAPAS
# ==========================================================
MAPAS = {
    "HP": ["Blackheart", "Colossus", "Den", "Exposure", "Scar"],
    "SnD": ["Colossus", "Den", "Exposure", "Raid", "Scar"],
    "Overload": ["Den", "Exposure", "Scar"]
}

# ==========================================================
# PICK & BAN FLOW
# ==========================================================
FORMATOS = {
    "bo5": [
        ("ban","HP","A"),
        ("ban","HP","B"),
        ("pick","HP","A"),
        ("side","HP","B"),
        ("pick","HP","B"),
        ("side","HP","A"),

        ("ban","SnD","B"),
        ("ban","SnD","A"),
        ("pick","SnD","B"),
        ("side","SnD","A"),
        ("pick","SnD","A"),
        ("side","SnD","B"),

        ("ban","Overload","A"),
        ("ban","Overload","B"),
        ("decider","Overload","A")
    ]
}

# ==========================================================
# ESTADO POR CANAL
# ==========================================================
matches = {}

def es_arbitro(member):
    return any(r.name == ROL_ARBITRO for r in member.roles)

def equipo_turno(match):
    accion, _, eq = FORMATOS[match["formato"]][match["paso"]]
    return match["teamA"] if eq == "A" else match["teamB"]

# ==========================================================
# VIEWS PICK & BAN
# ==========================================================
class MapButton(discord.ui.Button):
    def __init__(self, mapa, match, modo):
        super().__init__(label=mapa, style=discord.ButtonStyle.primary)
        self.mapa = mapa
        self.match = match
        self.modo = modo

    async def callback(self, interaction):
        if interaction.user not in self.match["teamA"].members and interaction.user not in self.match["teamB"].members:
            return await interaction.response.send_message("⛔ No eres jugador del partido", ephemeral=True)

        if self.mapa in self.match["usados"][self.modo]:
            return await interaction.response.send_message("❌ Mapa ya usado", ephemeral=True)

        accion, modo, _ = FORMATOS[self.match["formato"]][self.match["paso"]]

        self.match["usados"][modo].add(self.mapa)

        if accion == "pick":
            self.match["mapas_finales"].append((modo, self.mapa))

        self.match["paso"] += 1
        await avanzar_pyb(interaction)

class MapView(discord.ui.View):
    def __init__(self, match, modo):
        super().__init__(timeout=None)
        for m in MAPAS[modo]:
            self.add_item(MapButton(m, match, modo))

class SideButton(discord.ui.Button):
    def __init__(self, side, match):
        super().__init__(label=side, style=discord.ButtonStyle.secondary)
        self.match = match

    async def callback(self, interaction):
        self.match["paso"] += 1
        await avanzar_pyb(interaction)

class SideView(discord.ui.View):
    def __init__(self, match):
        super().__init__(timeout=None)
        self.add_item(SideButton("Ataque", match))
        self.add_item(SideButton("Defensa", match))

# ==========================================================
# PICK & BAN ADVANCE
# ==========================================================
async def avanzar_pyb(interaction):
    match = matches[interaction.channel.id]

    if match["paso"] >= len(FORMATOS[match["formato"]]):
        return await interaction.response.send_message("✅ Pick & Ban terminado")

    accion, modo, _ = FORMATOS[match["formato"]][match["paso"]]

    if accion in ["pick","ban"]:
        view = MapView(match, modo)
    else:
        view = SideView(match)

    await interaction.response.send_message(
        f"🎮 **{accion.upper()} {modo}**\nTurno: {equipo_turno(match).mention}",
        view=view
    )

# ==========================================================
# COMANDO SETPARTIDO
# ==========================================================
@bot.command()
async def setpartido(ctx, teamA: discord.Role, teamB: discord.Role, formato: str):
    formato = formato.lower()
    if formato not in FORMATOS:
        return await ctx.send("❌ Formato inválido")

    matches[ctx.channel.id] = {
        "teamA": teamA,
        "teamB": teamB,
        "formato": formato,
        "paso": 0,
        "usados": {"HP": set(), "SnD": set(), "Overload": set()},
        "mapas_finales": [],
        "resultados": [],
        "reclamacion": False
    }

    await ctx.send(
        f"🎮 Partido creado: **{teamA.name} vs {teamB.name}** ({formato.upper()})"
    )

    await avanzar_pyb(ctx)

# ==========================================================
# ARRANQUE
# ==========================================================
if __name__ == "__main__":
    threading.Thread(target=run_tcp_healthcheck, daemon=True).start()
    bot.run(os.getenv("DISCORD_TOKEN"))
