"""
Haron Visuals Bot — @HaronVisualsBot

Функции:
  /start            — приветствие + главное меню (reply-клавиатура, как у Archive Visual bot)
  Профиль           — статус подписки, HWID, дата регистрации
  Активировать ключ — ввод ключа HARON-XXXX-XXXX-XXXX (продаются на FunPay с автовыдачей)
  Сбросить HWID     — с кулдауном
  Купить визуалы    — ссылка на лот FunPay
  Поддержка         — ссылка на тебя

Админ-команды:
  /genkeys <план> <кол-во>  — сгенерировать ключи (1d/7d/30d/90d/forever)
  /give <tg_id> <план>      — выдать подписку вручную (конкурсы)
  /stats                    — статистика

Запуск: webhook-режим для Render (aiohttp-сервер, здоровье на "/").
"""

import asyncio
import logging
from datetime import datetime, timezone

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
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

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


# ---------- FSM для ввода ключа ----------

class KeyInput(StatesGroup):
    waiting_key = State()


# ---------- Хелперы ----------

def fmt_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


def is_admin(tg_id: int) -> bool:
    return tg_id in config.ADMIN_IDS


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
async def activate_key(message: Message, state: FSMContext):
    key = message.text.strip().upper()

    if not key.startswith("HARON-"):
        await message.answer(
            "❌ Неверный формат ключа. Пример:\n"
            "<code>HARON-AB12-CD34-EF56</code>\n\nПопробуй ещё раз или /start"
        )
        return

    status, plan = await db.activate_key(key, message.from_user.id)

    if status == "not_found":
        await message.answer("❌ Такого ключа не существует. Проверь и попробуй ещё раз.")
        return
    if status == "used":
        await message.answer("❌ Этот ключ уже был активирован.")
        await state.clear()
        return

    await state.clear()
    plan_name = config.PLANS[plan][0]
    sub_text = await sub_status_text(message.from_user.id)
    await message.answer(
        f"✅ Ключ активирован!\n"
        f"🎁 Тариф: <b>{plan_name}</b>\n\n{sub_text}",
        reply_markup=main_menu(),
    )


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


# ---------- Запуск (webhook для Render) ----------

async def on_startup(bot: Bot):
    await db.init()
    if config.WEBHOOK_BASE_URL:
        await bot.set_webhook(
            config.WEBHOOK_BASE_URL + config.WEBHOOK_PATH,
            drop_pending_updates=True,
        )
        log.info("Webhook set: %s", config.WEBHOOK_BASE_URL + config.WEBHOOK_PATH)


async def health(request: web.Request) -> web.Response:
    """Health-check для Render и UptimeRobot."""
    return web.Response(text="Haron Visuals Bot: OK")


def main():
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)
    dp.startup.register(on_startup)

    app = web.Application()
    app.router.add_get("/", health)

    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=config.WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    web.run_app(app, host="0.0.0.0", port=config.PORT)


if __name__ == "__main__":
    main()