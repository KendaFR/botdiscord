import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
from datetime import datetime, timedelta
import asyncio
from dotenv import load_dotenv

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
            print("⚠️  reminders.json corrompu, réinitialisation.")
            os.remove(CONFIG_FILE)
    return []

def save_reminders(reminders: list[dict]):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(reminders, f, indent=2, ensure_ascii=False)

# ─── Background task ──────────────────────────────────────────────────────────

@tasks.loop(minutes=1)
async def check_reminders():
    now = datetime.now()
    reminders = load_reminders()
    modified = False

    for reminder in reminders:
        if not reminder.get("active", True):
            continue

        target_weekday = reminder["weekday"]       # 0=lun … 6=dim
        target_hour    = reminder["event_hour"]
        target_minute  = reminder["event_minute"]
        notify_before  = reminder["notify_before_minutes"]

        # Calcule l'heure d'envoi = heure de l'événement - rappel
        event_dt = now.replace(
            hour=target_hour, minute=target_minute, second=0, microsecond=0
        )
        send_dt = event_dt - timedelta(minutes=notify_before)

        # Est-on au bon jour et à la bonne minute ?
        if now.weekday() != target_weekday:
            continue
        if now.hour != send_dt.hour or now.minute != send_dt.minute:
            continue

        # Évite le double envoi dans la même minute
        last_sent = reminder.get("last_sent")
        current_key = now.strftime("%Y-%W-%u")  # année-semaine-jour
        if last_sent == current_key:
            continue

        # Envoi
        channel = bot.get_channel(reminder["channel_id"])
        if channel is None:
            print(f"⚠️  Channel {reminder['channel_id']} introuvable.")
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
    jour="Jour de la semaine (ex: dimanche)",
    heure_evenement="Heure de l'événement (0-23)",
    minute_evenement="Minute de l'événement (0-59)",
    rappel_avant="Combien de minutes avant l'événement envoyer le rappel",
    message="Message personnalisé (laisser vide pour le message par défaut)"
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
        return await interaction.response.send_message("❌ Heure invalide (0-23).", ephemeral=True)
    if not (0 <= minute_evenement <= 59):
        return await interaction.response.send_message("❌ Minute invalide (0-59).", ephemeral=True)
    if rappel_avant < 1:
        return await interaction.response.send_message("❌ Le rappel doit être ≥ 1 minute avant.", ephemeral=True)

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

    send_dt = datetime(2000, 1, 1, heure_evenement, minute_evenement) - timedelta(minutes=rappel_avant)

    embed = discord.Embed(title="✅ Rappel créé", color=0x5865F2)
    embed.add_field(name="Salon", value=channel.mention, inline=True)
    embed.add_field(name="Jour", value=jour.capitalize(), inline=True)
    embed.add_field(name="Événement à", value=event_time, inline=True)
    embed.add_field(name="Envoi à", value=f"{send_dt.hour:02d}h{send_dt.minute:02d}", inline=True)
    embed.add_field(name="Rappel avant", value=f"{rappel_avant} min", inline=True)
    embed.add_field(name="ID", value=f"`#{new_id}`", inline=True)
    embed.add_field(name="Message", value=message, inline=False)
    await interaction.response.send_message(embed=embed)


@tree.command(name="reminder_list", description="Lister tous les rappels actifs")
async def reminder_list(interaction: discord.Interaction):
    reminders = load_reminders()
    if not reminders:
        return await interaction.response.send_message("📭 Aucun rappel configuré.", ephemeral=True)

    embed = discord.Embed(title="📋 Rappels programmés", color=0x5865F2)
    for r in reminders:
        channel = bot.get_channel(r["channel_id"])
        ch_name = channel.mention if channel else f"<#{r['channel_id']}>"
        event_time = f"{r['event_hour']:02d}h{r['event_minute']:02d}"
        send_dt = (
            datetime(2000, 1, 1, r["event_hour"], r["event_minute"])
            - timedelta(minutes=r["notify_before_minutes"])
        )
        status = "🟢" if r.get("active", True) else "🔴"
        embed.add_field(
            name=f"{status} Rappel #{r['id']} — {DAYS_FR[r['weekday']].capitalize()}",
            value=(
                f"**Salon :** {ch_name}\n"
                f"**Événement :** {event_time} | **Envoi :** {send_dt.hour:02d}h{send_dt.minute:02d} "
                f"({r['notify_before_minutes']} min avant)\n"
                f"**Message :** {r['message'][:80]}{'…' if len(r['message']) > 80 else ''}"
            ),
            inline=False
        )
    await interaction.response.send_message(embed=embed)


@tree.command(name="reminder_delete", description="Supprimer un rappel par son ID")
@app_commands.describe(id="L'ID du rappel à supprimer (voir /reminder_list)")
async def reminder_delete(interaction: discord.Interaction, id: int):
    reminders = load_reminders()
    new_reminders = [r for r in reminders if r["id"] != id]
    if len(new_reminders) == len(reminders):
        return await interaction.response.send_message(f"❌ Rappel `#{id}` introuvable.", ephemeral=True)
    save_reminders(new_reminders)
    await interaction.response.send_message(f"🗑️ Rappel `#{id}` supprimé.")


@tree.command(name="reminder_toggle", description="Activer / désactiver un rappel")
@app_commands.describe(id="L'ID du rappel")
async def reminder_toggle(interaction: discord.Interaction, id: int):
    reminders = load_reminders()
    for r in reminders:
        if r["id"] == id:
            r["active"] = not r.get("active", True)
            save_reminders(reminders)
            state = "activé 🟢" if r["active"] else "désactivé 🔴"
            return await interaction.response.send_message(f"Rappel `#{id}` {state}.")
    await interaction.response.send_message(f"❌ Rappel `#{id}` introuvable.", ephemeral=True)


@tree.command(name="reminder_test", description="Tester l'envoi immédiat d'un rappel")
@app_commands.describe(id="L'ID du rappel à tester")
async def reminder_test(interaction: discord.Interaction, id: int):
    reminders = load_reminders()
    for r in reminders:
        if r["id"] == id:
            channel = bot.get_channel(r["channel_id"])
            if channel is None:
                return await interaction.response.send_message("❌ Salon introuvable.", ephemeral=True)
            await channel.send(r["message"])
            return await interaction.response.send_message(f"✅ Test envoyé dans {channel.mention}.")
    await interaction.response.send_message(f"❌ Rappel `#{id}` introuvable.", ephemeral=True)

# ─── Events ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Connecté en tant que {bot.user} ({bot.user.id})")
    try:
        synced = await tree.sync()
        print(f"⚡ {len(synced)} commande(s) slash synchronisée(s).")
    except Exception as e:
        print(f"⚠️  Erreur sync slash: {e}")
    check_reminders.start()

# ─── Run ──────────────────────────────────────────────────────────────────────

bot.run(TOKEN)