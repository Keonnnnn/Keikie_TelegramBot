import os
from dotenv import load_dotenv
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# States
CHOICE, TOTAL, PEOPLE_EQUAL, GST_EQUAL, SERVICE_EQUAL, PEOPLE_INDIV, ITEM_AMOUNT, GST_INDIV, SERVICE_INDIV = range(9)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")


def format_money(amount: float) -> str:
    return f"${amount:.2f}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hello! I can help split a bill.\n"
        "Send /split to begin."
    )


async def split_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()

    keyboard = [["Equal", "Individual"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

    await update.message.reply_text(
        "Do you want to split the bill equally or by individual amounts?",
        reply_markup=reply_markup,
    )
    return CHOICE


async def choose_split_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text.strip().lower()

    if choice == "equal":
        context.user_data["split_type"] = "equal"
        await update.message.reply_text(
            "Send me the total bill amount.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return TOTAL

    if choice == "individual":
        context.user_data["split_type"] = "individual"
        await update.message.reply_text(
            "How many people are there?",
            reply_markup=ReplyKeyboardRemove(),
        )
        return PEOPLE_INDIV

    await update.message.reply_text("Please choose either Equal or Individual.")
    return CHOICE


# ---------- Equal split flow ----------

async def get_total(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()

    try:
        total = float(text)
        if total <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Please enter a valid positive number for the total bill.")
        return TOTAL

    context.user_data["total"] = total
    await update.message.reply_text("How many people are splitting the bill?")
    return PEOPLE_EQUAL


async def get_people_equal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()

    try:
        people = int(text)
        if people <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Please enter a valid whole number greater than 0.")
        return PEOPLE_EQUAL

    context.user_data["people"] = people
    await update.message.reply_text("Enter GST percentage (for example: 9). Send 0 if none.")
    return GST_EQUAL


async def get_gst_equal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()

    try:
        gst = float(text)
        if gst < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Please enter a valid GST percentage, like 9 or 0.")
        return GST_EQUAL

    context.user_data["gst"] = gst
    await update.message.reply_text("Enter service charge percentage (for example: 10). Send 0 if none.")
    return SERVICE_EQUAL


async def get_service_equal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()

    try:
        service = float(text)
        if service < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Please enter a valid service charge percentage, like 10 or 0.")
        return SERVICE_EQUAL

    total = context.user_data["total"]
    people = context.user_data["people"]
    gst = context.user_data["gst"]
    context.user_data["service"] = service

    service_amount = total * (service / 100)
    subtotal_after_service = total + service_amount
    gst_amount = subtotal_after_service * (gst / 100)
    grand_total = subtotal_after_service + gst_amount
    per_person = grand_total / people

    await update.message.reply_text(
        f"Split type: Equal\n"
        f"Base bill: {format_money(total)}\n"
        f"Service charge ({service:.2f}%): {format_money(service_amount)}\n"
        f"GST ({gst:.2f}%): {format_money(gst_amount)}\n"
        f"Grand total: {format_money(grand_total)}\n"
        f"People: {people}\n"
        f"Each person pays: {format_money(per_person)}"
    )
    return ConversationHandler.END


# ---------- Individual split flow ----------

async def get_people_individual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()

    try:
        people = int(text)
        if people <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Please enter a valid whole number greater than 0.")
        return PEOPLE_INDIV

    context.user_data["people"] = people
    context.user_data["amounts"] = []
    context.user_data["current_person"] = 1

    await update.message.reply_text("Enter the bill amount for Person 1:")
    return ITEM_AMOUNT


async def get_individual_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()

    try:
        amount = float(text)
        if amount < 0:
            raise ValueError
    except ValueError:
        current_person = context.user_data["current_person"]
        await update.message.reply_text(f"Please enter a valid amount for Person {current_person}.")
        return ITEM_AMOUNT

    context.user_data["amounts"].append(amount)
    context.user_data["current_person"] += 1

    current_person = context.user_data["current_person"]
    people = context.user_data["people"]

    if current_person <= people:
        await update.message.reply_text(f"Enter the bill amount for Person {current_person}:")
        return ITEM_AMOUNT

    await update.message.reply_text("Enter GST percentage (for example: 9). Send 0 if none.")
    return GST_INDIV


async def get_gst_individual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()

    try:
        gst = float(text)
        if gst < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Please enter a valid GST percentage, like 9 or 0.")
        return GST_INDIV

    context.user_data["gst"] = gst
    await update.message.reply_text("Enter service charge percentage (for example: 10). Send 0 if none.")
    return SERVICE_INDIV


async def get_service_individual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()

    try:
        service = float(text)
        if service < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Please enter a valid service charge percentage, like 10 or 0.")
        return SERVICE_INDIV

    amounts = context.user_data["amounts"]
    gst = context.user_data["gst"]

    base_total = sum(amounts)
    service_amount = base_total * (service / 100)
    subtotal_after_service = base_total + service_amount
    gst_amount = subtotal_after_service * (gst / 100)
    grand_total = subtotal_after_service + gst_amount

    lines = [
        "Split type: Individual",
        f"Base total: {format_money(base_total)}",
        f"Service charge ({service:.2f}%): {format_money(service_amount)}",
        f"GST ({gst:.2f}%): {format_money(gst_amount)}",
        f"Grand total: {format_money(grand_total)}",
        "",
        "Per person:",
    ]

    if base_total == 0:
        for i, amount in enumerate(amounts, start=1):
            lines.append(
                f"Person {i}: original {format_money(amount)} → final {format_money(0)}"
            )
    else:
        for i, amount in enumerate(amounts, start=1):
            final_share = grand_total * (amount / base_total)
            lines.append(
                f"Person {i}: original {format_money(amount)} → final {format_money(final_share)}"
            )

    await update.message.reply_text("\n".join(lines))
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def main() -> None:
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("split", split_start)],
        states={
            CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_split_type)],

            TOTAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_total)],
            PEOPLE_EQUAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_people_equal)],
            GST_EQUAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_gst_equal)],
            SERVICE_EQUAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_service_equal)],

            PEOPLE_INDIV: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_people_individual)],
            ITEM_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_individual_amount)],
            GST_INDIV: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_gst_individual)],
            SERVICE_INDIV: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_service_individual)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()