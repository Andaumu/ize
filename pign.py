#!/usr/bin/env python3
"""Bot Ping đơn giản để đo độ trễ phản hồi"""
import time
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Thay bằng token của một bot khác (tạo mới trên @BotFather)
BOT_TOKEN = "8504373990:AAFrTK10Is1KlArldSbuZB9sI8Q-UvsLkZU"

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Phản hồi lệnh /ping và tính thời gian xử lý"""
    t_start = time.time()
    msg = await update.message.reply_text("🏓 Đang đo...")
    elapsed_ms = (time.time() - t_start) * 1000
    await msg.edit_text(f"🏓 Pong!\n⏱️ Độ trễ: `{elapsed_ms:.1f} ms`", parse_mode='Markdown')

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("ping", ping))
    print("🤖 Bot Ping đang chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()