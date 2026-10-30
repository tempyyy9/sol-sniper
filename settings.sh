#!/usr/bin/env bash
# Быстро поменять настройки и перезапустить
cd "$(dirname "$0")" && ./venv/bin/python configure.py && ./start.sh
