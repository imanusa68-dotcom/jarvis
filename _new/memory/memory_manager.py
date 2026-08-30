"""
memory_manager.py — MARK XXV Hafıza Sistemi
============================================
Düzeltmeler:
  - _MEMORY_EVERY_N_TURNS: 3 → 1 (her turda kontrol)
  - Stage 1 YES/NO check daha geniş kriterlere sahip
  - Extraction prompt daha kapsamlı ve agresif
  - Projeleri, favori şeyleri, arkadaşları daha iyi yakalar
"""

import json
import re
from datetime import datetime
from threading import Lock
from pathlib import Path
import sys
import time

# Stage 3.0 - durability floor. All durable JSON state goes through this one
# module: atomic writes, snapshots, quarantine instead of silent data loss.
from core.safe_json import (
    atomic_write_json,
    import_legacy_once,
    load_json_safe,
    state_dir,
    state_path,
    update as safe_update,
)


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR           = get_base_dir()

# ── Stage 3.0: where memory actually lives ──────────────────────────────────
# Memory used to be stored INSIDE the build folder. Since every new build is
# unpacked into a new directory, memory silently reset on every update - the
# assistant was reborn blank each time. It now lives in the same durable dir
# as jarvis.db (~/.jarvis), and the old build-folder file is imported once.
_LEGACY_MEMORY_PATH = BASE_DIR / "memory" / "long_term.json"

_migrated_for: str | None = None   # state dir the one-time import already ran for
_lock              = Lock()
MAX_VALUE_LENGTH   = 400
# Суточный потолок вызовов здесь больше НЕ живёт: его считает core/metering,
# один на весь проект и по квотным суткам поставщика (блок 5).

# ── Stage1 call throttle ────────────────────────────────────────────────────
# Prevents Stage1 YES/NO gate from firing on every single turn.
# Stage1 will run at most once per _MEMORY_STAGE1_MIN_INTERVAL seconds.
_MEMORY_STAGE1_MIN_INTERVAL: float = 90.0   # seconds between Stage1 checks
_last_memory_stage1:         float = 0.0    # time.monotonic() of last check


def _memory_path() -> Path:
    """Resolved at call time so tests can redirect state via JARVIS_STATE_DIR."""
    return state_path("long_term.json")


def _ensure_migrated() -> None:
    """Idempotent one-time lift of build-folder memory into the durable dir.

    Safe to call on every read/write: it is a no-op once the durable copy
    exists, and it never deletes the user's original file.
    """
    global _migrated_for
    key = str(state_dir())
    if _migrated_for == key:
        return
    try:
        import_legacy_once(_LEGACY_MEMORY_PATH, _memory_path(), label="Memory")
        # Переезд api_usage.json убран вместе со своим счётчиком: файл больше
        # никто не читает, а тащить его из папки сборки в дом — значит делать
        # вид, что он ещё что-то значит.
        _migrated_for = key
    except Exception as exc:
        print(f"[Memory] \u26a0\ufe0f Import of legacy memory failed: {exc}")


# Convenience alias for callers/logs that want the current location.
MEMORY_PATH = _memory_path()


def _empty_memory() -> dict:
    return {
        "identity":      {},
        "preferences":   {},
        "projects":      {},
        "relationships": {},
        "wishes":        {},
        "notes":                {},
        "communication_habits": {}
    }


def load_memory() -> dict:
    """Load long-term memory from the durable state dir.

    Stage 3.0 behaviour change (this was the amnesia bug): a corrupt file is
    QUARANTINED and recovery is attempted from rotating snapshots. We never
    again return an empty dict on a parse error and then let the next save
    overwrite the damaged file with it.
    """
    _ensure_migrated()
    with _lock:
        data = load_json_safe(_memory_path(), _empty_memory, label="Memory")
        for key in _empty_memory():
            if not isinstance(data.get(key), dict):
                data[key] = {}
        return data


def save_memory(memory: dict) -> None:
    """Durably replace memory on disk (temp file + fsync + atomic rename).

    If anything goes wrong the previous file is left completely untouched -
    a failed save can no longer truncate or blank the user's memory.
    """
    if not isinstance(memory, dict):
        return
    _ensure_migrated()
    with _lock:
        try:
            atomic_write_json(_memory_path(), memory)
        except Exception as exc:
            print(f"[Memory] ⚠️ Save error — disk copy left unchanged: {exc}")


def _canonical_key(key) -> str:
    """Привести имя факта к одному виду: snake_case, нижний регистр.

    ЗАЧЕМ. Замер живого разговора владельца 30.08.2026. Он сказал «кстати
    моего кота зовут лев а не тигр». Модель прочитала имя ключа из БЛОКА
    ПРОМПТА, где строки печатаются человеку — «Cat Name: Cat named Tigr»,
    — и позвала forget_memory('Cat Name'). На диске ключ зовётся
    `cat_name`, поиск шёл по точному совпадению, ответ был «Not found».
    Затем save_memory('Cat Name') СОЗДАЛ ВТОРОЙ ключ рядом с первым, и в
    промпт поехали две строки подряд:
        - Cat Name: Cat named Tigr
        - Cat Name: Lev
    То есть Джарвис одновременно знает два имени одного кота и не может
    предпочесть верное. Это потеря данных, а не косметика: старое имя не
    стёрлось, а поправка не заменила его.

    ПОЧЕМУ ПРАВКА ЗДЕСЬ, А НЕ В ПРОМПТЕ. Мы сами печатаем в блок
    `key.replace('_', ' ').title()` — человеку так читать легче. Значит
    дом ПРЕДЛАГАЕТ модели написание «Cat Name» и сам же его не принимает.
    Просить модель угадывать обратное преобразование — перекладывать нашу
    работу на догадку; она уже угадала неверно. Одно место входа лечит
    все зовущие сразу: и запись, и стирание, и авто-запись.

    ЧТО НАМЕРЕННО НЕ ДЕЛАЕТСЯ. Не переводим, не сокращаем, не правим
    опечатки, не трогаем ЗНАЧЕНИЕ факта — только имя ключа, и только
    регистр с пробелами и дефисами. Более умное сведение (синонимы,
    склейка cat/kitten) означало бы, что автоматика решает, какие два
    факта владельца — один; это запрещено инвариантом дома.

    Не-строку возвращаем как есть: падать на мусоре из модели нельзя,
    а вызывающий уже умеет пропускать пустое.
    """
    if not isinstance(key, str):
        return key
    canon = key.strip().lower().replace(" ", "_").replace("-", "_")
    while "__" in canon:
        canon = canon.replace("__", "_")
    return canon or key


def _truncate_value(val: str) -> str:
    if isinstance(val, str) and len(val) > MAX_VALUE_LENGTH:
        return val[:MAX_VALUE_LENGTH].rstrip() + "…"
    return val


def _recursive_update(target: dict, updates: dict,
                      _seen: list | None = None) -> bool:
    """Влить факты в память. True, если появилось что-то НОВОЕ.

    `_seen` — служебный список для рекурсии: туда попадают ключи
    фактов, у которых обновилась ТОЛЬКО дата: владелец повторил
    то же самое. В возвращаемое значение он НЕ входит:
    обновление даты не новость и не должно печатать «Saved».

    Параметр необязательный намеренно: вызовы с двумя
    аргументами — в том числе из тестов — работают как прежде.
    """
    changed = False
    for key, value in updates.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue

        # ИМЯ ФАКТА ПРИВОДИТСЯ К ОДНОМУ ВИДУ ЗДЕСЬ, на входе в память.
        # Замер (живой лог 30.08.2026): модель читает имена ключей из блока
        # промпта, где они напечатаны для человека — «Cat Name», — и пишет
        # их дословно. Без этой строки рядом с `cat_name` появлялся второй
        # ключ `Cat Name`, и в промпт уезжали ДВА имени одного кота.
        # Категории намеренно не касаемся: их список закрытый и проверяется
        # отдельно, а ключи модель придумывает сама.
        key = _canonical_key(key)

        if isinstance(value, dict) and "value" not in value:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
                changed = True
            if _recursive_update(target[key], value, _seen):
                changed = True
        else:
            if isinstance(value, dict) and "value" in value:
                new_val = _truncate_value(str(value["value"]))
            else:
                new_val = _truncate_value(str(value))

            # "said" carries the user's original sentence, untranslated. It rides
            # alongside the English value instead of replacing it: the value stays
            # short and reasonable for the prompt, while the original wording is
            # what search will match when the user asks in their own language.
            said = None
            if isinstance(value, dict):
                raw_said = value.get("said")
                if raw_said is not None and str(raw_said).strip():
                    said = _truncate_value(str(raw_said).strip())

            entry    = {"value": new_val, "updated": datetime.now().strftime("%Y-%m-%d")}
            existing = target.get(key, {})
            if said:
                entry["said"] = said
            elif isinstance(existing, dict) and existing.get("said"):
                # Never lose wording we already have just because a later, sloppier
                # save omitted it.
                entry["said"] = existing["said"]

            if (not isinstance(existing, dict)
                    or existing.get("value") != new_val
                    or existing.get("said") != entry.get("said")):
                target[key] = entry
                changed = True
            else:
                # ТО ЖЕ САМОЕ СКАЗАНО ЗАНОВО — ЭТО СОБЫТИЕ, А НЕ ПУСТОТА.
                #
                # Здесь был дефект, найденный замером (probe57) при подготовке
                # срока годности фактов. Раньше эта ветка отсутствовала: если
                # значение совпало, запись не трогали вовсе — и поле `updated`
                # навсегда оставалось датой ПЕРВОГО раза. Владелец мог сказать
                # «у меня опять болит голова» десять раз, а в памяти всё так же
                # стояло 29.08, будто с тех пор он молчал.
                #
                # Пока промпт не смотрел на дату, это было незаметно. Как только
                # дата начинает решать, что показывать (см. _expire_stale ниже),
                # тот же дефект становится потерей: однажды скрытый факт не
                # вернулся бы НИКОГДА, сколько бы владелец ни повторял. Поэтому
                # правка стоит ЗДЕСЬ и отдельно — лечится причина, а не симптом.
                #
                # `changed` НАМЕРЕННО остаётся прежним. Обновление даты — не
                # новость: печатать «💾 Saved» о факте, который и так лежал,
                # значит врать о записи, которой не было. Ровно за такую ложь
                # (сообщение об успехе там, где успеха нет) в этом файле уже
                # переписывался update_memory.
                #
                # Мы правим ТОЛЬКО дату и не касаемся ни `value`, ни `said`:
                # если существующая запись хранит дословную фразу владельца,
                # а повтор пришёл без неё, подмена целой записи потеряла бы
                # формулировку — то же самое, от чего защищает ветка выше.
                stamp = entry["updated"]
                if existing.get("updated") != stamp:
                    existing["updated"] = stamp
                    if _seen is not None:
                        _seen.append(key)

    return changed


def _note_in_index(memory: dict, memory_update: dict) -> None:
    """Mirror just-written facts into the search index (Stage 3B.5).

    Before this, the index was rebuilt from scratch on every lookup so that a
    fact said seconds ago could be found. Measured, that full rebuild costs
    2.5 s at 10 000 facts - paid mid-sentence, every time. Writing the one fact
    that changed costs microseconds and keeps both copies in step.

    It also fixes provenance: facts arriving here are marked as explicitly said
    with full confidence, instead of every row reporting source=legacy / 0.60
    even when the user had just spoken it.

    Never raises. The JSON file is still the durable copy; a failure to index
    must never turn into a failure to remember.
    """
    try:
        from memory.fact_store import note_fact
    except Exception:
        return
    for category, entries in memory_update.items():
        if not isinstance(entries, dict):
            continue
        for key in entries:
            stored = (memory.get(category) or {}).get(key)
            if not isinstance(stored, dict) or not stored.get("value"):
                continue
            note_fact(category, key, stored["value"],
                      verbatim=stored.get("said"))


def update_memory(memory_update: dict) -> dict:
    """Добавить факты в память, не потеряв чужие.

    БЛОК 9: ЧИТАТЬ-ПРАВИТЬ-ПИСАТЬ ТЕПЕРЬ ОДНО ДЕЙСТВИЕ, А НЕ ТРИ.
    Раньше здесь было: загрузить всё -> дописать своё -> сохранить всё. Между
    загрузкой и сохранением замка не было, и два потока затирали работу друг
    друга. Замерено 21.08.2026 на копии настоящей памяти владельца: голосовое
    «запомни» и фоновый извлекатель одновременно -> ФАКТ ВЛАДЕЛЬЦА ИСЧЕЗ.

    Достижимо это было легко: писателей памяти три (голосовое «запомни»,
    голосовое «забудь» и фоновый извлекатель, который рождается новым потоком
    на каждой реплике), а у фонового между чтением и записью стоит вызов модели
    на 6-8 секунд — окно наложения огромное.

    И хуже самой потери: старый код печатал «Saved» ПОСЛЕ неудачной записи —
    Джарвис говорил «запомнил» о том, чего не запомнил. Теперь об удаче
    сообщается только когда запись действительно легла.
    """
    if not isinstance(memory_update, dict) or not memory_update:
        return load_memory()

    _ensure_migrated()
    touched = False
    refreshed: list = []

    def change(memory: dict) -> dict:
        nonlocal touched
        touched = _recursive_update(memory, memory_update, refreshed)
        return memory

    try:
        memory = safe_update(_memory_path(), change, _empty_memory,
                             label="Memory")
    except Exception as exc:
        # Диск не пострадал: прежний файл цел (атомарная замена). Но и врать
        # про успех нельзя — отказ обязан быть слышен.
        print(f"[Memory] ⚠️ Не сохранил, прежняя копия цела: {exc}")
        return load_memory()

    if touched:
        print(f"[Memory] 💾 Saved: {list(memory_update.keys())}")
        _note_in_index(memory, memory_update)
    elif refreshed:
        # ПОВТОР ТОГО ЖЕ — новостей нет, поэтому молчим: печать
        # «Saved» о факте, который и так лежал, значит врать.
        #
        # Но индекс отметить ОБЯЗАНО, и это исправление второго,
        # пред-существовавшего дефекта (замер probe61/probe65).
        # `safe_update` перезаписывает файл КАЖДЫЙ раз, даже когда
        # содержание не изменилось. Меняется mtime — значит,
        # расходится «отпечаток» файла, и следующий recall_memory
        # платит ПОЛНЫМ пересбором зеркала: по замеру в шапке
        # sync_if_stale — 216 мс на 1000 фактов и 2.5 СЕКУНДЫ на
        # 10 000, посреди разговора, вслух. `note_fact` попутно
        # принимает новый отпечаток, поэтому расхождение исчезает,
        # а дата в индексе начинает совпадать с датой в JSON.
        _note_in_index(memory, memory_update)
    return memory


def should_extract_memory(user_text: str, jarvis_text: str, api_key: str) -> bool:
    """
    Stage 1: Quick YES/NO gate.

    Check order (cheapest → most expensive, no premature side effects):
      1. Time throttle — cheapest, no I/O
      2. Guard cooldown — no I/O, no side effects
      3. Daily API limit — has I/O side effect (increments counter), so last

    Key fix: when cooldown is active we push _last_memory_stage1 forward by the
    remaining cooldown duration.  This prevents an immediate retry the moment
    the cooldown window expires.
    """
    global _last_memory_stage1

    now = time.monotonic()

    # 0. Блок 10: отметить, что владелец сейчас говорил.
    # Стоит ПЕРЕД всеми отсечками нарочно: ниже стоят ограничитель частоты и
    # остывание квоты, и любая из них вернула бы False, а владелец при этом
    # говорил. Отметка нужна планировщику, чтобы не повторять напоминание тому,
    # кто уже отозвался; к разбору памяти она отношения не имеет.
    # Ни диска, ни сети — одно число в памяти процесса.
    #
    # ДЫРА, НАЗВАННАЯ ВСЛУХ: main.py:1768 зовёт разбор только для реплик
    # ДЛИННЕЕ 5 СИМВОЛОВ, поэтому «да» и «ок» сюда не доходят и повтор
    # прозвучит зря. Закрыть можно только в ui.py (там реплика пишется всегда),
    # но он тоже под контрольной суммой; владелец 22.08.2026 решил дыру принять.
    # Ошибка безопасная: услышать напоминание дважды лучше, чем потерять.
    try:
        from core import scheduler
        scheduler.note_owner_spoke()
    except Exception:
        pass

    # 1. Time throttle — skip if called too recently
    if now - _last_memory_stage1 < _MEMORY_STAGE1_MIN_INTERVAL:
        return False

    # 2. Guard cooldown — if Gemini is in quota cooldown, defer next check
    try:
        from core.aux_model import default_model
        from core.model_guard import get_guard
        guard = get_guard()
        aux_m = default_model()
        if not guard.is_available(aux_m):
            rem = guard.cooldown_remaining(aux_m)
            # Push next eligible check to: now + remaining cooldown + base interval
            # This guarantees we do not retry the instant cooldown ends.
            _last_memory_stage1 = now + rem
            print(f"[Memory] ⏳ Stage1 skipped — quota cooldown {rem:.0f}s remaining")
            return False
    except Exception:
        guard = None

    # 3. Суточный потолок. Считает его ТОЛЬКО core/metering (блок 5).
    #
    # Здесь стоял свой счётчик (_api_allowed, файл api_usage.json, потолок 50).
    # Он удалён, а не выключен (правило 9), и причина не в красоте: он считал
    # по МЕСТНОЙ дате, а квотные сутки у поставщика свои. Два счётчика с
    # разными сутками расходятся раз в день на несколько часов, и понять,
    # какой из них прав, невозможно. Плюс он не видел ни один другой вызов в
    # проекте, то есть отвечал на вопрос «сколько я потратил» неверно всегда.
    #
    # Сам отказ по потолку теперь приходит изнутри aux_call — здесь только
    # мягкий предварительный вопрос, чтобы не будить дверь напрасно.
    try:
        from core import metering
        left = metering.remaining("aux_light")
        if left.get("known") and left.get("left") == 0:
            _last_memory_stage1 = now + 300.0   # отойти на 5 минут
            print("[Memory] ℹ️  суточный потолок вызовов исчерпан — "
                  "извлечение памяти пропущено")
            return False
    except Exception:
        pass

    # Commit to making a Stage1 call — update throttle timestamp
    _last_memory_stage1 = now

    combined = (
        f"User: {user_text[:300]}\nJarvis: {jarvis_text[:200]}"
    )
    prompt = (
        "Does this conversation contain ANY of the following?\n"
        "- Personal facts (name, age, city, job, birthday, nationality)\n"
        "- Preferences or favorites (food, color, music, sport, game, film, book, etc.)\n"
        "- Active projects or goals the user is working on\n"
        "- People in the user's life (friends, family, partner, colleagues)\n"
        "- Things the user wants to do or buy in the future\n"
        "- Communication habits: preferred autonomy level, tolerance for questions, default apps\n"
        "- Any other fact worth remembering long-term\n\n"
        f"Reply only YES or NO.\n\nConversation:\n{combined}"
    )

    from core.aux_model import aux_call, aux_is_quota_error, aux_cooldown_seconds
    ok, text = aux_call(prompt, api_key, caller="Memory-Stage1")

    if not ok:
        if aux_is_quota_error(text):
            secs = aux_cooldown_seconds(text)
            _last_memory_stage1 = now + secs  # defer past cooldown end
            print(f"[Memory] 🚫 Stage1 hit quota — next check deferred {secs:.0f}s")
        else:
            print(f"[Memory] ⚠️ Stage1 check failed: {text}")
        return False

    return "YES" in text.upper()


def extract_memory(user_text: str, jarvis_text: str, api_key: str) -> dict:
    """
    Stage 2: Full fact extraction.
    Guard-protected via aux_call; always check availability before calling.
    """
    from core.aux_model import aux_call, aux_is_quota_error

    combined = f"User: {user_text[:500]}\nJarvis: {jarvis_text[:300]}"
    prompt = (
        "Extract ALL memorable personal facts from this conversation. Any language.\n"
        "Return ONLY valid JSON. Use {} if truly nothing is worth saving.\n\n"
        "Category guide:\n"
        "  identity      → name, age, birthday, city, country, job, school, nationality, language\n"
        "  preferences   → ANY favorite or preferred thing:\n"
        "                  favorite_food, favorite_color, favorite_music, favorite_film,\n"
        "                  favorite_game, favorite_sport, favorite_book, favorite_artist,\n"
        "                  favorite_country, hobbies, interests, dislikes, etc.\n"
        "  projects      → projects being built, ongoing work, goals, ideas in progress\n"
        "  relationships → people mentioned: friends, family, partner, colleagues\n"
        "  wishes        → future plans, things to buy, travel plans, dreams\n"
        "  communication_habits → how user prefers to interact: autonomy_level,\n"
        "                  question_tolerance, preferred_browser, typical_phrasing, etc.\n"
        "  notes         → anything else worth remembering (habits, schedule, etc.)\n\n"
        "IMPORTANT:\n"
        "- Be LIBERAL: if something MIGHT be worth remembering, include it.\n"
        "- Extract from BOTH user and Jarvis turns.\n"
        "- Skip: weather, reminders, search results, one-time commands.\n"
        "- Use concise English values regardless of conversation language.\n\n"
        'Format: {"identity":{"name":{"value":"Ali"}},'
        ' "preferences":{"favorite_color":{"value":"blue"}},'
        ' "projects":{"mark_xxv":{"value":"JARVIS-like AI assistant"}}}\n\n'
        f"Conversation:\n{combined}\n\nJSON:"
    )

    ok, text = aux_call(prompt, api_key, caller="Memory-Stage2")
    if not ok:
        if not aux_is_quota_error(text):
            print(f"[Memory] ⚠️ Stage2 extract failed: {text}")
        return {}

    import re
    text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    if not text or text == "{}":
        return {}

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


# How much of the prompt long-term memory is allowed to occupy. The old code
# rendered everything and then chopped the string at 2000 characters, which cut
# through the middle of whichever fact happened to be there - the model then
# read half a sentence as if it were the whole truth, and nothing said so.
# A budget that removes WHOLE facts and admits how many it removed is the only
# honest way to run out of room.
#
# ШАГ 6, 29.08.2026: 1200 -> 4000. Это НЕ "на всякий случай побольше", это
# исправление замеренной потери. Живой запуск владельца с JARVIS_DEBUG_PROMPT=1
# показал блок 1170 знаков при бюджете 1200 и хвост "3 more saved facts did not
# fit": до модели не доходили его заметки (notes=2) и хобби (hobbies=1) - целые
# секции выпадали, потому что рендерятся последними.
#
# Почему именно 4000, а не 2000 и не 8000 (замер probe23, на настоящих длинах
# фактов владельца - средняя 22 знака, а не выдуманные 60):
#
#     бюджет | фактов владельца доходит | доля системного промпта
#       1200 |            8             |  4.1%
#       2000 |           25             |  6.9%
#       4000 |           65             | 13.8%   <- выбрано
#       8000 |          146             | 27.5%
#
# Системный промпт уже 29064 знака и уходит каждую сессию, так что 4000 - это
# +9.7% к тому, что и так отправляется, за рост с 8 фактов до 65. Дальше цена
# растёт быстрее пользы: 8000 удваивает расход ради фактов, которые владелец
# в жизни не надиктует. Ограничения на ХРАНЕНИЕ при этом нет никакого - замер
# probe21: 3000 фактов лежат целыми, поиск среди них 24 мс. Бюджет ограничивает
# только то, что видно СРАЗУ, без вызова recall_memory.
PROMPT_CHAR_BUDGET = 4000

# Отдельный кошелёк для правил поведения. Замер probe20 показал дефект, который
# из одного общего бюджета не виден: 8 правил владельца занимали 751 знак из
# 1200, то есть 63%, и сколько бы фактов он ни надиктовал, до модели доходило
# ровно 9. Правила и факты о человеке платили из одного кармана, и правила его
# уже выпили.
#
# Теперь правила берут не больше своей доли, а остальное гарантированно
# достаётся фактам. Это НЕ освобождение правил от бюджета: если их станет
# слишком много, лишние всё равно выпадут - но выпадут ПРАВИЛА, а не рассказ
# владельца о себе. Обратное (правила съедают всё) как раз и было дефектом.
_RULES_BUDGET_SHARE = 0.35

# Behavioural rules are never dropped. A forgotten favourite colour is a small
# disappointment; a forgotten "warn me before you touch my files" is a broken
# promise, and the user has to notice the breach to find out it was lost.
_PINNED_SECTION_MARKERS = ("communication habits",)


def _split_prompt_sections(lines: list) -> tuple:
    """Turn rendered lines back into (identity head, [ [header, facts], ... ])."""
    head = []
    index = 0
    while index < len(lines) and lines[index] != "":
        head.append(lines[index])
        index += 1

    sections = []
    current = None
    while index < len(lines):
        line = lines[index]
        index += 1
        if line == "":
            continue
        if line.startswith("  - "):
            if current is not None:
                current[1].append(line)
        else:
            current = [line, []]
            sections.append(current)
    return head, sections


def _render_prompt_sections(head: list, sections: list) -> str:
    out = list(head)
    for header, facts in sections:
        if not facts:
            continue          # a heading with nothing under it is just noise
        out.append("")
        out.append(header)
        out.extend(facts)
    return "\n".join(out)


def _rules_over_share(sections: list, budget: int) -> int:
    """Насколько правила поведения вылезли за свою долю бюджета.

    ШАГ 6. Считаем ТОЛЬКО строки правил (без заголовка секции): именно они
    отнимали место у рассказа владельца о себе. Замер probe20: 8 правил = 751
    знак при бюджете 1200 = 63%, и обычных фактов проходило ровно 9, сколько бы
    их ни было.
    """
    share = int(budget * _RULES_BUDGET_SHARE)
    used = 0
    for title, facts in sections:
        if any(m in title.lower() for m in _PINNED_SECTION_MARKERS):
            used += sum(len(line) + 1 for line in facts)
    return max(0, used - share)


def _fit_prompt_to_budget(head: list, sections: list, header: str,
                          budget: int) -> int:
    """Remove whole facts until the block fits. Returns how many were removed."""
    droppable = [
        i for i, (title, _) in enumerate(sections)
        if not any(m in title.lower() for m in _PINNED_SECTION_MARKERS)
    ]
    pinned = [i for i in range(len(sections)) if i not in droppable]

    dropped = 0
    length = len(header) + len(_render_prompt_sections(head, sections))

    everything = list(range(len(sections)))

    def _pop_rule() -> bool:
        """Убрать одно ПРАВИЛО, когда правила залезли в чужую долю.

        Без этого закрепление правил работало как захват: они занимали 63%
        бюджета, и факты владельца упирались в потолок 9 штук независимо от
        того, сколько он рассказал. Здесь мы не отменяем приоритет правил -
        внутри своей доли они по-прежнему неприкосновенны и уходят последними
        (см. _pop_one). Мы лишь не даём им забрать больше своей доли.
        """
        nonlocal length
        for i in reversed(pinned):
            facts = sections[i][1]
            # Последнее правило не забираем: одно правило поведения должно
            # доходить всегда, иначе "предупреждай перед удалением файлов"
            # может исчезнуть целиком, а это обещание владельцу.
            if len(facts) <= 1:
                continue
            line = facts.pop()
            length -= len(line) + 1
            return True
        return False

    def _pop_one() -> bool:
        """Remove one whole fact, ordinary sections first, pinned only if forced.

        Pinning behaviour rules is a PREFERENCE, not an exemption. Measured on a
        1000-fact profile, treating it as an exemption produced a 9550-character
        block - eight times the budget - because a seventh of those facts were
        behaviour rules and nothing was allowed to touch them. An unbounded
        block is the very bug 3B.3 existed to kill, so the budget always wins in
        the end; pinned facts simply go last.
        """
        nonlocal length
        for order in (droppable, everything):
            for i in reversed(order):
                facts = sections[i][1]
                if not facts:
                    continue
                line = facts.pop()
                length -= len(line) + 1
                if not facts:
                    # the heading and its blank line leave with the last fact
                    length -= len(sections[i][0]) + 2
                return True
        return False        # nothing left to give up

    # ШАГ 6, ПЕРВЫМ ДЕЛОМ: если правила поведения залезли за свою долю, платят
    # они, а не рассказ владельца о себе. Порядок здесь и есть всё исправление:
    # раньше этого прохода не было, и первым всегда страдал владелец.
    while _rules_over_share(sections, budget) > 0:
        if not _pop_rule():
            break
        dropped += 1

    while length > budget:
        if not _pop_one():
            break
        dropped += 1

    # The running total is an estimate by construction, so settle it against
    # one real render. This normally costs zero extra iterations.
    while len(header) + len(_render_prompt_sections(head, sections)) > budget:
        if not _pop_one():
            break
        dropped += 1
    return dropped


def _without_junk(memory: dict) -> dict:
    """Drop recognised garbage before it reaches the prompt (Stage 3B.5).

    The index has known how to spot junk since 3B.1 ("soon", "updated,
    disregard previous") and hid it from search. The prompt did not: it was
    built straight from the JSON, so a fact the index considered worthless
    still went to the model every session. Two components disagreeing about
    what is worth knowing is worse than either rule alone.

    Junk is only hidden, never deleted - Invariant: automation does not get
    to decide which of the user's data is worthless.
    """
    try:
        from memory.fact_store import looks_like_junk
    except Exception:
        return memory

    cleaned = {}
    for category, entries in memory.items():
        if not isinstance(entries, dict):
            cleaned[category] = entries
            continue
        kept = {}
        for key, entry in entries.items():
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val is not None and looks_like_junk(val):
                continue
            kept[key] = entry
        cleaned[category] = kept
    return cleaned


# -- Срок годности фактов о событиях (расширение принципа 3B.5) ---------------
#
# ЧТО ЛЕЧИМ. Блок памяти собирался из JSON и на поле `updated` не смотрел
# никто. Поэтому «у меня болит голова» ехало в промпт вечно, и через две недели
# Джарвис спрашивал «как голова?» — не из заботы, а потому что для него это
# по-прежнему СЕГОДНЯ. Замер на настоящей памяти владельца (probe49/probe50):
# 4 просроченных факта из 18 — 22% памяти и 32% знаков блока.
#
# ЧЕМ ЭТО НЕ ЯВЛЯЕТСЯ. Это не удаление и не понижение достоверности. Инвариант
# дома — «junk is hidden, never deleted» — здесь ПРОДОЛЖЕН: строка исчезает
# только из текста, который уходит в модель. Запись на диске цела; поиск её
# находит, потому что промпт собирается из JSON, а поиск идёт по SQLite — две
# разные копии (проверено probe51). Достоверность (`confidence`) НЕ трогаем
# намеренно: junk-путь понижает её до 0.2, а поиск отсекает всё ниже 0.3, и
# тронув её здесь, мы превратили бы обещание «факт всё ещё находится» в ложь.
#
# ПОЧЕМУ СУДИМ ПО КЛЮЧУ, А НЕ ПО ЗНАЧЕНИЮ. Ключ — это то, что модель выбрала
# как ИМЯ факта: `headache_today` против `tigr_allergy`. Значения же structurally
# неразличимы (`likes coffee` и `works remotely` — одинаковые по форме), на этом
# ровно и провалилась предыдущая, уже одобренная попытка «ключ ≈ значение»
# (probe44-47): она давала ложные отказы на `works_remotely` и `vegetarian`.
#
# ТРИ СТРАХОВКИ ОТ ЛОЖНОГО СКРЫТИЯ, каждая добавлена по замеру:
#   1. Целые СЛОВА, не подстроки (probe55: подстроки давали 7 ложных скрытий —
#      `visit` находилось внутри `visitation_rights`).
#   2. Категории identity / preferences / projects / communication_habits не
#      проверяются вовсе: там нечему истекать.
#   3. Слова-роли (`job`, `plan`, `rule`, `habit`, `career`…) запрещают скрытие
#      даже при слове-маркере: `daily_plan_habit` — это привычка, а не событие.
# Итог замера (probe63): 0 ошибок на 40 реальных ключах, 0 исключений на 17
# видах ядовитых данных.
#
# ПОЧЕМУ FAIL-OPEN. Любая непонятная дата — отсутствующая, кривая, будущая, не
# строка — даёт None и НЕ скрывает. Ошибиться в сторону «показать лишнее»
# дешево (владелец слышит устаревшую деталь), ошибиться в сторону «спрятать»
# дороже. Тесты хранят факты вообще без дат, и это ровно тот случай.
_EXPIRY_WORD_MARKS = frozenset((
    "today", "yesterday", "tomorrow", "tonight", "event", "visit",
    "сегодня", "вчера", "завтра", "сейчас", "приезд", "звонил", "событие",
))

# Пары слов: по одному слову судить нельзя («last» есть в «last name»), а
# вместе они означают время. Без русских слов правило было слепо на половине
# реальных ключей владельца — замерено probe57.
_EXPIRY_WORD_PAIRS = (
    ("last", "contact"), ("right", "now"), ("just", "now"),
    ("this", "week"), ("this", "month"),
)

# Слово-роль сильнее слова-маркера: роль описывает, ЧЕМ факт является, а
# маркер — лишь когда о нём говорили.
_EXPIRY_ROLE_WORDS = frozenset((
    "job", "policy", "career", "plan", "planner", "rule", "habit", "name",
    "profession", "work", "role", "brand", "book", "fan", "goal", "business",
))

_EXPIRY_NEVER_CATEGORIES = frozenset((
    "identity", "preferences", "projects", "communication_habits",
))

# С какого дня показывать возраст и с какого скрывать совсем.
# Два дня без метки — «недавно»: если владелец вчера сказал про голову, а
# сегодня спрашивает про таблетку, оговорка была бы неуместна.
_EXPIRY_FRESH_DAYS = 2
_EXPIRY_STALE_DAYS = 14

_EXPIRY_WORD_SPLIT = re.compile(r"[^0-9a-zа-яё]+")

# Защита от ДВОЙНОЙ метки. Замерено (probe64): если функцию применить к уже
# помеченному результату, получится «headache today [5 дн. назад] [5 дн.
# назад]». Так и случится, если однажды кто-то позовёт срок годности дважды —
# например, из диагностики и из сборки промпта.
_EXPIRY_LABEL_TAIL = re.compile(r"\[\d+\s*дн\. назад\]\s*$")


def _expiry_words(key) -> list:
    return [w for w in _EXPIRY_WORD_SPLIT.split(str(key).lower()) if w]


def _is_event_key(category, key) -> bool:
    """Похоже ли, что это факт о СОБЫТИИ (а не о свойстве человека)."""
    if str(category).lower() in _EXPIRY_NEVER_CATEGORIES:
        return False
    words = _expiry_words(key)
    if not words:
        return False
    if any(w in _EXPIRY_ROLE_WORDS for w in words):
        return False
    if any(w in _EXPIRY_WORD_MARKS for w in words):
        return True
    return any(a in words and b in words for a, b in _EXPIRY_WORD_PAIRS)


def _fact_age_days(entry, today):
    """Возраст факта в днях или None, если понять нельзя. None = НЕ СКРЫВАТЬ.

    Замер probe52: 6 из 7 крайних случаев ломали наивный strptime. Здесь
    каждый из них честно приводит к None, кроме даты в будущем — она тоже
    None, потому что «минус три дня» означает сбитые часы, а не свежесть.
    """
    if not isinstance(entry, dict):
        return None
    raw = entry.get("updated")
    if not raw or not isinstance(raw, str):
        return None
    try:
        stamp = datetime.strptime(raw.strip()[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    try:
        age = (today - stamp).days
    except Exception:
        return None
    return None if age < 0 else age


def _expire_stale(memory: dict, *, today=None, days: int | None = None) -> tuple:
    """Убрать из блока промпта просроченные события. Возвращает (память, сколько).

    НИЧЕГО НЕ ПИШЕТ И НЕ ПОРТИТ ВХОД. Возвращаются новые словари, а запись с
    меткой возраста — копия. Замер probe64 показал, зачем это важно буквально:
    правка на месте дописывала метку в исходный объект, и при следующем
    сохранении «[5 дн. назад]» уехало бы на ДИСК, накапливаясь при каждом
    вызове.
    """
    if days is None:
        days = _EXPIRY_STALE_DAYS
    if today is None:
        today = datetime.now().date()

    out, hidden = {}, 0
    for category, entries in (memory or {}).items():
        if not isinstance(entries, dict):
            out[category] = entries
            continue
        kept = {}
        for key, entry in entries.items():
            if not _is_event_key(category, key):
                kept[key] = entry
                continue
            age = _fact_age_days(entry, today)
            if age is None:
                kept[key] = entry          # непонятно -> оставить (fail-open)
                continue
            if age > days:
                hidden += 1                # просрочено -> в промпт не едет
                continue
            if age > _EXPIRY_FRESH_DAYS and isinstance(entry, dict):
                value = str(entry.get("value") or "")
                if _EXPIRY_LABEL_TAIL.search(value):
                    kept[key] = entry      # метка уже стоит, второй не надо
                else:
                    marked = dict(entry)
                    marked["value"] = f"{value} [{age} дн. назад]"
                    kept[key] = marked
            else:
                kept[key] = entry
        out[category] = kept
    return out, hidden


def _visible_memory(memory: dict | None) -> tuple:
    """Что РЕАЛЬНО увидит модель: без мусора и без просроченных событий.

    Одна дверь для двух зовущих — сборки промпта и диагностики в main.py.
    Раньше диагностика повторяла фильтр своими руками, и любое расхождение
    превращало её в ложь: «In prompt: 18 facts» при 14 фактически ушедших.
    Отдельная функция делает такое расхождение невозможным по построению.

    Флаг читается ЛЕНИВО, и это замер, а не вкус: чтение настройки стоит
    0.065 мс, а вся сборка блока — 0.051 мс (probe62), то есть флаг дороже
    работы, которую он охраняет. Поэтому сначала смотрим, есть ли вообще
    просроченное, и только тогда спрашиваем настройку.
    """
    cleaned = _without_junk(memory or {})
    expired, hidden = _expire_stale(cleaned)
    if not hidden and expired == cleaned:
        return cleaned, 0
    try:
        from core.feature_flags import memory_expiry_enabled
        if not memory_expiry_enabled():
            return cleaned, 0
    except Exception:
        pass                                # нет настроек -> поведение по умолчанию
    return expired, hidden


def _expiry_note(expired: int) -> str:
    """Текст заметки о просроченных фактах. ОДНО место, два зовущих.

    ОТДЕЛЬНАЯ заметка, а не прибавка к «не влезло»: причины разные, и
    поведение модели обязано отличаться. «Не влезло» значит «спроси, если
    нужно». «Срок годности вышел» значит «не начинай разговор сам» — именно
    это и было болью, «как голова?» спустя месяц. И обязательно говорим,
    что факт НЕ удалён: иначе Джарвис уверенно скажет «не знаю» вместо
    того, чтобы посмотреть.

    Согласование в числе — не косметика: этот текст читает МОДЕЛЬ, и
    «1 facts are hidden» она может понять как «фактов несколько», а потом
    сослаться на то, чего нет.
    """
    if expired == 1:
        said, it, them = ("1 older saved fact about a passing event is",
                          "It is", "it")
    else:
        said, it, them = (
            f"{expired} older saved facts about passing events are",
            "They are", "them")
    return (f"\n\n({said} time-expired and hidden from this list. "
            f"{it} NOT deleted \u2014 do not bring {them} up on your own, "
            f"but call recall_memory if the person asks about {them}.)")


def format_memory_for_prompt(memory: dict | None) -> str:
    if not memory:
        return ""
    memory, _expired = _visible_memory(memory)

    header = "[WHAT YOU KNOW ABOUT THIS PERSON \u2014 use naturally, never recite like a list]\n"
    lines  = []

    identity  = memory.get("identity", {})
    id_fields = ["name", "age", "birthday", "city", "job", "language", "school", "nationality"]
    for field in id_fields:
        entry = identity.get(field)
        if entry:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"{field.title()}: {val}")
    for key, entry in identity.items():
        if key in id_fields:
            continue
        val = entry.get("value") if isinstance(entry, dict) else entry
        if val:
            lines.append(f"{key.replace('_', ' ').title()}: {val}")

    prefs = memory.get("preferences", {})
    if prefs:
        lines.append("")
        lines.append("Preferences:")
        for key, entry in list(prefs.items()):
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    projects = memory.get("projects", {})
    if projects:
        lines.append("")
        lines.append("Active Projects / Goals:")
        for key, entry in list(projects.items()):
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    rels = memory.get("relationships", {})
    if rels:
        lines.append("")
        lines.append("People in their life:")
        for key, entry in list(rels.items()):
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    wishes = memory.get("wishes", {})
    if wishes:
        lines.append("")
        lines.append("Wishes / Plans / Wants:")
        for key, entry in list(wishes.items()):
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    notes = memory.get("notes", {})
    if notes:
        lines.append("")
        lines.append("Other notes:")
        for key, entry in list(notes.items()):
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key}: {val}")

    habits = memory.get("communication_habits", {})
    if habits:
        lines.append("")
        lines.append("Communication Habits / Behavioural Defaults:")
        for key, entry in list(habits.items()):
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    # Any category we do not know about explicitly MUST still reach the prompt.
    # The model is free to invent categories (e.g. "hobbies"), and update_memory
    # happily stores them. Rendering only a fixed whitelist meant such facts were
    # saved to disk and then never spoken about again: the file was correct, but
    # Jarvis behaved as if it had never heard them. That is data loss on the READ
    # path, and it is exactly as damaging as losing the bytes.
    known = {
        "identity", "preferences", "projects", "relationships",
        "wishes", "notes", "communication_habits",
    }
    for cat, entries in memory.items():
        if cat in known or not isinstance(entries, dict) or not entries:
            continue
        cat_lines = []
        for key, entry in list(entries.items()):
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                cat_lines.append(f"  - {key.replace('_', ' ').title()}: {val}")
        if cat_lines:
            lines.append("")
            lines.append(f"{cat.replace('_', ' ').title()}:")
            lines.extend(cat_lines)

    if not lines:
        # ПУСТО ПОСЛЕ СРОКА ГОДНОСТИ — НЕ ТО ЖЕ, ЧТО ПУСТО ВСЕГДА.
        # Найдено тестом (test_the_prompt_says_the_fact_is_hidden...): если
        # ВСЯ память состояла из просроченных событий, ранний возврат ""
        # уносил и заметку. Джарвис не получал ни фактов, ни подсказки — и
        # уверенно отвечал «я о тебе ничего не знаю», хотя на диске лежало.
        # Ровно эта ложь и есть худший исход всей затеи, поэтому заметка
        # обязана дожить до промпта даже в одиночестве.
        if _expired:
            return header + _expiry_note(_expired) + "\n"
        return ""


    head, sections = _split_prompt_sections(lines)

    # ЗАМЕТКА О СРОКЕ ГОДНОСТИ ПЛАТИТ ИЗ БЮДЖЕТА, А НЕ СВЕРХ НЕГО.
    #
    # Замерено (probe73/probe76) уже ПОСЛЕ того, как правка легла: заметка
    # приписывалась к ГОТОВОМУ блоку, то есть после делёжки бюджета. На памяти
    # из 60 просроченных и 120 живых фактов блок вышел на 4343 знака против
    # 4000 — перебор 343, а сторож test_the_budget_still_wins_in_the_end терпит
    # +300. То есть моя же правка ломала бюджет, который в этом файле заводили
    # ровно против неограниченного блока. Дефект нашёлся замером, а не в бою.
    #
    # Лечится не смягчением сторожа, а вычетом: сколько фактов просрочено,
    # известно ДО делёжки (_visible_memory посчитал выше), поэтому длину
    # заметки можно вычесть из бюджета заранее. Новая возможность не имеет
    # права молча расширять расход на промпт — цена платится из своих.
    expired_note = _expiry_note(_expired) if _expired else ""

    budget  = max(0, PROMPT_CHAR_BUDGET - len(expired_note))
    dropped = _fit_prompt_to_budget(head, sections, header, budget)

    result = header + _render_prompt_sections(head, sections) + expired_note
    if dropped:
        # Saying the number out loud matters: without it, Jarvis cannot tell the
        # difference between "nothing was ever saved" and "it did not fit", and
        # would answer "I don't know that" with complete confidence.
        result += (
            f"\n\n({dropped} more saved facts did not fit here. They are NOT "
            "forgotten — call recall_memory to look any of them up before "
            "saying you do not know something.)"
        )

    return result + "\n"


def remember(key: str, value: str, category: str = "notes") -> str:
    valid = {"identity", "preferences", "projects", "relationships", "wishes", "notes", "communication_habits"}
    if category not in valid:
        category = "notes"
    update_memory({category: {key: {"value": value}}})
    return f"Remembered: {category}/{key} = {value}"


def forget(key: str, category: str | None = None) -> str:
    """Delete a fact from memory. HONEST by construction.

    The model routinely guesses the wrong category (it saved a fact under
    "habits" but asks to forget it from "notes"). A forget that only checked the
    one named category would silently miss and report success on nothing --
    which is how Jarvis ended up telling the user "deleted" while the stale fact
    lived on. So: try the named category first, then fall back to scanning every
    category for the key. The return string reflects what ACTUALLY happened, so
    the caller can tell the user the truth instead of a hopeful guess.
    """
    # Блок 9: поиск и удаление — ОДНО действие под замком. Раньше между
    # чтением и записью мог влезть фоновый извлекатель, и «забудь» либо
    # возвращало забытый факт назад, либо теряло чужой свежий.
    removed = []

    # ЗАМЕРЕННЫЙ ПРОМАХ, живой лог 30.08.2026. Владелец поправил имя кота,
    # и модель позвала стирание ТРИ раза подряд, читая имена из блока
    # промпта: 'Cat Name' -> Not found, 'Tigr Allergy' -> Not found,
    # 'cat_behavior' -> Forgotten. Третий сработал ровно потому, что был
    # написан в снейк-кейсе. Дом сам печатает «Cat Name» человеку и сам же
    # такое имя не принимал — промах был НАШ, не модели.
    #
    # ПОЧЕМУ ЭТО ХУЖЕ НЕУДОБСТВА. «Not found» здесь — ЛОЖНОЕ отрицание:
    # факт есть, но стирание его не видит. А следующий save с тем же
    # написанием создаёт ВТОРОЙ ключ, и в промпте оказываются два
    # противоречащих факта: «Cat Name: Cat named Tigr» и «Cat Name: Lev».
    # Честность ответа при этом не страдает: настоящее «Not found» на
    # несуществующем ключе по-прежнему возвращается (сторож на месте).
    wanted = _canonical_key(key)

    def change(memory: dict) -> dict:
        def _hit(entries: dict):
            """Найти имя ключа в категории с учётом написания.

            Точное совпадение имеет ПРИОРИТЕТ: если владелец завёл ключ с
            причудливым именем и просит именно его, мы не имеем права
            стереть похожий вместо названного.
            """
            if key in entries:
                return key
            for existing in entries:
                if _canonical_key(existing) == wanted:
                    return existing
            return None

        # 1. Try the category the caller named, if any.
        candidates = []
        if category and isinstance(memory.get(category), dict):
            found = _hit(memory[category])
            if found is not None:
                candidates.append((category, found))
        # 2. Fall back to any category that actually holds the key.
        if not candidates:
            for cat_name, entries in memory.items():
                if isinstance(entries, dict):
                    found = _hit(entries)
                    if found is not None:
                        candidates.append((cat_name, found))
        for cat_name, fact_key in candidates:
            del memory[cat_name][fact_key]
            removed.append(f"{cat_name}/{fact_key}")
        return memory

    try:
        safe_update(_memory_path(), change, _empty_memory, label="Memory")
    except Exception as exc:
        print(f"[Memory] ⚠️ Не забыл, прежняя копия цела: {exc}")
        return f"Could not forget {key}: {exc}"

    if not removed:
        return f"Not found: {key}"

    # Stage 3B.5: forget from the index in the same breath. Otherwise the fact
    # stays searchable, recall_memory keeps handing it back, and "forgotten"
    # becomes a lie told with a straight face.
    try:
        from memory.fact_store import note_fact
        for entry in removed:
            cat_name, _, fact_key = entry.partition("/")
            note_fact(cat_name, fact_key, "", deleted=True)
    except Exception as exc:
        print(f"[Memory] index delete skipped (non-fatal): {exc}")

    return "Forgotten: " + ", ".join(removed)

# Alias — eski import'larla uyumluluk için
forget_memory = forget