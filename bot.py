import os
import time
import json
from pathlib import Path
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID", "2040"))
API_HASH = os.getenv("API_HASH", "b18441a1ff607e10a989891a5462e627")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8080")
STORAGE_FILE = "file_store.json"

app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workers=4)

def load_store():
    if Path(STORAGE_FILE).exists():
        with open(STORAGE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_store(store):
    with open(STORAGE_FILE, "w") as f:
        json.dump(store, f, indent=2)

def generate_token(file_id: str) -> str:
    import hashlib
    raw = f"{file_id}:{time.time()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

@app.on_message(filters.command("start"))
async def start(client, message):
    if len(message.command) > 1:
        token = message.command[1]
        store = load_store()
        if token in store:
            try:
                await client.copy_message(
                    chat_id=message.chat.id,
                    from_chat_id=store[token]["chat_id"],
                    message_id=store[token]["message_id"]
                )
                return
            except Exception as e:
                await message.reply_text("❌ Error retrieving file.")
                return
        else:
            await message.reply_text("❌ Invalid or expired link.")
            return

    await message.reply_text(
        "👋 **File → Link Bot (Streaming Edition)**\n\n"
        "📤 Send me **any file** up to 4GB\n"
        "🔗 I'll give you a direct download link\n"
        "✅ Streams directly from Telegram!"
    )

@app.on_message(filters.document | filters.video | filters.audio | filters.photo)
async def handle_file(client, message):
    file_obj = None
    filename = "file"
    
    if message.document:
        file_obj = message.document
        filename = file_obj.file_name
    elif message.video:
        file_obj = message.video
        filename = file_obj.file_name or f"video_{file_obj.file_unique_id}.mp4"
    elif message.audio:
        file_obj = message.audio
        filename = file_obj.file_name or f"audio_{file_obj.file_unique_id}.mp3"
    elif message.photo:
        file_obj = message.photo
        filename = f"photo_{file_obj.file_unique_id}.jpg"
        
    if not file_obj:
        await message.reply_text("❌ Unsupported file type.")
        return

    msg = await message.reply_text("⏳ Generating streaming link...")
    
    try:
        token = generate_token(file_obj.file_unique_id)
        safe_filename = filename.replace("/", "_").replace("\\", "_") if filename else f"file_{file_obj.file_unique_id}"
        
        store = load_store()
        store[token] = {
            "filename": safe_filename,
            "message_id": message.id,
            "chat_id": message.chat.id,
            "file_size": getattr(file_obj, "file_size", 0),
            "file_id": getattr(file_obj, "file_id", ""),
            "uploaded_at": time.time(),
            "downloads": 0
        }
        save_store(store)
        
        download_url = f"{BASE_URL}/download/{token}"
        stream_url = f"{BASE_URL}/stream/{token}"
        bot_info = await client.get_me()
        telegram_url = f"https://t.me/{bot_info.username}?start={token}"
        
        size = getattr(file_obj, "file_size", 0) or 0
        if size > 1024**3:
            size_str = f"{size/1024**3:.1f} GB"
        elif size > 1024**2:
            size_str = f"{size/1024**2:.1f} MB"
        else:
            size_str = f"{size/1024:.1f} KB"
            
        keyboard = [
            [InlineKeyboardButton("⬇️ Download", url=download_url)],
            [InlineKeyboardButton("▶️ Stream (Browser)", url=stream_url)],
            [InlineKeyboardButton("📱 Watch in Telegram", url=telegram_url)]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard) if "localhost" not in BASE_URL and "127.0.0.1" not in BASE_URL else None
        
        await msg.edit_text(
            f"✅ **File ready!**\n\n"
            f"📄 **Name:** `{safe_filename}`\n"
            f"📦 **Size:** {size_str}\n\n"
            f"🔗 **Download:** `{download_url}`\n"
            f"🔗 **Stream:** `{stream_url}`\n"
            f"🔗 **Telegram:** `{telegram_url}`",
            reply_markup=reply_markup
        )
    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)}")
