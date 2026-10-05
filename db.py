import secrets
import string
from datetime import datetime, timedelta, timezone
import asyncpg
import config

pool: asyncpg.Pool = None

async def init():
    global pool
    pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=1, max_size=5)
    async with pool.acquire() as con:
        await con.execute("""CREATE TABLE IF NOT EXISTS users (tg_id BIGINT PRIMARY KEY, username TEXT, first_seen TIMESTAMPTZ NOT NULL DEFAULT now(), hwid TEXT, hwid_reset_at TIMESTAMPTZ);
CREATE TABLE IF NOT EXISTS subscriptions (id SERIAL PRIMARY KEY, tg_id BIGINT NOT NULL REFERENCES users(tg_id), plan TEXT NOT NULL, started_at TIMESTAMPTZ NOT NULL DEFAULT now(), expires_at TIMESTAMPTZ, source TEXT NOT NULL DEFAULT 'key');
CREATE TABLE IF NOT EXISTS license_keys (key TEXT PRIMARY KEY, plan TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT, activated_by BIGINT, activated_at TIMESTAMPTZ);
CREATE TABLE IF NOT EXISTS payments (order_id TEXT PRIMARY KEY, tg_id BIGINT NOT NULL, plan TEXT NOT NULL, payment_id TEXT, amount TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'created', created_at TIMESTAMPTZ NOT NULL DEFAULT now(), paid_at TIMESTAMPTZ);
CREATE TABLE IF NOT EXISTS promos (code TEXT PRIMARY KEY, percent INT NOT NULL, max_uses INT NOT NULL DEFAULT 0, used INT NOT NULL DEFAULT 0, active BOOL NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS pending_vendor (tg_id BIGINT PRIMARY KEY, plan TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());""")
        await con.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS vendor_username TEXT")
        await con.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password TEXT")
        await con.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS promo_code TEXT")

async def upsert_user(tg_id: int, username: str | None):
    async with pool.acquire() as con:
        await con.execute("INSERT INTO users (tg_id, username) VALUES ($1,$2) ON CONFLICT (tg_id) DO UPDATE SET username=EXCLUDED.username", tg_id, username)

async def get_user(tg_id: int):
    async with pool.acquire() as con:
        return await con.fetchrow("SELECT * FROM users WHERE tg_id=$1", tg_id)

async def get_user_by_username(username: str):
    async with pool.acquire() as con:
        return await con.fetchrow("SELECT * FROM users WHERE username=$1", username)

async def set_password(tg_id: int, password: str):
    async with pool.acquire() as con:
        await con.execute("UPDATE users SET password=$1 WHERE tg_id=$2", password, tg_id)

async def get_active_subscription(tg_id: int):
    async with pool.acquire() as con:
        return await con.fetchrow("SELECT * FROM subscriptions WHERE tg_id=$1 AND (expires_at IS NULL OR expires_at>now()) ORDER BY (expires_at IS NULL) DESC, expires_at DESC LIMIT 1", tg_id)

async def grant_subscription(tg_id: int, plan: str, source: str = "key"):
    name, days, _paid = config.PLANS[plan]
    now = datetime.now(timezone.utc)
    async with pool.acquire() as con:
        current = await con.fetchrow("SELECT * FROM subscriptions WHERE tg_id=$1 AND (expires_at IS NULL OR expires_at>now()) ORDER BY (expires_at IS NULL) DESC, expires_at DESC LIMIT 1", tg_id)
        if current and current["expires_at"] is None:
            return current
        if days is None:
            expires_at = None
        else:
            base = now
            if current and current["expires_at"] and current["expires_at"] > now:
                base = current["expires_at"]
            expires_at = base + timedelta(days=days)
        return await con.fetchrow("INSERT INTO subscriptions (tg_id, plan, expires_at, source) VALUES ($1,$2,$3,$4) RETURNING *", tg_id, plan, expires_at, source)

async def get_vendor_username(tg_id: int):
    async with pool.acquire() as con:
        return await con.fetchval("SELECT vendor_username FROM users WHERE tg_id=$1", tg_id)

async def set_vendor_username(tg_id: int, vendor_username: str):
    async with pool.acquire() as con:
        await con.execute("UPDATE users SET vendor_username=$1 WHERE tg_id=$2", vendor_username, tg_id)

def _make_key() -> str:
    alphabet = string.ascii_uppercase + string.digits
    part = lambda: "".join(secrets.choice(alphabet) for _ in range(4))
    return f"HARON-{part()}-{part()}-{part()}"

async def generate_keys(plan: str, count: int, admin_id: int) -> list[str]:
    keys = []
    async with pool.acquire() as con:
        for _ in range(count):
            while True:
                key = _make_key()
                try:
                    await con.execute("INSERT INTO license_keys (key, plan, created_by) VALUES ($1,$2,$3)", key, plan, admin_id)
                    keys.append(key)
                    break
                except asyncpg.UniqueViolationError:
                    continue
    return keys

async def activate_key(key: str, tg_id: int):
    key = key.strip().upper()
    async with pool.acquire() as con:
        row = await con.fetchrow("SELECT * FROM license_keys WHERE key=$1", key)
        if row is None:
            return "not_found", None
        if row["activated_by"] is not None:
            return "used", None
        await con.execute("UPDATE license_keys SET activated_by=$1, activated_at=now() WHERE key=$2", tg_id, key)
    await grant_subscription(tg_id, row["plan"], source="key")
    return "ok", row["plan"]

async def reset_hwid(tg_id: int):
    now = datetime.now(timezone.utc)
    cooldown = timedelta(days=config.HWID_RESET_COOLDOWN_DAYS)
    async with pool.acquire() as con:
        user = await con.fetchrow("SELECT * FROM users WHERE tg_id=$1", tg_id)
        if user and user["hwid_reset_at"]:
            passed = now - user["hwid_reset_at"]
            if passed < cooldown:
                return "cooldown", int((cooldown-passed).total_seconds())
        await con.execute("UPDATE users SET hwid=NULL, hwid_reset_at=$1 WHERE tg_id=$2", now, tg_id)
    return "ok", 0

async def stats():
    async with pool.acquire() as con:
        users = await con.fetchval("SELECT count(*) FROM users")
        active = await con.fetchval("SELECT count(DISTINCT tg_id) FROM subscriptions WHERE expires_at IS NULL OR expires_at>now()")
        keys_free = await con.fetchval("SELECT count(*) FROM license_keys WHERE activated_by IS NULL")
        keys_used = await con.fetchval("SELECT count(*) FROM license_keys WHERE activated_by IS NOT NULL")
    return users, active, keys_free, keys_used

async def save_payment(order_id: str, tg_id: int, plan: str, payment_id: str, amount: str, promo_code: str | None = None):
    async with pool.acquire() as con:
        await con.execute("INSERT INTO payments (order_id,tg_id,plan,payment_id,amount,status,promo_code) VALUES ($1,$2,$3,$4,$5,'created',$6) ON CONFLICT (order_id) DO UPDATE SET payment_id=EXCLUDED.payment_id, promo_code=EXCLUDED.promo_code", order_id, tg_id, plan, payment_id, amount, promo_code)

async def set_payment_paid(order_id: str):
    async with pool.acquire() as con:
        row = await con.fetchrow("SELECT * FROM payments WHERE order_id=$1", order_id)
        if row is None or row["status"] == "paid":
            return None
        await con.execute("UPDATE payments SET status='paid', paid_at=now() WHERE order_id=$1", order_id)
        return row

async def get_all_tg_ids():
    async with pool.acquire() as con:
        rows = await con.fetch("SELECT tg_id FROM users")
        return [r["tg_id"] for r in rows]

async def create_promo(code: str, percent: int, max_uses: int = 0):
    code = code.strip().upper()
    async with pool.acquire() as con:
        await con.execute("INSERT INTO promos (code, percent, max_uses) VALUES ($1,$2,$3) ON CONFLICT (code) DO UPDATE SET percent=EXCLUDED.percent, max_uses=EXCLUDED.max_uses, active=TRUE", code, percent, max_uses)

async def get_promo(code: str):
    async with pool.acquire() as con:
        return await con.fetchrow("SELECT * FROM promos WHERE code=$1", code.strip().upper())

async def list_promos():
    async with pool.acquire() as con:
        return await con.fetch("SELECT * FROM promos ORDER BY created_at DESC")

async def del_promo(code: str):
    async with pool.acquire() as con:
        await con.execute("DELETE FROM promos WHERE code=$1", code.strip().upper())

async def bump_promo(code: str):
    async with pool.acquire() as con:
        await con.execute("UPDATE promos SET used=used+1 WHERE code=$1", code.strip().upper())

async def set_pending(tg_id: int, plan: str):
    async with pool.acquire() as con:
        await con.execute("INSERT INTO pending_vendor (tg_id, plan) VALUES ($1,$2) ON CONFLICT (tg_id) DO UPDATE SET plan=EXCLUDED.plan, created_at=now()", tg_id, plan)

async def get_pending(tg_id: int):
    async with pool.acquire() as con:
        return await con.fetchrow("SELECT * FROM pending_vendor WHERE tg_id=$1", tg_id)

async def clear_pending(tg_id: int):
    async with pool.acquire() as con:
        await con.execute("DELETE FROM pending_vendor WHERE tg_id=$1", tg_id)
