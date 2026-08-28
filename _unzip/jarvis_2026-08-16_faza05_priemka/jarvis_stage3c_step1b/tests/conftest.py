# tests/conftest.py
# The test suite must describe THIS project, not the machine it happens to run
# on.
#
# The failure that forced this file into existence: test_resolve_active_app_from_snapshot
# fed the awareness layer a fake Word window and asked which application was
# active — and got back "C:\\Windows\\System32\\cmd.exe - python -m pytest -q",
# the terminal the suite itself was running in. The test was not wrong and the
# code was not wrong; the layer simply read the real screen because nobody had
# told it not to. Three separate attempts to fix that by teaching the code to
# recognise its own console failed, because recognising the console only changes
# WHICH real window leaks in.
#
# The rule, applied once, for every test: the live screen readers return
# "nothing here", so the only facts a test can see are the ones it supplied
# itself. A test that wants a window ingests one; a test that wants a live read
# patches that seam explicitly.

import pytest


@pytest.fixture(autouse=True)
def no_live_screen(monkeypatch):
    """Cut every path from the test suite to the real desktop."""
    try:
        from core.awareness import _inspectors
    except Exception:                      # awareness not importable here
        yield
        return

    # Live foreground read and window enumeration — the two doors to the OS.
    monkeypatch.setattr(_inspectors, "_live_foreground", lambda: None, raising=False)
    monkeypatch.setattr(_inspectors, "_list_windows", lambda: [], raising=False)
    # Own-window detection reads real process handles; without a real desktop
    # there is nothing to detect.
    monkeypatch.setattr(_inspectors, "console_window", lambda: 0, raising=False)
    monkeypatch.setattr(_inspectors, "process_window_handles",
                        lambda: frozenset(), raising=False)

    # Per-test state: caches, the remembered document, the answer journal.
    try:
        _inspectors.reset()
    except Exception:
        pass
    try:
        from core.awareness import _perception
        _perception.reset()
    except Exception:
        pass

    yield

    try:
        _inspectors.reset()
    except Exception:
        pass
    try:
        from core.awareness import _perception
        _perception.reset()
    except Exception:
        pass


# -- Метка сборки (шаг 33.4) ---------------------------------------
# Число тестов знает только прогон, поэтому BUILD.txt дописывается
# в конце. Два правила: хук никогда не красит прогон, и частичный
# прогон не затирает цифры полного.

def pytest_sessionstart(session):
    import time
    try:
        session.config._jv_started = time.time()
    except Exception:
        pass
    # Шаг 35.3: слепок настоящего дома ДО прогона.
    try:
        session.config._jv_home_before = home_fingerprint()
    except Exception:
        session.config._jv_home_before = None


def pytest_sessionfinish(session, exitstatus):
    import time
    # Шаг 35.3: прогон не имеет права оставить след в доме владельца.
    # Сторож не красит отдельный тест — он красит ВЕСЬ прогон, потому
    # что виноват может быть любой из тысячи тестов.
    try:
        before = getattr(session.config, '_jv_home_before', None)
        if before is not None:
            changed = _diff_fingerprints(before, home_fingerprint())
            if changed:
                _say('[ДОМ] прогон изменил настоящий ~/.jarvis: '
                     + ', '.join(changed)
                     + ' (если в это время работал main.py — это не ошибка)',
                     '[HOME] the run modified the real ~/.jarvis: '
                     + ', '.join(changed))
                session.exitstatus = 1
    except Exception:
        pass
    try:
        from core import build_stamp
    except Exception:
        return
    try:
        total = int(getattr(session, 'testscollected', 0) or 0)
        failed = int(getattr(session, 'testsfailed', 0) or 0)
        started = getattr(session.config, '_jv_started', None)
        seconds = (time.time() - started) if started else 0.0
        params = getattr(session.config, 'invocation_params', None)
        raw = [str(a) for a in (getattr(params, 'args', ()) or ())
               if not str(a).startswith('-')]
        full = (not raw) or raw == ['tests']
        build_stamp.stamp_tests(total=total, failed=failed,
                                seconds=seconds, full=full)
    except Exception:
        return


# ── Шаг 35: один дом на тест, ноль утечек ─────────────────────────
# Почему дом подменяется на КАЖДЫЙ тест, а не один раз на прогон.
# Три модуля (core/audit_log, core/state_version, core/action_log)
# устроены так, что JARVIS_STATE_DIR снимает с них предохранитель
# «под тестами в дом не пиши». Одна общая подмена на весь прогон сняла
# бы предохранитель и посадила все тесты писать в ОДНУ общую папку —
# то есть создала бы ровно ту утечку, которую мы лечим. Свой дом на
# тест снимает предохранитель безопасно и переживёт параллельный
# прогон, когда он появится.
#
# Файлу, которому нужно состояние, переживающее соседние тесты,
# ставится метка @pytest.mark.shared_home — тогда дом общий на файл.

import os
import tempfile
from pathlib import Path

_shared_homes = {}

# Эти модули кешируют соединение или размер файла ПРОШЛОГО дома.
# Без сброса тест получил бы новый дом, а писал бы в предыдущий.
# consent_runtime.reset() трогает только запасное соединение и не
# сбрасывает подмену, поставленную тестом, — проверено по коду.
_HOME_CACHES = (
    # ПОРЯДОК В ЭТОМ СПИСКЕ ЗНАЧИМ, и это куплено падением прогона 20.08.2026
    # (access violation, а не красный тест). Очередь задач обязана
    # остановиться и дождаться своих работников ПЕРВОЙ: иначе работник пишет
    # в соединение, которое касса в это же мгновение закрывает, а обращение к
    # закрытому соединению — авария на уровне C, то есть падение всего
    # процесса без объяснений.
    ("agent.task_queue", "reset_for_tests"),
    ("core.action_log", "reset"),
    ("core.audit_log", "reset"),
    ("actions.fileops_bridge", "reset"),
    ("core.consent_runtime", "reset"),
    # Шаг 1.1 фазы 1: решение «копия не вышла — схему не правим» держится
    # защёлкой на ЗАПУСК, а весь прогон — один запуск. Без сброса первый
    # тест с неудачной копией выключил бы механизм для всех следующих, и
    # они были бы зелёными по неверной причине.
    ("core.store", "reset_schema_state"),
    # Блок 3: та же болезнь, тот же рецепт. Номер запуска и счётчик шагов
    # живут на процесс, а прогон — один процесс. Без сброса номер первого
    # теста достался бы всем, и шаги считались бы сквозь чужие тесты
    # (наступил 18.08.2026: ждал шаги 1-4, получил 2-5).
    ("core.task_context", "reset_for_tests"),
    # Блок 5: «про поломку учёта сказали один раз» — тоже защёлка на процесс.
    ("core.metering", "reset_for_tests"),
    # Блок 6: у чёрного ящика на процессе живут ЧЕТЫРЕ вещи, и каждая без
    # сброса испортила бы соседний тест: соединение к ПРОШЛОМУ дому, защёлка
    # «брошенные записи сметены», защёлка «за эти сутки уже убрано» и счётчик
    # потерь. Та же болезнь и тот же рецепт, что у трёх пунктов выше.
    ("core.blackbox", "reset_for_tests"),
    # Блок 7: касса записи держит соединение и числа на процесс. Без сброса
    # тест писал бы в дом предыдущего теста — та же болезнь, что у всех выше.
    ("core.writer", "reset_for_tests"),
    # Блок 9: у кассы состояния на процессе живёт защёлка «осиротевшие
    # временные файлы убраны». Без сброса первый же тест выключил бы уборку
    # для всех следующих — та же болезнь, что у пунктов выше.
    ("core.safe_json", "reset_for_tests"),
    # Блок 10: у напоминаний на процессе живут ТРИ защёлки — «старый файл уже
    # перенесён», «за эти сутки уже убрано» и отметка «владелец только что
    # говорил». Без сброса первый тест, где владелец «отозвался», отменил бы
    # повторы во всех следующих, и они были бы зелёными по неверной причине.
    ("actions.reminder", "reset_for_tests"),
    ("core.scheduler", "reset_for_tests"),
)


def _reset_home_caches():
    for mod_name, fn_name in _HOME_CACHES:
        try:
            mod = __import__(mod_name, fromlist=["*"])
            getattr(mod, fn_name)()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def jarvis_home(tmp_path_factory, monkeypatch, request):
    """Каждый тест живёт в своём доме. Настоящий ~/.jarvis не трогается."""
    if request.node.get_closest_marker("shared_home") is not None:
        key = getattr(getattr(request.node, "module", None), "__name__", "?")
        home = _shared_homes.get(key)
        if home is None:
            home = tmp_path_factory.mktemp("jv_home_shared")
            _shared_homes[key] = home
    else:
        home = tmp_path_factory.mktemp("jv_home")
    monkeypatch.setenv("JARVIS_STATE_DIR", str(home))
    _reset_home_caches()
    # Окно «пачки удалений» живёт в глобальной переменной модуля и
    # переживает границу теста на 180 секунд. Из-за него сторож
    # test_a_broken_consent_store_does_not_open_the_gate был зелёным
    # только тогда, когда до него никто ничего не удалял (шаг 35.2).
    try:
        from core import security
        security.reset_delete_burst()
    except Exception:
        pass
    yield
    _reset_home_caches()


# ── Сторож настоящего дома (шаг 35.3) ─────────────────────────────
# Только чтение. Папку не создаём: нет дома — сравнивать нечего.

def home_fingerprint(home=None):
    """Слепок дома: имя -> (размер файла или -1 у папки, время)."""
    root = Path(home) if home is not None else Path.home() / ".jarvis"
    out = {}
    try:
        entries = list(os.scandir(root))
    except Exception:
        return out
    for e in entries:
        try:
            st = e.stat()
            out[e.name] = (-1 if e.is_dir() else st.st_size, int(st.st_mtime))
        except Exception:
            out[e.name] = (-2, -2)
    return out


def _diff_fingerprints(before, after):
    return [name for name in sorted(set(before) | set(after))
            if before.get(name) != after.get(name)]


def _say(text, ascii_text):
    try:
        print(text)
    except Exception:
        try:
            print(ascii_text)
        except Exception:
            pass
