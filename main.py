import threading
import asyncio

from web import run_flask
from bot import run_bot


if __name__ == "__main__":
    # Flask в фоновом потоке — необходим для удержания сервиса на Render
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Запуск асинхронного Telegram-бота
    asyncio.run(run_bot())
