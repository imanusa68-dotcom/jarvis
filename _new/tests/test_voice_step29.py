# -*- coding: utf-8 -*-
"""
Шаг 29. Свой голос, когда сети нет.

Почему: голос, который владелец слышит онлайн, присылает Google по проводу
(audio_in_queue -> pyaudio). Провод отвалился — рот исчез, и ядро только
печатало в окно. Здесь у Джарвиса появляется собственный рот: системный
голос Windows. Он звучит ТОЛЬКО когда сети нет — иначе два рта заговорят
разом поверх друг друга.

Д-5 попутно: в окно (и теперь в уши) уходила служебная команда для модели
«[НАПОМИНАНИЕ] Немедленно скажи мне вслух следующее напоминание: ...».
Живой голос зачитал бы её владельцу дословно. Для ушей и глаз нужен
человеческий текст, для модели — прежняя команда, слово в слово.

Сети, COM и звука здесь нет: SAPI — двойник, поток — двойник, ядро настоящее.
Этот файл НИЧЕГО не пишет в папку проекта (урок шага 28: тест поставил
владельцу живое напоминание, и оно выстрелило ему в лицо).

Run standalone: python tests/test_voice_step29.py
"""
import ast
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MAIN_SRC = (ROOT / "main.py").read_text(encoding="utf-8")
SAY_PATH = ROOT / "core" / "say_local.py"

WANTED = ("_on_text_command", "_answer_offline", "_deliver_reminder", "_say_local")

SERVICE_PREFIX = "[НАПОМИНАНИЕ] Немедленно скажи мне вслух следующее напоминание: "


# ───────────────────────── двойники SAPI ─────────────────────────

class _Token:
    """Один системный голос в списке Windows."""

    def __init__(self, description, language):
        self._desc = description
        self._lang = language

    def GetDescription(self):  # noqa: N802 - имя из COM
        return self._desc

    def GetAttribute(self, name):  # noqa: N802 - имя из COM
        if name == "Language":
            return self._lang
        raise KeyError(name)


class _SpVoice:
    """Двойник SAPI.SpVoice: помнит, что и как сказали."""

    def __init__(self, tokens=None, explode=False):
        self._tokens = tokens if tokens is not None else [
            _Token("Microsoft Zira Desktop - English (United States)", "409"),
            _Token("Microsoft Irina Desktop - Russian", "419"),
        ]
        self._explode = explode
        self.said = []
        self.flags = []
        self.waited = []
        self.Voice = None

    def GetVoices(self):  # noqa: N802 - имя из COM
        return self._tokens

    def Speak(self, text, flags=0):  # noqa: N802 - имя из COM
        if self._explode:
            raise RuntimeError("COM умер посреди фразы")
        self.said.append(text)
        self.flags.append(flags)
        return 1

    def WaitUntilDone(self, ms=0):  # noqa: N802 - имя из COM
        self.waited.append(ms)
        return True


def _dispatcher(voice):
    """Подменённая фабрика COM-объекта."""
    def _make(name):
        assert name == "SAPI.SpVoice", name
        return voice
    return _make


# ───────────────────────── двойники main.py ─────────────────────────

class _Ui:
    def __init__(self):
        self.log = []

    def write_log(self, text):
        self.log.append(text)


class _Sm:
    def __init__(self, writable):
        self._writable = writable

    def is_writable(self):
        return self._writable


class _Thread:
    """Поток-двойник: выполняет работу сразу, но помнит, что его завели."""

    started = []

    def __init__(self, target=None, name=None, daemon=None, args=(), kwargs=None):
        self.target = target
        self.name = name
        self.daemon = daemon
        self.args = args
        self.kwargs = kwargs or {}

    def start(self):
        _Thread.started.append(self)
        self.target(*self.args, **self.kwargs)


class _FakeThreading:
    Thread = _Thread


class _FakeAsyncio:
    def __init__(self):
        self.calls = []

    def run_coroutine_threadsafe(self, coro, loop):
        self.calls.append(coro)
        return _Delivered()


class _Delivered:
    def result(self, timeout=None):
        return True


class _Live:
    def __init__(self, online):
        self.ui = _Ui()
        self._loop = object() if online else None
        self._sm = _Sm(online)
        self.sent = []
        self.spoken = []

    def _safe_send_text(self, text):
        self.sent.append(text)
        return "coro:" + text

    def speak(self, text):
        self.spoken.append(text)


class _VoiceSpy(types.ModuleType):
    """Подменённый core.say_local: ловит всё, что уходит в уши."""

    def __init__(self, real_module):
        super().__init__("core.say_local")
        self.heard = []

        def say(text):
            self.heard.append(text)
            return True

        self.say = say
        # Разбор текста берём у настоящего модуля, пойманного ДО подмены:
        # иначе шпион позвал бы сам себя и ушёл в бесконечную рекурсию.
        self.human_reminder = real_module.human_reminder


def _live(online=False, voice_spy=None):
    """Собрать двойника с живыми методами из main.py."""
    tree = ast.parse(MAIN_SRC)
    cls = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "JarvisLive":
            cls = node
            break
    assert cls is not None, "в main.py нет класса JarvisLive"
    picked = [n for n in cls.body
              if isinstance(n, ast.FunctionDef) and n.name in WANTED]
    missing = set(WANTED) - {n.name for n in picked}
    assert not missing, "в main.py нет методов: %s" % ", ".join(sorted(missing))
    module = ast.Module(body=picked, type_ignores=[])
    ast.fix_missing_locations(module)
    space = {
        "asyncio": _FakeAsyncio(),
        "threading": _FakeThreading,
        "print": lambda *a, **k: None,
    }
    exec(compile(module, "main.py<extract>", "exec"), space)  # noqa: S102
    rig = _Live(online)
    for name in WANTED:
        setattr(rig, name, types.MethodType(space[name], rig))
    if voice_spy is not None:
        sys.modules["core.say_local"] = voice_spy
    return rig


def _fresh(online=False):
    _Thread.started = []
    import core.say_local as real_mod  # настоящий модуль ДО подмены
    spy = _VoiceSpy(real_mod)
    real = sys.modules.get("core.say_local")
    rig = _live(online=online, voice_spy=spy)
    return rig, spy, real


def _restore(real):
    if real is not None:
        sys.modules["core.say_local"] = real
    else:
        sys.modules.pop("core.say_local", None)


# ───────────────────────── проверки: сам голос ─────────────────────────

def test_a_the_voice_module_exists():
    """Без своего рта весь шаг бессмыслен."""
    assert SAY_PATH.exists(), "нет файла core/say_local.py"


def test_b_the_voice_never_reaches_for_the_network():
    """Рот без сети не имеет права ходить в сеть."""
    src = SAY_PATH.read_text(encoding="utf-8")
    for bad in ("genai", "requests", "urllib", "socket", "http"):
        assert bad not in src, "голос тянется в сеть: %s" % bad


def test_c_windows_is_imported_lazily():
    """Верхний импорт pywin32 уронит проект на любой не-Windows машине."""
    src = SAY_PATH.read_text(encoding="utf-8")
    for line in src.splitlines():
        if line.startswith("import ") or line.startswith("from "):
            assert "win32" not in line and "pythoncom" not in line, line


def test_d_ears_do_not_hear_the_window_furniture():
    """«Jarvis:» и разделители — для глаз, не для ушей."""
    import core.say_local as sl
    got = sl.clean_for_ears("Jarvis: время и дата · напоминания · заметка")
    assert not got.startswith("Jarvis"), got
    assert "·" not in got, got
    assert "время и дата" in got, got


def test_e_empty_text_is_not_spoken():
    """Пустая фраза не должна дёргать COM вообще."""
    import core.say_local as sl
    voice = _SpVoice()
    assert sl.say("   ", dispatch=_dispatcher(voice)) is False
    assert voice.said == [], voice.said


def test_f_the_phrase_is_actually_spoken():
    import core.say_local as sl
    voice = _SpVoice()
    assert sl.say("Сейчас 12:47", dispatch=_dispatcher(voice)) is True
    assert voice.said == ["Сейчас 12:47"], voice.said


def test_g_russian_voice_is_picked_by_language_not_by_name():
    """Имя «Irina» есть не на каждой машине; язык 419 — есть."""
    import core.say_local as sl
    voice = _SpVoice()
    sl.say("привет", dispatch=_dispatcher(voice))
    assert voice.Voice is not None, "русский голос не выбран вообще"
    assert voice.Voice.GetAttribute("Language") == "419", \
        voice.Voice.GetDescription()


def test_h_without_a_russian_voice_it_still_speaks():
    """Нет русского — говорим чем есть, а не молчим."""
    import core.say_local as sl
    only_english = [_Token("Microsoft Zira Desktop", "409")]
    voice = _SpVoice(tokens=only_english)
    assert sl.say("привет", dispatch=_dispatcher(voice)) is True
    assert voice.said == ["привет"], voice.said


def test_i_speech_does_not_block_the_caller():
    """Флаг 1 = SVSFlagsAsync. Без него окно замрёт на время фразы."""
    import core.say_local as sl
    voice = _SpVoice()
    sl.say("длинная фраза", dispatch=_dispatcher(voice))
    assert voice.flags == [1], voice.flags


def test_j_a_dead_com_is_silence_not_a_crash():
    import core.say_local as sl
    voice = _SpVoice(explode=True)
    assert sl.say("привет", dispatch=_dispatcher(voice)) is False


def test_k_the_service_order_never_reaches_the_owner():
    """Д-5: команда для модели не должна звучать вслух."""
    import core.say_local as sl
    got = sl.human_reminder(SERVICE_PREFIX + "выключить чайник")
    assert "Немедленно скажи" not in got, got
    assert "НАПОМИНАНИЕ]" not in got, got
    assert "выключить чайник" in got, got


def test_l_a_plain_reminder_is_left_alone():
    import core.say_local as sl
    assert sl.human_reminder("Напоминание: чайник") == "Напоминание: чайник"


# ───────────────────────── проверки: main.py ─────────────────────────

def test_m_offline_answer_is_spoken_out_loud():
    """Главное этого вечера: без сети он ГОВОРИТ, а не только печатает."""
    rig, spy, real = _fresh(online=False)
    try:
        rig._answer_offline("сколько времени")
        assert any("Jarvis:" in x for x in rig.ui.log), rig.ui.log
        assert spy.heard, "ответ напечатан, но не произнесён"
        assert "Сейчас" in spy.heard[0], spy.heard
    finally:
        _restore(real)


def test_n_only_one_mouth_when_the_line_is_alive():
    """Связь жива — говорит модель. Свой голос обязан молчать."""
    rig, spy, real = _fresh(online=True)
    try:
        rig._say_local("я не должен звучать")
        assert spy.heard == [], spy.heard
    finally:
        _restore(real)


def test_o_the_voice_runs_in_its_own_thread():
    """Речь не имеет права держать рабочий поток владельца."""
    rig, spy, real = _fresh(online=False)
    try:
        rig._say_local("фраза")
        assert _Thread.started, "голос звучит прямо в рабочем потоке"
        assert _Thread.started[0].daemon is True, "поток переживёт выход"
    finally:
        _restore(real)


def test_p_offline_reminder_is_human_in_the_window():
    """Д-5: в окне человеческий текст, а не приказ модели."""
    rig, spy, real = _fresh(online=False)
    try:
        rig._deliver_reminder(SERVICE_PREFIX + "выключить чайник")
        shown = " ".join(rig.ui.log)
        assert "выключить чайник" in shown, rig.ui.log
        assert "Немедленно скажи" not in shown, rig.ui.log
    finally:
        _restore(real)


def test_q_offline_reminder_is_spoken_too():
    rig, spy, real = _fresh(online=False)
    try:
        rig._deliver_reminder(SERVICE_PREFIX + "выключить чайник")
        assert spy.heard, "напоминание молча легло в окно"
        assert "выключить чайник" in spy.heard[0], spy.heard
        assert "Немедленно скажи" not in spy.heard[0], spy.heard
    finally:
        _restore(real)


def test_r_live_reminder_still_goes_to_the_model_verbatim():
    """Регресс: при живой связи приказ модели менять нельзя."""
    rig, spy, real = _fresh(online=True)
    try:
        rig._deliver_reminder(SERVICE_PREFIX + "выключить чайник")
        assert rig.spoken == [SERVICE_PREFIX + "выключить чайник"], rig.spoken
        assert spy.heard == [], "свой голос влез при живой связи"
    finally:
        _restore(real)


def test_s_step28_win_did_not_move():
    """Фраза в умирающую сессию по-прежнему доходит до рук."""
    rig, spy, real = _fresh(online=False)
    try:
        rig._on_text_command("сколько времени")
        assert any("Сейчас" in x for x in rig.ui.log), rig.ui.log
    finally:
        _restore(real)


def test_t_this_file_leaves_no_reminder_behind():
    """Урок шага 28: тест не смеет трогать данные владельца."""
    assert not (ROOT / "memory" / "reminders.json").exists(), \
        "тест оставил живое напоминание в папке проекта"


def test_u_the_mouth_waits_for_the_phrase_to_end():
    """
    Главный урок вечера 13.08.2026.

    Первая редакция шага 29 прошла все двадцать проверок и молчала вживую:
    фраза уходила в очередь, поток тут же умирал, очередь умирала с ним.
    Двойник не умеет слышать тишину — поэтому проверяем факт ожидания.
    """
    import core.say_local as sl
    voice = _SpVoice()
    assert sl.say("Jarvis: проверка", dispatch=_dispatcher(voice)) is True
    assert voice.waited, "рот не дождался конца фразы — звука не будет"
    assert voice.waited[0] > 0, voice.waited
    assert voice.flags == [1], voice.flags


def test_v_two_phrases_never_talk_over_each_other():
    """Ответ и напоминание могут совпасть: в комнате один рот."""
    import threading as _t
    import time as _time

    import core.say_local as sl

    state = {"busy": False, "overlap": False}

    class _SlowVoice:
        def __init__(self):
            self.Voice = None

        def GetVoices(self):
            return []

        def Speak(self, text, flags=0):  # noqa: N802
            if state["busy"]:
                state["overlap"] = True
            state["busy"] = True
            _time.sleep(0.05)
            state["busy"] = False
            return 1

        def WaitUntilDone(self, ms=0):  # noqa: N802
            return True

    def talk():
        sl.say("Jarvis: фраза", dispatch=lambda name: _SlowVoice())

    threads = [_t.Thread(target=talk) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert state["overlap"] is False, "два голоса заговорили одновременно"


def test_w_an_old_double_without_waiting_still_speaks():
    """Чужой двойник без WaitUntilDone не должен ронять рот."""
    import core.say_local as sl

    class _Dumb:
        def __init__(self):
            self.said = []
            self.Voice = None

        def GetVoices(self):
            return []

        def Speak(self, text, flags=0):  # noqa: N802
            self.said.append(text)
            return 1

    dumb = _Dumb()
    assert sl.say("Jarvis: проверка", dispatch=lambda name: dumb) is True
    assert dumb.said == ["проверка"], dumb.said


def _main():
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    ok = 0
    for name, fn in tests:
        try:
            fn()
            ok += 1
            print("  PASS ", name)
        except Exception as e:  # noqa: BLE001
            print("  FAIL ", name, "->", type(e).__name__, e)
    print("RESULT: %d/%d" % (ok, len(tests)))
    return 0 if ok == len(tests) else 1


if __name__ == "__main__":
    sys.exit(_main())
