# -*- coding: utf-8 -*-
"""ПРОВЕРКА ШАГА 3-ТЕР (версия 2) — запускается из корня проекта:

    python check_lang.py          — полная проверка
    python check_lang.py -i       — вводишь свои фразы руками, как в разговоре

Что изменилось по сравнению с версией 1:
  — голосовые файлы сверяются с зашитой контрольной суммой принятой сборки 3-бис,
    а не с тем, что случайно лежит рядом в Загрузках (дефект версии 1);
  — сравнение папок стало справочным и больше не объявляет тревогу;
  — появился режим -i для проверки своими фразами;
  — версия 3: умолчание стало русским, латинский обрывок — английским;
  — версия 6: появился раздел про зрение, порог старых дверей стал пять.

Этот файл — временный инструмент, в архив проекта он не входит.
"""
import hashlib
import re
import socket
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Голосовые файлы. Переснято на шаге 29 фазы 0.7 (свой голос).
# В main.py три новых места: метод _say_local (свой рот с замком на живую
# сессию), озвучка ответа ядра и человеческое напоминание без сети.
# Сам синтез живёт в отдельном core/say_local.py, а не в этих двух файлах.
# Всё остальное в этих двух файлах обязано оставаться нетронутым.
#
# Переснято 17.08.2026, фаза 1 блок 1: в main.py появилась врезка «схема
# базы» (2453 байта) между снимками и меткой сборки. При этом обнаружено,
# что эталон УЖЕ был просрочен на 2882 байта: правки шагов 31-36 в него не
# попали, и сторож всё это время поднимал тревогу на main.py по старому
# делу. Новая сумма закрывает и то, и другое — то есть история между 30 и
# 36 этим эталоном НЕ проверена, и это названный долг, а не порядок.
#
# ПЕРЕСНЯТО 22.08.2026, ФАЗА 1 БЛОК 10 — ui.py, ПЕРВЫЙ РАЗ С ШАГА 25.
# Раньше здесь стояло «ui.py не трогали вовсе», и это было ценным фактом:
# окно и голос заведомо целы. Факт куплен за конкретную беду, названную вслух.
#
# Живая проба владельца: он поставил напоминание, отошёл на шесть минут,
# вернулся, НАПЕЧАТАЛ «здаров» — Джарвис промолчал. Замер показал, что голос и
# печать идут разными дорогами: микрофон попадает в разбор памяти
# (main.py:1749), а печать уходит прямо в модель через _on_text_command
# (main.py:941), мимо него. Напечатанное не считалось ответом ВООБЩЕ, ни при
# какой длине, и напоминание не догоняло владельца никогда. Обхода не было.
#
# `write_log` — единственная точка, через которую проходит любая реплика
# владельца, и лежит она здесь. Правка: +31 строка (25 объяснения, 6 кода),
# НОЛЬ удалённых, ни одна существующая строка не изменена — сверено построчно с
# копией из архива. Отрисовка не тронута, код целиком в try/except.
#
# Что мы потеряли этой правкой: утверждение «ui.py не менялся с шага 25».
# Отсчёт начинается заново от 22.08.2026. Сам сторож работает как раньше.
# ЭТАЛОН main.py ОБНОВЛЁН 28.08.2026 (фаза 1г). Важно, КАК он был просрочен:
# сторож был КРАСНЫМ ещё ДО этой правки — проверено на нетронутом архиве
# владельца (там main.py = 7e66de7b0c998192abf76dc48dbe39c0, 132418 Б, а
# эталон ждал 121226 Б). То есть правки фазы 1в в эталон не попали, и сторож
# уже неделю обвинял любую сессию, которая его запустит. Это ровно тот случай,
# от которого сторож должен защищать: расхождение накапливается молча.
# Новое значение снято ПОСЛЕ правки фазы 1г и сверено с тем, что изменение в
# main.py — только блок двери памяти (три инструмента вместо одного) плюс
# пометка [ЗАКРЫТО В ФАЗЕ 1Г] на устаревшей записке. Отрисовка и голос не
# тронуты; 35 сторожей речевого пути и памяти зелёные.
VOICE_REFERENCE = {
    # Переснято 29.08.2026, фаза 1е: в `_update_memory_async` добавлены два
    # пропуска через дверь (`memory_self_write`, `personality_self_write`).
    # Голос НЕ тронут — в diff только добавленные строки, ни одной удалённой.
    "main.py": ("3aa14d5d74957df1f419209d90d70c90", 141370),
    "ui.py": ("c3ae1a5806ff9f35bf8adeb7a113fb86", 55156),
}

args = [a.lower() for a in sys.argv[1:]]
DIALOG = "-i" in args or "--dialog" in args
prev_arg = None
for i, a in enumerate(sys.argv[1:]):
    if a.lower() in ("--prev", "-p") and i + 2 <= len(sys.argv[1:]):
        prev_arg = sys.argv[i + 2]

problems = []
checks = 0

print("=" * 72)
print("ПРОВЕРКА (версия 18) — язык, зрение, одна дверь, слой поставщика, номер модели, оффлайн-ядро, руки без сети, тихий оффлайн, потерянное слово и говорящий голос")
print("=" * 72)
print("Папка: " + str(ROOT))
print("Python: " + sys.version.split()[0])
print()

try:
    from core import lang
    from core import search_locale
    from core import aux_model
    from agent import executor
except Exception as e:
    print("НЕ ЗАПУСКАЕТСЯ: " + type(e).__name__ + ": " + str(e))
    print("Проверь, что файл лежит в КОРНЕ проекта, рядом с main.py")
    sys.exit(2)


# ───── мины: сеть, ключ, обе двери к модели ─────
class NetworkTouched(RuntimeError):
    pass


def _mine(*a, **k):
    raise NetworkTouched("определение языка полезло наружу")


socket.socket = _mine
socket.create_connection = _mine
socket.getaddrinfo = _mine
# Подлинные двери сохраняем ДО минирования: раздел 4.9 зовёт настоящий
# aux_call, чтобы проверить слой поставщика боевым путём, а не в обход мины.
_AUX_CALL_REAL = aux_model.aux_call
_CHEAP_CALL_REAL = aux_model.cheap_call
aux_model.cheap_call = _mine
aux_model.aux_call = _mine
executor._get_api_key = _mine
print("Сеть, ключ и обе двери к модели заминированы.")
print()


def ask(text):
    """Боевой путь: точно та же функция, что зовётся в работе."""
    return executor._detect_language(text), lang.detect_with_reason(text)[1]


# ───── режим «как в разговоре» ─────
if DIALOG:
    print("Режим ручной проверки. Пиши любую фразу на любом языке и жми Enter.")
    print("Пустая строка или слово  выход  — закончить.")
    print("Сеть заминирована: если ответ появляется — он родился внутри твоего компьютера.")
    print("-" * 72)
    while True:
        try:
            phrase = input("\nтвоя фраза > ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if phrase.strip().lower() in ("", "выход", "exit", "quit", "q"):
            break
        t0 = time.perf_counter()
        try:
            name, why = ask(phrase)
        except NetworkTouched as e:
            print("  ПРОВАЛ: " + str(e))
            continue
        except Exception as e:
            print("  ОШИБКА: " + type(e).__name__ + ": " + str(e)[:80])
            continue
        ms = (time.perf_counter() - t0) * 1000
        print("  язык: " + str(name) + "   почему: " + str(why)
              + "   за " + format(ms, ".3f") + " мс")
    print("\nЗакончили.")
    sys.exit(0)

# ───── 1. таблица фраз ─────
CASES = [
    ("\u043f\u0440\u0438\u0432\u0435\u0442, \u0441\u044d\u0440", "Russian"),
    ("\u043d\u0430\u0439\u0434\u0438 \u043d\u043e\u0432\u043e\u0441\u0442\u0438 \u043f\u0440\u043e RTX 5060 \u0438 \u0441\u043e\u0445\u0440\u0430\u043d\u0438 \u0432 \u0444\u0430\u0439\u043b", "Russian"),
    ("\u043f\u0440\u0438\u0432\u0456\u0442 \u044f\u043a \u0441\u043f\u0440\u0430\u0432\u0438 \u0454\u0434\u0438\u043d\u0438\u0439", "Ukrainian"),
    ("bugun hava cok guzel ve ben eve gidiyorum", "Turkish"),
    ("das wetter ist heute sehr warm und gut", "German"),
    ("hola como estas amigo mio", "Spanish"),
    ("hello world how are you today", "English"),
    ("\u4eca\u5929\u5929\u6c14\u5f88\u597d", "Chinese"),
    ("\u3053\u3093\u306b\u3061\u306f\u4e16\u754c", "Japanese"),
    ("\uc548\ub155\ud558\uc138\uc694 \ubc18\uac11\uc2b5\ub2c8\ub2e4", "Korean"),
    ("\u0645\u0631\u062d\u0628\u0627 \u0643\u064a\u0641 \u062d\u0627\u0644\u0643", "Arabic"),
    ("\u0928\u092e\u0938\u094d\u0924\u0947 \u0915\u0948\u0938\u0947 \u0939\u094b", "Hindi"),
    ("\u03b3\u03b5\u03b9\u03b1 \u03c3\u03bf\u03c5 \u03c6\u03af\u03bb\u03b5", "Greek"),
    ("\u0130stanbul", "Turkish"),
    ("Gda\u0144sk", "Polish"),
    ("RTX 5060 review", "English"),
    ("ok", "English"),
    ("42", "Russian"),
    ("", "Russian"),
]

print("ФРАЗА".ljust(34) + "ОТВЕТ".ljust(13) + "ПОЧЕМУ".ljust(22) + "ОЖИДАЛОСЬ")
print("-" * 72)
for text, want in CASES:
    checks += 1
    try:
        got, reason = ask(text)
    except NetworkTouched as e:
        got, reason = "СЕТЬ!", str(e)
        problems.append("сеть тронута на фразе " + repr(text))
    except Exception as e:
        got, reason = "ОШИБКА", type(e).__name__
        problems.append("ошибка на фразе " + repr(text) + ": " + str(e)[:60])
    if got != want and got not in ("СЕТЬ!", "ОШИБКА"):
        problems.append(repr(text) + " дал " + str(got) + ", ожидался " + want)
    mark = "  ok" if got == want else "  <-- НЕ СОВПАЛО"
    shown = (text[:30] + "...") if len(text) > 30 else (text if text else "(пусто)")
    print(shown.ljust(34) + str(got).ljust(13) + str(reason).ljust(22) + want + mark)
print()

# ───── 2. скорость ─────
N = 5000
t0 = time.perf_counter()
for _ in range(N):
    lang.detect("bugun hava cok guzel ve ben eve gidiyorum")
dt = time.perf_counter() - t0
per = dt / N * 1_000_000
checks += 1
print("СКОРОСТЬ: " + str(N) + " вызовов за " + format(dt, ".3f") + " с = "
      + format(per, ".1f") + " мкс на вызов")
if per > 500:
    problems.append("медленно: " + format(per, ".1f") + " мкс на вызов")
print()

# ───── 3. дверей к модели в языковом пути нет ─────
src_lang = (ROOT / "core" / "lang.py").read_text(encoding="utf-8", errors="replace")
src_loc = (ROOT / "core" / "search_locale.py").read_text(encoding="utf-8", errors="replace")
src_exe = (ROOT / "agent" / "executor.py").read_text(encoding="utf-8", errors="replace")
for label, src, forbidden in (
    ("core/lang.py", src_lang, ("cheap_call", "aux_call", "genai", "requests", "urllib")),
    ("core/search_locale.py", src_loc, ("cheap_call", "aux_call", "genai")),
):
    checks += 1
    hits = [w for w in forbidden if w in src]
    if hits:
        problems.append(label + " снова знает про: " + ", ".join(hits))
        print("ДВЕРИ: " + label + " — НАЙДЕНО " + ", ".join(hits))
    else:
        print("ДВЕРИ: " + label + " — ни сети, ни модели")
checks += 1
if "core.lang" not in src_loc:
    problems.append("core/search_locale.py больше не зовёт быстрый определитель")
checks += 1
lang_zone = src_exe.split("def _translate_to_goal_language")[0]
if "cheap_call" in lang_zone:
    problems.append("agent/executor.py: в языковой части снова есть дверь к модели")
    print("ДВЕРИ: agent/executor.py — НАЙДЕНА cheap_call в языковой части")
else:
    print("ДВЕРИ: agent/executor.py — языковая часть чистая")
print()

# ───── 3-бис. дверь к модели у композера: она должна быть одна ─────
import types as _types

OLD_SDK = "google." + "generativeai"


class OldDoorTouched(RuntimeError):
    pass


class _OldSdkTrap(_types.ModuleType):
    """Заминированный старый SDK: любое касание — взрыв."""

    def __getattr__(self, item):
        raise OldDoorTouched("кто-то полез в старый SDK: " + item)


sys.modules[OLD_SDK] = _OldSdkTrap(OLD_SDK)


class _DoorRecorder:
    """Подставная общая дверь: запоминает заходы и отдаёт заготовленный ответ."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def __call__(self, prompt, api_key, model=None, image_parts=None, caller="unknown"):
        self.calls.append({"model": model, "caller": caller})
        return self.reply


def _compose_with(reply):
    """Боевой путь композера с подменённой дверью."""
    from core import response_composer
    door = _DoorRecorder(reply)
    saved = aux_model.aux_call
    aux_model.aux_call = door
    try:
        text = response_composer.compose(
            result="Найдено 8 источников, файл RTX_5060_news.txt создан.",
            goal="найди новости про RTX 5060 и сохрани в файл",
            tool_used="web_search",
            language="ru",
            api_key="слово-ключ-для-проверки",
        )
    finally:
        aux_model.aux_call = saved
    return text, door


def _live_old_doors():
    """Живые строки с импортом старого SDK: (файл, номер строки)."""
    skip = {"tests", "__pycache__", ".pytest_cache", "logs", "docs"}
    found = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if any(part in skip for part in rel.parts):
            continue
        for number, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        ):
            s = line.strip()
            if s.startswith("#"):
                continue
            if OLD_SDK in s and "import" in s:
                found.append((rel.as_posix(), number))
    return found


print("ДВЕРЬ К МОДЕЛИ — композер фраз владельцу:")
print("   (строки [ResponseComposer] ниже — ожидаемые, это проверка отказов)")

# А. в исходнике композера старой двери нет
checks += 1
_src_comp = (ROOT / "core" / "response_composer.py").read_text(
    encoding="utf-8", errors="replace"
)
if OLD_SDK in _src_comp:
    problems.append("core/response_composer.py снова лезет в старый SDK своей дверью")
    print("   исходник: НАШЁН старый SDK")
elif "aux_call" not in _src_comp:
    problems.append("core/response_composer.py больше не зовёт общую дверь aux_call")
    print("   исходник: общей двери не видно")
else:
    print("   исходник: своей двери нет, зовёт общую aux_call")

# Б. заход ровно один и роль aux_light
checks += 1
try:
    from config.loader import get_model as _wd_get_model
    _want_model = _wd_get_model("aux_light")
    _reply = "Готово, сэр — новости сохранил в файл."
    _text, _door = _compose_with((True, _reply))
    if len(_door.calls) != 1:
        problems.append("композер стучал в дверь " + str(len(_door.calls)) + " раз вместо одного")
        print("   заходы: " + str(len(_door.calls)) + " — НЕ ОДИН")
    elif _door.calls[0]["model"] != _want_model:
        problems.append("модель подменилась: " + str(_door.calls[0]["model"])
                        + " вместо роли aux_light (" + str(_want_model) + ")")
        print("   модель: " + str(_door.calls[0]["model"]) + " — НЕ ТА")
    elif _reply[:20] not in _text:
        problems.append("ответ модели не дошёл до владельца: " + repr(_text[:60]))
        print("   ответ модели потерялся по дороге")
    else:
        print("   заход один, роль aux_light (" + str(_want_model) + "), ответ дошёл")
except OldDoorTouched as e:
    problems.append("композер полез в старую дверь: " + str(e))
    print("   ПРОВАЛ: " + str(e))
except Exception as e:
    problems.append("проверка двери упала: " + type(e).__name__ + ": " + str(e)[:80])
    print("   ОШИБКА: " + type(e).__name__ + ": " + str(e)[:80])

# В. отказ не утекает во фразу владельцу
checks += 1
_leaked = []
for _refusal in ("[quota-cooldown:65s]", "[quota-429:cooldown 30s]", "[error:503 UNAVAILABLE]"):
    try:
        _text, _ = _compose_with((False, _refusal))
    except Exception as e:
        _leaked.append(_refusal + " — " + type(e).__name__)
        continue
    if not _text.strip():
        _leaked.append(_refusal + " — композер промолчал")
    elif "[quota" in _text or "[error:" in _text:
        _leaked.append(_refusal + " — утекло во фразу")
if _leaked:
    problems.append("служебный текст дошёл до владельца: " + "; ".join(_leaked))
    print("   отказы: ПРОВАЛ — " + "; ".join(_leaked))
else:
    print("   отказы модели: владелец видит человеческую фразу, а не код ошибки")

print()
print("ДВЕРЬ К МОДЕЛИ — разборщ��к кода (explain):")


class _CodeDoor:
    """Подставная общая дверь для разборщика кода."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def __call__(self, prompt, api_key, model=None, image_parts=None, caller="unknown"):
        self.calls.append({"prompt": prompt, "model": model, "caller": caller})
        return self.reply


def _explain_with(reply, params=None, key_getter=None):
    import actions.code_helper as _ch

    door = _CodeDoor(reply)
    saved_door = aux_model.aux_call
    saved_key = _ch._get_api_key
    aux_model.aux_call = door
    _ch._get_api_key = key_getter or (lambda: "слово-ключ-для-проверки")
    try:
        out = _ch.code_helper(params or {"action": "explain", "code": "print(1 + 1)"})
    finally:
        aux_model.aux_call = saved_door
        _ch._get_api_key = saved_key
    return out, door


# Д. в исходнике разборщика старой двери нет
checks += 1
_src_code = (ROOT / "actions" / "code_helper.py").read_text(encoding="utf-8", errors="replace")
if OLD_SDK in _src_code or "_get_gemini" in _src_code:
    problems.append("actions/code_helper.py снова держит свою дверь")
    print("   исходник: НАШЁН старый SDK")
elif "aux_call" not in _src_code:
    problems.append("actions/code_helper.py больше не зовёт общую дверь aux_call")
    print("   исходник: общей двери не видно")
else:
    _lazy = [
        ln for ln in _src_code.splitlines()
        if "core.aux_model" in ln and "import" in ln and not ln.strip().startswith("#")
    ]
    if any(not (ln.startswith(" ") or ln.startswith(chr(9))) for ln in _lazy):
        problems.append("импорт двери вылез на уровень модуля — SDK грузится при старте")
        print("   исходник: импорт двери НЕ ленивый")
    else:
        print("   исходник: своей двери нет, общая открывается лениво")

# Е. заход один и роль aux_heavy
checks += 1
try:
    from config.loader import get_model as _wd_get_model2
    _want_heavy = _wd_get_model2("aux_heavy")
    _reply_c = "Этот код складывает единицу с единицей, сэр."
    _text_c, _door_c = _explain_with((True, _reply_c))
    if len(_door_c.calls) != 1:
        problems.append("разборщик стучал в дверь " + str(len(_door_c.calls)) + " раз вместо одного")
        print("   заходы: " + str(len(_door_c.calls)) + " — НЕ ОДИН")
    elif _door_c.calls[0]["model"] != _want_heavy:
        problems.append("разбор кода оглупел: " + str(_door_c.calls[0]["model"])
                        + " вместо роли aux_heavy (" + str(_want_heavy) + ")")
        print("   модель: " + str(_door_c.calls[0]["model"]) + " — НЕ ТА")
    elif _text_c.strip() != _reply_c:
        problems.append("объяснение не дошло целиком: " + repr(_text_c[:60]))
        print("   ответ модели потерялся по дороге")
    else:
        print("   заход один, роль aux_heavy (" + str(_want_heavy) + "), ответ дошёл")
except OldDoorTouched as e:
    problems.append("разборщик полез в старую дверь: " + str(e))
    print("   ПРОВАЛ: " + str(e))
except Exception as e:
    problems.append("проверка разборщика упала: " + type(e).__name__ + ": " + str(e)[:80])
    print("   ОШИБКА: " + type(e).__name__ + ": " + str(e)[:80])

# Ж. отказ модели и пропавший ключ не утекают владельцу
checks += 1
_leaked_c = []
for _refusal in ("[quota-cooldown:65s]", "[quota-429:cooldown 30s]", "[error:503 UNAVAILABLE]"):
    try:
        _t, _ = _explain_with((False, _refusal))
    except Exception as e:
        _leaked_c.append(_refusal + " — " + type(e).__name__)
        continue
    if not _t.strip():
        _leaked_c.append(_refusal + " — разборщик промолчал")
    elif "[quota" in _t or "[error:" in _t:
        _leaked_c.append(_refusal + " — утекло во фразу")
if _leaked_c:
    problems.append("служебный текст дошёл до владельца: " + "; ".join(_leaked_c))
    print("   отказы: ПРОВАЛ — " + "; ".join(_leaked_c))
else:
    print("   отказы модели: владелец видит человеческую фразу, а не код ошибки")

# З. без ключа инструмент не падает
checks += 1


def _no_key_at_all():
    raise RuntimeError("gemini_api_key не найден")


try:
    _t_nokey, _door_nokey = _explain_with((True, "сюда дойти не должно"), key_getter=_no_key_at_all)
    if not str(_t_nokey).strip():
        problems.append("без ключа разборщик промолчал")
        print("   без ключа: тишина — ПРОВАЛ")
    elif "gemini_api_key" in str(_t_nokey):
        problems.append("без ключа служебный текст утёк владельцу")
        print("   без ключа: утекло — ПРОВАЛ")
    elif _door_nokey.calls:
        problems.append("без ключа всᄅ равно пошли к модели")
        print("   без ключа: всё равно стучались — ПРОВАЛ")
    else:
        print("   ключ пропал: инструмент говорит фразу, а не падает")
except Exception as e:
    problems.append("без ключа разборщик упал: " + type(e).__name__)
    print("   без ключа: УПАЛ — " + type(e).__name__ + ": " + str(e)[:60])

print()
print("ДВЕРЬ К МОДЕЛИ — зрение («нажми на кнопку ОК»):")
print("   (строки [ComputerControl] ниже — ожидаемые, это проверка отказов)")

import builtins as _bi
import io as _io
from contextlib import redirect_stdout as _redirect

_EYE_PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 4096
_EYE_SIZE = (1920, 1080)


class _FakeShot:
    """Картинка, которая умеет только сохраниться в буфер."""

    def __init__(self, blob):
        self._blob = blob

    def save(self, buf, format=None):
        buf.write(self._blob)


class _FakeGui:
    """Подставной pyautogui: ни одного настоящего снимка и ни одного клика."""

    def __init__(self, size, blob):
        self._size = size
        self._blob = blob
        self.shots = 0

    def size(self):
        return self._size

    def screenshot(self):
        self.shots += 1
        return _FakeShot(self._blob)


class _EyeDoor:
    """Подставная общая дверь для зрения."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def __call__(self, prompt, api_key, model=None, image_parts=None, caller="unknown"):
        self.calls.append({"prompt": prompt, "model": model, "caller": caller,
                           "image_parts": image_parts})
        return self.reply


def _look_with(reply, size=_EYE_SIZE, printer=None):
    """Боевой путь зрения: настоящая функция, подставные дверь, экран и ключ."""
    import actions.computer_control as _cc
    import config.loader as _loader

    door = _EyeDoor(reply)
    gui = _FakeGui(size, _EYE_PNG)
    saved_door = aux_model.aux_call
    saved_gui = getattr(_cc, "pyautogui", None)
    saved_flag = _cc._PYAUTOGUI
    saved_key = _loader.get_api_key
    saved_sleep = time.sleep
    saved_print = _bi.print
    aux_model.aux_call = door
    _cc.pyautogui = gui
    _cc._PYAUTOGUI = True
    _loader.get_api_key = lambda: "слово-ключ-для-проверки"
    time.sleep = lambda _s: None
    if printer is not None:
        _bi.print = printer
    buf = _io.StringIO()
    try:
        with _redirect(buf):
            coords = _cc._analyze_screen_for_element("кнопка ОК")
    finally:
        aux_model.aux_call = saved_door
        _cc.pyautogui = saved_gui
        _cc._PYAUTOGUI = saved_flag
        _loader.get_api_key = saved_key
        time.sleep = saved_sleep
        _bi.print = saved_print
    return coords, door, buf.getvalue(), gui


# И. в исходнике зрения старой двери нет, а общая открывается лениво
checks += 1
_src_eye = (ROOT / "actions" / "computer_control.py").read_text(encoding="utf-8", errors="replace")
_lazy_eye = [ln for ln in _src_eye.splitlines()
             if "core.aux_model" in ln and "import" in ln and not ln.strip().startswith("#")]
_at_import_eye = [ln for ln in _src_eye.splitlines()
                  if ln and not ln[0].isspace() and "get_model(" in ln]
_lazy_broken = any(not (ln.startswith(" ") or ln.startswith(chr(9))) for ln in _lazy_eye)
if OLD_SDK in _src_eye or "GenerativeModel" in _src_eye:
    problems.append("actions/computer_control.py снова держит свою дверь")
    print("   исходник: НАШЁН старый SDK")
elif "aux_call" not in _src_eye:
    problems.append("actions/computer_control.py больше не зовёт общую дверь aux_call")
    print("   исходник: общей двери не видно")
elif (not _lazy_eye) or _lazy_broken or _at_import_eye:
    problems.append("зрение спрашивает дверь или роль на импорте — SDK грузится при старте")
    print("   исходник: импорт двери НЕ ленивый")
else:
    print("   исходник: своей двери нет, общая открывается лениво")

# К. заход один, роль vision, снимок парой, координаты доехали
checks += 1
try:
    from config.loader import get_model as _wd_get_model3
    _want_vision = _wd_get_model3("vision")
    _xy, _door_e, _log_e, _gui_e = _look_with((True, "450,320"))
    _parts = _door_e.calls[0]["image_parts"] if _door_e.calls else None
    _size_text = str(_EYE_SIZE[0]) + "x" + str(_EYE_SIZE[1])
    _parts_bad = (not isinstance(_parts, list) or len(_parts) != 1
                  or not isinstance(_parts[0], tuple) or len(_parts[0]) != 2
                  or not isinstance(_parts[0][0], (bytes, bytearray))
                  or _parts[0][1] != "image/png")
    if len(_door_e.calls) != 1:
        problems.append("зрение стучало в дверь " + str(len(_door_e.calls)) + " раз вместо одного")
        print("   заходы: " + str(len(_door_e.calls)) + " — НЕ ОДИН")
    elif _door_e.calls[0]["model"] != _want_vision:
        problems.append("зрение оглупело: " + str(_door_e.calls[0]["model"])
                        + " вместо роли vision (" + str(_want_vision) + ")")
        print("   модель: " + str(_door_e.calls[0]["model"]) + " — НЕ ТА")
    elif "Vision" not in str(_door_e.calls[0]["caller"]):
        problems.append("дверь не знает, что стучит зрение: " + str(_door_e.calls[0]["caller"]))
        print("   кто стучался: " + str(_door_e.calls[0]["caller"]) + " — НЕ Vision")
    elif _xy != (450, 320):
        problems.append("координаты не доехали до мыши: " + repr(_xy))
        print("   координаты: " + repr(_xy) + " — НЕ ТЕ")
    elif _parts_bad:
        problems.append("снимок уходит в дверь не парой (байты, image/png)")
        print("   картинка: формат НЕ ТОТ")
    elif _size_text not in str(_door_e.calls[0]["prompt"]):
        problems.append("в промпте зрения нет настоящего размера экрана")
        print("   промпт: размера экрана НЕ видно")
    elif _gui_e.shots != 1:
        problems.append("снимков экрана " + str(_gui_e.shots) + " вместо одного")
        print("   снимки: " + str(_gui_e.shots) + " — НЕ ОДИН")
    else:
        print("   заход один, роль vision (" + str(_want_vision)
              + "), снимок парой (байты, image/png), координаты 450,320")
except OldDoorTouched as e:
    problems.append("зрение полезло в старую дверь: " + str(e))
    print("   ПРОВАЛ: " + str(e))
except Exception as e:
    problems.append("проверка зрения упала: " + type(e).__name__ + ": " + str(e)[:80])
    print("   ОШИБКА: " + type(e).__name__ + ": " + str(e)[:80])

# Л. ни один отказ не двигает мышь
checks += 1
_bad_eye = []
for _reply_e, _why_e in (
    ((False, "[quota-cooldown:65s]"), "остывание квоты"),
    ((False, "[error:503 UNAVAILABLE]"), "временный отказ"),
    ((True, "   "), "пустой ответ"),
    ((True, "NOT_FOUND"), "элемент не найден"),
    ((True, "5000,90"), "координаты за краем экрана"),
):
    try:
        _xy_bad, _door_bad, _log_bad, _ = _look_with(_reply_e)
    except Exception as e:
        _bad_eye.append(_why_e + " — упало " + type(e).__name__)
        continue
    if _xy_bad is not None:
        _bad_eye.append(_why_e + " — мышь получила " + repr(_xy_bad))
if _bad_eye:
    problems.append("зрение двигает мышь на отказе: " + "; ".join(_bad_eye))
    print("   отказы: ПРОВАЛ — " + "; ".join(_bad_eye))
else:
    print("   отказ квоты, 503, пустота, NOT_FOUND и край экрана: мышь не двигается")

# М. замер печатается, и его падение не отменяет нажатие
checks += 1
_real_print = _bi.print


def _print_hates_measure(*a, **k):
    if a and "снимок" in str(a[0]):
        raise UnicodeEncodeError("cp1251", "x", 0, 1, "консоль не в UTF-8")
    return _real_print(*a, **k)


try:
    _xy_m, _, _log_m, _ = _look_with((True, "450,320"))
    _seen = ("зрение: снимок" in _log_m) and ("КБ" in _log_m) and ("ответ модели" in _log_m)
    _xy_m2, _, _, _ = _look_with((True, "450,320"), printer=_print_hates_measure)
    if not _seen:
        problems.append("строка замера зрения не печатается — время проверить нечем")
        print("   замер: строки нет — ПРОВАЛ")
    elif _xy_m2 != (450, 320):
        problems.append("падение строки замера отменило нажатие")
        print("   замер: его падение уносит нажатие — ПРОВАЛ")
    else:
        print("   замер: печатается (экран, вес снимка, две цифры времени) и не мешает нажатию")
except Exception as e:
    problems.append("проверка замера упала: " + type(e).__name__ + ": " + str(e)[:80])
    print("   ОШИБКА: " + type(e).__name__ + ": " + str(e)[:80])

print()
# Г. сколько старых дверей осталось по проекту
checks += 1
_doors = _live_old_doors()
_files = sorted({name for name, _ in _doors})
print("   старых дверей осталось: " + str(len(_doors)) + " в " + str(len(_files)) + " файлах")
for _name, _line in _doors:
    print("      " + _name + ":" + str(_line))
if len(_doors) > 5:
    problems.append("старых дверей стало больше, а не меньше: " + str(len(_doors)))
for _moved in ("core/response_composer.py", "actions/code_helper.py",
               "actions/computer_control.py"):
    if _moved in _files:
        problems.append(_moved + " вернулся в старый SDK")
print()

# ───── 4. голос: сверка с принятой сборкой 3-БИС ─────
print("ГОЛОС — сверка со сборкой вечера 2, правда про экран:")
for name, (want_md5, want_size) in VOICE_REFERENCE.items():
    checks += 1
    p = ROOT / name
    if not p.exists():
        problems.append(name + " пропал из проекта")
        print("   " + name + " — ФАЙЛА НЕТ")
        continue
    data = p.read_bytes()
    got_md5 = hashlib.md5(data).hexdigest()
    if got_md5 == want_md5 and len(data) == want_size:
        print("   " + name.ljust(9) + " байт в байт тот же  (" + str(len(data)) + " Б, " + got_md5 + ")")
    else:
        problems.append("ГОЛОСОВОЙ ФАЙЛ ТРОНУТ: " + name + " — сейчас " + got_md5
                        + " (" + str(len(data)) + " Б), а должно быть " + want_md5)
        print("   " + name.ljust(9) + " ИЗМЕНЁН: " + got_md5 + " (" + str(len(data)) + " Б)")
print()

# ───── 4.5 экран: Джарвис спрашивает кнопку, а не вспоминает (вечер 2) ─────
print("ЭКРАН — правда про тумблер (вечер 2):")


class _SCPlayer:
    """Подставной интерфейс: только то, что читает действие."""

    def __init__(self, on):
        self.screen_control = on
        self.log = []

    def write_log(self, line):
        self.log.append(line)


def _ask_screen(on):
    """Боевой путь: настоящее computer_control, мышь и модель заминированы."""
    import actions.computer_control as _cc_sc

    def _no_touch(*a, **k):
        raise AssertionError("вопрос про тумблер потянулся к мыши или к модели")

    saved_click = _cc_sc._click
    saved_locate = _cc_sc._locate
    _cc_sc._click = _no_touch
    _cc_sc._locate = _no_touch
    buf = _io.StringIO()
    try:
        with _redirect(buf):
            return _cc_sc.computer_control(
                parameters={"action": "screen_status"}, player=_SCPlayer(on)
            )
    finally:
        _cc_sc._click = saved_click
        _cc_sc._locate = saved_locate


# А. вопрос отвечается даже когда клики запрещены, и мышь не двигается
checks += 1
_ans_off = _ans_on = ""
try:
    _ans_off = _ask_screen(False)
    _ans_on = _ask_screen(True)
except Exception as _sc_e:
    problems.append("вопрос про тумблер сломался: " + repr(_sc_e))
    print("   вопрос: УПАЛ — " + repr(_sc_e))

if _ans_off and _ans_on:
    _off_ok = "on screen): OFF" in _ans_off
    _on_ok = "on screen): ON" in _ans_on
    _not_blocked = "Screen control is currently disabled" not in _ans_off
    if _off_ok and _on_ok and _not_blocked:
        print("   вопрос: отвечает OFF/ON, работает и при выключенном тумблере, мышь цела")
    elif not _not_blocked:
        problems.append("screen_status сам попал под запрет — спросить нельзя")
        print("   вопрос: ЗАБЛОКИРОВАН тумблером")
    else:
        problems.append("screen_status отвечает не про то состояние")
        print("   вопрос: ОТВЕТ НЕ ТОТ — off=" + repr(_ans_off[:60])
              + " on=" + repr(_ans_on[:60]))

# Б. включённый тумблер велит действовать, а не переспрашивать
checks += 1
if _ans_on:
    if "RIGHT NOW" in _ans_on and "instead of asking the user to enable" in _ans_on:
        print("   включено: прямое указание действовать, а не просить включить")
    else:
        problems.append("при включённом тумблере ответ больше не велит действовать")
        print("   включено: УКАЗАНИЕ ПРОПАЛО")

# В. два похожих названия больше не путаются
checks += 1
if _ans_off and _ans_on:
    if ("Do NOT use screen_share_control" in _ans_off
            and "Screen View" in _ans_off and "Screen View" in _ans_on):
        print("   Screen View: назван отдельно в обоих ответах — путать нечем")
    else:
        problems.append("ответ перестал отделять Screen View от Screen control")
        print("   Screen View: РАЗДЕЛЕНИЕ ПРОПАЛО")

# Г. текст отказа один на весь проект, учит перепроверить и держит фразу теста
checks += 1
try:
    import actions.computer_control as _cc_msg
    import core.gate as _gate_wd

    _msg = _gate_wd.SCREEN_OFF_MSG
    _same = _cc_msg._screen_off_message() == _msg
    _golden = _msg.startswith("Screen control is currently disabled")
    _teaches = "screen_status" in _msg and "Never refuse from memory" in _msg
    if _same and _golden and _teaches:
        print("   отказ: один источник, первая фраза цела, велит перепроверить")
    elif not _same:
        problems.append("текст отказа снова размножился: gate и computer_control расходятся")
        print("   отказ: ДВА РАЗНЫХ ТЕКСТА")
    elif not _golden:
        problems.append("отказ больше не начинается фразой, которую держит золотой тест")
        print("   отказ: ПЕРВАЯ ФРАЗА ПОТЕРЯНА")
    else:
        problems.append("отказ больше не отправляет модель перепроверить тумблер")
        print("   отказ: ИНСТРУКЦИЯ ПОТЕРЯНА")
except Exception as _msg_e:
    problems.append("проверка текста отказа упала: " + repr(_msg_e))
    print("   отказ: УПАЛА ПРОВЕРКА — " + repr(_msg_e))

# Д. вопрос не требует согласования, а клик по-прежнему требует тумблер
checks += 1
try:
    import core.security as _sec_wd

    _p_st = {"action": "screen_status"}
    _risk_ok = _sec_wd.get_risk("computer_control", _p_st) == "low"
    _pol_ok = _sec_wd.get_policy("computer_control", _p_st) == "auto"
    _buf_g = _io.StringIO()
    with _redirect(_buf_g):
        _r_click = _gate_wd.dispatch(
            "computer_control",
            {"action": "screen_click", "description": "OK button"},
            screen_control=False,
        )
        _r_ask = _gate_wd.dispatch("computer_control", _p_st, screen_control=False)
    if _risk_ok and _pol_ok and _r_ask.verdict == "run" and _r_click.verdict == "screen_off":
        print("   шлюз: вопрос пропускает без согласования, клик без тумблера не пускает")
    elif not (_risk_ok and _pol_ok):
        problems.append("screen_status снова high/confirm — за вопрос попросят согласование")
        print("   шлюз: ВОПРОС ТРЕБУЕТ СОГЛАСОВАНИЯ")
    elif _r_ask.verdict != "run":
        problems.append("шлюз не пускает вопрос про тумблер: " + str(_r_ask.verdict))
        print("   шлюз: ВОПРОС НЕ ПРОХОДИТ")
    else:
        problems.append("клик стал доступен без тумблера — дверь открыли всем")
        print("   шлюз: КЛИК ПРОХОДИТ БЕЗ ТУМБЛЕРА")
except Exception as _sec_e:
    problems.append("проверка шлюза упала: " + repr(_sec_e))
    print("   шлюз: УПАЛА ПРОВЕРКА — " + repr(_sec_e))

# Е. вопрос вне списка запретов, а список цел; и модель о нём знает
checks += 1
_src_cc_sc = (ROOT / "actions" / "computer_control.py").read_text(
    encoding="utf-8", errors="replace")
_src_main_sc = (ROOT / "main.py").read_text(encoding="utf-8", errors="replace")
try:
    _blk = _src_cc_sc[_src_cc_sc.index("INTERACTIVE_ACTIONS = {"):]
    _blk = _blk[:_blk.index("}")]
except ValueError:
    _blk = ""
_knows = "action='screen_status' is READ-ONLY" in _src_main_sc
if not _blk:
    problems.append("список INTERACTIVE_ACTIONS не найден в computer_control.py")
    print("   список: НЕ НАЙДЕН")
elif "screen_status" in _blk:
    problems.append("screen_status попал в список запретов — спросить станет нельзя")
    print("   список: ВОПРОС ВНУТРИ ЗАПРЕТОВ")
elif "screen_click" not in _blk:
    problems.append("из INTERACTIVE_ACTIONS пропал screen_click — запреты ослабли")
    print("   список: ЗАПРЕТЫ ОСЛАБЛИ")
elif not _knows:
    problems.append("main.py больше не рассказывает модели про screen_status")
    print("   описание: МОДЕЛЬ О ДЕЙСТВИИ НЕ ЗНАЕТ")
else:
    print("   список: вопрос снаружи запретов, запреты целы, модель о нём знает")
print()

# ───── 4.7 агентский путь: одна дверь к модели (фаза 0.5) ─────
print("АГЕНТСКИЙ ПУТЬ — одна дверь к модели:")

import io as _io_ag
import json as _json_ag
from contextlib import redirect_stdout as _redir_ag

_OLD_A = "import " + "google." + "generativeai"
_OLD_B = "from " + "google." + "generativeai"
_OLD_C = "Generative" + "Model("
_OLD_D = "genai." + "configure("
_SKIP_AG = {"__pycache__", ".git", ".venv", "venv", "tests", "logs", ".pytest_cache"}

_bad_ag = []
for _p_ag in sorted(ROOT.rglob("*.py")):
    _rel_ag = _p_ag.relative_to(ROOT)
    if any(part in _SKIP_AG for part in _rel_ag.parts):
        continue
    _txt_ag = _p_ag.read_text(encoding="utf-8", errors="ignore")
    for _needle_ag in (_OLD_A, _OLD_B, _OLD_C, _OLD_D):
        if _needle_ag in _txt_ag:
            _bad_ag.append(str(_rel_ag) + ": " + _needle_ag)
checks += 1
if _bad_ag:
    problems.append("вторая дверь к модели вернулась: " + "; ".join(_bad_ag[:5]))
    print("   старый SDK: НАЙДЕН — " + "; ".join(_bad_ag[:3]))
else:
    print("   старый SDK: не найден ни в одном файле проекта")

_pyproj_ag = (ROOT / "pyproject.toml").read_text(encoding="utf-8", errors="ignore")
checks += 1
if '"google-generativeai' in _pyproj_ag:
    problems.append("google-generativeai снова в зависимостях")
elif '"google-genai' not in _pyproj_ag:
    problems.append("новый SDK исчез из зависимостей")
else:
    print("   зависимости: остался только новый SDK")

import core.aux_model as _aux_ag
import agent.planner as _pl_ag
import agent.error_handler as _eh_ag
import agent.executor as _ex_ag
from config.loader import get_model as _gm_ag


class _AgDoor:
    """Ловушка вместо общей двери: помнит вопрос, отвечает заказанным."""

    def __init__(self, ok=True, answer=""):
        self.ok = ok
        self.answer = answer
        self.calls = []

    def __call__(self, prompt, api_key, model=None, image_parts=None, caller="unknown"):
        self.calls.append({"prompt": prompt, "model": model, "caller": caller})
        return self.ok, self.answer


class _AgMine:
    def __call__(self, *a, **kw):
        raise AssertionError("модель вызвана там, где это запрещено")


def _ag_run(door, fn):
    saved = _aux_ag.aux_call
    keys = [(m, m._get_api_key) for m in (_pl_ag, _eh_ag, _ex_ag)]
    _aux_ag.aux_call = door
    for _m, _ in keys:
        _m._get_api_key = lambda: "KEY-FOR-WATCHDOG"
    buf = _io_ag.StringIO()
    try:
        with _redir_ag(buf):
            out = fn()
    finally:
        _aux_ag.aux_call = saved
        for _m, _orig in keys:
            _m._get_api_key = _orig
    return out, buf.getvalue()


_PLAN_AG = _json_ag.dumps({"goal": "g", "steps": [{"step": 1, "tool": "weather_report",
                                                  "description": "d", "parameters": {},
                                                  "critical": True}]})
_STEP_AG = {"step": 2, "tool": "web_search", "description": "find prices",
            "parameters": {}, "critical": False}

_d1 = _AgDoor(True, _PLAN_AG)
_plan1, _ = _ag_run(_d1, lambda: _pl_ag.create_plan("узнай погоду"))
checks += 1
if len(_d1.calls) != 1 or _plan1["steps"][0]["tool"] != "weather_report":
    problems.append("планировщик не прошёл через общую дверь")
elif not _d1.calls[0]["prompt"].startswith(_pl_ag.PLANNER_PROMPT[:120]):
    problems.append("правила планировщика не едут первым куском промпта")
elif _d1.calls[0]["model"] != _gm_ag("aux_light"):
    problems.append("роль планировщика съехала")
else:
    print("   планировщик: одна дверь, роль aux_light, правила первым куском")

_d2 = _AgDoor(False, "[quota-cooldown:65s]")
_plan2, _out2 = _ag_run(_d2, lambda: _pl_ag.create_plan("открой браузер"))
checks += 1
if not _plan2.get("steps"):
    problems.append("при отказе двери план оказался пустым")
elif "quota-cooldown" not in _out2 or "JSON parse failed" in _out2:
    problems.append("отказ квоты выдан за битый JSON")
else:
    print("   отказ квоты: назван своим именем, план остаётся запасной")

_d3 = _AgDoor(True, _json_ag.dumps({"decision": "retry", "reason": "r", "user_message": "u"}))
_res3, _ = _ag_run(_d3, lambda: _eh_ag.analyze_error(_STEP_AG, "timeout"))
_d4 = _AgDoor(False, "[error:503 UNAVAILABLE]")
_res4, _ = _ag_run(_d4, lambda: _eh_ag.analyze_error(_STEP_AG, "timeout"))
checks += 1
if _res3["decision"] is not _eh_ag.ErrorDecision.RETRY:
    problems.append("решение разборщика ошибок не разобралось")
elif _d3.calls[0]["model"] != _gm_ag("aux_light"):
    problems.append("роль разборщика ошибок съехала")
elif _res4["decision"] is not _eh_ag.ErrorDecision.REPLAN:
    problems.append("отказ двери не дал честного replan")
else:
    print("   разбор ошибок: одна дверь, отказ отвечает replan")

_step5, _out5 = _ag_run(_AgMine(), lambda: _eh_ag.generate_fix(_STEP_AG, "boom", "try cmd"))
checks += 1
if _step5["tool"] != "cmd_control" or _step5["parameters"].get("action") == "run":
    problems.append("починка снова предлагает шаг, который проект бл��кирует")
else:
    print("   починка шага: без вызова модели, запасной шаг на cmd_control")

_d6 = _AgDoor(False, "[quota-cooldown:65s]")
_out6, _txt6 = _ag_run(_d6, lambda: _ex_ag._translate_to_goal_language(
    "Some English text", "сохрани отчёт"))
checks += 1
if _out6 != "Some English text":
    problems.append("при отказе перевода потерян исходный текст")
elif _d6.calls[0]["model"] != _gm_ag("aux_heavy"):
    problems.append("роль перевода съехала")
elif "Translation failed" not in _txt6:
    problems.append("отказ перевода прошёл молча")
else:
    print("   перевод: роль aux_heavy, при отказе исходный текст цел")

_names_ag = []
for _fn_ag in (lambda: _pl_ag.create_plan("цель"),
               lambda: _pl_ag.replan("цель", [], _STEP_AG, "boom"),
               lambda: _eh_ag.analyze_error(_STEP_AG, "boom"),
               lambda: _ex_ag._translate_to_goal_language("text", "цель")):
    _d_ag = _AgDoor(True, _PLAN_AG)
    _ag_run(_d_ag, _fn_ag)
    _names_ag.append(_d_ag.calls[0]["caller"])
checks += 1
if len(set(_names_ag)) != 4 or "unknown" in _names_ag:
    problems.append("имена вызывающих в журнале слились: " + str(_names_ag))
else:
    print("   журнал: четыре разных имени — видно, кто жжёт квоту")
print()


# ───── 4.8 поиск и исследование: та же одна дверь ─────
print("ПОИСК И ИССЛЕДОВАНИЕ — одна дверь к модели:")

import actions.web_search as _ws_s
import actions.deep_research as _dr_s

_bad_s = []
for _rel_s in ("actions/web_search.py", "actions/deep_research.py"):
    _txt_s = (ROOT / _rel_s).read_text(encoding="utf-8", errors="ignore")
    if ("genai." + "Client(") in _txt_s or "from google import genai" in _txt_s:
        _bad_s.append(_rel_s)
checks += 1
if _bad_s:
    problems.append("свой клиент к модели вернулся в поиск: " + ", ".join(_bad_s))
else:
    print("   исходники: своего клиента нет ни в поиске, ни в исследовании")


def _s_run(door, fn, key="KEY-FOR-WATCHDOG", key_raises=False):
    saved = _aux_ag.aux_call
    keys = [(m, m._get_api_key) for m in (_ws_s, _dr_s)]

    def _fake_key():
        if key_raises:
            raise RuntimeError("gemini_api_key not found")
        return key

    _aux_ag.aux_call = door
    for _m, _ in keys:
        _m._get_api_key = _fake_key
    buf = _io_ag.StringIO()
    try:
        with _redir_ag(buf):
            out = fn()
    finally:
        _aux_ag.aux_call = saved
        for _m, _orig in keys:
            _m._get_api_key = _orig
    return out, buf.getvalue()


_RES_S = [{"title": "First", "snippet": "aaa", "url": "https://a.example", "domain": "a.example"}]
_EV_S = {"sources": [{"title": "S1", "url": "https://a.example", "domain": "a.example",
                      "content_preview": "first preview", "published_date": None}]}

_d7 = _AgDoor(True, "Digest text, sir.")
_o7, _ = _s_run(_d7, lambda: _ws_s._synthesize_news_digest("news", _RES_S, date_str="2026-08-09"))
checks += 1
if _o7 != "Digest text, sir." or len(_d7.calls) != 1:
    problems.append("сводка новостей не прошла через общую дверь")
elif _d7.calls[0]["model"] != _gm_ag("aux_light") or _d7.calls[0]["caller"] != "WebSearch-Digest":
    problems.append("роль или имя сводки новостей съехали")
else:
    print("   сводка новостей: одна дверь, роль aux_light, имя WebSearch-Digest")

_d8 = _AgDoor(False, "[quota-cooldown:65s]")
_o8, _t8 = _s_run(_d8, lambda: _ws_s._synthesize_with_gemini("what is x", _RES_S))
checks += 1
if "Search results for:" not in _o8 or "https://a.example" not in _o8:
    problems.append("при отказе модели поиск не показал найденные ссылки")
elif "quota-cooldown" not in _t8:
    problems.append("отказ модели в поиске прошёл молча")
else:
    print("   отказ модели: владелец всё равно видит найденные ссылки")

_d9 = _AgDoor(False, "[error:503 UNAVAILABLE]")
_o9, _ = _s_run(_d9, lambda: _dr_s._synthesize("what is x", _EV_S))
checks += 1
if _o9.get("confidence") != "low" or not str(_o9.get("uncertainty", "")).startswith("model unavailable"):
    problems.append("исследование выдало отказ модели за уверенный ответ")
elif "first preview" not in _o9.get("answer", ""):
    problems.append("исследование выбросило собранные источники")
else:
    print("   исследование: отказ назван честно, источники не потеряны")

_o10, _ = _s_run(_AgMine(), lambda: _ws_s._synthesize_news_digest("news", _RES_S), key_raises=True)
checks += 1
if "News digest" not in _o10:
    problems.append("пропавший ключ убил сводку новостей")
else:
    print("   ключ пропал: сводка собирается без модели и без падения")
print()


# ───── 4.9 слой поставщика: SDK живёт в одном файле (фаза 0.5) ─────
print("СЛОЙ ПОСТАВЩИКА — SDK в одном файле:")

import core.aux_model as _pv_aux
import core.provider as _pv_pkg
from core.provider.base import Provider as _PvBase

_PV_DIR = ROOT / "core" / "provider"
_PV_DOOR = "core/provider/gemini.py"
_PV_CLIENT = "genai." + "Client("
_PV_SEND = ".generate" + "_content("
_PV_IMPORTS = ("from google import genai", "from google.genai import", "import google.genai")
# Голос и Screen View держат свой клиент намеренно: другой протокол.
_PV_LIVE = {
    "core/screen_live_runtime.py",
    "core/screen_live_session.py",
    "core/screen_share_manager.py",
    "actions/screen_processor.py",
}
# Берёт из SDK только числовой код ошибки живой сессии, клиента не строит.
_PV_CODES = {"core/session_manager.py"}

checks += 1
_pv_missing = [_n for _n in ("__init__.py", "base.py", "gemini.py")
               if not (_PV_DIR / _n).exists()]
if _pv_missing:
    problems.append("слой поставщика неполон: нет " + ", ".join(_pv_missing))
elif _pv_aux._generate.__module__ != "core.aux_model":
    problems.append("дверь _generate уехала из core/aux_model.py")
else:
    print("   файлы: base.py, gemini.py и фасад на месте")

_pv_doors, _pv_imps, _pv_whole = [], [], False
_pv_scan = []
for _pv_folder in ("core", "agent", "actions", "memory"):
    _pv_scan.extend(sorted((ROOT / _pv_folder).rglob("*.py")))
for _pv_p in _pv_scan:
    if "__pycache__" in _pv_p.parts:
        continue
    _pv_rel = _pv_p.relative_to(ROOT).as_posix()
    _pv_src = _pv_p.read_text(encoding="utf-8", errors="ignore")
    if _pv_rel == _PV_DOOR:
        _pv_whole = _PV_CLIENT in _pv_src and _PV_SEND in _pv_src
        continue
    if (_PV_CLIENT in _pv_src or _PV_SEND in _pv_src) and _pv_rel not in _PV_LIVE:
        _pv_doors.append(_pv_rel)
    if any(_n in _pv_src for _n in _PV_IMPORTS) and _pv_rel not in (_PV_LIVE | _PV_CODES):
        _pv_imps.append(_pv_rel)

checks += 1
if _pv_doors:
    problems.append("вторая дверь к SDK: " + ", ".join(_pv_doors))
elif _pv_imps:
    problems.append("новый файл ввозит SDK сам: " + ", ".join(_pv_imps))
elif not _pv_whole:
    problems.append("core/provider/gemini.py больше не строит клиента — двери нет")
else:
    print("   исходники: клиент и запрос только в core/provider/gemini.py")

checks += 1
_pv_head = []
for _pv_n in ("__init__.py", "base.py", "gemini.py"):
    _pv_lines = (_PV_DIR / _pv_n).read_text(encoding="utf-8", errors="ignore").splitlines()
    for _pv_i, _pv_l in enumerate(_pv_lines, start=1):
        if _pv_l[:1].strip() and ("import google" in _pv_l or "from google" in _pv_l):
            _pv_head.append(_pv_n + ":" + str(_pv_i))
if _pv_head:
    problems.append("SDK ввозится на уровне модуля: " + ", ".join(_pv_head))
else:
    print("   ввоз: слой поднимается без SDK — оффлайн-ядру это понадобится")


class _PvFake(_PvBase):
    """Подставной поставщик: всё записывает, в сеть не ходит."""

    name = "fake"

    def __init__(self, *script):
        self.script = list(script) or ["ok"]
        self.built = []
        self.sent = []

    def build_payload(self, prompt, image_parts=None):
        self.built.append({"prompt": prompt, "images": image_parts})
        return {"made_by": self.name, "prompt": prompt, "images": image_parts}

    def generate(self, model, payload, api_key):
        self.sent.append({"model": model, "payload": payload, "key": api_key})
        _step = self.script[min(len(self.sent) - 1, len(self.script) - 1)]
        if isinstance(_step, BaseException):
            raise _step
        return _step


def _pv_ask(fake, model, image_parts=None, caller="Watchdog"):
    _saved_p, _saved_sleep = _pv_pkg.set_provider(fake), time.sleep
    time.sleep = lambda _s: None
    _buf = _io_ag.StringIO()
    try:
        with _redir_ag(_buf):
            _ok, _text = _AUX_CALL_REAL("вопрос", "KEY-FOR-WATCHDOG", model=model,
                                        image_parts=image_parts, caller=caller)
    finally:
        _pv_pkg.set_provider(_saved_p)
        time.sleep = _saved_sleep
    return _ok, _text, _buf.getvalue()


checks += 1
_pv_f1 = _PvFake("готово")
_pv_ok1, _pv_t1, _pv_o1 = _pv_ask(_pv_f1, "watchdog-provider-text")
if not _pv_ok1 or _pv_t1 != "готово":
    problems.append("текстовый вопрос не доехал до поставщика: " + repr(_pv_t1))
elif len(_pv_f1.sent) != 1 or _pv_f1.sent[0]["model"] != "watchdog-provider-text":
    problems.append("имя модели или число заходов к поставщику съехало")
elif _pv_f1.sent[0]["key"] != "KEY-FOR-WATCHDOG":
    problems.append("ключ по дороге к поставщику подменился")
elif _pv_f1.sent[0]["payload"].get("made_by") != "fake":
    problems.append("тело собрал один поставщик, а отправил другой")
else:
    print("   текст: модель, ключ и тело доехали без изменений")

checks += 1
_pv_pic = [(b"PNG-bytes", "image/png")]
_pv_f2 = _PvFake("вижу")
_pv_ok2, _pv_t2, _pv_o2 = _pv_ask(_pv_f2, "watchdog-provider-vision", image_parts=_pv_pic)
_pv_door_src = (_PV_DIR / "gemini.py").read_text(encoding="utf-8", errors="ignore")
if not _pv_ok2 or not _pv_f2.built or _pv_f2.built[0]["images"] != _pv_pic:
    problems.append("картинка не доехала до поставщика целой")
elif "from_bytes" not in _pv_door_src or "from_text" not in _pv_door_src:
    problems.append("в поставщике пропала ветка картинки")
elif _pv_door_src.index("from_bytes") > _pv_door_src.index("from_text"):
    problems.append("порядок частей перевернулся: текст раньше картинки")
else:
    print("   картинка: доезжает целой, порядок картинка-текст сохранён")

checks += 1
_pv_f3 = _PvFake(RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded"))
_pv_ok3, _pv_t3, _pv_o3 = _pv_ask(_pv_f3, "watchdog-provider-quota")
_pv_f4 = _PvFake(RuntimeError("503 UNAVAILABLE: model is overloaded"), "со второй попытки")
_pv_ok4, _pv_t4, _pv_o4 = _pv_ask(_pv_f4, "watchdog-provider-transient")
if _pv_ok3 or not _pv_t3.startswith("[quota-429"):
    problems.append("429 от поставщика больше не включает остывание: " + repr(_pv_t3))
elif len(_pv_f3.sent) != 1:
    problems.append("429 повторили — это сжигает дневную квоту")
elif not _pv_ok4 or _pv_t4 != "со второй попытки":
    problems.append("временный отказ поставщика больше не переживается повтором")
elif len(_pv_f4.sent) != 2:
    problems.append("число попыток съехало: " + str(len(_pv_f4.sent)))
elif "временный отказ" not in _pv_o4:
    problems.append("временный отказ прошёл молча")
else:
    print("   отказы: 429 включает остывание без повтора, 503 переживается вслух")
print()


# ───── 4.10 клиент живёт между вызовами и не висит вечно ─────
print("СЛОЙ ПОСТАВЩИКА — клиент живёт между вызовами:")

import types as _cc_types
import threading as _cc_thr
import core.provider.gemini as _cc_door
from config.loader import get_limit as _cc_get_limit

_CC_KEY_A = "KEY-FOR-WATCHDOG-A"
_CC_KEY_B = "KEY-FOR-WATCHDOG-B"


class _CcState:
    built = []
    sent = []
    refused = 0
    refuse = False
    error = None


class _CcModels:
    def generate_content(self, model=None, contents=None):
        _CcState.sent.append(model)
        if _CcState.error is not None:
            raise _CcState.error
        return _cc_types.SimpleNamespace(text="ответ")


class _CcClient:
    def __init__(self, api_key=None, http_options=None):
        if http_options is not None and _CcState.refuse:
            _CcState.refused += 1
            raise TypeError("unexpected keyword argument 'http_options'")
        _CcState.built.append(http_options)
        self.models = _CcModels()


def _cc_sdk_on():
    """Подставить поддельный SDK: настоящего здесь нет и сети тоже."""
    _saved = {n: sys.modules.get(n) for n in ("google", "google.genai")}
    _g = _cc_types.ModuleType("google")
    _gg = _cc_types.ModuleType("google.genai")
    _gg.Client = _CcClient
    _g.genai = _gg
    sys.modules["google"] = _g
    sys.modules["google.genai"] = _gg
    _CcState.built, _CcState.sent = [], []
    _CcState.refused, _CcState.refuse, _CcState.error = 0, False, None
    _cc_door.reset_clients()
    _cc_door._TIMEOUT_REFUSED = False
    _cc_door._TIMEOUT_REASON = ""
    return _saved


def _cc_sdk_off(saved):
    """Вернуть всё как было — иначе следующие разделы увидят подделку."""
    for _name, _mod in saved.items():
        if _mod is None:
            sys.modules.pop(_name, None)
        else:
            sys.modules[_name] = _mod
    _cc_door.reset_clients()
    _cc_door._TIMEOUT_REFUSED = False
    _cc_door._TIMEOUT_REASON = ""


checks += 1
_cc_saved = _cc_sdk_on()
try:
    _cc_first = _cc_door._client_for(_CC_KEY_A)
    _cc_second = _cc_door._client_for(_CC_KEY_A)
    _cc_seen = []
    _cc_go = _cc_thr.Event()

    def _cc_worker():
        _cc_go.wait()
        _cc_seen.append(_cc_door._client_for(_CC_KEY_A))

    _cc_threads = [_cc_thr.Thread(target=_cc_worker) for _ in range(20)]
    for _t in _cc_threads:
        _t.start()
    _cc_go.set()
    for _t in _cc_threads:
        _t.join()
    if _cc_first is not _cc_second:
        problems.append("клиент строится заново на каждый вызов — кэш потерялся")
    elif len(_cc_seen) != 20 or len(set(id(_c) for _c in _cc_seen)) != 1:
        problems.append("потоки получили разных клиентов — замок не держит")
    elif len(_CcState.built) != 1:
        problems.append("построено клиентов: " + str(len(_CcState.built)) + ", а нужен один")
    else:
        print("   кэш: один ключ — один клиент, двадцать потоков лишних не наплодили")
finally:
    _cc_sdk_off(_cc_saved)

checks += 1
_cc_saved = _cc_sdk_on()
try:
    _cc_a = _cc_door._client_for(_CC_KEY_A)
    _cc_b = _cc_door._client_for(_CC_KEY_B)
    _cc_b2 = _cc_door._client_for(_CC_KEY_B)
    _cc_live = [_v for _k, _v in _cc_door._CACHE.items() if _k == "client"]
    if _cc_a is _cc_b:
        problems.append("новый ключ работает старым клиентом — владелец сменит ключ впустую")
    elif _cc_b2 is not _cc_b:
        problems.append("новый клиент не ложится в кэш")
    elif len(_cc_live) != 1:
        problems.append("в кэше накопилось клиентов: " + str(len(_cc_live)))
    else:
        print("   смена ключа: прежний клиент выброшен, в кэше всегда один")
finally:
    _cc_sdk_off(_cc_saved)

checks += 1
_cc_saved = _cc_sdk_on()
try:
    _cc_door._client_for(_CC_KEY_A)
    _cc_mark = repr(_cc_door._CACHE.get("mark"))
    if _CC_KEY_A in _cc_mark:
        problems.append("сырой ключ лежит в кэше — он утечёт в любую трассировку")
    elif _cc_door._fingerprint(_CC_KEY_A) not in _cc_mark:
        problems.append("отпечатка ключа в кэше нет — смену ключа заметить нечем")
    elif _cc_door._fingerprint(_CC_KEY_A) == _cc_door._fingerprint(_CC_KEY_B):
        problems.append("два разных ключа дают один отпечаток")
    else:
        print("   ключ: в кэше лежит отпечаток, а не сам ключ")
finally:
    _cc_sdk_off(_cc_saved)

checks += 1
_cc_saved = _cc_sdk_on()
try:
    _cc_secs = _cc_get_limit("provider", "timeout_seconds", None)
    _cc_door._client_for(_CC_KEY_A)
    _cc_opt = _CcState.built[-1] if _CcState.built else None
    if _cc_secs is None:
        problems.append("в реестре пропала строка срока ожидания поставщика")
    elif not isinstance(_cc_opt, dict):
        problems.append("срок ожидания вообще не доехал до клиента: " + repr(_cc_opt))
    elif _cc_opt.get("timeout") != int(float(_cc_secs) * 1000):
        problems.append("срок доехал не в миллисекундах: " + repr(_cc_opt))
    else:
        print("   срок ожидания: " + str(_cc_secs) + " с из реестра, до клиента доехало "
              + str(_cc_opt.get("timeout")) + " мс")
finally:
    _cc_sdk_off(_cc_saved)

checks += 1
_cc_saved = _cc_sdk_on()
try:
    _CcState.refuse = True
    _cc_client = _cc_door._client_for(_CC_KEY_A)
    _cc_ms, _cc_refused, _cc_reason = _cc_door.timeout_status()
    if _cc_client is None or not _CcState.built:
        problems.append("старый SDK отказал от срока — и весь вызов умер")
    elif _CcState.refused != 1 or _CcState.built[-1] is not None:
        problems.append("откат без срока не сработал: " + repr(_CcState.built))
    elif not _cc_refused or not _cc_reason:
        problems.append("отказ от срока невиден снаружи — узнать о нём нечем")
    else:
        print("   старый SDK: срок не принят — клиент всё равно построен, отказ виден снаружи")
finally:
    _cc_sdk_off(_cc_saved)

checks += 1
_cc_saved = _cc_sdk_on()
try:
    _cc_prov = _cc_door.GeminiProvider()
    _CcState.error = RuntimeError("503 UNAVAILABLE: model is overloaded")
    _cc_raised = None
    try:
        _cc_prov.generate("watchdog-cache-model", "вопрос", _CC_KEY_A)
    except Exception as _exc:
        _cc_raised = _exc
    _cc_left = dict(_cc_door._CACHE)
    _CcState.error = None
    _cc_after = _cc_prov.generate("watchdog-cache-model", "вопрос", _CC_KEY_A)
    if _cc_raised is None or "503" not in str(_cc_raised):
        problems.append("отказ запроса проглочен внутри поставщика")
    elif _cc_left:
        problems.append("упавший клиент остался в кэше — повтор пойдёт в мёртвое соединение")
    elif _cc_after != "ответ" or len(_CcState.built) != 2:
        problems.append("после отказа клиент не перестроился")
    else:
        print("   отказ запроса: клиент выброшен, ошибка ушла наверх нетронутой")
finally:
    _cc_sdk_off(_cc_saved)
print()


# ───── 4.11 номер модели не теряется, а пустота называется честно ─────
print("ПОИСК — номер модели доезжает до запросов:")

from core.query_rewriter import detect_topic_entity as _tn_detect
from core.query_rewriter import classify_intent as _tn_intent
from core.query_rewriter import rewrite as _tn_rewrite

_TN_WITH_NUMBER = (
    ("новости RTX 5060", "RTX 5060"),
    ("новости про Ryzen 9800X3D", "Ryzen 9800X3D"),
    ("что нового в GTA 6", "GTA 6"),
    ("новости про iPhone 17", "iPhone 17"),
    ("новости Windows 11", "Windows 11"),
    ("новости про PlayStation 5 Pro", "PlayStation 5 Pro"),
    ("RTX 5060 Ti обзор", "RTX 5060 Ti"),
    ("news about RTX 5060", "RTX 5060"),
)

checks += 1
_tn_bad = [(_q, _tn_detect(_q)) for _q, _want in _TN_WITH_NUMBER if _tn_detect(_q) != _want]
if _tn_bad:
    problems.append("номер модели теряется по дороге в поиск: " + str(_tn_bad[:3]))
else:
    print("   тема: восемь моделей с номерами доезжают целиком")

_TN_NO_NUMBER = (
    ("Claude Code новости", "Claude Code"),
    ("возможности OpenAI API", "OpenAI API"),
    ("новости GPT-5", "GPT-5"),
    ("новости про Galaxy S26 Ultra", "Galaxy S26 Ultra"),
)

checks += 1
_tn_plain = [(_q, _tn_detect(_q)) for _q, _want in _TN_NO_NUMBER if _tn_detect(_q) != _want]
if _tn_plain:
    problems.append("сломались имена без номеров: " + str(_tn_plain[:3]))
else:
    print("   тема: имена без номеров не пострадали")

checks += 1
if _tn_detect("новости про Сбер") != "Сбер" or _tn_detect("новости про Ми-8") != "Ми-8":
    problems.append("предлог снова прилип к теме: " + repr(_tn_detect("новости про Сбер")))
else:
    print("   тема: предлог больше не уезжает внутрь запроса")

checks += 1
_tn_lonely = [_q for _q in ("новости 5060", "news 5060") if _tn_detect(_q) is not None]
if _tn_lonely:
    problems.append("голый номер снова стал темой: " + str(_tn_lonely))
else:
    print("   тема: голый номер без имени темой не считается")

checks += 1
if _tn_detect("что нового в мире") is not None:
    problems.append("общий вопрос получил тему: " + repr(_tn_detect("что нового в мире")))
else:
    print("   тема: общий вопрос по-прежнему идёт в сводку дня")

checks += 1
_tn_intents = [_tn_intent("новости RTX 5060"), _tn_intent("новости про Ryzen 9800X3D")]
if _tn_intents != ["topic_news", "topic_news"]:
    problems.append("намерение поиска съехало: " + str(_tn_intents))
else:
    print("   намерение: обе живые фразы остались topic_news")

checks += 1
_tn_vars = _tn_rewrite("новости про Ryzen 9800X3D", language="ru")
_tn_qs = _tn_vars.get("topic_queries", [])
_tn_dirty = [_q for _q in _tn_qs if "9800X3D" not in _q]
if _tn_vars.get("topic") != "Ryzen 9800X3D":
    problems.append("в запросы ушла обрезанная тема: " + repr(_tn_vars.get("topic")))
elif not _tn_qs or _tn_dirty:
    problems.append("номер выпал из запросов: " + str(_tn_dirty[:3]))
else:
    print("   запросы: все " + str(len(_tn_qs)) + " вариантов несут полный номер")

checks += 1
_TN_SPANS = ("главные новости за вчера", "новости за неделю",
             "новости за прошлую неделю", "новости за месяц")
_tn_span = [(_q, _tn_detect(_q)) for _q in _TN_SPANS if _tn_detect(_q) is not None]
if _tn_span:
    problems.append("срок снова стал темой поиска: " + str(_tn_span[:3]))
else:
    print("   тема: срок вроде «за неделю» темой не становится")

checks += 1
_TN_DIGESTS = ("главные новости за вчера", "новости за сегодня")
_tn_dig = [(_q, _tn_intent(_q)) for _q in _TN_DIGESTS
           if _tn_intent(_q) != "headline_digest"]
if _tn_dig:
    problems.append("сводка дня угнана в поиск по теме: " + str(_tn_dig))
else:
    print("   намерение: «новости за вчера» доходит до сводки дня")

_TN_SEARCH = ROOT / "actions" / "web_search.py"
_tn_src = _TN_SEARCH.read_text(encoding="utf-8")
_TN_NO_GUESS = "Never guess whether something exists, was announced, released or cancelled"
_TN_NOT_COVERED = "If the results do not cover the exact subject of the question"

checks += 1
if _tn_src.count(_TN_NO_GUESS) != 2:
    problems.append("запрет догадок про выход товара стоит не в обоих ответах: "
                    + str(_tn_src.count(_TN_NO_GUESS)))
elif _TN_NOT_COVERED not in _tn_src:
    problems.append("нет правила честно называть несовпадение темы")
else:
    print("   честность: догадки про анонс запрещены и в сводке, и в ответе")

checks += 1
_tn_rw = (ROOT / "core" / "query_rewriter.py").read_text(encoding="utf-8")
_tn_door = [_n for _n in ("aux_call", "genai." + "Client(", "api_key") if _n in _tn_rw]
if _tn_door:
    problems.append("переписчик запросов открыл дверь к модели: " + str(_tn_door))
else:
    print("   разбор запроса: без модели и без сети, как и был")

checks += 1
_TN_TEST = ROOT / "tests" / "test_topic_number_survives.py"
_tn_raw = _TN_TEST.read_bytes() if _TN_TEST.exists() else b""
if not _tn_raw:
    problems.append("пропал тест tests/test_topic_number_survives.py")
elif b"\r\n" in _tn_raw:
    problems.append("в тесте номера модели завелись CRLF")
elif b"\r\n" not in _TN_SEARCH.read_bytes():
    problems.append("actions/web_search.py потерял CRLF после правки")
else:
    print("   окончания строк: тест LF, actions/web_search.py остался CRLF")
print()


# ───── 5. справочно: что отличается от соседней папки ─────
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", ".idea", ".vscode", "logs"}
SKIP_NAMES = {"secrets.json", "settings.json", "settings.json.imported",
              "check_lang.py", "check_live.py", "diag_lang.py", "api_keys.json"}


def fingerprint(base):
    out = {}
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.name in SKIP_NAMES or p.suffix in (".pyc", ".zip"):
            continue
        try:
            out[p.relative_to(base).as_posix()] = hashlib.md5(p.read_bytes()).hexdigest()
        except OSError:
            pass
    return out


def pick_previous():
    if prev_arg:
        cand = Path(prev_arg)
        return (cand, True) if (cand / "main.py").exists() else (None, False)
    dated, undated = [], []
    for cand in (ROOT.parent.parent).glob("*/*"):
        if not (cand / "main.py").exists() or cand.resolve() == ROOT:
            continue
        m = re.search(r"(\d{4}-\d{2}-\d{2})", cand.parent.name)
        (dated if m else undated).append((m.group(1) if m else "", cand))
    if dated:
        dated.sort()
        return dated[-1][1], True
    if undated:
        return undated[-1][1], False
    return None, False


prev, trustworthy = pick_previous()
if prev is None:
    print("СПРАВОЧНО: соседней сборки для сравнения не нашлось — пропущено")
else:
    print("СПРАВОЧНО — чем папка отличается от: " + prev.parent.name)
    if not trustworthy:
        print("   ВНИМАНИЕ: в имени этой папки нет даты — вероятно, это старая версия,")
        print("   так что различий будет много и это НОРМАЛЬНО. Судить по ней нельзя.")
    a, b = fingerprint(prev), fingerprint(ROOT)
    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    changed = sorted(k for k in set(a) & set(b) if a[k] != b[k])
    for title, items in (("новый", added), ("удалён", removed), ("изменён", changed)):
        for name in items[:10]:
            print("   " + title + ": " + name)
        if len(items) > 10:
            print("   " + title + ": ... и ещё " + str(len(items) - 10))
    print("   итого: новых " + str(len(added)) + ", удалённых " + str(len(removed))
          + ", изменённых " + str(len(changed)))
    print("   (этот список никогда не объявляет тревогу — голос судится выше, по сумме)")
print()


# ───── 4.12 оффлайн-ядро: руки живут без сети (фаза 0.7, шаг 1) ─────
print("ОФФЛАЙН-ЯДРО — что Джарвис умеет без интернета:")

_OC_FILE = ROOT / "core" / "offline_core.py"
_oc = None

checks += 1
if not _OC_FILE.exists():
    problems.append("нет файла core/offline_core.py — оффлайн-ядро пропало")
else:
    print("   файл core/offline_core.py на месте")

if _OC_FILE.exists():
    _oc_src = _OC_FILE.read_text(encoding="utf-8")

    checks += 1
    _oc_bad = [_n for _n in ("aux_call", "cheap_call", "genai", "generativeai",
                             "generate_content", "requests", "urllib", "socket",
                             "print(")
               if _n in _oc_src]
    if _oc_bad:
        problems.append("в оффлайн-ядре появилась дверь наружу: " + ", ".join(_oc_bad))
    else:
        print("   ни модели, ни сети, ни печати внутри ядра")

    checks += 1
    _oc_top = []
    for _ln in _oc_src.splitlines():
        if _ln[:1] in (" ", "\t"):
            continue
        _t = _ln.strip()
        for _need in ("import actions", "from actions", "import main", "from main"):
            if _t.startswith(_need):
                _oc_top.append(_t)
    if _oc_top:
        problems.append("оффлайн-ядро ввозит тяжёлое наверху: " + "; ".join(_oc_top))
    else:
        print("   модули действий ввозятся лениво, наверху их нет")

    import importlib as _oc_il

    checks += 1
    _oc_before = {_n for _n in sys.modules if _n.startswith("actions")}
    try:
        _oc = _oc_il.import_module("core.offline_core")
        _oc_after = {_n for _n in sys.modules if _n.startswith("actions")}
    except Exception as _e:
        _oc_after = _oc_before
        problems.append("оффлайн-ядро не ввозится: " + type(_e).__name__ + ": " + str(_e))
    if _oc is not None and _oc_after != _oc_before:
        problems.append("ввоз ядра притащил модули действий: "
                        + ", ".join(sorted(_oc_after - _oc_before)))
    elif _oc is not None:
        print("   ввоз ядра ничего лишнего за собой не тащит")

if _oc is not None:
    import core.gate as _oc_gate

    class _OcVerdict:
        def __init__(self, verdict, tool, action):
            self.verdict = verdict
            self.tool = tool
            self.action = action
            self.risk = "low"
            self.policy = "auto"
            self.mode = "interactive"
            self.message = "служебный текст для модели"
            self.reason = "сторож"

        @property
        def allowed(self):
            return self.verdict == "run"

    class _OcDoor:
        def __init__(self, verdict="run"):
            self.verdict = verdict
            self.calls = []

        def __call__(self, tool, params=None, *, mode="interactive", screen_control=False):
            self.calls.append((tool, dict(params or {})))
            return _OcVerdict(self.verdict, tool, (params or {}).get("action"))

    class _OcSpy:
        def __init__(self):
            self.calls = []

        def __call__(self, parameters=None, **kw):
            self.calls.append(dict(parameters or {}))
            return "сделано"

    def _oc_run(phrase, verdict="run"):
        door, spy = _OcDoor(verdict), _OcSpy()
        old_door, old_import = _oc_gate.dispatch, _oc._import_tool
        _oc_gate.dispatch = door
        _oc._import_tool = lambda _t: spy
        try:
            reply = _oc.handle(phrase)
        finally:
            _oc_gate.dispatch = old_door
            _oc._import_tool = old_import
        return reply, door, spy

    checks += 1
    _oc_polite_bad = []
    for _w in ("пж", "пжп", "пжл", "пжлст", "пжалста", "плиз", "плз",
               "плс", "please", "plz", "pls", "пожалуйста"):
        _oc_r, _oc_d, _oc_s = _oc_run("открой блокнот " + _w)
        _oc_name = _oc_d.calls[-1][1].get("app_name") if _oc_d.calls else None
        if _oc_name != "блокнот":
            _oc_polite_bad.append((_w, _oc_name))
    if _oc_polite_bad:
        problems.append("вежливый хвост уехал в лаунчер: " + repr(_oc_polite_bad))
    else:
        print("   вежливость срезана: в лаунчер уходит чистое «блокнот»")

    _OC_CASES = [
        ("напомни через 20 минут выпить воды", "reminder", "set"),
        ("покажи мои напоминания", "reminder", "list"),
        ("открой блокнот", "open_app", None),
        ("найди файл отчет", "file_controller", "find"),
        ("запиши заметку: купить хлеб", "file_controller", "create_file"),
        ("отмени последнее действие", "file_controller", "undo"),
        ("повтори последнее действие", "file_controller", "redo"),
    ]
    checks += 1
    _oc_wrong = []
    for _p, _tool, _act in _OC_CASES:
        _r, _d, _s = _oc_run(_p)
        if _r is None or len(_d.calls) != 1 or _d.calls[0][0] != _tool:
            _oc_wrong.append(_p)
        elif _act is not None and _d.calls[0][1].get("action") != _act:
            _oc_wrong.append(_p)
        elif len(_s.calls) != 1:
            _oc_wrong.append(_p)
    if _oc_wrong:
        problems.append("команды мимо единственной двери: " + "; ".join(_oc_wrong))
    else:
        print("   семь команд: каждая через дверь и ровно один раз")

    checks += 1
    _r, _d, _s = _oc_run("запиши заметку: секрет", verdict="confirm")
    if _s.calls:
        problems.append("отказ двери не остановил инструмент в оффлайн-ядре")
    elif _r is None or _r.ok:
        problems.append("отказ двери подан как успех")
    elif "служебный текст" in _r.text:
        problems.append("владельцу показан текст, написанный для модели")
    else:
        print("   отказ двери: ничего не выполнено, объяснение человеческое")

    checks += 1
    _oc_touch = []
    for _p in ("сколько времени", "статус", "сколько осталось квоты", "что ты делал"):
        _r, _d, _s = _oc_run(_p)
        if _r is None or _d.calls:
            _oc_touch.append(_p)
    if _oc_touch:
        problems.append("время, статус, квота или журнал полезли в дверь: "
                        + ", ".join(_oc_touch))
    else:
        print("   время, статус, квота и журнал двери не касаются")

    checks += 1
    _oc_stolen = []
    for _p in ("открой мне глаза на правду", "переведи фразу на английский",
               "расскажи про историю рима", "сделай скриншот экрана",
               "какая погода в москве"):
        _r, _d, _s = _oc_run(_p)
        if _r is not None:
            _oc_stolen.append(_p)
    if _oc_stolen:
        problems.append("ядро перехватило фразы для модели: " + "; ".join(_oc_stolen))
    else:
        print("   обычные фразы остаются модели, ядро молчит")

    checks += 1
    _r, _d, _s = _oc_run("напомни купить хлеб")
    if _r is None or _d.calls:
        problems.append("напоминание без часа выдумало время")
    else:
        print("   напоминание без часа: спрашивает, а не выдумывает")

    checks += 1
    _r, _d, _s = _oc_run("открой загрузки")
    _oc_path = _d.calls[0][1].get("path", "") if _d.calls else ""
    if not _d.calls or _d.calls[0][0] != "open_path":
        problems.append("папка загрузок не пошла в open_path")
    elif not Path(_oc_path).is_absolute():
        problems.append("путь папки выдуман, а не взят у общего резолвера: " + _oc_path)
    else:
        print("   папки: настоящий путь от общего резолвера: " + _oc_path)

    checks += 1
    _oc_crash = []
    for _junk in (None, 12, "", "?!;", "x" * 5000):
        try:
            _oc.handle(_junk)
        except Exception as _e:
            _oc_crash.append(type(_e).__name__)
    if _oc_crash:
        problems.append("ядро падает на мусоре: " + ", ".join(_oc_crash))
    else:
        print("   мусор на входе: ни одного падения")
print()

print("РУКИ ДОХОДЯТ ДО ВЛАДЕЛЬЦА — текст и напоминания без сети:")

import ast as _hm_ast
import types as _hm_types

_HM_FILE = ROOT / "main.py"
_hm_src = _HM_FILE.read_text(encoding="utf-8") if _HM_FILE.exists() else ""
_hm_want = ("_on_text_command", "_answer_offline",
            "_reminder_checker_loop", "_deliver_reminder", "_say_local")

checks += 1
if "text command queued" in _hm_src:
    problems.append("в main.py осталось обещание очереди — фраза владельца снова умрёт молча")
else:
    print("   старое обещание очереди: в main.py его нет")

checks += 1
_hm_cls = None
_hm_methods = {}
try:
    for _hm_node in _hm_ast.parse(_hm_src).body:
        if isinstance(_hm_node, _hm_ast.ClassDef) and _hm_node.name == "JarvisLive":
            _hm_cls = _hm_node
except Exception as _hm_e:
    problems.append("main.py не разбирается: " + type(_hm_e).__name__)
if _hm_cls is not None:
    _hm_methods = {_m.name: _m for _m in _hm_cls.body
                   if isinstance(_m, _hm_ast.FunctionDef)}
_hm_missing = [_w for _w in _hm_want if _w not in _hm_methods]
if _hm_missing:
    problems.append("в main.py нет методов: " + ", ".join(_hm_missing))
else:
    print("   развилка и выдача напоминаний на месте, обе названы своими именами")

if not _hm_missing:

    class _HmFuture:
        """Судьба фразы, отданной в сессию."""

        def __init__(self, outcome):
            self.outcome = outcome
            self.waited = None

        def result(self, timeout=None):
            self.waited = timeout
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return self.outcome

    class _HmAsyncio:
        def __init__(self, outcome=None):
            self.calls = []
            self.outcome = outcome
            self.futures = []

        def run_coroutine_threadsafe(self, coro, loop):
            self.calls.append(coro)
            if self.outcome is None:
                return None
            _f = _HmFuture(self.outcome)
            self.futures.append(_f)
            return _f

    class _HmUi:
        def __init__(self):
            self.log = []

        def write_log(self, text):
            self.log.append(text)

    class _HmSm:
        def __init__(self, alive):
            self._alive = alive

        def is_writable(self):
            return self._alive

    class _HmRig:
        """Двойник JarvisLive: main.py целиком не ввозится нарочно."""

        def __init__(self, alive):
            self.ui = _HmUi()
            self._loop = object() if alive else None
            self._sm = _HmSm(alive)
            self.sent = []
            self.spoken = []

        def _safe_send_text(self, text):
            self.sent.append(text)
            return "coro"

        def speak(self, text):
            self.spoken.append(text)

    class _HmQuietThreading:
        """Стенду нужен маршрут фразы, а не звук: поток не стартует."""

        class Thread:
            def __init__(self, target=None, name=None, daemon=None,
                         args=(), kwargs=None):
                self.target = target
                self.name = name
                self.daemon = daemon

            def start(self):
                return None

    _hm_mod = _hm_ast.Module(body=[_hm_methods[_w] for _w in _hm_want],
                             type_ignores=[])
    _hm_ast.fix_missing_locations(_hm_mod)
    _hm_code = compile(_hm_mod, "main.py<guard>", "exec")

    def _hm_rig(alive, outcome=None):
        _hm_spy = _HmAsyncio(outcome)
        space = {"asyncio": _hm_spy, "threading": _HmQuietThreading,
                 "print": lambda *a, **k: None}
        exec(_hm_code, space)
        rig = _HmRig(alive)
        for _w in _hm_want:
            setattr(rig, _w, _hm_types.MethodType(space[_w], rig))
        rig.spy = _hm_spy
        return rig

    checks += 1
    _hm_live = _hm_rig(True)
    _hm_live._on_text_command("сколько времени")
    if _hm_live.sent != ["сколько времени"] or _hm_live.ui.log:
        problems.append("при живой сессии текст ушёл не в модель: отправлено "
                        + repr(_hm_live.sent) + ", в окно " + repr(_hm_live.ui.log))
    else:
        print("   живая сессия: рот один, ядро молчит")

    checks += 1
    _hm_off = _hm_rig(False)
    _hm_off._on_text_command("сколько сейчас времени")
    _hm_line = _hm_off.ui.log[0] if _hm_off.ui.log else ""
    if _hm_off.sent or not _hm_line.startswith("Jarvis: ") or "Сейчас" not in _hm_line:
        problems.append("без сети вопрос о времени остался без ответа: " + repr(_hm_off.ui.log))
    else:
        print("   без сети: " + _hm_line)

    checks += 1
    _hm_no = _hm_rig(False)
    _hm_no._on_text_command("расскажи анекдот про кота")
    _hm_txt = _hm_no.ui.log[0] if _hm_no.ui.log else ""
    if "напоминания" not in _hm_txt or "очеред" in _hm_txt:
        problems.append("разговорная фраза без сети получила не честный отказ: " + repr(_hm_txt))
    else:
        print("   разговор без сети: отказ честный, со списком умений")

    checks += 1
    _hm_on = _hm_rig(True)
    _hm_on._deliver_reminder("Напоминание: выпить воды")
    _hm_dark = _hm_rig(False)
    _hm_dark._deliver_reminder("Напоминание: позвонить в банк")
    if (_hm_on.spoken != ["Напоминание: выпить воды"] or _hm_on.ui.log
            or _hm_dark.spoken
            or _hm_dark.ui.log != ["Jarvis: Напоминание: позвонить в банк"]):
        problems.append("напоминание доставлено неверно: вслух " + repr(_hm_on.spoken)
                        + ", без сети в окно " + repr(_hm_dark.ui.log))
    else:
        print("   напоминание: с сетью вслух, без сети в окно — но не в никуда")

    checks += 1
    _hm_saved = sys.modules.get("core.offline_core")
    _hm_fake = _hm_types.ModuleType("core.offline_core")

    def _hm_boom(*a, **k):
        raise RuntimeError("ядро сломано")

    _hm_fake.handle = _hm_boom
    _hm_fake.offline_notice = _hm_boom
    sys.modules["core.offline_core"] = _hm_fake
    try:
        _hm_dead = _hm_rig(False)
        _hm_dead._on_text_command("открой загрузки")
    finally:
        if _hm_saved is None:
            sys.modules.pop("core.offline_core", None)
        else:
            sys.modules["core.offline_core"] = _hm_saved
    if not _hm_dead.ui.log or "RuntimeError" not in _hm_dead.ui.log[0]:
        problems.append("сломанное ядро осталось незамеченным: " + repr(_hm_dead.ui.log))
    else:
        print("   ядро развалилось: владелец видит причину, а не тишину")

    checks += 1
    _hm_lost = _hm_rig(True, outcome=False)
    _hm_lost._on_text_command("сколько времени")
    _hm_lost_line = _hm_lost.ui.log[0] if _hm_lost.ui.log else ""
    if _hm_lost.sent != ["сколько времени"] or "Сейчас" not in _hm_lost_line:
        problems.append("фраза на умирающей сессии потеряна: отправлено "
                        + repr(_hm_lost.sent) + ", в окно " + repr(_hm_lost.ui.log))
    else:
        print("   рот умер на полуслове: фразу подхватили руки, а не тишина")

    checks += 1
    _hm_ok = _hm_rig(True, outcome=True)
    _hm_ok._on_text_command("сколько времени")
    _hm_wait = _hm_ok.spy.futures[0].waited if _hm_ok.spy.futures else None
    if _hm_ok.ui.log or _hm_wait is None or not (0 < float(_hm_wait) <= 10):
        problems.append("доехавшая фраза получила второй голос или ждали без часов: "
                        + repr(_hm_ok.ui.log) + ", ожидание " + repr(_hm_wait))
    else:
        print("   фраза доехала: ядро молчит, ожидание ответа ограничено")

checks += 1
try:
    from datetime import timedelta as _hm_td
    from datetime import timezone as _hm_tz
    from core.time_utils import describe_timezone as _hm_desc
    _hm_label = _hm_desc(_hm_tz(_hm_td(hours=3)))
except Exception as _hm_e:
    _hm_label = "ОШИБКА " + type(_hm_e).__name__
if _hm_label != "UTC+03:00":
    problems.append("метка пояса без имени собрана неверно: " + repr(_hm_label))
else:
    print("   пояс без имени (такой отдаёт Windows): UTC+03:00 один раз, а не дважды")

checks += 1
try:
    from core.offline_core import handle as _hm_handle
    _hm_reply = _hm_handle("сколько времени")
    _hm_now_text = "" if _hm_reply is None else _hm_reply.text
except Exception as _hm_e:
    _hm_now_text = ""
_hm_inside = _hm_now_text[_hm_now_text.rfind("(") + 1:_hm_now_text.rfind(")")]
_hm_parts = [_p.strip() for _p in _hm_inside.split(",")]
if not _hm_now_text or len(_hm_parts) != len(set(_hm_parts)):
    problems.append("в ответе про время метка пояса повторяется: " + repr(_hm_now_text))
else:
    print("   живой ответ про время: " + _hm_now_text)

checks += 1
_stale = []
if VOICE_REFERENCE["main.py"][0] in ("2f69e64713602327e4b3181a3ce69574",
                                     "1995f055f17707e314b769e6b20766e1",
                                     "ed7ff0b477147546de1eb7953a2baf05",
                                     "e61657020d0d870dc7fffe785142a61d",
                                     # эталон фазы 1д — до правки авто-записи
                                     "df308628b057716ca85882e98d123e26",
                                     # промежуточный, до правки комментария
                                     "8980c211540526c9916fe374299c5ad9"):
    _stale.append("main.py")
if VOICE_REFERENCE["ui.py"][0] == "710f10c2a098fa38e21641f8120df93a":
    _stale.append("ui.py")
if _stale:
    problems.append("эталон голоса не переснят — сторож судит старые: " + ", ".join(_stale))
else:
    print("   эталон голоса переснят: main.py 29.08.2026 (фаза 1е, "
          "авто-запись через дверь), ui.py 22.08.2026")
print()

print("ТИХИЙ ОФФЛАЙН (шаг 3 фазы 0.7)")

import ast as _q_ast
import socket as _q_socket
import types as _q_types

_Q_MAIN = (ROOT / "main.py").read_text(encoding="utf-8")
_Q_UI = (ROOT / "ui.py").read_text(encoding="utf-8")
_Q_LINK = (ROOT / "core" / "link.py").read_text(encoding="utf-8")
_Q_SCREEN = (ROOT / "core" / "screen_live_runtime.py").read_text(encoding="utf-8")


def _q_find(source, name):
    for node in _q_ast.walk(_q_ast.parse(source)):
        if isinstance(node, (_q_ast.FunctionDef, _q_ast.AsyncFunctionDef)):
            if node.name == name:
                return node
    return None


def _q_cut(source, names):
    found = {}
    for node in _q_ast.walk(_q_ast.parse(source)):
        if isinstance(node, (_q_ast.FunctionDef, _q_ast.AsyncFunctionDef)):
            if node.name in names:
                found[node.name] = node
    if len(found) != len(names):
        return None
    mod = _q_ast.Module(body=[found[n] for n in names], type_ignores=[])
    _q_ast.fix_missing_locations(mod)
    space = {"print": lambda *a, **k: None}
    exec(compile(mod, "guard", "exec"), space)
    return space


class _QSock:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _QDial:
    """Подмена socket.create_connection — сторож в сеть не ходит."""

    def __init__(self, outcome=None):
        self.calls = []
        self.outcome = outcome or _QSock()

    def __call__(self, target, timeout=None):
        self.calls.append((target, timeout))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class _QWin:
    def __init__(self, state):
        self.typing_queue = []
        self.is_typing = True
        self.speaking = False
        self.muted = False
        self.status_text = ""
        self._jarvis_state = state
        self.asked = []

    def _set_state_impl(self, state):
        self.asked.append(state)


# 1. глушитель гасит своё
checks += 1
_q_noise_ctx = {
    "message": "Exception in callback Connection.connection_lost(ConnectionResetError())",
    "exception": AttributeError("'ClientConnection' object has no attribute 'recv_messages'"),
}
try:
    from core.quiet_loop import NOISE_LINE as _q_line
    from core.quiet_loop import make_handler as _q_make
    _q_said = []
    _q_passed = []
    _q_h = _q_make(printer=_q_said.append,
                   previous=lambda lp, ctx: _q_passed.append(ctx))
    _q_h(None, dict(_q_noise_ctx))
except Exception as _q_e:
    problems.append("глушитель не отвечает: " + type(_q_e).__name__ + ": " + str(_q_e)[:70])
else:
    if _q_said != [_q_line] or _q_passed:
        problems.append("шум чужой библиотеки не погашен: " + repr(_q_said))
    elif not _q_line.isascii() or "\n" in _q_line:
        problems.append("строка глушителя не однострочная ASCII: " + repr(_q_line))
    else:
        print("   шум websockets гаснет одной строкой, без трассировки")

# 2. чужое горе проходит насквозь
checks += 1
try:
    _q_said2 = []
    _q_passed2 = []
    _q_h2 = _q_make(printer=_q_said2.append,
                    previous=lambda lp, ctx: _q_passed2.append(ctx))
    _q_h2(None, {"message": "Task exception was never retrieved",
                 "exception": ValueError("настоящая беда")})
    _q_h2(None, {"message": "Exception in callback Connection.connection_lost()",
                 "exception": AttributeError("'X' object has no attribute 'transport'")})
except Exception as _q_e:
    problems.append("глушитель упал на чужой ошибке: " + type(_q_e).__name__)
else:
    if _q_said2 or len(_q_passed2) != 2:
        problems.append("глушитель съел чужую беду: пропущено "
                        + str(len(_q_passed2)) + " из 2")
    else:
        print("   чужие ошибки проходят насквозь — молча ничего не теряется")

# 3. глушитель не падает на кривом входе
checks += 1
try:
    _q_said3 = []
    _q_h3 = _q_make(printer=_q_said3.append, previous=lambda lp, ctx: None)
    _q_h3(None, None)
    _q_h3(None, {"exception": None})
except Exception as _q_e:
    problems.append("глушитель сам стал источником падения: " + type(_q_e).__name__)
else:
    print("   кривой контекст не валит глушитель")

# 4. глушитель стоит в обоих циклах событий
checks += 1
_q_installed = []
for _q_name, _q_src in (("main.py", _Q_MAIN), ("core/screen_live_runtime.py", _Q_SCREEN)):
    if "quiet_loop" in _q_src and "_install_quiet" in _q_src:
        _q_installed.append(_q_name)
if len(_q_installed) != 2:
    problems.append("глушитель врезан не везде: " + repr(_q_installed))
else:
    print("   глушитель стоит в обоих циклах: голосовом и экранном")

# 5. три ответа двери связи
checks += 1
try:
    from core.link import ALIVE as _q_alive
    from core.link import DOWN as _q_down
    from core.link import UNKNOWN as _q_unk
    from core.link import probe as _q_probe
    from core.link import says_no as _q_says_no
    _q_ok = _QDial()
    _q_verdicts = (_q_probe(connector=_q_ok),
                   _q_probe(connector=_QDial(OSError("getaddrinfo failed"))),
                   _q_probe(connector=_QDial(RuntimeError("странное"))))
except Exception as _q_e:
    problems.append("дверь связи не отвечает: " + type(_q_e).__name__ + ": " + str(_q_e)[:70])
else:
    if _q_verdicts != (_q_alive, _q_down, _q_unk):
        problems.append("дверь связи отвечает неверно: " + repr(_q_verdicts))
    elif not _q_ok.outcome.closed:
        problems.append("проверка связи оставила соединение открытым")
    elif _q_says_no(connector=_QDial(RuntimeError("?"))) is not False:
        problems.append("сомнение засчитано как «сети нет» — запрём себя в оффлайне")
    else:
        print("   дверь связи: yes / no / unknown, сомнение — в пользу попытки")

# 6. адрес живёт в реестре, а не в коде
checks += 1
try:
    from config.loader import get_limit as _q_limit
    from core.link import address as _q_address
    _q_host, _q_port, _q_timeout = _q_address()
    _q_dial = _QDial()
    _q_probe(connector=_q_dial)
except Exception as _q_e:
    problems.append("адрес проверки не читается: " + type(_q_e).__name__)
else:
    if not _q_host or not _q_port:
        problems.append("адрес проверки пропал из config/registry.yaml")
    elif _q_host in _Q_LINK:
        problems.append("адрес поставщика зашит в core/link.py")
    elif _q_dial.calls != [((_q_host, _q_port), _q_timeout)]:
        problems.append("проверка стучалась не туда: " + repr(_q_dial.calls))
    elif _q_limit("provider", "timeout_seconds", None) != 60:
        problems.append("срок ожидания клиента сбит врезкой probe_*")
    else:
        print("   адрес проверки из реестра: " + str(_q_host) + ":" + str(_q_port)
          + ", срок " + str(_q_timeout) + " с")

# 7. дверь связи не трогает сеть при ввозе
checks += 1
_q_mines = ("socket", "create_connection", "getaddrinfo")
_q_saved_net = {_n: getattr(_q_socket, _n) for _n in _q_mines}
_q_saved_mod = sys.modules.get("core.link")


def _q_boom(*a, **k):
    raise AssertionError("дверь связи полезла в сеть")


try:
    for _n in _q_mines:
        setattr(_q_socket, _n, _q_boom)
    sys.modules.pop("core.link", None)
    import importlib as _q_il
    _q_fresh = _q_il.import_module("core.link")
    _q_blind = _q_fresh.probe()
except Exception as _q_e:
    problems.append("дверь связи полезла в сеть при ввозе: " + type(_q_e).__name__)
    _q_blind = "?"
finally:
    for _n, _v in _q_saved_net.items():
        setattr(_q_socket, _n, _v)
    if _q_saved_mod is not None:
        sys.modules["core.link"] = _q_saved_mod
if _q_blind == "yes":
    problems.append("при заминированной сети проверка солгала «связь есть»")
elif _q_blind != "?":
    print("   дверь связи ввозится без единого касания сети")

# 8. socket ввозится лениво, своего рта у двери нет
checks += 1
_q_top = []
for _q_node in _q_ast.parse(_Q_LINK).body:
    if isinstance(_q_node, (_q_ast.Import, _q_ast.ImportFrom)):
        _q_names = [_a.name for _a in getattr(_q_node, "names", [])]
        _q_module = getattr(_q_node, "module", "") or ""
        if "socket" in _q_module or any("socket" in _x for _x in _q_names):
            _q_top.append(_q_module or ",".join(_q_names))
if _q_top:
    problems.append("socket ввозится на верхнем уровне двери связи: " + repr(_q_top))
elif "print(" in _Q_LINK:
    problems.append("у двери связи появился свой рот")
else:
    print("   socket ввозится внутри вызова, говорит только главный")

# 9. в цикле спрашивают про связь ДО сессии
checks += 1
_q_run = _q_find(_Q_MAIN, "run")
if _q_run is None:
    problems.append("в main.py пропал главный цикл run()")
else:
    _q_checks = [_n.lineno for _n in _q_ast.walk(_q_run)
                 if isinstance(_n, _q_ast.Call) and isinstance(_n.func, _q_ast.Attribute)
                 and _n.func.attr == "_link_says_no"]
    _q_sessions = [_n.lineno for _n in _q_ast.walk(_q_run)
                   if isinstance(_n, _q_ast.Call) and isinstance(_n.func, _q_ast.Attribute)
                   and _n.func.attr == "_run_session"]
    if not _q_checks or not _q_sessions:
        problems.append("в цикле нет вопроса о связи или вызова сессии")
    elif min(_q_checks) > min(_q_sessions):
        problems.append("проверка связи стоит после сессии — память собирается впустую")
    else:
        print("   про связь спрашивают до того, как поднимать сессию")

# 10. без сети ветка ждёт и ничего не тратит
checks += 1
_q_branch = None
if _q_run is not None:
    for _q_node in _q_ast.walk(_q_run):
        if isinstance(_q_node, _q_ast.If):
            if any(isinstance(_x, _q_ast.Continue) for _x in _q_ast.walk(_q_node)):
                _q_branch = _q_node
                break
if _q_branch is None:
    problems.append("в цикле нет ветки ожидания связи")
else:
    _q_dump = _q_ast.dump(_q_branch)
    _q_bad = [_w for _w in ("_run_session", "_build_config") if _w in _q_dump]
    if _q_bad:
        problems.append("без сети всё равно тратится работа: " + ", ".join(_q_bad))
    elif "sleep" not in _q_dump or "LINK_POLL_SECONDS" not in _q_dump:
        problems.append("ветка ожидания крутится без паузы по имени")
    else:
        print("   без сети: ни сессии, ни сборки памяти — только пауза")

# 11. первая попытка и слепая попытка
checks += 1
_q_dump_run = _q_ast.dump(_q_run) if _q_run is not None else ""
_q_miss = [_w for _w in ("first_attempt", "blind_countdown", "LINK_BLIND_TRY_EVERY")
           if _w not in _q_dump_run]
if _q_miss:
    problems.append("проверка стала приговором, нет: " + ", ".join(_q_miss))
else:
    print("   холодный старт идёт всегда, а раз в минуту — попытка вслепую")

# 12. сам метод отвечает честно и не падает
checks += 1
_q_space = _q_cut(_Q_MAIN, ["_link_says_no"])
if _q_space is None:
    problems.append("из main.py пропал метод _link_says_no")
else:
    _q_before = sys.modules.get("core.link")
    _q_answers = []
    try:
        for _q_ans in (True, False):
            _q_fake = _q_types.ModuleType("core.link")
            _q_fake.says_no = (lambda value: (lambda *a, **k: value))(_q_ans)
            sys.modules["core.link"] = _q_fake
            _q_answers.append(_q_space["_link_says_no"](object()))
        sys.modules["core.link"] = _q_types.ModuleType("core.link")
        _q_answers.append(_q_space["_link_says_no"](object()))
    except Exception as _q_e:
        problems.append("метод проверки связи упал: " + type(_q_e).__name__)
    finally:
        if _q_before is not None:
            sys.modules["core.link"] = _q_before
        else:
            sys.modules.pop("core.link", None)
    if _q_answers != [True, False, False]:
        problems.append("метод проверки связи отвечает неверно: " + repr(_q_answers))
    else:
        print("   сломанная проверка = «пробуем», а не «сидим в оффлайне»")

# 13. владелец узнаёт об обрыве и возврате — без притворства речью
checks += 1
_q_lines = []
if _q_run is not None:
    for _q_node in _q_ast.walk(_q_run):
        if isinstance(_q_node, _q_ast.Constant) and isinstance(_q_node.value, str):
            if "Сети нет" in _q_node.value or "Связь вернулась" in _q_node.value:
                _q_lines.append(_q_node.value)
if len(_q_lines) < 2:
    problems.append("владелец не узнаёт ни об обрыве, ни о возврате связи")
elif any(not _q_l.startswith("SYS:") for _q_l in _q_lines):
    problems.append("статус связи притворяется речью: " + repr(_q_lines))
else:
    print("   об обрыве и возврате сообщают строкой SYS:, без голоса")

# 14. консольные строки — только ASCII (у владельца ломается даже тире)
checks += 1
_q_console = [_l.strip() for _l in _Q_MAIN.splitlines() if "[Link]" in _l]
_q_dirty = [_l for _l in _q_console if not _l.isascii()]
if not _q_console:
    problems.append("из main.py пропали консольные строки про связь")
elif _q_dirty:
    problems.append("консольная строка не ASCII — будет кракозябра: " + repr(_q_dirty[0][:60]))
else:
    print("   консольные строки про связь читаемы в любой консоли")

# 15. окно не врёт и не забывает
checks += 1
_q_ui = _q_cut(_Q_UI, ["_set_state_impl", "_start_typing"])
if _q_ui is None:
    problems.append("в ui.py пропали _set_state_impl или _start_typing")
else:
    _q_off = _QWin("LISTENING")
    _q_ui["_set_state_impl"](_q_off, "OFFLINE")
    _q_other = _QWin("LISTENING")
    _q_ui["_set_state_impl"](_q_other, "НЕЗНАКОМОЕ")
    _q_keep = _QWin("OFFLINE")
    _q_ui["_start_typing"](_q_keep)
    _q_back = _QWin("SPEAKING")
    _q_ui["_start_typing"](_q_back)
    if _q_off.status_text != "OFFLINE":
        problems.append("окно показало " + repr(_q_off.status_text) + " вместо OFFLINE")
    elif _q_other.status_text != "ONLINE":
        problems.append("старое поведение окна сломано: " + repr(_q_other.status_text))
    elif _q_keep.asked:
        problems.append("после печати окно сбросило OFFLINE в " + repr(_q_keep.asked))
    elif _q_back.asked != ["LISTENING"]:
        problems.append("обычное возвращение в LISTENING сломано: " + repr(_q_back.asked))
    else:
        print("   окно: OFFLINE честно, после печати не затирается, остальное как было")
print()

print("СВОЙ ГОЛОС БЕЗ СЕТИ — шаг 29:")

_v_path = ROOT / "core" / "say_local.py"

checks += 1
if not _v_path.exists():
    problems.append("нет файла core/say_local.py — без сети Джарвис снова немой")
else:
    print("   модуль своего голоса на месте")

if _v_path.exists():
    _v_src = _v_path.read_text(encoding="utf-8")

    checks += 1
    _v_net = [_w for _w in ("genai", "requests", "urllib", "socket") if _w in _v_src]
    if _v_net:
        problems.append("голос тянется в сеть: " + ", ".join(_v_net))
    else:
        print("   голос не знает про сеть вовсе")

    checks += 1
    _v_top = [_ln for _ln in _v_src.splitlines()
              if (_ln.startswith("import ") or _ln.startswith("from "))
              and ("win32" in _ln or "pythoncom" in _ln)]
    if _v_top:
        problems.append("pywin32 импортируется сверху — упадёт всё, где нет Windows: " + repr(_v_top))
    else:
        print("   Windows-часть подгружается лениво")

    import core.say_local as _v_mod

    checks += 1
    _v_ears = _v_mod.clean_for_ears("Jarvis: время и дата \u00b7 напоминания")
    if _v_ears.startswith("Jarvis") or "\u00b7" in _v_ears:
        problems.append("вслух читается оформление окна: " + repr(_v_ears))
    else:
        print("   для ушей: " + _v_ears)

    checks += 1
    _v_order = ("[НАПОМИНАНИЕ] Немедленно скажи мне вслух "
                "следующее напоминание: выключить чайник")
    _v_hum = _v_mod.human_reminder(_v_order)
    if "Немедленно скажи" in _v_hum or "выключить чайник" not in _v_hum:
        problems.append("служебный приказ модели уходит владельцу: " + repr(_v_hum))
    else:
        print("   напоминание по-человечески: " + _v_hum)

    class _VTok:
        def __init__(self, lang):
            self._lang = lang

        def GetAttribute(self, name):
            return self._lang

        def GetDescription(self):
            return "voice " + self._lang

    class _VVoice:
        def __init__(self):
            self.said = []
            self.flags = []
            self.waited = []
            self.Voice = None

        def GetVoices(self):
            return [_VTok("409"), _VTok("419")]

        def Speak(self, text, flags=0):
            self.said.append(text)
            self.flags.append(flags)

        def WaitUntilDone(self, ms=0):
            self.waited.append(ms)
            return True

    checks += 1
    _v_voice = _VVoice()
    _v_ok = _v_mod.say("проверка", dispatch=lambda name: _v_voice)
    if not _v_ok or _v_voice.said != ["проверка"]:
        problems.append("голос не произнёс фразу на двойнике: " + repr(_v_voice.said))
    elif _v_voice.flags != [1]:
        problems.append("речь отдана не тем флагом: " + repr(_v_voice.flags))
    elif not _v_voice.waited:
        problems.append("рот не дожидается конца фразы — владелец услышит тишину")
    elif _v_voice.Voice is None or _v_voice.Voice.GetAttribute("Language") != "419":
        problems.append("русский голос выбран не по коду языка 419")
    else:
        print("   двойник SAPI: сказал, дождался конца, голосом языка 419")

    checks += 1
    import threading as _v_threading
    import time as _v_time

    _v_state = {"busy": False, "overlap": False}

    class _VSlow:
        def __init__(self):
            self.Voice = None

        def GetVoices(self):
            return []

        def Speak(self, text, flags=0):
            if _v_state["busy"]:
                _v_state["overlap"] = True
            _v_state["busy"] = True
            _v_time.sleep(0.03)
            _v_state["busy"] = False

        def WaitUntilDone(self, ms=0):
            return True

    _v_threads = [
        _v_threading.Thread(
            target=lambda: _v_mod.say("фраза", dispatch=lambda name: _VSlow())
        )
        for _ in range(3)
    ]
    for _v_t in _v_threads:
        _v_t.start()
    for _v_t in _v_threads:
        _v_t.join(timeout=10)
    if _v_state["overlap"]:
        problems.append("два голоса говорят одновременно — замка на рот нет")
    else:
        print("   замок на один рот: три фразы встали в очередь")

    checks += 1
    _v_had_pytest = "pytest" in sys.modules
    if not _v_had_pytest:
        import types as _v_types
        sys.modules["pytest"] = _v_types.ModuleType("pytest")
    try:
        _v_muted = _v_mod.say("это не должно прозвучать")
    finally:
        if not _v_had_pytest:
            sys.modules.pop("pytest", None)
    if _v_muted is not False:
        problems.append("живой синтезатор работает во время тестов — прогон заговорит вслух")
    else:
        print("   предохранитель: прогон тестов молчит")

checks += 1
if "_say_local" not in _hm_src:
    problems.append("в main.py нет своего рта — без сети снова только текст в окне")
else:
    print("   main.py умеет говорить сам")

checks += 1
_v_body = _hm_src.split("def _say_local", 1)[-1].split("def set_speaking", 1)[0]
if "is_writable" not in _v_body:
    problems.append("свой голос не смотрит на живую сессию — заговорят два рта разом")
elif "threading.Thread" not in _v_body:
    problems.append("речь звучит в рабочем потоке — окно будет замирать на каждой фразе")
else:
    print("   замок на два рта и отдельный поток речи на месте")

checks += 1
_v_del = _hm_src.split("def _deliver_reminder", 1)[-1].split("def _build_config", 1)[0]
if "human_reminder" not in _v_del:
    problems.append("напоминание без сети всё ещё показывает приказ модели")
elif "self.speak(msg)" not in _v_del:
    problems.append("при живой связи модели больше не уходит исходная команда")
else:
    print("   напоминание: владельцу по-человечески, модели как раньше")

checks += 1
_v_ans = _hm_src.split("def _answer_offline", 1)[-1].split("def set_speaking", 1)[0]
if "self._say_local(" not in _v_ans:
    problems.append("ответ ядра не озвучивается — шаг 29 откатился назад")
else:
    print("   отве�� без сети уходит и в окно, и в голос")

checks += 1
_v_leak = ROOT / "memory" / "reminders.json"
if _v_leak.exists():
    problems.append("в папке проекта лежит живое напоминание от тестов: " + str(_v_leak))
else:
    print("   живые данные чисты: тесты больше не ставят напоминаний")
print()

# ───── 4.13 одна касса: Джарвис помнит, что делал (фаза 0.7, шаг 30) ─────
print("ШАГ 30. Одна касса «что ты делал»")
_al_file = ROOT / "core" / "action_log.py"
_al_src = ""

checks += 1
if not _al_file.exists():
    problems.append("касса пропала: core/action_log.py нет, «что ты делал» снова пусто")
else:
    _al_src = _al_file.read_text(encoding="utf-8", errors="replace")
    if "JARVIS_STATE_DIR" not in _al_src:
        problems.append("у кассы нет предохранителя: прогон тестов полезет в базу владельца")
    elif '"pytest" not in sys.modules' not in _al_src:
        problems.append("предохранитель кассы больше не смотрит на pytest")
    elif "print(" in _al_src:
        problems.append("касса начала печатать — ядру печать запрещена")
    else:
        print("   касса на месте: с предохранителем и молча")

checks += 1
_al_oc = (ROOT / "core" / "offline_core.py").read_text(encoding="utf-8", errors="replace")
_al_main = (ROOT / "main.py").read_text(encoding="utf-8", errors="replace")
if "from core.action_log import note" not in _al_oc:
    problems.append("оффлайн-ядро больше не пишет сделанное — журнал снова врёт про пустоту")
elif "from core.action_log import note as _note_action" not in _al_main:
    problems.append("онлайновый путь main.py отвязался от кассы — снова два журнала")
elif "record_action as _ds_record" in _al_main:
    problems.append("старый путь записи не удалён, а оставлен рядом — будет двоить")
else:
    print("   оба исполнителя пишут в одну кассу, старый путь удалён")

checks += 1
_al_run = _al_oc.split("def _run_tool", 1)[-1].split("def _note", 1)[0]
_al_door = _al_run.split("if not result.allowed:", 1)[-1].split("try:", 1)[0]
if "_note(" in _al_door:
    problems.append("отказ двери попадает в журнал — Джарвис будет хвастаться тем, чего не делал")
elif "_note(tool, params, answer, True)" not in _al_run:
    problems.append("сделанное дело не записывается: пропала запись после успешного инструмента")
elif "_note(tool, params, _short(exc), False)" not in _al_run:
    problems.append("упавший инструмент не записывается как неудача")
else:
    print("   в журнале только то, что случилось: успех и падение, но не отказ двери")

checks += 1
_al_ds = (ROOT / "core" / "dialogue_state.py").read_text(encoding="utf-8", errors="replace")
_al_jr = (ROOT / "core" / "journal.py").read_text(encoding="utf-8", errors="replace")
if "RECENT_MAX = 8" not in _al_src:
    problems.append("касса помнит не восемь дел — три места разошлись")
elif "_JOURNAL_MAX = 8" not in _al_ds:
    problems.append("память диалога помнит не восемь дел — три места разошлись")
elif "JOURNAL_MAX = 8" not in _al_jr:
    problems.append("база помнит не восемь дел — три места разошлись")
else:
    print("   три места согласны: последних дел ровно восемь")
print()

# ───── 4.14 громкость без сети (фаза 0.7, шаг 31) ─────
print("ШАГ 31. Громкость без сети")
_vv_file = ROOT / "actions" / "volume.py"
_vv_src = ""

checks += 1
if not _vv_file.exists():
    problems.append("инструмент громкости пропал: actions/volume.py нет")
else:
    _vv_raw = _vv_file.read_bytes()
    _vv_src = _vv_raw.decode("utf-8", errors="replace")
    if b"\r\n" not in _vv_raw:
        problems.append("actions/volume.py потерял CRLF — остальные actions/* с CRLF")
    elif _vv_src.count("print(") != 1:
        problems.append("у громкости не одна печатная строка — лишняя болтовня в окне")
    elif "[Volume] точный регулятор не вышел" not in _vv_src:
        problems.append("громкость печатает не то — причина отказа потеряна")
    else:
        print("   инструмент громкости на месте и говорит ровно одну строку")

checks += 1
if "requests" in _vv_src or "urllib" in _vv_src or "socket" in _vv_src:
    problems.append("инструмент громкости знает про сеть — ему туда нельзя")
elif [ln for ln in _vv_src.splitlines()
      if ln.startswith(("import comtypes", "import pycaw", "from pycaw",
                        "from comtypes", "import ctypes"))]:
    problems.append("Windows-часть громкости ввозится сразу — прогон тестов упадёт")
else:
    print("   громкость не знает про сеть, Windows-часть подгружается лениво")

checks += 1
if "_regulator" not in _vv_src or "_tap" not in _vv_src:
    problems.append("у громкости пропали швы: точный регулятор или клавиши")
elif "def _blind(" not in _vv_src:
    problems.append("у громкости нет запасного пути — без pycaw она онемеет")
else:
    print("   два пути на месте: точное число и запасные клавиши")

checks += 1
if "_TO_ZERO" not in _vv_src or "_BLIND_STEP" not in _vv_src:
    problems.append("вслепую пропал отсчёт от нуля — «поставь 24 процента» снова отвалится")
elif "примерно" not in _vv_src:
    problems.append("вслепую число называется без слова «примерно» — это ложная точность")
else:
    print("   вслепую число ставится отсчётом от нуля и зовётся примерным")

checks += 1
if "_remember(exc)" not in _vv_src or "_LAST_WHY" not in _vv_src:
    problems.append("причина отказа регулятора снова глотается молча — чинить будет нечем")
elif "except Exception:" in _vv_src.split("def volume(", 1)[-1]:
    problems.append("в volume() остался молчаливый except — причина утечёт в мусор")
else:
    print("   причина отказа точного регулятора запоминается и произносится")

checks += 1
_vv_sec = (ROOT / "core" / "security.py").read_text(encoding="utf-8", errors="replace")
if '"volume": ToolPolicy(' not in _vv_sec:
    problems.append("дверь не знает про громкость — неизвестный инструмент запрещён")
elif '"computer_settings": ToolPolicy(' not in _vv_sec:
    problems.append("пропала политика computer_settings — замок на настройки снят")
elif 'status="blocked"' not in _vv_sec.split('"computer_settings": ToolPolicy(', 1)[1][:400]:
    problems.append("computer_settings больше не blocked — этого не просили")
else:
    print("   громкость разрешена отдельно, настройки системы по-прежнему закрыты")

checks += 1
_vv_oc = (ROOT / "core" / "offline_core.py").read_text(encoding="utf-8", errors="replace")
if '"volume": ("actions.volume", "volume")' not in _vv_oc:
    problems.append("ядро не умеет ввозить громкость")
elif "_route_volume" not in _vv_oc.split("_ROUTES", 1)[-1]:
    problems.append("маршрут громкости не включён в таблицу — фраза уйдёт в пустоту")
elif "громкость" not in _vv_oc.split("_SKILLS", 1)[-1].split(")\n\n", 1)[0]:
    problems.append("меню не обещает громкость — владелец не узнает, что она есть")
else:
    print("   ядро знает громкость и обещает её в меню")

checks += 1
_vv_route = _vv_oc.split("def _route_volume", 1)[-1].split("def ", 1)[0]
if "_VOLUME_ALIEN" not in _vv_oc:
    problems.append("пропал отсев чужих фраз — «включи музыку погромче» уйдёт в громкость")
elif "музык" not in _vv_oc.split("_VOLUME_ALIEN", 1)[1][:400]:
    problems.append("отсев чужих фраз больше не знает про музыку")
elif "_VOLUME_ALIEN.search" not in _vv_route.split("return", 1)[0]:
    problems.append("чужие фразы отсеиваются НЕ первым делом — будет перехват")
else:
    print("   музыка и видео остаются большой модели")
print()

print("=" * 72)
if problems:
    print("ПРОБЛЕМЫ (" + str(len(problems)) + "):")
    for pr in problems:
        print("  ✗ " + pr)
    print("Шаг не принимаем — пришли этот вывод целиком.")
    sys.exit(1)
print("ВСЁ СОВПАЛО: " + str(checks) + " проверок, ни одного выхода в сеть, голос не тронут.")
print("=" * 72)
sys.exit(0)
