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

app, pytgcalls = create_client()
vc_handler = VCRelayHandler(app, pytgcalls)


# ─── COMMANDS ─────────────────────────────────────────

@app.on_message(filters.command("join", prefixes="/") & filters.private)
async def join_cmd(client: Client, msg: Message):
    if msg.chat.id != PRIVATE_GROUP_ID:
        return

    parts = msg.text.split()
    if len(parts) < 2:
        await msg.reply("Usage: `/join <target_chat_id>`")
        return

    try:
        target_id = int(parts[1])
    except ValueError:
        await msg.reply("❌ Invalid ID.")
        return

    if target_id in active_relays and active_relays[target_id]["running"]:
        await msg.reply(f"⚠️ Already relaying to `{target_id}`.")
        return

    await msg.reply(f"🔄 Joining `{target_id}` with extreme audio...")
    success = await vc_handler.join_and_bridge(
        target_id,
        volume=DEFAULT_VOLUME,
        gain_db=DEFAULT_GAIN,
        bass_db=DEFAULT_BASS,
        clarity_db=DEFAULT_CLARITY,
    )

    if success:
        await msg.reply(
            f"✅ **Relay Active!**\n\n"
            f"🎯 Target: `{target_id}`\n"
            f"🔊 Volume: `{DEFAULT_VOLUME}x`\n"
            f"📈 Gain: `+{DEFAULT_GAIN}dB`\n"
            f"🔊 Bass: `+{DEFAULT_BASS}dB` at 100Hz\n"
            f"🎤 Clarity: `+{DEFAULT_CLARITY}dB` at 2kHz\n\n"
            f"**⚠️ WARNING:** This is EXTREME amplification. "
            f"Start low and test first!\n\n"
            f"Commands:\n"
            f"`/volume <0-1000>`\n"
            f"`/gain <dB>`\n"
            f"`/bass <dB>`\n"
            f"`/clarity <dB>`\n"
            f"`/set vol=400 gain=100 bass=50 clarity=50`"
        )
    else:
        await msg.reply("❌ Failed. Is the target VC active?")


@app.on_message(filters.command("leave", prefixes="/") & filters.private)
async def leave_cmd(client: Client, msg: Message):
    if msg.chat.id != PRIVATE_GROUP_ID:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.reply("Usage: `/leave <target_chat_id>`")
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
    if msg.chat.id != PRIVATE_GROUP_ID:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        await show_status(msg)
        return
    try:
        vol = float(parts[1])
        vol = max(0.0, min(1000.0, vol))
    except ValueError:
        await msg.reply("❌ Use a number 0-1000.")
        return
    targets = [int(parts[2])] if len(parts) >= 3 else list(active_relays.keys())
    for tid in targets:
        await vc_handler.update_audio(tid, volume=vol)
    await msg.reply(f"✅ Volume set to `{vol}x` for {len(targets)} relay(s).")


@app.on_message(filters.command("gain", prefixes="/") & filters.private)
async def gain_cmd(client: Client, msg: Message):
    if msg.chat.id != PRIVATE_GROUP_ID:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        await show_status(msg)
        return
    try:
        gain = float(parts[1])
    except ValueError:
        await msg.reply("❌ Use a number (dB).")
        return
    targets = [int(parts[2])] if len(parts) >= 3 else list(active_relays.keys())
    for tid in targets:
        await vc_handler.update_audio(tid, gain_db=gain)
    await msg.reply(f"✅ Gain set to `+{gain}dB` for {len(targets)} relay(s).")


@app.on_message(filters.command("bass", prefixes="/") & filters.private)
async def bass_cmd(client: Client, msg: Message):
    if msg.chat.id != PRIVATE_GROUP_ID:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        await show_status(msg)
        return
    try:
        bass = float(parts[1])
    except ValueError:
        await msg.reply("❌ Use a number (dB).")
        return
    targets = [int(parts[2])] if len(parts) >= 3 else list(active_relays.keys())
    for tid in targets:
        await vc_handler.update_audio(tid, bass_db=bass)
    await msg.reply(f"✅ Bass set to `+{bass}dB` for {len(targets)} relay(s).")


@app.on_message(filters.command("clarity", prefixes="/") & filters.private)
async def clarity_cmd(client: Client, msg: Message):
    if msg.chat.id != PRIVATE_GROUP_ID:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        await show_status(msg)
        return
    try:
        clarity = float(parts[1])
    except ValueError:
        await msg.reply("❌ Use a number (dB).")
        return
    targets = [int(parts[2])] if len(parts) >= 3 else list(active_relays.keys())
    for tid in targets:
        await vc_handler.update_audio(tid, clarity_db=clarity)
    await msg.reply(f"✅ Clarity set to `+{clarity}dB` for {len(targets)} relay(s).")


@app.on_message(filters.command("set", prefixes="/") & filters.private)
async def set_cmd(client: Client, msg: Message):
    """
    /set vol=400 gain=100 bass=50 clarity=50 [target_id]
    Set all parameters at once.
    """
    if msg.chat.id != PRIVATE_GROUP_ID:
        return

    parts = msg.text.split()
    if len(parts) < 2:
        await msg.reply("Usage: `/set vol=400 gain=100 bass=50 clarity=50 [target_id]`")
        return

    vol = gain = bass = clarity = None
    target_id = None

    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            try:
                v = float(v)
                if k == "vol":
                    vol = max(0, min(1000, v))
                elif k == "gain":
                    gain = v
                elif k == "bass":
                    bass = v
                elif k == "clarity":
                    clarity = v
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

    summary = []
    if vol is not None: summary.append(f"vol={vol}")
    if gain is not None: summary.append(f"gain={gain}dB")
    if bass is not None: summary.append(f"bass={bass}dB")
    if clarity is not None: summary.append(f"clarity={clarity}dB")
    await msg.reply(f"✅ Set {', '.join(summary)} for {len(targets)} relay(s).")


@app.on_message(filters.command("status", prefixes="/") & filters.private)
async def status_cmd(client: Client, msg: Message):
    await show_status(msg)


async def show_status(msg: Message):
    if not active_relays:
        await msg.reply("📭 No active relays.")
        return

    text = "**📡 Active Relays:**\n\n"
    for tid, relay in active_relays.items():
        if relay["running"]:
            text += (
                f"🔹 **Target:** `{tid}`\n"
                f"   🔊 Volume: `{relay['volume']}x`\n"
                f"   📈 Gain: `+{relay['gain']}dB`\n"
                f"   🔊 Bass: `+{relay['bass']}dB`\n"
                f"   🎤 Clarity: `+{relay['clarity']}dB`\n"
                f"   ⚡ Total boost: "
                f"`{relay['volume'] * (10 ** (relay['gain'] / 20)):.0f}x` linear\n\n"
            )
    await msg.reply(text)


@app.on_message(filters.command("leaveall", prefixes="/") & filters.private)
async def leave_all_cmd(client: Client, msg: Message):
    if msg.chat.id != PRIVATE_GROUP_ID:
        return
    targets = list(active_relays.keys())
    for tid in targets:
        await vc_handler.leave_and_disconnect(tid)
    await msg.reply(f"❌ Left all {len(targets)} relays.")


# ─── MAIN ──────────────────────────────────────────────

async def main():
    await pytgcalls.start()
    await app.start()
    logger.info("🤖 Extreme VC Relay Bot running!")
    logger.info(f"Private Group: {PRIVATE_GROUP_ID}")
    logger.info(f"Default: vol={DEFAULT_VOLUME}x, gain={DEFAULT_GAIN}dB, "
                f"bass={DEFAULT_BASS}dB, clarity={DEFAULT_CLARITY}dB")
    logger.info("⚠️  CAUTION: Default settings are EXTREME! Use /set to adjust.")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
