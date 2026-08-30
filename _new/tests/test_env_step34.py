# -*- coding: utf-8 -*-
# tests/test_env_step34.py -- сторожа слоя окружения (шаг 34.1, план Р11).
#
# Главное правило этого файла: ни один тест не зовёт setup() на НАСТОЯЩИХ
# потоках. Иначе один тест перенастроит вывод всему прогону, и краснеть будет
# чужой тест через полчаса. Везде заглушки и try/finally.

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import env  # noqa: E402

CR = chr(13)
LF = chr(10)
BACKSLASH = chr(92)


class _Stub:
    # Поддельный поток: запоминает, что с ним делали.
    def __init__(self, encoding, boom=False):
        self.encoding = encoding
        self.calls = []
        self.boom = boom

    def reconfigure(self, encoding=None, errors=None):
        if self.boom:
            raise OSError("поток отказался")
        self.calls.append((encoding, errors))
        self.encoding = encoding

    def write(self, text):
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False


class _Deaf:
    # Поток без reconfigure -- такой подсовывает pytest и отладчики.
    encoding = "cp1251"

    def write(self, text):
        return len(text)

    def flush(self):
        pass


class _Swap:
    # Подменить потоки и состояние модуля, потом вернуть всё как было.
    def __init__(self, out, err=None):
        self.out = out
        self.err = err if err is not None else _Stub("utf-8")

    def __enter__(self):
        self.old_out = sys.stdout
        self.old_err = sys.stderr
        self.old_done = env._state["setup_done"]
        self.old_streams = dict(env._state["streams"])
        sys.stdout = self.out
        sys.stderr = self.err
        env._state["setup_done"] = False
        env._state["streams"] = {}
        return self

    def __exit__(self, *exc):
        sys.stdout = self.old_out
        sys.stderr = self.old_err
        env._state["setup_done"] = self.old_done
        env._state["streams"] = self.old_streams
        return False


# -- Потоки вывода ----------------------------------------------------

def test_setup_is_idempotent():
    out = _Stub("cp1251")
    with _Swap(out):
        first = env.setup()
        env.setup()
        env.setup()
        second = env.setup()
    assert len(out.calls) == 1, "setup() перенастроил поток больше одного раза: " + str(out.calls)
    assert first == second, "повторный вызов вернул другой ответ"


def test_a_working_console_is_left_alone():
    # Живая консоль владельца уже utf-8. Лезть в неё -- значит сломать то,
    # что работает. Этот тест стережёт главную архитектурную ошибку Р11.
    out = _Stub("UTF-8")
    with _Swap(out):
        answer = env.setup()
    assert out.calls == [], "рабочую консоль трогать нельзя, а её тронули: " + str(out.calls)
    assert "не трогали" in answer["stdout"], "отчёт не говорит, что консоль оставили в покое: " + str(answer)


def test_a_redirected_stream_is_switched_to_utf8():
    # Перенаправление в файл даёт cp1251, а в cp1251 нет наших значков.
    out = _Stub("cp1251")
    with _Swap(out):
        answer = env.setup()
    assert out.calls == [("utf-8", "replace")], "поток не переведен на utf-8 с заменой: " + str(out.calls)
    assert "utf-8" in answer["stdout"], "отчёт молчит о переводе: " + str(answer)


def test_a_stream_that_refuses_does_not_kill_the_run():
    # Если поток отказался -- это не повод не запустить Джарвиса,
    # но и молчать нельзя (I19): причина обязана остаться в отчёте.
    out = _Stub("cp1251", boom=True)
    with _Swap(out):
        answer = env.setup()
        seen = env.report()["streams"]
    assert "OSError" in answer["stdout"], "причина отказа потеряна: " + str(answer)
    assert "OSError" in seen["stdout"], "доктор не увидит отказ: " + str(seen)


def test_a_stream_without_reconfigure_is_left_alone():
    with _Swap(_Deaf()):
        answer = env.setup()
    assert "не умеет" in answer["stdout"], "поток без перенастройки описан неверно: " + str(answer)


def test_no_stream_at_all_is_not_an_error():
    # pythonw.exe и запуск без консоли: sys.stdout равен None.
    with _Swap(None, None):
        answer = env.setup()
        facts = env.report()
    assert "нет" in answer["stdout"], "отсутствие потока описано неверно: " + str(answer)
    assert facts["console_encoding"] == "нет потока", "отчёт врёт про консоль: " + str(facts)


# -- Секреты ------------------------------------------------------------

def test_redact_hides_a_model_key():
    # Ключ собран из кусков и нарочно короткий: живой ключ в тесте -- это
    # ровно та утечка, которую мы лечим.
    fake = "A" + "Iza" + "Sy" + ("b" * 20)
    text = "ошибка запроса, key=" + fake + " конец"
    hidden = env.redact(text)
    assert fake not in hidden, "ключ остался в тексте: " + hidden
    assert "ошибка запроса" in hidden, "вместе с ключом стёрли всю строку: " + hidden
    assert "конец" in hidden, "хвост строки потерян: " + hidden


def test_redact_hides_the_home_path():
    home = str(Path.home())
    text = "база лежит в " + home + BACKSLASH + ".jarvis" + BACKSLASH + "jarvis.db"
    hidden = env.redact(text)
    assert home not in hidden, "имя пользователя утекло в тексте: " + hidden
    assert ".jarvis" in hidden, "вместе с домом стёрли имя файла: " + hidden


def test_redact_never_crashes_on_junk():
    # Её зовут в обработчиках ошибок. Падение там -- потеря причины ошибки.
    for junk in (None, 12, 3.5, b"bytes", ["список"], {"a": 1}, Path(".")):
        answer = env.redact(junk)
        assert isinstance(answer, str), "redact вернул не строку для: " + repr(junk)


# -- Файлы ---------------------------------------------------------------

def test_our_files_are_written_in_utf8_without_cr():
    folder = Path(tempfile.mkdtemp(prefix="jv_env_"))
    target = folder / "note.txt"
    text = "первая строка" + LF + "вторая строка" + LF
    env.write_text(target, text)
    raw = target.read_bytes()
    assert CR.encode("ascii") not in raw, "в наш файл влез возврат каретки"
    assert raw.decode("utf-8") == text, "файл записан не в utf-8"
    assert env.read_text(target) == text, "чтение вернуло не то, что записали"


def test_read_text_survives_broken_bytes():
    # Журнал или чужой конфиг могут быть в cp1251 или просто битыми.
    folder = Path(tempfile.mkdtemp(prefix="jv_env_"))
    target = folder / "broken.txt"
    target.write_bytes(bytes([0xFF, 0xFE, 0x41, 0x42]))
    answer = env.read_text(target)
    assert isinstance(answer, str), "чтение битого файла не вернуло строку"
    assert "AB" in answer, "читаемая часть файла потеряна: " + repr(answer)


# -- Отчёт -----------------------------------------------------------------

def test_report_names_all_three_channels():
    facts = env.report()
    for key in ("console_encoding", "locale_encoding", "fs_encoding", "utf8_mode",
                "python", "platform", "setup_done", "streams"):
        assert key in facts, "в отчёте об окружении нет ключа " + key
    assert isinstance(facts["utf8_mode"], int), "режим utf-8 должен быть числом"


def test_env_creates_no_files_of_its_own():
    # Г-3: слой окружения не пишет ничего сам по себе.
    folder = Path(tempfile.mkdtemp(prefix="jv_env_"))
    before = sorted(os.listdir(folder))
    old_cwd = Path.cwd()
    try:
        os.chdir(folder)
        with _Swap(_Stub("cp1251")):
            env.setup()
            env.report()
            env.redact("проверка")
            env.redirection_is_safe()
    finally:
        os.chdir(old_cwd)
    after = sorted(os.listdir(folder))
    assert before == after, "слой окружения создал файлы: " + str(after)


# -- Связь с главным файлом --------------------------------------------

def test_main_sets_up_the_environment_before_it_speaks():
    # Модуль, который никто не зовёт, -- мёртвый модуль. Здесь стережётся
    # не только факт вызова, но и его место: после замка настраивать потоки
    # поздно -- сообщение об отказе замка уже уйдёт в старой кодировке.
    text = (ROOT / "main.py").read_text(encoding="utf-8", errors="replace")
    assert "env.setup()" in text, "main.py не зовёт слой окружения"
    where_setup = text.index("env.setup()")
    marker = "instance_lock.acquire("
    if marker in text:
        assert where_setup < text.index(marker), "настройка окружения позже замка"


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
