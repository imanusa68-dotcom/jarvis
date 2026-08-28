"""
Проверка внедрения задачи 009 (шаги 1-4) вручную, без Джарвиса.

Запуск из корня проекта:
    python tools/check_active_document.py

Скрипт даёт 5 секунд, чтобы ты переключился на нужное окно (Word, Excel,
VS Code, Блокнот, браузер), потом определяет активный документ и печатает
всю карточку ответа: путь, откуда взят, насколько уверен, сколько миллисекунд
заняло и что именно сказал бы Джарвис голосом.

Ничего не меняет на диске: только читает заголовок окна и спрашивает Office об его
же собственном документе.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.awareness import _inspectors as ins  # noqa: E402

PAUSE_S = 5
SAMPLES = 2


def _direct_window() -> dict | None:
    """Заголовок переднего окна напрямую через Windows API.

    Обычно эти данные берутся из модели мира, которую ведёт фоновый наблюдатель
    Джарвиса. Здесь читаем сами, чтобы проверка работала даже когда Джарвис
    выключен.
    """
    try:
        import win32gui
        import win32process
    except Exception as exc:
        print(f"[!] pywin32 недоступен: {exc}")
        return None
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd) or ""
        process = ""
        try:
            import psutil
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process = psutil.Process(pid).name()
        except Exception:
            pass
        return {"title": title, "process": process, "hwnd": int(hwnd)}
    except Exception as exc:
        print(f"[!] не удалось прочитать активное окно: {exc}")
        return None


def _report(result: dict) -> None:
    print(f"  заголовок окна : {result.get('_title', '')!r}")
    print(f"  процесс       : {result.get('app') or '-'}")
    print(f"  найден путь  : {result.get('found')}")
    print(f"  путь          : {result.get('path') or '-'}")
    print(f"  имя           : {result.get('name') or '-'}")
    print(f"  тип           : {result.get('kind')}")
    print(f"  источник      : {result.get('source')}")
    print(f"  уверенность   : {result.get('confidence')}")
    print(f"  несохранёно   : {result.get('dirty')}")
    print(f"  кандидаты     : {result.get('candidates') or '-'}")
    print(f"  время, мс     : {result.get('elapsed_ms')}")
    print(f"  причина       : {result.get('reason') or '-'}")
    print(f"  СКАЗАЛ БЫ     : {ins.render(result)}")


def main() -> int:
    print("=" * 72)
    print("Проверка 009 — «Какой документ открыт сейчас?»")
    print("=" * 72)
    print(f"Переключись на нужное окно. Замер через {PAUSE_S} секунд...")
    for left in range(PAUSE_S, 0, -1):
        print(f"  {left}...", end="\r", flush=True)
        time.sleep(1)
    print(" " * 20)

    # Подменяем только источник данных об окне, всё остальное — настоящее:
    # тот же разбор заголовка, тот же COM, те же проверки пути.
    window = _direct_window()
    if not window or not window["title"]:
        print("Не удалось прочитать активное окно — дальше проверять нечего.")
        return 1

    ins.active_window = lambda: window
    ins.reset()

    for i in range(1, SAMPLES + 1):
        print(f"\n── замер {i} из {SAMPLES} " + "─" * 40)
        started = time.monotonic()
        result = ins.active_document(use_cache=(i > 1))
        wall_ms = int((time.monotonic() - started) * 1000)
        result["_title"] = window["title"]
        _report(result)
        print(f"  фактически, мс: {wall_ms}  (второй замер должен быть почти 0 — кэш)")

    print("\n── состояние защит " + "─" * 40)
    for key, value in ins.stats().items():
        print(f"  {key}: {value}")

    print("\n── самопроверка главного правила " + "─" * 25)
    ok = True
    final = ins.active_document(use_cache=False)
    path = final.get("path")
    if path:
        if not ins.is_absolute_path(path):
            print("  ПРОВАЛ: выдан неабсолютный путь")
            ok = False
        elif not os.path.exists(path):
            print("  ПРОВАЛ: выдан путь, которого нет на диске")
            ok = False
        else:
            print("  ОК: путь абсолютный и файл реально существует")
    else:
        print("  ОК: путь не выдан, вместо него честная причина (это тоже верный ответ)")
    if final.get("elapsed_ms", 0) > 900:
        print(f"  ПРОВАЛ: дольше бюджета — {final['elapsed_ms']} мс")
        ok = False
    else:
        print(f"  ОК: уложился в бюджет — {final.get('elapsed_ms')} мс")

    print("\nИТОГ: " + ("внедрено правильно" if ok else "есть проблема, см. выше"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
