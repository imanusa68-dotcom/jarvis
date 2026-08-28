#!/bin/bash
# Прогон набора тестов Jarvis в песочнице Linux на Python 3.12.
#
# ЗАЧЕМ ЭТОТ ФАЙЛ. У владельца Windows, и там достаточно `python -m pytest -q`
# из корня. В песочнице три вещи иначе, и каждая — про среду, НЕ про код:
#   1. TMPDIR — /tmp здесь tmpfs на 996 МБ, а core/state_snapshot.py требует
#      2 ГБ свободного запаса (MIN_FREE_BYTES). Без подмены 57 тестов снимков
#      и откатов падают «снимок не сделан» — на SSD 477 ГБ такого не бывает.
#   2. xvfb-run — pyautogui на Linux требует X-дисплея на импорте.
#   3. venv на 3.12 — в системе песочницы 3.13, проект просит ~=3.12.
#
# ОЖИДАЕМЫЙ РЕЗУЛЬТАТ: 1775 passed, 17 failed.
# Все 17 — платформенные, проверено построчно: код честно возвращает 'gedit'
# вместо 'notepad.exe', 'nautilus' вместо 'explorer.exe', и '/HOME' != '/home'
# на регистрозависимой файловой системе. Эталон Windows = 1775 + 17 = 1792.
#
# ПРАВИЛО: если passed стало МЕНЬШЕ 1775 — это регресс, разбираться сегодня же.
set -u
cd "$(dirname "$0")/_unzip/jarvis_2026-08-16_faza05_priemka/jarvis_stage3c_step1b" || exit 1
mkdir -p /home/user/webapp/_tmp
exec env TMPDIR=/home/user/webapp/_tmp xvfb-run -a .venv/bin/python -m pytest "$@"
