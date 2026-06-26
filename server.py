import os
import json
import math
import asyncio
import logging
import mimetypes
from pathlib import Path
from aiohttp import web
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

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

MESSAGE_CACHE = {}

CHUNK_SIZE = 1048576  # Pyrogram's fixed 1 MiB chunk size

async def _pipe_stream(request, response, bot_app, target_media,
                       first_chunk_idx, num_chunks, slice_start, content_length):
    """
    Streams Pyrogram chunks to the aiohttp response.
    Isolated into its own function so the finally block can guarantee
    generator.aclose() is called regardless of how the loop exits
    (client abort, ConnectionResetError, CancelledError, or normal EOF).
    """
    generator = bot_app.stream_media(
        target_media,
        offset=first_chunk_idx,
        limit=num_chunks,
    )
    bytes_remaining = content_length
    is_first_chunk = True

    try:
        async for raw_chunk in generator:
            # Detect client disconnect before attempting a write.
            if request.transport is None or request.transport.is_closing():
                logger.debug("Client disconnected mid-stream, cleaning up generator.")
                break

            # Slice the first chunk to honour the byte-level range start.
            if is_first_chunk:
                raw_chunk = raw_chunk[slice_start:]
                is_first_chunk = False

            # Trim the last chunk so we never overshoot the requested range end.
            if len(raw_chunk) > bytes_remaining:
                raw_chunk = raw_chunk[:bytes_remaining]

            if not raw_chunk:
                break

            try:
                await response.write(raw_chunk)
            except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                # Client aborted (normal for movi-player's probing requests).
                logger.debug("Write interrupted by client, breaking stream loop.")
                break

            bytes_remaining -= len(raw_chunk)
            if bytes_remaining <= 0:
                break

    except asyncio.CancelledError:
        logger.debug("Stream handler task cancelled (server shutdown or client abort).")

    except Exception as e:
        logger.error(f"Unexpected error in _pipe_stream: {e}", exc_info=True)

    finally:
        # THE CRITICAL FIX: explicitly close the async generator.
        # This throws GeneratorExit into stream_media, causing Pyrogram to
        # cancel the in-flight GetFile RPC and release the MTProto session slot.
        # Without this, every aborted request leaks a slot until the pool deadlocks.
        try:
            await generator.aclose()
        except Exception as e:
            logger.warning(f"Error during generator.aclose(): {e}")

async def serve_file(request, inline=False):
    token = request.match_info.get('token')
    print(f"[serve_file] HIT — token={token}, range={request.headers.get('Range')}")
    store = load_store()
    if token not in store:
        return web.Response(status=404, text="File not found")
        
    info = store[token]
    filename = info["filename"]
    file_size = info["file_size"]
    message_id = info["message_id"]
    chat_id = info["chat_id"]
    
    bot_app = request.app['bot']
    
    cache_key = f"{chat_id}_{message_id}"
    msg = MESSAGE_CACHE.get(cache_key)
    if not msg:
        msg = await bot_app.get_messages(chat_id, message_id)
        if msg:
            MESSAGE_CACHE[cache_key] = msg
            
    if not msg:
        return web.Response(status=404, text="Message not found on Telegram")
        
    file_obj = msg.document or msg.video or msg.audio or msg.photo
    if not file_obj:
        return web.Response(status=404, text="File not found on Telegram")
        
    target_media = file_obj
        
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    
    disposition = "inline" if inline else "attachment"
    headers = {
        "Content-Disposition": f'{disposition}; filename="{filename}"',
        "Content-Type": mime_type,
        "Accept-Ranges": "bytes",
        "Cross-Origin-Resource-Policy": "cross-origin",
        "Access-Control-Allow-Origin": "*"
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
    
    content_length = end_byte - start_byte + 1
    first_chunk_idx = start_byte // CHUNK_SIZE
    slice_start = start_byte % CHUNK_SIZE
    num_chunks = math.ceil((slice_start + content_length) / CHUNK_SIZE)

    async with request.app['pyrogram_sem']:
        await _pipe_stream(
            request, response, bot_app, target_media,
            first_chunk_idx, num_chunks, slice_start, content_length
        )

    try:
        store[token]["downloads"] = store[token].get("downloads", 0) + 1
        save_store(store)
    except Exception as e:
        logger.warning(f"Could not update download counter: {e}")
        
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
    <script type="module" src="https://cdn.jsdelivr.net/npm/movi-player@latest/dist/index.min.js"></script>
    <style>
        body {{ margin: 0; background: #000; display: flex; justify-content: center; align-items: center; height: 100vh; overflow: hidden; color: white; font-family: sans-serif; }}
        movi-player, video {{ width: 100vw; height: 100vh; outline: none; }}
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
        <div style="position: absolute; top: 10px; left: 10px; z-index: 9999; background: rgba(0,0,0,0.7); padding: 10px; border-radius: 8px;">
            <p style="margin: 0 0 10px 0; font-size: 14px;">If video fails to play (MKV/AC3 codec limitation):</p>
            <input type="text" value="https://{request.host}/download/{token}" readonly style="width: 300px; padding: 5px; background: #222; color: #0f0; border: 1px solid #444; border-radius: 4px;" onclick="this.select()">
            <p style="margin: 10px 0 0 0; font-size: 12px; color: #aaa;">Copy this link and open in <b>VLC, MPV, or MX Player</b> (Network Stream) for full multi-audio/subtitle support.</p>
        </div>
        <movi-player src="/download/{token}" controls autoplay></movi-player>
        """
        
    html += """
</body>
</html>"""

    return web.Response(
        text=html, 
        content_type="text/html",
        headers={
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Embedder-Policy": "require-corp"
        }
    )

async def handle_download(request):
    return await serve_file(request, inline=False)

def create_app(bot_app):
    app = web.Application()
    app['bot'] = bot_app
    app.router.add_get('/', serve_homepage)
    app.router.add_get('/download/{token}', handle_download)
    app.router.add_get('/stream/{token}', handle_stream)
    return app
