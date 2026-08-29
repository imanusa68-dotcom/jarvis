"""Сторожа шага 3-бис: неудача модели обязана быть слышна, а 503 — пережит.

Повод: в ночь на 2026-08-07 один заход из шести вернул 503 UNAVAILABLE. Ошибка
нигде не печаталась, поэтому удачный поход к модели и сорвавшийся выглядели
одинаково — причину нашли только отдельной прослушкой.

Что охраняется:
  1. Временный отказ (503, обрыв сети) переживается одним повтором.
  2. Пауза и число заходов берутся из config/registry.yaml, а не из кода.
  3. На 429 повтора нет никогда: лишний заход жжёт дневную квоту.
  4. Постоянная ошибка (400) не повторяется: повтор её не вылечит.
  5. Любая неудача называет вслух причину и того, кто звонил.
  6. Пропуск по остыванию тоже слышен — раньше он уходил молча.
  7. В определении языка видно, кто решил: буквы, модель или отказ.
  8. Пропавший раздел реестра даёт сегодняшнее поведение, а не падение.

Запуск: python -m pytest -q  или  python tests/test_transient_retry_is_audible.py
"""
import io
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agent.executor as executor          # noqa: E402
import config.loader as loader             # noqa: E402
import core.aux_model as aux_model         # noqa: E402

# Тексты ошибок взяты с живой машины, а не придуманы.
ERR_503 = RuntimeError(
    "503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is "
    "currently experiencing high demand.', 'status': 'UNAVAILABLE'}}"
)
ERR_429 = RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded for this model")
ERR_400 = ValueError("400 INVALID_ARGUMENT: prompt is malformed")


def _retry_numbers():
    text = (ROOT / "config" / "registry.yaml").read_text(encoding="utf-8")
    block = ((yaml.safe_load(text) or {}).get("limits") or {}).get("retry") or {}
    return int(block["attempts"]), float(block["pause_seconds"])


class _Answer:
    def __init__(self, text):
        self.text = text


class _Sdk:
    """Подмена единственной двери к SDK: считает заходы, отвечает по сценарию."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def __call__(self, model, contents, api_key):
        self.calls.append(model)
        step = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(step, BaseException):
            raise step
        return _Answer(step)


def _call(sdk, model, caller="test"):
    """Вызов с поддельной дверью: без сети, без ключа и без настоящего сна."""
    saved_door, saved_sleep = aux_model._generate, time.sleep
    slept = []
    aux_model._generate = sdk
    time.sleep = lambda seconds: slept.append(seconds)
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            ok, text = aux_model.aux_call("вопрос", "test-key", model=model, caller=caller)
    finally:
        aux_model._generate = saved_door
        time.sleep = saved_sleep
    return ok, text, buf.getvalue(), slept


def test_a_temporary_failure_is_survived_by_one_retry():
    attempts, pause = _retry_numbers()
    assert attempts >= 2, "в реестре не осталось второго захода"

    sdk = _Sdk(ERR_503, "Turkish")
    ok, text, out, slept = _call(sdk, "test-503-then-ok")

    assert ok and text == "Turkish", f"повтор не спас: {text!r}"
    assert len(sdk.calls) == 2, f"заходов было {len(sdk.calls)}, а нужно два"
    assert slept == [pause], f"пауза взята не из реестра: {slept}"
    assert "временный отказ" in out, f"первый срыв ушёл молча: {out!r}"
    assert "повтор удался" in out, f"успешный повтор не отмечен: {out!r}"


def test_a_network_break_is_also_temporary():
    sdk = _Sdk(ConnectionError("connection reset by peer"), "German")
    ok, text, out, _slept = _call(sdk, "test-network-break")

    assert ok and text == "German"
    assert len(sdk.calls) == 2, "обрыв сети не пережит повтором"
    assert "временный отказ" in out


def test_a_temporary_failure_that_never_clears_gives_up_out_loud():
    attempts, _pause = _retry_numbers()
    sdk = _Sdk(ERR_503, ERR_503, ERR_503, ERR_503)
    ok, text, out, _slept = _call(sdk, "test-503-forever")

    assert not ok and text.startswith("[error:"), f"ответ не похож на отказ: {text!r}"
    assert len(sdk.calls) == attempts, (
        f"заходов {len(sdk.calls)}, а в реестре разрешено {attempts}"
    )
    assert "не удался" in out and "503" in out, f"окончательный срыв молчит: {out!r}"


def test_a_429_is_never_retried_because_a_second_try_burns_quota():
    sdk = _Sdk(ERR_429, "Turkish")
    ok, text, out, _slept = _call(sdk, "test-429-model")

    assert not ok and text.startswith("[quota-429"), f"исчерпание не узнано: {text!r}"
    assert len(sdk.calls) == 1, "по исчерпанной квоте пошёл повтор — так жгут лимит"
    assert "429" in out


def test_a_permanent_error_is_not_retried():
    sdk = _Sdk(ERR_400, "Turkish")
    ok, text, out, _slept = _call(sdk, "test-400-model")

    assert not ok and text.startswith("[error:")
    assert len(sdk.calls) == 1, "постоянную ошибку повтор не лечит"
    assert "не удался" in out and "400" in out


def test_every_failure_names_the_caller_and_the_reason():
    sdk = _Sdk(ERR_400)
    _ok, _text, out, _slept = _call(sdk, "test-caller-model", caller="Executor-Language")

    assert "Executor-Language" in out, f"по журналу не понять, кто звонил: {out!r}"
    assert "INVALID_ARGUMENT" in out, f"причина не названа: {out!r}"


def test_a_skip_by_cooldown_is_audible():
    from core.model_guard import get_guard

    model = "test-cooldown-model"
    get_guard().record_429(60.0, model)

    sdk = _Sdk("Turkish")
    ok, text, out, _slept = _call(sdk, model)

    assert not ok and text.startswith("[quota-cooldown"), f"остывание не сработало: {text!r}"
    assert sdk.calls == [], "модель звали, хотя она остывает"
    assert "остывает" in out, f"пропуск по остыванию ушёл молча: {out!r}"


def test_the_retry_numbers_live_in_the_config_not_in_the_code():
    attempts, pause = _retry_numbers()
    assert attempts >= 1 and pause >= 0
    assert loader.get_limit("retry", "attempts") == attempts
    assert loader.get_limit("retry", "pause_seconds") == pause
    assert loader.get_limit("retry", "no_such_limit", 7) == 7

    needle = str(pause)
    offenders = []
    for folder in ("core", "agent"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            if needle in path.read_text(encoding="utf-8", errors="ignore"):
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"пауза {needle} зашита в код: {offenders}"


def test_a_missing_retry_section_degrades_to_todays_behaviour():
    saved = loader.get_limit
    try:
        loader.get_limit = lambda role, name, default=None: default
        assert aux_model._retry_plan() == (1, 0.0), "без раздела в реестре нужен один заход без паузы"

        loader.get_limit = lambda role, name, default=None: "мусор"
        assert aux_model._retry_plan() == (1, 0.0), "битое число в реестре обязано деградировать, а не ронять"

        loader.get_limit = lambda role, name, default=None: -5
        assert aux_model._retry_plan() == (1, 0.0), "отрицательные числа не должны проходить"
    finally:
        loader.get_limit = saved


def test_a_transient_needle_never_swallows_a_429():
    assert aux_model._is_transient(ERR_503) is True
    assert aux_model._is_transient(ConnectionError("connection reset")) is True
    assert aux_model._is_transient(ERR_429) is False, "429 попал в «временные» — повтор сожжёт квоту"
    assert aux_model._is_transient(ERR_400) is False


def _ask_language(text):
    """Спросить язык и услышать консоль.

    Дверь к модели и выдача ключа заминированы: если определение языка
    когда-нибудь снова полезет в сеть, тест упадёт вслух, а не просто
    станет на полсекунды медленнее и когда-нибудь не тем языком.
    """
    def _mine(*args, **kwargs):
        raise AssertionError("определение языка снова полезло к модели")

    saved_call, saved_key = aux_model.cheap_call, executor._get_api_key
    aux_model.cheap_call = _mine
    executor._get_api_key = _mine
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            name = executor._detect_language(text)
    finally:
        aux_model.cheap_call = saved_call
        executor._get_api_key = saved_key
    return name, buf.getvalue()


def test_the_language_path_says_who_decided():
    name, out = _ask_language("привет, сэр")
    assert name == "Russian" and "буквы" in out, f"решение по буквам молчит: {out!r}"

    name, out = _ask_language("bugun hava cok guzel ve ben eve gidiyorum")
    assert name == "Turkish" and "слова" in out, f"решение по словам молчит: {out!r}"


def test_a_dead_model_can_no_longer_touch_the_language():
    # Ночью 7 августа 2026 один и тот же турецкий текст отвечал то Turkish,
    # то English: ответ зависел от чужого сервера и его 503. Теперь сервера
    # в этом пути нет вовсе, а дверь выше заминирована.
    for text, want in (
        ("привет, сэр", "Russian"),
        ("bugun hava cok guzel ve ben eve gidiyorum", "Turkish"),
        ("hello world how are you", "English"),
        ("", "Russian"),
    ):
        name, out = _ask_language(text)
        assert name == want, f"{text!r} дал {name!r}, а должен {want!r}"
        assert "не определён" not in out, f"отказывать больше некому: {out!r}"


def test_the_language_answer_is_always_a_name_never_a_sentence():
    # Раньше модель могла ответить целой вежливой фразой вместо имени языка,
    # и такую фразу приходилось ловить и отбрасывать. Сейчас фразе неоткуда
    # взяться: имя берётся из таблицы локалей, а не из чужой речи.
    from core.search_locale import _LOCALE_MAP

    allowed = {row[3] for row in _LOCALE_MAP.values()}
    for text in ("привет, сэр", "bugun hava cok guzel ve ben eve gidiyorum",
                 "hello world how are you", "今天天气很好", "42", ""):
        name, _ = _ask_language(text)
        assert name in allowed, f"{text!r} дал не имя языка, а {name!r}"


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  ✗ {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} зелёных")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())
