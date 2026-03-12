from bot import build_application


def main() -> None:
    app = build_application()
    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()