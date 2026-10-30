#!/usr/bin/env bash
# Установка снайпера на чистый Ubuntu-сервер. Запуск: bash install.sh
set -e
cd "$(dirname "$0")"
echo "== Устанавливаю Python и зависимости (1–2 минуты) =="
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip tmux >/dev/null
python3 -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q -r requirements.txt
chmod +x *.sh
echo
echo "== Готово. Теперь настройка: =="
./venv/bin/python configure.py
