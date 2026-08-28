# -*- coding: utf-8 -*-
"""
Живая приёмка без сети: три дефекта, найденные владельцем голосом 12.08.2026.

Зачем этот файл. На шаге 26 ядро научилось понимать живую речь, но владелец
сел за клавиатуру и за четыре фразы нашёл три дыры, которых не было ни в одном
моём корпусе. Каждый тест здесь — запись его настоящей фразы, а не моей выдумки.

Д-1  «через 1 минут напомни выключить чайник»  → в записке осталось одно слово
      «чайник»: оборот вырезался в пустоту, соседние слова слиплись в
      «минутвыключить», а чистка времени съела этот ком целиком.
Д-2  «открой блокнот плиз» → в лаунчер уехало имя «блокнот плиз».
Д-3  «бро какое время?» → отказ: «бро» не было в обращениях, а «какое время»
      не было в шаблоне часов.

Ни один тест не ходит в сеть и не трогает диск: дверь и инструменты подставные.

Run standalone: python tests/test_offline_speech_step27.py
"""
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core.gate as gate  # noqa: E402
import core.offline_core as oc  # noqa: E402

SOURCE = (ROOT / "core" / "offline_core.py").read_text(encoding="utf-8")


@dataclass
class _Verdict:
    verdict: str = "run"
    tool: str = ""
    action: object = None
    risk: str = "low"
    policy: str = "auto"
    mode: str = "interactive"
    message: str = "текст, написанный для модели"
    reason: str = "проверка"

    @property
    def allowed(self):
        return self.verdict == "run"


class _Door:
    def __init__(self, verdict="run"):
        self.verdict = verdict
        self.calls = []

    def __call__(self, tool, params=None, *, mode="interactive", screen_control=False):
        self.calls.append((tool, dict(params or {})))
        return _Verdict(verdict=self.verdict, tool=tool,
                        action=(params or {}).get("action"))


class _Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, parameters=None, **kw):
        self.calls.append(dict(parameters or {}))
        return "сделано"


class _Rig:
    """Подставная дверь и подставные руки: на диск ничего не попадает."""

    def __init__(self, verdict="run"):
        self.verdict = verdict

    def __enter__(self):
        self.door = _Door(self.verdict)
        self.spy = _Spy()
        self._old_door = gate.dispatch
        self._old_import = oc._import_tool
        gate.dispatch = self.door
        oc._import_tool = lambda tool: self.spy
        return self

    def __exit__(self, *exc):
        gate.dispatch = self._old_door
        oc._import_tool = self._old_import
        return False

    def last(self):
        return self.door.calls[-1] if self.door.calls else (None, {})


def _params(phrase):
    """Что ушло в инструмент после этой фразы."""
    with _Rig() as rig:
        reply = oc.handle(phrase)
        tool, params = rig.last()
        return reply, tool, params


# ── Д-1: слипшиеся слова ───────────────────────────────────────

def test_a_time_first_keeps_the_whole_note():
    """Живая фраза владельца: сначала время, потом глагол."""
    reply, tool, params = _params("через 1 минут напомни выключить чайник")
    assert tool == "reminder", tool
    assert params.get("message") == "выключить чайник", repr(params.get("message"))


def test_b_verb_first_keeps_the_whole_note():
    reply, tool, params = _params("напомни через 1 минуту выключить чайник")
    assert params.get("message") == "выключить чайник", repr(params.get("message"))


def test_c_two_word_note_survives_five_minutes():
    reply, tool, params = _params("через 5 минут напомни позвонить маме")
    assert params.get("message") == "позвонить маме", repr(params.get("message"))


def test_d_hour_form_still_works():
    """Регресс: эта фраза работала живьём и обязана работать дальше."""
    reply, tool, params = _params("поставь напоминание через час про плиту")
    assert params.get("message") == "плиту", repr(params.get("message"))


def test_e_tomorrow_at_nine_still_works():
    reply, tool, params = _params("напомнить завтра в 9:00 позвонить в банк")
    assert params.get("message") == "позвонить в банк", repr(params.get("message"))


def test_f_twenty_minutes_still_works():
    reply, tool, params = _params("напомни через 20 минут выпить воды")
    assert params.get("message") == "выпить воды", repr(params.get("message"))


# ── Д-2: вежливость в хвосте ──────────────────────────────────

def test_g_slang_please_never_reaches_the_launcher():
    """Живая фраза: «открой блокнот плиз»."""
    for phrase in ("открой блокнот плиз", "открой блокнот плз",
                   "открой блокнот пжлст", "открой блокнот плиииз"):
        reply, tool, params = _params(phrase)
        assert tool == "open_app", (phrase, tool)
        assert params.get("app_name") == "блокнот", (phrase, params)


def test_h_polite_word_still_works():
    """Регресс: старая вежливость не сломана."""
    reply, tool, params = _params("открой блокнот пожалуйста")
    assert params.get("app_name") == "блокнот", params


def test_i_thanks_is_not_a_politeness_tail():
    """«Спасибо» резать нельзя: это может быть частью имени файла."""
    reply, tool, params = _params("запиши заметку сказать спасибо")
    assert params.get("content") == "сказать спасибо", params


# ── Д-3: обращение и вопрос о часах ─────────────────────────────

def test_j_bro_asks_about_time():
    """Живая фраза: «бро какое время?»— и ответ без всякой двери."""
    with _Rig() as rig:
        reply = oc.handle("бро какое время?")
        assert reply is not None, "ядро промолчало"
        assert not rig.door.calls, rig.door.calls


def test_k_every_human_way_to_ask_the_clock():
    for phrase in ("какое время", "какое сейчас время", "который час",
                   "братан сколько времени", "дружище сколько время",
                   "чувак который час"):
        with _Rig() as rig:
            reply = oc.handle(phrase)
            assert reply is not None, phrase
            assert not rig.door.calls, (phrase, rig.door.calls)


def test_l_season_of_the_year_is_not_a_clock():
    """Предохранитель: не всякое «время» — это часы."""
    for phrase in ("какое время года сейчас", "в какое время суток лучше бегать",
                   "какое время дня ты любишь"):
        with _Rig() as rig:
            assert oc.handle(phrase) is None, phrase


# ── общие предохранители ───────────────────────────────────

_STILL_NOT_OURS = (
    "открой мне глаза на правду",
    "переведи фразу на английский",
    "расскажи про историю рима",
    "сделай скриншот экрана",
    "какая погода в москве",
    "включи музыку погромче",
    "а во сколько ты вчера открыл этот файл",
    "что такое квота",
    "найди мне место в этой жизни",
    "почему напоминание не сработало",
)


def test_m_anti_corpus_did_not_move():
    """Расширение разбора ничего не украло у модели."""
    for phrase in _STILL_NOT_OURS:
        with _Rig() as rig:
            assert oc.handle(phrase) is None, phrase


def test_n_menu_is_still_the_same_promise():
    """Меню — обещание. Менять его в этом шаге не договаривались."""
    assert oc._MENU[0] == (
        "время и дата · напоминания · открыть приложение, файл или папку · "
        "найти файл · заметка в файл · «что ты делал» · "
        "отмена и повтор последнего действия · громкость"), repr(oc._MENU[0])


def test_o_source_has_no_forbidden_words():
    for word in ("aux_call", "cheap_call", "genai", "generativeai",
                 "generate_content", "requests", "urllib", "socket", "print("):
        assert word not in SOURCE, word


def test_p_junk_still_does_not_crash():
    for junk in (None, 12, "", "   ", "?!;", "x" * 5000, ["открой"], {"a": 1}):
        with _Rig():
            oc.handle(junk)


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
            print("  PASS  " + fn.__name__)
        except AssertionError as e:
            print("  FAIL  " + fn.__name__ + ": " + str(e))
        except Exception as e:
            print("  ERROR " + fn.__name__ + ": " + type(e).__name__ + ": " + str(e))
    total = len(tests)
    print("RESULT: %d/%d %s" % (passed, total, "ALL PASS" if passed == total else "RED"))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(_run())
