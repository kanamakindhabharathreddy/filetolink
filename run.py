import os
import sys
import asyncio
from dotenv import load_dotenv
from aiohttp import web

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
    print("Starting bot...")
    await bot_app.start()
    print("Bot started!")
    
    web_app = create_app(bot_app)
    web_app['pyrogram_sem'] = asyncio.Semaphore(3)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, HOST, PORT)
    await site.start()
    print(f"Server running at http://{HOST}:{PORT}")
    
    # Keep running forever
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
