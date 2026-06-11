import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
from datetime import datetime, timedelta
import asyncio
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

load_dotenv()

# ─── Configuration ───────────────────────────────────────────────────────────

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN manquant — remplis le fichier .env")

CONFIG_FILE = "reminders.json"

DAYS_MAP = {
    "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3,
    "vendredi": 4, "samedi": 5, "dimanche": 6
}
DAYS_FR = {v: k for k, v in DAYS_MAP.items()}

# ─── Bot setup ────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ─── Persistence ──────────────────────────────────────────────────────────────

def load_reminders() -> list[dict]:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            print("⚠️ reminders.json corrompu, reset.")
            os.remove(CONFIG_FILE)
    return []

def save_reminders(reminders: list[dict]):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(reminders, f, indent=2, ensure_ascii=False)

# ─── Background task ──────────────────────────────────────────────────────────

@tasks.loop(minutes=1)
async def check_reminders():
    now = datetime.now(ZoneInfo("Europe/Paris"))
    reminders = load_reminders()
    modified = False

    for reminder in reminders:
        if not reminder.get("active", True):
            continue

        target_weekday = reminder["weekday"]
        target_hour = reminder["event_hour"]
        target_minute = reminder["event_minute"]
        notify_before = reminder["notify_before_minutes"]

        # Vérifie jour semaine
        if now.weekday() != target_weekday:
            continue

        # Construction heure événement (heure française)
        today = now.date()

        event_dt = datetime(
            today.year,
            today.month,
            today.day,
            target_hour,
            target_minute,
            tzinfo=ZoneInfo("Europe/Paris")
        )

        send_dt = event_dt - timedelta(minutes=notify_before)

        # Vérifie heure d'envoi
        if now.hour != send_dt.hour or now.minute != send_dt.minute:
            continue

        # Anti double envoi
        last_sent = reminder.get("last_sent")
        current_key = now.strftime("%Y-%W-%u")

        if last_sent == current_key:
            continue

        channel = bot.get_channel(reminder["channel_id"])
        if channel is None:
            print(f"⚠️ Channel {reminder['channel_id']} introuvable.")
            continue

        event_time_str = f"{target_hour:02d}h{target_minute:02d}"

        message = reminder.get(
            "message",
            f"@everyone 📅 Rappel : réunion à **{event_time_str}** !"
        )

        await channel.send(message)
        print(f"✅ Rappel envoyé dans #{channel.name}")

        reminder["last_sent"] = current_key
        modified = True

    if modified:
        save_reminders(reminders)

@check_reminders.before_loop
async def before_check():
    await bot.wait_until_ready()

# ─── Slash commands ───────────────────────────────────────────────────────────

@tree.command(name="reminder_add", description="Ajouter un rappel hebdomadaire")
@app_commands.describe(
    channel="Le salon où envoyer le rappel",
    jour="Jour de la semaine",
    heure_evenement="Heure (0-23)",
    minute_evenement="Minute (0-59)",
    rappel_avant="Minutes avant l'événement",
    message="Message personnalisé"
)
@app_commands.choices(jour=[
    app_commands.Choice(name=day.capitalize(), value=day)
    for day in DAYS_MAP
])
async def reminder_add(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    jour: str,
    heure_evenement: int,
    minute_evenement: int,
    rappel_avant: int,
    message: str = ""
):

    if not (0 <= heure_evenement <= 23):
        return await interaction.response.send_message("❌ Heure invalide.", ephemeral=True)

    if not (0 <= minute_evenement <= 59):
        return await interaction.response.send_message("❌ Minute invalide.", ephemeral=True)

    if rappel_avant < 1:
        return await interaction.response.send_message("❌ Rappel trop court.", ephemeral=True)

    weekday = DAYS_MAP[jour]
    event_time = f"{heure_evenement:02d}h{minute_evenement:02d}"

    if not message:
        message = f"@everyone 📅 Rappel : réunion à **{event_time}** !"

    reminders = load_reminders()
    new_id = max((r["id"] for r in reminders), default=0) + 1

    reminders.append({
        "id": new_id,
        "channel_id": channel.id,
        "weekday": weekday,
        "event_hour": heure_evenement,
        "event_minute": minute_evenement,
        "notify_before_minutes": rappel_avant,
        "message": message,
        "active": True,
        "last_sent": None
    })

    save_reminders(reminders)

    embed = discord.Embed(title="✅ Rappel créé", color=0x5865F2)
    embed.add_field(name="Salon", value=channel.mention, inline=True)
    embed.add_field(name="Jour", value=jour.capitalize(), inline=True)
    embed.add_field(name="Événement", value=event_time, inline=True)

    await interaction.response.send_message(embed=embed)

# ─── Events ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Connecté en tant que {bot.user}")
    try:
        synced = await tree.sync()
        print(f"⚡ {len(synced)} commandes slash synchronisées")
    except Exception as e:
        print(f"⚠️ Erreur sync: {e}")

    check_reminders.start()

# ─── Run ──────────────────────────────────────────────────────────────────────

bot.run(TOKEN)