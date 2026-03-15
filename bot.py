#!/usr/bin/env python3
"""
╔══════════════════════════════════════════╗
║     CATBOX TELEGRAM BOT 🐈  v3           ║
║  Pyrogram (MTProto) — 2GB file support   ║
║  Videos + Images → Catbox direct links   ║
╚══════════════════════════════════════════╝
ENV VARS needed on Railway:
  BOT_TOKEN       — from @BotFather
  API_ID          — from my.telegram.org
  API_HASH        — from my.telegram.org
  CATBOX_USERHASH — from catbox.moe settings
  ADMIN_ID        — your Telegram numeric ID
"""

import os
import asyncio
import aiohttp
import logging
import time
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode

# ── CONFIG ────────────────────────────────
BOT_TOKEN       = os.environ["BOT_TOKEN"]
API_ID          = int(os.environ["API_ID"])
API_HASH        = os.environ["API_HASH"]
CATBOX_USERHASH = os.environ["CATBOX_USERHASH"]
ADMIN_ID        = int(os.environ["ADMIN_ID"])
TEMP_DIR        = "/tmp/catbox"
MAX_MB          = 200
MAX_BYTES       = MAX_MB * 1024 * 1024
CATBOX_API      = "https://catbox.moe/user/api.php"
# ─────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)
os.makedirs(TEMP_DIR, exist_ok=True)

app = Client(
    "catbox_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    # Store session in /tmp so Railway doesn't complain
    workdir="/tmp",
)


# ════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════
def human_size(b: int) -> str:
    if not b:
        return "? B"
    for u in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"


def pbar(done: int, total: int, w: int = 18) -> str:
    if total == 0:
        return "▒" * w + " ..."
    pct  = min(done / total, 1.0)
    fill = int(w * pct)
    return "█" * fill + "░" * (w - fill) + f" {int(pct*100)}%"


async def safe_edit(msg: Message, text: str):
    try:
        await msg.edit_text(text)
    except Exception:
        pass


# ════════════════════════════════════════
#  PYROGRAM DOWNLOAD with progress
#  (MTProto — supports up to 2GB)
# ════════════════════════════════════════
async def pyro_download(msg_with_media: Message, dest: str,
                        prog_msg: Message, name: str) -> str:
    """
    Download using Pyrogram's built-in download method.
    This uses MTProto — can handle files up to 2GB.
    Returns final path.
    """
    total    = 0
    last_t   = [0.0]   # mutable for closure

    # Get file size from media object
    media = (
        msg_with_media.video
        or msg_with_media.document
        or msg_with_media.photo
        or msg_with_media.animation
    )
    if media:
        total = getattr(media, "file_size", 0) or 0

    async def progress(current, total_bytes):
        now = time.time()
        if now - last_t[0] > 2.0:
            bar = pbar(current, total_bytes)
            await safe_edit(
                prog_msg,
                f"📥 **Downloading...**\n"
                f"📁 `{name}`\n"
                f"`{bar}`\n"
                f"_{human_size(current)} / {human_size(total_bytes)}_"
            )
            last_t[0] = now

    path = await msg_with_media.download(
        file_name=dest,
        progress=progress,
    )
    return path


# ════════════════════════════════════════
#  CATBOX UPLOAD
# ════════════════════════════════════════
async def catbox_upload(path: str, prog_msg: Message, name: str) -> str:
    size    = os.path.getsize(path)
    timeout = aiohttp.ClientTimeout(total=600, connect=30)

    await safe_edit(
        prog_msg,
        f"☁️ **Uploading to Catbox...**\n"
        f"📁 `{name}`\n"
        f"`{'░' * 18} 0%`\n"
        f"_Connecting..._"
    )

    # Animated progress ticker while upload happens
    async def ticker():
        steps = [
            (4,  6,  "22%",  "Uploading..."),
            (5,  9,  "50%",  "Uploading..."),
            (6,  13, "72%",  "Almost done..."),
            (6,  16, "89%",  "Finishing..."),
        ]
        for delay, fill, pct, label in steps:
            await asyncio.sleep(delay)
            bar = "█" * fill + "░" * (18 - fill)
            await safe_edit(
                prog_msg,
                f"☁️ **Uploading to Catbox...**\n"
                f"📁 `{name}`\n"
                f"`{bar} {pct}`\n"
                f"_{human_size(size)} — {label}_"
            )

    tick_task = asyncio.create_task(ticker())

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            with open(path, "rb") as f:
                form = aiohttp.FormData()
                form.add_field("reqtype",  "fileupload")
                form.add_field("userhash", CATBOX_USERHASH)
                form.add_field(
                    "fileToUpload", f,
                    filename=os.path.basename(path),
                    content_type="application/octet-stream",
                )
                async with session.post(CATBOX_API, data=form) as resp:
                    result = (await resp.text()).strip()
    finally:
        tick_task.cancel()

    return result


# ════════════════════════════════════════
#  CORE UPLOAD PIPELINE
# ════════════════════════════════════════
async def handle_upload(client: Client, orig_msg: Message, kind: str):
    """Full pipeline: detect → download → upload → reply link"""

    user  = orig_msg.from_user
    uname = f"@{user.username}" if user and user.username else (
            user.first_name if user else "Unknown")

    # ── Detect media and name ──
    media = None
    name  = "file"
    size  = 0

    if kind == "video":
        if orig_msg.video:
            media = orig_msg.video
            name  = media.file_name or f"video_{media.file_id[:8]}.mp4"
            size  = media.file_size or 0
        elif orig_msg.document:
            media = orig_msg.document
            name  = media.file_name or f"video_{media.file_id[:8]}.mp4"
            size  = media.file_size or 0
            if not any(name.lower().endswith(e) for e in
                       [".mp4",".mkv",".avi",".mov",".webm",".flv"]):
                name += ".mp4"
        elif orig_msg.animation:
            media = orig_msg.animation
            name  = media.file_name or f"anim_{media.file_id[:8]}.mp4"
            size  = media.file_size or 0

    elif kind == "image":
        if orig_msg.photo:
            media = orig_msg.photo
            name  = f"photo_{orig_msg.id}.jpg"
            size  = media.file_size or 0
        elif orig_msg.document:
            media = orig_msg.document
            name  = media.file_name or f"image_{media.file_id[:8]}.jpg"
            size  = media.file_size or 0

    if not media:
        return

    # ── Size check (only if Telegram told us the size) ──
    if size > 0 and size > MAX_BYTES:
        await orig_msg.reply_text(
            f"❌ **Too large!**\n"
            f"`{human_size(size)}` > limit `{MAX_MB} MB`\n\n"
            f"_Catbox free limit is 200 MB._"
        )
        return

    # ── Reply progress msg to original ──
    prog = await orig_msg.reply_text(
        f"⏳ **Starting...**\n📁 `{name}`"
    )

    dest = os.path.join(TEMP_DIR, f"{orig_msg.id}_{prog.id}_{name}")

    try:
        # Phase 1 — MTProto download
        await pyro_download(orig_msg, dest, prog, name)

        real_size = os.path.getsize(dest)

        if real_size > MAX_BYTES:
            await safe_edit(
                prog,
                f"❌ **File too large!**\n"
                f"`{human_size(real_size)}` > `{MAX_MB} MB`"
            )
            return

        # Phase 2 — Catbox upload
        url = await catbox_upload(dest, prog, name)

        # Phase 3 — Done!
        if url.startswith("https://"):
            em = "🎬" if kind == "video" else "🖼️"
            await safe_edit(
                prog,
                f"{em} **Done!**\n\n"
                f"📁 `{name}`\n"
                f"📦 `{human_size(real_size)}`\n\n"
                f"🔗 `{url}`\n\n"
                f"_Tap to copy 👆_"
            )
            log.info(f"✅ {uname} | {name} → {url}")

            # Admin notification
            if orig_msg.chat.id != ADMIN_ID:
                try:
                    await client.send_message(
                        ADMIN_ID,
                        f"{em} **New Upload**\n"
                        f"👤 {uname}\n"
                        f"📁 `{name}`\n"
                        f"📦 `{human_size(real_size)}`\n"
                        f"🔗 `{url}`"
                    )
                except Exception:
                    pass
        else:
            await safe_edit(
                prog,
                f"❌ **Catbox Error!**\n`{url}`\n\n"
                f"_Check CATBOX\\_USERHASH in Railway vars._"
            )

    except Exception as e:
        log.exception(f"Upload error: {name}")
        await safe_edit(prog, f"❌ **Error!**\n`{str(e)[:300]}`")
    finally:
        if os.path.exists(dest):
            os.remove(dest)


# ════════════════════════════════════════
#  PYROGRAM HANDLERS
# ════════════════════════════════════════

@app.on_message(filters.command(["start", "help"]))
async def cmd_start(client: Client, msg: Message):
    await msg.reply_text(
        "🐈 **Catbox Upload Bot**\n\n"
        "Send me:\n"
        "• 🎬 **Video** (up to 200 MB) → direct `.mp4` link\n"
        "• 🖼️ **Photo / Image** → direct link\n"
        "• 📎 **File** (video as document) → also works!\n\n"
        "⚡ Multiple files = **simultaneous uploads!**\n"
        "💬 Links reply to your original message.\n"
        "🔗 Catbox links are **permanent.**"
    )


@app.on_message(filters.command("stats") & filters.user(ADMIN_ID))
async def cmd_stats(client: Client, msg: Message):
    try:
        tmp = len(os.listdir(TEMP_DIR))
    except Exception:
        tmp = 0
    await msg.reply_text(
        f"📊 **Stats**\n"
        f"🗂 Temp files: `{tmp}`\n"
        f"📦 Max: `{MAX_MB} MB`"
    )


# VIDEO — inline player
@app.on_message(filters.video & ~filters.edited)
async def on_video(client: Client, msg: Message):
    asyncio.create_task(handle_upload(client, msg, "video"))


# ANIMATION / GIF
@app.on_message(filters.animation & ~filters.edited)
async def on_animation(client: Client, msg: Message):
    asyncio.create_task(handle_upload(client, msg, "video"))


# PHOTO — compressed
@app.on_message(filters.photo & ~filters.edited)
async def on_photo(client: Client, msg: Message):
    asyncio.create_task(handle_upload(client, msg, "image"))


# DOCUMENT — video or image sent as file
@app.on_message(filters.document & ~filters.edited)
async def on_document(client: Client, msg: Message):
    doc  = msg.document
    mime = doc.mime_type or ""
    if mime.startswith("video/"):
        asyncio.create_task(handle_upload(client, msg, "video"))
    elif mime.startswith("image/"):
        asyncio.create_task(handle_upload(client, msg, "image"))
    else:
        await msg.reply_text(
            "⚠️ **Unsupported file!**\nSend **videos** or **images** only."
        )


# ════════════════════════════════════════
#  START
# ════════════════════════════════════════
if __name__ == "__main__":
    log.info(f"🐈 Catbox Bot (Pyrogram) | Admin:{ADMIN_ID} | Max:{MAX_MB}MB")
    app.run()
