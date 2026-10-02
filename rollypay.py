"""RollyPay API: создание платежа + проверка подписи вебхуков.

Доки: https://docs.rollypay.io/api/payments , https://docs.rollypay.io/api/callbacks
"""

import hashlib
import hmac
import uuid

import aiohttp

import config


def verify_signature(raw_body: bytes, timestamp: str, signature: str) -> bool:
    """Проверка X-Signature = HMAC_SHA256(timestamp + '.' + raw_body, signing_secret)."""
    if not timestamp or not signature or not config.ROLLYPAY_SIGNING_SECRET:
        return False
    mac = hmac.new(
        config.ROLLYPAY_SIGNING_SECRET.encode(),
        f"{timestamp}.".encode() + raw_body,
        hashlib.sha256,
    )
    expected = mac.hexdigest()
    return hmac.compare_digest(expected, signature)


async def create_payment(amount_rub: int, order_id: str, description: str, test: bool = False) -> dict:
    """POST /api/v1/payments -> dict с pay_url, payment_id. Кидает RuntimeError при ошибке API."""
    if not config.ROLLYPAY_API_KEY:
        raise RuntimeError("ROLLYPAY_API_KEY не задан (env на bothost)")

    payload: dict = {
        "amount": f"{amount_rub:.2f}",
        "payment_currency": "RUB",
        "order_id": order_id,
        "description": description,
    }
    if config.ROLLYPAY_TERMINAL_ID:
        payload["terminal_id"] = config.ROLLYPAY_TERMINAL_ID
    if test:
        payload["test"] = True

    headers = {
        "Content-Type": "application/json",
        "X-API-Key": config.ROLLYPAY_API_KEY,
        "X-Nonce": uuid.uuid4().hex,
    }
    url = config.ROLLYPAY_API_BASE.rstrip("/") + "/payments"
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload, headers=headers, timeout=20) as r:
            data = await r.json(content_type=None)
            if r.status != 200:
                raise RuntimeError(f"RollyPay {r.status}: {data}")
            return data
