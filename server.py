import os
import json
import mimetypes
from pathlib import Path
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()
STORAGE_FILE = "file_store.json"

def load_store():
    if Path(STORAGE_FILE).exists():
        with open(STORAGE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_store(store):
    with open(STORAGE_FILE, "w") as f:
        json.dump(store, f, indent=2)

async def serve_homepage(request):
    store = load_store()
    total_files = len(store)
    total_downloads = sum(v.get("downloads", 0) for v in store.values())
    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>TG Stream Server</title>
<style>body{{font-family:monospace;background:#0a0a0a;color:#00ff88;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}</style>
</head><body>
<h1>⚡ TG Streaming Server (Pyrogram)</h1>
<p>Files Hosted: {total_files} | Total Downloads: {total_downloads}</p>
</body></html>"""
    return web.Response(text=html, content_type="text/html")

async def serve_file(request, inline=False):
    token = request.match_info.get('token')
    store = load_store()
    if token not in store:
        return web.Response(status=404, text="File not found")
        
    info = store[token]
    filename = info["filename"]
    file_size = info["file_size"]
    message_id = info["message_id"]
    chat_id = info["chat_id"]
    
    bot_app = request.app['bot']
    
    msg = await bot_app.get_messages(chat_id, message_id)
    if not msg:
        return web.Response(status=404, text="Message not found on Telegram")
        
    file_obj = msg.document or msg.video or msg.audio or msg.photo
    if not file_obj:
        return web.Response(status=404, text="File not found on Telegram")
        
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    
    disposition = "inline" if inline else "attachment"
    headers = {
        "Content-Disposition": f'{disposition}; filename="{filename}"',
        "Content-Type": mime_type,
        "Accept-Ranges": "bytes"
    }

    range_header = request.headers.get("Range")
    start_byte = 0
    end_byte = file_size - 1
    
    if range_header:
        try:
            s, e = range_header.replace("bytes=", "").split("-")
            start_byte = int(s) if s else 0
            end_byte = int(e) if e else file_size - 1
            headers["Content-Range"] = f"bytes {start_byte}-{end_byte}/{file_size}"
            response = web.StreamResponse(status=206, headers=headers)
        except Exception:
            response = web.StreamResponse(status=200, headers=headers)
    else:
        response = web.StreamResponse(status=200, headers=headers)
        
    response.content_length = end_byte - start_byte + 1
    await response.prepare(request)
    
    try:
        async for chunk in bot_app.stream_media(file_obj, offset=start_byte, limit=(end_byte - start_byte + 1)):
            await response.write(chunk)
            
        store[token]["downloads"] = store[token].get("downloads", 0) + 1
        save_store(store)
    except Exception as e:
        print(f"Stream error: {e}")
        
    return response

async def handle_stream(request):
    token = request.match_info.get('token')
    store = load_store()
    if token not in store:
        return web.Response(status=404, text="File not found")
        
    info = store[token]
    filename = info.get("filename", "")
    mime_type = mimetypes.guess_type(filename)[0] or "video/mp4"
    
    # Serve an HTML page with a video player
    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Streaming: {filename}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ margin: 0; background: #000; display: flex; justify-content: center; align-items: center; height: 100vh; overflow: hidden; color: white; font-family: sans-serif; }}
        video {{ max-width: 100%; max-height: 100vh; outline: none; }}
        .audio-container {{ text-align: center; }}
    </style>
</head>
<body>
    """
    
    if "audio" in mime_type:
        html += f"""
        <div class="audio-container">
            <h2>{filename}</h2>
            <audio controls autoplay>
                <source src="/download/{token}" type="{mime_type}">
                Your browser does not support the audio element.
            </audio>
        </div>"""
    else:
        html += f"""
        <video controls autoplay playsinline>
            <source src="/download/{token}" type="{mime_type}">
            Your browser does not support the video tag.
        </video>"""
        
    html += """
</body>
</html>"""

    return web.Response(text=html, content_type="text/html")
    app = web.Application()
    app['bot'] = bot_app
    app.router.add_get('/', serve_homepage)
    app.router.add_get('/download/{token}', handle_download)
    app.router.add_get('/stream/{token}', handle_stream)
    return app
