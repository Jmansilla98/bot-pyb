import discord
from discord.ext import commands
import os
import asyncio
import re
import socket
import threading

# ==========================================================
# TCP HEALTH CHECK (para Web Service con TCP)
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
# CONFIGURACIÓN DISCORD
# ==========================================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

ROL_ARBITRO = "Arbitro"

# ==========================================================
# MAPAS / MODOS
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

# ==========================================================
# ESTADO POR CANAL
# ==========================================================
pyb_channels = {}

def get_pyb(cid):
    return pyb_channels.get(cid)

def es_arbitro(user):
    return any(r.name == ROL_ARBITRO for r in user.roles)

def rol_actual(pyb):
    _, _, eq = FORMATOS[pyb["formato"]][pyb["paso"]]
    return pyb["equipo_a"] if eq == "A" else pyb["equipo_b"]

# ==========================================================
# EMBEDS
# ==========================================================
def embed_turno(pyb):
    accion, modo, _ = FORMATOS[pyb["formato"]][pyb["paso"]]
    e = discord.Embed(
        title=f"{accion.upper()} {modo}",
        description=f"Turno: {rol_actual(pyb).mention}"
    )
    e.add_field(
        name="Equipos",
        value=f"🔵 {pyb['equipo_a'].mention}\n🔴 {pyb['equipo_b'].mention}",
        inline=False
    )
    return e

# ==========================================================
# BOTONES MAPAS
# ==========================================================
class MapaButton(discord.ui.Button):
    def __init__(self, mapa, modo, pyb):
        super().__init__(
            label=mapa,
            style=discord.ButtonStyle.primary,
            disabled=mapa in pyb["usados"][modo]
        )
        self.mapa = mapa
        self.modo = modo
        self.pyb = pyb

    async def callback(self, interaction):
        if rol_actual(self.pyb) not in interaction.user.roles:
            return await interaction.response.send_message(
                "⛔ No es tu turno", ephemeral=True
            )

        accion, modo, _ = FORMATOS[self.pyb["formato"]][self.pyb["paso"]]

        # BAN o PICK SOLO AFECTA AL MODO ACTUAL
        self.pyb["usados"][modo].add(self.mapa)

        if accion == "pick":
            self.pyb["mapas_finales"].append((modo, self.mapa))

        self.pyb["paso"] += 1
        await avanzar_pyb(interaction)

class MapaView(discord.ui.View):
    def __init__(self, pyb, modo):
        super().__init__(timeout=None)
        for m in MAPAS[modo]:
            self.add_item(MapaButton(m, modo, pyb))

# ==========================================================
# BOTONES BANDOS
# ==========================================================
class BandoButton(discord.ui.Button):
    def __init__(self, bando, pyb):
        super().__init__(label=bando, style=discord.ButtonStyle.secondary)
        self.pyb = pyb

    async def callback(self, interaction):
        if rol_actual(self.pyb) not in interaction.user.roles:
            return await interaction.response.send_message(
                "⛔ No es tu turno", ephemeral=True
            )

        self.pyb["paso"] += 1
        await avanzar_pyb(interaction)

class BandoView(discord.ui.View):
    def __init__(self, pyb):
        super().__init__(timeout=None)
        for b in BANDOS:
            self.add_item(BandoButton(b, pyb))

# ==========================================================
# FLUJO PICK & BAN
# ==========================================================
async def avanzar_pyb(interaction):
    pyb = get_pyb(interaction.channel.id)

    if pyb["paso"] >= len(FORMATOS[pyb["formato"]]):
        return await interaction.response.edit_message(
            content="✅ Pick & Ban finalizado",
            embed=None,
            view=None
        )

    accion, modo, _ = FORMATOS[pyb["formato"]][pyb["paso"]]

    if accion in ["pick", "ban"]:
        view = MapaView(pyb, modo)
    else:
        view = BandoView(pyb)

    await interaction.response.edit_message(
        embed=embed_turno(pyb),
        view=view
    )

# ==========================================================
# COMANDOS
# ==========================================================
@bot.command()
async def setpartido(ctx, equipo_a: discord.Role, equipo_b: discord.Role, formato: str):
    formato = formato.lower()
    if formato not in FORMATOS:
        return await ctx.send("❌ Formato inválido (bo3 / bo5)")

    pyb_channels[ctx.channel.id] = {
        "equipo_a": equipo_a,
        "equipo_b": equipo_b,
        "formato": formato,
        "paso": 0,
        "usados": {
            "HP": set(),
            "SnD": set(),
            "Overload": set()
        },
        "mapas_finales": []
    }

    _, modo, _ = FORMATOS[formato][0]
    await ctx.send(
        embed=embed_turno(pyb_channels[ctx.channel.id]),
        view=MapaView(pyb_channels[ctx.channel.id], modo)
    )

# ==========================================================
# ARRANQUE
# ==========================================================
if __name__ == "__main__":
    threading.Thread(target=run_tcp_healthcheck, daemon=True).start()
    bot.run(os.getenv("DISCORD_TOKEN"))
