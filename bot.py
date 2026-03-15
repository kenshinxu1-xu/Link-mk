#!/usr/bin/env python3
"""
╔══════════════════════════════════════════╗
║     CATBOX TELEGRAM UPLOAD BOT 🐈        ║
║  Videos (200MB) + Images — Direct Links  ║
║  Railway Ready — ENV vars config         ║
╚══════════════════════════════════════════╝
FIXES v2:
  - Forwarded videos detected properly (document handler)
  - file_size=0/None no longer causes false rejection
  - Real size checked AFTER download
  - All links reply to original video message
  - Timeout increased for large files
  - Mid-upload progress animation
"""

import os
import asyncio
import aiohttp
import aiofiles
import logging
import time
from telegram import Update, Message
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

# ─────────────────────────────────────────
BOT_TOKEN       = os.environ.get("BOT_TOKEN", "")
CATBOX_USERHASH = os.environ.get("CATBOX_USERHASH", "")
ADMIN_ID        = int(os.environ.get("ADMIN_ID", "0"))
TEMP_DIR        = "/tmp/catbox_uploads"
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


def human_size(b: int) -> str:
    if not b:
        return "? B"
    for u in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"


def progress_bar(done: int, total: int, w: int = 18) -> str:
    if total == 0:
        return "▒" * w + " ..."
    filled = max(0, min(w, int(w * done / total)))
    return "█" * filled + "░" * (w - filled) + f" {done*100//total}%"


async def safe_edit(msg: Message, text: str):
    try:
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass


async def tg_download(file_obj, dest: str, prog_msg: Message, name: str):
    timeout = aiohttp.ClientTimeout(total=600, connect=30)
    tg_file = await file_obj.get_file()
    total   = tg_file.file_size or 0
    done    = 0
    last_t  = 0.0

    async with aiofiles.open(dest, "wb") as f:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(tg_file.file_path) as r:
                async for chunk in r.content.iter_chunked(131072):
                    await f.write(chunk)
                    done += len(chunk)
                    now = time.time()
                    if now - last_t > 2.0:
                        await safe_edit(
                            prog_msg,
                            f"📥 *Downloading...*\n"
                            f"📁 `{name}`\n"
                            f"`{progress_bar(done, total)}`\n"
                            f"_{human_size(done)} / {human_size(total)}_"
                        )
                        last_t = now


async def catbox_upload(path: str, prog_msg: Message, name: str) -> str:
    size    = os.path.getsize(path)
    timeout = aiohttp.ClientTimeout(total=600, connect=30)

    await safe_edit(
        prog_msg,
        f"☁️ *Uploading to Catbox...*\n"
        f"📁 `{name}`\n"
        f"`{'░' * 18} 0%`\n"
        f"_Connecting..._"
    )

    async def progress_ticker():
        steps = [
            (4,  f"`{'█'*4 + '░'*14} 22%`\n_Uploading..._"),
            (5,  f"`{'█'*8 + '░'*10} 44%`\n_Uploading..._"),
            (5,  f"`{'█'*12 + '░'*6} 66%`\n_Almost there..._"),
            (5,  f"`{'█'*15 + '░'*3} 83%`\n_Finishing..._"),
        ]
        for delay, bar in steps:
            await asyncio.sleep(delay)
            await safe_edit(
                prog_msg,
                f"☁️ *Uploading to Catbox...*\n"
                f"📁 `{name}`\n"
                f"{bar}"
            )

    ticker = asyncio.create_task(progress_ticker())

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
        ticker.cancel()

    return result


async def handle_upload(
    update:   Update,
    ctx:      ContextTypes.DEFAULT_TYPE,
    file_obj,
    name:     str,
    size:     int,
    orig_msg: Message,
    kind:     str,
):
    user  = update.effective_user
    uname = f"@{user.username}" if user.username else user.first_name

    # Only reject if size is KNOWN and over limit (size=0 = unknown, let through)
    if size > 0 and size > MAX_BYTES:
        await orig_msg.reply_text(
            f"❌ *File too large!*\n"
            f"Size: `{human_size(size)}`\n"
            f"Catbox limit: `{MAX_MB} MB`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # This reply is linked to the original video message
    prog = await orig_msg.reply_text(
        f"⏳ *Starting...*\n📁 `{name}`",
        parse_mode=ParseMode.MARKDOWN,
    )

    dest = os.path.join(TEMP_DIR, f"{prog.message_id}_{name}")

    try:
        await tg_download(file_obj, dest, prog, name)

        real_size = os.path.getsize(dest)

        if real_size > MAX_BYTES:
            await prog.edit_text(
                f"❌ *Too large after download!*\n"
                f"`{human_size(real_size)}` > `{MAX_MB} MB`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        url = await catbox_upload(dest, prog, name)

        if url.startswith("https://"):
            em = "🎬" if kind == "video" else "🖼️"
            await prog.edit_text(
                f"{em} *Done!*\n\n"
                f"📁 `{name}`\n"
                f"📦 `{human_size(real_size)}`\n\n"
                f"🔗 `{url}`\n\n"
                f"_Tap to copy_ 👆",
                parse_mode=ParseMode.MARKDOWN,
            )
            log.info(f"✅ {uname} uploaded {name} → {url}")

            if update.effective_chat.id != ADMIN_ID:
                try:
                    await ctx.bot.send_message(
                        ADMIN_ID,
                        f"{em} *New Upload*\n👤 {uname}\n📁 `{name}`\n📦 `{human_size(real_size)}`\n🔗 `{url}`",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception:
                    pass
        else:
            await prog.edit_text(
                f"❌ *Catbox Error!*\n`{url}`",
                parse_mode=ParseMode.MARKDOWN,
            )

    except asyncio.TimeoutError:
        await safe_edit(prog, f"❌ *Timeout!* File took too long.\n📁 `{name}`")
    except Exception as e:
        log.exception(f"Error uploading {name}")
        await safe_edit(prog, f"❌ *Error!*\n`{str(e)[:300]}`")
    finally:
        if os.path.exists(dest):
            os.remove(dest)


async def cmd_start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🐈 *Catbox Upload Bot*\n\n"
        "Send me:\n"
        "• 🎬 *Video* (max 200 MB) → direct `.mp4` link\n"
        "• 🖼️ *Photo / Image* → direct link\n"
        "• 📎 *File* (video/image as document) → works too!\n\n"
        "⚡ Multiple files = *simultaneous uploads!*\n"
        "💬 Link is replied to your original message.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_stats(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    try:
        tmp = len(os.listdir(TEMP_DIR))
    except Exception:
        tmp = 0
    await update.message.reply_text(
        f"📊 *Stats*\n🗂 Temp files: `{tmp}`\n📦 Max: `{MAX_MB} MB`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def on_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg   = update.message
    video = msg.video
    if not video:
        return
    name = video.file_name or f"video_{video.file_id[:8]}.mp4"
    asyncio.create_task(
        handle_upload(update=update, ctx=ctx, file_obj=video,
                      name=name, size=video.file_size or 0,
                      orig_msg=msg, kind="video")
    )


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg   = update.message
    photo = msg.photo[-1]
    name  = f"photo_{photo.file_id[:8]}.jpg"
    asyncio.create_task(
        handle_upload(update=update, ctx=ctx, file_obj=photo,
                      name=name, size=photo.file_size or 0,
                      orig_msg=msg, kind="image")
    )


async def on_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg  = update.message
    doc  = msg.document
    mime = doc.mime_type or ""
    name = doc.file_name or f"file_{doc.file_id[:8]}"
    size = doc.file_size or 0

    if mime.startswith("video/"):
        kind = "video"
        if not any(name.lower().endswith(e) for e in [".mp4",".mkv",".avi",".mov",".webm",".flv"]):
            name += ".mp4"
    elif mime.startswith("image/"):
        kind = "image"
    else:
        await msg.reply_text(
            "⚠️ *Unsupported!* Send videos or images only.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    asyncio.create_task(
        handle_upload(update=update, ctx=ctx, file_obj=doc,
                      name=name, size=size, orig_msg=msg, kind=kind)
    )


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN env var missing!")
    if not CATBOX_USERHASH:
        raise ValueError("CATBOX_USERHASH env var missing!")
    if ADMIN_ID == 0:
        raise ValueError("ADMIN_ID env var missing!")

    log.info(f"🐈 Starting | Admin:{ADMIN_ID} | Max:{MAX_MB}MB")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_start))
    app.add_handler(CommandHandler("stats",  cmd_stats))
    app.add_handler(MessageHandler(filters.VIDEO,        on_video))
    app.add_handler(MessageHandler(filters.PHOTO,        on_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))

    log.info("✅ Polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
