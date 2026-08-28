# -*- coding: utf-8 -*-
"""
Golden-сценарии этапа 0 (merge-гейт миграции MARK XXXV → MARK XXXVI).

Фиксируют ТЕКУЩЕЕ поведение диспетчеризации и security-гейта:
main.JarvisLive._execute_tool (главный путь) и agent.executor._call_tool
(агентный путь) — включая известные дыры и баги, помеченные KNOWN-HOLE /
KNOWN-BAD. Эти пометки — контракт для следующих этапов: этап 1 (единый
dispatch) обязан ИЗМЕНИТЬ помеченные сценарии осознанно, а не молча.

Хендлеры замоканы на границе main-неймспейса / actions-модулей: golden
проверяет решения гейта и маршрутизацию, не сами тулы (их поведение —
юнит-тесты). LLM не используется. Голосовые interruption-кейсы уровня
сессии здесь отсутствуют намеренно: barge-in в прототипе невозможен
(микрофон глушится на время речи), перебивание существует только текстом;
сессионная обвязка FakeVoiceSession появится на этапе 5.

Запуск: uv run pytest tests/golden -q
"""

import asyncio
import sys
from types import SimpleNamespace

import pytest

# Диспетчер печатает эмодзи; capture отключён (-p no:capture), консоль Windows —
# cp1251. Без reconfigure каждый print роняет тест UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import main as jmain
import agent.executor as jexec

# Stage 3C: этот файл проверяет РЕШЕНИЯ гейта, а решение по `confirmed`
# зависит от режима подтверждений. Раньше сценарий ниже читал, какой режим
# случайно стоит на машине, и краснел от переключения тумблера без единой
# изменённой строки исходника. Режим теперь называется вслух.
try:
    from core.feature_flags import durable_consent_enabled as _dce
    _CONSENT_ON = bool(_dce())
except Exception:  # флага нет — значит точно легаси
    _CONSENT_ON = False


# ── Обвязка ──────────────────────────────────────────────────────────────────

class FakeUI:
    def __init__(self):
        self.muted = False
        self.screen_control = False
        self.states = []

    def set_state(self, s):
        self.states.append(s)

    def write_log(self, msg):
        pass


HANDLERS = [
    "open_app", "weather_action", "browser_control", "file_controller",
    "reminder", "cmd_control", "desktop_control", "code_helper",
    "web_search_action", "open_search_source", "computer_control",
    "game_updater", "youtube_video", "screen_process",
]


@pytest.fixture
def jarvis(monkeypatch):
    jl = jmain.JarvisLive.__new__(jmain.JarvisLive)  # без __init__: ни клиента, ни awareness
    jl.ui = FakeUI()
    jl.speak = lambda *a, **k: None
    calls = []

    def recorder(name):
        def handler(*args, **kwargs):
            calls.append((name, args, kwargs))
            return f"[{name} executed]"
        return handler

    for h in HANDLERS:
        monkeypatch.setattr(jmain, h, recorder(h))
    monkeypatch.setattr(jmain, "update_memory", recorder("update_memory"))
    return jl, calls


def run_tool(jl, name, args):
    fc = SimpleNamespace(name=name, args=args, id="golden-1")
    resp = asyncio.run(jl._execute_tool(fc))
    return resp.response["result"]


def handler_called(calls, name):
    return any(c[0] == name for c in calls)


# ── Голоса сценариев: (id, tool, args, ожидание) ─────────────────────────────
# ожидание: ("exec", handler)      — гейт пропустил, хендлер вызван
#           ("confirm",)           — CONFIRMATION_REQUIRED, хендлер НЕ вызван
#           ("blocked",)           — SECURITY-блок гейта, хендлер НЕ вызван
#           ("screen_off",)        — интерактив при выключенном SCREEN
#           ("result", substring)  — специальный детерминированный ответ

SCENARIOS = [
    # -- разрешённые read/auto тулы главного пути --
    ("open_app",            "open_app",        {"app_name": "notepad"},                    ("exec", "open_app")),
    ("weather",             "weather_report",  {"city": "Moscow"},                         ("exec", "weather_action")),
    ("web_search",          "web_search",      {"query": "python"},                        ("exec", "web_search_action")),
    ("browser",             "browser_control", {"action": "search", "browser": "chrome",
                                                "query": "x"},                             ("exec", "browser_control")),
    ("reminder",            "reminder",        {"date": "2026-07-20", "time": "10:00",
                                                "message": "x"},                           ("exec", "reminder")),
    ("fc_list",             "file_controller", {"action": "list", "path": "desktop"},      ("exec", "file_controller")),
    ("fc_read",             "file_controller", {"action": "read", "path": "desktop",
                                                "name": "a.txt"},                          ("exec", "file_controller")),
    ("fc_copy",             "file_controller", {"action": "copy", "path": "desktop",
                                                "name": "a.txt"},                          ("exec", "file_controller")),
    ("yt_get_info",         "youtube_video",   {"action": "get_info", "url": "u"},         ("exec", "youtube_video")),
    ("yt_trending",         "youtube_video",   {"action": "trending"},                     ("exec", "youtube_video")),
    ("desktop_list",        "desktop_control", {"action": "list"},                         ("exec", "desktop_control")),
    ("code_explain",        "code_helper",     {"action": "explain", "code": "1+1"},       ("exec", "code_helper")),
    ("games_list",          "game_updater",    {"action": "list"},                         ("exec", "game_updater")),
    ("cmd_safe",            "cmd_control",     {"task": "show disk usage"},                ("exec", "cmd_control")),
    ("cc_screenshot",       "computer_control", {"action": "screenshot"},                  ("exec", "computer_control")),

    # -- confirm-гейт (policy=confirm без confirmed) --
    ("fc_write_confirm",    "file_controller", {"action": "write", "path": "desktop",
                                                "name": "a.txt", "content": "x"},          ("confirm",)),
    ("fc_move_confirm",     "file_controller", {"action": "move", "path": "desktop",
                                                "name": "a.txt", "new_path": "documents"}, ("confirm",)),
    ("fc_rename_confirm",   "file_controller", {"action": "rename", "path": "desktop",
                                                "name": "a.txt", "new_name": "b.txt"},     ("confirm",)),
    # param-aware промоушен: danger-слово в свободном тексте cmd_control
    ("cmd_danger_confirm",  "cmd_control",     {"task": "delete all temp files"},          ("confirm",)),

    # -- Stage 3C: выдуманный моделью confirmed=true БОЛЬШЕ НЕ ОТКРЫВАЕТ ДВЕРЬ --
    # Проверка перевёрнута НАМЕРЕННО. При включённых талонах единственный способ
    # подтвердить — echo реального consent_id, который выдал сам гейт. Булев флаг
    # модель может просто заявить, поэтому он бессилен. При выключенном флаге
    # живёт легаси-путь, и там старое поведение сохраняется.
    ("fc_write_confirmed_is_powerless",
                            "file_controller", {"action": "write", "path": "desktop",
                                                "name": "a.txt", "content": "x",
                                                "confirmed": True},
                                               (("confirm",) if _CONSENT_ON
                                                else ("exec", "file_controller"))),

    # Stage 2.6: delete is no longer blocked - it is reversible (staged copy +
    # Recycle Bin), so it went from "blocked" to "must be confirmed first".
    ("fc_delete_confirm",   "file_controller", {"action": "delete", "path": "desktop",
                                                "name": "a.txt"},                          ("confirm",)),

    # -- blocked политикой --
    ("yt_play_blocked",     "youtube_video",   {"action": "play", "query": "x"},           ("blocked",)),
    ("desk_org_blocked",    "desktop_control", {"action": "organize"},                     ("blocked",)),
    ("code_run_blocked",    "code_helper",     {"action": "run", "description": "x"},      ("blocked",)),
    ("games_install_block", "game_updater",    {"action": "install", "game_name": "X"},    ("blocked",)),
    ("dev_agent_blocked",   "dev_agent",       {"description": "x"},                       ("blocked",)),
    ("flight_blocked",      "flight_finder",   {"origin": "a", "destination": "b",
                                                "date": "2026-08-01"},                     ("blocked",)),
    # KNOWN-BAD: open_search_source отсутствует в SECURITY_POLICY, хотя объявлен
    # в TOOL_DECLARATIONS и описан в ARCHITECTURE.md как allowed → unknown-блок.
    # Решение о судьбе тула — этап 1; до тех пор фиксируем фактическое поведение.
    ("oss_known_bad",       "open_search_source", {"number": 1},                           ("blocked",)),

    # -- SCREEN-тоггл --
    ("cc_click_screen_off", "computer_control", {"action": "screen_click",
                                                 "description": "OK button"},              ("screen_off",)),

    # -- awareness-тулы при выключенном слое (детерминированный ответ) --
    ("sysctx_off",          "system_context",   {},                       ("result", "Слой осознания системы")),
    ("resolve_off",         "resolve_reference", {"kind": "file"},        ("result", "Слой осознания системы")),

    # -- screen_process: daemon-поток, немедленный ответ-инструкция --
    ("screen_process",      "screen_process",   {"text": "что на экране"},
                                                ("result", "Vision module activated")),
]


@pytest.mark.parametrize("sid,tool,args,expect", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_golden_main_dispatch(jarvis, sid, tool, args, expect):
    jl, calls = jarvis
    result = run_tool(jl, tool, dict(args))

    kind = expect[0]
    if kind == "exec":
        assert handler_called(calls, expect[1]), f"{sid}: хендлер {expect[1]} не вызван; result={result!r}"
        # strip confirmed: хендлер никогда не видит гейтовый флаг
        for _, a, k in calls:
            params = k.get("parameters") or (a[0] if a else {})
            if isinstance(params, dict):
                assert "confirmed" not in params, f"{sid}: confirmed протёк в хендлер"
    elif kind == "confirm":
        assert "CONFIRMATION_REQUIRED" in result, f"{sid}: нет запроса подтверждения; result={result!r}"
        assert not calls, f"{sid}: хендлер вызван до подтверждения: {calls}"
    elif kind == "blocked":
        assert result.startswith("SECURITY"), f"{sid}: нет SECURITY-блока; result={result!r}"
        assert not calls, f"{sid}: хендлер вызван вопреки блокировке: {calls}"
    elif kind == "screen_off":
        assert "Screen control is currently disabled" in result, f"{sid}: {result!r}"
        assert not calls, f"{sid}: интерактив выполнен при SCREEN OFF"
    elif kind == "result":
        assert expect[1] in result, f"{sid}: ожидали {expect[1]!r} в {result!r}"


def test_golden_screen_on_allows_interactive(jarvis):
    jl, calls = jarvis
    jl.ui.screen_control = True
    result = run_tool(jl, "computer_control", {"action": "screen_click", "description": "OK"})
    assert handler_called(calls, "computer_control"), result


def test_golden_save_memory_now_asks_the_door(jarvis, monkeypatch):
    """save_memory СПРАШИВАЕТ у двери — и всё равно пишет молча (фаза 1в).

    РЕШЕНИЕ ИЗМЕНЕНО ОСОЗНАННО, 28.08.2026, с согласия владельца.

    Этот тест раньше назывался `test_golden_save_memory_pregate` и утверждал
    ОБРАТНОЕ: «save_memory намеренно идёт ДО гейта», с проверкой
    `assert not gate_hits`. Так было записано в STAGE0-PLAN.md:137, и это
    было честной фиксацией фактического поведения этапа 0.

    ПОЧЕМУ ПЕРЕПИСАН, А НЕ «ПОДКРУЧЕН». Измерение на живой машине владельца
    показало цену того решения: голосовое «запомни, что я не пью кофе после
    шести» память записало, а в журнале двери не появилось НИ ОДНОЙ строки.
    Память оказалась единственным местом в доме, куда можно положить факт, не
    оставив следа. Для фазы 2 (под-агенты работают без владельца) это
    неприемлемо: забор I12/Г-3 живёт ВНУТРИ двери, и пока дверь недостижима,
    забор для этого пути не работает.

    Переименование намеренное: старое имя врало бы о том, что тест проверяет.
    Молча заменить `assert not gate_hits` на обратное — это грабли «починил
    молча»; правка обязана быть видна в истории.

    ЧТО ЗАКРЕПЛЯЕТСЯ ЗДЕСЬ (три вещи, и все три важны):
      1. дверь СПРОШЕНА — иначе забор под-агента для этого пути мёртв;
      2. владельцу по-прежнему РАЗРЕШЕНО — риск не поднимали, вопросов нет;
      3. ответ по-прежнему `silent` — Джарвис не отчитывается вслух.
    """
    jl, calls = jarvis
    gate_hits = []
    import core.security as sec
    real = sec.check_tool_call
    monkeypatch.setattr(sec, "check_tool_call",
                        lambda *a, **k: gate_hits.append(a) or real(*a, **k))
    fc = SimpleNamespace(name="save_memory",
                         args={"category": "notes", "key": "k", "value": "v"}, id="g")
    resp = asyncio.run(jl._execute_tool(fc))
    assert gate_hits, "save_memory обошёл дверь — фаза 1в откатилась"
    assert handler_called(calls, "update_memory"), (
        "дверь спрошена, но запись не состоялась — владельцу отказали, "
        "хотя риск не меняли")
    assert resp.response.get("silent") is True, (
        "потерян silent — Джарвис начнёт вслух отчитываться о каждой записи")


def test_golden_gate_is_actually_called(jarvis, monkeypatch):
    """Страховка от fail-open: гейт реально вызывается на обычном туле."""
    jl, _ = jarvis
    gate_hits = []
    import core.security as sec
    real = sec.check_tool_call
    monkeypatch.setattr(sec, "check_tool_call",
                        lambda *a, **k: gate_hits.append(a) or real(*a, **k))
    run_tool(jl, "open_app", {"app_name": "notepad"})
    assert gate_hits, "check_tool_call не был вызван — гейт отключён?"


def test_golden_gate_failclosed(jarvis, monkeypatch):
    """Этап 1: при исключении в гейте диспетчер FAIL-CLOSED — хендлер НЕ выполняется.

    Раньше (этап 0) main.py делал fail-open ('deferring to local guards') и тул
    исполнялся мимо гейта. Этап 1 осознанно заменил это на fail-closed: любая
    ошибка гейта возвращает SECURITY-ответ, действие не запускается.
    """
    jl, calls = jarvis
    import core.security as sec

    def boom(*a, **k):
        raise RuntimeError("gate exploded")

    monkeypatch.setattr(sec, "check_tool_call", boom)
    result = run_tool(jl, "file_controller",
                      {"action": "write", "path": "desktop", "name": "a.txt", "content": "x"})
    assert not handler_called(calls, "file_controller"), result  # хендлер НЕ вызван
    assert result.startswith("SECURITY"), result


# ── Агентный путь (agent/executor) — парные сценарии дыры ────────────────────

def test_golden_agent_path_write_is_denied(monkeypatch):
    """Этап 1: тот же write, что требует confirm в главном пути, в АГЕНТНОМ
    (автономном) пути теперь ОТКЛОНЯЕТСЯ — человека в цикле нет, подтвердить
    некому, поэтому единый гейт fail-closed возвращает SECURITY, а хендлер не
    вызывается. (Раньше это была дыра: executor исполнял write мимо гейта.)"""
    import actions.file_controller as fcmod
    calls = []
    monkeypatch.setattr(fcmod, "file_controller",
                        lambda **k: calls.append(k) or "[fc executed]")
    # Блок 8: отказ гейта приходит ИСКЛЮЧЕНИЕМ, а не строкой. Проверяемое
    # свойство не изменилось — действие не выполнено; изменилась ФОРМА отказа,
    # и это изменение осознанное. Причина: строку исполнитель считал удачей и
    # отвечал владельцу «сделано» о том, чего не делал (замер 19.08.2026).
    with pytest.raises(jexec.ToolRefused) as got:
        jexec._call_tool("file_controller",
                         {"action": "write", "path": "desktop",
                          "name": "a.txt", "content": "x"},
                         None)
    assert not calls, f"write не должен исполняться в автономном пути: {calls}"
    assert "SECURITY" in str(got.value), got.value


def test_golden_agent_path_gate_is_consulted(monkeypatch):
    """Этап 1 (вторая проекция): executor ТЕПЕРЬ обращается к core.security через
    единый gate.dispatch на каждом вызове — дыра «гейт не спрашивают» закрыта."""
    import core.security as sec
    import actions.file_controller as fcmod
    gate_hits = []
    real = sec.check_tool_call
    monkeypatch.setattr(sec, "check_tool_call",
                        lambda *a, **k: gate_hits.append(a) or real(*a, **k))
    calls = []
    monkeypatch.setattr(fcmod, "file_controller",
                        lambda **k: calls.append(k) or "[fc executed]")
    # Блок 8: отказ — исключение (см. парный сценарий выше).
    with pytest.raises(jexec.ToolRefused) as got:
        jexec._call_tool("file_controller",
                         {"action": "write", "path": "d", "name": "a"}, None)
    assert gate_hits, "gate.dispatch не обратился к core.security — гейт не подключён"
    assert not calls, "write исполнился в автономном пути вопреки гейту"
    assert "SECURITY" in str(got.value), got.value


def test_golden_agent_path_unknown_tool_blocked():
    """Удалённые/неизвестные тулы агентный путь блокирует локально."""
    # Блок 8: та же смена формы отказа. Суть та же — тул не исполняется.
    for tool, params in (("flight_finder", {"origin": "a"}),
                         ("totally_unknown_tool", {})):
        with pytest.raises(jexec.ToolRefused) as got:
            jexec._call_tool(tool, params, None)
        assert "SECURITY" in str(got.value), (tool, got.value)
