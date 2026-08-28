# Тесты шага 33.4: BUILD.txt пишет код (план Р6, пункт 2).
#
# Главная мысль набора: метка либо говорит правду, либо прямо
# сознаётся, чего она не видела. Самый опасный сценарий — не падение,
# а тихо устаревшие цифры, которым владелец поверит через месяц.
#
# Папка всегда временная: настоящий BUILD.txt проекта тесты не трогают;
# его пишет только хук в конце прогона и старт Jarvis.
#
# В наборе: python -m pytest -q из корня.
# Без pytest: python tests/test_build_stamp_step33.py

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import build_stamp, state_version  # noqa: E402


# -- Снасти -----------------------------------------------------------

class _Spot:
    # Папка на выброс вместо корня проекта.

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="jv_build_"))
        self.old = os.environ.get(build_stamp.DIR_ENV)
        os.environ[build_stamp.DIR_ENV] = str(self.dir)
        return self.dir

    def __exit__(self, *exc):
        if self.old is None:
            os.environ.pop(build_stamp.DIR_ENV, None)
        else:
            os.environ[build_stamp.DIR_ENV] = self.old
        shutil.rmtree(self.dir, ignore_errors=True)
        return False


class _Patched:
    # Подмена одного имени с гарантированным возвратом на место.

    def __init__(self, holder, name, value):
        self.holder = holder
        self.name = name
        self.value = value

    def __enter__(self):
        self.old = getattr(self.holder, self.name)
        setattr(self.holder, self.name, self.value)
        return self.value

    def __exit__(self, *exc):
        setattr(self.holder, self.name, self.old)
        return False


def _log():
    lines = []
    return lines, lines.append


def _text(folder):
    return (folder / build_stamp.FILE_NAME).read_text(encoding="utf-8")


def _temps(folder):
    return [p.name for p in folder.iterdir()
            if p.name.startswith(build_stamp.TMP_PREFIX)]


# -- Файл вообще есть и читаем -----------------------------------------

def test_stamp_appears_where_asked():
    lines, printer = _log()
    with _Spot() as folder:
        data = build_stamp.write(written_by="tests", printer=printer)
        assert data is not None, "метка не легла"
        assert (folder / build_stamp.FILE_NAME).exists()
        assert lines, "о записи не сказали ни слова"


def test_build_txt_written_by_code():
    # Имя теста взято из плана (Р6, пункт 2) без изменений.
    # Здесь сознательное исключение из граблей 13.1 (grep только по core
    # и agent): утверждение плана — именно про то, что пишет КОД, а не
    # владелец. Проверить это можно только там, откуда его зовут.
    hook = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    start = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "build_stamp" in hook, "прогон тестов метку не обновляет"
    assert "pytest_sessionfinish" in hook, "нет хука конца прогона"
    assert "build_stamp" in start, "старт Jarvis метку не обновляет"
    assert "stamp_start" in start, "старт зовёт не того писателя"


def test_lines_read_back_as_they_were_written():
    with _Spot():
        build_stamp.write(written_by="tests", fields={"tests_total": 1278},
                         printer=lambda t: None)
        back = build_stamp.read()
        assert back["tests_total"] == "1278"
        assert back["written_by"] == "tests"
        assert back["stamp_ver"] == str(build_stamp.SCHEMA_VER)


def test_header_explains_who_wrote_the_file():
    with _Spot() as folder:
        build_stamp.write(written_by="tests", printer=lambda t: None)
        text = _text(folder)
        for line in build_stamp.HEADER:
            assert line in text, "владелец не поймёт, что это за файл"


def test_stamp_says_phase_step_and_code_version():
    with _Spot():
        build_stamp.write(written_by="tests", printer=lambda t: None)
        back = build_stamp.read()
        assert back["code_ver"] == str(state_version.CODE_VER)
        assert back["phase"] == str(state_version.PHASE)
        assert back["step"] == str(state_version.STEP)
        assert back["folder"] == state_version.project_dir().name


def test_file_has_no_carriage_returns():
    with _Spot() as folder:
        build_stamp.write(written_by="tests", printer=lambda t: None)
        raw = (folder / build_stamp.FILE_NAME).read_bytes()
        assert chr(13).encode() not in raw, "в файл пролез CRLF"

# -- Цифры тестов -------------------------------------------------------

def test_full_run_writes_the_numbers():
    with _Spot():
        build_stamp.stamp_tests(total=1298, failed=0, seconds=27.38,
                               full=True, printer=lambda t: None)
        back = build_stamp.read()
        assert back["tests_total"] == "1298"
        assert back["tests_failed"] == "0"
        assert back["tests_seconds"] == "27.38"
        assert back["tests_scope"] == build_stamp.SCOPE_FULL
        assert back["tests_at"]


def test_partial_run_never_overwrites_full_numbers():
    # Гонять один файл — норма вечером. Метка после этого не должна
    # выглядеть как обвал набора с 1298 до 21.
    with _Spot():
        build_stamp.stamp_tests(total=1298, failed=0, seconds=27.38,
                               full=True, printer=lambda t: None)
        build_stamp.stamp_tests(total=21, failed=0, seconds=1.2,
                               full=False, printer=lambda t: None)
        back = build_stamp.read()
        assert back["tests_total"] == "1298", "частичный прогон затёр полный"
        assert back["partial_total"] == "21", "частичный прогон не записан"
        assert back["partial_at"]


def test_tests_writer_admits_it_did_not_see_the_home():
    # Под тестами дом — песочница. Значит прогон обязан сознаться,
    # а не переписать версии хранилищ цифрами из песочницы.
    with _Spot():
        build_stamp.stamp_tests(total=10, failed=0, seconds=1.0,
                               full=True, printer=lambda t: None)
        back = build_stamp.read()
        for key in build_stamp.HOME_KEYS:
            assert back[key] == build_stamp.NOT_SEEN_BY_TESTS, key


def test_start_fills_the_home_block():
    with _Spot():
        build_stamp.stamp_start(printer=lambda t: None)
        back = build_stamp.read()
        assert back["home_at"]
        assert back["home_path"]
        assert back["jarvis_db_ver"]
        assert back["tests_total"] == build_stamp.NOT_SEEN_BY_START


def test_two_writers_keep_each_other_lines():
    with _Spot():
        build_stamp.stamp_tests(total=1298, failed=0, seconds=27.38,
                               full=True, printer=lambda t: None)
        build_stamp.stamp_start(printer=lambda t: None)
        back = build_stamp.read()
        assert back["tests_total"] == "1298", "старт стёр цифры тестов"
        assert back["home_at"], "блок дома не записан"
        assert back["written_by"] == build_stamp.BY_START
        build_stamp.stamp_tests(total=1300, failed=0, seconds=28.0,
                               full=True, printer=lambda t: None)
        again = build_stamp.read()
        assert again["home_at"] == back["home_at"], "прогон стёр блок дома"
        assert again["tests_total"] == "1300"


def test_unknown_lines_are_preserved():
    # Если в будущем кто-то допишет свою строку, её не выкидываем.
    with _Spot() as folder:
        build_stamp.write(written_by="tests", fields={"zzz_своё": "42"},
                         printer=lambda t: None)
        build_stamp.write(written_by="start", printer=lambda t: None)
        assert "zzz_своё = 42" in _text(folder)


# -- Когда всё плохо ------------------------------------------------

def test_failure_never_raises_into_the_caller():
    # Метка — удобство, а не основание уронить прогон тестов.
    def boom(text, spot):
        raise OSError("диск ушёл")

    lines, printer = _log()
    with _Spot():
        with _Patched(build_stamp, "_atomic_write", boom):
            out = build_stamp.write(written_by="tests", printer=printer)
        assert out is None, "ошибку проглотили и соврали про успех"
        assert lines and "не обновили" in lines[0], "отказ промолчали"


def test_busy_file_is_retried():
    # Блокнот или антивирус держит файл: на Windows это PermissionError.
    real = os.replace
    seen = {"n": 0}

    def flaky(src, dst):
        seen["n"] += 1
        if seen["n"] < 3:
            raise PermissionError("файл держит чужой")
        return real(src, dst)

    with _Spot() as folder:
        with _Patched(build_stamp, "REPLACE_PAUSE", 0):
            with _Patched(os, "replace", flaky):
                out = build_stamp.write(written_by="tests",
                                       printer=lambda t: None)
        assert out is not None, "сдались после первого отказа"
        assert seen["n"] == 3, "повторов не было"
        assert (folder / build_stamp.FILE_NAME).exists()


def test_no_temp_leftovers_after_a_hopeless_failure():
    def always(src, dst):
        raise PermissionError("держит навсегда")

    with _Spot() as folder:
        with _Patched(build_stamp, "REPLACE_PAUSE", 0):
            with _Patched(os, "replace", always):
                out = build_stamp.write(written_by="tests",
                                       printer=lambda t: None)
        assert out is None
        assert not _temps(folder), "остались временные обрезки"
        assert not (folder / build_stamp.FILE_NAME).exists(), (
            "половинчатая метка выжила")


def test_no_temp_leftovers_after_success():
    with _Spot() as folder:
        build_stamp.write(written_by="tests", printer=lambda t: None)
        assert not _temps(folder)


def test_broken_stamp_is_not_fatal():
    # Файл испортили руками или оборвали запись выключением питания.
    with _Spot() as folder:
        (folder / build_stamp.FILE_NAME).write_bytes(b"\xff\xfe not a stamp")
        out = build_stamp.write(written_by="tests", printer=lambda t: None)
        assert out is not None, "из-за мусора в файле перестали писать"
        assert build_stamp.read()["written_by"] == "tests"


# -- Границы и отчёт --------------------------------------------------

def test_module_is_not_a_second_writer_of_state():
    src = (ROOT / "core" / "build_stamp.py").read_text(encoding="utf-8")
    assert "atomic_write_json" not in src
    assert "record_snapshot" not in src
    assert "sv.save" not in src
    assert "sv.write(" not in src


def test_default_place_is_the_project_root():
    saved = os.environ.pop(build_stamp.DIR_ENV, None)
    try:
        assert build_stamp.dir_path() == state_version.project_dir()
        assert build_stamp.path().name == build_stamp.FILE_NAME
    finally:
        if saved is not None:
            os.environ[build_stamp.DIR_ENV] = saved


def test_test_run_never_touches_the_real_stamp():
    with _Spot() as folder:
        target = build_stamp.path()
        assert str(target).startswith(str(folder)), "метка ушла не туда"
        assert str(state_version.project_dir()) not in str(target), (
            "тест метит в настоящий файл проекта")


def test_stale_says_nothing_without_numbers():
    with _Spot():
        build_stamp.stamp_start(printer=lambda t: None)
        assert build_stamp.stale() is None, "судим о том, чего не знаем"


def test_stale_notices_code_newer_than_tests():
    with _Spot():
        build_stamp.stamp_tests(total=5, failed=0, seconds=1.0,
                               full=True, printer=lambda t: None)
        with _Patched(build_stamp, "_newest_code_at", lambda: 1.0):
            assert build_stamp.stale() is False, "зря пугаем"
        with _Patched(build_stamp, "_newest_code_at", lambda: 4.0e9):
            assert build_stamp.stale() is True, (
                "код правили после прогона, а метка молчит")


def test_report_gives_doctor_what_it_needs():
    with _Spot():
        build_stamp.stamp_tests(total=7, failed=1, seconds=2.5,
                               full=True, printer=lambda t: None)
        out = build_stamp.report()
        for key in ("path", "exists", "written_at", "written_by", "code_ver",
                    "phase", "step", "tests_total", "tests_failed",
                    "tests_at", "stale"):
            assert key in out, key
        assert out["exists"] is True
        assert out["tests_total"] == "7"
        assert out["tests_failed"] == "1"


def test_report_survives_a_missing_file():
    with _Spot():
        out = build_stamp.report()
        assert out["exists"] is False
        assert out["tests_total"] is None


# -- Запуск без pytest ----------------------------------------------

if __name__ == "__main__":
    passed = 0
    failed = 0
    for name, func in sorted(list(globals().items())):
        if not name.startswith("test_") or not callable(func):
            continue
        try:
            func()
            passed += 1
            print("OK   " + name)
        except Exception as exc:
            failed += 1
            print("FAIL " + name + ": " + type(exc).__name__ + ": " + str(exc))
    print("итог: " + str(passed) + " зелёных, " + str(failed) + " красных")
    sys.exit(1 if failed else 0)
