"""
Разведка по названию токена (например, CATPAD):
  1) скан уже существующих токенов с таким названием (DexScreener + pump.fun) — чтобы знать подделки заранее;
  2) живое наблюдение за ВСЕМИ новыми токенами pump.fun — мгновенное уведомление, если появился токен с таким названием.

Запуск отдельно:  python scout.py            (берёт TOKEN_KEYWORDS из .env)
                  python scout.py catpad     (разовый скан по слову)
"""
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

load_dotenv()

KEYWORDS = [k.strip() for k in os.getenv("TOKEN_KEYWORDS", "catpad").split(",") if k.strip()]
CHANNEL = os.getenv("CHANNEL", "").lstrip("@").lower()
WS_URL = "wss://pumpportal.fun/api/data"


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


NORM_KEYS = [norm(k) for k in KEYWORDS]


def name_matches(name: str, symbol: str) -> bool:
    hay = norm(name) + " " + norm(symbol)
    return any(k and k in hay for k in NORM_KEYS)


def ago(ts_ms) -> str:
    if not ts_ms:
        return "?"
    d = datetime.now(timezone.utc) - datetime.fromtimestamp(ts_ms / 1000, timezone.utc)
    h = d.total_seconds() / 3600
    return f"{h*60:.0f} мин назад" if h < 1 else (f"{h:.0f} ч назад" if h < 48 else f"{h/24:.0f} дн назад")


async def fetch_metadata(http, uri: str) -> dict:
    """Ссылки токена (twitter/telegram/website) из его метаданных."""
    if not uri:
        return {}
    try:
        r = await http.get(uri, timeout=4)
        j = r.json()
        return {k: j.get(k) for k in ("twitter", "telegram", "website", "description") if j.get(k)}
    except Exception:
        return {}


def links_point_to_channel(meta: dict) -> bool:
    tg = (meta.get("telegram") or "").lower()
    return bool(CHANNEL) and CHANNEL in tg


async def scan_existing(http, keyword: str) -> list:
    """Уже существующие токены с этим названием."""
    found = {}
    try:
        r = await http.get("https://api.dexscreener.com/latest/dex/search", params={"q": keyword}, timeout=8)
        for p in r.json().get("pairs") or []:
            if p.get("chainId") != "solana":
                continue
            b = p["baseToken"]
            if not name_matches(b.get("name", ""), b.get("symbol", "")):
                continue
            m = b["address"]
            prev = found.get(m)
            liq = (p.get("liquidity") or {}).get("usd") or 0
            if not prev or liq > prev["liq"]:
                found[m] = {"mint": m, "name": b.get("name"), "symbol": b.get("symbol"),
                            "created": p.get("pairCreatedAt"), "liq": liq, "mcap": p.get("marketCap") or p.get("fdv"),
                            "dex": p.get("dexId"), "url": p.get("url")}
    except Exception as e:
        print("dexscreener:", e)
    try:
        r = await http.get("https://frontend-api-v3.pump.fun/coins", timeout=8, params={
            "searchTerm": keyword, "limit": 50, "offset": 0, "sort": "created_timestamp",
            "order": "DESC", "includeNsfw": "true"})
        for c in r.json() if r.status_code == 200 else []:
            if not name_matches(c.get("name", ""), c.get("symbol", "")):
                continue
            m = c["mint"]
            found.setdefault(m, {"mint": m, "name": c.get("name"), "symbol": c.get("symbol"),
                                 "created": c.get("created_timestamp"), "liq": 0,
                                 "mcap": c.get("usd_market_cap"), "dex": "pump.fun",
                                 "url": f"https://pump.fun/coin/{m}"})
            found[m]["creator"] = c.get("creator")
            found[m]["twitter"] = c.get("twitter")
            found[m]["telegram"] = c.get("telegram")
    except Exception as e:
        print("pump.fun:", e)
    return sorted(found.values(), key=lambda x: x.get("created") or 0, reverse=True)


def format_scan(keyword: str, items: list) -> str:
    if not items:
        return f"🔎 «{keyword}»: токенов с таким названием на Solana пока нет."
    lines = [f"🔎 «{keyword}»: найдено {len(items)} (это НЕ настоящие, если канал ещё не объявлял):"]
    for t in items[:15]:
        mc = f"${t['mcap']:,.0f}" if t.get("mcap") else "?"
        lines.append(f"• {t['name']} (${t['symbol']}) · {t['dex']} · mcap {mc} · {ago(t.get('created'))}\n  {t['mint']}")
    if len(items) > 15:
        lines.append(f"…и ещё {len(items) - 15}")
    return "\n".join(lines)


async def watch_new_tokens(on_match, stop=lambda: False):
    """Слушает все новые токены pump.fun. on_match(token_dict, meta) вызывается при совпадении названия."""
    import websockets
    http = httpx.AsyncClient(timeout=5)
    while not stop():
        try:
            async with websockets.connect(WS_URL, ping_interval=20, max_size=2**20) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                print(f"👀 Слежу за новыми токенами pump.fun: {', '.join(KEYWORDS)}")
                async for raw in ws:
                    if stop():
                        return
                    try:
                        t = json.loads(raw)
                    except Exception:
                        continue
                    if not t.get("mint") or not name_matches(t.get("name", ""), t.get("symbol", "")):
                        continue
                    meta = await fetch_metadata(http, t.get("uri"))
                    await on_match(t, meta)
        except Exception as e:
            print("ws reconnect:", e)
            await asyncio.sleep(1)


def format_new(t: dict, meta: dict) -> str:
    s = [f"🚨 НОВЫЙ токен с похожим названием: {t.get('name')} (${t.get('symbol')})",
         f"CA: {t['mint']}",
         f"Создатель: {t.get('traderPublicKey')}",
         f"Первая покупка создателя: {t.get('solAmount', 0)} SOL",
         f"https://pump.fun/coin/{t['mint']}"]
    for k in ("twitter", "telegram", "website"):
        if meta.get(k):
            s.append(f"{k}: {meta[k]}")
    s.append("✅ В метаданных ссылка на наш канал" if links_point_to_channel(meta)
             else "⚠️ Ссылки на наш канал в метаданных нет")
    return "\n".join(s)


if __name__ == "__main__":
    async def _run():
        http = httpx.AsyncClient()
        kws = sys.argv[1:] or KEYWORDS
        for k in kws:
            print(format_scan(k, await scan_existing(http, k)))
        if len(sys.argv) == 1:
            async def show(t, meta):
                print(format_new(t, meta))
            await watch_new_tokens(show)
    asyncio.run(_run())
