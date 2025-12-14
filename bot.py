import discord
from discord.ext import commands
import os
import asyncio
import socket
import threading

# ==========================================================
# TCP HEALTH CHECK (Koyeb / Web Service)
# ==========================================================
def run_tcp_healthcheck():
    host = "0.0.0.0"
    port = int(os.getenv("PORT", 8000))
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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
# FORMATO PYB
# ==========================================================
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

def is_ref(user):
    return any(r.name == ROL_ARBITRO for r in user.roles)

def turno_actual(match):
    _, _, eq = FORMATOS[match["formato"]][match["step"]]
    return match["team_a"] if eq == "A" else match["team_b"]

# ==========================================================
# UI BUTTONS
# ==========================================================
class MapButton(discord.ui.Button):
    def __init__(self, mapa, modo, match):
        super().__init__(label=mapa, style=discord.ButtonStyle.primary)
        self.mapa = mapa
        self.modo = modo
        self.match = match

    async def callback(self, interaction):
        if turno_actual(self.match) not in interaction.user.roles:
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)

        accion, modo, _ = FORMATOS[self.match["formato"]][self.match["step"]]
        self.match["used"][modo].add(self.mapa)

        if accion == "pick":
            self.match["final_maps"].append((modo, self.mapa))

        self.match["step"] += 1
        await avanzar(interaction)

class MapView(discord.ui.View):
    def __init__(self, match, modo):
        super().__init__(timeout=None)
        for m in MAPAS[modo]:
            self.add_item(MapButton(m, modo, match))

class SideButton(discord.ui.Button):
    def __init__(self, side, match):
        super().__init__(label=side, style=discord.ButtonStyle.secondary)
        self.match = match

    async def callback(self, interaction):
        if turno_actual(self.match) not in interaction.user.roles:
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)
        self.match["step"] += 1
        await avanzar(interaction)

class SideView(discord.ui.View):
    def __init__(self, match):
        super().__init__(timeout=None)
        for s in BANDOS:
            self.add_item(SideButton(s, match))

# ==========================================================
# EMBEDS
# ==========================================================
def embed_turn(match):
    accion, modo, _ = FORMATOS[match["formato"]][match["step"]]
    e = discord.Embed(title=f"{accion.upper()} {modo}")
    e.add_field(name="Turno", value=turno_actual(match).mention)
    e.add_field(
        name="Equipos",
        value=f"{match['team_a'].mention} vs {match['team_b'].mention}",
        inline=False
    )
    return e

# ==========================================================
# FLOW
# ==========================================================
async def avanzar(interaction):
    match = matches[interaction.channel.id]

    if match["step"] >= len(FORMATOS[match["formato"]]):
        return await interaction.response.edit_message(
            content="✅ Pick & Ban finalizado",
            embed=None,
            view=None
        )

    accion, modo, _ = FORMATOS[match["formato"]][match["step"]]
    view = MapView(match, modo) if accion in ["pick", "ban"] else SideView(match)
    await interaction.response.edit_message(embed=embed_turn(match), view=view)

# ==========================================================
# COMMANDS
# ==========================================================
@bot.command()
async def setpartido(ctx, team_a: discord.Role, team_b: discord.Role, formato: str):
    formato = formato.lower()
    if formato not in FORMATOS:
        return await ctx.send("❌ Formato inválido")

    matches[ctx.channel.id] = {
        "team_a": team_a,
        "team_b": team_b,
        "formato": formato,
        "step": 0,
        "used": {"HP": set(), "SnD": set(), "Overload": set()},
        "final_maps": []
    }

    accion, modo, _ = FORMATOS[formato][0]
    await ctx.send(
        embed=embed_turn(matches[ctx.channel.id]),
        view=MapView(matches[ctx.channel.id], modo)
    )

# ==========================================================
# START
# ==========================================================
if __name__ == "__main__":
    threading.Thread(target=run_tcp_healthcheck, daemon=True).start()
    bot.run(os.getenv("DISCORD_TOKEN"))
