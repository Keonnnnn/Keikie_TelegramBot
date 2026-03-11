from fastapi import FastAPI, Request
from telegram import Update
from bot_app import build_application
from mangum import Mangum

app = FastAPI()

telegram_app = build_application()
_initialized = False


async def ensure_initialized() -> None:
    global _initialized
    if not _initialized:
        await telegram_app.initialize()
        await telegram_app.start()
        _initialized = True


@app.get("/")
async def root():
    return {"ok": True, "message": "API root is live"}


@app.get("/api/telegram")   # ← changed from /telegram
async def healthcheck():
    await ensure_initialized()
    return {"ok": True, "message": "Telegram webhook is live"}


@app.post("/api/telegram")  # ← changed from /telegram
async def telegram_webhook(request: Request):
    await ensure_initialized()
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


handler = Mangum(app)  # ← required for Vercel