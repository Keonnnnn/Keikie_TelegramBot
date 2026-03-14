"""
Keke Bill Splitter Bot — bot_app.py
"""

import os
import copy
import logging
from dotenv import load_dotenv
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
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

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

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
    EDIT_PRICE,         # 16
) = range(17)

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
}


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def fmt(amount: float) -> str:
    return f"${amount:.2f}"


def yn_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes", callback_data="shared_yes"),
            InlineKeyboardButton("❌ No",  callback_data="shared_no"),
        ]
    ])


def split_type_keyboard() -> InlineKeyboardMarkup:
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
    return InlineKeyboardMarkup(rows)


def country_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🇸🇬  Singapore  (GST {SG_GST:.0f}%, Service {SG_SERVICE:.0f}%)", callback_data="country_sg")],
        [InlineKeyboardButton("🌍  Other countries",                                              callback_data="country_other")],
    ])


def review_keyboard(items: list) -> InlineKeyboardMarkup:
    rows = []
    for i, (iname, iamt) in enumerate(items):
        rows.append([
            InlineKeyboardButton(f"✏️ {iname}", callback_data=f"review_edit_{i}"),
            InlineKeyboardButton(f"🗑️ {iname}", callback_data=f"review_remove_{i}"),
        ])
    rows.append([InlineKeyboardButton("✅  Confirm & continue", callback_data="review_done")])
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
    return InlineKeyboardMarkup(rows)


def progress(data: dict) -> str:
    idx   = data.get("current_person_index", 0)
    total = data.get("people", 0)
    return f"[{idx + 1}/{total}] " if total else ""


# ─────────────────────────────────────────────────────────
# Logging incoming messages
# ─────────────────────────────────────────────────────────

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
        NAME_INDIV:      f"{progress(data)}What's the name of Person {data.get('current_person_index', 0) + 1}?",
        ITEM_COUNT:      f"How many items did {data.get('current_name', '?')} order?",
        ITEM_NAME:       f"What's the name of {data.get('current_name', '?')}'s item {data.get('current_item', '?')}?",
        ITEM_AMOUNT:     f"Enter the amount for {data.get('current_item_name', 'the item')}:",
        REVIEW_PERSON:   "Review your items above.",
        SHARED_CONFIRM:  "Do you have any shared items to add?",
        SHARED_NAME_AMT: "Enter the shared item as: Name, amount",
        SHARED_PEOPLE:   "Select who shares this item.",
        TAX_CONFIRM:     "Select which taxes apply to this bill.",
        COUNTRY_SELECT:  "Select your country.",
        MANUAL_GST:      "Enter the GST / tax percentage (e.g. 9), or 0 for none.",
        MANUAL_SERVICE:  "Enter the service charge percentage (e.g. 10), or 0 for none.",
    }
    return prompts.get(state, "Please continue.")


async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    history: list = context.user_data.get("_history", [])
    if not history:
        await update.message.reply_text("Nothing to undo yet.")
        return ConversationHandler.END

    prev_state, snapshot = history.pop()
    for k in [k for k in context.user_data if k != "_history"]:
        del context.user_data[k]
    context.user_data.update(snapshot)

    label = STATE_LABELS.get(prev_state, "previous step")
    await update.message.reply_text(
        f"Undone! Back to: {label}\n\n" + _re_prompt(prev_state, context.user_data),
    )
    return prev_state


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
        lines += ["  ────────────────────",
                  f"  Grand total :  {fmt(grand_total)}", "",
                  "💰 Per person:"]

        for name in names:
            p_base      = person_base.get(name, 0.0)
            final_share = grand_total * (p_base / base_total) if base_total else 0.0
            lines.append(f"\n  👤 {name}  →  {fmt(final_share)}")
            for iname, iamt in amounts_by.get(name, []):
                lines.append(f"      • {iname}: {fmt(iamt)}")
            for iname, iamt, sharers in shared_items:
                if name in sharers:
                    lines.append(f"      • {iname} (shared /{len(sharers)}): {fmt(iamt / len(sharers))}")

        if shared_items:
            lines += ["", "🔀 Shared items:"]
            for iname, iamt, sharers in shared_items:
                lines.append(f"  • {iname}  {fmt(iamt)}  -> {', '.join(sharers)}")

    lines += ["", "━━━━━━━━━━━━━━━━━━━━━", "Generated by @Keikie_Bot"]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────
# /start  /help  /restart
# ─────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_sticker(STICKER_ID)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➗  Split a bill", callback_data="cmd_split")],
        [InlineKeyboardButton("💡  How it works", callback_data="cmd_help")],
    ])
    await update.message.reply_text(
        "👋 Hi! I'm *Keke*, your bill-splitting assistant!\n\n"
        "I help you split restaurant bills fairly — equally or by individual orders, "
        "with shared items, GST, and service charge all handled for you. 🧾\n\n"
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
        "*Equal split* — enter the final receipt total and number of people. "
        "Everyone pays the same amount.\n\n"
        "*Individual split* — enter each person's name and their items (name + price). "
        "Optionally add shared items split among specific people, then choose your taxes.\n\n"
        "At any prompt:\n"
        "  /undo — go back one step\n"
        "  /restart — start over\n"
        "  /cancel — quit",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def button_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➗  Split a bill", callback_data="cmd_split")],
    ])
    await query.message.reply_text(
        "💡 *How Keke works*\n\n"
        "*Equal split* — enter the final receipt total and number of people. "
        "Everyone pays the same amount.\n\n"
        "*Individual split* — enter each person's name and their items (name + price). "
        "Optionally add shared items split among specific people, then choose your taxes.\n\n"
        "At any prompt:\n"
        "  /undo — go back one step\n"
        "  /restart — start over\n"
        "  /cancel — quit",
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


# ─────────────────────────────────────────────────────────
# Conversation entry points
# ─────────────────────────────────────────────────────────

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
        await update.message.reply_text("⚠️ Please enter a valid positive number (e.g. 45.80).")
        return TOTAL
    push_history(context, TOTAL)
    context.user_data["total"] = total
    await update.message.reply_text("How many people are splitting the bill?")
    return PEOPLE_EQUAL


async def get_people_equal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        people = int(update.message.text.strip())
        if people <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Please enter a whole number greater than 0.")
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
        await update.message.reply_text("⚠️ Please enter a whole number greater than 0.")
        return PEOPLE_INDIV
    push_history(context, PEOPLE_INDIV)
    context.user_data["people"] = people
    await update.message.reply_text(f"{progress(context.user_data)}What's the name of Person 1?")
    return NAME_INDIV


async def get_name_individual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("⚠️ Please enter a valid name.")
        return NAME_INDIV
    push_history(context, NAME_INDIV)
    context.user_data["names"].append(name)
    context.user_data["amounts_by_person"][name] = []
    context.user_data["current_name"] = name
    await update.message.reply_text(
        f"{progress(context.user_data)}How many items did {name} order?")
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
        await update.message.reply_text("⚠️ Please enter a number of items (at least 1).")
        return ITEM_COUNT
    push_history(context, ITEM_COUNT)
    context.user_data["item_count"]   = count
    context.user_data["current_item"] = 1
    name = context.user_data["current_name"]
    await update.message.reply_text(
        f"{progress(context.user_data)}Item 1/{count} for {name} — what's it called?\n"
        f"(type - to skip naming it)")
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
        f"({name}'s item {item_num}/{item_count})")
    return ITEM_AMOUNT


async def get_individual_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        amount = float(update.message.text.strip())
        if amount < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Please enter a valid amount (e.g. 13.95).")
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
            f"(type - to skip)")
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
                    f"No items left for {name}. How many items did they order?")
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
        await update.message.reply_text("⚠️ Please enter a valid price (e.g. 12.50).")
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
        await reply(f"{progress(context.user_data)}What's the name of Person {next_index + 1}?")
        return NAME_INDIV

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
            "⚠️ Use the format: Name, amount  (e.g. Wine, 45.00)")
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

        await query.edit_message_text("Which country are you dining in?")
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

    # ── Singapore: fixed rates ──────────────────────────
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

    # ── Other countries: manual entry ───────────────────
    if query.data == "country_other":
        push_history(context, COUNTRY_SELECT)
        context.user_data["country"] = "Other"
        await query.edit_message_text("🌍 Other country selected.")

        if need_gst:
            await query.message.reply_text(
                "Enter the tax percentage (GST / VAT / SST / etc.) for your country, or 0 for none.\n"
                "e.g. 10"
            )
            return MANUAL_GST
        else:
            # No GST needed, check service
            context.user_data["gst"]       = 0.0
            context.user_data["gst_label"] = "GST"
            if need_service:
                await query.message.reply_text(
                    "Enter the service charge percentage for your country, or 0 for none.\n"
                    "e.g. 10"
                )
                return MANUAL_SERVICE
            else:
                # Neither — shouldn't normally reach here but handle gracefully
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
        await update.message.reply_text("⚠️ Enter a valid percentage like 10 or 0.")
        return MANUAL_GST

    push_history(context, MANUAL_GST)
    context.user_data["gst"]       = v
    context.user_data["gst_label"] = "Tax"

    if context.user_data.get("need_service"):
        await update.message.reply_text(
            "Enter the service charge percentage, or 0 for none.\ne.g. 10"
        )
        return MANUAL_SERVICE

    # No service charge needed — finish
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
        await update.message.reply_text("⚠️ Enter a valid percentage like 10 or 0.")
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
# Cancel
# ─────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled. Send /split to start again.")
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────
# Menu registration
# ─────────────────────────────────────────────────────────

async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start",   "Start interacting with Keke"),
        BotCommand("split",   "Start a new bill split"),
        BotCommand("undo",    "Undo the last step"),
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

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("split", split_start),
            CallbackQueryHandler(split_start_button, pattern="^cmd_split$"),
        ],
        states={
            CHOICE: [
                CallbackQueryHandler(choose_split_type, pattern="^split_(equal|individual)$"),
            ],
            TOTAL:        [MessageHandler(filters.TEXT & ~filters.COMMAND, get_total)],
            PEOPLE_EQUAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_people_equal)],
            PEOPLE_INDIV: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_people_individual)],
            NAME_INDIV:   [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name_individual)],
            ITEM_COUNT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, get_item_count)],
            ITEM_NAME:    [MessageHandler(filters.TEXT & ~filters.COMMAND, get_item_name)],
            ITEM_AMOUNT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, get_individual_amount)],
            REVIEW_PERSON: [
                CallbackQueryHandler(review_person, pattern="^review_(done|edit_\\d+|remove_\\d+)$"),
            ],
            EDIT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_price)],
            SHARED_CONFIRM: [
                CallbackQueryHandler(shared_confirm, pattern="^shared_(yes|no)$"),
            ],
            SHARED_NAME_AMT: [MessageHandler(filters.TEXT & ~filters.COMMAND, shared_name_amt)],
            SHARED_PEOPLE: [
                CallbackQueryHandler(shared_people, pattern="^(sharer_toggle_.+|sharer_confirm|sharer_noop)$"),
            ],
            TAX_CONFIRM: [
                CallbackQueryHandler(
                    tax_confirm,
                    pattern="^(tax_toggle_gst|tax_toggle_service|tax_none|tax_confirm|tax_noop)$"
                ),
            ],
            COUNTRY_SELECT: [
                CallbackQueryHandler(country_select, pattern="^country_(sg|other)$"),
            ],
            MANUAL_GST:     [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_gst)],
            MANUAL_SERVICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_service)],
        },
        fallbacks=[
            CommandHandler("cancel",  cancel),
            CommandHandler("restart", restart),
            CommandHandler("undo",    undo),
        ],
    )

    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("help",    help_cmd))
    app.add_handler(CommandHandler("restart", restart))
    app.add_handler(CallbackQueryHandler(button_help, pattern="^cmd_help$"))
    app.add_handler(CallbackQueryHandler(done_bye,    pattern="^done_bye$"))
    app.add_handler(MessageHandler(filters.ALL, log_message), group=-1)
    app.add_handler(conv)
    return app


def main() -> None:
    app = build_application()
    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()