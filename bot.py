"""
Keke Bill Splitter Bot — bot_app.py
"""

import os
import copy
import logging
from dotenv import load_dotenv
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
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
# States
# ─────────────────────────────────────────────────────────
(
    CHOICE,           # 0
    TOTAL,            # 1
    PEOPLE_EQUAL,     # 2
    PEOPLE_INDIV,     # 3
    NAME_INDIV,       # 4
    ITEM_COUNT,       # 5
    ITEM_NAME,        # 6
    ITEM_AMOUNT,      # 7
    REVIEW_PERSON,    # 8
    SHARED_CONFIRM,   # 9
    SHARED_NAME_AMT,  # 10
    SHARED_PEOPLE,    # 11
    GST,              # 12
    SERVICE,          # 13
    EDIT_PRICE,       # 14
) = range(15)

STATE_LABELS = {
    CHOICE:         "split type",
    TOTAL:          "total bill amount",
    PEOPLE_EQUAL:   "number of people",
    PEOPLE_INDIV:   "number of people",
    NAME_INDIV:     "person name",
    ITEM_COUNT:     "item count",
    ITEM_NAME:      "item name",
    ITEM_AMOUNT:    "item amount",
    REVIEW_PERSON:  "item review",
    SHARED_CONFIRM: "shared items",
    SHARED_NAME_AMT:"shared item details",
    SHARED_PEOPLE:  "shared item people",
    GST:            "GST",
    SERVICE:        "service charge",
    EDIT_PRICE:     "edit item price",
}


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def fmt(amount: float) -> str:
    return f"${amount:.2f}"


def yn_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([["Yes", "No"]], resize_keyboard=True, one_time_keyboard=True)


def progress(data: dict) -> str:
    idx   = data.get("current_person_index", 0)
    total = data.get("people", 0)
    return f"[{idx + 1}/{total}] " if total else ""


# ─────────────────────────────────────────────────────────
# Logging incoming messages
# ─────────────────────────────────────────────────────────

async def log_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message and update.message.text:
        user = update.message.from_user
        name = user.username or user.first_name or "unknown"
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
        CHOICE:         "How do you want to split the bill? Tap Equal or Individual.",
        TOTAL:          "Enter the total bill amount:",
        PEOPLE_EQUAL:   "How many people are splitting the bill?",
        PEOPLE_INDIV:   "How many people are splitting the bill?",
        NAME_INDIV:     f"{progress(data)}What's the name of Person {data.get('current_person_index', 0) + 1}?",
        ITEM_COUNT:     f"How many items did {data.get('current_name', '?')} order?",
        ITEM_NAME:      f"What's the name of {data.get('current_name', '?')}'s item {data.get('current_item', '?')}?",
        ITEM_AMOUNT:    f"Enter the amount for {data.get('current_item_name', 'the item')}:",
        REVIEW_PERSON:  "Review items above. Reply done / edit N / remove N.",
        SHARED_CONFIRM: "Do you have any shared items to add? Tap Yes or No.",
        SHARED_NAME_AMT:"Enter the shared item as: Name, amount",
        SHARED_PEOPLE:  "Who shares this item? Enter names separated by commas.",
        GST:            "Enter GST percentage (e.g. 9), or 0 for none.",
        SERVICE:        "Enter service charge percentage (e.g. 10), or 0 for none.",
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
        reply_markup=ReplyKeyboardRemove(),
    )
    return prev_state


# ─────────────────────────────────────────────────────────
# Summary builder
# ─────────────────────────────────────────────────────────

def build_summary(data: dict) -> str:
    lines = ["🧾 BILL SUMMARY", "━━━━━━━━━━━━━━━━━━━━━", ""]
    gst     = data.get("gst",     0.0)
    service = data.get("service", 0.0)

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

        lines += ["📌 Mode: Individual split", f"👥 People: {len(names)}", "",
                  f"  Base total :  {fmt(base_total)}"]
        if service:
            lines.append(f"  Service ({service:.1f}%) :  {fmt(service_amount)}")
        if gst:
            lines.append(f"  GST ({gst:.1f}%)     :  {fmt(gst_amount)}")
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
    await update.message.reply_text(
        "👋 Hi! I'm Keke, your bill-splitting assistant.\n\n"
        "Commands:\n"
        "  /split — start a new split\n"
        "  /undo — undo the last step\n"
        "  /restart — restart from scratch\n"
        "  /cancel — quit\n"
        "  /help — how to use me"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "💡 How Keke works\n\n"
        "Equal split — enter the final receipt total and number of people.\n\n"
        "Individual split — enter each person's name and their items (name + price). "
        "Optionally add shared items split among specific people, then set GST and service charge.\n\n"
        "At any prompt:\n"
        "  /undo — go back one step\n"
        "  /restart — start over\n"
        "  /cancel — quit"
    )


async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("🔄 Restarted. Send /split to begin.",
                                    reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────
# Conversation entry
# ─────────────────────────────────────────────────────────

async def split_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    keyboard = ReplyKeyboardMarkup([["⚖️ Equal", "🧮 Individual"]],
                                   resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("How do you want to split the bill?", reply_markup=keyboard)
    return CHOICE


async def choose_split_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text.strip().lower()

    if "equal" in choice:
        push_history(context, CHOICE)
        context.user_data["split_type"] = "equal"
        await update.message.reply_text(
            "⚖️ Equal split selected.\n\nEnter the total bill amount (the final number on the receipt):",
            reply_markup=ReplyKeyboardRemove(),
        )
        return TOTAL

    if "individual" in choice:
        push_history(context, CHOICE)
        context.user_data["split_type"]           = "individual"
        context.user_data["names"]                = []
        context.user_data["amounts_by_person"]    = {}
        context.user_data["shared_items"]         = []
        context.user_data["current_person_index"] = 0
        await update.message.reply_text(
            "🧮 Individual split selected.\n\nHow many people are splitting the bill?",
            reply_markup=ReplyKeyboardRemove(),
        )
        return PEOPLE_INDIV

    await update.message.reply_text("Please tap ⚖️ Equal or 🧮 Individual.")
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
    await update.message.reply_text("📋 Copy the summary above and share it with your group!")
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


async def _show_person_review(update: Update, context: ContextTypes.DEFAULT_TYPE, name: str) -> int:
    items = context.user_data["amounts_by_person"][name]
    person_total = sum(a for _, a in items)
    lines = [f"📋 {name}'s items (subtotal: {fmt(person_total)})", ""]
    for i, (iname, iamt) in enumerate(items, 1):
        lines.append(f"  {i}. {iname} — {fmt(iamt)}")
    lines += [
        "",
        "Reply:",
        "  done     — confirm and continue",
        "  edit N   — change price of item N  (e.g. edit 2)",
        "  remove N — delete item N           (e.g. remove 1)",
    ]
    await update.message.reply_text("\n".join(lines))
    return REVIEW_PERSON


async def review_person(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text  = update.message.text.strip().lower()
    name  = context.user_data["current_name"]
    items = context.user_data["amounts_by_person"][name]

    if text == "done":
        push_history(context, REVIEW_PERSON)
        return await _advance_to_next_person_or_shared(update, context)

    if text.startswith("edit "):
        parts = text.split()
        if len(parts) == 2 and parts[1].isdigit():
            idx = int(parts[1]) - 1
            if 0 <= idx < len(items):
                context.user_data["edit_item_idx"] = idx
                iname, iamt = items[idx]
                await update.message.reply_text(
                    f"Current price of {iname} is {fmt(iamt)}. What's the new price?")
                return EDIT_PRICE
            await update.message.reply_text(f"⚠️ Item number out of range (1-{len(items)}).")
            return REVIEW_PERSON
        await update.message.reply_text("⚠️ Use: edit N  (e.g. edit 2)")
        return REVIEW_PERSON

    if text.startswith("remove "):
        parts = text.split()
        if len(parts) == 2 and parts[1].isdigit():
            idx = int(parts[1]) - 1
            if 0 <= idx < len(items):
                removed = items.pop(idx)
                await update.message.reply_text(f"Removed {removed[0]} ({fmt(removed[1])}).")
                if items:
                    return await _show_person_review(update, context, name)
                await update.message.reply_text(
                    f"No items left for {name}. How many items did they order?")
                return ITEM_COUNT
            await update.message.reply_text(f"⚠️ Item number out of range (1-{len(items)}).")
            return REVIEW_PERSON
        await update.message.reply_text("⚠️ Use: remove N  (e.g. remove 1)")
        return REVIEW_PERSON

    await update.message.reply_text("⚠️ Reply: done / edit N / remove N")
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
    await update.message.reply_text(f"Updated {old_name}: {fmt(old_price)} -> {fmt(new_price)}")
    return await _show_person_review(update, context, name)


async def _advance_to_next_person_or_shared(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    next_index = context.user_data["current_person_index"] + 1
    context.user_data["current_person_index"] = next_index

    if next_index < context.user_data["people"]:
        await update.message.reply_text(
            f"{progress(context.user_data)}What's the name of Person {next_index + 1}?")
        return NAME_INDIV

    await update.message.reply_text(
        "All personal items recorded!\n\nDo you have any shared items to add?\n"
        "(e.g. a bottle of wine split among specific people)",
        reply_markup=yn_keyboard(),
    )
    return SHARED_CONFIRM


# ─────────────────────────────────────────────────────────
# Shared items
# ─────────────────────────────────────────────────────────

async def shared_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text.strip().lower()
    push_history(context, SHARED_CONFIRM)

    if "yes" in choice:
        await update.message.reply_text(
            "Enter the shared item as:\nName, amount  (e.g. Wine, 45.00)",
            reply_markup=ReplyKeyboardRemove(),
        )
        return SHARED_NAME_AMT

    await update.message.reply_text("No shared items. Moving on…", reply_markup=ReplyKeyboardRemove())
    await update.message.reply_text("Enter GST percentage (e.g. 9), or 0 for none.")
    return GST


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
    context.user_data["pending_shared"] = {"name": iname, "amount": iamt}
    known = context.user_data["names"]
    await update.message.reply_text(
        f"Who shares {iname}? Enter names separated by commas.\n"
        f"(Known people: {', '.join(known)})")
    return SHARED_PEOPLE


async def shared_people(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    known   = context.user_data["names"]
    entered = [n.strip() for n in update.message.text.split(",")]
    valid   = [n for n in entered if n in known]
    invalid = [n for n in entered if n not in known]

    if not valid:
        await update.message.reply_text(
            f"⚠️ No valid names. Known people: {', '.join(known)}\nPlease try again.")
        return SHARED_PEOPLE

    if invalid:
        await update.message.reply_text(f"⚠️ Skipping unrecognised: {', '.join(invalid)}.")

    push_history(context, SHARED_PEOPLE)
    item = context.user_data.pop("pending_shared")
    context.user_data["shared_items"].append((item["name"], item["amount"], valid))

    await update.message.reply_text(
        f"Added {item['name']} {fmt(item['amount'])} shared by {', '.join(valid)}.\n\n"
        "Add another shared item?",
        reply_markup=yn_keyboard(),
    )
    return SHARED_CONFIRM


# ─────────────────────────────────────────────────────────
# GST / Service
# ─────────────────────────────────────────────────────────

async def get_gst(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        v = float(update.message.text.strip())
        if v < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Enter a valid percentage like 9 or 0.")
        return GST
    push_history(context, GST)
    context.user_data["gst"] = v
    await update.message.reply_text("Enter service charge percentage (e.g. 10), or 0.")
    return SERVICE


async def get_service(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        v = float(update.message.text.strip())
        if v < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Enter a valid percentage like 10 or 0.")
        return SERVICE
    push_history(context, SERVICE)
    context.user_data["service"] = v
    await update.message.reply_text(build_summary(context.user_data))
    await update.message.reply_text("📋 Copy the summary above and share it with your group!")
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────
# Cancel
# ─────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled. Send /split to start again.",
                                    reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────
# App builder
# ─────────────────────────────────────────────────────────

def build_application() -> Application:
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("split", split_start)],
        states={
            CHOICE:         [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_split_type)],
            TOTAL:          [MessageHandler(filters.TEXT & ~filters.COMMAND, get_total)],
            PEOPLE_EQUAL:   [MessageHandler(filters.TEXT & ~filters.COMMAND, get_people_equal)],
            PEOPLE_INDIV:   [MessageHandler(filters.TEXT & ~filters.COMMAND, get_people_individual)],
            NAME_INDIV:     [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name_individual)],
            ITEM_COUNT:     [MessageHandler(filters.TEXT & ~filters.COMMAND, get_item_count)],
            ITEM_NAME:      [MessageHandler(filters.TEXT & ~filters.COMMAND, get_item_name)],
            ITEM_AMOUNT:    [MessageHandler(filters.TEXT & ~filters.COMMAND, get_individual_amount)],
            REVIEW_PERSON:  [MessageHandler(filters.TEXT & ~filters.COMMAND, review_person)],
            EDIT_PRICE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_price)],
            SHARED_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, shared_confirm)],
            SHARED_NAME_AMT:[MessageHandler(filters.TEXT & ~filters.COMMAND, shared_name_amt)],
            SHARED_PEOPLE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, shared_people)],
            GST:            [MessageHandler(filters.TEXT & ~filters.COMMAND, get_gst)],
            SERVICE:        [MessageHandler(filters.TEXT & ~filters.COMMAND, get_service)],
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
    app.add_handler(MessageHandler(filters.ALL, log_message), group=-1)
    app.add_handler(conv)
    return app


def main() -> None:
    app = build_application()
    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()