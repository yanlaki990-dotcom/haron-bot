"""
Haron Visuals Bot — @HaronVisualsBot

Функции:
  /start            — приветствие + главное меню (reply-клавиатура)
  Профиль           — статус подписки, HWID, дата регистрации
  Активировать ключ — ввод ключа HARON-XXXX-XXXX-XXXX, затем логин/пароль для SecureFabric
  Сбросить HWID     — с кулдауном
  Купить визуалы    — ссылка на лот FunPay
  Поддержка         — ссылка на тебя

Админ-команды:
  /genkeys <план> <кол-во>  — сгенерировать ключи
  /give <tg_id> <план>      — выдать подписку вручную
  /stats                    — статистика

Запуск: polling (для Bothost)
"""

import asyncio
import logging
import random
import re
from datetime import datetime, timezone

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

import config
import db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("haron-bot")

router = Router()


# ---------- Клавиатуры ----------

def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 Профиль")],
            [KeyboardButton(text="🔑 Активировать ключ")],
            [
                KeyboardButton(text="💻 Сбросить HWID"),
                KeyboardButton(text="🛒 Купить визуалы"),
            ],
            [KeyboardButton(text="🟢 Поддержка")],
        ],
        resize_keyboard=True,
    )


def buy_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛒 Открыть FunPay", url=config.FUNPAY_URL)],
            [InlineKeyboardButton(text="📢 Канал", url=config.CHANNEL_URL)],
        ]
    )


def support_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🟢 Написать в поддержку", url=config.SUPPORT_URL)]
        ]
    )


# ---------- FSM для ввода ключа, логина, пароля ----------

class KeyInput(StatesGroup):
    waiting_key = State()
    waiting_username = State()
    waiting_password = State()


# ---------- Хелперы ----------

def fmt_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


def is_admin(tg_id: int) -> bool:
    return tg_id in config.ADMIN_IDS


def get_days_for_plan(plan: str) -> int:
    """Вернуть срок подписки в днях на основе ключа плана."""
    if plan == "forever":
        return 9999
    digits = ''.join(ch for ch in plan if ch.isdigit())
    if digits:
        return int(digits)
    return 30


async def sub_status_text(tg_id: int) -> str:
    sub = await db.get_active_subscription(tg_id)
    if sub is None:
        return "❌ Нет активной подписки"
    if sub["expires_at"] is None:
        return "♾ Подписка: <b>Навсегда</b>"
    left = sub["expires_at"] - datetime.now(timezone.utc)
    days = left.days
    hours = left.seconds // 3600
    return (
        f"✅ Подписка активна до <b>{fmt_dt(sub['expires_at'])}</b>\n"
        f"⏳ Осталось: <b>{days} д. {hours} ч.</b>"
    )


# ---------- Пользовательские хендлеры ----------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await db.upsert_user(message.from_user.id, message.from_user.username)

    user = await db.get_user(message.from_user.id)
    if user and user["first_seen"] and (
        datetime.now(timezone.utc) - user["first_seen"]
    ).total_seconds() > 60:
        text = "С возвращением! Вы вошли в аккаунт."
    else:
        text = (
            "👋 Добро пожаловать в <b>Haron Visuals</b>!\n\n"
            "Здесь ты можешь активировать ключ, управлять подпиской "
            "и HWID.\n\n"
            "🛒 Ключи продаются на FunPay (кнопка «Купить визуалы»)."
        )

    await message.answer(text, reply_markup=main_menu())


@router.message(F.text == "👤 Профиль")
async def profile(message: Message):
    await db.upsert_user(message.from_user.id, message.from_user.username)
    user = await db.get_user(message.from_user.id)

    hwid = user["hwid"] if user and user["hwid"] else "не привязан"
    status = await sub_status_text(message.from_user.id)

    await message.answer(
        f"👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{message.from_user.id}</code>\n"
        f"📅 Регистрация: {fmt_dt(user['first_seen'])}\n"
        f"💻 HWID: <code>{hwid}</code>\n\n"
        f"{status}",
        reply_markup=main_menu(),
    )


@router.message(F.text == "🔑 Активировать ключ")
async def ask_key(message: Message, state: FSMContext):
    await state.set_state(KeyInput.waiting_key)
    await message.answer(
        "🔑 Отправь ключ в формате:\n<code>HARON-XXXX-XXXX-XXXX</code>\n\n"
        "Для отмены — /start"
    )


@router.message(KeyInput.waiting_key, F.text)
async def process_key(message: Message, state: FSMContext):
    key = message.text.strip().upper()
    if not key.startswith("HARON-"):
        await message.answer(
            "❌ Неверный формат. Пример: <code>HARON-AB12-CD34-EF56</code>\nПопробуй ещё раз."
        )
        return

    async with db.pool.acquire() as con:
        row = await con.fetchrow("SELECT * FROM license_keys WHERE key = $1", key)
        if row is None:
            await message.answer("❌ Ключ не найден.")
            return

        if row["activated_by"] is not None:
            tg_id = row["activated_by"]
            user = await db.get_user(tg_id)
            vendor_username = user.get("vendor_username") if user else "неизвестно"
            sub = await db.get_active_subscription(tg_id)

            if sub:
                if sub["expires_at"] is None:
                    status_text = "♾ Бессрочная"
                else:
                    left = sub["expires_at"] - datetime.now(timezone.utc)
                    days = left.days
                    hours = left.seconds // 3600
                    status_text = f"до {fmt_dt(sub['expires_at'])} (осталось {days} д. {hours} ч.)"
                await message.answer(
                    f"🔑 Этот ключ уже активирован пользователем <b>{vendor_username}</b>.\n"
                    f"Статус подписки: {status_text}\n"
                    f"Если это ваш ключ, войдите в бота с того аккаунта или обратитесь в поддержку."
                )
            else:
                await message.answer(
                    f"🔑 Этот ключ уже активирован, но активная подписка не найдена.\n"
                    f"Возможно, она истекла. Обратитесь в поддержку."
                )
            await state.clear()
            return

    # Ключ свободен
    await state.update_data(key=key, plan=row["plan"])
    await message.answer(
        "🔑 Ключ действителен! Теперь придумайте логин для входа в игру.\n"
        "Логин: от 3 до 64 символов, только латиница, цифры, _ . @ + -"
    )
    await state.set_state(KeyInput.waiting_username)


@router.message(KeyInput.waiting_username, F.text)
async def process_username(message: Message, state: FSMContext):
    username = message.text.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.@+-]{3,64}", username):
        await message.answer(
            "❌ Логин должен быть 3–64 символа и содержать только:\n"
            "латиницу, цифры, _ . @ + -\nПопробуйте снова."
        )
        return
    await state.update_data(username=username)
    await message.answer("Теперь введите пароль (минимум 4 символа).")
    await state.set_state(KeyInput.waiting_password)


@router.message(KeyInput.waiting_password, F.text)
async def process_password(message: Message, state: FSMContext):
    password = message.text.strip()
    if len(password) < 4:
        await message.answer("❌ Пароль должен быть хотя бы 4 символа. Попробуйте снова.")
        return

    data = await state.get_data()
    username = data["username"]
    key = data["key"]
    plan = data["plan"]

    if plan == "forever":
        payload = {
            "username": username,
            "password": password,
            "lifetime": True
        }
    else:
        days = get_days_for_plan(plan)
        payload = {
            "username": username,
            "password": password,
            "days": days
        }

    headers = {
        "X-Vendor-Key": config.VENDOR_API_KEY,
        "Content-Type": "application/json"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(config.VENDOR_API_URL, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    await message.answer(
                        f"❌ Ошибка при создании аккаунта в системе лицензирования.\n"
                        f"Код: {resp.status}\n{error_text[:200]}\n"
                        "Попробуйте позже или свяжитесь с поддержкой."
                    )
                    await state.clear()
                    return
                result = await resp.json()
    except Exception as e:
        await message.answer(f"❌ Ошибка соединения: {e}\nПовторите позже.")
        await state.clear()
        return

    if result.get("status") == "ok":
        action = result.get("action")
        expires_at = result.get("expiresAt")
        if expires_at and expires_at != 0:
            expire_date = datetime.fromtimestamp(expires_at / 1000).strftime("%d.%m.%Y %H:%M")
        else:
            expire_date = "бессрочно"

        async with db.pool.acquire() as con:
            await con.execute(
                "UPDATE license_keys SET activated_by = $1, activated_at = now() WHERE key = $2",
                message.from_user.id,
                key
            )
        await db.grant_subscription(message.from_user.id, plan, source="key")
        await db.set_vendor_username(message.from_user.id, username)

        plan_name = config.PLANS[plan][0]
        await message.answer(
            f"✅ Аккаунт в SecureFabric {action}!\n\n"
            f"👤 Логин: <code>{username}</code>\n"
            f"🔑 Пароль: <code>{password}</code>\n"
            f"🎁 Тариф: <b>{plan_name}</b>\n"
            f"⏳ Действует до: {expire_date}\n\n"
            f"📥 Скачать лаунчер: {config.LOADER_URL}\n\n"
            "Сохраните логин и пароль — они нужны для входа в игру.",
            reply_markup=main_menu()
        )
        await state.clear()
    else:
        error_code = result.get("code", "UNKNOWN")
        await message.answer(
            f"❌ SecureFabric вернул ошибку: {error_code}\n"
            "Пожалуйста, свяжитесь с поддержкой."
        )
        await state.clear()


@router.message(F.text == "💻 Сбросить HWID")
async def reset_hwid(message: Message):
    sub = await db.get_active_subscription(message.from_user.id)
    if sub is None:
        await message.answer("❌ Сброс HWID доступен только с активной подпиской.")
        return

    status, seconds_left = await db.reset_hwid(message.from_user.id)
    if status == "cooldown":
        hours = seconds_left // 3600
        await message.answer(
            f"⏳ Сбрасывать HWID можно раз в "
            f"{config.HWID_RESET_COOLDOWN_DAYS} дн.\n"
            f"Следующий сброс через: <b>{hours // 24} д. {hours % 24} ч.</b>"
        )
        return

    await message.answer(
        "✅ HWID сброшен. При следующем запуске клиента "
        "привяжется новое железо."
    )


@router.message(F.text == "🛒 Купить визуалы")
async def buy(message: Message):
    await message.answer(
        "🛒 <b>Покупка Haron Visuals</b>\n\n"
        "1. Открой лот на FunPay\n"
        "2. Оплати — ключ придёт автоматически (автовыдача)\n"
        "3. Вернись сюда и нажми «🔑 Активировать ключ»\n\n"
        "Тарифы: 30 дней / 3 месяца / навсегда",
        reply_markup=buy_keyboard(),
    )


@router.message(F.text == "🟢 Поддержка")
async def support(message: Message):
    await message.answer(
        "🟢 Возникли вопросы или проблемы — пиши:",
        reply_markup=support_keyboard(),
    )


# ---------- Админ-команды ----------

@router.message(Command("genkeys"))
async def gen_keys(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = (message.text or "").split()
    if len(parts) != 3 or parts[1] not in config.PLANS or not parts[2].isdigit():
        await message.answer(
            "Использование: <code>/genkeys план кол-во</code>\n"
            "Планы: " + ", ".join(config.PLANS.keys()) + "\n"
            "Пример: <code>/genkeys 30d 10</code>"
        )
        return

    plan, count = parts[1], min(int(parts[2]), 50)
    keys = await db.generate_keys(plan, count, message.from_user.id)
    plan_name = config.PLANS[plan][0]

    keys_text = "\n".join(f"<code>{k}</code>" for k in keys)
    await message.answer(
        f"🔑 Сгенерировано {len(keys)} ключей ({plan_name}):\n\n{keys_text}\n\n"
        f"Скопируй их в автовыдачу FunPay."
    )


@router.message(Command("give"))
async def give_sub(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = (message.text or "").split()
    if len(parts) != 3 or parts[2] not in config.PLANS or not parts[1].lstrip("-").isdigit():
        await message.answer(
            "Использование: <code>/give tg_id план</code>\n"
            "Пример: <code>/give 123456789 7d</code>"
        )
        return

    target_id, plan = int(parts[1]), parts[2]
    await db.upsert_user(target_id, None)
    await db.grant_subscription(target_id, plan, source="contest")
    plan_name = config.PLANS[plan][0]

    await message.answer(f"✅ Выдана подписка <b>{plan_name}</b> пользователю <code>{target_id}</code>")

    try:
        await message.bot.send_message(
            target_id,
            f"🎉 Тебе выдана подписка <b>Haron Visuals — {plan_name}</b>!\n"
            f"Проверь статус в «👤 Профиль».",
        )
    except Exception:
        await message.answer("⚠️ Не удалось отправить уведомление (пользователь не запускал бота).")


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not is_admin(message.from_user.id):
        return

    users, active, keys_free, keys_used = await db.stats()
    await message.answer(
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: <b>{users}</b>\n"
        f"✅ Активных подписок: <b>{active}</b>\n"
        f"🔑 Ключей свободно: <b>{keys_free}</b>\n"
        f"🔑 Ключей активировано: <b>{keys_used}</b>"
    )


# ---------- Запуск (polling для Bothost) ----------

async def on_startup():
    """Инициализация БД при старте."""
    await db.init()
    log.info("База данных инициализирована.")


async def main():
    """Точка входа: запуск бота в режиме polling."""
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)
    dp.startup.register(on_startup)

    log.info("Бот запущен и ожидает сообщения...")
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
