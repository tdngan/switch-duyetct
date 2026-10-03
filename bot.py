import os
import logging
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

log = logging.getLogger("switch-duyetct")


# ============================================================
# CONFIG
# ============================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

CLOUDFLARE_API_TOKEN = os.getenv(
    "CLOUDFLARE_API_TOKEN", ""
).strip()

CLOUDFLARE_ZONE = os.getenv(
    "CLOUDFLARE_ZONE",
    "baoptthcantho.vn",
).strip()

DNS_RECORD = os.getenv(
    "DNS_RECORD",
    "duyetct.baoptthcantho.vn",
).strip()

IP12 = os.getenv("IP12", "115.74.213.12").strip()
IP118 = os.getenv("IP118", "113.164.176.118").strip()

CLOUDFLARE_PROXIED = (
    os.getenv("CLOUDFLARE_PROXIED", "true")
    .lower()
    == "true"
)

ALLOWED_USER_IDS = {
    int(x.strip())
    for x in os.getenv(
        "DISCORD_ALLOWED_USER_IDS",
        "",
    ).split(",")
    if x.strip().isdigit()
}


# ============================================================
# VALIDATION
# ============================================================

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN chưa được cấu hình.")

if not CLOUDFLARE_API_TOKEN:
    raise RuntimeError(
        "CLOUDFLARE_API_TOKEN chưa được cấu hình."
    )

if not ALLOWED_USER_IDS:
    raise RuntimeError(
        "DISCORD_ALLOWED_USER_IDS chưa được cấu hình."
    )


# ============================================================
# CLOUDFLARE
# ============================================================

CF_API = "https://api.cloudflare.com/client/v4"


class CloudflareError(Exception):
    pass


async def cf_request(
    method: str,
    url: str,
    **kwargs,
):
    headers = kwargs.pop("headers", {})

    headers.update(
        {
            "Authorization": (
                f"Bearer {CLOUDFLARE_API_TOKEN}"
            ),
            "Content-Type": "application/json",
        }
    )

    timeout = aiohttp.ClientTimeout(total=15)

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.request(
            method,
            url,
            headers=headers,
            **kwargs,
        ) as response:

            data = await response.json()

            if not data.get("success"):
                raise CloudflareError(
                    str(data.get("errors", data))
                )

            return data


async def get_zone_id() -> str:

    url = (
        f"{CF_API}/zones"
        f"?name={CLOUDFLARE_ZONE}"
        f"&status=active"
    )

    data = await cf_request("GET", url)

    result = data.get("result", [])

    if not result:
        raise CloudflareError(
            f"Không tìm thấy zone: {CLOUDFLARE_ZONE}"
        )

    return result[0]["id"]


async def get_record(
    zone_id: str,
) -> tuple[str, dict]:

    url = (
        f"{CF_API}/zones/{zone_id}/dns_records"
        f"?type=A"
        f"&name={DNS_RECORD}"
    )

    data = await cf_request("GET", url)

    result = data.get("result", [])

    if not result:
        raise CloudflareError(
            f"Không tìm thấy A record: {DNS_RECORD}"
        )

    record = result[0]

    return record["id"], record


async def get_current_ip() -> tuple[str, dict]:

    zone_id = await get_zone_id()

    record_id, record = await get_record(zone_id)

    return record["content"], record


async def switch_ip(
    target_ip: str,
) -> tuple[str, str]:

    zone_id = await get_zone_id()

    record_id, record = await get_record(zone_id)

    current_ip = record["content"]

    if current_ip == target_ip:
        return current_ip, target_ip

    url = (
        f"{CF_API}/zones/{zone_id}"
        f"/dns_records/{record_id}"
    )

    payload = {
        "type": "A",
        "name": DNS_RECORD,
        "content": target_ip,
        "ttl": 1,
        "proxied": CLOUDFLARE_PROXIED,
    }

    await cf_request(
        "PUT",
        url,
        json=payload,
    )

    # Đọc lại để xác nhận
    verify_ip, _ = await get_current_ip()

    if verify_ip != target_ip:
        raise CloudflareError(
            "Cloudflare không trả về IP mong muốn "
            f"sau khi cập nhật: {verify_ip}"
        )

    return current_ip, verify_ip


# ============================================================
# DISCORD BOT
# ============================================================

intents = discord.Intents.default()

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
)


# ============================================================
# AUTHORIZATION
# ============================================================

def is_allowed(
    user_id: int,
) -> bool:
    return user_id in ALLOWED_USER_IDS


async def deny(
    interaction: discord.Interaction,
):

    await interaction.response.send_message(
        "⛔ Bạn không được phép sử dụng chức năng này.",
        ephemeral=True,
    )


# ============================================================
# EMBED
# ============================================================

def make_status_embed(
    current_ip: str,
) -> discord.Embed:

    if current_ip == IP12:
        status = f"🟢 IP 12 — `{IP12}`"
    elif current_ip == IP118:
        status = f"🔵 IP 118 — `{IP118}`"
    else:
        status = f"⚠️ IP ngoài danh sách — `{current_ip}`"

    embed = discord.Embed(
        title="DNS Switch — duyetct",
        description=(
            f"**Domain**\n"
            f"`{DNS_RECORD}`\n\n"
            f"**IP hiện tại**\n"
            f"{status}\n\n"
            f"**IP 12:** `{IP12}`\n"
            f"**IP 118:** `{IP118}`"
        ),
        color=discord.Color.blue(),
    )

    embed.set_footer(
        text="Cloudflare DNS"
    )

    return embed


# ============================================================
# BUTTON VIEW
# ============================================================

class SwitchView(
    discord.ui.View
):

    def __init__(
        self,
        timeout: Optional[float] = 300,
    ):
        super().__init__(
            timeout=timeout
        )

    async def do_switch(
        self,
        interaction: discord.Interaction,
        target_ip: str,
    ):

        if not is_allowed(
            interaction.user.id
        ):
            await deny(interaction)
            return

        await interaction.response.defer(
            ephemeral=True
        )

        try:

            old_ip, new_ip = await switch_ip(
                target_ip
            )

            if old_ip == new_ip:

                message = (
                    "ℹ️ DNS đã ở IP này.\n\n"
                    f"**Domain:** `{DNS_RECORD}`\n"
                    f"**IP:** `{new_ip}`"
                )

            else:

                message = (
                    "✅ **Đã chuyển DNS thành công.**\n\n"
                    f"**Domain:** `{DNS_RECORD}`\n"
                    f"**Trước:** `{old_ip}`\n"
                    f"**Sau:** `{new_ip}`\n\n"
                    "Cloudflare đã xác nhận."
                )

            await interaction.followup.send(
                message,
                ephemeral=True,
            )

        except Exception as exc:

            log.exception(
                "Cloudflare switch failed"
            )

            await interaction.followup.send(
                "❌ **Không thể chuyển DNS.**\n\n"
                f"`{exc}`",
                ephemeral=True,
            )

    @discord.ui.button(
        label="IP 1",
        emoji="🟢",
        style=discord.ButtonStyle.success,
        custom_id="duyetct_IP12",
    )
    async def IP12_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await self.do_switch(
            interaction,
            IP12,
        )

    @discord.ui.button(
        label="IP 2",
        emoji="🔵",
        style=discord.ButtonStyle.primary,
        custom_id="duyetct_IP118",
    )
    async def IP118_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await self.do_switch(
            interaction,
            IP118,
        )


# ============================================================
# /duyetct
# ============================================================

@bot.tree.command(
    name="duyetct",
    description="Xem và chuyển DNS duyetct.baoptthcantho.vn",
)
async def duyetct(
    interaction: discord.Interaction,
):

    if not is_allowed(
        interaction.user.id
    ):
        await deny(interaction)
        return

    await interaction.response.defer(
        ephemeral=True
    )

    try:

        current_ip, _ = await get_current_ip()

        embed = make_status_embed(
            current_ip
        )

        await interaction.followup.send(
            embed=embed,
            view=SwitchView(),
            ephemeral=True,
        )

    except Exception as exc:

        log.exception(
            "Cannot get DNS status"
        )

        await interaction.followup.send(
            "❌ Không đọc được trạng thái Cloudflare.\n\n"
            f"`{exc}`",
            ephemeral=True,
        )


# ============================================================
# /switch
# ============================================================

@bot.tree.command(
    name="switch",
    description="Chuyển DNS duyetct sang IP 12 hoặc IP 118",
)
@app_commands.describe(
    target="Chọn IP muốn chuyển",
)
@app_commands.choices(
    target=[
        app_commands.Choice(
            name=f"IP 12 ({IP12})",
            value="12",
        ),
        app_commands.Choice(
            name=f"IP 118 ({IP118})",
            value="118",
        ),
    ]
)
async def switch_command(
    interaction: discord.Interaction,
    target: app_commands.Choice[str],
):

    if not is_allowed(
        interaction.user.id
    ):
        await deny(interaction)
        return

    if target.value == "12":
        target_ip = IP12
    else:
        target_ip = IP118

    await interaction.response.defer(
        ephemeral=True
    )

    try:

        old_ip, new_ip = await switch_ip(
            target_ip
        )

        if old_ip == new_ip:

            message = (
                "ℹ️ DNS đã ở IP này.\n\n"
                f"`{DNS_RECORD}` → `{new_ip}`"
            )

        else:

            message = (
                "✅ **Đã chuyển DNS.**\n\n"
                f"`{DNS_RECORD}`\n"
                f"`{old_ip}` → `{new_ip}`\n\n"
                "Cloudflare đã xác nhận."
            )

        await interaction.followup.send(
            message,
            ephemeral=True,
        )

    except Exception as exc:

        log.exception(
            "Switch command failed"
        )

        await interaction.followup.send(
            "❌ **Lỗi khi chuyển DNS.**\n\n"
            f"`{exc}`",
            ephemeral=True,
        )


# ============================================================
# READY
# ============================================================

@bot.event
async def on_ready():

    log.info(
        "Logged in as %s (%s)",
        bot.user,
        bot.user.id,
    )

    try:

        synced = await bot.tree.sync()

        log.info(
            "Synced %d slash commands.",
            len(synced),
        )

    except Exception:

        log.exception(
            "Failed to sync slash commands"
        )


# ============================================================
# RUN
# ============================================================

bot.run(DISCORD_TOKEN)
