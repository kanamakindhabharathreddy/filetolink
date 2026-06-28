import os
import time
import shutil
import asyncio
import logging
from aiohttp import web

logger = logging.getLogger(__name__)

HLS_DIR = "/dev/shm/hls"
if not os.path.exists("/dev/shm"):
    HLS_DIR = "/tmp/hls"

SESSIONS = {}

async def _ffmpeg_worker(token, bot_app, target_media, pyrogram_sem):
    out_dir = os.path.join(HLS_DIR, token)
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)
    
    m3u8_path = os.path.join(out_dir, "stream.m3u8")

    cmd = [
        "ffmpeg", "-y",
        "-i", "pipe:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-f", "hls",
        "-hls_time", "4",
        "-hls_playlist_type", "event",
        "-hls_flags", "delete_segments+append_list",
        "-hls_segment_type", "fmp4",
        "-hls_segment_filename", os.path.join(out_dir, "seg%d.m4s"),
        m3u8_path
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )

    SESSIONS[token] = {'process': process, 'last_accessed': time.time(), 'dir': out_dir}

    generator = bot_app.stream_media(target_media)
    
    try:
        async with pyrogram_sem:
            async for chunk in generator:
                if process.returncode is not None:
                    break
                process.stdin.write(chunk)
                await process.stdin.drain()
    except asyncio.CancelledError:
        logger.debug(f"FFmpeg worker cancelled for {token}")
    except Exception as e:
        logger.error(f"Error piping to FFmpeg for {token}: {e}")
    finally:
        try:
            process.stdin.close()
        except:
            pass
        try:
            await generator.aclose()
        except:
            pass
        
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()

async def start_hls_session(token, bot_app, target_media, pyrogram_sem):
    if token in SESSIONS:
        SESSIONS[token]['last_accessed'] = time.time()
        return
    
    asyncio.create_task(_ffmpeg_worker(token, bot_app, target_media, pyrogram_sem))

async def serve_hls_playlist(request):
    token = request.match_info.get('token')
    if token not in SESSIONS:
        return web.Response(status=404, text="HLS session not found")
    
    SESSIONS[token]['last_accessed'] = time.time()
    m3u8_path = os.path.join(SESSIONS[token]['dir'], "stream.m3u8")
    
    # Poll until m3u8 exists (user wait for first segment)
    for _ in range(20):
        if os.path.exists(m3u8_path):
            break
        await asyncio.sleep(0.5)
        
    if not os.path.exists(m3u8_path):
        return web.Response(status=500, text="HLS playlist generation timeout")
        
    return web.FileResponse(m3u8_path, headers={
        "Cache-Control": "no-cache",
        "Access-Control-Allow-Origin": "*"
    })

async def serve_hls_segment(request):
    token = request.match_info.get('token')
    segment = request.match_info.get('segment')
    
    if token not in SESSIONS:
        return web.Response(status=404, text="HLS session not found")
        
    SESSIONS[token]['last_accessed'] = time.time()
    seg_path = os.path.join(SESSIONS[token]['dir'], segment)
    
    if not os.path.exists(seg_path):
        return web.Response(status=404, text="Segment not found")
        
    return web.FileResponse(seg_path, headers={
        "Cache-Control": "public, max-age=3600",
        "Access-Control-Allow-Origin": "*"
    })

async def cleanup_worker():
    while True:
        await asyncio.sleep(15)
        now = time.time()
        to_delete = []
        for token, data in SESSIONS.items():
            if now - data['last_accessed'] > 30:  # 30 seconds idle
                to_delete.append(token)
                
        for token in to_delete:
            logger.info(f"Cleaning up idle HLS session {token}")
            data = SESSIONS.pop(token)
            try:
                data['process'].kill()
            except:
                pass
            shutil.rmtree(data['dir'], ignore_errors=True)

def setup_hls_routes(app):
    app.router.add_get('/hls/{token}/stream.m3u8', serve_hls_playlist)
    app.router.add_get('/hls/{token}/{segment}', serve_hls_segment)
    asyncio.create_task(cleanup_worker())
