#!/usr/bin/env python3
"""
╔══════════════════════════════════════════╗
║     CATBOX TELEGRAM UPLOAD BOT 🐈        ║
║  Videos (200MB) + Images — Direct Links  ║
║  Railway Ready — ENV vars config         ║
╚══════════════════════════════════════════╝
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
#  CONFIG — reads from Railway ENV variables
# ─────────────────────────────────────────
BOT_TOKEN       = os.environ.get("BOT_TOKEN", "")
CATBOX_USERHASH = os.environ.get("CATBOX_USERHASH", "")
ADMIN_ID        = int(os.environ.get("ADMIN_ID", "0"))
TEMP_DIR        = "/tmp/catbox_uploads"   # /tmp works on Railway
MAX_MB          = 200
CATBOX_API      = "https://catbox.moe/user/api.php"
# ─────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)
os.makedirs(TEMP_DIR, exist_ok=True)


# ════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════
def human_size(b: int) -> str:
    for u in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"


def progress_bar(done: int, total: int, w: int = 18) -> str:
    if total == 0:
        return "░" * w + " 0%"
    filled = int(w * done / total)
    pct = done * 100 / total
    return "█" * filled + "░" * (w - filled) + f" {pct:.0f}%"


# ════════════════════════════════════════
#  DOWNLOAD from Telegram
# ════════════════════════════════════════
async def tg_download(file_obj, dest: str, prog_msg: Message, name: str):
    tg_file = await file_obj.get_file()
    total   = tg_file.file_size or 0
    done    = 0
    last_t  = 0

    async with aiofiles.open(dest, "wb") as f:
        async with aiohttp.ClientSession() as s:
            async with s.get(tg_file.file_path) as r:
                async for chunk in r.content.iter_chunked(131072):  # 128KB
                    await f.write(chunk)
                    done += len(chunk)
                    now = time.time()
                    if now - last_t > 1.5:
                        bar = progress_bar(done, total)
                        try:
                            await prog_msg.edit_text(
                                f"📥 *Downloading...*\n"
                                f"📁 `{name}`\n"
                                f"`{bar}`\n"
                                f"_{human_size(done)} / {human_size(total)}_",
                                parse_mode=ParseMode.MARKDOWN,
                            )
                        except Exception:
                            pass
                        last_t = now


# ════════════════════════════════════════
#  UPLOAD to Catbox
# ════════════════════════════════════════
async def catbox_upload(path: str, prog_msg: Message, name: str) -> str:
    size = os.path.getsize(path)

    async def upd(txt):
        try:
            await prog_msg.edit_text(txt, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

    await upd(
        f"☁️ *Uploading to Catbox...*\n"
        f"📁 `{name}`\n"
        f"`{'░' * 18} 0%`\n"
        f"_Connecting..._"
    )

    async with aiohttp.ClientSession() as session:
        with open(path, "rb") as f:
            form = aiohttp.FormData()
            form.add_field("reqtype", "fileupload")
            form.add_field("userhash", CATBOX_USERHASH)
            form.add_field(
                "fileToUpload", f,
                filename=os.path.basename(path),
                content_type="application/octet-stream",
            )

            # Catbox doesn't support streaming progress on upload side
            # so we just show an animated status
            async with session.post(CATBOX_API, data=form) as resp:
                # While waiting, show uploading bar (approx)
                await upd(
                    f"☁️ *Uploading to Catbox...*\n"
                    f"📁 `{name}`\n"
                    f"`{'█' * 9 + '░' * 9} 50%`\n"
                    f"_{human_size(size)} — please wait..._"
                )
                result = (await resp.text()).strip()

    return result


# ════════════════════════════════════════
#  CORE UPLOAD FLOW
# ════════════════════════════════════════
async def handle_upload(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    file_obj,
    name: str,
    size: int,
    orig_msg: Message,
    kind: str,  # "video" | "image"
):
    # Size guard
    if size > MAX_MB * 1024 * 1024:
        await orig_msg.reply_text(
            f"❌ *Too large!*\n"
            f"`{human_size(size)}` > limit `{MAX_MB} MB`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    prog = await orig_msg.reply_text(
        f"⏳ *Starting...*\n📁 `{name}`",
        parse_mode=ParseMode.MARKDOWN,
    )

    dest = os.path.join(TEMP_DIR, f"{prog.message_id}_{name}")

    try:
        # Phase 1 — Download
        await tg_download(file_obj, dest, prog, name)

        # Phase 2 — Upload
        url = await catbox_upload(dest, prog, name)

        # Phase 3 — Result
        if url.startswith("https://"):
            em = "🎬" if kind == "video" else "🖼️"
            await prog.edit_text(
                f"{em} *Done!*\n\n"
                f"📁 `{name}`\n"
                f"📦 `{human_size(size)}`\n\n"
                f"🔗 `{url}`\n\n"
                f"_Tap the link above to copy!_ 👆",
                parse_mode=ParseMode.MARKDOWN,
            )
            # Admin log
            user = update.effective_user
            uname = f"@{user.username}" if user.username else user.first_name
            if update.effective_chat.id != ADMIN_ID:
                try:
                    await ctx.bot.send_message(
                        ADMIN_ID,
                        f"{em} *New Upload*\n"
                        f"👤 {uname}\n"
                        f"📁 `{name}`\n"
                        f"🔗 `{url}`",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception:
                    pass
        else:
            await prog.edit_text(
                f"❌ *Catbox Error*\n`{url}`",
                parse_mode=ParseMode.MARKDOWN,
            )

    except Exception as e:
        log.exception("Upload failed")
        try:
            await prog.edit_text(
                f"❌ *Error!*\n`{e}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
    finally:
        if os.path.exists(dest):
            os.remove(dest)


# ════════════════════════════════════════
#  TELEGRAM HANDLERS
# ════════════════════════════════════════
async def cmd_start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🐈 *Catbox Upload Bot*\n\n"
        "Send me:\n"
        "• 🎬 *Video* (max 200 MB) → direct `.mp4` link\n"
        "• 🖼️ *Image/Photo* → direct link\n\n"
        "⚡ Multiple files = uploaded *simultaneously!*\n"
        "🔗 Links are permanent on catbox.moe",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_stats(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    tmp_files = len(os.listdir(TEMP_DIR))
    await update.message.reply_text(
        f"📊 *Bot Stats*\n\n"
        f"🗂 Temp files active: `{tmp_files}`\n"
        f"📦 Max size: `{MAX_MB} MB`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def on_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg   = update.message
    video = msg.video
    name  = video.file_name or f"video_{video.file_id[:8]}.mp4"
    asyncio.create_task(
        handle_upload(ctx=ctx, update=update, file_obj=video,
                      name=name, size=video.file_size or 0,
                      orig_msg=msg, kind="video")
    )


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg   = update.message
    photo = msg.photo[-1]
    name  = f"photo_{photo.file_id[:8]}.jpg"
    asyncio.create_task(
        handle_upload(ctx=ctx, update=update, file_obj=photo,
                      name=name, size=photo.file_size or 0,
                      orig_msg=msg, kind="image")
    )


async def on_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg  = update.message
    doc  = msg.document
    mime = doc.mime_type or ""
    name = doc.file_name or doc.file_id[:12]
    size = doc.file_size or 0

    if mime.startswith("video/"):
        kind = "video"
    elif mime.startswith("image/"):
        kind = "image"
    else:
        await msg.reply_text(
            "⚠️ Only *videos* and *images* supported!",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    asyncio.create_task(
        handle_upload(ctx=ctx, update=update, file_obj=doc,
                      name=name, size=size, orig_msg=msg, kind=kind)
    )


# ════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════
def main():
    if not BOT_TOKEN:
        raise ValueError("❌ BOT_TOKEN env variable not set!")
    if not CATBOX_USERHASH:
        raise ValueError("❌ CATBOX_USERHASH env variable not set!")
    if ADMIN_ID == 0:
        raise ValueError("❌ ADMIN_ID env variable not set!")

    log.info(f"🐈 Catbox Bot starting | Admin: {ADMIN_ID}")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_start))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(MessageHandler(filters.VIDEO,        on_video))
    app.add_handler(MessageHandler(filters.PHOTO,        on_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))

    log.info("✅ Polling started!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
