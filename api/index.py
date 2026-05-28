import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, Request
from telegram import Update
from bot import build_application, post_init

app = FastAPI()

telegram_app = build_application()
_initialized = False


async def ensure_initialized() -> None:
    global _initialized

    if not _initialized:
        await telegram_app.initialize()
        await post_init(telegram_app)
        await telegram_app.start()
        _initialized = True


@app.get("/")
async def root():
    return {"ok": True, "message": "API root is live"}


@app.get("/api/telegram")
async def healthcheck():
    await ensure_initialized()
    return {"ok": True, "message": "Telegram webhook is live"}


@app.post("/api/telegram")
async def telegram_webhook(request: Request):
    await ensure_initialized()

    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)

    await telegram_app.process_update(update)

    return {"ok": True}