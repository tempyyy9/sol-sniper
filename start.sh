#!/usr/bin/env bash
# Запускает бота как службу: работает, даже когда закроешь приложение на телефоне, и перезапускается при сбое.
cd "$(dirname "$0")"
DIR="$(pwd)"
cat > /etc/systemd/system/sniper.service <<EOF
[Unit]
Description=SOL sniper
After=network-online.target
[Service]
WorkingDirectory=$DIR
ExecStart=$DIR/venv/bin/python -u $DIR/bot.py
Restart=on-failure
RestartSec=3
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now sniper >/dev/null 2>&1
systemctl restart sniper
echo "✅ Бот запущен. Сообщение «Снайпер запущен» придёт тебе в Избранное Telegram."
echo "Логи: ./logs.sh (выход — Ctrl+C)"
