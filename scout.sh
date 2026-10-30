#!/usr/bin/env bash
# Разовая разведка: какие токены с этим названием уже есть. Пример: ./scout.sh catpad
cd "$(dirname "$0")" && ./venv/bin/python scout.py "${@:-catpad}"
