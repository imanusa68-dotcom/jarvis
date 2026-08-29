# tests/test_fatal_error_reaches_the_exit.py
"""
Сторожа шага 0: смертельная ошибка обязана доходить до выхода.

Что охраняется: при неверном ключе API Джарвис должен ОСТАНОВИТЬСЯ с внятной
ошибкой, а не ходить вечно по кругу переподключений. И наоборот: обычный
обрыв связи обязан вести к переподключению, а не к остановке.

ГЛАВНОЕ ПРАВИЛО ЭТОГО ФАЙЛА
Мало проверить, что `is_fatal_error` умеет отвечать правильно. Она умела и до
шага 0 — а вызывать её было некому. Поэтому здесь проверяется ПУТЬ: доходит ли
исключение из `_run_session` до того места, где его классифицируют. Проверять
только `is_fatal_error()` означало бы проверять полпути и остаться зелёным при
полностью мёртвой ветке — это грабли №4 проекта.

ЗАМЕР, ИЗ КОТОРОГО ВЫРОСЛИ ЭТИ СТОРОЖА (29.08.2026, до шага 0):
    случай                                    куда попадало управление
    1006 abnormal closure (штатный обрыв)     нормальный выход
    API key not valid (ФАТАЛЬНАЯ -> стоп)     нормальный выход   <-- дыра
    Ctrl+C / выключение (CancelledError)      нормальный выход   <-- дыра

Причина была одна: `return uptime` ВНУТРИ `finally` в `_run_session`. Такой
возврат поглощает любое исключение. Из-за этого в цикле `run` были недостижимы
три ветки, а единственный вызов `is_fatal_error` стоял в мёртвом коде.

Тестов на это НЕ БЫЛО ВОВСЕ: 1898 тестов проходили и с мёртвой веткой. Этот
файл закрывает названный долг.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from core.session_manager import (
    ReconnectGuard,
    is_fatal_error,
    is_recoverable_error,
)

ROOT = Path(__file__).resolve().parent.parent


class _FakeAPIError(Exception):
    """Похоже на google.genai.errors.APIError: код лежит отдельным полем."""

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        super().__init__(f"{code} None. {message}")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Устройство кода: возврата из `finally` больше нет
# ─────────────────────────────────────────────────────────────────────────────

def test_run_session_does_not_return_from_finally():
    """
    Главный сторож. Проверяется разбором кода, а не глазами: `return` внутри
    `finally` — это не стилевая придирка, а поглощение всех исключений сразу.
    """
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))

    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_session":
            found = True
            for inner in ast.walk(node):
                if isinstance(inner, ast.Try):
                    for stmt in inner.finalbody:
                        for leaf in ast.walk(stmt):
                            assert not isinstance(leaf, ast.Return), (
                                "в `finally` внутри _run_session снова стоит `return` — "
                                "он поглотит любое исключение, и остановка по фатальной "
                                "ошибке снова станет мёртвым кодом"
                            )
    assert found, "не найден _run_session — сторож потерял охраняемое место"


def test_run_session_still_returns_uptime_somewhere():
    """
    Обратная сторона: убрать `return` целиком тоже нельзя — по времени жизни
    сессии предохранитель решает, считать ли попытку успешной.
    """
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "return self._last_uptime" in src, (
        "_run_session больше не возвращает время жизни сессии — "
        "предохранитель ослепнет и начнёт считать здоровые сессии отказами"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Путь: исключение доходит до классификации
# ─────────────────────────────────────────────────────────────────────────────

def _walk_the_path(exc: BaseException | None):
    """
    Повторяет устройство `_run_session` + `run` в одном месте: уборка в
    `finally`, возврат за его пределами. Возвращает, куда попало управление.
    """
    cleanups: list[str] = []

    async def run_session():
        try:
            if exc is not None:
                raise exc
        finally:
            cleanups.append("cleanup")
        return 5.0

    async def loop_once():
        try:
            uptime = await run_session()
            return "normal", uptime
        except asyncio.CancelledError:
            return "stop_by_owner", None
        except Exception as e:                      # noqa: BLE001
            if is_fatal_error(e):
                return "stop_fatal", None
            return "reconnect", None

    where, uptime = asyncio.run(loop_once())
    return where, uptime, cleanups


@pytest.mark.parametrize(
    "name, exc, expected",
    [
        ("штатный выход", None, "normal"),
        ("обрыв 1006", _FakeAPIError(1006, "abnormal closure [internal]"), "reconnect"),
        ("обрыв 1011", _FakeAPIError(1011, "keepalive ping timeout"), "reconnect"),
        ("неверный ключ", RuntimeError("API key not valid"), "stop_fatal"),
        ("нет прав", RuntimeError("permission denied"), "stop_fatal"),
        ("выключение", asyncio.CancelledError(), "stop_by_owner"),
    ],
)
def test_the_error_reaches_the_place_where_it_is_judged(name, exc, expected):
    """Смертельная ведёт к остановке, обрыв — к переподключению."""
    where, _uptime, _cleanups = _walk_the_path(exc)
    assert where == expected, (
        f"{name}: управление ушло в «{where}», а должно было в «{expected}». "
        "Если здесь всё стало «normal» — вернулся `return` внутри `finally`"
    )


@pytest.mark.parametrize(
    "exc",
    [
        None,
        _FakeAPIError(1006, "abnormal closure [internal]"),
        RuntimeError("API key not valid"),
        asyncio.CancelledError(),
    ],
)
def test_cleanup_runs_exactly_once_on_every_exit(exc):
    """
    Уборка обязана идти всегда и ровно один раз — иначе починка ошибок
    превратилась бы в утечку звуковых потоков и очередей.
    """
    _where, _uptime, cleanups = _walk_the_path(exc)
    assert cleanups == ["cleanup"], (
        f"уборка выполнена {len(cleanups)} раз вместо одного"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Обрыв 1006 не должен считаться загадочным
# ─────────────────────────────────────────────────────────────────────────────

def test_1006_is_a_plain_disconnect_not_a_mystery():
    """
    Живой лог владельца показал `APIError: 1006 None. abnormal closure`.
    Google уронил сессию сам, Джарвис переподключился за 3.0с — то есть по
    сути это штатный обрыв. Но 1006 не считался НИ восстановимым, НИ фатальным
    и потому печатал полный traceback в консоль владельцу.

    Слово "close" из списка здесь не помогает: в "closure" его нет.
    """
    err = _FakeAPIError(1006, "abnormal closure [internal]")
    assert is_recoverable_error(err), (
        "1006 снова не опознан как обрыв — владелец увидит полный traceback "
        "вместо спокойного переподключения"
    )
    assert not is_fatal_error(err), "1006 — это не смертельная ошибка"


@pytest.mark.parametrize(
    "text",
    ["API key not valid", "api_key_invalid", "permission denied",
     "authentication failed", "invalid api key"],
)
def test_fatal_errors_stay_fatal(text):
    """
    Обратная проверка: расширяя список обрывов, легко случайно объявить
    восстановимой ошибку настроек — и тогда Джарвис будет вечно стучаться
    с неверным ключом.
    """
    err = RuntimeError(text)
    assert is_fatal_error(err), f"«{text}» перестала быть смертельной"
    assert not is_recoverable_error(err), (
        f"«{text}» опознана как обрыв — Джарвис будет переподключаться вечно"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. Предохранитель: долгая здоровая сессия — не отказ
# ─────────────────────────────────────────────────────────────────────────────

def test_a_long_session_killed_by_the_network_is_not_a_failure():
    """
    Замер (29.08.2026) показал ловушку самой починки: как только исключение
    начало доходить до `except`, наивный код записал бы отказ, не глядя на
    время жизни. Тогда восемь двухчасовых сессий, оборванных сетью, роняли
    предохранитель — три минуты молчания на здоровой связи.

    Здесь проверяется правило: отказ считается ТОЛЬКО если сессия не успела
    стать устойчивой.
    """
    guard = ReconnectGuard()
    long_life = ReconnectGuard.STABLE_SECONDS + 1.0

    for _ in range(ReconnectGuard.MAX_FAILURES + 1):
        if long_life >= ReconnectGuard.STABLE_SECONDS:
            guard.record_success(long_life)
        else:
            guard.record_failure()
        assert not guard.is_circuit_open(), (
            "предохранитель сработал на здоровых долгих сессиях — "
            "Джарвис замолчит на три минуты без причины"
        )


def test_flapping_short_sessions_still_trip_the_breaker():
    """
    Обратная сторона: предохранитель обязан остаться живым. Если связь мигает
    и сессии рвутся за секунды — пауза нужна, иначе Джарвис будет жечь квоту
    в холостом цикле.
    """
    guard = ReconnectGuard()
    short_life = 4.0

    for _ in range(ReconnectGuard.MAX_FAILURES):
        if short_life >= ReconnectGuard.STABLE_SECONDS:
            guard.record_success(short_life)
        else:
            guard.record_failure()

    assert guard.is_circuit_open(), (
        "предохранитель больше не срабатывает на мигающей связи — "
        "потерян тормоз, который экономит квоту"
    )


def test_the_uptime_survives_the_exception_path():
    """
    Развилка по устойчивости работает только если время жизни посчитано ДО
    броска исключения — то есть в `finally`. Иначе в ветке ошибки оно всегда
    ноль, и любая сессия считается отказом.
    """
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    finally_pos = src.find("self._last_uptime = asyncio.get_event_loop().time()")
    usage_pos = src.find("if self._last_uptime >= ReconnectGuard.STABLE_SECONDS")

    assert finally_pos > 0, "время жизни сессии больше не считается в `finally`"
    assert usage_pos > 0, (
        "в ветке ошибки пропала развилка по устойчивости — долгие здоровые "
        "сессии снова начнут двигать предохранитель"
    )
