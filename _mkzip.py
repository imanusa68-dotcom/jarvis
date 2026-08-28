# -*- coding: utf-8 -*-
"""Сборка архива проекта для владельца.

ПОЧЕМУ НЕ `git archive --format=zip`, ХОТЯ ЭТО КОРОЧЕ
Проверено 28.08.2026: git archive кладёт русские имена БЕЗ флага UTF-8, и
распаковка падает —

    error: cannot create .../JARVIS MARK XXXVI #U2014 #U041c#U0443...md
           File name too long

то есть архив «есть», а развернуть его нельзя. Здесь флаг 0x800 ставится
руками на каждое имя.

Список файлов берётся у git ls-files: в архив попадает ровно то, что под
контролем версий, — значит ни секретов (они в .gitignore), ни .venv, ни
__pycache__, ни временных домов из прогонов.
"""
import os
import subprocess
import zipfile

SRC = "/home/user/webapp/_unzip/jarvis_2026-08-16_faza05_priemka/jarvis_stage3c_step1b"
OUT = "/home/user/webapp/jarvis_faza1b_2026-08-28.zip"
PREFIX = "jarvis_stage3c_step1b/"

raw = subprocess.run(["git", "-C", SRC, "ls-files", "-z"],
                     capture_output=True, check=True).stdout
names = [n.decode("utf-8") for n in raw.split(b"\0") if n]

if os.path.exists(OUT):
    os.remove(OUT)

written = 0
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for n in names:
        p = os.path.join(SRC, n)
        if not os.path.isfile(p):
            continue
        info = zipfile.ZipInfo.from_file(p, PREFIX + n)
        info.flag_bits |= 0x800          # имя в UTF-8
        info.compress_type = zipfile.ZIP_DEFLATED
        with open(p, "rb") as fh, z.open(info, "w") as dst:
            dst.write(fh.read())
        written += 1

print("файлов в архиве:", written, "из", len(names), "у git")
print("размер:", round(os.path.getsize(OUT) / 1048576, 2), "МБ")
