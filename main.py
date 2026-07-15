import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import Message

from config import (
    API_ID, API_HASH, SESSION_STRING, PRIVATE_GROUP_ID,
    active_relays, DEFAULT_VOLUME, DEFAULT_GAIN,
    DEFAULT_BASS, DEFAULT_CLARITY,
)
from bot.client import create_client
from bot.vc_handler import VCRelayHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = create_client()
vc_handler = VCRelayHandler(app)


# ─── ALL COMMANDS ─────────────────────────────────────

@app.on_message(filters.command("join", prefixes="/") & filters.private)
async def join_cmd(client: Client, msg: Message):
    """ /join <target_chat_id> """
    if msg.chat.id != PRIVATE_GROUP_ID:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.reply("Usage: `/join -1001234567890`")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await msg.reply("❌ Invalid chat ID.")
        return
    if target_id in active_relays and active_relays[target_id]["running"]:
        await msg.reply(f"⚠️ Already relaying to `{target_id}`.")
        return

    await msg.reply(f"🔄 Joining `{target_id}` with extreme audio...")
    success = await vc_handler.join_and_bridge(target_id)
    if success:
        await msg.reply(
            f"✅ **Relay Active!**\n\n"
            f"🎯 Target: `{target_id}`\n"
            f"🔊 Volume: `{DEFAULT_VOLUME}x`\n"
            f"📈 Gain: `+{DEFAULT_GAIN}dB`\n"
            f"🔊 Bass: `+{DEFAULT_BASS}dB`\n"
            f"🎤 Clarity: `+{DEFAULT_CLARITY}dB`\n\n"
            f"**⚠️ This is EXTREME amplification!**\n"
            f"Use /set to adjust:\n`/set vol=5 gain=10 bass=5 clarity=5`"
        )
    else:
        await msg.reply("❌ Failed. Is the target VC active?")


@app.on_message(filters.command("leave", prefixes="/") & filters.private)
async def leave_cmd(client: Client, msg: Message):
    """ /leave <target_chat_id> """
    if msg.chat.id != PRIVATE_GROUP_ID:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.reply("Usage: `/leave -1001234567890`")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await msg.reply("❌ Invalid ID.")
        return
    await vc_handler.leave_and_disconnect(target_id)
    await msg.reply(f"❌ Disconnected from `{target_id}`.")


@app.on_message(filters.command("volume", prefixes="/") & filters.private)
async def volume_cmd(client: Client, msg: Message):
    """ /volume <0-1000> [target_id] """
    if msg.chat.id != PRIVATE_GROUP_ID:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        await status_cmd(client, msg)
        return
    try:
        vol = max(0.0, min(1000.0, float(parts[1])))
    except ValueError:
        await msg.reply("❌ Use a number 0-1000.")
        return
    targets = [int(parts[2])] if len(parts) >= 3 and parts[2].lstrip("-").isdigit() else list(active_relays.keys())
    for tid in targets:
        await vc_handler.update_audio(tid, volume=vol)
    await msg.reply(f"✅ Volume → `{vol}x` for {len(targets)} relay(s).")


@app.on_message(filters.command("gain", prefixes="/") & filters.private)
async def gain_cmd(client: Client, msg: Message):
    """ /gain <dB> [target_id] """
    if msg.chat.id != PRIVATE_GROUP_ID:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        return
    try:
        gain = float(parts[1])
    except ValueError:
        await msg.reply("❌ Use a number (dB).")
        return
    targets = [int(parts[2])] if len(parts) >= 3 and parts[2].lstrip("-").isdigit() else list(active_relays.keys())
    for tid in targets:
        await vc_handler.update_audio(tid, gain_db=gain)
    await msg.reply(f"✅ Gain → `+{gain}dB` for {len(targets)} relay(s).")


@app.on_message(filters.command("bass", prefixes="/") & filters.private)
async def bass_cmd(client: Client, msg: Message):
    """ /bass <dB> [target_id] """
    if msg.chat.id != PRIVATE_GROUP_ID:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        return
    try:
        bass = float(parts[1])
    except ValueError:
        await msg.reply("❌ Use a number (dB).")
        return
    targets = [int(parts[2])] if len(parts) >= 3 and parts[2].lstrip("-").isdigit() else list(active_relays.keys())
    for tid in targets:
        await vc_handler.update_audio(tid, bass_db=bass)
    await msg.reply(f"✅ Bass → `+{bass}dB` for {len(targets)} relay(s).")


@app.on_message(filters.command("clarity", prefixes="/") & filters.private)
async def clarity_cmd(client: Client, msg: Message):
    """ /clarity <dB> [target_id] """
    if msg.chat.id != PRIVATE_GROUP_ID:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        return
    try:
        clarity = float(parts[1])
    except ValueError:
        await msg.reply("❌ Use a number (dB).")
        return
    targets = [int(parts[2])] if len(parts) >= 3 and parts[2].lstrip("-").isdigit() else list(active_relays.keys())
    for tid in targets:
        await vc_handler.update_audio(tid, clarity_db=clarity)
    await msg.reply(f"✅ Clarity → `+{clarity}dB` for {len(targets)} relay(s).")


@app.on_message(filters.command("set", prefixes="/") & filters.private)
async def set_cmd(client: Client, msg: Message):
    """ /set vol=400 gain=100 bass=50 clarity=50 [target_id] """
    if msg.chat.id != PRIVATE_GROUP_ID:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.reply("Usage: `/set vol=5 gain=10 bass=5 clarity=5 [target_id]`")
        return

    vol = gain = bass = clarity = None
    target_id = None
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            try:
                v = float(v)
                if k == "vol": vol = max(0, min(1000, v))
                elif k == "gain": gain = v
                elif k == "bass": bass = v
                elif k == "clarity": clarity = v
            except ValueError:
                pass
        else:
            try:
                target_id = int(p)
            except ValueError:
                pass

    targets = [target_id] if target_id else list(active_relays.keys())
    for tid in targets:
        await vc_handler.update_audio(tid, volume=vol, gain_db=gain, bass_db=bass, clarity_db=clarity)

    changes = []
    if vol is not None: changes.append(f"vol={vol}")
    if gain is not None: changes.append(f"gain={gain}dB")
    if bass is not None: changes.append(f"bass={bass}dB")
    if clarity is not None: changes.append(f"clarity={clarity}dB")
    await msg.reply(f"✅ Set {', '.join(changes)} for {len(targets)} relay(s).")


@app.on_message(filters.command("status", prefixes="/") & filters.private)
async def status_cmd(client: Client, msg: Message):
    """ /status - show all active relays """
    if msg.chat.id != PRIVATE_GROUP_ID:
        return
    if not active_relays:
        await msg.reply("📭 No active relays.")
        return
    text = "**📡 Active Relays:**\n\n"
    for tid, relay in active_relays.items():
        if relay["running"]:
            total = relay["volume"] * (10 ** (relay["gain"] / 20))
            text += (
                f"🔹 **Target:** `{tid}`\n"
                f"   🔊 Vol: `{relay['volume']}x`\n"
                f"   📈 Gain: `+{relay['gain']}dB`\n"
                f"   🔊 Bass: `+{relay['bass']}dB`\n"
                f"   🎤 Clarity: `+{relay['clarity']}dB`\n"
                f"   ⚡ Total: `{total:.0f}x` linear\n\n"
            )
    await msg.reply(text)


@app.on_message(filters.command("leaveall", prefixes="/") & filters.private)
async def leave_all_cmd(client: Client, msg: Message):
    """ /leaveall """
    if msg.chat.id != PRIVATE_GROUP_ID:
        return
    targets = list(active_relays.keys())
    for tid in targets:
        await vc_handler.leave_and_disconnect(tid)
    await msg.reply(f"❌ Left all {len(targets)} relays.")


# ─── START ────────────────────────────────────────────

async def main():
    await app.start()
    logger.info("🤖 Extreme VC Relay Bot is LIVE!")
    logger.info(f"Private Group: {PRIVATE_GROUP_ID}")
    logger.info(f"Defaults: vol={DEFAULT_VOLUME}x | gain={DEFAULT_GAIN}dB | bass={DEFAULT_BASS}dB | clarity={DEFAULT_CLARITY}dB")
    logger.info("⚠️  These are EXTREME settings — use /set to adjust safely!")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
