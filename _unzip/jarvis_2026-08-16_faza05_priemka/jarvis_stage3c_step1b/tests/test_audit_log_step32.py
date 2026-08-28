# -*- coding: utf-8 -*-
"""
Сторож шага 32 фазы 0: журнал решений двери живёт ВНЕ папки проекта.

Почему этот файл есть. Журнал гейта — единственный свидетель того, что
Джарвис сделал и почему дверь это разрешила. Пока он лежал в BASE/logs,
каждая распаковка свежего zip начинала пустую тетрадь, а прошлая оставалась
в «Загрузках». Одного правильного пути здесь мало: без сторожа любая будущая
правка тихо вернёт журнал внутрь проекта, и заметит это владелец в тот вечер,
когда журнал понадобится, — то есть поздно.

Отдельно проверяется то, что само себя не покажет: что запись вООБЩЕ
происходит. Молчащая касса снаружи выглядит как полностью здоровая система.

Каждый тест работает в песочнице: свой временный дом через $JARVIS_STATE_DIR.
Настоящие ~/.jarvis и папка проекта не трогаются ни на чтение, ни на запись.

Запуск:  python -m pytest tests/test_audit_log_step32.py -q
или:     python tests/test_audit_log_step32.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.loader import get_base_dir          # noqa: E402
from core import audit_log                      # noqa: E402
from core.gate import dispatch                  # noqa: E402
from core.safe_json import STATE_DIR_ENV        # noqa: E402


class _Home:
    """Временный дом, полностью возвращаемый на выходе."""

    def __enter__(self) -> "_Home":
        self.tmp = Path(tempfile.mkdtemp(prefix="jv_audit_test_"))
        self._env = os.environ.get(STATE_DIR_ENV)
        os.environ[STATE_DIR_ENV] = str(self.tmp)
        audit_log.reset()
        return self

    def __exit__(self, *exc) -> bool:
        if self._env is None:
            os.environ.pop(STATE_DIR_ENV, None)
        else:
            os.environ[STATE_DIR_ENV] = self._env
        audit_log.reset()
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False

    @property
    def journal(self) -> Path:
        return self.tmp / "logs" / "gate-audit.jsonl"

    def generation(self, index: int) -> Path:
        return self.journal.with_name(f"{self.journal.name}.{index}")

    def records(self) -> list:
        text = self.journal.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]


# ── Где лежит журнал ────────────────────────────────────────────

def test_journal_lives_in_the_home_not_in_the_project():
    """Адрес журнала — дом/logs, и ни одним коленом не внутри сборки."""
    project = get_base_dir().resolve()
    with _Home() as home:
        target = audit_log.path()
        assert target == home.journal, f"журнал не в доме: {target}"
        assert target.parent.name == audit_log.DIR_NAME
        assert project not in target.resolve().parents, \
            "журнал вернулся внутрь проекта — это регресс шага 32"


def test_path_is_resolved_at_call_time():
    """Смена дома после импорта действует.

    Константа уровня модуля уже стоила проекту бага с настройками:
    тест выставлял JARVIS_STATE_DIR, а код уже запомнил настоящий дом.
    """
    with _Home() as first:
        assert audit_log.path() == first.journal
        with _Home() as second:
            assert audit_log.path() == second.journal
        assert audit_log.path() == first.journal


def test_project_tree_has_no_journal():
    """В дереве проекта журнала нет ни одного файла."""
    stray = sorted(str(p) for p in get_base_dir().rglob("gate-audit.jsonl*"))
    assert not stray, (
        "журнал найден внутри проекта: " + ", ".join(stray) +
        ". Если это старый журнал от сборки до шага 32 — удалите папку logs "
        "вручную: zip не умеет удалять файлы.")


def test_test_run_never_touches_the_real_home():
    """Прогон без JARVIS_STATE_DIR пишет во временную папку, а не в дом."""
    if "pytest" not in sys.modules:
        return  # одиночный запуск ведёт себя как живой: дом настоящий
    saved = os.environ.pop(STATE_DIR_ENV, None)
    audit_log.reset()
    try:
        target = audit_log.path().resolve()
        real_home = (Path.home() / ".jarvis").resolve()
        assert real_home not in target.parents, \
            "прогон тестов засоряет настоящий журнал владельца"
        assert get_base_dir().resolve() not in target.parents
    finally:
        if saved is not None:
            os.environ[STATE_DIR_ENV] = saved
        audit_log.reset()


# ── Что именно записано ────────────────────────────────────────

def test_dispatch_writes_a_full_line():
    """Решение двери попадает в журнал целиком, вместе с версией формата."""
    with _Home() as home:
        verdict = dispatch("web_search", {"query": "x"}, mode="interactive")
        assert home.journal.exists(), "журнал не создан — касса молчит"
        records = home.records()
        assert len(records) == 1, f"ожидалась одна строка, а их {len(records)}"
        line = records[0]
        for key in ("schema_ver", "ts", "ts_utc", "tool", "action", "mode",
                    "verdict", "risk", "policy", "reason", "param_keys"):
            assert key in line, f"в строке журнала нет поля {key}"
        assert line["schema_ver"] == audit_log.SCHEMA_VER
        assert line["tool"] == "web_search"
        assert line["verdict"] == verdict.verdict == "run"
        assert line["ts_utc"].endswith("Z")
        assert line["param_keys"] == ["query"]


def test_blocked_calls_are_recorded_too():
    """Запрет — тоже событие: без этого нельзя разобрать «почему он отказал»."""
    with _Home() as home:
        dispatch("file_controller", {"action": "delete", "path": "x"},
                 mode="autonomous")
        line = home.records()[-1]
        assert line["verdict"] == "blocked"
        assert line["reason"], "причина отказа не записана"


def test_values_never_reach_the_journal():
    """В журнале только имена параметров — теперь навсегда, а не на один вечер.

    До шага 32 журнал умирал вместе с папкой сборки; теперь он вечный,
    и цена любой утечки в него выросла.
    """
    with _Home() as home:
        dispatch("web_search", {"query": "marker-secret-xyz"}, mode="interactive")
        text = home.journal.read_text(encoding="utf-8")
        assert "marker-secret-xyz" not in text, "значение параметра утекло в журнал"
        assert "query" in home.records()[0]["param_keys"]


def test_the_till_owns_schema_and_time():
    """Звонящий не может подменить версию формата или отметку времени."""
    with _Home() as home:
        assert audit_log.append({"schema_ver": 99, "ts": "подделка", "tool": "t"})
        line = home.records()[0]
        assert line["schema_ver"] == audit_log.SCHEMA_VER
        assert line["ts"] != "подделка"
        assert line["tool"] == "t"


def test_lines_end_with_lf_only():
    """CRLF в данных ломает разбор всем, кроме Python; до шага 32 он там был."""
    with _Home() as home:
        dispatch("web_search", {"query": "x"}, mode="interactive")
        raw = home.journal.read_bytes()
        assert b"\r\n" not in raw, "журнал пишется с CRLF"
        assert raw.endswith(b"\n")


# ── Потолок и отказы ───────────────────────────────────────────

def test_rotation_keeps_a_hard_ceiling():
    """Ротация проверяется по-настоящему.

    С боевым потолком 8 МБ этот код впервые выполнился бы примерно через
    год — то есть оставался бы непроверенным. Потому потолок — константа
    модуля, а не число в глубине функции.
    """
    with _Home() as home:
        saved = audit_log.MAX_BYTES
        audit_log.MAX_BYTES = 900
        try:
            for index in range(60):
                assert audit_log.append({"tool": "t", "n": index})
        finally:
            audit_log.MAX_BYTES = saved

        assert home.generation(1).exists(), "ротации нет: файл растёт без предела"
        assert home.generation(audit_log.GENERATIONS).exists(), \
            "поколения не доехали до последнего"
        assert not home.generation(audit_log.GENERATIONS + 1).exists(), \
            "поколений больше, чем разрешено — потолка нет"
        assert home.journal.stat().st_size <= 900


def test_a_broken_journal_never_breaks_the_action():
    """Записать нельзя — действие всё равно выполняется, потеря посчитана."""
    with _Home() as home:
        # На месте папки logs — файл: mkdir упадёт, касса обязана стерпеть.
        home.journal.parent.write_text("не папка", encoding="utf-8")

        result = dispatch("web_search", {"query": "x"}, mode="interactive")
        assert result.verdict == "run", "журнал уронил само действие"
        assert audit_log.append({"tool": "t"}) is False
        assert audit_log.lost_count() >= 2, "потеря записи прошла незамеченной"


def test_journal_survives_a_new_build():
    """Главный смысл шага: записи живут дольше папки сборки.

    Смену сборки изображаем тем, что касса забывает всё, что помнила
    (reset() = новый процесс из новой папки), а дом остаётся тем же.
    """
    with _Home() as home:
        dispatch("web_search", {"query": "вечер первый"}, mode="interactive")
        audit_log.reset()
        dispatch("web_search", {"query": "вечер второй"}, mode="interactive")
        assert len(home.records()) == 2, \
            "записи первого вечера потерялись при смене сборки"


if __name__ == "__main__":
    _tests = [value for name, value in sorted(globals().items())
              if name.startswith("test_") and callable(value)]
    for _fn in _tests:
        _fn()
        print(f"OK   {_fn.__name__}")
    print(f"OK: {len(_tests)} passed (standalone)")
