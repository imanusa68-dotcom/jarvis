# tests/test_agent_uses_one_door.py
# ─────────────────────────────────────────────────────────────────────────────
# Агентский путь ходит к модели ОДНОЙ дверью (фаза 0.5, 09.08.2026).
#
# До этого шага planner/executor/error_handler строили свой клиент старым
# SDK: они не знали про остывание после 429, не умели повторять временный
# отказ и падали насмерть без ключа. Здесь закреплено три вещи:
#   1. второй двери в проекте больше нет нигде;
#   2. правила, которые раньше ехали системной инструкцией, доехали до модели;
#   3. отказ двери никогда не выдаётся за ответ модели и никогда не роняет шаг.
#
# Ни сети, ни ключа, ни SDK здесь не нужно: общая дверь подменяется ловушкой.
# ─────────────────────────────────────────────────────────────────────────────

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.aux_model as aux_model            # noqa: E402
import agent.planner as planner               # noqa: E402
import agent.error_handler as handler         # noqa: E402
import agent.executor as executor             # noqa: E402
from agent.error_handler import ErrorDecision  # noqa: E402
from config.loader import get_model            # noqa: E402


# Слово собирается из кусков нарочно: иначе сканер поймал бы сам себя.
OLD_IMPORT_A = "import " + "google." + "generativeai"
OLD_IMPORT_B = "from " + "google." + "generativeai"
OLD_CLIENT = "Generative" + "Model("
OLD_CONFIG = "genai." + "configure("

SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "tests", "logs",
             ".pytest_cache", "build", "dist"}

PLAN_JSON = json.dumps({
    "goal": "узнать погоду",
    "steps": [{"step": 1, "tool": "weather_report",
               "description": "check weather", "parameters": {},
               "critical": True}],
})

ANALYSIS_JSON = json.dumps({
    "decision": "retry",
    "reason": "network hiccup",
    "fix_suggestion": "",
    "max_retries": 1,
    "user_message": "One moment, sir.",
})

FAILED_STEP = {"step": 2, "tool": "web_search", "description": "find prices",
               "parameters": {"query": "prices"}, "critical": False}


class _Door:
    """Ловушка вместо общей двери: помнит, о чём спросили, и отвечает заказанным."""

    def __init__(self, ok=True, answer=""):
        self.ok = ok
        self.answer = answer
        self.calls = []

    def __call__(self, prompt, api_key, model=None, image_parts=None,
                 caller="unknown"):
        self.calls.append({"prompt": prompt, "api_key": api_key,
                           "model": model, "caller": caller})
        return self.ok, self.answer


class _Mine:
    """Дверь, которую нельзя открывать вообще."""

    def __call__(self, *a, **kw):
        raise AssertionError("модель была вызвана там, где её вызывать запрещено")


def _run(door, fn, api_key="KEY-FOR-TESTS"):
    """Вызвать fn с подменённой дверью и ключом, вернуть (результат, консоль)."""
    saved_door = aux_model.aux_call
    saved_keys = [(m, m._get_api_key) for m in (planner, handler, executor)]
    aux_model.aux_call = door
    for mod, _ in saved_keys:
        mod._get_api_key = lambda: api_key
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            result = fn()
    finally:
        aux_model.aux_call = saved_door
        for mod, original in saved_keys:
            mod._get_api_key = original
    return result, buf.getvalue()


def _sources():
    """Все исходники проекта кроме самих тестов."""
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        yield rel, path.read_text(encoding="utf-8", errors="ignore")


# ───── 1. второй двери больше нет ───────────────────────────────────────

def test_the_old_sdk_is_gone_from_the_whole_project():
    offenders = []
    for rel, text in _sources():
        for needle in (OLD_IMPORT_A, OLD_IMPORT_B, OLD_CLIENT, OLD_CONFIG):
            if needle in text:
                offenders.append(f"{rel}: {needle}")
    assert not offenders, f"вторая дверь к модели вернулась: {offenders}"


def test_the_old_sdk_is_gone_from_the_dependencies():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8", errors="ignore")
    assert '"google-generativeai' not in text, "старый SDK снова в зависимостях"
    assert '"google-genai' in text, "новый SDK исчез из зависимостей"


def test_the_agent_folder_never_builds_its_own_client():
    for name in ("planner.py", "executor.py", "error_handler.py"):
        text = (ROOT / "agent" / name).read_text(encoding="utf-8", errors="ignore")
        assert "aux_call" in text or name == "executor.py", f"{name} не знает общей двери"
        assert "generate_content(" not in text, f"{name} снова зовёт модель напрямую"


# ───── 2. планировщик ─────────────────────────────────────────────────

def test_the_planner_walks_through_the_one_door():
    door = _Door(True, PLAN_JSON)
    plan, printed = _run(door, lambda: planner.create_plan("узнай погоду"))
    assert len(door.calls) == 1, f"заходов к модели: {len(door.calls)}"
    assert plan["steps"][0]["tool"] == "weather_report", f"план не разобрался: {plan}"
    assert door.calls[0]["api_key"] == "KEY-FOR-TESTS", "ключ не доехал до двери"


def test_the_planner_rules_travel_inside_the_prompt():
    door = _Door(True, PLAN_JSON)
    _run(door, lambda: planner.create_plan("узнай погоду"))
    prompt = door.calls[0]["prompt"]
    head = planner.PLANNER_PROMPT[:120]
    assert head in prompt, "правила планировщика потеряны вместе с системной инструкцией"
    assert prompt.startswith(head), "правила обязаны идти первым куском"
    assert "Return ONLY the JSON object" in prompt, "требование JSON не повторено в конце"
    assert "узнай погоду" in prompt, "сама задача до модели не доехала"


def test_the_planner_keeps_its_two_roles():
    door = _Door(True, PLAN_JSON)
    _run(door, lambda: planner.create_plan("цель"))
    assert door.calls[0]["model"] == get_model("aux_light"), "роль планирования съехала"

    door2 = _Door(True, PLAN_JSON)
    _run(door2, lambda: planner.replan("цель", [], FAILED_STEP, "boom"))
    assert door2.calls[0]["model"] == get_model("aux_heavy"), "роль перепланирования съехала"


def test_a_refusal_is_never_parsed_as_a_plan():
    door = _Door(False, "[quota-cooldown:65s]")
    plan, printed = _run(door, lambda: planner.create_plan("открой браузер"))
    assert plan["steps"], "при отказе двери план обязан остаться запасной, а не пустой"
    assert "quota-cooldown" in printed, f"настоящая причина не названа: {printed!r}"
    assert "JSON parse failed" not in printed, "отказ квоты выдан за битый JSON"


def test_a_broken_answer_still_yields_a_usable_plan():
    door = _Door(True, "это вообще не JSON")
    plan, printed = _run(door, lambda: planner.create_plan("найди цены"))
    assert plan["steps"][0]["tool"] in ("web_search", "open_app", "browser_control")
    assert "Fallback plan" in printed, f"падение в запасной план прошло молча: {printed!r}"


def test_a_missing_key_no_longer_kills_the_step():
    door = _Mine()
    saved = planner._get_api_key
    planner._get_api_key = lambda: (_ for _ in ()).throw(RuntimeError("no key"))
    saved_door = aux_model.aux_call
    aux_model.aux_call = door
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            plan = planner.create_plan("цель без ключа")
    finally:
        planner._get_api_key = saved
        aux_model.aux_call = saved_door
    printed = buf.getvalue()
    assert plan["steps"], "без ключа планировщик обязан дать запасной план"
    assert "ключ не найден" in printed, f"отсутствие ключа прошло молча: {printed!r}"


def test_the_replan_falls_back_instead_of_exploding():
    door = _Door(False, "[error:503 UNAVAILABLE]")
    plan, printed = _run(door,
                         lambda: planner.replan("цель", [], FAILED_STEP, "boom"))
    assert plan["steps"], "перепланирование при отказе осталось без запасного плана"
    assert "503" in printed, f"причина отказа не названа: {printed!r}"


# ───── 3. разборщик ошибок ───────────────────────────────────────────

def test_the_error_analyst_walks_through_the_one_door():
    door = _Door(True, ANALYSIS_JSON)
    result, _ = _run(door, lambda: handler.analyze_error(FAILED_STEP, "timeout"))
    assert len(door.calls) == 1, "разбор ошибки не пошёл через общую дверь"
    assert result["decision"] is ErrorDecision.RETRY, f"решение не разобралось: {result}"
    assert door.calls[0]["model"] == get_model("aux_light"), "роль разбора ошибок съехала"


def test_the_error_rules_travel_inside_the_prompt():
    door = _Door(True, ANALYSIS_JSON)
    _run(door, lambda: handler.analyze_error(FAILED_STEP, "timeout"))
    prompt = door.calls[0]["prompt"]
    head = handler.ERROR_ANALYST_PROMPT[:120]
    assert prompt.startswith(head), "правила разбора потеряны вместе с системной инструкцией"
    assert "web_search" in prompt, "упавший шаг до модели не доехал"


def test_a_refused_analysis_still_answers_replan():
    door = _Door(False, "[quota-429:cooldown 65s]")
    result, printed = _run(door, lambda: handler.analyze_error(FAILED_STEP, "boom"))
    assert result["decision"] is ErrorDecision.REPLAN, "отказ двери не дал честного решения"
    assert result["user_message"], "владелец остался без фразы"
    assert "quota-429" in printed, f"причина не названа: {printed!r}"


def test_all_three_failures_answer_with_one_voice():
    door = _Door(False, "[error:boom]")
    refused, _ = _run(door, lambda: handler.analyze_error(FAILED_STEP, "boom"))
    door2 = _Door(True, "это не JSON")
    broken, _ = _run(door2, lambda: handler.analyze_error(FAILED_STEP, "boom"))
    assert refused["user_message"] == broken["user_message"], \
        "три пути неудачи разошлись в тексте — единый источник сломан"
    assert refused["fix_suggestion"] == broken["fix_suggestion"]


def test_a_critical_step_is_never_skipped():
    critical = dict(FAILED_STEP, critical=True)
    door = _Door(True, json.dumps({"decision": "skip", "reason": "meh",
                                   "user_message": "skipping"}))
    result, _ = _run(door, lambda: handler.analyze_error(critical, "boom"))
    assert result["decision"] is ErrorDecision.REPLAN, "критичный шаг разрешили пропустить"


def test_the_exhausted_attempts_branch_never_calls_the_model():
    door = _Mine()
    result, _ = _run(door, lambda: handler.analyze_error(FAILED_STEP, "boom",
                                                         attempt=2, max_attempts=2))
    assert result["decision"] is ErrorDecision.REPLAN


# ───── 4. мёртвая ветка починки ──────────────────────────────────────

def test_the_fix_never_calls_the_model():
    door = _Mine()
    step, printed = _run(door, lambda: handler.generate_fix(FAILED_STEP, "boom", "try cmd"))
    assert step["tool"] == "cmd_control", f"запасной шаг не тот: {step}"
    assert printed.strip(), "подмена шага прошла молча"


def test_the_fix_never_offers_code_that_the_project_forbids():
    step, _ = _run(_Mine(), lambda: handler.generate_fix(FAILED_STEP, "boom", "try cmd"))
    assert step["tool"] != "code_helper", "вернулся шаг, который code_helper всё равно блокирует"
    assert step["parameters"].get("action") != "run", "действие run запрещено в проекте"
    assert step["parameters"].get("task") == FAILED_STEP["description"]
    assert step["critical"] == FAILED_STEP["critical"], "критичность шага потеряна"


# ───── 5. перевод в исполнителе ─────────────────────────────────────

def test_the_translation_walks_through_the_one_door():
    door = _Door(True, "Переведённый текст")
    out, _ = _run(door, lambda: executor._translate_to_goal_language(
        "Some English text", "сохрани отчёт"))
    assert out == "Переведённый текст", f"перевод не вернулся: {out!r}"
    assert door.calls[0]["model"] == get_model("aux_heavy"), "роль перевода съехала"
    assert door.calls[0]["caller"] == "Executor-Translate"


def test_a_failed_translation_returns_the_original_text():
    door = _Door(False, "[quota-cooldown:65s]")
    out, printed = _run(door, lambda: executor._translate_to_goal_language(
        "Some English text", "сохрани отчёт"))
    assert out == "Some English text", "при отказе перевода потерян исходный текст"
    assert "Translation failed" in printed, f"отказ перевода прошёл молча: {printed!r}"


def test_an_empty_answer_is_not_a_translation():
    door = _Door(True, "   ")
    out, _ = _run(door, lambda: executor._translate_to_goal_language(
        "Some English text", "сохрани отчёт"))
    assert out == "Some English text", "пустой ответ модели подменил текст"


# ───── 6. видно, кто жжёт квоту ─────────────────────────────────────

def test_every_caller_has_its_own_name_in_the_log():
    names = []
    for fn in (
        lambda d: planner.create_plan("цель"),
        lambda d: planner.replan("цель", [], FAILED_STEP, "boom"),
        lambda d: handler.analyze_error(FAILED_STEP, "boom"),
        lambda d: executor._translate_to_goal_language("text", "цель"),
    ):
        door = _Door(True, PLAN_JSON)
        _run(door, lambda: fn(door))
        names.append(door.calls[0]["caller"])
    assert len(set(names)) == 4, f"имена вызывающих слились: {names}"
    assert all(n != "unknown" for n in names), f"безымянный вызов: {names}"


if __name__ == "__main__":
    cases = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = 0
    for name, fn in cases:
        try:
            fn()
            passed += 1
            print(f"  PASS  {name}")
        except Exception as exc:
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
    verdict = "ALL PASS" if passed == len(cases) else "SOME FAILED"
    print(f"RESULT: {passed}/{len(cases)} {verdict}")
    sys.exit(0 if passed == len(cases) else 1)
