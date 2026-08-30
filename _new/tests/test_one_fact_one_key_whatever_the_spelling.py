"""ОДИН ФАКТ — ОДИН КЛЮЧ, КАК БЫ ЕГО НИ НАПИСАЛИ.

ЖИВОЙ ЗАМЕР, 30.08.2026. Владелец сказал: «кстати моего кота зовут лев а
не тигр». Правило про оговорку сработало — модель ПОШЛА стирать. Но в его
журнале три вызова подряд:
    forget_memory {'key': 'Cat Name'}      -> Not found: Cat Name
    forget_memory {'key': 'Tigr Allergy'}  -> Not found: Tigr Allergy
    forget_memory {'key': 'cat_behavior'}  -> Forgotten: notes/cat_behavior
    save_memory   {'key': 'Cat Name', 'value': 'Lev'}
Третий сработал ровно потому, что был написан в снейк-кейсе.

ПРОМАХ БЫЛ НАШ, НЕ МОДЕЛИ. Блок памяти печатается ЧЕЛОВЕКУ, и мы сами
превращаем `cat_name` в «Cat Name: Cat named Tigr» ради читаемости. Модель
взяла имя оттуда — единственного места, где она их видит, — и написала
дословно. Дом предлагал написание и сам же его не принимал.

ЧТО ЭТО СТОИЛО. Не неудобство, а ПОТЕРЯ ДАННЫХ:
  * «Not found» — ложное отрицание: факт есть, стирание его не видит;
  * следующий save создал ВТОРОЙ ключ рядом с первым, и в промпт поехали
    две строки: «Cat Name: Cat named Tigr» и «Cat Name: Lev».
То есть Джарвис одновременно знал два имени одного кота и не мог
предпочесть верное — ровно то, от чего чинилась оговорка.

ГДЕ ЛЕЧИТСЯ. Одно место входа в память (`_canonical_key`), два зовущих:
запись (`_recursive_update`) и стирание (`forget`). Лечить в промпте
нельзя: тогда модель обязана угадывать обратное преобразование, а она уже
угадала неверно.

ЧЕГО ЗДЕСЬ НАМЕРЕННО НЕ ПРОВЕРЯЕТСЯ, потому что этого и не делается:
синонимы, переводы, склейка cat/kitten. Свести два факта владельца в один
по смыслу — значит позволить автоматике решать, какие его сведения
лишние. Приводится только НАПИСАНИЕ: регистр, пробелы, дефисы.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def mm(tmp_path, monkeypatch):
    """Свежий модуль памяти на свой каталог состояния."""
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    import memory.memory_manager as module
    importlib.reload(module)
    return module


# ── 1. САМО ПРИВЕДЕНИЕ ───────────────────────────────────────────────────

def test_the_prompt_label_becomes_the_stored_key(mm):
    """«Cat Name» -> `cat_name`. Ровно то, что напечатано в блоке.

    Это обратное преобразование к нашей же строке в промпте:
    `key.replace('_', ' ').title()`. Если оно разойдётся, дефект вернётся.
    """
    assert mm._canonical_key("Cat Name") == "cat_name"
    assert mm._canonical_key("Favorite Cars") == "favorite_cars"
    assert mm._canonical_key("Tigr Allergy") == "tigr_allergy"


def test_a_correct_key_is_left_alone(mm):
    """Уже правильное имя не должно меняться — иначе поедут все ключи."""
    for key in ("cat_name", "favorite_color", "work_arrangement", "x"):
        assert mm._canonical_key(key) == key


def test_spacing_dashes_and_case_are_all_folded(mm):
    """Три способа написать одно имя сходятся в одно."""
    for variant in ("Cat  Name", " cat name ", "CAT-NAME", "Cat_Name"):
        assert mm._canonical_key(variant) == "cat_name", variant


def test_garbage_does_not_crash_the_house(mm):
    """Модель присылает что угодно. Падать на входе в память нельзя."""
    assert mm._canonical_key(None) is None
    assert mm._canonical_key(42) == 42
    # Пустое имя возвращается как есть: решение «пропустить» принимает
    # вызывающий, а не преобразователь.
    assert mm._canonical_key("   ") == "   "


# ── 2. ЗАПИСЬ: ВТОРОЙ КЛЮЧ БОЛЬШЕ НЕ ПОЯВЛЯЕТСЯ ─────────────────────────

def test_the_owners_duplicate_cannot_happen_again(mm):
    """Сквозная проверка его случая: было два ключа, теперь один.

    Именно этот тест падает на старом коде — он и есть доказательство,
    что дефект был и что он закрыт.
    """
    mm.update_memory({"relationships": {"cat_name": {"value": "Cat named Tigr"}}})
    mm.update_memory({"relationships": {"Cat Name": {"value": "Lev"}}})

    entries = mm.load_memory()["relationships"]
    assert list(entries) == ["cat_name"], entries
    assert entries["cat_name"]["value"] == "Lev", "поправка обязана заменить"


def test_the_prompt_shows_one_cat_not_two(mm):
    """Главное для владельца: в блоке ОДНА строка про кота.

    Он видел две подряд. Проверяем то, что видит он, а не только словарь.
    """
    mm.update_memory({"relationships": {"cat_name": {"value": "Cat named Tigr"}}})
    mm.update_memory({"relationships": {"Cat Name": {"value": "Lev"}}})

    prompt = mm.format_memory_for_prompt(mm.load_memory())
    assert prompt.count("Cat Name:") == 1, prompt
    assert "Lev" in prompt
    assert "Tigr" not in prompt


def test_leading_and_trailing_spaces_do_not_make_a_new_key(mm):
    """Замерено до правки: '  Friend Alex  ' сохранялся ДОСЛОВНО.

    Такой ключ невидим для стирания и печатается в промпт с рваными
    пробелами.
    """
    mm.update_memory({"relationships": {"  Friend Alex  ": {"value": "friend Alex"}}})
    assert list(mm.load_memory()["relationships"]) == ["friend_alex"]


def test_other_facts_are_untouched_by_the_change(mm):
    """Приведение имён не имеет права задеть чужие записи."""
    mm.update_memory({
        "preferences": {"favorite_color": {"value": "green"}},
        "relationships": {"cat_name": {"value": "Cat named Tigr"}},
    })
    mm.update_memory({"relationships": {"Cat Name": {"value": "Lev"}}})

    memory = mm.load_memory()
    assert memory["preferences"]["favorite_color"]["value"] == "green"


def test_the_value_itself_is_never_touched(mm):
    """Приводится ИМЯ ключа, а не значение факта.

    Значение — слова владельца. Автоматика их не правит.
    """
    mm.update_memory({"identity": {"City": {"value": "Khimki",
                                            "said": "я в Химках живу"}}})
    entry = mm.load_memory()["identity"]["city"]
    assert entry["value"] == "Khimki"
    assert entry["said"] == "я в Химках живу"


# ── 3. СТИРАНИЕ: «NOT FOUND» БОЛЬШЕ НЕ ЛЖЁТ ─────────────────────────────

def test_forgetting_by_the_prompt_label_now_works(mm):
    """Ровно первый вызов из его лога: forget('Cat Name') -> Forgotten.

    В журнале было «Not found: Cat Name» при живом факте на диске.
    """
    mm.update_memory({"relationships": {"cat_name": {"value": "Cat named Tigr"}}})
    result = mm.forget("Cat Name", "relationships")
    assert result.startswith("Forgotten:"), result
    assert "relationships/cat_name" in result
    assert "cat_name" not in mm.load_memory()["relationships"]


def test_forgetting_the_second_missed_key_works_too(mm):
    """Второй вызов из его лога: 'Tigr Allergy'."""
    mm.update_memory({"relationships": {"tigr_allergy": {"value": "chicken allergy"}}})
    assert mm.forget("Tigr Allergy", "relationships").startswith("Forgotten:")


def test_forgetting_still_works_with_the_wrong_category(mm):
    """Прежнее умение цело: категорию модель угадывает плохо.

    Проверяется вместе с новым написанием — две поблажки не должны
    отменять друг друга.
    """
    mm.update_memory({"habits": {"work_schedule": {"value": "works at night"}}})
    result = mm.forget("Work Schedule", "notes")     # и имя, и категория мимо
    assert result.startswith("Forgotten:"), result
    assert "habits/work_schedule" in result


def test_an_honest_not_found_is_still_honest(mm):
    """САМОЕ ВАЖНОЕ ЗДЕСЬ. Поблажка не имеет права стать ложным успехом.

    Весь смысл `forget` — сообщать правду о том, что случилось. Если
    приведение имён начнёт находить «похожее», Джарвис снова начнёт
    говорить «забыл» о том, чего не стирал, — тот самый дефект, из-за
    которого forget_memory и появился.
    """
    mm.update_memory({"preferences": {"favorite_color": {"value": "green"}}})
    result = mm.forget("Sister Name", "relationships")
    assert result.startswith("Not found"), result
    assert "green" in mm.format_memory_for_prompt(mm.load_memory())


def test_a_similar_but_different_key_is_not_deleted(mm):
    """`cat_name` и `cat_names` — РАЗНЫЕ факты, и оба обязаны выжить.

    Граница поблажки: только написание, никакой похожести.
    """
    mm.update_memory({"relationships": {"cat_name": {"value": "Lev"},
                                        "cat_names": {"value": "Lev and Tigr"}}})
    mm.forget("Cat Name", "relationships")
    left = mm.load_memory()["relationships"]
    assert "cat_names" in left, "похожий ключ не должен пострадать"
    assert "cat_name" not in left


def test_an_exact_match_wins_over_a_folded_one(mm):
    """Если владелец завёл причудливое имя и просит ИМЕННО его — стираем его.

    Иначе поблажка начнёт решать за владельца, какой из его ключей он
    имел в виду.
    """
    # ЗАМЕР 30.08.2026: `load_memory()` файла НЕ создаёт (проверено —
    # `path.exists()` False сразу после вызова), он лишь читает и при
    # отсутствии отдаёт заготовку. Файл появляется только после записи,
    # поэтому заготовку создаём через `update_memory`.
    import json
    from core.safe_json import state_path
    mm.update_memory({"relationships": {"seed": "чтобы файл появился"}})
    path = state_path("long_term.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    # Пишем оба написания НАПРЯМУЮ: через update_memory это уже невозможно,
    # но старые файлы владельца такие пары содержать могут.
    data["relationships"] = {"cat_name": {"value": "Lev"},
                             "Cat Name": {"value": "Tigr"}}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    result = mm.forget("Cat Name", "relationships")
    assert "relationships/Cat Name" in result, result
    left = mm.load_memory()["relationships"]
    assert "cat_name" in left, "точное совпадение не должно задеть канон"


def test_old_files_with_a_stray_key_can_be_cleaned(mm):
    """Уже испорченная память владельца лечится стиранием.

    У него на диске сейчас может лежать пара `cat_name` + `Cat Name`.
    Правка не переписывает его файл сама (автоматика не правит данные
    молча), но стирание обязано до такого ключа доставать.
    """
    import json
    from core.safe_json import state_path
    mm.update_memory({"relationships": {"seed": "чтобы файл появился"}})
    path = state_path("long_term.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["relationships"] = {"Cat Name": {"value": "Cat named Tigr"}}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    assert mm.forget("cat_name", "relationships").startswith("Forgotten:")
    assert not mm.load_memory()["relationships"]


# ── 4. УСТРОЙСТВО: ОДНА ДВЕРЬ, А НЕ ДВЕ КОПИИ ПРАВИЛА ───────────────────

def test_both_callers_use_the_single_door(mm):
    """Запись и стирание зовут ОДНУ функцию, а не по своей копии.

    Две копии правила разошлись бы, и тогда сохранённое стало бы
    неудаляемым — худший из возможных исходов для памяти.
    """
    src = (mm.__file__).replace(".pyc", ".py")
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    assert text.count("def _canonical_key(") == 1
    # обе точки подключения на месте:
    assert "key = _canonical_key(key)" in text
    assert "wanted = _canonical_key(key)" in text


def test_the_expiry_feature_still_sees_the_keys(mm):
    """Срок годности узнаёт события по СЛОВАМ в ключе.

    Если приведение сломает написание, `_is_event_key` ослепнет и прежняя
    правка тихо перестанет работать. Проверяется вместе, а не по
    отдельности: именно на стыке двух правок и появляются такие потери.
    """
    # ПОРЯДОК ДОВОДОВ ВАЖЕН: подпись `_is_event_key(category, key)` —
    # СНАЧАЛА категория. Первая редакция этого сторожа звала наоборот и
    # падала на исправном коде; ошибка была в стороже, не в доме.
    assert mm._is_event_key("notes", mm._canonical_key("Headache Today"))
    assert mm._is_event_key("notes", mm._canonical_key("Sister Visit"))
    # Имя кота — свойство, не событие: срок годности его не касается.
    assert not mm._is_event_key("relationships", mm._canonical_key("Cat Name"))


# ── 5. УЖЕ ИСПОРЧЕННАЯ ПАМЯТЬ ВЛАДЕЛЬЦА ЛЕЧИТСЯ ДО КОНЦА ────────────────

def test_the_owners_existing_pair_is_fully_cleanable(mm):
    """Правка не переписывает диск владельца молча — значит, пара уже лежит.

    ЗАЧЕМ ЭТОТ СТОРОЖ ОТДЕЛЬНО. Остальные проверяют, что дубль больше НЕ
    ПОЯВИТСЯ. Но у владельца он УЖЕ есть: по его журналу на диске рядом
    стоят `cat_name` (Tigr) и `Cat Name` (Lev). Замер 30.08.2026 вскрыл
    неочевидное: одно «забудь» стирает ключ с ТОЧНЫМ написанием, то есть
    как раз `Cat Name` со ВЕРНЫМ значением «Lev», а устаревший `cat_name`
    с «Tigr» остаётся жить и уезжать в промпт. Так задумано — точное
    совпадение имеет приоритет, чтобы поблажка не решала за владельца, —
    но это значит, что чистка требует ДВУХ стираний, а не одного.

    Сторож закрепляет именно этот порядок, чтобы рецепт, выданный
    владельцу, не разошёлся с поведением дома после будущих правок.
    """
    import json
    from core.safe_json import state_path
    mm.update_memory({"relationships": {"seed": "чтобы файл появился"}})
    path = state_path("long_term.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["relationships"] = {
        "cat_name": {"value": "Cat named Tigr", "updated": "2026-08-25"},
        "Cat Name": {"value": "Lev", "updated": "2026-08-30"},
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # Первое стирание забирает ТОЧНОЕ написание.
    assert mm.forget("Cat Name", "relationships") == "Forgotten: relationships/Cat Name"
    assert "cat_name" in mm.load_memory()["relationships"], (
        "устаревший ключ обязан остаться: иначе поблажка решила за владельца"
    )
    # Второе — добирает оставшийся, уже через поблажку.
    assert mm.forget("cat name", "relationships") == "Forgotten: relationships/cat_name"
    left = {k: v for k, v in mm.load_memory()["relationships"].items() if k != "seed"}
    assert left == {}, left

    # И только теперь запись даёт РОВНО одну строку в промпт.
    mm.update_memory({"relationships": {"cat_name": "Lev"}})
    block = mm.format_memory_for_prompt(mm.load_memory())
    assert block.count("Cat Name:") == 1, block
    assert "Tigr" not in block


# ── 6. УЖЕ ЛЕЖАЩИЙ ДВОЙНИК ПОДБИРАЕТСЯ ПРИ ЗАПИСИ ───────────────────────

def test_a_stale_spelling_is_picked_up_when_the_fact_is_rewritten(mm):
    """Второй живой прогон владельца: правки выше НЕ ХВАТИЛО.

    ЗАМЕР 30.08.2026, ВТОРОЙ РАЗГОВОР. Приведение имени закрыло появление
    НОВЫХ двойников, но на диске владельца остался старый `Cat Name` от
    прошлой поломки. Он сказал «кота зовут лев но теперь буду звать его
    алексик», модель отработала БЕЗУПРЕЧНО:
        forget_memory {'key':'cat_name'}                  -> Forgotten
        save_memory   {'key':'cat_name','value':'Alexic'}  -> Saved
    А в промпт снова уехали две строки — «Cat Name: Lev» и
    «Cat Name: Alexic», потому что стирала и писала она в `cat_name`, а
    рядом жил `Cat Name`, до которого никто не дотягивался.

    Сторож держит именно эту последовательность: тот, кто её сломает,
    вернёт владельцу вопрос «а как всё-таки зовут кота».
    """
    import json
    from core.safe_json import state_path
    mm.update_memory({"relationships": {"seed": "чтобы файл появился"}})
    path = state_path("long_term.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["relationships"] = {
        "cat_name": {"value": "Cat named Tigr", "updated": "2026-08-25"},
        "Cat Name": {"value": "Lev", "updated": "2026-08-30"},
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    mm.forget("cat_name", "relationships")            # как в журнале
    mm.update_memory({"relationships": {"cat_name": "Alexic"}})

    left = {k: v for k, v in mm.load_memory()["relationships"].items()
            if k != "seed"}
    assert list(left) == ["cat_name"], left
    assert left["cat_name"]["value"] == "Alexic"
    block = mm.format_memory_for_prompt(mm.load_memory())
    assert block.count("Cat Name:") == 1, block
    assert "Lev" not in block, "прежнее имя не должно остаться в промпте"


def test_picking_up_a_stale_spelling_keeps_the_owners_own_words(mm):
    """Перенос, а не выброс: поле `said` обязано уехать вместе с фактом.

    В `said` лежит исходная фраза владельца, и именно по ней потом ищет
    поиск. Если бы старая запись просто удалялась, переименование ключа
    молча стирало бы его собственные слова — потеря, которую он заметил
    бы только через месяц, когда поиск перестал бы находить факт.
    """
    import json
    from core.safe_json import state_path
    mm.update_memory({"relationships": {"seed": "чтобы файл появился"}})
    path = state_path("long_term.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["relationships"] = {
        "Cat Name": {"value": "Lev", "updated": "2026-08-20",
                     "said": "кота зовут лев"},
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    mm.update_memory({"relationships": {"cat_name": "Lev"}})   # без said
    entry = mm.load_memory()["relationships"]["cat_name"]
    assert entry["said"] == "кота зовут лев", entry


def test_picking_up_does_not_reach_a_similar_but_different_key(mm):
    """Подбор берёт ТОЛЬКО тот же факт, не соседний похожий.

    `cat_names` («два кота») — другой факт, чем `cat_name`. Если подбор
    станет жаднее, запись одного факта начнёт молча съедать другой, и это
    будет хуже исходного дефекта: дубль владелец видит, а тихую пропажу нет.
    """
    mm.update_memory({"relationships": {"cat_names": "два кота"}})
    mm.update_memory({"relationships": {"cat_name": "Lev"}})
    left = mm.load_memory()["relationships"]
    assert "cat_names" in left, left
    assert "cat_name" in left, left


# --------------------------------------------------------------------------
# РАЗДЕЛ 7. УРОВЕНЬ КАТЕГОРИЙ — НАВЕРХУ СЛИВАЕМ, А НЕ ВЫБРАСЫВАЕМ.
#
# ЭТОТ РАЗДЕЛ ПОЯВИЛСЯ ПОТОМУ, ЧТО ПРАВКА e92266f ВНЕСЛА ДЕФЕКТ ХУЖЕ ТОГО,
# который чинила, и ни один из 22 сторожей выше его не заметил. Все они
# подавали функции ИМЯ ФАКТА в чужом написании. Ни один не подал КАТЕГОРИЮ.
# Проверялось только то место, где правка ожидалась работать.
#
# ЗАМЕР 30.08.2026, версия против версии:
#     до e92266f:    ФАКТ ЖИВ: True
#     после e92266f: ФАКТ ЖИВ: False
# Один вызов update_memory({'notes': ...}) при наличии на диске `Notes`
# оставлял от двух категорий одну, со всем содержимым второй, молча —
# без строки в журнале. И это не безобидно: замерено, что факты из
# категории в чужом написании ВИДНЫ и в блоке промпта, и в поиске recall.
#
# `_recursive_update` рекурсивна, и уровни в ней значат разное: внизу
# ключи — имена фактов, наверху — категории. Два написания одного ФАКТА —
# противоречие, верно ровно одно, выбирает владелец последней фразой.
# Два написания одной КАТЕГОРИИ — две полки с разными фактами, и терять
# содержимое ни одной нельзя.
# --------------------------------------------------------------------------


def _seed_categories(mm, extra: dict):
    """Положить на диск категории в чужом написании, минуя update_memory.

    Иначе их не создать: канонизация на входе как раз и не даст записать
    `Notes`. А на диске владельца такие полки уже лежат от прошлых поломок.
    """
    import json
    from core.safe_json import state_path
    mm.update_memory({"notes": {"seed": "чтобы файл появился"}})
    path = state_path("long_term.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(extra)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_a_category_in_a_foreign_spelling_does_not_lose_its_facts(mm):
    """РОВНО ТОТ СЛУЧАЙ, КОТОРЫЙ Я СЛОМАЛ. Запись в `notes` при наличии
    `Notes` не имеет права уничтожить содержимое `Notes`."""
    _seed_categories(mm, {"Notes": {
        "important_thing": {"value": "ВАЖНЫЙ ФАКТ", "updated": "2026-08-29"},
    }})
    mm.update_memory({"notes": {"new_fact": "что-то"}})
    left = mm.load_memory()
    assert "Notes" not in left, "чужое написание должно исчезнуть как ключ"
    assert "important_thing" in left["notes"], left["notes"]
    assert left["notes"]["important_thing"]["value"] == "ВАЖНЫЙ ФАКТ"


def test_three_spellings_of_one_category_all_fold_into_one(mm):
    """`notes` + `Notes` + `NOTES` — одна полка, и ни один факт не пропал."""
    _seed_categories(mm, {
        "Notes": {"b": {"value": "BBB", "updated": "2026-08-29"}},
        "NOTES": {"c": {"value": "CCC", "updated": "2026-08-29"}},
    })
    mm.update_memory({"notes": {"e": "EEE"}})
    left = mm.load_memory()
    assert "Notes" not in left and "NOTES" not in left, list(left)
    for fact in ("seed", "b", "c", "e"):
        assert fact in left["notes"], (fact, left["notes"])


def test_on_a_collision_the_canonical_fact_survives(mm):
    """Если один и тот же факт есть на обеих полках — выживает канонический.

    Он либо новее, либо его прямо сейчас перезаписывает владелец. Иначе
    свежее значение молча откатилось бы к старому.
    """
    mm.update_memory({"notes": {"a": "СВЕЖЕЕ"}})
    _seed_categories(mm, {"Notes": {
        "a": {"value": "СТАРОЕ", "updated": "2026-01-01"},
        "b": {"value": "BBB", "updated": "2026-01-01"},
    }})
    mm.update_memory({"notes": {"z": "неважно"}})
    left = mm.load_memory()["notes"]
    assert left["a"]["value"] == "СВЕЖЕЕ", left["a"]
    assert left["b"]["value"] == "BBB", "чужой факт всё равно должен переехать"


def test_folding_categories_does_not_touch_a_different_category(mm):
    """`My Notes` — ДРУГАЯ категория, а не написание `notes`. Не трогать."""
    _seed_categories(mm, {"My Notes": {
        "d": {"value": "DDD", "updated": "2026-08-29"},
    }})
    mm.update_memory({"notes": {"e": "EEE"}})
    left = mm.load_memory()
    assert "My Notes" in left, list(left)
    assert left["My Notes"]["d"]["value"] == "DDD"


def test_the_fact_level_still_overwrites_and_does_not_merge(mm):
    """АСИММЕТРИЯ НАМЕРЕННАЯ. Наверху слияние, внизу — перезапись.

    Если слияние протечёт на уровень фактов, вернётся исходный дефект:
    два имени одного кота в промпте. Этот сторож держит границу.
    """
    import json
    from core.safe_json import state_path
    mm.update_memory({"relationships": {"seed": "x"}})
    path = state_path("long_term.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["relationships"]["Cat Name"] = {
        "value": "Lev", "updated": "2026-08-20",
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    mm.update_memory({"relationships": {"cat_name": "Alexic"}})
    left = mm.load_memory()["relationships"]
    assert "Cat Name" not in left, left
    assert left["cat_name"]["value"] == "Alexic", left["cat_name"]
    block = mm.format_memory_for_prompt(mm.load_memory())
    assert block.count("Cat Name:") == 1, block
    assert "Lev" not in block, block


def test_the_code_tells_categories_from_facts_by_value_type(mm):
    """Различение уровней обязано остаться в коде, и одним условием.

    Глубину рекурсии пришлось бы протаскивать лишним доводом через все
    вызовы, включая один из фонового потока. Тип значения уже проверяется
    тем же условием, по которому функция решает — спускаться или писать.
    """
    from pathlib import Path
    text = Path(mm.__file__).read_text(encoding="utf-8")
    assert "_is_container" in text, "различение уровней пропало"
    assert text.count("_is_container = isinstance(value, dict)") == 1, \
        "решение об уровне должно приниматься в одном месте"
