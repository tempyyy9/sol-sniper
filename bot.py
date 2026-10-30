"""
SOL-снайпер: слушает Telegram-канал, находит адрес токена Solana и сразу покупает.

Запуск:
  python bot.py                     — боевой режим (или DRY_RUN=true в .env — без покупки)
  python bot.py --test "текст"      — проверить, какой адрес бот найдёт в тексте (без сети)
  python bot.py --test-buy MINT     — реальная тестовая покупка одного токена на BUY_AMOUNT_SOL
"""
import asyncio
import base64
import os
import re
import sys
import time
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

load_dotenv()

# ---------- настройки ----------
API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
CHANNEL = os.getenv("CHANNEL", "").lstrip("@")
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")
EXTRA_SEND_URLS = [u.strip() for u in os.getenv("EXTRA_SEND_URLS", "").split(",") if u.strip()]
BUY_AMOUNT_SOL = float(os.getenv("BUY_AMOUNT_SOL", "2"))
SLIPPAGE = float(os.getenv("SLIPPAGE", "50"))            # проценты
PRIORITY_FEE = float(os.getenv("PRIORITY_FEE", "0.005")) # в SOL
POOL = os.getenv("POOL", "auto")                          # auto / pump / raydium / pump-amm / bonk / launchlab
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
REQUIRE_PUMP_SUFFIX = os.getenv("REQUIRE_PUMP_SUFFIX", "false").lower() == "true"
WINDOW_START = os.getenv("WINDOW_START", "")              # ISO, напр. 2026-11-01T18:00:00+08:00
WINDOW_END = os.getenv("WINDOW_END", "")
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "1.0"))  # подстраховка опросом, сек (0 = выкл)
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "4"))
SESSION = os.getenv("SESSION_NAME", "sniper")
# Разведка по названию: off = только уведомлять; channel_link = покупать, если название совпало И в метаданных ссылка на канал;
# any = покупать первый новый токен с таким названием (ОПАСНО: подделки)
NAME_AUTOBUY = os.getenv("NAME_AUTOBUY", "off")
SCOUT = os.getenv("SCOUT", "true").lower() == "true"

PUMPPORTAL_URL = "https://pumpportal.fun/api/trade-local"

# ---------- извлечение адреса ----------
B58 = r"[1-9A-HJ-NP-Za-km-z]{32,44}"
RE_PUMP_LINK = re.compile(r"pump\.fun/(?:coin/)?(" + B58 + r")")
RE_DEX_LINK = re.compile(r"dexscreener\.com/solana/(" + B58 + r")", re.I)
RE_ANY = re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])(" + B58 + r")(?![1-9A-HJ-NP-Za-km-z])")

IGNORE = {
    "So11111111111111111111111111111111111111112",   # wSOL
    "11111111111111111111111111111111",              # System
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",   # Token program
    "EPjFWJd5ynKCZsByPmekzDhPzX7FHoDPFvDDNkRLWfo1",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}


def extract_candidates(text: str):
    """Возвращает список (адрес, тип), лучшие кандидаты первыми."""
    if not text:
        return []
    out, seen = [], set()

    def add(addr, kind):
        if addr in IGNORE or addr in seen:
            return
        seen.add(addr)
        out.append((addr, kind))

    for m in RE_PUMP_LINK.finditer(text):
        add(m.group(1), "pump")
    for m in RE_DEX_LINK.finditer(text):
        add(m.group(1), "dex")      # может быть адрес пары — уточняем через API
    for m in RE_ANY.finditer(text):
        add(m.group(1), "raw")

    def score(item):
        addr, kind = item
        s = {"pump": 0, "raw": 2, "dex": 3}[kind]
        if addr.endswith("pump") or addr.endswith("bonk"):
            s -= 1
        return s

    out.sort(key=score)
    if REQUIRE_PUMP_SUFFIX:
        out = [c for c in out if c[0].endswith("pump")]
    return out


def message_text(msg) -> str:
    """Текст поста + скрытые ссылки + ссылки в кнопках."""
    parts = [msg.message or ""]
    for ent in (msg.entities or []):
        url = getattr(ent, "url", None)
        if url:
            parts.append(url)
    markup = getattr(msg, "reply_markup", None)
    for row in getattr(markup, "rows", []) or []:
        for btn in row.buttons:
            url = getattr(btn, "url", None)
            if url:
                parts.append(url)
    return "\n".join(parts)


def in_window() -> bool:
    now = datetime.now(timezone.utc)
    if WINDOW_START and now < datetime.fromisoformat(WINDOW_START):
        return False
    if WINDOW_END and now > datetime.fromisoformat(WINDOW_END):
        return False
    return True


# ---------- покупка ----------
class Buyer:
    def __init__(self):
        from solders.keypair import Keypair
        self.kp = Keypair.from_base58_string(PRIVATE_KEY)
        self.pub = str(self.kp.pubkey())
        self.http = httpx.AsyncClient(http2=True, timeout=10)
        self.bought = False
        self.lock = asyncio.Lock()

    async def warmup(self):
        """Открываем соединения заранее, чтобы первая покупка не ждала TLS."""
        for url in [RPC_URL, *EXTRA_SEND_URLS]:
            try:
                await self.http.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "getHealth"})
            except Exception:
                pass
        try:
            await self.http.get("https://pumpportal.fun")
        except Exception:
            pass

    async def balance(self) -> float:
        r = await self.http.post(RPC_URL, json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [self.pub]})
        return r.json()["result"]["value"] / 1e9

    async def resolve_dex(self, addr: str) -> str:
        """Ссылка DexScreener может вести на пару, а не на токен — достаём адрес токена."""
        try:
            r = await self.http.get(f"https://api.dexscreener.com/latest/dex/pairs/solana/{addr}", timeout=3)
            pairs = r.json().get("pairs") or []
            if pairs:
                return pairs[0]["baseToken"]["address"]
        except Exception:
            pass
        return addr

    async def build_tx(self, mint: str) -> bytes:
        r = await self.http.post(PUMPPORTAL_URL, data={
            "publicKey": self.pub, "action": "buy", "mint": mint,
            "amount": BUY_AMOUNT_SOL, "denominatedInSol": "true",
            "slippage": SLIPPAGE, "priorityFee": PRIORITY_FEE, "pool": POOL,
        })
        if r.status_code != 200:
            raise RuntimeError(f"PumpPortal {r.status_code}: {r.text[:200]}")
        return r.content

    async def send(self, raw: bytes) -> str:
        from solders.transaction import VersionedTransaction
        tx = VersionedTransaction.from_bytes(raw)
        signed = VersionedTransaction(tx.message, [self.kp])
        b64 = base64.b64encode(bytes(signed)).decode()
        body = {"jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
                "params": [b64, {"encoding": "base64", "skipPreflight": True, "maxRetries": 0}]}
        urls = [RPC_URL, *EXTRA_SEND_URLS]
        results = await asyncio.gather(*[self.http.post(u, json=body) for u in urls], return_exceptions=True)
        for res in results:
            if isinstance(res, httpx.Response):
                j = res.json()
                if "result" in j:
                    return j["result"]
        raise RuntimeError(f"send failed: {results}")

    async def confirm(self, sig: str, timeout=25) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            r = await self.http.post(RPC_URL, json={"jsonrpc": "2.0", "id": 1, "method": "getSignatureStatuses",
                                                   "params": [[sig], {"searchTransactionHistory": False}]})
            st = r.json()["result"]["value"][0]
            if st:
                if st.get("err"):
                    raise RuntimeError(f"tx error: {st['err']}")
                if st.get("confirmationStatus") in ("confirmed", "finalized"):
                    return True
            await asyncio.sleep(0.4)
        return False

    async def buy(self, mint: str, notify):
        async with self.lock:
            if self.bought:
                return
            self.bought = True          # покупаем ОДИН раз
        t0 = time.time()
        if DRY_RUN:
            await notify(f"🧪 DRY RUN: купил бы {mint} на {BUY_AMOUNT_SOL} SOL")
            return
        last_err = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                raw = await self.build_tx(mint)
                sig = await self.send(raw)
                print(f"[{attempt}] отправлено за {time.time()-t0:.2f}s: {sig}")
                if await self.confirm(sig):
                    await notify(f"✅ КУПЛЕНО {mint}\n{BUY_AMOUNT_SOL} SOL, {time.time()-t0:.1f}s\nhttps://solscan.io/tx/{sig}")
                    return
                last_err = "не подтвердилась вовремя"
            except Exception as e:
                last_err = str(e)
                print(f"[{attempt}] ошибка: {e}")
            await asyncio.sleep(0.3)
        await notify(f"❌ Не удалось купить {mint}: {last_err}")


# ---------- Telegram ----------
async def main():
    from telethon import TelegramClient, events
    from telethon.errors import FloodWaitError

    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()
    buyer = Buyer()
    await buyer.warmup()
    channel = await client.get_entity(CHANNEL)

    async def notify(text):
        print(text)
        try:
            await client.send_message("me", text)
        except Exception:
            pass

    async def handle(msg, source):
        if buyer.bought or not in_window():
            return
        cands = extract_candidates(message_text(msg))
        if not cands:
            return
        addr, kind = cands[0]
        if kind == "dex":
            addr = await buyer.resolve_dex(addr)
        print(f"{datetime.now().isoformat()} [{source}] найден {addr} ({kind}) в посте {msg.id}")
        await buyer.buy(addr, notify)

    @client.on(events.NewMessage(chats=channel))
    async def on_new(ev):
        await handle(ev.message, "new")

    @client.on(events.MessageEdited(chats=channel))
    async def on_edit(ev):
        await handle(ev.message, "edit")

    async def poller():
        """Подстраховка: если пуш-обновление от Telegram задержится."""
        last_id = (await client.get_messages(channel, limit=1))[0].id
        while not buyer.bought:
            if POLL_INTERVAL <= 0 or not in_window():
                await asyncio.sleep(5)
                continue
            try:
                msgs = await client.get_messages(channel, limit=3, min_id=last_id)
                for m in sorted(msgs, key=lambda x: x.id):
                    last_id = max(last_id, m.id)
                    await handle(m, "poll")
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds)
            except Exception as e:
                print("poll:", e)
            await asyncio.sleep(POLL_INTERVAL)

    bal = await buyer.balance()
    await notify(f"🟢 Снайпер запущен. Канал @{CHANNEL}, кошелёк {buyer.pub[:6]}…, баланс {bal:.3f} SOL, "
                 f"ставка {BUY_AMOUNT_SOL} SOL, slippage {SLIPPAGE}%, {'DRY RUN' if DRY_RUN else 'БОЕВОЙ РЕЖИМ'}")
    if bal < BUY_AMOUNT_SOL + PRIORITY_FEE + 0.01:
        await notify("⚠️ Баланса не хватит на покупку + комиссии!")
    asyncio.create_task(poller())

    if SCOUT:
        import scout
        http = httpx.AsyncClient()
        for kw in scout.KEYWORDS:
            await notify(scout.format_scan(kw, await scout.scan_existing(http, kw)))

        async def on_name_match(t, meta):
            await notify(scout.format_new(t, meta))
            if buyer.bought or not in_window():
                return
            if NAME_AUTOBUY == "any" or (NAME_AUTOBUY == "channel_link" and scout.links_point_to_channel(meta)):
                await notify(f"⚡ Покупаю по названию ({NAME_AUTOBUY})")
                await buyer.buy(t["mint"], notify)

        asyncio.create_task(scout.watch_new_tokens(on_name_match))
    await client.run_until_disconnected()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--test":
        print(extract_candidates(" ".join(sys.argv[2:])))
    elif len(sys.argv) >= 3 and sys.argv[1] == "--test-buy":
        DRY_RUN = False
        async def _t():
            b = Buyer()
            await b.warmup()
            async def p(t): print(t)
            await b.buy(sys.argv[2], p)
        asyncio.run(_t())
    else:
        asyncio.run(main())
