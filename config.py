"""
Haron Visuals Bot — конфигурация.
Все значения берутся из переменных окружения (на Render задаются в Environment).
"""

import os

# Токен бота от @BotFather
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Строка подключения к Postgres (Neon.tech), вида:
# postgresql://user:password@ep-xxx.eu-central-1.aws.neon.tech/neondb?sslmode=require
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Telegram ID админов через запятую, например: "123456789,987654321"
ADMIN_IDS = [
    int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x
]

# Публичный URL сервиса на Render, например: https://haron-visuals-bot.onrender.com
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "")

# Секретный путь вебхука, чтобы никто чужой не дёргал endpoint
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "haron-secret")

WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"

# Порт веб-сервера (Render передаёт свой через $PORT)
PORT = int(os.getenv("PORT", "10000"))

# Ссылки
FUNPAY_URL = os.getenv("FUNPAY_URL", "https://funpay.com/users/20786080/")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/HaronVisuals")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/HaronVisuals")

# Сброс HWID: не чаще, чем раз в 90 дней
HWID_RESET_COOLDOWN_DAYS = int(os.getenv("HWID_RESET_COOLDOWN_DAYS", "90"))

# Тарифы: код -> (название, дней; None = навсегда, платный ли)
PLANS = {
    "1d": ("1 день", 1, False),
    "7d": ("7 дней", 7, False),
    "30d": ("30 дней", 30, True),
    "90d": ("3 месяца", 90, True),
    "forever": ("Навсегда", None, True),
}
