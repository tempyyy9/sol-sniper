"""Мастер настройки: задаёт вопросы, пишет .env и входит в Telegram. Запуск: ./venv/bin/python configure.py"""
import asyncio
import os

ENV = ".env"
old = {}
if os.path.exists(ENV):
    for line in open(ENV, encoding="utf-8"):
        if "=" in line and not line.startswith("#"):
            k, v = line.rstrip("\n").split("=", 1)
            old[k] = v

FIELDS = [
    ("TG_API_ID", "Telegram api_id (цифры с my.telegram.org)", ""),
    ("TG_API_HASH", "Telegram api_hash (длинная строка с my.telegram.org)", ""),
    ("CHANNEL", "Канал: username без @ (для теста — свой тестовый канал)", ""),
    ("PRIVATE_KEY", "Приватный ключ НОВОГО кошелька (из Phantom)", ""),
    ("RPC_URL", "Ссылка Helius RPC (https://mainnet.helius-rpc.com/?api-key=...)", "https://api.mainnet-beta.solana.com"),
    ("BUY_AMOUNT_SOL", "Сколько SOL тратить на покупку", "2"),
    ("SLIPPAGE", "Проскальзывание, % (100 = любая цена)", "50"),
    ("PRIORITY_FEE", "Приоритетная комиссия, SOL (больше = быстрее)", "0.01"),
    ("WINDOW_START", "Начало ожидания, пример 2026-11-01T17:30:00+08:00 (пусто = сразу)", ""),
    ("WINDOW_END", "Конец ожидания (пусто = без конца)", ""),
    ("TOKEN_KEYWORDS", "Ожидаемое название токена (через запятую)", "catpad"),
    ("NAME_AUTOBUY", "Покупать по названию до поста? off (только уведомить) / channel_link / any", "off"),
    ("DRY_RUN", "Тестовый режим без покупки? true / false", "true"),
]
FIXED = {"EXTRA_SEND_URLS": "", "POOL": "auto", "REQUIRE_PUMP_SUFFIX": "false",
         "POLL_INTERVAL": "1.0", "MAX_ATTEMPTS": "4", "SESSION_NAME": "sniper", "SCOUT": "true"}

print("Отвечай на вопросы. Enter — оставить значение в [скобках].\n")
vals = {}
for key, q, default in FIELDS:
    cur = old.get(key, default)
    shown = (cur[:6] + "…") if key == "PRIVATE_KEY" and cur else cur
    ans = input(f"{q}\n[{shown}]: ").strip()
    vals[key] = ans or cur
    print()
vals["CHANNEL"] = vals["CHANNEL"].lstrip("@").replace("https://t.me/", "")
for k, v in FIXED.items():
    vals[k] = old.get(k, v)

with open(ENV, "w", encoding="utf-8") as f:
    for k, v in vals.items():
        f.write(f"{k}={v}\n")
os.chmod(ENV, 0o600)
print("✅ Настройки сохранены в .env\n")

# Проверка ключа кошелька
try:
    from solders.keypair import Keypair
    print("Кошелёк:", Keypair.from_base58_string(vals["PRIVATE_KEY"]).pubkey())
except Exception as e:
    print("❌ Приватный ключ не подходит:", e, "\nЗапусти configure.py ещё раз.")
    raise SystemExit(1)

# Вход в Telegram (один раз). Код придёт в приложение Telegram.
async def login():
    from telethon import TelegramClient
    c = TelegramClient(vals["SESSION_NAME"], int(vals["TG_API_ID"]), vals["TG_API_HASH"])
    await c.start()
    me = await c.get_me()
    print(f"✅ Вошёл в Telegram как {me.first_name}")
    try:
        from telethon.tl.functions.channels import JoinChannelRequest
        await c(JoinChannelRequest(vals["CHANNEL"]))
        print(f"✅ Подписан на @{vals['CHANNEL']}")
    except Exception as e:
        print("Канал:", e)
    await c.disconnect()

print("\nВход в Telegram: введи номер телефона в формате +7..., потом код из Telegram (и пароль 2FA, если есть).")
asyncio.run(login())
print("\nВсё готово. Запуск: ./start.sh   Логи: ./logs.sh   Стоп: ./stop.sh")
