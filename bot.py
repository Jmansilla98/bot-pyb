import discord
from discord.ext import commands
import os
import asyncio
import socket
import threading

# ==========================================================
# TCP HEALTH CHECK (PARA WEB SERVICE)
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

BANDOS = ["Ataque", "Defensa"]

# ==========================================================
# FORMATOS PICK & BAN
# ==========================================================
FORMATOS = {
    "bo3": [
        ("ban", "HP", "A"), ("ban", "HP", "B"),
        ("pick", "HP", "A"), ("side", "HP", "B"),
        ("ban", "SnD", "B"), ("ban", "SnD", "A"),
        ("pick", "SnD", "B"), ("side", "SnD", "A"),
        ("ban", "Overload", "A"), ("ban", "Overload", "B"),
        ("side", "Overload", "A")
    ],
    "bo5": [
        ("ban", "HP", "A"), ("ban", "HP", "B"),
        ("pick", "HP", "A"), ("side", "HP", "B"),
        ("pick", "HP", "B"), ("side", "HP", "A"),
        ("ban", "SnD", "B"), ("ban", "SnD", "A"),
        ("pick", "SnD", "B"), ("side", "SnD", "A"),
        ("pick", "SnD", "A"), ("side", "SnD", "B"),
        ("ban", "Overload", "A"), ("ban", "Overload", "B"),
        ("side", "Overload", "A")
    ]
}

# ==========================================================
# ESTADO POR CANAL
# ==========================================================
matches = {}

def current_team(match):
    _, _, eq = FORMATOS[match["formato"]][match["step"]]
    return match["teamA"] if eq == "A" else match["teamB"]

# ==========================================================
# EMBEDS
# ==========================================================
def embed_turn(match):
    action, mode, _ = FORMATOS[match["formato"]][match["step"]]
    e = discord.Embed(title=f"{action.upper()} {mode}")
    e.add_field(name="Turno", value=current_team(match).mention, inline=False)
    e.add_field(
        name="Equipos",
        value=f"🔵 {match['teamA'].mention}\n🔴 {match['teamB'].mention}",
        inline=False
    )
    return e

# ==========================================================
# BOTONES
# ==========================================================
class MapButton(discord.ui.Button):
    def __init__(self, mapa, mode, match):
        super().__init__(
            label=mapa,
            style=discord.ButtonStyle.primary,
            disabled=mapa in match["banned"][mode]
        )
        self.mapa = mapa
        self.mode = mode
        self.match = match

    async def callback(self, interaction: discord.Interaction):
        if current_team(self.match) not in interaction.user.roles:
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)

        action, mode, _ = FORMATOS[self.match["formato"]][self.match["step"]]

        if action == "ban":
            self.match["banned"][mode].add(self.mapa)

        if action == "pick":
            self.match["picked"].append((mode, self.mapa))

        self.match["step"] += 1
        await advance(interaction)

class SideButton(discord.ui.Button):
    def __init__(self, side, match):
        super().__init__(label=side, style=discord.ButtonStyle.secondary)
        self.match = match

    async def callback(self, interaction: discord.Interaction):
        if current_team(self.match) not in interaction.user.roles:
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)

        self.match["step"] += 1
        await advance(interaction)

class MapView(discord.ui.View):
    def __init__(self, match, mode):
        super().__init__(timeout=None)
        for m in MAPAS[mode]:
            self.add_item(MapButton(m, mode, match))

class SideView(discord.ui.View):
    def __init__(self, match):
        super().__init__(timeout=None)
        for s in BANDOS:
            self.add_item(SideButton(s, match))

# ==========================================================
# FLUJO
# ==========================================================
async def advance(interaction: discord.Interaction):
    match = matches[interaction.channel.id]

    if match["step"] >= len(FORMATOS[match["formato"]]):
        return await interaction.response.edit_message(
            content="🎮 Pick & Ban finalizado",
            embed=None,
            view=None
        )

    action, mode, _ = FORMATOS[match["formato"]][match["step"]]

    if action in ["ban", "pick"]:
        await interaction.response.edit_message(
            embed=embed_turn(match),
            view=MapView(match, mode)
        )
    else:
        await interaction.response.edit_message(
            embed=embed_turn(match),
            view=SideView(match)
        )

# ==========================================================
# COMANDOS
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
        "step": 0,
        "banned": {
            "HP": set(),
            "SnD": set(),
            "Overload": set()
        },
        "picked": []
    }

    match = matches[ctx.channel.id]
    action, mode, _ = FORMATOS[formato][0]

    await ctx.send(
        embed=embed_turn(match),
        view=MapView(match, mode)
    )

# ==========================================================
# ARRANQUE
# ==========================================================
if __name__ == "__main__":
    threading.Thread(target=run_tcp_healthcheck, daemon=True).start()
    bot.run(os.getenv("DISCORD_TOKEN"))
