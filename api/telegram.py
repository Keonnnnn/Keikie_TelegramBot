import json
from telegram import Update
from bot_app import build_application

app = build_application()
_initialized = False


async def _ensure_initialized() -> None:
    global _initialized
    if not _initialized:
        await app.initialize()
        await app.start()
        _initialized = True


async def handler(request):
    await _ensure_initialized()

    if request.method == "GET":
        return {
            "statusCode": 200,
            "headers": {"content-type": "application/json"},
            "body": json.dumps({"ok": True, "message": "Telegram webhook is live"}),
        }

    if request.method != "POST":
        return {
            "statusCode": 405,
            "headers": {"content-type": "application/json"},
            "body": json.dumps({"ok": False, "error": "Method not allowed"}),
        }

    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.process_update(update)

    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json"},
        "body": json.dumps({"ok": True}),
    }