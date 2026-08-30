# -*- coding: utf-8 -*-
"""Перепись моделей: что на самом деле доступно по ключу владельца.

Зачем это нужно.

В консоли Google модели показаны человеческими названиями («3.5 Flash Lite»),
а коду нужно то имя, которое понимает API. Они совпадают не всегда: бывает
приставка «models/», хвост «-preview» и дата. Ошибиться здесь дорого: шесть
мест в проекте спрашивают модель прямо во время загрузки файла, поэтому
неверное имя в реестре убивает не одну функцию, а весь запуск.

Что делает инструмент: спрашивает у Google список моделей, доступных этому
ключу, и печатает их. Это перечисление, а не генерация — дневная квота
запросов на нём не тратится.

Чего инструмент не делает: не пишет файлов, не меняет ни настроек, ни
реестра, не печатает сам ключ.

Запуск (из корня проекта):

    python tools/list_models.py

Подробности (лимиты токенов у каждой модели):

    python tools/list_models.py --full
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.loader import ConfigError, get_api_key  # noqa: E402

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"
_TIMEOUT = 30

# Подсказки для группировки. Это НЕ имена моделей, а куски слов, по которым
# удобно глазами найти нужное в длинном списке. Единственное место с именами
# моделей по-прежнему config/registry.yaml.
_HINTS = (
    ("живой голос", ("native-audio", "live")),
    ("лёгкие/тяжёлые вызовы", ("flash",)),
    ("дешёвая болтовня", ("gemma",)),
    ("эмбеддинги", ("embedding", "embed")),
)


def _fetch_page(key: str, page_token: str) -> dict:
    """Одна страница списка. Ключ уходит заголовком, а не в адресе."""
    url = _ENDPOINT + "?pageSize=200"
    if page_token:
        url += "&pageToken=" + urllib.parse.quote(page_token)
    req = urllib.request.Request(url, method="GET")
    req.add_header("x-goog-api-key", key)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_models(key: str) -> list:
    """Все страницы подряд. Пустой список — это тоже честный ответ."""
    out = []
    token = ""
    for _ in range(20):  # предохранитель от бесконечной страницы
        data = _fetch_page(key, token)
        out.extend(data.get("models") or [])
        token = data.get("nextPageToken") or ""
        if not token:
            break
    return out


def _methods(m: dict) -> str:
    return ",".join(m.get("supportedGenerationMethods") or []) or "-"


def _print_model(m: dict, full: bool) -> None:
    name = m.get("name") or "?"
    shown = m.get("displayName") or ""
    print(f"  {name}")
    print(f"      в консоли: {shown}")
    print(f"      умеет:     {_methods(m)}")
    if full:
        print(f"      вход:      {m.get('inputTokenLimit', '?')} токенов")
        print(f"      выход:     {m.get('outputTokenLimit', '?')} токенов")


def _human_error(err: Exception) -> str:
    if isinstance(err, urllib.error.HTTPError):
        code = err.code
        if code in (400, 401, 403):
            return ("Google не принял ключ. Проверьте, что в окне запуска "
                    "введён действующий ключ (старый ротирован).")
        if code == 429:
            return "Google просит подождать: слишком много обращений подряд."
        return f"Google ответил отказом (код {code})."
    if isinstance(err, urllib.error.URLError):
        return "Нет связи с Google: проверьте интернет."
    return f"Не удалось разобрать ответ: {err}"


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    full = "--full" in argv

    try:
        key = get_api_key()
    except ConfigError as e:
        print(f"Ключ не настроен: {e}")
        return 2

    try:
        models = list_models(key)
    except Exception as e:  # noqa: BLE001 — владельцу нужна фраза, а не трасса
        print(_human_error(e))
        return 1

    if not models:
        print("Google вернул пустой список моделей. Это не норма — "
              "похоже, ключ принадлежит проекту без доступа к API.")
        return 1

    print(f"Доступно моделей: {len(models)}\n")
    names = [str(m.get("name") or "") for m in models]

    # Группы взаимоисключающие: модель попадает только в первую подходящую,
    # иначе голосовые видны и в «живом голосе», и в «лёгких вызовах».
    taken: set = set()
    for title, hints in _HINTS:
        picked = []
        for m in models:
            name = str(m.get("name") or "")
            if name in taken:
                continue
            if any(h in name.lower() for h in hints):
                picked.append(m)
                taken.add(name)
        print(f"── {title} ({len(picked)}) " + "─" * 20)
        if not picked:
            print("  ничего не подошло")
        for m in picked:
            _print_model(m, full)
        print()

    rest = [m for m in models if str(m.get("name") or "") not in taken]
    print(f"── остальное ({len(rest)}) " + "─" * 20)
    for m in rest:
        print("  " + str(m.get("name") or "?"))

    print("\nПолный список одной колонкой (удобно скопировать):")
    for n in sorted(names):
        print(n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
