# -*- coding: utf-8 -*-
"""
Громкость системы — узкий инструмент (шаг 31, фаза 0.7).

Почему отдельный файл, а не computer_settings. В computer_settings живут
яркость, wifi, выключение и перезагрузка машины, и он закрыт навсегда.
Сносить ту дверь ради громкости нельзя, поэтому у громкости своя узкая
дверца с шестью действиями и больше ничего.

Два пути, в таком порядке:
  1) точный — pycaw: видно настоящее число, его можно произнести;
  2) запасной — медиа-клавиши: шагаем вслепую и ЧЕСТНО говорим,
     что числа не видим. Выдумывать проценты запрещено.

Правила файла: печатает РОВНО одну строку — причину, по
которой не вышел точный регулятор (в шаге 31 она глоталась молча,
и владелец увидел «pycaw недоступен» при установленном pycaw),
в сеть не ходит, никогда не бросает
исключение наружу и ввозит Windows-части ТОЛЬКО внутри функций:
без этого прогон тестов умрёт на машине без pycaw.

Швы для теста: _regulator() и _tap(). Тест подменяет их и проверяет
оба пути, не крутя настоящую громкость машины.
"""

_STEP = 10          # шаг громкости в процентах на одну просьбу
_TAPS = 5           # столько нажатий клавиши ≈ тот же шаг (по 2 %)
_KEY_MUTE = 0xAD
_KEY_DOWN = 0xAE
_KEY_UP = 0xAF
_KEYUP = 0x0002

_ACTIONS = ("up", "down", "set", "mute", "unmute", "status")

_BLIND_STEP = 2     # столько процентов даёт одно нажатие медиа-клавиши
_TO_ZERO = 60       # столько нажатий вниз гарантированно доводят звук до нуля

_LAST_WHY = ""      # последняя причина, по которой точный путь не вышел
_TOLD = False       # говорим о ней один раз за запуск, а не на каждую просьбу

_NO_NUMBERS = ("Точное число мне не видно, сэр: точный регулятор pycaw"
               " недоступен, шагаю клавишами вслепую.")


def _remember(exc):
    """
    Запомнить и ОДИН раз сказать, почему точный регулятор не вышел.

    На машине владельца pycaw УСТАНОВЛЕН, а точный путь всё равно
    не завёлся. Без текста ошибки чинить можно только гаданием,
    поэтому одна строка в терминал — ровно как у голоса в шаге 29.
    В окно владельцу эта техническая строка НЕ попадает.
    """
    global _LAST_WHY, _TOLD
    _LAST_WHY = "%s: %s" % (type(exc).__name__, exc)
    if not _TOLD:
        _TOLD = True
        print("[Volume] точный регулятор не вышел: " + _LAST_WHY)


def _clamp(value):
    """Любой ввод — в честное число 0..100 или None."""
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, number))


def _regulator():
    """
    Точный регулятор Windows. Шев для теста.

    CoInitialize обязателен: инструменты зовутся из потока, а в новом
    потоке COM по умолчанию не поднят — ровно на этом горел голос в шаге 29.
    """
    from ctypes import POINTER, cast
    import comtypes
    from comtypes import CLSCTX_ALL
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    except Exception:
        from pycaw.api.endpointvolume import IAudioEndpointVolume
        from pycaw.utils import AudioUtilities
    try:
        comtypes.CoInitialize()
    except Exception:
        pass
    speakers = AudioUtilities.GetSpeakers()
    iface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(iface, POINTER(IAudioEndpointVolume))


def _tap(key, times=1):
    """Медиа-клавиша тем же путём, каким нажимает сама клавиатура. Шев."""
    import ctypes
    user32 = ctypes.windll.user32
    for _ in range(max(1, int(times))):
        user32.keybd_event(key, 0, 0, 0)
        user32.keybd_event(key, 0, _KEYUP, 0)


def _read(reg):
    """Сколько сейчас и выключен ли звук."""
    level = _clamp(reg.GetMasterVolumeLevelScalar() * 100)
    return (0 if level is None else level), bool(reg.GetMute())


def _say_level(level, muted):
    if muted:
        return "Звук сейчас выключен, сэр. Громкость стоит на %d процентах." % level
    return "Громкость %d процентов, сэр." % level


def _precise(reg, action, params):
    """Точный путь: видно число до и после, поэтому ответ точный."""
    level, muted = _read(reg)

    if action == "status":
        return _say_level(level, muted)

    if action == "mute":
        if muted:
            return "Звук и так выключен, сэр."
        reg.SetMute(1, None)
        return ("Звук выключен, сэр. Говорить вслух мне теперь бесполезно —"
                " пишите в окно или скажите 'включи звук'.")

    if action == "unmute":
        if not muted:
            return "Звук и так включён, сэр. Громкость %d процентов." % level
        reg.SetMute(0, None)
        return "Звук включён, сэр. Громкость %d процентов." % level

    if action == "set":
        target = _clamp(params.get("level"))
        if target is None:
            return ("Не понял, какое число громкости, сэр. Скажите так:"
                    " поставь громкость тридцать процентов.")
        reg.SetMasterVolumeLevelScalar(target / 100.0, None)
        if muted and target > 0:
            reg.SetMute(0, None)
        now, now_muted = _read(reg)
        return _say_level(now, now_muted)

    if action == "up":
        # Просьба «громче» при выключенном звуке означает «хочу СЛЫШАТЬ».
        if muted:
            reg.SetMute(0, None)
            muted = False
        if level >= 100:
            return "Громкость уже на максимуме, сэр — выше некуда."
        target = _clamp(level + _STEP)
    else:
        if level <= 0:
            return "Громкость уже на нуле, сэр — тише некуда."
        target = _clamp(level - _STEP)

    reg.SetMasterVolumeLevelScalar(target / 100.0, None)
    now, _ = _read(reg)
    return "Готово, сэр: было %d, стало %d процентов." % (level, now)


def _blind(action, params):
    """
    Запасной путь: клавиши. Числа отсюда не видно вообще,
    поэтому в ответах НЕТ ни одного процента: выдуманное число хуже,
    чем честное «не вижу».
    """
    if action in ("mute", "unmute"):
        _tap(_KEY_MUTE)
        return ("Нажал переключатель звука, сэр. " + _NO_NUMBERS
                + " Клавиша одна на оба случая: она переключает.")
    if action == "set":
        target = _clamp(params.get("level"))
        if target is None:
            return ("Не понял, какое число громкости, сэр. Скажите так:"
                    " поставь громкость тридцать процентов.")
        # Числа отсюда не видно, но нуль — единственная точка, которую
        # можно нащупать вслепую: уводим звук в нуль с запасом, потом
        # отсчитываем шаги наверх. Слово «примерно» обязательно: если
        # у звуковой карты шаг не два процента, число разойдётся,
        # а врать точностью хуже, чем сказать «примерно».
        _tap(_KEY_DOWN, _TO_ZERO)
        ups = int(round(target / float(_BLIND_STEP)))
        if ups:
            _tap(_KEY_UP, ups)
        return ("Поставил примерно %d процентов, сэр: точного регулятора"
                " нет, поэтому я убрал звук в нуль и отсчитал шагами по два"
                " процента. Если у вашей звуковой карты шаг другой,"
                " число немного разойдётся." % target)
    if action == "status":
        return "Звук на месте, сэр. " + _NO_NUMBERS
    _tap(_KEY_UP if action == "up" else _KEY_DOWN, _TAPS)
    word = "Громче" if action == "up" else "Тише"
    return word + ", сэр. " + _NO_NUMBERS


def volume(parameters=None):
    """
    Единственная дверца инструмента. Всегда возвращает строку для владельца.

    Неизвестное действие отбиваем сами: общая дверь проекта незнакомое
    ДЕЙСТВИЕ у разрешённого инструмента пропускает (проверено на коде),
    а менять общие правила двери в шаге про громкость нельзя.
    """
    params = dict(parameters or {})
    action = str(params.get("action") or "status").strip().lower()
    if action not in _ACTIONS:
        return "Такого с громкостью я не умею, сэр: %s." % action

    reg = None
    try:
        reg = _regulator()
    except Exception as exc:
        _remember(exc)
        reg = None

    if reg is not None:
        try:
            return _precise(reg, action, params)
        except Exception as exc:
            _remember(exc)

    try:
        return _blind(action, params)
    except Exception as exc:
        _remember(exc)
        return ("Не смог тронуть громкость, сэр: ни точный регулятор,"
                " ни клавиши не отвечают.")
