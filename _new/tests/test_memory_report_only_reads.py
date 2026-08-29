# tests/test_memory_report_only_reads.py
"""
СТОРОЖ ПОКАЗОМЕТРА ПАМЯТИ (memory_report.py).

Этот отчёт владелец будет запускать на СВОЕЙ живой памяти, чтобы убедиться,
что срок годности работает. Значит у него две обязанности, и обе надо
охранять кодом, а не обещанием:

  1. НИЧЕГО НЕ ПИСАТЬ. Инструмент проверки, который меняет проверяемое, —
     это не проверка. Запуск отчёта на настоящем ~/.jarvis не имеет права
     тронуть ни один байт: ни память, ни настройки, ни индекс, ни журнал.

  2. НЕ ИМЕТЬ СВОЕЙ КОПИИ ПРАВИЛ. Отчёт обязан считать теми же функциями и
     теми же порогами, что и сборка промпта. Своя копия разошлась бы с
     настоящей при первой же смене порога и врала бы УСПОКАИВАЮЩЕ — а
     успокаивающая ложь хуже отсутствия отчёта: она снимает подозрение,
     когда подозревать как раз нужно.

Замерено: `load_memory` зовёт миграцию, а миграция имеет право писать —
поэтому отчёт открывает long_term.json напрямую. Сторож ниже проверяет и
это тоже, по исходнику: соблазн «просто позвать load_memory» вернётся.
"""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import runpy
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def day(n: int) -> str:
    return (datetime.now().date() - timedelta(days=n)).strftime("%Y-%m-%d")


# Значение должно быть НЕ мусорным, иначе его скроет фильтр мусора и тест
# измерит не то, что думает. Замерено: looks_like_junk("x") -> True.
ROT = "someone visited on that occasion"
LIVE = "owner works on serious matters"


def _write_memory(home: Path) -> Path:
    path = home / "long_term.json"
    path.write_text(json.dumps({
        "identity": {"name": {"value": "Rustam", "updated": day(300)}},
        "notes": {
            "meeting_yesterday": {"value": ROT, "updated": day(40)},
            "headache_today": {"value": ROT, "updated": day(30)},
            "serious_work": {"value": LIVE, "updated": day(30)},
        },
    }, ensure_ascii=False), encoding="utf-8")
    return path


def _fingerprint(home: Path) -> dict:
    """Слепок ВСЕЙ папки состояния: имя -> (размер, время изменения)."""
    out = {}
    for p in sorted(home.rglob("*")):
        try:
            st = p.stat()
        except OSError:
            continue
        out[str(p.relative_to(home))] = (
            -1 if p.is_dir() else st.st_size, st.st_mtime_ns)
    return out


def _run_report() -> str:
    """Запустить отчёт и вернуть его вывод.

    Свой перехват, а не `capsys`: в этом доме pyproject.toml задаёт
    `addopts = "-p no:capture"` (обход сломанного pyreadline в capture-слое),
    и `capsys` падает с AttributeError на этапе подготовки теста. Замерено:
    'NoneType' object has no attribute 'set_fixture'. Значит перехват вывода
    здесь — не роскошь, а единственный доступный способ.
    """
    mod = importlib.import_module("memory_report")
    importlib.reload(mod)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = mod.main()
    assert code == 0
    return buf.getvalue()


# ── 1. Отчёт ничего не пишет ──────────────────────────────────────

def test_report_does_not_write_anything(tmp_path, monkeypatch):
    """Запуск отчёта не меняет НИ ОДНОГО файла в папке состояния."""
    home = tmp_path / "home"
    home.mkdir()
    _write_memory(home)
    monkeypatch.setenv("JARVIS_STATE_DIR", str(home))

    before = _fingerprint(home)
    _run_report()
    after = _fingerprint(home)

    assert before == after, (
        "Отчёт тронул папку состояния. Разница: "
        f"{set(before.items()) ^ set(after.items())}")


def test_report_creates_no_new_files(tmp_path, monkeypatch):
    """Даже новых файлов не появляется — ни журнала, ни индекса."""
    home = tmp_path / "home"
    home.mkdir()
    _write_memory(home)
    monkeypatch.setenv("JARVIS_STATE_DIR", str(home))

    _run_report()

    assert {p.name for p in home.iterdir()} == {"long_term.json"}


def test_report_survives_an_empty_home(tmp_path, monkeypatch):
    """Нет файла памяти — отчёт говорит это словами, а не падает."""
    home = tmp_path / "empty"
    home.mkdir()
    monkeypatch.setenv("JARVIS_STATE_DIR", str(home))

    _run_report()
    assert not list(home.iterdir()), "Отчёт создал файл в пустом доме"


def test_report_survives_broken_json(tmp_path, monkeypatch):
    """Битый файл не должен ни падать, ни быть перезаписан «на всякий»."""
    home = tmp_path / "broken"
    home.mkdir()
    path = home / "long_term.json"
    path.write_text("{ это не json", encoding="utf-8")
    monkeypatch.setenv("JARVIS_STATE_DIR", str(home))

    before = _fingerprint(home)
    _run_report()
    assert _fingerprint(home) == before


# ── 2. Отчёт согласен со сборкой промпта ──────────────────────────

def test_report_counts_agree_with_the_real_prompt(tmp_path, monkeypatch):
    """Число «скрыто по сроку годности» — то же, что у сборки промпта.

    Если отчёт и промпт разойдутся, владелец будет чинить не ту проблему.
    """
    home = tmp_path / "home"
    home.mkdir()
    _write_memory(home)
    monkeypatch.setenv("JARVIS_STATE_DIR", str(home))

    from memory import memory_manager as mm
    memory = json.loads((home / "long_term.json").read_text(encoding="utf-8"))
    _visible, hidden = mm._visible_memory(memory)
    assert hidden == 2, "Опора теста поехала: ожидались 2 просроченных"

    out = _run_report()
    assert f"{hidden}" in out
    assert "Скрыто по сроку годности" in out


def test_report_shows_the_same_block_the_model_gets(tmp_path, monkeypatch):
    """Показанный блок — БУКВАЛЬНО тот, что уедет модели, а не пересказ."""
    home = tmp_path / "home"
    home.mkdir()
    _write_memory(home)
    monkeypatch.setenv("JARVIS_STATE_DIR", str(home))

    from memory import memory_manager as mm
    memory = json.loads((home / "long_term.json").read_text(encoding="utf-8"))
    block = mm.format_memory_for_prompt(memory)
    assert block.strip(), "Опора теста поехала: блок пуст"

    out = _run_report()
    for line in block.strip().splitlines():
        if line.strip():
            assert line in out, f"Строка блока не показана: {line!r}"


def test_report_names_the_hidden_facts(tmp_path, monkeypatch):
    """Скрытые факты названы по имени — иначе отчёт нельзя оспорить."""
    home = tmp_path / "home"
    home.mkdir()
    _write_memory(home)
    monkeypatch.setenv("JARVIS_STATE_DIR", str(home))

    out = _run_report()
    assert "meeting_yesterday" in out
    assert "headache_today" in out


def test_report_says_plainly_when_nothing_is_old_enough(tmp_path, monkeypatch):
    """Свежая память: «ничего не скрыто» + почему это НОРМАЛЬНО.

    Ровно та ловушка, из-за которой отчёт и появился: пустой результат не
    должен читаться как «правка не работает».
    """
    home = tmp_path / "fresh"
    home.mkdir()
    (home / "long_term.json").write_text(json.dumps({
        "notes": {"meeting_yesterday": {"value": ROT, "updated": day(1)}},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("JARVIS_STATE_DIR", str(home))

    out = _run_report()
    assert "По времени пока ничего не скрыто" in out
    assert "НОРМАЛЬНО" in out


# ── 3. Отчёт не имеет своей копии правил ──────────────────────────

def test_report_borrows_the_thresholds_it_does_not_invent_them():
    """Пороги взяты из модуля. Вписанные числа тихо разойдутся с правдой."""
    mod = importlib.import_module("memory_report")
    importlib.reload(mod)
    from memory import memory_manager as mm
    assert mod._EXPIRY_STALE_DAYS is mm._EXPIRY_STALE_DAYS
    assert mod._EXPIRY_FRESH_DAYS is mm._EXPIRY_FRESH_DAYS


def test_report_source_has_no_hand_written_threshold():
    """В исходнике нет сравнения возраста с числом-константой."""
    src = (ROOT / "memory_report.py").read_text(encoding="utf-8")
    assert "age > 14" not in src
    assert "age > 2" not in src


def test_report_does_not_go_through_load_memory():
    """`load_memory` зовёт миграцию, а миграция имеет право ПИСАТЬ.

    Соблазн «просто позвать load_memory» вернётся при первой правке —
    поэтому запрет закреплён здесь, а не только в комментарии.
    """
    src = (ROOT / "memory_report.py").read_text(encoding="utf-8")
    # Ищем ВЫЗОВ, а не слово: в докстринге «не через load_memory» стоит
    # ровно затем, чтобы объяснить запрет, и запрет на слово запрещал бы
    # собственное объяснение. Мой первый заход именно на этом и покраснел.
    assert "load_memory(" not in src, (
        "Отчёт стал звать load_memory — он больше не только для чтения")
    assert "import load_memory" not in src


def test_report_uses_the_single_door():
    """Считает через `_visible_memory`, а не через свои правила."""
    src = (ROOT / "memory_report.py").read_text(encoding="utf-8")
    assert "_visible_memory" in src


def test_report_never_writes_by_source_inspection():
    """В исходнике нет ни записи, ни изменения настроек."""
    src = (ROOT / "memory_report.py").read_text(encoding="utf-8")
    for forbidden in ("write_text(", "json.dump(", "set_setting",
                      "save_memory", "update_memory", "note_fact",
                      "upsert_fact", "os.remove", "shutil."):
        assert forbidden not in src, f"Отчёт умеет менять: {forbidden}"
    # `open` разрешён только на чтение.
    assert 'open(path, "w"' not in src
    assert "open(path, 'w'" not in src


# ── 4. Отчёт запускается как программа ────────────────────────────

def test_report_runs_as_a_script(tmp_path, monkeypatch):
    """`python memory_report.py` работает — именно так его и позовут."""
    home = tmp_path / "home"
    home.mkdir()
    _write_memory(home)
    monkeypatch.setenv("JARVIS_STATE_DIR", str(home))

    before = _fingerprint(home)
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(ROOT / "memory_report.py"), run_name="__main__")
    assert exc.value.code == 0
    assert _fingerprint(home) == before
