# -*- coding: utf-8 -*-
# tests/test_doctor_step34.py -- сторожа доктора (шаг 34.2, план Р11).
#
# Доктор запускается ОТДЕЛЬНЫМ процессом с JARVIS_STATE_DIR на временную
# папку. Две причины: импорт доктора в общий прогон тащит чужие модули,
# а главное -- так проверяется ИМЕННО то, что увидит владелец в командной
# строке. Запусков три на весь файл, а не по одному на тест: прогон и так
# разбух до 59 секунд.
#
# Грабля, на которую я уже наступил живьём 15.08.2026: сторож "доктор не
# открывает базу" покраснел, найдя слово sqlite3 в КОММЕНТАРИИ-запрете
# самого доктора. Поэтому сторожа смотрят на КОД без строк-комментариев.

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DOCTOR = ROOT / "tools" / "doctor.py"
FAKE_KEY = "A" + "Iza" + "Sy" + ("z" * 20)
LF = chr(10)
_CACHE = {}


def _run(state_dir):
    variables = dict(os.environ)
    variables["JARVIS_STATE_DIR"] = str(state_dir)
    variables["PYTHONUTF8"] = "1"
    variables["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(DOCTOR)],
        cwd=str(ROOT), env=variables, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=180,
    )


def _listing(folder):
    out = []
    for path in sorted(Path(folder).rglob("*")):
        out.append(str(path.relative_to(folder)) + "|" + (str(path.stat().st_size) if path.is_file() else "dir"))
    return out


def _rich():
    # Один богатый дом и один запуск на все тесты о содержании отчёта.
    if "rich" in _CACHE:
        return _CACHE["rich"]
    home = Path(tempfile.mkdtemp(prefix="jv_doc_"))

    header = bytearray(100)
    header[0:16] = b"SQLite format 3" + bytes([0])
    header[60:64] = (6).to_bytes(4, "big")
    (home / "jarvis.db").write_bytes(bytes(header))

    (home / "STATE.json").write_text(json.dumps({
        "versions": {"db": 6, "memory": 2},
        "last_run": {"at": "2026-08-15T12:00:00", "clean_exit": False, "path": "C:/gde-to"},
        "snapshots": [{"id": "a"}, {"id": "b"}],
    }), encoding="utf-8")

    (home / "jarvis.lock.info").write_text(
        json.dumps({"pid": 4242, "at": "2026-08-15T12:00:00", "note": "key=" + FAKE_KEY}),
        encoding="utf-8")

    (home / "state_backups").mkdir()
    (home / "state_backups" / "chuzhaya_papka").mkdir()

    (home / "logs").mkdir()
    (home / "logs" / "gate-audit.jsonl").write_text(
        json.dumps({"event": "state_rollback", "at": "2026-08-15T12:27:26", "result": "done"}) + LF,
        encoding="utf-8")

    before = _listing(home)
    proc = _run(home)
    after = _listing(home)
    _CACHE["rich"] = (home, proc, before, after)
    return _CACHE["rich"]


# -- Что видит владелец ------------------------------------------------

def test_doctor_shows_nine_sections():
    home, proc, before, after = _rich()
    assert proc.returncode == 0, "доктор вышел с кодом " + str(proc.returncode) + ": " + proc.stderr[-400:]
    for number in range(1, 10):
        assert "== " + str(number) + ". " in proc.stdout, "нет раздела " + str(number) + " в отчёте"
    assert "раздел упал" not in proc.stdout, "какой-то раздел упал: " + proc.stdout


def test_doctor_reads_the_db_version_without_opening_it():
    home, proc, before, after = _rich()
    assert "user_version): 6" in proc.stdout, "версия схемы не прочитана из заголовка"
    for suffix in ("-wal", "-shm"):
        assert not (home / ("jarvis.db" + suffix)).exists(), "доктор открыл базу и создал " + suffix


def test_doctor_notices_a_stranger_folder():
    home, proc, before, after = _rich()
    assert "без манифеста" in proc.stdout, "чужая папка в снимках не замечена"
    assert "ЗАМЕЧАНИЯ" in proc.stdout, "замечания не показаны списком"


def test_doctor_hides_secrets():
    home, proc, before, after = _rich()
    assert FAKE_KEY not in proc.stdout, "ключ из записки замка вывалился на экран"
    assert "pid 4242" in proc.stdout, "записка замка вообще не прочитана"


def test_doctor_says_out_loud_that_it_did_not_check_liveness():
    home, proc, before, after = _rich()
    assert "НЕ ПРОВЕРЯЛИ" in proc.stdout, "доктор молчит о том, что живость процесса не проверена"


# -- Главное: ничего не меняет ---------------------------------------

def test_doctor_changes_nothing_in_a_full_home():
    home, proc, before, after = _rich()
    assert before == after, "доктор изменил дом: было " + str(before) + ", стало " + str(after)
    assert "ничего не записал" in proc.stdout, "доктор не сказал, что ничего не менял"


def test_doctor_does_not_create_the_home():
    # Самый важный случай: владелец зовёт доктора ДО первого запуска.
    folder = Path(tempfile.mkdtemp(prefix="jv_doc_")) / "ещё-нет"
    proc = _run(folder)
    assert proc.returncode == 0, "доктор упал без дома: " + proc.stderr[-400:]
    assert not folder.exists(), "доктор создал дом, хотя не имеет права ничего писать"
    assert "до первого запуска" in proc.stdout, "пустой дом описан как беда, а это норма"


def test_doctor_leaves_a_broken_state_file_alone():
    # safe_json на битом файле уносит его в карантин и пишет копию.
    # Доктор обязан читать голым json.loads и не лечить пациента.
    home = Path(tempfile.mkdtemp(prefix="jv_doc_"))
    spot = home / "STATE.json"
    spot.write_text("{это не json", encoding="utf-8")
    before = _listing(home)
    raw_before = spot.read_bytes()
    proc = _run(home)
    assert proc.returncode == 0, "доктор упал на битом файле: " + proc.stderr[-400:]
    assert spot.read_bytes() == raw_before, "доктор переписал битый файл состояния"
    assert _listing(home) == before, "доктор наплодил файлов вокруг битого: " + str(_listing(home))
    assert "БИТЫЙ" in proc.stdout, "доктор не сказал, что файл битый"


# -- Сторожа устройства (читают исходник) ------------------------------

def _source():
    return DOCTOR.read_text(encoding="utf-8", errors="replace")


def _code():
    # Исходник без строк-комментариев. Иначе сторож ловит слова из
    # собственных запретов доктора и краснеет на честности.
    lines = []
    for line in _source().splitlines():
        if line.lstrip().startswith("#"):
            continue
        lines.append(line)
    return LF.join(lines)


def test_every_print_goes_through_the_redactor():
    text = _code()
    assert text.count("print(") == 1, "в докторе больше одного print -- секрет утечёт мимо сокрытия"
    assert "print(env.redact(" in text, "единственный print не проходит через сокрытие"


def test_doctor_never_opens_the_database_or_takes_the_lock():
    text = _code()
    for forbidden in ("sqlite3", ".acquire(", "open_store(", "state_version.load", "load_json"):
        assert forbidden not in text, "доктор делает запрещённое: " + forbidden


def test_the_five_bans_are_written_down_in_the_doctor():
    # Запреты должны жить в шапке файла: через месяц я сам не вспомню,
    # почему доктор читает байты заголовка вместо обычного запроса.
    text = _source()
    assert "ПЯТЬ ЗАПРЕТОВ" in text, "из шапки доктора пропали запреты"


if __name__ == "__main__":
    failed = 0
    for name in sorted(globals()):
        if not name.startswith("test_"):
            continue
        try:
            globals()[name]()
            print("OK   " + name)
        except Exception as exc:
            failed += 1
            print("FAIL " + name + ": " + type(exc).__name__ + ": " + str(exc))
    print("итог: " + str(len([n for n in globals() if n.startswith("test_")]) - failed) + " зелёных, " + str(failed) + " красных")
    sys.exit(1 if failed else 0)
