# tools/perception_trace.py
# "Он ответил неправильно" — и дальше начинались догадки.
#
# Этот скрипт показывает ВСЁ, на чём построен ответ о том, что сейчас на экране:
# какое окно увидели, какой файл вытащили из заголовка, какие признаки сработали,
# сколько очков набрала каждая версия, сколько миллисекунд стоил каждый источник.
#
#   python tools/perception_trace.py                — про передний план
#   python tools/perception_trace.py happ           — про окно по имени
#   python tools/perception_trace.py --all          — все окна
#   python tools/perception_trace.py --watch        — повторять каждые 2 секунды

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.awareness import _perception as pc   # noqa: E402


def one(target: str, hint: str = "") -> None:
    started = time.monotonic()
    subject = pc.describe(target, hint)
    total = int((time.monotonic() - started) * 1000)

    print("=" * 72)
    print(f"ВОПРОС     : target={target!r} name={hint!r}")
    print(f"ОТВЕТ     : {pc.render_subject(subject)}")
    print(f"ВРЕМЯ      : {total} мс")
    print("-" * 72)
    print(pc.trace(subject))

    window = subject.get("window") or {}
    if window.get("title"):
        artifact = pc.extract_artifact(window.get("title"), window.get("process"))
        verdict = pc.score(window, artifact)
        print("-" * 72)
        print(f"СЧЁТ      : {verdict['scores']}")
        for why in verdict["reasons"]:
            print(f"  • {why}")
    print("=" * 72)


def main() -> int:
    args = [a for a in sys.argv[1:]]
    watch = "--watch" in args
    args = [a for a in args if a != "--watch"]

    if "--all" in args:
        target, hint = "all", ""
    elif args:
        target, hint = "window", " ".join(args)
    else:
        target, hint = "foreground", ""

    if not watch:
        one(target, hint)
        return 0

    print("Ctrl+C чтобы остановить.")
    try:
        while True:
            one(target, hint)
            time.sleep(2.0)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
