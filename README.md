# SOL-снайпер

Слушает открытый Telegram-канал и, как только в посте появляется адрес токена
(просто адрес, ссылка pump.fun или DexScreener, в том числе скрытая ссылка или кнопка),
сразу покупает его на BUY_AMOUNT_SOL через PumpPortal (pump.fun, Raydium, PumpSwap, Bonk — `POOL=auto`).
Покупает ОДИН раз и пишет результат тебе в «Избранное» Telegram.

## Что нужно
1. **VPS** с Ubuntu, регион Амстердам или Франкфурт (рядом с серверами Telegram и большинством валидаторов Solana). Hetzner, DigitalOcean и т. п., ~5 $/мес.
2. **Telegram API ID и Hash**: https://my.telegram.org → API development tools. Лучше завести отдельный аккаунт Telegram и **вступить им в канал**.
3. **RPC Helius**: https://dashboard.helius.dev → бесплатный API key (для скорости лучше платный тариф).
4. **Новый кошелёк** (Phantom/Solflare) → приватный ключ base58. На нём 2 SOL + ~0.05 SOL на комиссии.

## Установка на сервере
```bash
sudo apt update && sudo apt install -y python3-venv
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && nano .env      # заполнить
python bot.py                          # первый раз попросит телефон и код из Telegram
```

## Порядок проверки (обязательно до дня запуска)
1. `python bot.py --test "текст поста"` — какой адрес бот найдёт.
2. `DRY_RUN=true`, `WINDOW_*` пусто, `CHANNEL` = свой тестовый канал → опубликуй там ссылку pump.fun → в «Избранном» должно прийти «DRY RUN: купил бы …».
3. `BUY_AMOUNT_SOL=0.01`, `python bot.py --test-buy <адрес любого живого токена pump.fun>` → реальная мини-покупка, проверь в кошельке.
4. В день запуска: `CHANNEL` = нужный канал, `WINDOW_START/END`, `BUY_AMOUNT_SOL=2`, `DRY_RUN=false`.
   Запускать в `tmux` (чтобы не умер при закрытии терминала): `tmux new -s snipe` → `python bot.py` → Ctrl+B, D.

## Настройки скорости
- `PRIORITY_FEE` — чем выше, тем быстрее попадёт в блок при ажиотаже (0.005–0.02 SOL).
- `SLIPPAGE` — допустимый рост цены, %. 100 = купить при любой цене.
- `POLL_INTERVAL=1` — подстраховка, если Telegram задержит уведомление о посте.

Транзакцию строит PumpPortal (берёт около 0.5 % комиссии). Ключ никуда не отправляется: подписывается только на твоём сервере.
