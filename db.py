"""
Haron Visuals Bot — база данных (Postgres через asyncpg).

Таблицы:
  users         — все, кто запускал бота
  subscriptions — активные/прошлые подписки
  license_keys  — ключи (генерируются админом, продаются через FunPay автовыдачу)
"""

import secrets
import string
from datetime import datetime, timedelta, timezone

import asyncpg

import config

pool: asyncpg.Pool = None


async def init():
    """Создаёт пул соединений и таблицы, если их нет."""
    global pool
    pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=1, max_size=5)

    async with pool.acquire() as con:
        await con.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                tg_id       BIGINT PRIMARY KEY,
                username    TEXT,
                first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
                hwid        TEXT,
                hwid_reset_at TIMESTAMPTZ
            );

            CREATE TABLE IF NOT EXISTS subscriptions (
                id          SERIAL PRIMARY KEY,
                tg_id       BIGINT NOT NULL REFERENCES users(tg_id),
                plan        TEXT NOT NULL,
                started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at  TIMESTAMPTZ,          -- NULL = навсегда
                source      TEXT NOT NULL DEFAULT 'key'  -- key / admin / contest
            );

            CREATE TABLE IF NOT EXISTS license_keys (
                key         TEXT PRIMARY KEY,
                plan        TEXT NOT NULL,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                created_by  BIGINT,
                activated_by BIGINT,
                activated_at TIMESTAMPTZ
            );
            """
        )


async def upsert_user(tg_id: int, username: str | None):
    async with pool.acquire() as con:
        await con.execute(
            """
            INSERT INTO users (tg_id, username) VALUES ($1, $2)
            ON CONFLICT (tg_id) DO UPDATE SET username = EXCLUDED.username
            """,
            tg_id,
            username,
        )


async def get_user(tg_id: int):
    async with pool.acquire() as con:
        return await con.fetchrow("SELECT * FROM users WHERE tg_id = $1", tg_id)


async def get_active_subscription(tg_id: int):
    """Возвращает активную подписку (не истёкшую или вечную), либо None."""
    async with pool.acquire() as con:
        return await con.fetchrow(
            """
            SELECT * FROM subscriptions
            WHERE tg_id = $1 AND (expires_at IS NULL OR expires_at > now())
            ORDER BY (expires_at IS NULL) DESC, expires_at DESC
            LIMIT 1
            """,
            tg_id,
        )


async def grant_subscription(tg_id: int, plan: str, source: str = "key"):
    """
    Выдаёт подписку. Если уже есть активная временная — новая добавляется
    К оставшемуся времени (стакается), а не заменяет его.
    Вечная подписка перекрывает всё.
    """
    name, days, _paid = config.PLANS[plan]
    now = datetime.now(timezone.utc)

    async with pool.acquire() as con:
        current = await con.fetchrow(
            """
            SELECT * FROM subscriptions
            WHERE tg_id = $1 AND (expires_at IS NULL OR expires_at > now())
            ORDER BY (expires_at IS NULL) DESC, expires_at DESC
            LIMIT 1
            """,
            tg_id,
        )

        # Уже есть вечная — ничего делать не надо
        if current and current["expires_at"] is None:
            return current

        if days is None:
            expires_at = None  # навсегда
        else:
            # Стакаем к текущему остатку, если он есть
            base = now
            if current and current["expires_at"] and current["expires_at"] > now:
                base = current["expires_at"]
            expires_at = base + timedelta(days=days)

        return await con.fetchrow(
            """
            INSERT INTO subscriptions (tg_id, plan, expires_at, source)
            VALUES ($1, $2, $3, $4)
            RETURNING *
            """,
            tg_id,
            plan,
            expires_at,
            source,
        )


# ---------- Ключи ----------

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
                    await con.execute(
                        "INSERT INTO license_keys (key, plan, created_by) VALUES ($1, $2, $3)",
                        key,
                        plan,
                        admin_id,
                    )
                    keys.append(key)
                    break
                except asyncpg.UniqueViolationError:
                    continue  # коллизия ключа — генерим заново
    return keys


async def activate_key(key: str, tg_id: int):
    """
    Пытается активировать ключ.
    Возвращает (status, plan):
      status: 'ok' | 'not_found' | 'used'
    """
    key = key.strip().upper()
    async with pool.acquire() as con:
        row = await con.fetchrow("SELECT * FROM license_keys WHERE key = $1", key)
        if row is None:
            return "not_found", None
        if row["activated_by"] is not None:
            return "used", None

        await con.execute(
            """
            UPDATE license_keys
            SET activated_by = $1, activated_at = now()
            WHERE key = $2
            """,
            tg_id,
            key,
        )

    await grant_subscription(tg_id, row["plan"], source="key")
    return "ok", row["plan"]


# ---------- HWID ----------

async def reset_hwid(tg_id: int):
    """
    Сбрасывает HWID с учётом кулдауна.
    Возвращает (status, seconds_left):
      status: 'ok' | 'cooldown'
    """
    now = datetime.now(timezone.utc)
    cooldown = timedelta(days=config.HWID_RESET_COOLDOWN_DAYS)

    async with pool.acquire() as con:
        user = await con.fetchrow("SELECT * FROM users WHERE tg_id = $1", tg_id)
        if user and user["hwid_reset_at"]:
            passed = now - user["hwid_reset_at"]
            if passed < cooldown:
                return "cooldown", int((cooldown - passed).total_seconds())

        await con.execute(
            "UPDATE users SET hwid = NULL, hwid_reset_at = $1 WHERE tg_id = $2",
            now,
            tg_id,
        )
    return "ok", 0


# ---------- Статистика для админа ----------

async def stats():
    async with pool.acquire() as con:
        users = await con.fetchval("SELECT count(*) FROM users")
        active = await con.fetchval(
            """
            SELECT count(DISTINCT tg_id) FROM subscriptions
            WHERE expires_at IS NULL OR expires_at > now()
            """
        )
        keys_free = await con.fetchval(
            "SELECT count(*) FROM license_keys WHERE activated_by IS NULL"
        )
        keys_used = await con.fetchval(
            "SELECT count(*) FROM license_keys WHERE activated_by IS NOT NULL"
        )
    return users, active, keys_free, keys_used