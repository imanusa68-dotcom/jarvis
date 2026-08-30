# -*- coding: utf-8 -*-
"""
Сторож шага 4 фазы 0: один Jarvis за раз.

Почему этот файл есть. Ничто не мешало запустить Jarvis дважды: два процесса
делят одну базу ~/.jarvis/jarvis.db, оба открывают микрофон, оба говорят и оба
тратят одну дневную квоту. Ничего не падает — день просто становится странным.

Почему замок именно такой. Крестик окна выполняет os._exit(0) (ui.py:156): ни
`finally`, ни atexit не выполняются никогда. Значит вариант «создать файл при
старте и удалить при выходе» невозможен в принципе: файл переживёт каждое
нормальное закрытие, и владелец получит «уже запущен» тогда, когда ничего не
запущено. Поэтому замок держит операционная система — она снимает его при
смерти процесса всегда, как бы тот ни умер.

Почему табличка лежит отдельно (урок приёмки 2026-08-06). Сначала номер
процесса писался внутрь самого файла замка, а запертый байт стоял далеко —
на смещении 4096. На Windows это не читается: блокировки диапазона там
обязательные и привязаны к дескриптору, а буферизованное чтение Python просит
у системы сразу 8 КБ и задевает запертый байт. Владелец увидел системную
ошибку вместо номера. Теперь табличка — отдельный файл jarvis.lock.info,
который никто не запирает.

Два правила этого файла:
  * Каждый тест работает во временном доме ($JARVIS_STATE_DIR), и перед любым
    захватом стоит проверка «путь действительно временный». Иначе прогон при
    работающем Jarvis трогал бы настоящий замок — а владелец именно так и
    делает: запускает тесты из того же окна.
  * «Второй экземпляр» — это настоящий второй процесс, а не второй файловый
    дескриптор в том же. Только так проверка означает то, что написано на
    этикетке.

Запуск:  python -m pytest tests/test_instance_lock.py -q
или:     python tests/test_instance_lock.py
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import instance_lock                    # noqa: E402
from core.safe_json import STATE_DIR_ENV          # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# Второй Jarvis для теста: берёт замок, отчитывается и ждёт, пока его убьют.
_CHILD = (
    "import os, sys, time; "
    "sys.path.insert(0, os.environ['JV_ROOT']); "
    "from core import instance_lock; "
    "instance_lock.acquire(); "
    "print('HELD', os.getpid(), flush=True); "
    "time.sleep(60)"
)


class _Home:
    """Временный дом. Настоящий ~/.jarvis не трогается ни разу."""

    def __enter__(self) -> "_Home":
        self.tmp = Path(tempfile.mkdtemp(prefix="jv_lock_"))
        self.home = self.tmp / "home"          # намеренно не создаём заранее
        self._saved = os.environ.get(STATE_DIR_ENV)
        os.environ[STATE_DIR_ENV] = str(self.home)
        self._children: list[subprocess.Popen] = []
        return self

    def __exit__(self, *exc) -> bool:
        for child in self._children:
            try:
                child.kill()
                child.wait(timeout=15)
            except Exception:
                pass
            try:
                if child.stdout:
                    child.stdout.close()
            except Exception:
                pass
        instance_lock.release()
        if self._saved is None:
            os.environ.pop(STATE_DIR_ENV, None)
        else:
            os.environ[STATE_DIR_ENV] = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False

    def guard(self) -> Path:
        """Хватать замок можно только внутри песочницы."""
        path = instance_lock.lock_path()
        assert str(path).startswith(str(self.home)), \
            f"тест целится в настоящий замок: {path}"
        return path

    def start_second_jarvis(self) -> subprocess.Popen:
        env = os.environ.copy()
        env[STATE_DIR_ENV] = str(self.home)
        env["JV_ROOT"] = str(ROOT)
        env["PYTHONIOENCODING"] = "utf-8"
        child = subprocess.Popen(
            [sys.executable, "-c", _CHILD], env=env, cwd=str(ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        self._children.append(child)
        first = child.stdout.readline().strip() if child.stdout else ""
        assert first.startswith("HELD"), \
            f"второй процесс не взял замок, сказал: {first!r}"
        return child


def _acquire_within(seconds: float) -> None:
    """Захват с коротким ожиданием: Windows отпускает дескрипторы убитого
    процесса не всегда мгновенно. Запас заведомо больше нормы, но конечен:
    заевший замок всё равно упадёт."""
    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        try:
            instance_lock.acquire()
            return
        except instance_lock.AlreadyRunning as e:
            last = e
            time.sleep(0.1)
    raise AssertionError(f"замок заел: за {seconds} с так и не освободился ({last})")


def _why_empty() -> str:
    """Что именно помешало прочитать табличку. Пустой словарь ничего не
    объясняет — а красный тест обязан называть причину."""
    return instance_lock.last_read_error() or "причина не записана"


def _main_function() -> ast.FunctionDef:
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    raise AssertionError("в main.py нет функции main()")


def _first_call_line(node: ast.AST, dotted: str) -> int | None:
    """Строка первого вызова. Разбор синтаксиса, а не поиск текста: сторож
    не должен находить сам себя в комментарии (урок шага 3)."""
    best = None
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            try:
                name = ast.unparse(sub.func)
            except Exception:
                continue
            if name == dotted or name.endswith("." + dotted):
                if best is None or sub.lineno < best:
                    best = sub.lineno
    return best


# ── 1. Замок живёт в доме, а не в папке проекта ───────────────────────

def test_lock_lives_in_the_state_dir_not_in_the_project():
    with _Home() as box:
        path = box.guard()
        assert path.name == "jarvis.lock"
        assert path.parent == box.home
        assert instance_lock.info_path().parent == box.home
        assert not str(path).startswith(str(ROOT)), \
            "замок оказался в папке сборки — при распаковке нового архива два́ " \
            "Jarvisа из разных папок перестанут видеть друг друга"


# ── 2. Чтение ничего не создаёт ─────────────────────────────────

def test_reading_the_lock_creates_nothing():
    """То же правило, что у настроек шага 2: спросить — не значит насорить."""
    with _Home() as box:
        box.guard()
        assert instance_lock.is_held() is False
        assert instance_lock.read_info() == {}
        assert not box.home.exists(), "чтение создало домашнюю папку"


# ── 3. Второй экземпляр получает отказ и узнаёт номер первого ───────────

def test_second_process_is_refused_and_names_the_holder():
    with _Home() as box:
        box.guard()
        child = box.start_second_jarvis()

        try:
            instance_lock.acquire()
            raise AssertionError(
                "второй экземпляр взял замок — это и есть дыра, ради которой шаг 4")
        except instance_lock.AlreadyRunning as busy:
            assert busy.info.get("pid") == child.pid, \
                f"в замке чужой номер: {busy.info} вместо {child.pid} " \
                f"(чтение таблички: {_why_empty()})"
            assert str(child.pid) in busy.note, \
                "сообщение владельцу не называет номер процесса"
            assert instance_lock.is_held() is False


# ── 4. Самый грубый выход не оставляет заевшего замка ────────────────

def test_hard_kill_leaves_no_stuck_lock():
    """Главный тест шага. Процесс убит без всякой уборки — ровно так же
    заканчивается закрытие окна крестиком (os._exit) и выдернутый шнур.
    Файл остаётся лежать — и это нормально: снимается блокировка, а не файл."""
    with _Home() as box:
        box.guard()
        child = box.start_second_jarvis()
        child.kill()
        child.wait(timeout=15)

        assert instance_lock.lock_path().exists(), \
            "файл замка исчез — значит кто-то его удаляет, а это гонка"
        _acquire_within(5.0)
        assert instance_lock.is_held() is True
        assert instance_lock.read_info().get("pid") == os.getpid(), \
            f"свою же табличку прочитать не удалось ({_why_empty()})"
        instance_lock.release()


# ── 5. Освобождение пускает следующий запуск ────────────────────────

def test_release_lets_the_next_run_in():
    with _Home() as box:
        box.guard()
        instance_lock.acquire()
        assert instance_lock.is_held() is True

        instance_lock.release()
        assert instance_lock.is_held() is False

        instance_lock.acquire()          # второй запуск после честного выхода
        instance_lock.release()
        instance_lock.release()          # повторное освобождение не ломается


# ── 6. Файл замка остаётся чистым жетоном ──────────────────────────

def test_the_lock_file_itself_stays_a_pure_token():
    """Из-за записи внутрь запертого файла шаг 4 не прошёл приёмку с первого
    раза: на Windows запертый файл не читается, потому что буферизованное
    чтение просит у системы сразу 8 КБ и задевает запертый байт. Табличка
    обязана лежать отдельно, а файл замка — оставаться пустым."""
    with _Home() as box:
        box.guard()
        instance_lock.acquire()

        assert instance_lock.info_path() != instance_lock.lock_path()
        assert instance_lock.info_path().name == "jarvis.lock.info"
        assert instance_lock.read_info().get("pid") == os.getpid(), \
            f"табличка не читается, пока замок держим мы сами ({_why_empty()})"

        instance_lock.release()
        raw = instance_lock.lock_path().read_bytes()
        assert b"pid" not in raw, \
            "в файл замка снова кто-то пишет — это и была поломка приёмки"


# ── 7. Владельцу никогда не показывают системную ошибку ────────────

def test_owner_message_never_shows_a_system_error():
    """На приёмке владелец увидел «[Errno 13] Permission denied» — строку,
    с которой ему нечего делать. Такое больше не проходит."""
    blind = instance_lock.AlreadyRunning({}, "[Errno 13] Permission denied")
    note = blind.note
    assert note.strip(), "сообщение владельцу пустое"
    for junk in ("errno", "permission", "denied", "oserror", "traceback"):
        assert junk not in note.lower(), \
            f"владельцу показали техническую строку: {note}"

    named = instance_lock.AlreadyRunning(
        {"pid": 4242, "started_at": "2026-08-06 14:37:24"}, "[Errno 13] denied")
    assert "4242" in named.note and "14:37:24" in named.note


# ── 8. Замок берётся РАНЬШЕ окна ──────────────────────────────

def test_main_takes_the_lock_before_the_window():
    """Иначе второй экземпляр успевает нарисовать окно и только потом умирает."""
    fn = _main_function()
    lock_line = _first_call_line(fn, "instance_lock.acquire")
    window_line = _first_call_line(fn, "JarvisUI")

    assert lock_line is not None, "main() не берёт замок вообще"
    assert window_line is not None, "main() больше не создаёт окно — проверь тест"
    assert lock_line < window_line, \
        f"окно создаётся раньше замка: {window_line} < {lock_line}"


# ── 9. При импорте main.py замок не берётся никогда ──────────────────

def test_the_lock_is_not_taken_at_import_time():
    """main импортируют два теста (golden dispatch и контракт stage3c). Если бы
    замок брался на уровне модуля, прогон при работающем Jarvis краснел целиком."""
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        line = _first_call_line(node, "instance_lock.acquire")
        assert line is None, f"замок берётся при импорте main.py, строка {line}"


if __name__ == "__main__":
    _tests = [value for name, value in sorted(globals().items())
              if name.startswith("test_") and callable(value)]
    for _fn in _tests:
        _fn()
        print(f"OK   {_fn.__name__}")
    print(f"OK: {len(_tests)} passed (standalone)")
