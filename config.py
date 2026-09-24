"""
Haron Visuals Bot — конфигурация.
Все значения берутся из переменных окружения (на Render задаются в Environment).
"""

import os

# Токен бота от @BotFather
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Строка подключения к Postgres (Neon.tech)
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Telegram ID админов через запятую
ADMIN_IDS = [
    int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x
]

# Публичный URL сервиса на Render
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "")

# Секретный путь вебхука
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "haron-secret")
WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"

# Порт веб-сервера
PORT = int(os.getenv("PORT", "10000"))

# -------------------- Настройки обязательной подписки --------------------
# Юзернейм канала для проверки подписки (бот должен быть там админом)
CHANNEL_ID = os.getenv("CHANNEL_ID", "@haronvisuals")
# Публичная ссылка на канал для кнопки
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/haronvisuals")

# Ссылки
FUNPAY_URL = os.getenv("FUNPAY_URL", "https://funpay.com/users/20786080/")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/HaronVisuals_supbot")

# Сброс HWID: не чаще, чем раз в 90 дней
HWID_RESET_COOLDOWN_DAYS = int(os.getenv("HWID_RESET_COOLDOWN_DAYS", "90"))

# Тарифы
PLANS = {
    "1d": ("1 день", 1, False),
    "7d": ("7 дней", 7, False),
    "30d": ("30 дней", 30, True),
    "90d": ("3 месяца", 90, True),
    "forever": ("Навсегда", None, True),
}

# -------------------- SecureFabric Vendor API --------------------
VENDOR_API_KEY = "sf_vkey_a437f9f901a6f8229c3bc010d37ba2a2"
MOD_ID = "haronvisuals"
VENDOR_API_URL = "https://server.rehab/v1/vendor/subscribers"
LOADER_URL = "https://server.rehab/download/haronvisuals"
