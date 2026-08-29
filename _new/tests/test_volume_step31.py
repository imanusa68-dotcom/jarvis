# -*- coding: utf-8 -*-
"""
Громкость без сети (шаг 31, фаза 0.7).

Зачем этот файл. До шага 31 системной громкости в проекте не было вовсе:
кнопка mute в окне глушит МИКРОФОН, а computer_settings закрыт целиком и
остаётся закрытым: там живут яркость, wifi и выключение машины.
Громкость пришла ОТДЕЛЬНЫМ узким инструментом.

Что здесь проверяется:
  1. «громче», «тише», «громкость 30», «выключи звук», «включи звук» и
     «какая громкость» доходят до инструмента теми действиями, которые
     владелец имел в виду;
  2. «включи музыку погромче» ОСТАЁТСЯ чужой фразой — её решает большая
     модель, и дверь при этом не трогается вообще;
  3. отказ двери останавливает инструмент до его запуска и не пачкает журнал;
  4. сделанное попадает в ту же кассу, что и остальные дела (шаг 30);
  5. точный путь читает число, ставит число и перечитывает его обратно;
  6. границы: 300 — это 100, минус пять — это 0, выше ста — «выше некуда»,
     мусор вместо числа до регулятора не доезжает;
  7. «громче» при выключенном звуке снимает mute: владелец хочет СЛЫШАТЬ;
  8. без точного регулятора работают клавиши, и в ответе НЕТ ни одного
     выдуманного процента;
  9. инструмент никогда не бросает исключение, печатает ровно одну
     строку (причину отказа точного регулятора, шаг 31-бис),
     в сеть не ходит и живёт с CRLF, как все actions/*;
 10. дверь знает новый инструмент, а computer_settings остался закрытым.

Ни один тест не крутит настоящую громкость машины: и регулятор, и клавиши
подставные. Сети здесь тоже нет.

Run standalone: python tests/test_volume_step31.py
"""
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import actions.volume as vol  # noqa: E402
import core.gate as gate  # noqa: E402
import core.offline_core as oc  # noqa: E402
import core.security as security  # noqa: E402

VOLUME_BYTES = (ROOT / "actions" / "volume.py").read_bytes()
VOLUME_SOURCE = VOLUME_BYTES.decode("utf-8")


# ──────────────────────── стенды ───────────────────────

@dataclass
class _Verdict:
    """Ответ подставной двери — той же формы, что настоящий GateResult."""

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
    """Подставная дверь: помнит всё, что через неё прошло."""

    def __init__(self, verdict="run"):
        self.verdict = verdict
        self.calls = []

    def __call__(self, tool, params=None, *, mode="interactive", screen_control=False):
        self.calls.append({"tool": tool, "params": dict(params or {})})
        return _Verdict(verdict=self.verdict, tool=tool,
                        action=(params or {}).get("action"))


class _Spy:
    """Подставной инструмент: звук машины не трогает."""

    def __init__(self, answer="Готово, сэр."):
        self.answer = answer
        self.calls = []

    def __call__(self, parameters=None, **kw):
        self.calls.append(dict(parameters or {}))
        return self.answer


class _CashDeskSpy:
    """Подставная касса шага 30: видно, что ядро собиралось записать."""

    def __init__(self):
        self.calls = []

    def __call__(self, tool=None, action=None, summary="", ok=True):
        self.calls.append({"tool": tool, "action": action,
                           "summary": str(summary), "ok": bool(ok)})
        return True


class _Rig:
    """Подменяет дверь, ввоз инструмента и кассу."""

    def __init__(self, verdict="run"):
        self.verdict = verdict

    def __enter__(self):
        import core.action_log as al
        self._al = al
        self.door = _Door(self.verdict)
        self.spy = _Spy()
        self.cash = _CashDeskSpy()
        self._real_dispatch = gate.dispatch
        self._real_import = oc._import_tool
        self._real_note = al.note
        gate.dispatch = self.door
        oc._import_tool = lambda tool: self.spy
        al.note = self.cash
        return self

    def __exit__(self, *exc):
        gate.dispatch = self._real_dispatch
        oc._import_tool = self._real_import
        self._al.note = self._real_note
        return False


class _FakeReg:
    """Подставной регулятор Windows: те же методы, что у pycaw."""

    def __init__(self, level=40, muted=False, boom=None):
        self.level = level
        self.muted = muted
        self.boom = boom
        self.written = []
        self.mutes = []

    def GetMasterVolumeLevelScalar(self):
        if self.boom == "read":
            raise OSError("регулятор не читается")
        return self.level / 100.0

    def GetMute(self):
        return 1 if self.muted else 0

    def SetMasterVolumeLevelScalar(self, value, ctx=None):
        if self.boom == "write":
            raise OSError("регулятор не крутится")
        self.level = int(round(value * 100))
        self.written.append(self.level)

    def SetMute(self, flag, ctx=None):
        self.muted = bool(flag)
        self.mutes.append(bool(flag))


class _Hands:
    """Подменяет швы инструмента: точный регулятор и медиа-клавиши."""

    def __init__(self, reg=None, reg_boom=None, keys_boom=None):
        self.reg = reg
        self.reg_boom = reg_boom
        self.keys_boom = keys_boom
        self.taps = []

    def __enter__(self):
        self._real_reg = vol._regulator
        self._real_tap = vol._tap

        def regulator():
            if self.reg_boom:
                raise OSError(self.reg_boom)
            return self.reg

        def tap(key, times=1):
            if self.keys_boom:
                raise OSError(self.keys_boom)
            self.taps.append((key, int(times)))

        vol._regulator = regulator
        vol._tap = tap
        return self

    def __exit__(self, *exc):
        vol._regulator = self._real_reg
        vol._tap = self._real_tap
        return False


def _digits(text):
    return [ch for ch in text if ch.isdigit()]


# ───────────────────── разбор речи ───────────────────

def test_a_louder_reaches_the_tool():
    for phrase in ("сделай громче", "погромче", "прибавь звук",
                   "говори громче"):
        with _Rig() as rig:
            reply = oc.handle(phrase)
            assert reply is not None, "фраза не опознана: %s" % phrase
            assert rig.door.calls, phrase
            assert rig.door.calls[-1]["tool"] == "volume", rig.door.calls[-1]
            assert rig.door.calls[-1]["params"]["action"] == "up", \
                "%s -> %r" % (phrase, rig.door.calls[-1])
            assert rig.spy.calls, "инструмент не был зван: %s" % phrase


def test_b_quieter_reaches_the_tool():
    for phrase in ("сделай потише", "тише", "убавь звук"):
        with _Rig() as rig:
            reply = oc.handle(phrase)
            assert reply is not None, phrase
            assert rig.door.calls[-1]["params"]["action"] == "down", \
                "%s -> %r" % (phrase, rig.door.calls[-1])


def test_c_exact_number_is_carried_through():
    with _Rig() as rig:
        oc.handle("поставь громкость 30 процентов")
        params = rig.door.calls[-1]["params"]
        assert params["action"] == "set", params
        assert params["level"] == 30, params
    with _Rig() as rig:
        oc.handle("громкость 75")
        assert rig.door.calls[-1]["params"] == {"action": "set", "level": 75}, \
            rig.door.calls[-1]


def test_d_sound_off_and_on():
    for phrase, action in (("выключи звук", "mute"),
                           ("отключи звук", "mute"),
                           ("включи звук", "unmute"),
                           ("верни звук", "unmute")):
        with _Rig() as rig:
            oc.handle(phrase)
            assert rig.door.calls, phrase
            assert rig.door.calls[-1]["params"]["action"] == action, \
                "%s -> %r" % (phrase, rig.door.calls[-1])


def test_e_asking_how_loud_only_reads():
    for phrase in ("какая сейчас громкость", "сколько громкости"):
        with _Rig() as rig:
            oc.handle(phrase)
            assert rig.door.calls[-1]["params"]["action"] == "status", \
                "%s -> %r" % (phrase, rig.door.calls[-1])


def test_f_music_stays_someone_elses_business():
    """Анти-корпус не сдвинулся: системная громкость — не плеер."""
    for phrase in ("включи музыку погромче",
                   "сделай музыку потише",
                   "поставь видео погромче",
                   "включи песню громче"):
        with _Rig() as rig:
            assert oc.handle(phrase) is None, "перехватил чужое: %s" % phrase
            assert rig.door.calls == [], "дверь трогали зря: %s" % phrase
            assert rig.spy.calls == [], phrase


def test_g_a_closed_door_stops_the_tool():
    with _Rig(verdict="blocked") as rig:
        reply = oc.handle("сделай громче")
        assert reply is not None
        assert reply.ok is False, reply
        assert rig.spy.calls == [], "инструмент запустили при закрытой двери"
        assert rig.cash.calls == [], "отказ двери попал в журнал"


def test_h_done_work_goes_to_the_same_cash_desk():
    with _Rig() as rig:
        oc.handle("сделай громче")
        assert len(rig.cash.calls) == 1, rig.cash.calls
        note = rig.cash.calls[-1]
        assert note["tool"] == "volume", note
        assert note["action"] == "up", note
        assert note["ok"] is True, note


# ─────────────────── сам инструмент ──────────────────

def test_i_precise_path_reads_sets_and_rereads():
    reg = _FakeReg(level=40)
    with _Hands(reg=reg):
        answer = vol.volume({"action": "up"})
        assert reg.written == [50], reg.written
        assert "50" in answer and "40" in answer, answer

        answer = vol.volume({"action": "down"})
        assert reg.level == 40, reg.level
        assert "40" in answer, answer

        answer = vol.volume({"action": "set", "level": 30})
        assert reg.level == 30, reg.level
        assert "30" in answer, answer

        answer = vol.volume({"action": "status"})
        assert "30" in answer, answer
        assert reg.written == [50, 40, 30], reg.written


def test_j_louder_unmutes_because_the_owner_wants_to_hear():
    reg = _FakeReg(level=20, muted=True)
    with _Hands(reg=reg):
        vol.volume({"action": "up"})
        assert reg.muted is False, "громче при выключенном звуке — пустая работа"
    reg = _FakeReg(level=50, muted=False)
    with _Hands(reg=reg):
        vol.volume({"action": "mute"})
        assert reg.muted is True
        vol.volume({"action": "unmute"})
        assert reg.muted is False


def test_k_the_edges_hold():
    reg = _FakeReg(level=50)
    with _Hands(reg=reg):
        vol.volume({"action": "set", "level": 300})
        assert reg.level == 100, reg.level
        vol.volume({"action": "set", "level": -5})
        assert reg.level == 0, reg.level
    reg = _FakeReg(level=100)
    with _Hands(reg=reg):
        answer = vol.volume({"action": "up"})
        assert "некуда" in answer, answer
    reg = _FakeReg(level=50)
    with _Hands(reg=reg):
        answer = vol.volume({"action": "set", "level": "мусор"})
        assert reg.written == [], "мусор доехал до регулятора"
        assert "не понял" in answer.lower(), answer


def test_l_without_the_regulator_no_invented_numbers():
    hands = _Hands(reg_boom="pycaw не отвечает")
    with hands:
        answer = vol.volume({"action": "up"})
        assert hands.taps and hands.taps[-1][0] == vol._KEY_UP, hands.taps
        assert _digits(answer) == [], "назвал число, которого не видел: %s" % answer
        assert "pycaw" in answer, answer

        vol.volume({"action": "down"})
        assert hands.taps[-1][0] == vol._KEY_DOWN, hands.taps

        answer = vol.volume({"action": "status"})
        assert _digits(answer) == [], answer

        answer = vol.volume({"action": "set", "level": 30})
        # Шаг 31-бис: вслепую число СТАВИТСЯ отсчётом от нуля,
        # но называется ТОЛЬКО со словом «примерно».
        assert "примерно" in answer, answer
        assert hands.taps[-2] == (vol._KEY_DOWN, vol._TO_ZERO), hands.taps


def test_m_a_broken_regulator_falls_back_to_keys():
    reg = _FakeReg(level=40, boom="write")
    hands = _Hands(reg=reg)
    with hands:
        answer = vol.volume({"action": "up"})
        assert hands.taps, "запасной путь не включился: %s" % answer
        assert isinstance(answer, str) and answer.strip()


def test_n_the_tool_never_raises_and_never_lies():
    hands = _Hands(reg_boom="регулятора нет", keys_boom="клавиш тоже нет")
    with hands:
        for action in ("up", "down", "mute", "unmute", "status", "set"):
            answer = vol.volume({"action": action})
            assert isinstance(answer, str) and answer.strip(), action
    with _Hands(reg=_FakeReg()):
        answer = vol.volume({"action": "взорвись"})
        assert "не умею" in answer, answer
        assert vol.volume(None), "без параметров инструмент обязан ответить"


def test_o_the_tool_is_quiet_offline_and_crlf():
    for word in ("requests", "urllib", "socket", "genai",
                 "generate_content", "input("):
        assert word not in VOLUME_SOURCE, word
    # Печать разрешена РОВНО одна: причина отказа точного регулятора.
    assert VOLUME_SOURCE.count("print(") == 1, VOLUME_SOURCE.count("print(")
    assert "[Volume] точный регулятор не вышел" in VOLUME_SOURCE
    assert b"\r\n" in VOLUME_BYTES, "actions/* у нас живут с CRLF"
    # Windows-части — только внутри функций: иначе прогон тестов без pycaw
    # умирает на ввозе файла, не дойдя ни до одной проверки.
    early = [ln for ln in VOLUME_SOURCE.splitlines()
             if ln.startswith(("import comtypes", "import pycaw", "from pycaw",
                              "from comtypes", "import ctypes", "from ctypes"))]
    assert early == [], early


def test_p_the_door_knows_volume_and_still_blocks_settings():
    ok = security.check_tool_call("volume", {"action": "up"})
    assert ok.allowed is True, ok
    for action in ("down", "set", "mute", "unmute", "status"):
        assert security.check_tool_call("volume", {"action": action}).allowed, action
    # Честно и проверено на коде: неизвестное ДЕЙСТВИЕ у разрешённого
    # инструмента дверь пропускает — так она ведёт себя со всеми
    # инструментами, а менять общие правила двери в шаге про громкость
    # нельзя. Второй рубеж держит сам инструмент (см. test_n).
    assert vol._ACTIONS == ("up", "down", "set", "mute", "unmute", "status"), vol._ACTIONS
    blocked = security.check_tool_call("computer_settings", {})
    assert blocked.allowed is False, "computer_settings открыли — этого не просили"


def test_q_menu_promises_volume():
    assert "громкость" in oc._MENU[0], oc._MENU[0]
    assert "volume" in oc._MENU[1], oc._MENU[1]
    assert oc._LOADERS["volume"] == ("actions.volume", "volume"), oc._LOADERS


# ─────────────────── запуск без pytest ──────────────────

def test_r_blind_set_counts_steps_from_zero():
    """Главная жалоба владельца: «поставь 24 процента» не работало вовсе."""
    hands = _Hands(reg_boom="pycaw не отвечает")
    with hands:
        answer = vol.volume({"action": "set", "level": 24})
    assert hands.taps == [(vol._KEY_DOWN, vol._TO_ZERO),
                          (vol._KEY_UP, 12)], hands.taps
    assert "примерно" in answer, answer
    assert "24" in answer, answer


def test_s_blind_set_to_zero_does_not_climb_back():
    hands = _Hands(reg_boom="нет регулятора")
    with hands:
        answer = vol.volume({"action": "set", "level": 0})
    assert hands.taps == [(vol._KEY_DOWN, vol._TO_ZERO)], hands.taps
    assert "примерно" in answer, answer


def test_t_blind_set_edges_and_junk():
    hands = _Hands(reg_boom="нет регулятора")
    with hands:
        answer = vol.volume({"action": "set", "level": 300})
        assert hands.taps == [(vol._KEY_DOWN, vol._TO_ZERO),
                              (vol._KEY_UP, 50)], hands.taps
        assert "100" in answer, answer
        del hands.taps[:]
        answer = vol.volume({"action": "set", "level": "громко"})
        assert hands.taps == [], "мусор дошёл до клавиш: %r" % hands.taps
        assert "Не понял" in answer, answer


def test_u_the_reason_is_remembered_and_said_once():
    """Причина отказа больше не глотается — и не повторяется без конца."""
    import io
    import contextlib
    vol._LAST_WHY = ""
    vol._TOLD = False
    box = io.StringIO()
    hands = _Hands(reg_boom="COM поднят другим образом")
    try:
        with hands, contextlib.redirect_stdout(box):
            vol.volume({"action": "up"})
            vol.volume({"action": "down"})
        said = box.getvalue()
        assert "COM поднят" in vol._LAST_WHY, vol._LAST_WHY
        assert "OSError" in vol._LAST_WHY, vol._LAST_WHY
        assert said.count("[Volume]") == 1, said
    finally:
        vol._LAST_WHY = ""
        vol._TOLD = False


def test_v_the_precise_path_stays_silent():
    """Когда всё работает, инструмент не пачкает окно ни одной строкой."""
    import io
    import contextlib
    box = io.StringIO()
    with _Hands(reg=_FakeReg(level=40)), contextlib.redirect_stdout(box):
        vol.volume({"action": "up"})
        vol.volume({"action": "set", "level": 24})
        vol.volume({"action": "status"})
    assert box.getvalue() == "", box.getvalue()


def _run_all():
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    green = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:
            print("FAIL  %s: %r" % (name, exc))
            traceback.print_exc()
        else:
            green += 1
            print("green %s" % name)
    print("RESULT: %d/%d" % (green, len(tests)))
    return 0 if green == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(_run_all())
