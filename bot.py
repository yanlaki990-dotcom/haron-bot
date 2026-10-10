"""Haron Visuals Bot"""
import asyncio, json, logging, re, uuid
from datetime import datetime, timezone
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, CallbackQuery, ReplyKeyboardMarkup
import config, db, rollypay

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("haron-bot")
router = Router()

class SubscriptionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        bot: Bot = data["bot"]
        user_id = event.from_user.id
        if user_id in config.ADMIN_IDS:
            return await handler(event, data)
        is_sub = False
        try:
            m = await bot.get_chat_member(chat_id=config.CHANNEL_ID, user_id=user_id)
            if m.status in ['creator','administrator','member']:
                is_sub = True
        except Exception as e:
            log.error(f"sub {user_id}: {e}")
        if is_sub:
            return await handler(event, data)
        if isinstance(event, CallbackQuery) and event.data == "check_sub":
            await event.answer("❌ Ты ещё не подписан!", show_alert=True)
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📢 Подписаться", url=config.CHANNEL_URL)],[InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub")]])
        if isinstance(event, Message):
            await event.answer("⚠️ <b>Подпишись на канал!</b>", reply_markup=kb)
        elif isinstance(event, CallbackQuery):
            await event.message.answer("⚠️ <b>Подпишись на канал!</b>", reply_markup=kb)
            await event.answer()
        return

def main_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🔑 Активировать ключ")],
        [KeyboardButton(text="💻 Сбросить HWID"), KeyboardButton(text="🛒 Купить визуалы")],
        [KeyboardButton(text="📢 Наш канал"), KeyboardButton(text="🟢 Поддержка")],
        [KeyboardButton(text="📄 Документы")]
    ], resize_keyboard=True)

def buy_tariffs_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"📅 30 Дней ({config.PRICES['30d']}р)", callback_data="buy_30d")],[InlineKeyboardButton(text=f"📅 90 Дней ({config.PRICES['90d']}р)", callback_data="buy_90d")],[InlineKeyboardButton(text=f"♾️ Навсегда ({config.PRICES['forever']}р)", callback_data="buy_forever")]])

def promo_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Нету промокода", callback_data="nopromo")]])

def support_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🟢 Написать в поддержку", url=config.SUPPORT_URL)]])

def channel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📢 Подписаться", url=config.CHANNEL_URL)]])

def legal_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📄 Политика", url=config.PRIVACY_URL)],[InlineKeyboardButton(text="📜 Оферта", url=config.OFFER_URL)],[InlineKeyboardButton(text="🟢 Поддержка", url=config.SUPPORT_URL)]])

class KeyInput(StatesGroup):
    waiting_key = State()
    waiting_credentials = State()

class Buy(StatesGroup):
    waiting_promo = State()

def fmt_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

def is_admin(tg_id: int) -> bool:
    return tg_id in config.ADMIN_IDS

def get_days_for_plan(plan: str) -> int:
    if plan == "forever":
        return 9999
    d = ''.join(ch for ch in plan if ch.isdigit())
    return int(d) if d else 30

def calc_price(plan: str, percent: int = 0) -> int:
    base = config.PRICES[plan]
    return base if not percent else max(round(base*(100-percent)/100),1)

def _mask_db_url(url: str) -> str:
    """Маскирует пароль в DATABASE_URL. Оставляет видимым host (ep-XXXX)."""
    if not url:
        return "(пусто)"
    try:
        # postgresql://user:password@host:port/db
        if "://" in url and "@" in url:
            proto, rest = url.split("://", 1)
            userpass, hostpart = rest.split("@", 1)
            if ":" in userpass:
                user, _ = userpass.split(":", 1)
                return f"{proto}://{user}:***@{hostpart}"
            return f"{proto}://{userpass}@{hostpart}"
        return url
    except Exception:
        return url

async def sub_status_text(tg_id: int) -> str:
    sub = await db.get_active_subscription(tg_id)
    if sub is None:
        return "❌ Нет активной подписки"
    if sub["expires_at"] is None:
        return "♾ Подписка: <b>Навсегда</b>"
    left = sub["expires_at"] - datetime.now(timezone.utc)
    return f"✅ Активна до <b>{fmt_dt(sub['expires_at'])}</b>\n⏳ Осталось: <b>{left.days} д. {left.seconds//3600} ч.</b>"

async def send_invoice(chat: Message, tg_id: int, plan: str, promo_code: str | None, percent: int):
    amount = calc_price(plan, percent)
    order_id = f"hv_{uuid.uuid4().hex[:12]}"
    try:
        pay = await rollypay.create_payment(amount, order_id, f"HaronVisuals {config.PLANS[plan][0]}" + (f" promo {promo_code}" if promo_code else ""))
    except Exception as e:
        await chat.answer(f"❌ Касса: {e}")
        return
    await db.save_payment(order_id, tg_id, plan, pay.get("payment_id",""), f"{amount:.2f}", promo_code)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"💳 Оплатить {amount}р", url=pay["pay_url"])]])
    txt = f"💳 <b>{config.PLANS[plan][0]} — {amount}р</b>"
    if promo_code:
        txt += f"\n🏷 <code>{promo_code}</code> (-{percent}%)"
    await chat.answer(txt, reply_markup=kb)

async def create_vendor_account(message: Message, username: str, password: str, plan: str, source: str):
    """Сохраняет аккаунт в Neon. Никаких внешних API."""
    try:
        await db.grant_subscription(message.from_user.id, plan, source=source)
        await db.set_vendor_username(message.from_user.id, username)
        await db.set_password(message.from_user.id, password)
    except Exception as e:
        log.error(f"save account failed for {message.from_user.id}: {e}")
        await message.answer(f"❌ Ошибка сохранения: {e}")
        return False

    sub = await db.get_active_subscription(message.from_user.id)
    if sub and sub["expires_at"]:
        exp_d = sub["expires_at"].strftime("%d.%m.%Y %H:%M")
    else:
        exp_d = "бессрочно"

    await message.answer(
        f"✅ Аккаунт создан!\n\n"
        f"👤 Логин: <code>{username}</code>\n"
        f"🔑 Пароль: <code>{password}</code>\n"
        f"🎁 Тариф: <b>{config.PLANS[plan][0]}</b>\n"
        f"⏳ До: {exp_d}\n\n"
        f"📥 Скачать лаунчер: {config.LOADER_URL}\n\n"
        f"Сохрани логин и пароль.",
        reply_markup=main_menu()
    )
    return True

# ============ DBCHECK ============

@router.message(Command("dbcheck"))
async def cmd_dbcheck(message: Message):
    if not is_admin(message.from_user.id):
        return
    try:
        masked = _mask_db_url(config.DATABASE_URL)
        async with db.pool.acquire() as con:
            total = await con.fetchval("SELECT count(*) FROM public.users")
            rows = await con.fetch(
                "SELECT tg_id, username, password, first_seen "
                "FROM public.users ORDER BY first_seen DESC LIMIT 5"
            )
        lines = [
            f"<b>DB URL:</b> <code>{masked}</code>",
            f"<b>Users count:</b> {total}",
            "",
            "<b>Last 5:</b>",
        ]
        for i, r in enumerate(rows, 1):
            ts = r["first_seen"].strftime("%d.%m %H:%M") if r["first_seen"] else "?"
            lines.append(
                f"{i}. tg_id=<code>{r['tg_id']}</code>, "
                f"user=<code>{r['username']}</code>, "
                f"pass=<code>{r['password']}</code>, "
                f"seen={ts}"
            )
        await message.answer("\n".join(lines))
    except Exception as e:
        log.error(f"dbcheck failed: {e}")
        await message.answer(f"❌ dbcheck error: <code>{e}</code>")

# ============ /DBCHECK END ============

@router.callback_query(F.data == "check_sub")
async def check_sub_callback(call: CallbackQuery, state: FSMContext):
    try:
        await call.message.delete()
    except Exception as e:
        log.warning(f"Не удалось удалить сообщение: {e}")
    await call.message.answer("✅ Спасибо! Нажми /start")
    await call.answer()

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await db.upsert_user(message.from_user.id, message.from_user.username)
    user = await db.get_user(message.from_user.id)
    if user and user["first_seen"] and (datetime.now(timezone.utc)-user["first_seen"]).total_seconds() > 60:
        text = "С возвращением! Вы вошли в аккаунт."
    else:
        text = "👋 Добро пожаловать в <b>Haron Visuals</b>!"
    await message.answer(text, reply_markup=main_menu())
    await message.answer("📄 <b>Документы:</b>", reply_markup=legal_keyboard())

@router.message(F.text == "👤 Профиль")
async def profile(message: Message):
    await db.upsert_user(message.from_user.id, message.from_user.username)
    user = await db.get_user(message.from_user.id)
    hwid = user["hwid"] if user and user["hwid"] else "не привязан"
    status = await sub_status_text(message.from_user.id)
    await message.answer(f"👤 <b>Профиль</b>\n\n🆔 <code>{message.from_user.id}</code>\n📅 {fmt_dt(user['first_seen'])}\n💻 HWID: <code>{hwid}</code>\n\n{status}", reply_markup=main_menu())

@router.message(F.text == "🔑 Активировать ключ")
async def ask_key(message: Message, state: FSMContext):
    await state.set_state(KeyInput.waiting_key)
    await message.answer("🔑 Отправь ключ:\n<code>HARON-XXXX-XXXX-XXXX</code>")

@router.message(KeyInput.waiting_key, F.text)
async def process_key(message: Message, state: FSMContext):
    key = message.text.strip().upper()
    if not key.startswith("HARON-"):
        await message.answer("❌ Формат: <code>HARON-AB12-CD34-EF56</code>")
        return
    async with db.pool.acquire() as con:
        row = await con.fetchrow("SELECT * FROM license_keys WHERE key=$1", key)
        if row is None:
            await message.answer("❌ Ключ не найден.")
            return
        if row["activated_by"] is not None:
            await message.answer("🔑 Уже активирован.")
            await state.clear()
            return
    await state.update_data(key=key, plan=row["plan"])
    await message.answer("Ок! Теперь логин и пароль через пробел.\nПример: <code>mylogin mypassword</code>")
    await state.set_state(KeyInput.waiting_credentials)

@router.message(KeyInput.waiting_credentials, F.text)
async def process_credentials(message: Message, state: FSMContext):
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("❌ Через пробел: <code>login password</code>")
        return
    username, password = parts[0].replace(" ",""), parts[1].replace(" ","")
    if not re.fullmatch(r"[A-Za-z0-9_.@+-]{3,64}", username):
        await message.answer("❌ Логин не подходит.")
        return
    if len(password) < 4:
        await message.answer("❌ Пароль мин. 4.")
        return
    data = await state.get_data()
    key, plan = data["key"], data["plan"]
    ok = await create_vendor_account(message, username, password, plan, "key")
    if not ok:
        await state.clear()
        return
    async with db.pool.acquire() as con:
        await con.execute("UPDATE license_keys SET activated_by=$1, activated_at=now() WHERE key=$2", message.from_user.id, key)
    await state.clear()

@router.message(F.text == "💻 Сбросить HWID")
async def reset_hwid(message: Message):
    sub = await db.get_active_subscription(message.from_user.id)
    if sub is None:
        await message.answer("❌ Только с подпиской.")
        return
    st, sec = await db.reset_hwid(message.from_user.id)
    if st == "cooldown":
        await message.answer(f"⏳ Через {sec//3600} ч.")
        return
    await message.answer("✅ HWID сброшен.")

@router.message(F.text == "🛒 Купить визуалы")
async def buy(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Выберите длительность подписки.", reply_markup=buy_tariffs_keyboard())

@router.callback_query(F.data.startswith("buy_"))
async def buy_tariff(call: CallbackQuery, state: FSMContext):
    await call.answer()
    plan = call.data.replace("buy_", "")
    if plan not in config.PRICES:
        return
    await state.update_data(plan=plan)
    await state.set_state(Buy.waiting_promo)
    await call.message.answer("Если у вас есть промокод, то введите его:", reply_markup=promo_keyboard())

@router.callback_query(F.data == "nopromo", Buy.waiting_promo)
async def no_promo(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    plan = data.get("plan")
    if plan not in config.PRICES:
        await state.clear()
        return
    await state.clear()
    await send_invoice(call.message, call.from_user.id, plan, None, 0)

@router.message(Buy.waiting_promo, F.text)
async def apply_promo(message: Message, state: FSMContext):
    if message.text in ("🛒 Купить визуалы","👤 Профиль","🔑 Активировать ключ","💻 Сбросить HWID","📢 Наш канал","🟢 Поддержка","📄 Документы","/start"):
        await state.clear()
        return
    data = await state.get_data()
    plan = data.get("plan")
    if plan not in config.PRICES:
        await state.clear()
        return
    code = message.text.strip().upper()
    if code in ("НЕТУ ПРОМОКОДА","НЕТ ПРОМОКОДА","БЕЗ ПРОМО"):
        await state.clear()
        await send_invoice(message, message.from_user.id, plan, None, 0)
        return
    promo = await db.get_promo(code)
    if promo is None or not promo["active"]:
        await message.answer("❌ Нет такого. Ещё раз или жми «Нету промокода».")
        return
    if promo["max_uses"] and promo["used"] >= promo["max_uses"]:
        await message.answer("❌ Промокод закончился.")
        return
    await state.clear()
    await message.answer(f"✅ Промокод применён (-{promo['percent']}%).")
    await send_invoice(message, message.from_user.id, plan, promo["code"], promo["percent"])

@router.message(F.text == "📢 Наш канал")
async def channel(message: Message):
    await message.answer("📢 Канал:", reply_markup=channel_keyboard())

@router.message(F.text == "🟢 Поддержка")
async def support(message: Message):
    await message.answer("Вопросы:", reply_markup=support_keyboard())

@router.message(F.text == "📄 Документы")
async def documents(message: Message):
    await message.answer("📄 Документы:", reply_markup=legal_keyboard())

@router.message(Command("genkeys"))
async def gen_keys(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 3 or parts[1] not in config.PLANS or not parts[2].isdigit():
        await message.answer("Пример: <code>/genkeys 30d 10</code>")
        return
    keys = await db.generate_keys(parts[1], min(int(parts[2]),50), message.from_user.id)
    await message.answer(f"🔑 {len(keys)}:\n" + "\n".join(f"<code>{k}</code>" for k in keys))

@router.message(Command("give"))
async def give_sub(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 3 or parts[2] not in config.PLANS:
        await message.answer("Пример: <code>/give 123 30d</code>")
        return
    await db.upsert_user(int(parts[1]), None)
    await db.grant_subscription(int(parts[1]), parts[2], source="contest")
    await message.answer("✅ Выдано")

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not is_admin(message.from_user.id):
        return
    u,a,kf,ku = await db.stats()
    await message.answer(f"📊 Юзеров: {u}\nАктивных: {a}\nСвободно: {kf}\nЮзано: {ku}")

@router.message(Command("post"))
async def cmd_post(message: Message):
    if not is_admin(message.from_user.id):
        return
    text = message.text.replace("/post","",1).strip()
    if not text and message.reply_to_message:
        text = message.reply_to_message.html_text or message.reply_to_message.text or ""
    if not text:
        await message.answer("Пример: <code>/post текст</code>")
        return
    ids = await db.get_all_tg_ids()
    await message.answer(f"📤 {len(ids)}...")
    ok=fail=0
    for uid in ids:
        try:
            await message.bot.send_message(uid, text)
            ok+=1
        except Exception:
            fail+=1
    await message.answer(f"✅ {ok} ок, {fail} ошибок")

@router.message(Command("createpromo"))
async def create_promo_cmd(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) not in (3,4):
        await message.answer("Пример: <code>/createpromo PRONIK20 20 100</code>")
        return
    try:
        percent = int(parts[2]); max_uses = int(parts[3]) if len(parts)==4 else 0
    except ValueError:
        await message.answer("❌ Числа.")
        return
    if not 1 <= percent <= 90:
        await message.answer("❌ 1-90.")
        return
    await db.create_promo(parts[1], percent, max_uses)
    await message.answer(f"✅ <code>{parts[1].upper()}</code> -{percent}% лимит {'∞' if max_uses==0 else max_uses}")

@router.message(Command("promos"))
async def promos_list(message: Message):
    if not is_admin(message.from_user.id):
        return
    rows = await db.list_promos()
    if not rows:
        await message.answer("Нет промо.")
        return
    txt = "🏷 <b>Промо:</b>\n\n"
    for r in rows:
        lim = "∞" if r["max_uses"]==0 else f"{r['used']}/{r['max_uses']}"
        txt += f"{'✅' if r['active'] else '❌'} <code>{r['code']}</code> -{r['percent']}% {lim}\n"
    await message.answer(txt)

@router.message(Command("delpromo"))
async def del_promo_cmd(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("Пример: <code>/delpromo CODE</code>")
        return
    await db.del_promo(parts[1])
    await message.answer("✅ Удалено.")

@router.message(F.text)
async def process_pending_vendor(message: Message):
    if message.text.startswith("/") or message.text in ("👤 Профиль","🔑 Активировать ключ","💻 Сбросить HWID","🛒 Купить визуалы","📢 Наш канал","🟢 Поддержка","📄 Документы"):
        return
    pend = await db.get_pending(message.from_user.id)
    if pend is None:
        return
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("❌ Введи логин и пароль через пробел.\nПример: <code>mylogin mypassword</code>")
        return
    username, password = parts[0].replace(" ",""), parts[1].replace(" ","")
    if not re.fullmatch(r"[A-Za-z0-9_.@+-]{3,64}", username):
        await message.answer("❌ Логин не подходит.")
        return
    if len(password) < 4:
        await message.answer("❌ Пароль мин. 4.")
        return
    ok = await create_vendor_account(message, username, password, pend["plan"], "rollypay")
    if ok:
        await db.clear_pending(message.from_user.id)

async def rollypay_webhook(request: web.Request):
    raw = await request.read()
    timestamp = request.headers.get("X-Timestamp", "")
    signature = request.headers.get("X-Signature", "")
    log.info(f"WEBHOOK HEADERS: {dict(request.headers)}")
    log.info(f"WEBHOOK BODY: {raw.decode()}")
    if not rollypay.verify_signature(raw, timestamp, signature):
        log.warning("RollyPay webhook: bad signature")
        return web.Response(status=403, text="bad sign")
    try:
        event = json.loads(raw.decode())
    except Exception as e:
        log.error(f"RollyPay webhook json error: {e}")
        return web.Response(status=400, text="bad json")

    if event.get("event_type") == "payment.paid" and event.get("status") == "paid":
        order_id = event.get("order_id", "")
        row = await db.set_payment_paid(order_id)
        if row is not None:
            if row["promo_code"]:
                try:
                    await db.bump_promo(row["promo_code"])
                except Exception:
                    pass
            await db.set_pending(row["tg_id"], row["plan"])
            asyncio.create_task(
                request.app["bot"].send_message(
                    row["tg_id"],
                    f"✅ Оплата прошла! <b>{config.PLANS[row['plan']][0]}</b>\n\n"
                    f"Теперь введи логин и пароль через пробел.\n"
                    f"Пример: <code>mylogin mypassword</code>"
                )
            )
    return web.Response(text="OK")

async def on_startup():
    await db.init()
    log.info("DB ok")

async def main():
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.message.middleware(SubscriptionMiddleware())
    dp.callback_query.middleware(SubscriptionMiddleware())
    dp.include_router(router)
    dp.startup.register(on_startup)
    app = web.Application()
    app["bot"] = bot
    app.router.add_post("/webhooks/rollypay", rollypay_webhook)
    runner = web.AppRunner(app)
    await runner.setup()

    log.info(f"Web server started on port {config.PORT}")
    await web.TCPSite(runner, "0.0.0.0", config.PORT).start()

    await dp.start_polling(bot, drop_pending_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
