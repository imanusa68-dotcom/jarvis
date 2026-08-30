# tests/test_state_desk.py
"""
Сторожа кассы состояния (фаза 1, блок 9, шаг 19).

Правило этих тестов: проверяем ПОВЕДЕНИЕ на настоящих потоках и настоящем
убитом процессе. Атомарность одной записи здесь НЕ проверяется — она была
сделана в Stage 3.0 и покрыта tests/test_stage30_durable_state.py. Здесь то,
чего атомарность НЕ ДАЁТ: сохранность правки и работа под одновременной
нагрузкой.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
import tokenize
from pathlib import Path

import pytest

from core import safe_json as sj

ROOT = Path(__file__).resolve().parent.parent

# Файл покрупнее: чем дольше идёт запись, тем шире окно наложения. На 241 КБ
# отказы ловились в 16% случаев, на двух килобайтах — почти никогда.
BIG = {"facts": {f"k{i}": "з" * 300 for i in range(400)}}


@pytest.fixture()
def home(tmp_path, monkeypatch):
    spot = tmp_path / "дом"
    spot.mkdir()
    monkeypatch.setenv(sj.STATE_DIR_ENV, str(spot))
    sj.reset_for_tests()
    return spot


# -- Главное: сохранность ПРАВКИ, а не файла ------------------------------

def test_two_threads_editing_the_same_file_lose_nothing(home):
    """ГЛАВНЫЙ СТОРОЖ БЛОКА, и он не про атомарность.

    Атомарная запись бережёт ФАЙЛ от разрыва. Она не бережёт ПРАВКУ: память
    правится «прочитать всё -> добавить свой факт -> записать всё обратно», и
    два таких потока затирают работу друг друга.

    Замерено 21.08.2026 на копии настоящей памяти владельца: голосовое
    «запомни» и фоновый извлекатель одновременно -> ФАКТ ВЛАДЕЛЬЦА ИСЧЕЗ, а
    Джарвис напечатал «Saved», то есть соврал. Файл при этом был целый и
    читался — просто в нём было не то.
    """
    notes = home / "notes.json"
    sj.atomic_write_json(notes, {})
    rounds = 60
    errors = []

    def adder(name):
        for i in range(rounds):
            try:
                sj.update(notes,
                          lambda d, k=f"{name}-{i}": d.__setitem__(k, "есть"))
            except Exception as exc:                     # noqa: BLE001
                errors.append(f"{name}: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=adder, args=(n,)) for n in ("А", "Б")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not [t for t in threads if t.is_alive()], "правка встала намертво"
    assert not errors, errors[:3]

    final = json.loads(notes.read_text(encoding="utf-8"))
    want = rounds * 2
    assert len(final) == want, (
        f"записано {len(final)} правок из {want} — "
        f"{want - len(final)} потеряно молча")


def test_three_threads_writing_at_once_never_get_refused(home):
    """ЗАМЕР 21.08.2026: на Windows одновременная запись ОТКАЗЫВАЛА в 16%.

        как было (с копиями)  отказов 39 из 240  PermissionError
        без копий             отказов  6 из 240
        с замком              отказов  0 из 240

    Причина: снятие копии ОТКРЫВАЕТ файл на чтение, а поверх открытого файла
    Windows переименовать не даёт. То есть защита мешала сама себе, и тем чаще,
    чем крупнее становилась память владельца.
    """
    target = home / "brain.json"
    sj.atomic_write_json(target, BIG)
    errors = []
    rounds = 60

    def hammer(name):
        for i in range(rounds):
            payload = dict(BIG)
            payload["i"] = i
            try:
                sj.atomic_write_json(target, payload)
            except Exception as exc:                     # noqa: BLE001
                errors.append(type(exc).__name__)

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=180)
    assert not [t for t in threads if t.is_alive()], "запись встала намертво"
    assert not errors, f"отказов записи {len(errors)}: {set(errors)}"
    json.loads(target.read_text(encoding="utf-8"))       # и файл читается


def test_a_reader_that_repairs_does_not_race_a_writer(home, monkeypatch):
    """Чтение с восстановлением — это ВТОРОЙ ПИСАТЕЛЬ: оно уносит битый файл в
    карантин и записывает на его место копию. Значит и оно обязано стоять в
    очереди, иначе чинит ровно тогда, когда кто-то пишет.

    ДВЕ МОИ ОШИБКИ В ЭТОМ ТЕСТЕ, ОБЕ НАЙДЕНЫ 21.08.2026, ОБЕ ЗАПИСАНЫ ЗДЕСЬ.

    Первая: тест был ЗЕЛЁНЫМ НИ О ЧЁМ. Я снял замок с чтения — тест не
    покраснел. Файл во время того теста был ЦЕЛЫЙ, поэтому чтение возвращалось
    на «source: primary» и до записи не доходило вовсе. Читатель, который ничего
    не чинит, не второй писатель, и проверять на нём было нечего.

    Вторая: починив первую, я сделал тест МИГАЮЩИМ — 2 падения из 8. Я портил
    файл вечно пишущему потоку, и тот успевал затереть порчу раньше, чем
    читатель успевал её увидеть; починка не случалась, и краснел мой же
    предохранитель. Гонка была В ТЕСТЕ. Мигающий сторож хуже отсутствующего: в
    прогоне порчи `-x` останавливается на первом падении, и мой стенд записал
    «поймано» ШЕСТЬ РАЗ подряд для порч, о которых этот тест знать не может.
    Ложные «17 из 17» — прямое следствие.

    Поэтому здесь больше нет угадывания тайминга. Порча файла делается ИЗНУТРИ
    самой записи (в этот момент замок заведомо занят, и никто не вмешается), а
    одновременность проверяется не сном, а вопросом «занят ли замок» — та же
    техника, что у кассы записи в базу в блоке 7.
    """
    target = home / "state.json"
    sj.atomic_write_json(target, {"важное": "версия 1"})
    sj.atomic_write_json(target, {"важное": "версия 2"})   # .bak1 -> починка есть

    real_write = sj._write_locked
    seen_unlocked = []
    depth = []
    peak = [0]
    tally = threading.Lock()

    def watched(path, payload, snapshots):
        # 1. Замок ОБЯЗАН быть занят на каждой записи, включая ту, которую
        #    делает чтение при восстановлении.
        if not sj._LOCK.locked():
            seen_unlocked.append(str(path.name))
        # 2. И внутри записи не может быть двух потоков сразу.
        with tally:
            depth.append(1)
            peak[0] = max(peak[0], len(depth))
        try:
            return real_write(path, payload, snapshots)
        finally:
            with tally:
                depth.pop()

    monkeypatch.setattr(sj, "_write_locked", watched)

    errors = []
    repairs = [0]
    unquarantined = [0]
    rounds = 12

    def repairer():
        for _ in range(rounds):
            try:
                # Порча ИЗНУТРИ записи: замок наш, писатель ждёт, затереть
                # порчу до нашего чтения физически некому.
                def spoil(data):
                    target.write_text('{"обрыв', encoding="utf-8")
                    return data
                sj.update(home / "trigger.json", spoil)
                _data, report = sj.load_json_report(target, dict, label="Проба")
            except Exception as exc:                     # noqa: BLE001
                errors.append(f"читатель: {type(exc).__name__}: {exc}")
                continue
            if report["source"] == "primary":
                errors.append("порча не дожила до чтения — тест снова ни о чём")
                continue
            repairs[0] += 1
            if not report["quarantined"]:
                unquarantined[0] += 1

    def writer(name):
        for i in range(rounds * 3):
            try:
                sj.atomic_write_json(home / f"other-{name}.json", {"n": i})
            except Exception as exc:                     # noqa: BLE001
                errors.append(f"писатель {name}: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=repairer)]
    threads += [threading.Thread(target=writer, args=(n,)) for n in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=180)
    assert not [t for t in threads if t.is_alive()], "чтение и запись столкнулись"
    assert not errors, errors[:3]
    assert repairs[0] == rounds, (
        f"настоящих починок {repairs[0]} из {rounds} — тест ни о чём")
    assert not seen_unlocked, (
        f"запись шла без замка: {sorted(set(seen_unlocked))} — чтение, которое "
        f"чинит, идёт мимо очереди")
    assert peak[0] == 1, (
        f"внутри записи оказалось потоков сразу: {peak[0]}")
    assert unquarantined[0] == 0, (
        f"починок без карантина: {unquarantined[0]} — повреждённый файл затёрли, "
        f"а он единственная копия потери")


def test_forgetting_one_fact_does_not_resurrect_or_lose_another(home):
    """ВТОРОЙ ЖИВОЙ СЦЕНАРИЙ, и его пропустила первая порча кода 21.08.2026.

    Я вернул «забудь» к коду до блока 9 (прочитать -> удалить -> записать) — и
    ни один сторож не покраснел. Причина простая: `forget` не проверялся ПОД
    НАГРУЗКОЙ вообще, а структурный сторож перечислял только три функции из
    четырёх. Порча была настоящая, слабым был сторож.

    Чем это грозит владельцу вслух. «Забудь мой адрес» и фоновый извлекатель
    сходятся в одном файле, и обе беды одинаково молчаливые:
      * фоновый записал свою копию, прочитанную ДО удаления -> адрес ВЕРНУЛСЯ,
        а Джарвис сказал «забыл»;
      * «забудь» записал свою копию, прочитанную ДО находки -> свежий факт
        ИСЧЕЗ, и никто об этом не узнает.
    """
    from memory import memory_manager as mm

    mm.update_memory({"personal": {"адрес": {"value": "Ленина 1"},
                                   "телефон": {"value": "12345"}}})
    assert mm.load_memory()["personal"]["адрес"]["value"] == "Ленина 1"

    errors = []
    ready = threading.Barrier(2, timeout=60)

    def forgetter():
        try:
            ready.wait()
            mm.forget("адрес")
        except Exception as exc:                          # noqa: BLE001
            errors.append(f"забудь: {type(exc).__name__}: {exc}")

    def background():
        try:
            ready.wait()
            mm.update_memory({"notes": {"находка": {"value": "заметил"}}})
        except Exception as exc:                          # noqa: BLE001
            errors.append(f"фон: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=forgetter),
               threading.Thread(target=background)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not [t for t in threads if t.is_alive()], "забывание встало намертво"
    assert not errors, errors

    final = mm.load_memory()
    assert "адрес" not in final.get("personal", {}), (
        "ФАКТ ВЕРНУЛСЯ ПОСЛЕ «ЗАБУДЬ» — Джарвис сказал «забыл» и соврал")
    assert final.get("notes", {}).get("находка"), (
        "свежий факт фона ЗАТЁРТ забыванием")
    assert final.get("personal", {}).get("телефон"), (
        "забыли не то, что просили")


def test_a_nested_write_joins_instead_of_deadlocking(home):
    """Вложенный вызов присоединяется к уже взятому замку.

    RLock был бы короче, но он ПРЯЧЕТ вложенность вместо того, чтобы её
    обработать — тот же выбор, что сделан у кассы записи в базу (блок 7).
    Без обработки это мёртвая хватка, а живьём — «Джарвис завис при запуске».
    """
    target = home / "deep.json"
    sj.atomic_write_json(target, {"n": 0})
    reached = []

    def outer(data):
        data["внешнее"] = 1
        sj.atomic_write_json(target, {"вложенное": 2})
        reached.append(True)
        return data

    th = threading.Thread(target=lambda: sj.update(target, outer))
    th.start()
    th.join(timeout=15)
    assert not th.is_alive(), "МЁРТВАЯ ХВАТКА на вложенной записи"
    assert reached, "вложенная запись не дошла до конца"


def test_the_lock_is_not_reentrant():
    """RLock спрятал бы вложенность. Тот же сторож стоит у пропуска (блок 3) и
    у кассы записи в базу (блок 7)."""
    assert not isinstance(sj._LOCK, type(threading.RLock())), (
        "замок кассы состояния стал повторным — вложенность перестанет ловиться")


# -- Мусор и улики --------------------------------------------------------

def test_an_orphaned_temp_file_is_swept_but_a_fresh_one_is_not(home):
    """Откуда берутся осиротевшие файлы: процесс умер между созданием
    временного файла и переименованием. Имя у него каждый раз новое, поэтому
    следующая запись его НЕ перезапишет — он ляжет рядом навсегда.

    Существующий тест на убийство процесса прямо разрешал один такой файл
    (`assert len(leftovers) <= 1`), то есть утечка была узаконена, а не поймана.

    Свежий файл трогать нельзя: это может быть запись, идущая прямо сейчас.
    """
    target = home / "brain.json"
    sj.atomic_write_json(target, {"n": 1})

    old = home / ".brain.json.deadbeef.tmp"
    old.write_text("недописанный мусор", encoding="utf-8")
    long_ago = time.time() - 2 * sj._ORPHAN_AGE_S
    os.utime(old, (long_ago, long_ago))

    fresh = home / ".brain.json.freshone.tmp"
    fresh.write_text("идёт запись прямо сейчас", encoding="utf-8")

    sj.reset_for_tests()
    sj.atomic_write_json(target, {"n": 2})

    assert not old.exists(), "старый осиротевший файл не убран"
    assert fresh.exists(), (
        "убран СВЕЖИЙ временный файл — а это могла быть идущая запись")


def test_a_failed_memory_write_never_ends_the_conversation(home, monkeypatch):
    """ЗАПИСЫВАЮ ОГРАНИЧЕНИЕ, А НЕ ПОБЕДУ.

    Блок 9 убрал «Saved» после провала ВНУТРИ memory_manager. Но у памяти есть
    ВТОРАЯ площадка, до которой блок 9 не достаёт, и она в замороженном main.py:

        main.py:203   update_memory(data)                    <- провал молчит
        main.py:204   print(f"[Memory] ✅ {...}")             <- и сразу «успех»

        main.py:1274  update_memory({category: {key: entry}})
        main.py:1276  print("[Memory] 💾 save_memory: ...")
        main.py:1281  response={"result": "ok"}              <- И МОДЕЛИ ТОЖЕ

    Вторая хуже первой: она говорит «ок» не владельцу, а МОДЕЛИ, и та скажет
    «запомнил» вслух.

    Очевидное лечение — бросать исключение — Я ОТКЛОНИЛ, и вот проверенная
    причина. Площадка А (main.py:198-216) обёрнута своим `try/except`, там
    исключение было бы поймано и напечатано честно. Площадка Б (main.py:1252
    `_execute_tool`) своего `try` НЕ ИМЕЕТ: исключение уйдёт наверх, в общий
    `except` цикла приёма (main.py:1801), а тот заканчивает цикл целиком. То
    есть «факт не сохранился» превратилось бы в «Джарвис замолчал посреди
    разговора». Цена лечения выше цены болезни.

    Поэтому сторож закрепляет ИМЕННО контракт «не бросать», чтобы будущая
    попытка починить вранье не обошлась владельцу обрывом разговора. Настоящее
    лечение возможно только когда main.py разморозят: там нужна проверка
    возвращённого значения перед печатью «✅».
    """
    from memory import memory_manager as mm

    def boom(*a, **k):
        raise OSError("диск полон")

    monkeypatch.setattr(mm, "safe_update", boom)
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)

    # Ни то, ни другое не имеет права бросить наружу: main.py их не ловит.
    result = mm.update_memory({"preferences": {"цвет": {"value": "синий"}}})
    assert isinstance(result, dict), (
        "update_memory вернула не словарь — main.py:203 сломается")

    answer = mm.forget("цвет")
    assert isinstance(answer, str) and "Could not forget" in answer, (
        "forget обязана вернуть ЧЕСТНУЮ строку: она уходит модели как результат "
        "(main.py:1291), и по ней модель решает, говорить ли «забыл»")


def test_the_sweep_happens_once_per_run(home):
    target = home / "brain.json"
    sj.atomic_write_json(target, {"n": 1})
    assert sj._swept is True
    later = home / ".brain.json.second.tmp"
    later.write_text("мусор", encoding="utf-8")
    long_ago = time.time() - 2 * sj._ORPHAN_AGE_S
    os.utime(later, (long_ago, long_ago))
    sj.atomic_write_json(target, {"n": 2})
    assert later.exists(), "уборка сработала второй раз за запуск"


def test_a_quarantined_file_is_evidence_and_is_never_swept(home):
    """Временный файл — мусор по построению: он недописан и не нужен никому.
    Карантин — УЛИКА: единственная копия того, что владелец потерял. Удалять
    улику ради чистоты нельзя, поэтому её называет доктор."""
    target = home / "broken.json"
    sj.atomic_write_json(target, {"важное": "версия 1"})
    sj.atomic_write_json(target, {"важное": "версия 2"})
    target.write_text('{"обрыв', encoding="utf-8")

    data, report = sj.load_json_report(target, dict, label="Проба")
    assert report["source"].startswith("snapshot:"), report
    assert data == {"важное": "версия 1"}, (
        "восстановили не предыдущую годную версию")

    quarantined = list(home.glob("broken.json.corrupt-*"))
    assert len(quarantined) == 1, "карантин не создан"
    old_stamp = quarantined[0].stat().st_mtime
    os.utime(quarantined[0], (old_stamp - 10 * sj._ORPHAN_AGE_S,
                              old_stamp - 10 * sj._ORPHAN_AGE_S))

    sj.reset_for_tests()
    sj.atomic_write_json(target, {"важное": "версия 3"})
    still = list(home.glob("broken.json.corrupt-*"))
    assert len(still) == 1, "уборка удалила УЛИКУ — единственную копию потери"


# -- Отказ записи отличим от успеха ---------------------------------------

def test_memory_never_says_saved_after_a_failed_write(home, monkeypatch,
                                                      capfd=None):
    """Старый код печатал «Saved» ПОСЛЕ неудачной записи — то есть Джарвис
    говорил «запомнил» о том, чего не запомнил, и дальше кладал факт в
    поисковый указатель. Указатель и файл расходились молча."""
    from memory import memory_manager as mm
    said = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: said.append(" ".join(
        str(x) for x in a)))

    def boom(*a, **k):
        raise OSError("диск полон")

    monkeypatch.setattr(mm, "safe_update", boom)
    mm.update_memory({"preferences": {"цвет": {"value": "синий"}}})

    joined = " ".join(said)
    assert "Saved" not in joined, (
        f"сказал «Saved» после провала записи: {joined[:200]}")
    assert "прежняя копия цела" in joined, (
        f"провал записи не назван вслух: {joined[:200]}")


def test_personality_never_says_updated_after_a_failed_write(home, monkeypatch):
    from memory import personality_engine as pe
    said = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: said.append(" ".join(
        str(x) for x in a)))

    def boom(*a, **k):
        raise OSError("диск полон")

    monkeypatch.setattr(pe, "safe_update", boom)
    pe.update_personality({"interaction_count": 1})

    joined = " ".join(said)
    assert "Updated" not in joined, (
        f"сказал «Updated» после провала записи: {joined[:200]}")


# -- Живой сценарий владельца ---------------------------------------------

def test_the_owner_fact_and_the_background_fact_both_survive(home):
    """ЖИВОЙ СЦЕНАРИЙ, на котором дефект и был найден.

    Владелец говорит «запомни X» (main.py:1274), а фоновый извлекатель в это же
    время сохраняет свою находку (main.py:203). Оба идут через update_memory.
    До блока 9 факт владельца исчезал.
    """
    from memory import memory_manager as mm
    errors = []

    def owner():
        try:
            mm.update_memory({"preferences": {"цвет": {"value": "синий"}}})
        except Exception as exc:                          # noqa: BLE001
            errors.append(f"владелец: {exc}")

    def background():
        try:
            mm.update_memory({"notes": {"находка": {"value": "заметил"}}})
        except Exception as exc:                          # noqa: BLE001
            errors.append(f"фон: {exc}")

    threads = [threading.Thread(target=owner),
               threading.Thread(target=background)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, errors

    final = mm.load_memory()
    assert final.get("preferences", {}).get("цвет"), (
        "ФАКТ ВЛАДЕЛЬЦА ПОТЕРЯН — а Джарвис сказал «запомнил»")
    assert final.get("notes", {}).get("находка"), "факт фона потерян"


# -- Структурные сторожа --------------------------------------------------

def test_the_state_desk_is_the_only_lock_around_json_state():
    """Два замка вокруг одного файла — это разный порядок захвата, то есть
    мёртвая хватка. Замки памяти и личности были ради записи; теперь запись
    сериализует касса, и своих замков у них быть не должно."""
    with io.open(ROOT / "core" / "safe_json.py", "rb") as fh:
        code = " ".join(t.string for t in tokenize.tokenize(fh.readline)
                        if t.type not in (tokenize.COMMENT, tokenize.STRING))
    assert "_LOCK" in code, "касса состояния потеряла свой замок"


def test_the_writers_go_through_update_not_through_read_then_write():
    """Читать и писать двумя отдельными действиями — и есть тот дефект.

    Список ПОЛНЫЙ нарочно: в первой версии здесь было три писателя из четырёх, и
    порча кода 21.08.2026 прошла незамеченной ровно через пропущенного —
    `forget`. Сторож, который перечисляет писателей, обязан перечислять всех:
    забытая строка в списке ничем не отличается от отсутствующего сторожа.
    """
    import inspect
    from memory import memory_manager as mm
    from memory import personality_engine as pe
    from config import loader

    for label, fn in (("память: запомни", mm.update_memory),
                      ("память: забудь", mm.forget),
                      ("личность", pe.update_personality),
                      ("настройки", loader.set_setting)):
        src = inspect.getsource(fn)
        assert "safe_update" in src, (
            f"{label}: правка идёт не через кассу состояния")
        assert "atomic_write_json" not in src, (
            f"{label}: пишет напрямую, минуя кассу — значит между чтением и "
            f"записью снова есть щель")


def test_the_doctor_still_changes_nothing(home):
    """Доктор получил новый раздел про память. Его главное правило не должно
    было пострадать: он только смотрит."""
    src = (ROOT / "tools" / "doctor.py").read_text(encoding="utf-8")
    assert "part_memory" in src
    for forbidden in ("load_json", "atomic_write_json", "open_store(",
                      ".acquire("):
        assert forbidden not in src, f"доктор начал делать {forbidden}"

    target = home / "long_term.json"
    sj.atomic_write_json(target, {"preferences": {"цвет": "синий"}})
    before = sorted((p.name, p.stat().st_size) for p in home.iterdir())
    env = dict(os.environ, JARVIS_STATE_DIR=str(home),
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, "tools/doctor.py"], env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          cwd=str(ROOT), timeout=120)
    assert proc.returncode == 0, proc.stderr[-500:]
    assert "long_term.json" in proc.stdout, "доктор не увидел память"
    after = sorted((p.name, p.stat().st_size) for p in home.iterdir())
    assert before == after, f"доктор изменил дом: {before} -> {after}"


def test_the_doctor_names_a_broken_memory_file(home):
    """Битая память доктору была НЕВИДИМА: она попадала только в общий список
    файлов дома как имя и размер."""
    (home / "long_term.json").write_text('{"обрыв', encoding="utf-8")
    env = dict(os.environ, JARVIS_STATE_DIR=str(home),
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, "tools/doctor.py"], env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          cwd=str(ROOT), timeout=120)
    assert "ФАЙЛ БИТЫЙ" in proc.stdout, proc.stdout[-600:]
    assert "копий нет" in proc.stdout, (
        "доктор не сказал, что копий нет — а это разница между "
        "«починится сам» и «данные потеряны»")
    assert (home / "long_term.json").read_text(encoding="utf-8") == '{"обрыв', (
        "доктор тронул битый файл")


# -- Падение процесса: атомарность не должна была пострадать -------------

def test_a_killed_process_still_leaves_the_previous_file_intact(home):
    """Это свойство было ДО блока 9 (Stage 3.0) и обязано остаться. Замок и
    уборка не имеют права его сломать."""
    victim = home / "brain.json"
    sj.atomic_write_json(victim, {"память": "важные факты", "n": 1})
    before = victim.read_text(encoding="utf-8")

    child = (
        "import os, sys, time\n"
        f"sys.path.insert(0, r'{ROOT}')\n"
        f"os.environ['{sj.STATE_DIR_ENV}'] = r'{home}'\n"
        "from core import safe_json as sj\n"
        "from pathlib import Path\n"
        "real = os.replace\n"
        "def dying(src, dst):\n"
        "    if str(dst).endswith('brain.json'):\n"
        "        print('READY', flush=True)\n"
        "        time.sleep(30)\n"
        "    return real(src, dst)\n"
        "os.replace = dying\n"
        f"sj.atomic_write_json(Path(r'{home}') / 'brain.json', {{'n': 2}})\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", child],
                            stdout=subprocess.PIPE, text=True,
                            encoding="utf-8")
    try:
        assert proc.stdout.readline().strip() == "READY"
        time.sleep(0.3)
    finally:
        proc.kill()
        proc.wait(timeout=30)

    assert victim.read_text(encoding="utf-8") == before, (
        "прежний файл пострадал от убитой записи")
    json.loads(victim.read_text(encoding="utf-8"))
