
import discord
from discord.ext import commands
import sqlite3
import random
import asyncio
import os
import time
import io
from collections import defaultdict, deque
from dotenv import load_dotenv

load_dotenv()

# ==================================================
#                  CONFIGURATION
# ==================================================

TOKEN = os.getenv("BOT_TOKEN")

WELCOME_CHANNEL_ID = 1568014602363601046
TICKET_CATEGORY_ID = 1568023910904103102
STAFF_ROLE_ID = 123456789012345678
APPLICATION_CHANNEL_ID = 1573401502800941107
TICKET_LOG_CHANNEL_ID = 123456789012345678

WELCOME_IMAGE_PATH = "welcome.png"
QUESTIONS = [
    "كم عمرك وشنو اسمك؟",
    " شنو اسمك داخل Minecraft؟",
    "تلعب من هاتف ولا PC؟",
    "تلعب Java ولا Bedrock ولا النسخة الأصلية؟",
    " من متى وأنت تلعب Minecraft؟",
    " سبق ودخلت كلان قبل؟",
    "اعطينا اسم كلان كنت فيه سابقًا.",
    "شنو أكثر شيء أنت شاطر فيه باللعبة؟",
    "أنت يوتيوبر ؟",
    "اسم قناتك(Handle)",
    " عندك خبرة في المونتاج؟",
    "شنو تطبيق المونتاج اللي تستخدمه؟",
    "ليش بدك تدخل Blacklist؟",
    " تقدر تلتزم بقوانين الكلان وتكون نشيط؟"
]

# ==================================================
#                    BOT SETUP
# ==================================================

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(
    command_prefix="--",
    intents=intents
)

db = sqlite3.connect("bot.db")
db.execute("PRAGMA journal_mode=WAL")

cursor = db.cursor()

def setup_database():
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='levels'
    """)
    if cursor.fetchone():
        cursor.execute("PRAGMA table_info(levels)")
        columns = [row[1] for row in cursor.fetchall()]

        # الجدول القديم لا يحتوي على guild_id؛ نحتفظ به كنسخة احتياطية
        if "guild_id" not in columns:
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table'
            """)
            existing = {row[0] for row in cursor.fetchall()}
            backup = "levels_legacy"
            index = 1
            while backup in existing:
                index += 1
                backup = f"levels_legacy_{index}"
            cursor.execute(f"ALTER TABLE levels RENAME TO {backup}")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS levels (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
    db.commit()


setup_database()

# ==================================================
#                    ANTI SPAM
# ==================================================

message_history = defaultdict(deque)
spam_until = {}
last_warning = {}

SPAM_LIMIT = 5
SPAM_SECONDS = 7
SPAM_BLOCK_SECONDS = 20
REPEAT_LIMIT = 3
REPEAT_SECONDS = 15


def check_spam(message):

    now = time.monotonic()

    key = (message.guild.id, message.author.id)

    history = message_history[key]

    while history and now - history[0][0] > REPEAT_SECONDS:
        history.popleft()

    content = message.content.strip().lower()

    history.append((now, content))

    recent = [
        item for item in history
        if now - item[0] <= SPAM_SECONDS
    ]

    repeated = sum(
        1 for item in history
        if item[1] == content
    )

    fast_spam = len(recent) >= SPAM_LIMIT

    repeat_spam = (
        bool(content)
        and repeated >= REPEAT_LIMIT
    )

    return fast_spam or repeat_spam


# ==================================================
#                     LEVEL SYSTEM
# ==================================================

LEVEL_UP_CHANNEL_ID = 1568026216332787874

LEVEL_ROLES = {
    1: 1567983672127131708,
    2: 1567983907603751123,
    3: 1567983966315876463,
    4: 1567984032615243826,
    5: 1567984123828641823,
    6: 1567984195635118102,
    7: 1567984260466606346,
    8: 1567984319702765598,
    9: 1567984388770234479,
    10: 1567984447599415346
}


def get_xp_needed(level):
    return 100 + (level * 50)


def get_member_level(guild_id, user_id):
    cursor.execute("""
        SELECT xp, level FROM levels
        WHERE guild_id=? AND user_id=?
    """, (guild_id, user_id))

    data = cursor.fetchone()

    if data is not None:
        return data

    # ترحيل بيانات العضو من الجدول القديم إن وُجد
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name LIKE 'levels_legacy%'
    """)

    legacy_tables = [row[0] for row in cursor.fetchall()]
    old_xp, old_level = 0, 0

    for table_name in legacy_tables:
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = [row[1] for row in cursor.fetchall()]

        if not {"user_id", "xp", "level"}.issubset(columns):
            continue

        cursor.execute(
            f"SELECT xp, level FROM {table_name} WHERE user_id=? LIMIT 1",
            (user_id,)
        )

        old_data = cursor.fetchone()

        if old_data is not None:
            old_xp = old_data[0] if old_data[0] is not None else 0
            old_level = old_data[1] if old_data[1] is not None else 0
            break

    cursor.execute("""
        INSERT OR IGNORE INTO levels (guild_id, user_id, xp, level)
        VALUES (?, ?, ?, ?)
    """, (guild_id, user_id, old_xp, old_level))

    db.commit()

    return old_xp, old_level


# ==================================================
#                 LEVEL ROLE SYSTEM
# ==================================================

async def sync_level_role(member, level):
    # أعلى رتبة متاحة هي Level 10
    target_level = min(max(level, 1), 10)

    target_role_id = LEVEL_ROLES.get(target_level)

    if target_role_id is None:
        return

    target_role = member.guild.get_role(target_role_id)

    if target_role is None:
        print(f"[LEVEL SYSTEM] Role {target_role_id} not found.")
        return

    # تحديد رتب المستويات فقط
    level_role_ids = set(LEVEL_ROLES.values())

    # إزالة رتب المستويات القديمة فقط
    # مع الحفاظ على جميع الرتب الأخرى
    old_level_roles = [
        role for role in member.roles
        if role.id in level_role_ids
        and role.id != target_role_id
    ]

    try:
        if old_level_roles:
            await member.remove_roles(
                *old_level_roles,
                reason=f"Level role update to Level {target_level}"
            )

        # إضافة رتبة المستوى الجديد إذا لم تكن موجودة
        if target_role not in member.roles:
            await member.add_roles(
                target_role,
                reason=f"Reached Level {target_level}"
            )

    except discord.Forbidden:
        print(
            f"[LEVEL SYSTEM] Missing permissions to update roles for "
            f"{member}."
        )

    except discord.HTTPException as error:
        print(f"[LEVEL SYSTEM] Discord API Error: {error}")


# ==================================================
#                  LEVEL UP MESSAGE
# ==================================================

async def send_level_up_message(member, level):
    channel = member.guild.get_channel(LEVEL_UP_CHANNEL_ID)

    if channel is None:
        try:
            channel = await bot.fetch_channel(LEVEL_UP_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            print("[LEVEL SYSTEM] Level Up channel not found or inaccessible.")
            return

    embed = discord.Embed(
        title="🎉 Level Up!",
        description=(
            f"مبروك {member.mention}!\n"
            f"لقد وصلت إلى المستوى **{level}** 🏆"
        ),
        color=discord.Color.gold()
    )

    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"{member.guild.name} • Level System")

    try:
        await channel.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException) as error:
        print(f"[LEVEL SYSTEM] Could not send Level Up message: {error}")


# ==================================================
#                       ADD XP
# ==================================================

async def add_xp(message):
    if message.guild is None or message.author.bot:
        return

    guild_id = message.guild.id
    user_id = message.author.id

    xp, old_level = get_member_level(guild_id, user_id)

    xp += random.randint(10, 20)

    level = old_level
    leveled_up = False

    while xp >= get_xp_needed(level):
        xp -= get_xp_needed(level)
        level += 1
        leveled_up = True

    cursor.execute("""
        UPDATE levels SET xp=?, level=?
        WHERE guild_id=? AND user_id=?
    """, (xp, level, guild_id, user_id))

    db.commit()

    # تنفيذ الترقية عند زيادة المستوى فقط
    if leveled_up:
        await sync_level_role(message.author, level)
        await send_level_up_message(message.author, level)


# ==================================================
#                       RANK
# ==================================================

@bot.command()
async def rank(ctx):
    if ctx.guild is None:
        return await ctx.send("❌ هذا الأمر يعمل داخل السيرفر فقط.")

    xp, level = get_member_level(ctx.guild.id, ctx.author.id)

    embed = discord.Embed(
        title="🏆 Rank",
        description=(
            f"العضو: {ctx.author.mention}\n"
            f"المستوى: **{level}**\n"
            f"XP: **{xp}/{get_xp_needed(level)}**"
        ),
        color=discord.Color.gold()
    )

    await ctx.send(embed=embed)


# ==================================================
#                        TOP
# ==================================================

@bot.command()
async def top(ctx):
    if ctx.guild is None:
        return await ctx.send("❌ هذا الأمر يعمل داخل السيرفر فقط.")

    cursor.execute("""
        SELECT user_id, level, xp FROM levels
        WHERE guild_id=?
        ORDER BY level DESC, xp DESC
        LIMIT 10
    """, (ctx.guild.id,))

    rows = cursor.fetchall()

    if not rows:
        return await ctx.send("لا توجد بيانات حتى الآن.")

    description = ""

    for number, (user_id, level, xp) in enumerate(rows, 1):
        member = ctx.guild.get_member(user_id)

        name = member.display_name if member else f"User {user_id}"

        description += (
            f"**{number}.** {name} — Level {level} | XP {xp}\n"
        )

    embed = discord.Embed(
        title="🏆 Top 10",
        description=description,
        color=discord.Color.gold()
    )

    await ctx.send(embed=embed)

# ==================================================
#                     WELCOME
# ==================================================

@bot.event
async def on_member_join(member):

    channel = bot.get_channel(WELCOME_CHANNEL_ID)

    if channel is None:
        return

    embed = discord.Embed(
        title="🎉 أهلاً وسهلاً بك!",
        description=(
            f"مرحباً {member.mention} ❤️\n\n"
            f"أنت العضو رقم **{member.guild.member_count}**\n"
            "نتمنى لك وقتاً ممتعاً معنا!"
        ),
        color=discord.Color.blurple()
    )

    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=member.guild.name)

    if os.path.exists(WELCOME_IMAGE_PATH):
        file = discord.File(
            WELCOME_IMAGE_PATH,
            filename="welcome.png"
        )
        embed.set_image(url="attachment://welcome.png")
        await channel.send(embed=embed, file=file)
    else:
        await channel.send(embed=embed)


# ==================================================
#                    TICKET PANEL
# ==================================================

TICKET_PANEL_IMAGE_PATH = "ticket.png"


# ==================================================
#                  TICKET HELPERS
# ==================================================

def is_ticket_staff(member):
    """التحقق من أن العضو من فريق الإدارة."""
    if not isinstance(member, discord.Member):
        return False

    return (
        member.guild_permissions.administrator
        or any(role.id == STAFF_ROLE_ID for role in member.roles)
    )


def get_ticket_data(channel):
    """قراءة بيانات التذكرة من Topic القناة."""
    data = {
        "owner": None,
        "claimed": 0,
        "closed": "no"
    }

    topic = channel.topic or ""

    for item in topic.split("|"):
        if "=" not in item:
            continue

        key, value = item.split("=", 1)

        if key == "owner":
            try:
                data["owner"] = int(value)
            except ValueError:
                pass

        elif key == "claimed":
            try:
                data["claimed"] = int(value)
            except ValueError:
                pass

        elif key == "closed":
            data["closed"] = value

    return data


async def update_ticket_data(channel, key, value):
    """تحديث بيانات التذكرة داخل Topic."""
    data = get_ticket_data(channel)

    if key not in data:
        raise ValueError("Invalid ticket data key")

    data[key] = value

    topic = (
        f"owner={data['owner']}|"
        f"claimed={data['claimed']}|"
        f"closed={data['closed']}"
    )

    await channel.edit(topic=topic)


async def send_ticket_log(channel, action, user):
    """إرسال سجل عمليات التذاكر."""
    log_channel = bot.get_channel(TICKET_LOG_CHANNEL_ID)

    if log_channel is None:
        return

    data = get_ticket_data(channel)

    embed = discord.Embed(
        title="📋 سجل التذاكر",
        description=(
            f"**الإجراء:** {action}\n"
            f"**القناة:** {channel.name}\n"
            f"**المنفذ:** {user.mention}\n"
            f"**صاحب التذكرة:** "
            f"<@{data['owner']}>" if data["owner"] else
            f"**الإجراء:** {action}\n"
            f"**القناة:** {channel.name}\n"
            f"**المنفذ:** {user.mention}\n"
            f"**صاحب التذكرة:** غير معروف"
        ),
        color=discord.Color.blurple()
    )

    embed.timestamp = discord.utils.utcnow()

    await log_channel.send(embed=embed)


async def save_transcript(channel):
    """حفظ محادثة التذكرة وإرسالها إلى اللوج."""
    log_channel = bot.get_channel(TICKET_LOG_CHANNEL_ID)

    if log_channel is None:
        return

    lines = [
        f"Transcript: {channel.name}",
        f"Channel ID: {channel.id}",
        "=" * 50
    ]

    async for message in channel.history(
        limit=None,
        oldest_first=True
    ):
        timestamp = message.created_at.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        content = message.content or ""

        if message.attachments:
            attachments = " | ".join(
                attachment.url for attachment in message.attachments
            )
            content += f" [Attachments: {attachments}]"

        if message.embeds:
            content += " [Embed]"

        lines.append(
            f"[{timestamp}] {message.author}: {content}"
        )

    transcript = "\n".join(lines)

    file = discord.File(
        io.BytesIO(transcript.encode("utf-8")),
        filename=f"{channel.name}-transcript.txt"
    )

    await log_channel.send(
        content=f"📄 Transcript للتذكرة **{channel.name}**",
        file=file
    )


# ==================================================
#                  TICKET CONTROL VIEW
# ==================================================

class TicketControlView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="استلام",
        emoji="📥",
        style=discord.ButtonStyle.success,
        custom_id="ticket_claim"
    )
    async def claim(self, interaction, button):

        channel = interaction.channel

        if not is_ticket_staff(interaction.user):
            return await interaction.response.send_message(
                "❌ هذا الزر خاص بالإدارة.",
                ephemeral=True
            )

        data = get_ticket_data(channel)

        if data["claimed"]:
            member = interaction.guild.get_member(data["claimed"])
            name = member.mention if member else str(data["claimed"])

            return await interaction.response.send_message(
                f"التذكرة مستلمة بالفعل بواسطة {name}.",
                ephemeral=True
            )

        await update_ticket_data(
            channel,
            "claimed",
            interaction.user.id
        )

        embed = discord.Embed(
            title="📥 تم استلام التذكرة",
            description=f"المسؤول عن التذكرة: {interaction.user.mention}",
            color=discord.Color.green()
        )

        await interaction.response.send_message(embed=embed)

        await send_ticket_log(
            channel,
            "استلام",
            interaction.user
        )

    @discord.ui.button(
        label="إغلاق",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="ticket_close"
    )
    async def close(self, interaction, button):

        channel = interaction.channel

        if not is_ticket_staff(interaction.user):
            return await interaction.response.send_message(
                "❌ الإدارة فقط تستطيع إغلاق التذكرة.",
                ephemeral=True
            )

        data = get_ticket_data(channel)

        if data["closed"] == "yes":
            return await interaction.response.send_message(
                "التذكرة مغلقة بالفعل.",
                ephemeral=True
            )

        if data["owner"] is None:
            return await interaction.response.send_message(
                "❌ بيانات صاحب التذكرة غير موجودة.",
                ephemeral=True
            )

        member = interaction.guild.get_member(data["owner"])

        if member:
            overwrite = channel.overwrites_for(member)
            overwrite.send_messages = False

            await channel.set_permissions(
                member,
                overwrite=overwrite
            )

        await update_ticket_data(
            channel,
            "closed",
            "yes"
        )

        await interaction.response.send_message(
            f"🔒 تم إغلاق التذكرة بواسطة {interaction.user.mention}."
        )

        await send_ticket_log(
            channel,
            "إغلاق",
            interaction.user
        )

    @discord.ui.button(
        label="إعادة فتح",
        emoji="🔓",
        style=discord.ButtonStyle.primary,
        custom_id="ticket_reopen"
    )
    async def reopen(self, interaction, button):

        channel = interaction.channel

        if not is_ticket_staff(interaction.user):
            return await interaction.response.send_message(
                "❌ هذا الزر خاص بالإدارة.",
                ephemeral=True
            )

        data = get_ticket_data(channel)

        if data["closed"] != "yes":
            return await interaction.response.send_message(
                "التذكرة مفتوحة بالفعل.",
                ephemeral=True
            )

        if data["owner"] is None:
            return await interaction.response.send_message(
                "❌ بيانات صاحب التذكرة غير موجودة.",
                ephemeral=True
            )

        member = interaction.guild.get_member(data["owner"])

        if member:
            overwrite = channel.overwrites_for(member)
            overwrite.send_messages = True
            overwrite.view_channel = True

            await channel.set_permissions(
                member,
                overwrite=overwrite
            )

        await update_ticket_data(
            channel,
            "closed",
            "no"
        )

        await interaction.response.send_message(
            f"🔓 تمت إعادة فتح التذكرة بواسطة {interaction.user.mention}."
        )

        await send_ticket_log(
            channel,
            "إعادة فتح",
            interaction.user
        )

    @discord.ui.button(
        label="حذف",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        custom_id="ticket_delete"
    )
    async def delete(self, interaction, button):

        if not is_ticket_staff(interaction.user):
            return await interaction.response.send_message(
                "❌ هذا الزر خاص بالإدارة.",
                ephemeral=True
            )

        channel = interaction.channel

        await interaction.response.send_message(
            "📄 جاري حفظ المحادثة وحذف التذكرة..."
        )

        try:
            await save_transcript(channel)

            await send_ticket_log(
                channel,
                "حذف",
                interaction.user
            )

            await asyncio.sleep(2)

            await channel.delete(
                reason=f"Deleted by {interaction.user}"
            )

        except discord.Forbidden:
            print("Missing permissions to delete ticket.")

        except Exception as error:
            print(f"Ticket error: {error}")


# ==================================================
#                    TICKET OPEN
# ==================================================

class TicketOpenView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="فتح تذكرة",
        emoji="🎫",
        style=discord.ButtonStyle.success,
        custom_id="ticket_open"
    )
    async def open_ticket(self, interaction, button):

        guild = interaction.guild

        if guild is None:
            return await interaction.response.send_message(
                "❌ هذا الزر يعمل داخل السيرفر فقط.",
                ephemeral=True
            )

        category = guild.get_channel(TICKET_CATEGORY_ID)

        if not isinstance(category, discord.CategoryChannel):
            return await interaction.response.send_message(
                "❌ قسم التذاكر غير موجود.",
                ephemeral=True
            )

        existing = discord.utils.get(
            category.text_channels,
            name=f"ticket-{interaction.user.id}"
        )

        if existing:
            return await interaction.response.send_message(
                f"لديك تذكرة بالفعل: {existing.mention}",
                ephemeral=True
            )

        staff_role = guild.get_role(STAFF_ROLE_ID)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False
            ),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )
        }

        bot_member = guild.me

        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True
            )

        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )

        channel = await guild.create_text_channel(
            name=f"ticket-{interaction.user.id}",
            category=category,
            overwrites=overwrites,
            topic=(
                f"owner={interaction.user.id}|"
                "claimed=0|closed=no"
            )
        )

        embed = discord.Embed(
            title="🎫 تذكرة الدعم الفني",
            description=(
                f"مرحباً {interaction.user.mention} ❤️\n\n"
                "تم فتح تذكرتك بنجاح.\n"
                "انتظر حتى يستلم أحد أفراد الإدارة طلبك.\n\n"
                "استخدم الأزرار للتحكم في التذكرة."
            ),
            color=discord.Color.blurple()
        )

        embed.set_footer(
            text="Ticket System | الدعم الفني"
        )

        await channel.send(
            content=interaction.user.mention,
            embed=embed,
            view=TicketControlView()
        )

        await interaction.response.send_message(
            f"✅ تم فتح تذكرتك: {channel.mention}",
            ephemeral=True
        )

        await send_ticket_log(
            channel,
            "فتح تذكرة",
            interaction.user
        )


# ==================================================
#                    TICKET PANEL
# ==================================================

@bot.command()
@commands.has_permissions(administrator=True)
async def ticketpanel(ctx):

    embed = discord.Embed(
        title="🎫 نظام التذاكر | الدعم الفني",
        description=(
            "مرحباً بك في نظام الدعم!\n\n"
            "إذا كنت تواجه مشكلة أو لديك استفسار أو طلب، "
            "يمكنك فتح تذكرة والتواصل مع فريق الإدارة.\n\n"
            "📌 يرجى شرح مشكلتك بوضوح وانتظار رد أحد أعضاء الفريق.\n\n"
            "⚠️ يُرجى عدم فتح تذاكر دون سبب، وشكراً لتعاونكم.\n\n"
            "💙 نحن هنا لمساعدتك!"
        ),
        color=discord.Color.blurple()
    )

    embed.set_footer(
        text="Ticket System | الدعم الفني"
    )

    if os.path.isfile(TICKET_PANEL_IMAGE_PATH):

        file = discord.File(
            TICKET_PANEL_IMAGE_PATH,
            filename="ticket.png"
        )

        embed.set_image(
            url="attachment://ticket.png"
        )

        await ctx.send(
            embed=embed,
            file=file,
            view=TicketOpenView()
        )

    else:

        await ctx.send(
            embed=embed,
            view=TicketOpenView()
        )

        print(
            f"WARNING: Image not found: {TICKET_PANEL_IMAGE_PATH}"
        )

# ==================================================
#                  APPLICATION SYSTEM
# ==================================================

class ApplicationView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="تقديم طلب",
        emoji="📝",
        style=discord.ButtonStyle.primary,
        custom_id="start_application"
    )
    async def apply(self, interaction, button):

        await interaction.response.send_message(
            "📩 تم إرسال رسالة خاصة لك لبدء التقديم.",
            ephemeral=True
        )

        try:

            await run_application(
                interaction.user
            )

        except discord.Forbidden:

            await interaction.followup.send(
                "❌ افتح الرسائل الخاصة حتى يستطيع البوت سؤالك.",
                ephemeral=True
            )


async def run_application(user):

    dm = await user.create_dm()

    answers = []

    await dm.send(
        "📝 **بدأ التقديم!**\n"
        "أجب عن الأسئلة بالترتيب.\n"
        "لديك 5 دقائق لكل سؤال."
    )

    for number, question in enumerate(QUESTIONS, 1):

        await dm.send(
            f"**السؤال {number}:**\n{question}"
        )

        def check(message):

            return (
                message.author.id == user.id
                and message.channel.id == dm.id
            )

        try:

            answer = await bot.wait_for(
                "message",
                check=check,
                timeout=300
            )

            answers.append(
                (question, answer.content)
            )

        except asyncio.TimeoutError:

            await dm.send(
                "⏰ انتهى وقت التقديم. حاول مرة أخرى."
            )

            return

    channel = bot.get_channel(
        APPLICATION_CHANNEL_ID
    )

    if channel is None:

        await dm.send(
            "❌ روم التقديم غير موجود."
        )

        return

    embed = discord.Embed(
        title="📋 تقديم جديد",
        description=f"المتقدم: {user.mention}",
        color=discord.Color.blue()
    )

    for question, answer in answers:

        embed.add_field(
            name=question[:256],
            value=answer[:1024] or "بدون إجابة",
            inline=False
        )

    await channel.send(
        embed=embed
    )

    await dm.send(
        "✅ تم إرسال تقديمك بنجاح!"
    )


@bot.command()
@commands.has_permissions(administrator=True)
async def applypanel(ctx):

    embed = discord.Embed(
        title="📝 التقديم للإدارة",
        description=(
            "اضغط على الزر بالأسفل.\n"
            "سيتم إرسال الأسئلة لك في الخاص."
        ),
        color=discord.Color.blue()
    )

    await ctx.send(
        embed=embed,
        view=ApplicationView()
    )


# ==================================================
#                  MESSAGE HANDLER
# ==================================================

@bot.event
async def on_message(message):

    if message.author.bot:
        return

    if message.guild is None:
        return

    # معالجة أوامر البادئة -- بالطريقة القياسية ومنعها من أخذ XP
    ctx = await bot.get_context(message)

    if ctx.valid:
        await bot.process_commands(message)
        return

    key = (
        message.guild.id,
        message.author.id
    )

    now = time.monotonic()

    # العضو تحت عقوبة منع XP
    if now < spam_until.get(key, 0):

        try:
            await message.delete()

        except (discord.Forbidden, discord.NotFound):
            pass

        return

    # فحص السبام
    if check_spam(message):

        spam_until[key] = (
            now + SPAM_BLOCK_SECONDS
        )

        try:

            await message.delete()

        except (discord.Forbidden, discord.NotFound):
            pass

        last = last_warning.get(key, 0)

        if now - last >= 30:

            last_warning[key] = now

            try:

                await message.channel.send(
                    f"⚠️ {message.author.mention} "
                    "تم اكتشاف السبام! لن تحصل على XP لمدة 20 ثانية."
                )

            except discord.Forbidden:
                pass

        return

    # رسالة سليمة = XP
    await add_xp(message)


# ==================================================
#                    BOT READY
# ==================================================

views_registered = False


@bot.event
async def on_ready():

    global views_registered

    if not views_registered:

        bot.add_view(TicketOpenView())
        bot.add_view(TicketControlView())
        bot.add_view(ApplicationView())

        views_registered = True

    print("=" * 40)
    print(f"Bot Online: {bot.user}")
    print("Welcome: ON")
    print("Levels: ON")
    print("Anti-Spam: ON")
    print("Tickets: ON")
    print("Ticket Claim: ON")
    print("Applications: ON")
    print("=" * 40)


# ==================================================
#                    ERROR HANDLER
# ==================================================

@bot.event
async def on_command_error(ctx, error):

    if isinstance(error, commands.MissingPermissions):

        return await ctx.send(
            "❌ ليس لديك صلاحية استخدام هذا الأمر."
        )

    if isinstance(error, commands.CommandNotFound):
        return

    print(f"Command Error: {error}")

    await ctx.send(
        "حدث خطأ أثناء تنفيذ الأمر."
    )


# ==================================================
#                    START BOT
# ==================================================

if not TOKEN:

    raise RuntimeError(
        "BOT_TOKEN غير موجود في ملف .env"
    )

bot.run(TOKEN)
