import discord
from discord.ext import commands
import os
import asyncio
import re
import requests

# ==========================================================
# CONFIGURACIÓN GENERAL
# ==========================================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

ROL_ARBITRO = "team1"

# ==========================================================
# MAPAS / BANDOS / COLORES
# ==========================================================
MAPAS = {
    "HP": ["Blackheart", "Colossus", "Den", "Exposure", "Scar"],
    "SnD": ["Colossus", "Den", "Exposure", "Raid", "Scar"],
    "Overload": ["Den", "Exposure", "Scar"]
}

BANDOS = ["Ataque", "Defensa"]

COLORES = {
    "HP": discord.Color.red(),
    "SnD": discord.Color.gold(),
    "Overload": discord.Color.purple()
}

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

def rol_actual(pyb):
    _, _, eq = FORMATOS[pyb["formato"]][pyb["paso"]]
    return pyb["equipo_a"] if eq == "A" else pyb["equipo_b"]

def es_arbitro(user):
    return ROL_ARBITRO in [r.name for r in user.roles]

def needed_wins(formato: str) -> int:
    return 2 if formato == "bo3" else 3

def serie_score(pyb):
    a_wins = sum(1 for r in pyb["resultados"] if r["winner"] == "A")
    b_wins = sum(1 for r in pyb["resultados"] if r["winner"] == "B")
    return a_wins, b_wins


def construir_mapas_finales(pyb):
    """
    Construye la lista final de mapas en orden fijo:
    BO3: HP, SnD, Overload
    BO5: HP, SnD, Overload, HP, SnD
    """
    resultado = []

    hp_picks = [m for m in pyb["mapas_finales"] if m[0] == "HP"]
    snd_picks = [m for m in pyb["mapas_finales"] if m[0] == "SnD"]

    restantes_overload = [
        m for m in MAPAS["Overload"]
        if m not in [x[1] for x in pyb["mapas_finales"] if x[0] == "Overload"]
    ]

    if pyb["formato"] == "bo3":
        resultado.append(hp_picks[0])
        resultado.append(snd_picks[0])
        resultado.append(("Overload", restantes_overload[0]))

    else:  # BO5
        resultado.append(hp_picks[0])
        resultado.append(snd_picks[0])
        resultado.append(("Overload", restantes_overload[0]))
        resultado.append(hp_picks[1])
        resultado.append(snd_picks[1])

    pyb["mapas_finales"] = resultado

# ==========================================================
# EMBEDS
# ==========================================================
def embed_turno(pyb):
    accion, modo, _ = FORMATOS[pyb["formato"]][pyb["paso"]]
    e = discord.Embed(title="🎮 PICK & BAN", color=COLORES[modo])
    e.add_field(name="Acción", value=accion.upper(), inline=True)
    e.add_field(name="Modo", value=modo, inline=True)
    e.add_field(name="Turno", value=rol_actual(pyb).mention, inline=True)
    e.add_field(
        name="Equipos",
        value=f"🔵 {pyb['equipo_a'].mention}\n🔴 {pyb['equipo_b'].mention}",
        inline=False
    )
    e.set_footer(text=f"Paso {pyb['paso']+1}/{len(FORMATOS[pyb['formato']])}")
    return e

def embed_resultado(pyb):
    i = len(pyb["resultados"])
    modo, mapa = pyb["mapas_finales"][i]
    e = discord.Embed(
        title=f"📝 Resultado Mapa {i+1}",
        description=f"**{modo} — {mapa}**",
        color=COLORES[modo]
    )
    e.add_field(name=pyb["equipo_a"].name, value="—", inline=True)
    e.add_field(name=pyb["equipo_b"].name, value="—", inline=True)
    e.set_footer(text="Solo el árbitro puede introducir resultados")
    return e

def embed_final(pyb):
    a_wins = sum(1 for r in pyb["resultados"] if r["winner"] == "A")
    b_wins = sum(1 for r in pyb["resultados"] if r["winner"] == "B")

    ganador = pyb["equipo_a"].name if a_wins > b_wins else pyb["equipo_b"].name
    scores_csv = f"{a_wins}-{b_wins}"

    pyb["challonge"]["scores_csv"] = scores_csv
    pyb["challonge"]["winner"] = "A" if a_wins > b_wins else "B"

    e = discord.Embed(
        title=f"🏆 {ganador} gana la serie {scores_csv}",
        color=discord.Color.green()
    )

    texto = ""
    for i, r in enumerate(pyb["resultados"]):
        texto += (
            f"**Mapa {i+1} — {r['modo']} ({r['mapa']})**\n"
            f"{pyb['equipo_a'].name} **{r['a']}** — **{r['b']}** {pyb['equipo_b'].name}\n\n"
        )

    e.add_field(name="Resultados por mapa", value=texto, inline=False)
    e.set_footer(text="Listo para subir a Challonge")
    return e

# ==========================================================
# BOTONES MAPAS
# ==========================================================
class MapaButton(discord.ui.Button):
    def __init__(self, mapa, pyb, modo):
        super().__init__(
            label=mapa,
            style=discord.ButtonStyle.primary,
            disabled=mapa in pyb["usados"][modo]
        )
        self.mapa = mapa
        self.pyb = pyb
        self.modo = modo

    async def callback(self, interaction):
        if rol_actual(self.pyb) not in interaction.user.roles:
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)

        accion, modo, _ = FORMATOS[self.pyb["formato"]][self.pyb["paso"]]
        self.pyb["usados"][modo].add(self.mapa)

        if accion == "pick":
            self.pyb["mapas_finales"].append((modo, self.mapa))

        self.pyb["paso"] += 1
        await avanzar_pyb(interaction)

class MapaView(discord.ui.View):
    def __init__(self, pyb, modo):
        super().__init__(timeout=None)
        for m in MAPAS[modo]:
            self.add_item(MapaButton(m, pyb, modo))

# ==========================================================
# BOTONES BANDOS
# ==========================================================
class BandoButton(discord.ui.Button):
    def __init__(self, bando, pyb):
        super().__init__(label=bando, style=discord.ButtonStyle.secondary)
        self.pyb = pyb

    async def callback(self, interaction):
        if rol_actual(self.pyb) not in interaction.user.roles:
            return await interaction.response.send_message("⛔ No es tu turno", ephemeral=True)
        self.pyb["paso"] += 1
        await avanzar_pyb(interaction)

class BandoView(discord.ui.View):
    def __init__(self, pyb):
        super().__init__(timeout=None)
        for b in BANDOS:
            self.add_item(BandoButton(b, pyb))

# ==========================================================
# RESULTADOS (SOLO ÁRBITRO)
# ==========================================================
class ResultadoModal(discord.ui.Modal, title="Introducir resultado"):
    res_a = discord.ui.TextInput(label="Resultado Equipo A", max_length=4)
    res_b = discord.ui.TextInput(label="Resultado Equipo B", max_length=4)

    def __init__(self, pyb):
        super().__init__()
        self.pyb = pyb

    async def on_submit(self, interaction):
        if not es_arbitro(interaction.user):
            return await interaction.response.send_message(
                "⛔ Solo árbitros",
                ephemeral=True
            )

        # Validación numérica básica
        if not re.fullmatch(r"\d+", self.res_a.value) or not re.fullmatch(r"\d+", self.res_b.value):
            return await interaction.response.send_message(
                "❌ Los resultados deben ser números enteros",
                ephemeral=True
            )

        a = int(self.res_a.value)
        b = int(self.res_b.value)

        # No permitir empate
        if a == b:
            return await interaction.response.send_message(
                "❌ No puede haber empate",
                ephemeral=True
            )

        # Detectar modo y mapa actual
        idx = len(self.pyb["resultados"])
        modo, mapa = self.pyb["mapas_finales"][idx]

        # =========================
        # VALIDACIÓN POR MODO
        # =========================

        if modo == "HP":
            # HP: 0–250 (250 es solo el máximo, no obligatorio)
            if not (0 <= a <= 250 and 0 <= b <= 250):
                return await interaction.response.send_message(
                    "❌ En HP los valores deben estar entre 0 y 250",
                    ephemeral=True
                )
            if a == 250 and b == 250:
                return await interaction.response.send_message(
                    "❌ En HP solo un equipo puede llegar a 250",
                    ephemeral=True
                )

        elif modo == "SnD":
            # SnD: 0–6
            if not (0 <= a <= 6 and 0 <= b <= 6):
                return await interaction.response.send_message(
                    "❌ En SnD los valores deben estar entre 0 y 6",
                    ephemeral=True
                )

        elif modo == "Overload":
            # Overload: número positivo sin límite superior
            if a < 0 or b < 0:
                return await interaction.response.send_message(
                    "❌ En Overload los valores deben ser números positivos",
                    ephemeral=True
                )

        # =========================
        # GUARDAR RESULTADO
        # =========================

        winner = "A" if a > b else "B"

        self.pyb["resultados"].append({
            "modo": modo,
            "mapa": mapa,
            "a": a,
            "b": b,
            "winner": winner
        })

        await avanzar_resultados(interaction)

class ResultadoButton(discord.ui.Button):
    def __init__(self, pyb):
        super().__init__(label="Introducir resultado", style=discord.ButtonStyle.success)
        self.pyb = pyb

    async def callback(self, interaction):
        if not es_arbitro(interaction.user):
            return await interaction.response.send_message("⛔ Solo árbitros", ephemeral=True)
        await interaction.response.send_modal(ResultadoModal(self.pyb))

class ResultadoView(discord.ui.View):
    def __init__(self, pyb):
        super().__init__(timeout=None)
        self.add_item(ResultadoButton(pyb))

# ==========================================================
# BOTÓN Y VIEW DE RECLAMACIÓN
# ==========================================================
class ReclamacionButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="🚨 Reclamación",
            style=discord.ButtonStyle.danger
        )
        self.usado = False

    async def callback(self, interaction: discord.Interaction):
        if self.usado:
            return await interaction.response.send_message(
                "❌ Ya hay una reclamación activa.",
                ephemeral=True
            )

        self.usado = True

        embed = discord.Embed(
            title="🚨 Reclamación registrada",
            description=f"El equipo **{interaction.user.display_name}** ha solicitado una reclamación.",
            color=discord.Color.red()
        )

        await interaction.response.send_message(embed=embed)

        # Mostrar acciones para el árbitro
        await interaction.channel.send(
            content="⚖️ **Acciones del árbitro**",
            view=ArbitroAccionesView(get_pyb(interaction.channel.id))
        )

class ReclamacionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=900)  # 15 minutos
        self.add_item(ReclamacionButton())

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

# ==========================================================
# NUEVA VIEW DE ACCIONES DEL ÁRBITRO
# ==========================================================
class ArbitroAccionesView(discord.ui.View):
    def __init__(self, pyb):
        super().__init__(timeout=None)
        self.pyb = pyb
        self.add_item(SubirChallongeButton(pyb))
        self.add_item(EditarResultadosButton(pyb))


class SubirChallongeButton(discord.ui.Button):
    def __init__(self, pyb):
        super().__init__(
            label="⬆️ Subir a Challonge",
            style=discord.ButtonStyle.success
        )
        self.pyb = pyb

    async def callback(self, interaction: discord.Interaction):
        if not es_arbitro(interaction.user):
            return await interaction.response.send_message(
                "⛔ Solo el árbitro puede subir a Challonge",
                ephemeral=True
            )

        scores = self.pyb["challonge"].get("scores_csv", "N/D")

        await interaction.response.send_message(
            f"✅ Resultado preparado para Challonge\n"
            f"**Score:** `{scores}`\n\n"
            f"(Aquí iría la llamada real a la API de Challonge)",
            ephemeral=True
        )


class EditarResultadosButton(discord.ui.Button):
    def __init__(self, pyb):
        super().__init__(
            label="✏️ Editar resultados",
            style=discord.ButtonStyle.secondary
        )
        self.pyb = pyb

    async def callback(self, interaction: discord.Interaction):
        if not es_arbitro(interaction.user):
            return await interaction.response.send_message(
                "⛔ Solo el árbitro puede editar resultados",
                ephemeral=True
            )

        # Resetear resultados para reintroducirlos
        self.pyb["resultados"].clear()

        await interaction.response.send_message(
            "✏️ Resultados reseteados. Puedes volver a introducirlos.",
            ephemeral=True
        )

        # Volver al primer mapa de resultados
        await interaction.message.edit(
            embed=embed_resultado(self.pyb),
            view=ResultadoView(self.pyb)
        )

# ==========================================================
# FLUJOS
# ==========================================================
async def avanzar_pyb(interaction):
    pyb = get_pyb(interaction.channel.id)

    if pyb["paso"] >= len(FORMATOS[pyb["formato"]]):
        # Construir la lista FINAL de mapas en orden correcto
        construir_mapas_finales(pyb)

        return await interaction.response.edit_message(embed=embed_resultado(pyb), view=ResultadoView(pyb))

    accion, modo, _ = FORMATOS[pyb["formato"]][pyb["paso"]]
    view = MapaView(pyb, modo) if accion in ["pick", "ban"] else BandoView(pyb)
    await interaction.response.edit_message(embed=embed_turno(pyb), view=view)

async def avanzar_resultados(interaction):
    pyb = get_pyb(interaction.channel.id)

    a_wins, b_wins = serie_score(pyb)
    target = needed_wins(pyb["formato"])

    # Si alguien ya ha ganado la serie (2 en BO3 / 3 en BO5), cerrar
    if a_wins >= target or b_wins >= target:
        return await interaction.response.edit_message(
            embed=embed_final(pyb),
            view=ReclamacionView()
        )

    # Si no hay ganador todavía, pedir siguiente mapa
    if len(pyb["resultados"]) < len(pyb["mapas_finales"]):
        return await interaction.response.edit_message(
            embed=embed_resultado(pyb),
            view=ResultadoView(pyb)
        )

    # Fallback de seguridad
    await interaction.response.edit_message(
        embed=embed_final(pyb),
        view=ReclamacionView()
    )

# ==========================================================
# COMANDOS
# ==========================================================
@bot.command()
async def setequipos(ctx, equipo_a: discord.Role, equipo_b: discord.Role):
    pyb_channels[ctx.channel.id] = {
        "equipo_a": equipo_a,
        "equipo_b": equipo_b,
        "formato": None,
        "paso": 0,
        "usados": {"HP": set(), "SnD": set(), "Overload": set()},
        "mapas_finales": [],
        "resultados": [],
        "challonge": {}
    }
    await ctx.send("✅ Equipos definidos")

@bot.command()
async def startpyb(ctx, formato: str):
    pyb = get_pyb(ctx.channel.id)
    if not pyb:
        return await ctx.send("❌ Usa !setequipos primero")

    formato = formato.lower()
    if formato not in FORMATOS:
        return await ctx.send("❌ Formato inválido")

    pyb.update({
        "formato": formato,
        "paso": 0,
        "usados": {"HP": set(), "SnD": set(), "Overload": set()},
        "mapas_finales": [],
        "resultados": [],
        "challonge": {}
    })

    _, modo, _ = FORMATOS[formato][0]
    await ctx.send(embed=embed_turno(pyb), view=MapaView(pyb, modo))

# ==========================================================
# RUN
# ==========================================================
# ⚠️ AQUÍ ES LO ÚNICO QUE TOCAS
# En Render o local:
# DISCORD_TOKEN = tu token nuevo

bot.run(os.getenv("DISCORD_TOKEN"))
