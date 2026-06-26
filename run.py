import os
import sys
import asyncio
import logging
from dotenv import load_dotenv
from aiohttp import web

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
    force=True
)

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

load_dotenv()
HOST = os.getenv("SERVER_HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", os.getenv("SERVER_PORT", "8080")))

from bot import app as bot_app
from server import create_app

async def main():
    print(f"Starting web server on {HOST}:{PORT}...")
    web_app = create_app(bot_app)
    web_app['pyrogram_sem'] = asyncio.Semaphore(3)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, HOST, PORT)
    await site.start()
    print(f"Web server live on {HOST}:{PORT}")

    # Start bot AFTER web server is already accepting connections
    print("Starting Pyrogram bot...")
    await bot_app.start()
    print("Bot started. Ready to serve files.")
    
    # Keep running forever
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
