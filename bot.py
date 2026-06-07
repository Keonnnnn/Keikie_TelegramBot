from multiprocessing import context
import os
import copy
import logging
from dotenv import load_dotenv
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
import re
import json
import asyncio
import base64
from openai import AsyncOpenAI

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Check your .env file or deployment environment variables.")
if not OPENROUTER_API_KEY:
    raise RuntimeError("OPENROUTER_API_KEY is missing. Check your .env file.")

openrouter_client = AsyncOpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
)

# ─────────────────────────────────────────────────────────
# Sticker
# ─────────────────────────────────────────────────────────
STICKER_ID = "CAACAgIAAxkBAAIDA2m1MG5DIROtxbH-hHIhKWtM41kJAAL3AANWnb0KC3IkHUj0DTA6BA"

# ─────────────────────────────────────────────────────────
# Singapore fixed rates
# ─────────────────────────────────────────────────────────
SG_GST     = 9.0
SG_SERVICE = 10.0


# ─────────────────────────────────────────────────────────
# States
# ─────────────────────────────────────────────────────────
(
    CHOICE,             # 0
    TOTAL,              # 1
    PEOPLE_EQUAL,       # 2
    PEOPLE_INDIV,       # 3
    NAME_INDIV,         # 4
    ITEM_COUNT,         # 5
    ITEM_NAME,          # 6
    ITEM_AMOUNT,        # 7
    REVIEW_PERSON,      # 8
    SHARED_CONFIRM,     # 9
    SHARED_NAME_AMT,    # 10
    SHARED_PEOPLE,      # 11
    TAX_CONFIRM,        # 12
    COUNTRY_SELECT,     # 13
    MANUAL_GST,         # 14
    MANUAL_SERVICE,     # 15
    EDIT_PRICE,          # 16
    RECEIPT_ADD_ITEM,        # 17
    RECEIPT_PEOPLE_COUNT,    # 18
    RECEIPT_PERSON_NAME,     # 19
   RECEIPT_ASSIGN_ITEM,     # 20
    RECEIPT_EDIT_ITEM,       # 21
) = range(22)

STATE_LABELS = {
    CHOICE:          "split type",
    TOTAL:           "total bill amount",
    PEOPLE_EQUAL:    "number of people",
    PEOPLE_INDIV:    "number of people",
    NAME_INDIV:      "person name",
    ITEM_COUNT:      "item count",
    ITEM_NAME:       "item name",
    ITEM_AMOUNT:     "item amount",
    REVIEW_PERSON:   "item review",
    SHARED_CONFIRM:  "shared items",
    SHARED_NAME_AMT: "shared item details",
    SHARED_PEOPLE:   "shared item people",
    TAX_CONFIRM:     "tax selection",
    COUNTRY_SELECT:  "country selection",
    MANUAL_GST:      "GST percentage",
    MANUAL_SERVICE:  "service charge percentage",
    EDIT_PRICE:      "edit item price",
    RECEIPT_ADD_ITEM: "add missing receipt item",
    RECEIPT_PEOPLE_COUNT: "number of people for receipt split",
    RECEIPT_PERSON_NAME: "receipt person name",
    RECEIPT_ASSIGN_ITEM: "assign receipt item",
    }


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def fmt(amount: float) -> str:
    return f"${amount:.2f}"

def md_escape(text: str) -> str:
    """Escape Telegram Markdown v1 special characters in plain text."""
    for ch in ("*", "_", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text

def clean_receipt_item_name(name: str) -> str:
    name = name.strip()

    # Remove leading symbols
    name = re.sub(r"^[\W_]+", "", name)

    # Remove noisy prefixes before quantity markers
    name = re.sub(r"^.*?\b\d+\s*[xX]\s*", "", name)

    # Remove single-letter / OCR junk prefixes
    name = re.sub(r"^(ss|oie|ioe|mm|a me|wei|nk|st)\s*\|?\s*", "", name, flags=re.IGNORECASE)

    # Remove leftover quantity markers
    name = re.sub(r"^\d+\s*[xX]\s*", "", name)
    name = re.sub(r"^\d+\s+", "", name)

    # Remove common modifier fragments
    name = re.sub(r"\bDine[- ]?In\b", "", name, flags=re.IGNORECASE)

    # Normalize spacing
    name = re.sub(r"\s+", " ", name)

    return name.strip(" -|")

def should_skip_receipt_line(name: str, amount: float) -> bool:
    lower = name.lower().strip()

    blacklist = [
        "subtotal", "sub total", "total", "grand",
        "cash", "balance", "balarce", "change", "paid",
        "receipt", "opening", "thank", "gst", "service",
        "company", "reg", "server", "table", "date",
        "visa", "tel", "dine-in", "dine in",
    ]

    if amount <= 0:
        return True

    if any(word in lower for word in blacklist):
        return True

    if lower.startswith("+"):
        return True

    if re.fullmatch(r"\d+\s*[xX]?", lower):
        return True

    if len(lower) < 3:
        return True

    return False

def action_bar() -> list:
    """Bottom row with Back / Restart / Exit — appended to any keyboard."""
    return [[
        InlineKeyboardButton("⬅️ Back",    callback_data="action_back"),
        InlineKeyboardButton("🔄 Restart", callback_data="action_restart"),
        InlineKeyboardButton("✖️ Exit",    callback_data="action_exit"),
    ]]


def yn_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes", callback_data="shared_yes"),
            InlineKeyboardButton("❌ No",  callback_data="shared_no"),
        ],
        *action_bar(),
    ])


def split_type_keyboard() -> InlineKeyboardMarkup:
    # No action bar here — it's the very first step
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚖️  Equal",      callback_data="split_equal")],
        [InlineKeyboardButton("🧮  Individual", callback_data="split_individual")],
    ])


def done_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➗  Split another bill",  callback_data="cmd_split")],
        [InlineKeyboardButton("🌐  Visit keonshu.com",   url="https://keonshu.com")],
        [InlineKeyboardButton("👋  That's all for now!", callback_data="done_bye")],
    ])


def receipt_done_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️  Edit GST / Service charge", callback_data="receipt_edit_taxes")],
        [InlineKeyboardButton("➗  Split another bill",         callback_data="cmd_split")],
        [InlineKeyboardButton("🌐  Visit keonshu.com",          url="https://keonshu.com")],
        [InlineKeyboardButton("👋  That's all for now!",        callback_data="done_bye")],
    ])

# (label, tax_rate, service_rate, tax_name)
KNOWN_COUNTRIES = {
    "sg": ("🇸🇬 Singapore", 9.0,  10.0, "GST"),
    "my": ("🇲🇾 Malaysia",  6.0,  10.0, "SST"),
    "au": ("🇦🇺 Australia", 10.0,  0.0, "GST"),
    "uk": ("🇬🇧 UK",        20.0,  0.0, "VAT"),
    "jp": ("🇯🇵 Japan",     10.0,  0.0, "Consumption Tax"),
}


def receipt_tax_type_keyboard(selected: list) -> InlineKeyboardMarkup:
    gst_tick     = "☑️" if "gst"     in selected else "☐"
    service_tick = "☑️" if "service" in selected else "☐"
    rows = [
        [InlineKeyboardButton(f"{gst_tick}  GST / VAT / Tax",   callback_data="rtax_toggle_gst")],
        [InlineKeyboardButton(f"{service_tick}  Service Charge", callback_data="rtax_toggle_service")],
        [InlineKeyboardButton("❌  No taxes",                    callback_data="rtax_none")],
    ]
    if selected:
        rows.append([InlineKeyboardButton("✅  Confirm", callback_data="rtax_tax_confirm")])
    else:
        rows.append([InlineKeyboardButton("— Select taxes or choose No taxes —", callback_data="rtax_noop")])
    return InlineKeyboardMarkup(rows)


def receipt_country_keyboard(need_gst: bool, need_service: bool) -> InlineKeyboardMarkup:
    rows = []
    for code, (label, gst, service, tax_name) in KNOWN_COUNTRIES.items():
        parts = []
        if need_gst and gst:
            parts.append(f"{tax_name} {gst:.0f}%")
        if need_service and service:
            parts.append(f"Service {service:.0f}%")
        if parts:
            rows.append([InlineKeyboardButton(f"{label}  ({', '.join(parts)})", callback_data=f"rtax_country_{code}")])
    rows.append([InlineKeyboardButton("🌍  Other (custom rates)", callback_data="rtax_country_other")])
    return InlineKeyboardMarkup(rows)


def receipt_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm items", callback_data="receipt_confirm")],
        [InlineKeyboardButton("✏️ Edit item",      callback_data="receipt_edit_item")],
        [InlineKeyboardButton("🧾 Edit taxes",     callback_data="receipt_edit_taxes_manual")],
        [InlineKeyboardButton("➕ Add missing item", callback_data="receipt_add_item")],
        [InlineKeyboardButton("📸 Retry receipt",  callback_data="receipt_retry")],
    ])

def tax_keyboard(selected: list) -> InlineKeyboardMarkup:
    gst_tick     = "☑️" if "gst"     in selected else "☐"
    service_tick = "☑️" if "service" in selected else "☐"
    rows = [
        [InlineKeyboardButton(f"{gst_tick}  Tax (GST / VAT / SST)",  callback_data="tax_toggle_gst")],
        [InlineKeyboardButton(f"{service_tick}  Service Charge",      callback_data="tax_toggle_service")],
        [InlineKeyboardButton("❌  No taxes",                         callback_data="tax_none")],
    ]
    if selected:
        rows.append([InlineKeyboardButton("✅  Confirm", callback_data="tax_confirm")])
    else:
        rows.append([InlineKeyboardButton("— Select taxes or choose No taxes —", callback_data="tax_noop")])
    rows += action_bar()
    return InlineKeyboardMarkup(rows)


def country_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🇸🇬  Singapore  (GST {SG_GST:.0f}%, Service {SG_SERVICE:.0f}%)", callback_data="country_sg")],
        [InlineKeyboardButton("🌍  Other countries", callback_data="country_other")],
        *action_bar(),
    ])


def review_keyboard(items: list) -> InlineKeyboardMarkup:
    rows = []
    for i, (iname, iamt) in enumerate(items):
        rows.append([
            InlineKeyboardButton(f"✏️ {iname}", callback_data=f"review_edit_{i}"),
            InlineKeyboardButton(f"🗑️ {iname}", callback_data=f"review_remove_{i}"),
        ])
    rows.append([InlineKeyboardButton("✅  Confirm & continue", callback_data="review_done")])
    rows += action_bar()
    return InlineKeyboardMarkup(rows)


def sharers_keyboard(names: list, selected: list) -> InlineKeyboardMarkup:
    rows = []
    for name in names:
        tick = "☑️" if name in selected else "☐"
        rows.append([InlineKeyboardButton(
            f"{tick}  {name}", callback_data=f"sharer_toggle_{name}"
        )])
    if selected:
        rows.append([InlineKeyboardButton("✅  Confirm", callback_data="sharer_confirm")])
    else:
        rows.append([InlineKeyboardButton("— Select at least one person —", callback_data="sharer_noop")])
    rows += action_bar()
    return InlineKeyboardMarkup(rows)

def receipt_single_assign_keyboard(people: list, selected: list, index: int) -> InlineKeyboardMarkup:
    rows = []
    person_buttons = []
    for j, person in enumerate(people):
        tick = "✅" if person in selected else "☐"
        person_buttons.append(
            InlineKeyboardButton(f"{tick} {person}", callback_data=f"bulk_t_{j}")
        )
    for i in range(0, len(person_buttons), 3):
        rows.append(person_buttons[i:i + 3])
    nav = []
    if index > 0:
        nav.append(InlineKeyboardButton("⬅️ Back", callback_data="bulk_back"))
    if selected:
        nav.append(InlineKeyboardButton("➡️ Confirm", callback_data="bulk_done"))
    else:
        nav.append(InlineKeyboardButton("— Select at least one —", callback_data="bulk_noop"))
    rows.append(nav)
    return InlineKeyboardMarkup(rows)


def receipt_review_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Generate split", callback_data="bulk_generate")],
        [InlineKeyboardButton("↩️ Redo from Item 1", callback_data="bulk_reassign")],
    ])


def single_assign_message(item_name: str, amount: float, index: int, total: int) -> str:
    progress_bar = "●" * (index + 1) + "○" * (total - index - 1)
    return (
        f"*Item {index + 1} of {total}* | {progress_bar}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🍽️ *{md_escape(item_name)}*\n"
        f"💵 {fmt(amount)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Who had this?"
    )


def review_assignments_message(items: list, assignments: dict) -> str:
    lines = ["📋 *Review your assignments:*\n", "━━━━━━━━━━━━━━━━━━━━━"]
    for i, (name, amount) in enumerate(items):
        people = assignments.get(i, [])
        people_str = " & ".join(people) if people else "—"
        lines.append(f"\n🍽️ *{name}*")
        lines.append(f"💵 {fmt(amount)}  →  👤 {people_str}")
    lines.append("\n━━━━━━━━━━━━━━━━━━━━━")
    lines.append("Looks good?")
    return "\n".join(lines)

def receipt_edit_keyboard(items: list) -> InlineKeyboardMarkup:
    rows = []

    for i, (name, amount) in enumerate(items):
        rows.append([
            InlineKeyboardButton(
                f"✏️ {i + 1}. {name}",
                callback_data=f"receipt_edit_{i}"
            )
        ])

    rows.append([
        InlineKeyboardButton("⬅️ Back", callback_data="receipt_edit_back")
    ])

    return InlineKeyboardMarkup(rows)

def progress(data: dict) -> str:
    idx   = data.get("current_person_index", 0)
    total = data.get("people", 0)
    return f"[{idx + 1}/{total}] " if total else ""


# ─────────────────────────────────────────────────────────
# Standalone action bar markup (for text-input steps)
# ─────────────────────────────────────────────────────────

ACTION_BAR_MARKUP = InlineKeyboardMarkup(action_bar())


# ─────────────────────────────────────────────────────────
# Logging incoming messages
# ─────────────────────────────────────────────────────────

async def handle_receipt_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.photo:
        return

    # Clear all receipt-related state to avoid stale data from previous sessions
    for key in [
        "receipt_items", "receipt_people", "receipt_people_count",
        "receipt_person_index", "receipt_stage", "receipt_assign_index",
        "receipt_assignments", "receipt_gst", "receipt_service",
        "receipt_tax_name", "receipt_need_gst", "receipt_need_service",
        "receipt_tax_stage", "receipt_edit_index", "adding_receipt_item",
        "bulk_assignments", "bulk_index", "bulk_selected",
        "rtax_selected",
    ]:
        context.user_data.pop(key, None)

    photo = update.message.photo[-1]

    await update.message.reply_text("📸 Got it! Reading your receipt...")

    file = await context.bot.get_file(photo.file_id)
    buf = bytearray()
    await file.download_as_bytearray(buf)
    image_bytes = bytes(buf)

    try:
        await update.message.reply_text("🔍 Detecting items...")

        prompt = """You are a receipt parser. Look at this receipt image and extract the ordered food/drink items and any taxes or service charges.

Return a JSON object with this exact structure:
{
  "items": [{"name": "Chicken Rice", "price": 5.50}, ...],
  "gst": 9.0,
  "gst_inclusive": false,
  "service_charge": 10.0,
  "service_inclusive": false
}

Rules for items:
- Ignore subtotals, totals, GST, service charge, taxes, table numbers, server names, dates, addresses, payment methods
- Each item should have a clean name and its price in dollars
- If a quantity is shown (e.g. "2x Escargots $11.80"), keep it as ONE entry with the full price (e.g. {"name": "Escargots (x2)", "price": 11.80}) — do NOT split into separate entries
- If an item has add-ons or modifiers with a price (e.g. "+ Upsize $1.00"), add that cost to the parent item's total price — do NOT list modifiers as separate items
- Ignore add-ons that cost $0.00

Rules for taxes:
- Look for GST, VAT, SST, tax, or similar — return the percentage as a number (e.g. 9.0 for 9%)
- For GST percentage: if the receipt is from Singapore (look for "GST Reg No", "SGD", "Singapore", or .sg addresses), ALWAYS use exactly 9.0 regardless of what calculated percentage appears on the receipt. Do NOT derive the GST percentage from the receipt totals.
- To determine gst_inclusive: add up subtotal + service_charge_amount + gst_amount and check if it equals the final total. If yes, GST is a separate charge (gst_inclusive: false). Only set gst_inclusive to true if there is NO GST line in the totals section at all AND the receipt explicitly states items are priced with GST already included (e.g. "GST inclusive pricing", "all prices include GST"). The phrase "Price payable includes GST" does NOT mean gst_inclusive — it just means the final bill total has GST in it.
- If the receipt adds GST as a line in the totals section (even if shown in parentheses like "(GST 9.28)"), that means GST is charged separately — set gst_inclusive to false
- Same logic for service charge — set service_inclusive to true if already included in prices
- If not found or not applicable, return 0 for the percentage

Return ONLY the raw JSON object. No explanation, no markdown."""

        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        response = None
        for attempt in range(3):
            try:
                response = await openrouter_client.chat.completions.create(
                    model="google/gemini-2.5-flash",
                    max_tokens=4096,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                            {"type": "text", "text": prompt},
                        ]
                    }],
                )
                break
            except Exception as retry_err:
                if attempt < 2:
                    await asyncio.sleep(5)
                else:
                    raise retry_err

        raw_json = response.choices[0].message.content.strip()
        logging.info("AI raw response: %s", raw_json[:500])
        # Strip markdown code fences robustly
        raw_json = re.sub(r"^```(?:json)?\s*", "", raw_json, flags=re.IGNORECASE)
        raw_json = re.sub(r"\s*```\s*$", "", raw_json, flags=re.DOTALL)
        raw_json = raw_json.strip()
        # Extract JSON object if extra text surrounds it
        json_match = re.search(r"\{[\s\S]*\}", raw_json)
        if json_match:
            raw_json = json_match.group(0)

        parsed = json.loads(raw_json)

        # Support both old array format and new object format
        if isinstance(parsed, list):
            detected_items     = [(item["name"], float(item["price"])) for item in parsed if item.get("name") and item.get("price")]
            detected_gst       = 0.0
            detected_service   = 0.0
            gst_inclusive      = False
            service_inclusive  = False
        else:
            detected_items     = [(item["name"], float(item["price"])) for item in parsed.get("items", []) if item.get("name") and item.get("price")]
            detected_gst       = float(parsed.get("gst", 0) or 0)
            detected_service   = float(parsed.get("service_charge", 0) or 0)
            gst_inclusive      = bool(parsed.get("gst_inclusive", False))
            service_inclusive  = bool(parsed.get("service_inclusive", False))

        # Only apply taxes that are NOT already included in item prices
        apply_gst     = detected_gst     if not gst_inclusive     else 0.0
        apply_service = detected_service if not service_inclusive else 0.0

        if detected_items:
            context.user_data["receipt_items"]   = detected_items
            context.user_data["receipt_gst"]     = apply_gst
            context.user_data["receipt_service"] = apply_service
            context.user_data["receipt_tax_name"] = "GST"

            msg = "🧾 Detected items:\n\n"
            for i, (name, amount) in enumerate(detected_items, 1):
                msg += f"{i}. {name} — {fmt(amount)}\n"

            tax_lines = []
            if detected_gst:
                if gst_inclusive:
                    tax_lines.append(f"GST: {detected_gst:.1f}% ✅ already included in prices")
                else:
                    tax_lines.append(f"GST: {detected_gst:.1f}% (will be added on top)")
            if detected_service:
                if service_inclusive:
                    tax_lines.append(f"Service charge: {detected_service:.1f}% ✅ already included in prices")
                else:
                    tax_lines.append(f"Service charge: {detected_service:.1f}% (will be added on top)")

            if tax_lines:
                msg += "\n🧾 Detected taxes:\n" + "\n".join(f"  • {t}" for t in tax_lines)
            else:
                msg += "\n🧾 No taxes detected."

            msg += "\n\nDoes this look correct?"

            await update.message.reply_text(msg, reply_markup=receipt_confirm_keyboard())
        else:
            await update.message.reply_text(
                "⚠️ AI couldn't find any items. Try a clearer photo.",
                reply_markup=receipt_confirm_keyboard()
            )

    except json.JSONDecodeError as e:
        logging.error("Gemini returned invalid JSON: %s | raw: %s", e, raw_json[:300] if 'raw_json' in dir() else "N/A")
        await update.message.reply_text("⚠️ AI returned an unexpected response. Please try again.")
    except Exception as e:
        logging.error("Receipt processing failed: %s", e)
        await update.message.reply_text(f"⚠️ Something went wrong:\n{e}")

async def log_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    user = update.message.from_user
    name = user.username or user.first_name or "unknown"
    if update.message.sticker:
        file_id = update.message.sticker.file_id
        print(f"[{name}] sent a sticker — file_id: {file_id}")
        print(f"  👆 Copy that into STICKER_ID at the top of bot_app.py")
    elif update.message.text:
        print(f"[{name}]: {update.message.text}")


# ─────────────────────────────────────────────────────────
# Action bar handler (Back / Restart / Exit)
# ─────────────────────────────────────────────────────────

async def handle_action_bar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "action_restart":
        context.user_data.clear()
        await query.message.reply_text(
            "🔄 Restarted! How do you want to split the bill?",
            reply_markup=split_type_keyboard(),
        )
        return CHOICE

    if query.data == "action_exit":
        context.user_data.clear()
        await query.message.reply_text("✖️ Session ended. Send /split to start again.")
        return ConversationHandler.END

    if query.data == "action_back":
        history: list = context.user_data.get("_history", [])
        if not history:
            await query.answer("Nothing to go back to!", show_alert=True)
            return ConversationHandler.END

        prev_state, snapshot = history.pop()
        for k in [k for k in context.user_data if k != "_history"]:
            del context.user_data[k]
        context.user_data.update(snapshot)

        label = STATE_LABELS.get(prev_state, "previous step")
        await query.message.reply_text(
            f"⬅️ Back to: *{label}*\n\n{_re_prompt(prev_state, context.user_data)}",
            parse_mode="Markdown",
            reply_markup=_state_keyboard(prev_state, context.user_data),
        )
        return prev_state

    return ConversationHandler.END


def _state_keyboard(state: int, data: dict):
    """Return the right keyboard for a given state (used after Back)."""
    if state == CHOICE:
        return split_type_keyboard()
    if state == SHARED_CONFIRM:
        return yn_keyboard()
    if state == TAX_CONFIRM:
        return tax_keyboard(data.get("pending_taxes", []))
    if state == COUNTRY_SELECT:
        return country_keyboard()
    if state == REVIEW_PERSON:
        name  = data.get("current_name", "")
        items = data.get("amounts_by_person", {}).get(name, [])
        return review_keyboard(items)
    # Text-input states: show standalone action bar
    return ACTION_BAR_MARKUP


# ─────────────────────────────────────────────────────────
# Undo / history
# ─────────────────────────────────────────────────────────

def push_history(context: ContextTypes.DEFAULT_TYPE, state: int) -> None:
    history: list = context.user_data.setdefault("_history", [])
    snapshot = copy.deepcopy({k: v for k, v in context.user_data.items() if k != "_history"})
    history.append((state, snapshot))
    if len(history) > 20:
        history.pop(0)


def _re_prompt(state: int, data: dict) -> str:
    prompts = {
        CHOICE:          "How do you want to split the bill?",
        TOTAL:           "Enter the total bill amount:",
        PEOPLE_EQUAL:    "How many people are splitting the bill?",
        PEOPLE_INDIV:    "How many people are splitting the bill?",
        NAME_INDIV:      f"Enter all {data.get('people', '?')} names separated by commas (e.g. Alice, Bob, Charlie):",
        ITEM_COUNT:      f"How many items did {data.get('current_name', '?')} order?",
        ITEM_NAME:       f"What's the name of {data.get('current_name', '?')}'s item {data.get('current_item', '?')}?",
        ITEM_AMOUNT:     f"Enter the amount for {data.get('current_item_name', 'the item')}:",
        REVIEW_PERSON:   "Review your items above.",
        SHARED_CONFIRM:  "Do you have any shared items to add?",
        SHARED_NAME_AMT: "Enter the shared item as: Name, amount",
        SHARED_PEOPLE:   "Select who shares this item.",
        TAX_CONFIRM:     "Select which taxes apply to this bill.",
        COUNTRY_SELECT:  "Select your country.",
        MANUAL_GST:      "Enter the tax percentage (e.g. 9), or 0 for none.",
        MANUAL_SERVICE:  "Enter the service charge percentage (e.g. 10), or 0 for none.",
    }
    return prompts.get(state, "Please continue.")


# ─────────────────────────────────────────────────────────
# Summary builder
# ─────────────────────────────────────────────────────────

def build_summary(data: dict) -> str:
    lines = ["🧾 BILL SUMMARY", "━━━━━━━━━━━━━━━━━━━━━", ""]
    gst       = data.get("gst",       0.0)
    service   = data.get("service",   0.0)
    gst_label = data.get("gst_label", "GST")
    country   = data.get("country",   None)

    if data.get("split_type") == "equal":
        total  = data["total"]
        people = data["people"]
        lines += [
            "📌 Mode: Equal split",
            f"👥 People: {people}",
            f"💵 Total: {fmt(total)}",
            "",
            f"💰 Each person pays: {fmt(total / people)}",
        ]

    else:
        names        = data["names"]
        amounts_by   = data["amounts_by_person"]
        shared_items = data.get("shared_items", [])

        person_base: dict = {n: sum(a for _, a in amounts_by.get(n, [])) for n in names}
        for _iname, iamt, sharers in shared_items:
            share = iamt / len(sharers)
            for s in sharers:
                person_base[s] = person_base.get(s, 0.0) + share

        base_total     = sum(person_base.values())
        service_amount = round(base_total * service / 100, 2)
        subtotal       = base_total + service_amount
        gst_amount     = round(subtotal * gst / 100, 2)
        grand_total    = subtotal + gst_amount

        if country:
            flag = "🇸🇬" if country == "Singapore" else "🌍"
            lines.append(f"🌏 Country: {flag} {country}")
        lines += ["📌 Mode: Individual split", f"👥 People: {len(names)}", "",
                  f"  Base total :  {fmt(base_total)}"]
        if service:
            lines.append(f"  Service ({service:.1f}%)   :  {fmt(service_amount)}")
        if gst:
            padding = "  " if len(gst_label) > 3 else "     "
            lines.append(f"  {gst_label} ({gst:.1f}%){padding} :  {fmt(gst_amount)}")
        lines += [
    "  ────────────────────",
    f"  Grand total :  {fmt(grand_total)}",
    "",
    "💰 FINAL AMOUNTS TO PAY",
    "━━━━━━━━━━━━━━━━━━━━━",
]
        for name in names:
            p_base = person_base.get(name, 0.0)
            final_share = grand_total * (p_base / base_total) if base_total else 0.0

            lines += [
                "",
                f"👤 {name}",
                f"💵 Pays: {fmt(final_share)}",
                "",
            ]

            for iname, iamt in amounts_by.get(name, []):
                lines.append(f"   • {iname}: {fmt(iamt)}")

            for iname, iamt, sharers in shared_items:
                if name in sharers:
                    lines.append(
                        f"   • {iname} (shared /{len(sharers)}): {fmt(iamt / len(sharers))}"
                    )

        if name != names[-1]:
            lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━━━━━")

        if shared_items:
            lines += ["", "🔀 Shared items:"]
            for iname, iamt, sharers in shared_items:
                lines.append(f"  • {iname}  {fmt(iamt)}  → {', '.join(sharers)}")

    lines += ["", "Generated by @Keikie_Bot"]
    return "\n".join(lines)

# ─────────────────────────────────────────────────────────
# /start  /help  /restart
# ─────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_sticker(STICKER_ID)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📸  Scan receipt",   callback_data="cmd_scan")],
        [InlineKeyboardButton("✏️  Enter manually", callback_data="cmd_split_manual")],
        [InlineKeyboardButton("💡  How it works",   callback_data="cmd_help")],
    ])
    await update.message.reply_text(
        "👋 Hi! I'm *Keke*, your handy all-in-one assistant!\n\n"
        "Right now, I can help you split restaurant bills fairly — equally or by individual orders, "
        "with shared items, GST, and service charge all handled for you. 🧾\n\n"
        "More useful tools are on the way. Stay tuned! 🛠️\n\n"
        "What would you like to do?",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➗  Split a bill", callback_data="cmd_split")],
    ])
    await update.message.reply_text(
        "💡 *How Keke works*\n\n"
        "📸 *Scan receipt* — snap a photo of your receipt and Keke will read the items automatically. "
        "You can edit, add, or remove items before splitting.\n\n"
        "✏️ *Enter manually* — enter each person's name and their ordered items with prices. "
        "Optionally add shared items (e.g. a shared bottle of wine) split among specific people.",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def button_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "📸 Send me a photo of your receipt and I'll read it for you!"
    )


async def button_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➗  Split a bill", callback_data="cmd_split")],
    ])
    await query.message.reply_text(
        "💡 *How Keke works*\n\n"
        "📸 *Scan receipt* — snap a photo of your receipt and Keke will read the items automatically. "
        "You can edit, add, or remove items before splitting.\n\n"
        "✏️ *Enter manually* — enter each person's name and their ordered items with prices. "
        "Optionally add shared items (e.g. a shared bottle of wine) split among specific people.",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("🔄 Restarted. Send /split to begin.")
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────
# Done / farewell
# ─────────────────────────────────────────────────────────

async def done_bye(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.reply_sticker(STICKER_ID)
    await query.message.reply_text("See you next time! 👋")

async def receipt_people_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        count = int(update.message.text.strip())
        if count <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Enter a whole number greater than 0.")
        return RECEIPT_PEOPLE_COUNT

    context.user_data["receipt_people_count"] = count
    context.user_data["receipt_people"] = []
    context.user_data["receipt_person_index"] = 0

    await update.message.reply_text("What is Person 1's name?")
    return RECEIPT_PERSON_NAME

async def receipt_person_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()

    if not name:
        await update.message.reply_text("⚠️ Please enter a valid name.")
        return RECEIPT_PERSON_NAME

    context.user_data["receipt_people"].append(name)
    context.user_data["receipt_person_index"] += 1

    index = context.user_data["receipt_person_index"]
    total = context.user_data["receipt_people_count"]

    if index < total:
        await update.message.reply_text(f"What is Person {index + 1}'s name?")
        return RECEIPT_PERSON_NAME

    context.user_data["receipt_assign_index"] = 0
    context.user_data["receipt_assignments"] = {}

    await update.message.reply_text(
        "✅ People added. Next, we’ll assign receipt items."
    )

    return await ask_receipt_item_assignment(update.message, context)

# ─────────────────────────────────────────────────────────
# Conversation entry points
# ─────────────────────────────────────────────────────────

async def receipt_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "receipt_confirm":
        context.user_data.pop("adding_receipt_item", None)
        items = context.user_data.get("receipt_items", [])

        if not items:
            await query.message.reply_text("⚠️ No receipt items found.")
            return ConversationHandler.END

        lines = ["✅ Receipt items confirmed!", ""]

        total = 0

        for name, amount in items:
            lines.append(f"• {name} — {fmt(amount)}")
            total += amount

        lines.append("")
        lines.append(f"💰 Total detected: {fmt(total)}")

        await query.message.reply_text("\n".join(lines))

        context.user_data["receipt_stage"] = "people_names"

        await query.message.reply_text(
            "👥 Enter the names of everyone splitting the bill, separated by commas:\n\n"
            "e.g. Alice, Bob, Charlie"
        )

        return ConversationHandler.END
    
    elif data == "receipt_edit_item":
        items = context.user_data.get("receipt_items", [])

        if not items:
            await query.message.reply_text("⚠️ No items to edit.")
            return ConversationHandler.END

        await query.message.reply_text(
            "Which item do you want to edit?",
            reply_markup=receipt_edit_keyboard(items)
    )

        return ConversationHandler.END

    elif data == "receipt_add_item":
        context.user_data["adding_receipt_item"] = True

        await query.message.reply_text(
            "✏️ Enter the missing item as:\n\n"
            "Item name, amount\n\n"
            "Example:\n"
            "Water Chestnut, 2.50"
        )

        return ConversationHandler.END

    elif data == "receipt_retry":
        context.user_data.pop("adding_receipt_item", None)
        await query.message.reply_text("📸 Please upload the receipt again.")
        return ConversationHandler.END

    return ConversationHandler.END

async def receipt_edit_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "receipt_edit_back":
        items = context.user_data.get("receipt_items", [])

        response = "🧾 Detected items:\n\n"
        for i, (name, amount) in enumerate(items, 1):
            response += f"{i}. {name} — {fmt(amount)}\n"

        response += "\nDoes this look correct?"

        await query.message.reply_text(
            response,
            reply_markup=receipt_confirm_keyboard()
        )
        return ConversationHandler.END

    if data.startswith("receipt_edit_"):
        index = int(data.replace("receipt_edit_", "", 1))
        items = context.user_data.get("receipt_items", [])

        if index < 0 or index >= len(items):
            await query.message.reply_text("⚠️ Invalid item.")
            return ConversationHandler.END

        context.user_data["receipt_edit_index"] = index

        old_name, old_amount = items[index]

        await query.message.reply_text(
            f"✏️ Editing:\n{old_name} — {fmt(old_amount)}\n\n"
            f"Enter the corrected item as:\n\n"
            f"Item name, amount\n\n"
            f"Example:\n"
            f"Chicken Rice, 5.50"
        )

        return RECEIPT_EDIT_ITEM

    return ConversationHandler.END

async def receipt_edit_item_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if context.user_data.get("receipt_edit_index") is None:
        return

    try:
        parts = update.message.text.strip().rsplit(",", 1)

        if len(parts) != 2:
            raise ValueError

        name = parts[0].strip()
        amount = float(parts[1].strip())

        if not name or amount <= 0:
            raise ValueError

    except ValueError:
        await update.message.reply_text(
            "⚠️ Use this format:\n\n"
            "Item name, amount\n\n"
            "Example:\n"
            "Chicken Rice, 5.50"
        )
        return RECEIPT_EDIT_ITEM

    index = context.user_data.get("receipt_edit_index")
    items = context.user_data.get("receipt_items", [])

    if index < 0 or index >= len(items):
        context.user_data.pop("receipt_edit_index", None)
        await update.message.reply_text("⚠️ Could not find item to edit.")
        return ConversationHandler.END

    items[index] = (name, amount)
    context.user_data.pop("receipt_edit_index", None)

    response = "🧾 Updated detected items:\n\n"

    for i, (item_name, item_amount) in enumerate(items, 1):
        response += f"{i}. {item_name} — {fmt(item_amount)}\n"

    response += "\nDoes this look correct?"

    await update.message.reply_text(
        response,
        reply_markup=receipt_confirm_keyboard()
    )

    return ConversationHandler.END

    
async def receipt_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("receipt_tax_stage"):
        await receipt_custom_tax_input(update, context)
        return

    if context.user_data.get("receipt_edit_index") is not None:
        await receipt_edit_item_text(update, context)
        return

    await receipt_add_item_text(update, context)


async def receipt_custom_tax_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        v = float(update.message.text.strip())
        if v < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Enter a valid percentage like 9 or 0.")
        return

    stage = context.user_data.get("receipt_tax_stage")

    if stage in ("gst", "edit_gst"):
        context.user_data["receipt_gst"] = v
        context.user_data["receipt_tax_stage"] = "service" if stage == "gst" else "edit_service"
        await update.message.reply_text(
            "Enter the service charge percentage (e.g. 10), or 0 for none:"
        )
    elif stage in ("service", "edit_service"):
        context.user_data["receipt_service"] = v
        context.user_data.pop("receipt_tax_stage", None)
        if stage == "edit_service":
            # Re-show confirm screen with updated taxes
            items = context.user_data.get("receipt_items", [])
            gst     = context.user_data.get("receipt_gst", 0.0)
            service = context.user_data.get("receipt_service", 0.0)
            msg = "🧾 Updated items:\n\n"
            for i, (name, amount) in enumerate(items, 1):
                msg += f"{i}. {name} — {fmt(amount)}\n"
            tax_lines = []
            if gst:
                tax_lines.append(f"GST: {gst:.1f}%")
            if service:
                tax_lines.append(f"Service charge: {service:.1f}%")
            if tax_lines:
                msg += "\n🧾 Updated taxes:\n" + "\n".join(f"  • {t}" for t in tax_lines)
            else:
                msg += "\n🧾 No taxes."
            msg += "\n\nDoes this look correct?"
            await update.message.reply_text(msg, reply_markup=receipt_confirm_keyboard())
        else:
            await send_receipt_split_summary(update.message, context)


async def receipt_add_item_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("receipt_edit_index") is not None:
        return

    text = update.message.text.strip()

    if context.user_data.get("adding_receipt_item"):
        try:
            parts = text.rsplit(",", 1)

            if len(parts) != 2:
                raise ValueError

            name = parts[0].strip()
            amount = float(parts[1].strip())

            if not name or amount <= 0:
                raise ValueError

        except ValueError:
            await update.message.reply_text(
                "⚠️ Use this format:\n\n"
                "Item name, amount\n\n"
                "Example:\n"
                "Water Chestnut, 2.50"
            )
            return

        context.user_data.pop("adding_receipt_item", None)

        items = context.user_data.setdefault("receipt_items", [])
        items.append((name, amount))

        response = "🧾 Updated detected items:\n\n"

        for i, (item_name, item_amount) in enumerate(items, 1):
            response += f"{i}. {item_name} — {fmt(item_amount)}\n"

        response += "\nDoes this look correct?"

        await update.message.reply_text(
            response,
            reply_markup=receipt_confirm_keyboard()
        )
        return

    if context.user_data.get("receipt_stage") == "people_names":
        names = [n.strip() for n in text.split(",") if n.strip()]

        if not names:
            await update.message.reply_text(
                "⚠️ Please enter at least one name, separated by commas.\n\n"
                "e.g. Alice, Bob, Charlie"
            )
            return

        context.user_data["receipt_people"] = names
        context.user_data.pop("receipt_stage", None)
        context.user_data["receipt_assign_index"] = 0
        context.user_data["receipt_assignments"] = {}

        await update.message.reply_text("✅ People added. Next, we’ll assign receipt items.")
        await ask_receipt_item_assignment(update.message, context)
        return

async def ask_receipt_item_assignment(message_obj, context: ContextTypes.DEFAULT_TYPE) -> int:
    items = context.user_data.get("receipt_items", [])
    people = context.user_data.get("receipt_people", [])

    if not items:
        await message_obj.reply_text("⚠️ No receipt items found. Please scan your receipt again.")
        return ConversationHandler.END

    if not people:
        await message_obj.reply_text("⚠️ No people found. Please try again.")
        return ConversationHandler.END

    context.user_data["bulk_assignments"] = {}
    context.user_data["bulk_index"] = 0
    context.user_data["bulk_selected"] = []

    try:
        item_name, amount = items[0]
        await message_obj.reply_text(
            single_assign_message(item_name, amount, 0, len(items)),
            parse_mode="Markdown",
            reply_markup=receipt_single_assign_keyboard(people, [], 0),
        )
    except Exception as e:
        logging.error("ask_receipt_item_assignment failed: %s", e)
        await message_obj.reply_text("⚠️ Something went wrong displaying items. Please try scanning again.")
        return ConversationHandler.END

    return RECEIPT_ASSIGN_ITEM


async def receipt_assign_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    items = context.user_data.get("receipt_items", [])
    people = context.user_data.get("receipt_people", [])
    assignments = context.user_data.setdefault("bulk_assignments", {})
    index = context.user_data.get("bulk_index", 0)
    selected = context.user_data.get("bulk_selected", [])

    if query.data == "bulk_noop":
        return RECEIPT_ASSIGN_ITEM

    if query.data.startswith("bulk_t_"):
        person_idx = int(query.data.split("_")[2])
        person = people[person_idx]
        if person in selected:
            selected.remove(person)
        else:
            selected.append(person)
        context.user_data["bulk_selected"] = selected
        await query.edit_message_reply_markup(
            reply_markup=receipt_single_assign_keyboard(people, selected, index),
        )
        return RECEIPT_ASSIGN_ITEM

    if query.data == "bulk_back":
        prev_index = index - 1
        assignments.pop(index, None)
        context.user_data["bulk_index"] = prev_index
        prev_selected = assignments.get(prev_index, []).copy()
        context.user_data["bulk_selected"] = prev_selected
        prev_name, prev_amount = items[prev_index]
        await query.edit_message_text(
            single_assign_message(prev_name, prev_amount, prev_index, len(items)),
            parse_mode="Markdown",
            reply_markup=receipt_single_assign_keyboard(people, prev_selected, prev_index),
        )
        return RECEIPT_ASSIGN_ITEM

    if query.data == "bulk_done":
        assignments[index] = selected.copy()
        context.user_data["bulk_assignments"] = assignments
        next_index = index + 1

        if next_index < len(items):
            context.user_data["bulk_index"] = next_index
            context.user_data["bulk_selected"] = []
            next_name, next_amount = items[next_index]
            await query.edit_message_text(
                single_assign_message(next_name, next_amount, next_index, len(items)),
                parse_mode="Markdown",
                reply_markup=receipt_single_assign_keyboard(people, [], next_index),
            )
            return RECEIPT_ASSIGN_ITEM

        await query.edit_message_text(
            review_assignments_message(items, assignments),
            parse_mode="Markdown",
            reply_markup=receipt_review_keyboard(),
        )
        return RECEIPT_ASSIGN_ITEM

    if query.data == "bulk_generate":
        receipt_assignments = {}
        for i, (name, amt) in enumerate(items):
            receipt_assignments[name] = {
                "amount": amt,
                "people": assignments[i],
            }
        context.user_data["receipt_assignments"] = receipt_assignments
        await query.edit_message_text("✅ All items assigned!")
        try:
            await send_receipt_split_summary(query.message, context)
        except Exception as e:
            logging.error("send_receipt_split_summary failed: %s", e)
            plain = str(e)
            await query.message.reply_text(f"⚠️ Failed to generate summary: {plain}")
        return RECEIPT_ASSIGN_ITEM

    if query.data == "bulk_reassign":
        context.user_data["bulk_index"] = 0
        context.user_data["bulk_selected"] = assignments.get(0, []).copy()
        item_name, amount = items[0]
        await query.edit_message_text(
            single_assign_message(item_name, amount, 0, len(items)),
            parse_mode="Markdown",
            reply_markup=receipt_single_assign_keyboard(people, assignments.get(0, []), 0),
        )
        return RECEIPT_ASSIGN_ITEM

    return RECEIPT_ASSIGN_ITEM

async def handle_receipt_tax(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    selected: list = context.user_data.setdefault("rtax_selected", [])

    if data == "rtax_noop":
        return

    if data == "rtax_toggle_gst":
        if "gst" in selected:
            selected.remove("gst")
        else:
            selected.append("gst")
        context.user_data["rtax_selected"] = selected
        await query.edit_message_reply_markup(reply_markup=receipt_tax_type_keyboard(selected))

    elif data == "rtax_toggle_service":
        if "service" in selected:
            selected.remove("service")
        else:
            selected.append("service")
        context.user_data["rtax_selected"] = selected
        await query.edit_message_reply_markup(reply_markup=receipt_tax_type_keyboard(selected))

    elif data == "rtax_none":
        context.user_data["receipt_gst"] = 0.0
        context.user_data["receipt_service"] = 0.0
        context.user_data.pop("rtax_selected", None)
        await query.edit_message_text("No taxes applied.")
        await send_receipt_split_summary(query.message, context)

    elif data == "rtax_tax_confirm":
        need_gst     = "gst"     in selected
        need_service = "service" in selected
        context.user_data["receipt_need_gst"]     = need_gst
        context.user_data["receipt_need_service"] = need_service
        context.user_data.pop("rtax_selected", None)
        await query.edit_message_text(
            "Which country are you in?",
            reply_markup=receipt_country_keyboard(need_gst, need_service),
        )

    elif data.startswith("rtax_country_"):
        code = data.replace("rtax_country_", "")
        need_gst     = context.user_data.get("receipt_need_gst",     False)
        need_service = context.user_data.get("receipt_need_service", False)

        if code == "other":
            context.user_data["receipt_tax_stage"] = "gst" if need_gst else "service"
            if not need_gst:
                context.user_data["receipt_gst"] = 0.0
            await query.edit_message_text(
                "Enter the GST / VAT / tax percentage (e.g. 9), or 0 for none:"
                if need_gst else
                "Enter the service charge percentage (e.g. 10), or 0 for none:"
            )
        else:
            label, gst, service, tax_name = KNOWN_COUNTRIES[code]
            context.user_data["receipt_gst"]      = gst     if need_gst     else 0.0
            context.user_data["receipt_service"]  = service if need_service else 0.0
            context.user_data["receipt_tax_name"] = tax_name
            parts = []
            if need_gst and gst:
                parts.append(f"{tax_name} {gst:.0f}%")
            if need_service and service:
                parts.append(f"Service {service:.0f}%")
            await query.edit_message_text(f"{label} — Applying: {', '.join(parts)}")
            if not context.user_data.get("receipt_assignments"):
                await query.message.reply_text("⚠️ Items haven't been assigned yet. Please complete item assignment first.")
                return
            try:
                await send_receipt_split_summary(query.message, context)
            except Exception as e:
                logging.error("send_receipt_split_summary failed: %s", e)
                await query.message.reply_text(f"⚠️ Failed to generate summary: {e}")


async def handle_receipt_edit_taxes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit taxes button on the final summary (after split is generated)."""
    query = update.callback_query
    await query.answer()
    context.user_data["rtax_selected"] = []
    await query.message.reply_text(
        "Does this bill include any taxes or charges?",
        reply_markup=receipt_tax_type_keyboard([]),
    )


async def handle_receipt_edit_taxes_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit taxes button shown before confirming items (overrides AI-detected taxes)."""
    query = update.callback_query
    await query.answer()
    gst     = context.user_data.get("receipt_gst", 0.0)
    service = context.user_data.get("receipt_service", 0.0)
    context.user_data["receipt_tax_stage"] = "edit_gst"
    await query.message.reply_text(
        f"Current detected taxes:\n"
        f"  • GST: {gst:.1f}%\n"
        f"  • Service charge: {service:.1f}%\n\n"
        f"Enter the GST / VAT / tax percentage (e.g. 9), or 0 for none:"
    )


async def send_receipt_split_summary(message_obj, context: ContextTypes.DEFAULT_TYPE) -> None:
    assignments = context.user_data.get("receipt_assignments", {})
    people = context.user_data.get("receipt_people", [])
    gst = context.user_data.get("receipt_gst", 0.0)
    service = context.user_data.get("receipt_service", 0.0)

    person_totals = {person: 0.0 for person in people}
    person_items = {person: [] for person in people}

    for item_name, info in assignments.items():
        amount = info["amount"]
        assigned_people = info["people"]
        share = amount / len(assigned_people)
        for person in assigned_people:
            person_totals[person] += share
            person_items[person].append((item_name, share))

    tax_name = context.user_data.get("receipt_tax_name", "Tax")
    base_total = sum(person_totals.values())
    service_amount = round(base_total * service / 100, 2)
    subtotal = base_total + service_amount
    gst_amount = round(subtotal * gst / 100, 2)
    grand_total = subtotal + gst_amount

    # Build incl. label for subtitle
    incl_parts = []
    if service:
        incl_parts.append("service charge")
    if gst:
        incl_parts.append(tax_name)
    incl_label = f"incl. of {' & '.join(incl_parts)}" if incl_parts else "excl. taxes"

    lines = [
        "🧾 RECEIPT SPLIT SUMMARY",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"💰 Final amounts to pay ({incl_label})",
        "────────────────────",
    ]

    for person in people:
        p_base = person_totals[person]
        final_share = grand_total * (p_base / base_total) if base_total else 0.0
        p_service = round(p_base * service / 100, 2)
        p_subtotal = p_base + p_service
        p_gst = round(p_subtotal * gst / 100, 2)

        lines.append(f"\n👤 {person}")
        for item_name, share in person_items[person]:
            lines.append(f"   • {item_name}: {fmt(share)}")
        if service:
            lines.append(f"   + Service ({service:.0f}%): {fmt(p_service)}")
        if gst:
            lines.append(f"   + {tax_name} ({gst:.0f}%): {fmt(p_gst)}")
        lines.append(f"💵 Pays: {fmt(final_share)}")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
    ]
    if service:
        lines.append(f"Base total      :  {fmt(base_total)}")
        lines.append(f"Service ({service:.0f}%)   :  {fmt(service_amount)}")
    if gst:
        lines.append(f"{tax_name} ({gst:.0f}%)      :  {fmt(gst_amount)}")
    lines += [
        f"Grand total     :  {fmt(grand_total)}",
        "",
        "Generated by @Keikie_Bot",
    ]

    text = "\n".join(lines)
    await message_obj.reply_text(text, reply_markup=receipt_done_keyboard())

async def split_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "How do you want to split the bill?",
        reply_markup=split_type_keyboard(),
    )
    return CHOICE


async def split_start_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📸  Scan receipt",   callback_data="cmd_scan")],
        [InlineKeyboardButton("✏️  Enter manually", callback_data="cmd_split_manual")],
    ])
    await query.message.reply_text(
        "How would you like to split the bill?",
        reply_markup=keyboard,
    )
    return ConversationHandler.END


async def split_start_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.message.reply_text(
        "How do you want to split the bill?",
        reply_markup=split_type_keyboard(),
    )
    return CHOICE


# ─────────────────────────────────────────────────────────
# Choose split type
# ─────────────────────────────────────────────────────────

async def choose_split_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "split_equal":
        push_history(context, CHOICE)
        context.user_data["split_type"] = "equal"
        await query.message.reply_text(
            "⚖️ Equal split selected.\n\nEnter the total bill amount (the final number on the receipt):",
            reply_markup=ACTION_BAR_MARKUP,
        )
        return TOTAL

    if query.data == "split_individual":
        push_history(context, CHOICE)
        context.user_data["split_type"]           = "individual"
        context.user_data["names"]                = []
        context.user_data["amounts_by_person"]    = {}
        context.user_data["shared_items"]         = []
        context.user_data["current_person_index"] = 0
        await query.message.reply_text(
            "🧮 Individual split selected.\n\nHow many people are splitting the bill?",
            reply_markup=ACTION_BAR_MARKUP,
        )
        return PEOPLE_INDIV

    return CHOICE


# ─────────────────────────────────────────────────────────
# Equal split flow
# ─────────────────────────────────────────────────────────

async def get_total(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        total = float(update.message.text.strip())
        if total <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "⚠️ Please enter a valid positive number (e.g. 45.80).",
            reply_markup=ACTION_BAR_MARKUP,
        )
        return TOTAL
    push_history(context, TOTAL)
    context.user_data["total"] = total
    await update.message.reply_text(
        "How many people are splitting the bill?",
        reply_markup=ACTION_BAR_MARKUP,
    )
    return PEOPLE_EQUAL


async def get_people_equal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        people = int(update.message.text.strip())
        if people <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "⚠️ Please enter a whole number greater than 0.",
            reply_markup=ACTION_BAR_MARKUP,
        )
        return PEOPLE_EQUAL
    push_history(context, PEOPLE_EQUAL)
    context.user_data["people"] = people
    await update.message.reply_text(build_summary(context.user_data))
    await update.message.reply_text(
        "📋 Copy the summary above and share it with your group!\n\nWhat would you like to do next?",
        reply_markup=done_keyboard(),
    )
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────
# Individual split — people & names
# ─────────────────────────────────────────────────────────

async def get_people_individual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        people = int(update.message.text.strip())
        if people <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "⚠️ Please enter a whole number greater than 0.",
            reply_markup=ACTION_BAR_MARKUP,
        )
        return PEOPLE_INDIV
    push_history(context, PEOPLE_INDIV)
    context.user_data["people"] = people
    await update.message.reply_text(
        f"Enter all {people} names separated by commas:\n\ne.g. Alice, Bob, Charlie",
        reply_markup=ACTION_BAR_MARKUP,
    )
    return NAME_INDIV


async def get_name_individual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    expected = context.user_data.get("people", 0)
    names = [n.strip() for n in text.split(",") if n.strip()]

    if not names:
        await update.message.reply_text(
            "⚠️ Please enter at least one name.",
            reply_markup=ACTION_BAR_MARKUP,
        )
        return NAME_INDIV

    if len(names) != expected:
        await update.message.reply_text(
            f"⚠️ You said {expected} people but entered {len(names)} name(s).\n\n"
            f"Please enter exactly {expected} names separated by commas.",
            reply_markup=ACTION_BAR_MARKUP,
        )
        return NAME_INDIV

    push_history(context, NAME_INDIV)
    for name in names:
        context.user_data["names"].append(name)
        context.user_data["amounts_by_person"][name] = []

    context.user_data["current_person_index"] = 0
    first_name = names[0]
    context.user_data["current_name"] = first_name
    await update.message.reply_text(
        f"{progress(context.user_data)}How many items did {first_name} order?",
        reply_markup=ACTION_BAR_MARKUP,
    )
    return ITEM_COUNT


# ─────────────────────────────────────────────────────────
# Individual split — items
# ─────────────────────────────────────────────────────────

async def get_item_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        count = int(update.message.text.strip())
        if count <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "⚠️ Please enter a number of items (at least 1).",
            reply_markup=ACTION_BAR_MARKUP,
        )
        return ITEM_COUNT
    push_history(context, ITEM_COUNT)
    context.user_data["item_count"]   = count
    context.user_data["current_item"] = 1
    name = context.user_data["current_name"]
    await update.message.reply_text(
        f"{progress(context.user_data)}Item 1/{count} for {name} — what's it called?\n"
        f"(type - to skip naming it)",
        reply_markup=ACTION_BAR_MARKUP,
    )
    return ITEM_NAME


async def get_item_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    item_label = f"Item {context.user_data['current_item']}" if raw == "-" else raw
    push_history(context, ITEM_NAME)
    context.user_data["current_item_name"] = item_label
    name       = context.user_data["current_name"]
    item_num   = context.user_data["current_item"]
    item_count = context.user_data["item_count"]
    await update.message.reply_text(
        f"{progress(context.user_data)}How much did {item_label} cost? "
        f"({name}'s item {item_num}/{item_count})",
        reply_markup=ACTION_BAR_MARKUP,
    )
    return ITEM_AMOUNT


async def get_individual_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        amount = float(update.message.text.strip())
        if amount < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "⚠️ Please enter a valid amount (e.g. 13.95).",
            reply_markup=ACTION_BAR_MARKUP,
        )
        return ITEM_AMOUNT

    push_history(context, ITEM_AMOUNT)
    name      = context.user_data["current_name"]
    item_name = context.user_data["current_item_name"]
    context.user_data["amounts_by_person"][name].append((item_name, amount))

    next_item  = context.user_data["current_item"] + 1
    item_count = context.user_data["item_count"]

    if next_item <= item_count:
        context.user_data["current_item"] = next_item
        await update.message.reply_text(
            f"{progress(context.user_data)}Item {next_item}/{item_count} for {name} — what's it called?\n"
            f"(type - to skip)",
            reply_markup=ACTION_BAR_MARKUP,
        )
        return ITEM_NAME

    return await _show_person_review(update, context, name)


# ─────────────────────────────────────────────────────────
# Item review (button-based)
# ─────────────────────────────────────────────────────────

async def _show_person_review(update: Update, context: ContextTypes.DEFAULT_TYPE, name: str) -> int:
    items = context.user_data["amounts_by_person"][name]
    person_total = sum(a for _, a in items)

    lines = [f"📋 *{name}'s items* (subtotal: {fmt(person_total)})", ""]
    for i, (iname, iamt) in enumerate(items, 1):
        lines.append(f"  {i}. {iname} — {fmt(iamt)}")
    lines.append("\nTap ✏️ to edit a price or 🗑️ to remove an item.")

    msg_func = (
        update.message.reply_text
        if update.message
        else update.callback_query.message.reply_text
    )
    await msg_func(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=review_keyboard(items),
    )
    return REVIEW_PERSON


async def review_person(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    name  = context.user_data["current_name"]
    items = context.user_data["amounts_by_person"][name]
    data  = query.data

    if data == "review_done":
        push_history(context, REVIEW_PERSON)
        return await _advance_to_next_person_or_shared(query, context)

    if data.startswith("review_edit_"):
        idx = int(data.split("_")[-1])
        if 0 <= idx < len(items):
            context.user_data["edit_item_idx"] = idx
            iname, iamt = items[idx]
            await query.message.reply_text(
                f"Current price of *{iname}* is {fmt(iamt)}.\nEnter the new price:",
                parse_mode="Markdown",
                reply_markup=ACTION_BAR_MARKUP,
            )
            return EDIT_PRICE

    if data.startswith("review_remove_"):
        idx = int(data.split("_")[-1])
        if 0 <= idx < len(items):
            items.pop(idx)
            if items:
                person_total = sum(a for _, a in items)
                lines = [f"📋 *{name}'s items* (subtotal: {fmt(person_total)})", ""]
                for i, (iname, iamt) in enumerate(items, 1):
                    lines.append(f"  {i}. {iname} — {fmt(iamt)}")
                lines.append("\nTap ✏️ to edit a price or 🗑️ to remove an item.")
                await query.edit_message_text(
                    "\n".join(lines),
                    parse_mode="Markdown",
                    reply_markup=review_keyboard(items),
                )
            else:
                await query.edit_message_text(f"All items removed for {name}.")
                await query.message.reply_text(
                    f"No items left for {name}. How many items did they order?",
                    reply_markup=ACTION_BAR_MARKUP,
                )
                return ITEM_COUNT

    return REVIEW_PERSON


async def edit_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name  = context.user_data["current_name"]
    items = context.user_data["amounts_by_person"][name]
    idx   = context.user_data.get("edit_item_idx", 0)
    try:
        new_price = float(update.message.text.strip())
        if new_price < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "⚠️ Please enter a valid price (e.g. 12.50).",
            reply_markup=ACTION_BAR_MARKUP,
        )
        return EDIT_PRICE

    old_name, old_price = items[idx]
    items[idx] = (old_name, new_price)
    await update.message.reply_text(
        f"✅ Updated *{old_name}*: {fmt(old_price)} → {fmt(new_price)}",
        parse_mode="Markdown",
    )
    return await _show_person_review(update, context, name)


async def _advance_to_next_person_or_shared(query, context: ContextTypes.DEFAULT_TYPE) -> int:
    next_index = context.user_data["current_person_index"] + 1
    context.user_data["current_person_index"] = next_index
    reply = query.message.reply_text

    if next_index < context.user_data["people"]:
        next_name = context.user_data["names"][next_index]
        context.user_data["current_name"] = next_name
        await reply(
            f"{progress(context.user_data)}How many items did {next_name} order?",
            reply_markup=ACTION_BAR_MARKUP,
        )
        return ITEM_COUNT

    await reply(
        "All personal items recorded!\n\nDo you have any shared items to add?\n"
        "(e.g. a bottle of wine split among specific people)",
        reply_markup=yn_keyboard(),
    )
    return SHARED_CONFIRM


# ─────────────────────────────────────────────────────────
# Shared items
# ─────────────────────────────────────────────────────────

async def shared_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    push_history(context, SHARED_CONFIRM)

    if query.data == "shared_yes":
        await query.message.reply_text(
            "Enter the shared item as:\nName, amount  (e.g. Wine, 45.00)",
            reply_markup=ACTION_BAR_MARKUP,
        )
        return SHARED_NAME_AMT

    await query.message.reply_text("No shared items. Moving on…")
    return await _ask_tax(query.message)


async def shared_name_amt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        parts = update.message.text.strip().rsplit(",", 1)
        if len(parts) != 2:
            raise ValueError
        iname = parts[0].strip()
        iamt  = float(parts[1].strip())
        if not iname or iamt <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "⚠️ Use the format: Name, amount  (e.g. Wine, 45.00)",
            reply_markup=ACTION_BAR_MARKUP,
        )
        return SHARED_NAME_AMT

    push_history(context, SHARED_NAME_AMT)
    context.user_data["pending_shared"]  = {"name": iname, "amount": iamt}
    context.user_data["pending_sharers"] = []

    known = context.user_data["names"]
    await update.message.reply_text(
        f"Who shares *{iname}*? Tap to select, then confirm.",
        parse_mode="Markdown",
        reply_markup=sharers_keyboard(known, []),
    )
    return SHARED_PEOPLE


async def shared_people(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    known    = context.user_data["names"]
    selected = context.user_data.get("pending_sharers", [])
    item     = context.user_data["pending_shared"]

    if query.data == "sharer_noop":
        return SHARED_PEOPLE

    if query.data == "sharer_confirm":
        if not selected:
            await query.answer("Please select at least one person.", show_alert=True)
            return SHARED_PEOPLE

        push_history(context, SHARED_PEOPLE)
        context.user_data.pop("pending_shared")
        context.user_data.pop("pending_sharers", None)
        context.user_data["shared_items"].append((item["name"], item["amount"], selected))

        await query.edit_message_text(
            f"✅ Added *{item['name']}* {fmt(item['amount'])} — shared by {', '.join(selected)}.",
            parse_mode="Markdown",
        )
        await query.message.reply_text(
            "Add another shared item?",
            reply_markup=yn_keyboard(),
        )
        return SHARED_CONFIRM

    if query.data.startswith("sharer_toggle_"):
        name = query.data[len("sharer_toggle_"):]
        if name in selected:
            selected.remove(name)
        else:
            selected.append(name)
        context.user_data["pending_sharers"] = selected
        await query.edit_message_reply_markup(
            reply_markup=sharers_keyboard(known, selected)
        )
        return SHARED_PEOPLE

    return SHARED_PEOPLE


# ─────────────────────────────────────────────────────────
# Tax selection
# ─────────────────────────────────────────────────────────

async def _ask_tax(message_obj) -> int:
    await message_obj.reply_text(
        "Does this bill include any taxes or charges?\nSelect all that apply:",
        reply_markup=tax_keyboard([]),
    )
    return TAX_CONFIRM


async def tax_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    selected: list = context.user_data.setdefault("pending_taxes", [])
    data = query.data

    if data == "tax_noop":
        return TAX_CONFIRM

    if data == "tax_none":
        push_history(context, TAX_CONFIRM)
        context.user_data.pop("pending_taxes", None)
        context.user_data["gst"]       = 0.0
        context.user_data["service"]   = 0.0
        context.user_data["gst_label"] = "GST"
        context.user_data["country"]   = None
        await query.edit_message_text("No taxes applied.")
        await query.message.reply_text(build_summary(context.user_data))
        await query.message.reply_text(
            "📋 Copy the summary above and share it with your group!\n\nWhat would you like to do next?",
            reply_markup=done_keyboard(),
        )
        return ConversationHandler.END

    if data == "tax_toggle_gst":
        if "gst" in selected:
            selected.remove("gst")
        else:
            selected.append("gst")
        context.user_data["pending_taxes"] = selected
        await query.edit_message_reply_markup(reply_markup=tax_keyboard(selected))
        return TAX_CONFIRM

    if data == "tax_toggle_service":
        if "service" in selected:
            selected.remove("service")
        else:
            selected.append("service")
        context.user_data["pending_taxes"] = selected
        await query.edit_message_reply_markup(reply_markup=tax_keyboard(selected))
        return TAX_CONFIRM

    if data == "tax_confirm":
        if not selected:
            await query.answer("Select at least one tax, or choose No taxes.", show_alert=True)
            return TAX_CONFIRM

        push_history(context, TAX_CONFIRM)
        context.user_data["need_gst"]     = "gst"     in selected
        context.user_data["need_service"] = "service" in selected
        context.user_data.pop("pending_taxes", None)

        await query.edit_message_text("Which country are you in?")
        await query.message.reply_text(
            "🌏 Select your country:",
            reply_markup=country_keyboard(),
        )
        return COUNTRY_SELECT

    return TAX_CONFIRM


# ─────────────────────────────────────────────────────────
# Country selection
# ─────────────────────────────────────────────────────────

async def country_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    need_gst     = context.user_data.get("need_gst",     False)
    need_service = context.user_data.get("need_service", False)

    if query.data == "country_sg":
        push_history(context, COUNTRY_SELECT)
        context.user_data["country"]   = "Singapore"
        context.user_data["gst"]       = SG_GST     if need_gst     else 0.0
        context.user_data["service"]   = SG_SERVICE if need_service else 0.0
        context.user_data["gst_label"] = "GST"

        applied = []
        if need_gst:
            applied.append(f"GST {SG_GST:.0f}%")
        if need_service:
            applied.append(f"Service Charge {SG_SERVICE:.0f}%")

        await query.edit_message_text(
            f"✅ 🇸🇬 Singapore selected.\nApplying: {', '.join(applied)}"
        )
        await query.message.reply_text(build_summary(context.user_data))
        await query.message.reply_text(
            "📋 Copy the summary above and share it with your group!\n\nWhat would you like to do next?",
            reply_markup=done_keyboard(),
        )
        return ConversationHandler.END

    if query.data == "country_other":
        push_history(context, COUNTRY_SELECT)
        context.user_data["country"] = "Other"
        await query.edit_message_text("🌍 Other country selected.")

        if need_gst:
            await query.message.reply_text(
                "Enter the tax percentage (GST / VAT / SST / etc.) for your country, or 0 for none.\ne.g. 10",
                reply_markup=ACTION_BAR_MARKUP,
            )
            return MANUAL_GST
        else:
            context.user_data["gst"]       = 0.0
            context.user_data["gst_label"] = "GST"
            if need_service:
                await query.message.reply_text(
                    "Enter the service charge percentage for your country, or 0 for none.\ne.g. 10",
                    reply_markup=ACTION_BAR_MARKUP,
                )
                return MANUAL_SERVICE
            else:
                context.user_data["service"] = 0.0
                await query.message.reply_text(build_summary(context.user_data))
                await query.message.reply_text(
                    "📋 Copy the summary above and share it with your group!\n\nWhat would you like to do next?",
                    reply_markup=done_keyboard(),
                )
                return ConversationHandler.END

    return COUNTRY_SELECT


# ─────────────────────────────────────────────────────────
# Manual tax entry (Other countries)
# ─────────────────────────────────────────────────────────

async def manual_gst(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        v = float(update.message.text.strip())
        if v < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "⚠️ Enter a valid percentage like 10 or 0.",
            reply_markup=ACTION_BAR_MARKUP,
        )
        return MANUAL_GST

    push_history(context, MANUAL_GST)
    context.user_data["gst"]       = v
    context.user_data["gst_label"] = "Tax"

    if context.user_data.get("need_service"):
        await update.message.reply_text(
            "Enter the service charge percentage, or 0 for none.\ne.g. 10",
            reply_markup=ACTION_BAR_MARKUP,
        )
        return MANUAL_SERVICE

    context.user_data["service"] = 0.0
    await update.message.reply_text(build_summary(context.user_data))
    await update.message.reply_text(
        "📋 Copy the summary above and share it with your group!\n\nWhat would you like to do next?",
        reply_markup=done_keyboard(),
    )
    return ConversationHandler.END


async def manual_service(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        v = float(update.message.text.strip())
        if v < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "⚠️ Enter a valid percentage like 10 or 0.",
            reply_markup=ACTION_BAR_MARKUP,
        )
        return MANUAL_SERVICE

    push_history(context, MANUAL_SERVICE)
    context.user_data["service"] = v
    await update.message.reply_text(build_summary(context.user_data))
    await update.message.reply_text(
        "📋 Copy the summary above and share it with your group!\n\nWhat would you like to do next?",
        reply_markup=done_keyboard(),
    )
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────
# Cancel (command fallback — kept for power users)
# ─────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("✖️ Session ended. Send /split to start again.")
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────
# Menu registration
# ─────────────────────────────────────────────────────────

async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start",   "Start interacting with Keke"),
        BotCommand("scan",    "Scan a receipt photo"),
        BotCommand("split",   "Enter bill manually"),
        BotCommand("restart", "Restart from scratch"),
        BotCommand("cancel",  "Quit current session"),
        BotCommand("help",    "How to use Keke"),
    ])


# ─────────────────────────────────────────────────────────
# App builder
# ─────────────────────────────────────────────────────────

def build_application() -> Application:
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    ACTION_PATTERN = "^action_(back|restart|exit)$"

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("split", split_start),
            CallbackQueryHandler(split_start_manual, pattern="^cmd_split_manual$"),
        ],
        states={
            CHOICE: [
                CallbackQueryHandler(choose_split_type, pattern="^split_(equal|individual)$"),
            ],
            TOTAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_total),
                CallbackQueryHandler(handle_action_bar, pattern=ACTION_PATTERN),
            ],
            PEOPLE_EQUAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_people_equal),
                CallbackQueryHandler(handle_action_bar, pattern=ACTION_PATTERN),
            ],
            PEOPLE_INDIV: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_people_individual),
                CallbackQueryHandler(handle_action_bar, pattern=ACTION_PATTERN),
            ],
            NAME_INDIV: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_name_individual),
                CallbackQueryHandler(handle_action_bar, pattern=ACTION_PATTERN),
            ],
            ITEM_COUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_item_count),
                CallbackQueryHandler(handle_action_bar, pattern=ACTION_PATTERN),
            ],
            ITEM_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_item_name),
                CallbackQueryHandler(handle_action_bar, pattern=ACTION_PATTERN),
            ],
            ITEM_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_individual_amount),
                CallbackQueryHandler(handle_action_bar, pattern=ACTION_PATTERN),
            ],
            REVIEW_PERSON: [
                CallbackQueryHandler(review_person, pattern="^review_(done|edit_\\d+|remove_\\d+)$"),
                CallbackQueryHandler(handle_action_bar, pattern=ACTION_PATTERN),
            ],
            EDIT_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_price),
                CallbackQueryHandler(handle_action_bar, pattern=ACTION_PATTERN),
            ],
            SHARED_CONFIRM: [
                CallbackQueryHandler(shared_confirm, pattern="^shared_(yes|no)$"),
                CallbackQueryHandler(handle_action_bar, pattern=ACTION_PATTERN),
            ],
            SHARED_NAME_AMT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, shared_name_amt),
                CallbackQueryHandler(handle_action_bar, pattern=ACTION_PATTERN),
            ],
            SHARED_PEOPLE: [
                CallbackQueryHandler(shared_people, pattern="^(sharer_toggle_.+|sharer_confirm|sharer_noop)$"),
                CallbackQueryHandler(handle_action_bar, pattern=ACTION_PATTERN),
            ],
            TAX_CONFIRM: [
                CallbackQueryHandler(
                    tax_confirm,
                    pattern="^(tax_toggle_gst|tax_toggle_service|tax_none|tax_confirm|tax_noop)$"
                ),
                CallbackQueryHandler(handle_action_bar, pattern=ACTION_PATTERN),
            ],
            COUNTRY_SELECT: [
                CallbackQueryHandler(country_select, pattern="^country_(sg|other)$"),
                CallbackQueryHandler(handle_action_bar, pattern=ACTION_PATTERN),
            ],
            MANUAL_GST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, manual_gst),
                CallbackQueryHandler(handle_action_bar, pattern=ACTION_PATTERN),
            ],
            MANUAL_SERVICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, manual_service),
                CallbackQueryHandler(handle_action_bar, pattern=ACTION_PATTERN),
            ],
            RECEIPT_ADD_ITEM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receipt_add_item_text),
            ],
            RECEIPT_ASSIGN_ITEM: [
                CallbackQueryHandler(
                    receipt_assign_item,
                    pattern="^(bulk_t_\\d+|bulk_done|bulk_noop|bulk_back|bulk_generate|bulk_reassign)$"
                ),
            ],
        },
        
        fallbacks=[
            CommandHandler("cancel",  cancel),
            CommandHandler("restart", restart),
        ],
    )
    
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("scan",    lambda u, c: u.message.reply_text("📸 Send me a photo of your receipt and I'll read it for you!")))
    app.add_handler(CommandHandler("help",    help_cmd))
    app.add_handler(CommandHandler("restart", restart))
    app.add_handler(CallbackQueryHandler(button_help,        pattern="^cmd_help$"))
    app.add_handler(CallbackQueryHandler(button_scan,        pattern="^cmd_scan$"))
    app.add_handler(CallbackQueryHandler(split_start_button, pattern="^cmd_split$"))
    app.add_handler(CallbackQueryHandler(done_bye,           pattern="^done_bye$"))

    app.add_handler(
        CallbackQueryHandler(
            receipt_actions,
            pattern="^receipt_(confirm|edit_item|add_item|retry)$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            receipt_edit_actions,
            pattern="^(receipt_edit_\\d+|receipt_edit_back)$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            receipt_assign_item,
            pattern="^(bulk_t_\\d+|bulk_done|bulk_noop|bulk_back|bulk_generate|bulk_reassign)$"
        )
    )

    app.add_handler(MessageHandler(filters.ALL, log_message), group=-1)
    app.add_handler(conv)

    app.add_handler(
        CallbackQueryHandler(
            handle_receipt_tax,
            pattern="^rtax_(toggle_gst|toggle_service|none|noop|tax_confirm|country_.+)$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            handle_receipt_edit_taxes,
            pattern="^receipt_edit_taxes$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            handle_receipt_edit_taxes_manual,
            pattern="^receipt_edit_taxes_manual$"
        )
    )
    app.add_handler(MessageHandler(filters.PHOTO, handle_receipt_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receipt_text_router))

    return app


def main() -> None:
    app = build_application()
    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()