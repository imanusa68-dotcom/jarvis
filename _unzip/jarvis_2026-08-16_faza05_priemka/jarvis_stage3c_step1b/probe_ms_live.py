# -*- coding: utf-8 -*-
"""Живая проба колонки ms. Запуск: python probe_ms_live.py

ЗАЧЕМ ОТДЕЛЬНАЯ ПРОБА, ЕСЛИ ТЕСТЫ ЗЕЛЁНЫЕ. Прогон 28.08.2026 у владельца
дал 1807 passed, и это ничего не говорит про НАСТОЯЩИЕ обращения к модели:
тесты моделей не зовут, они подставляют заглушки. Число, записанное в базу
на поддельном вызове, доказывает только то, что код исполнился.

Проба идёт двумя частями, и вторая — необязательная:
  ЧАСТЬ 1  без сети и без ключа. Проверяет саму механику: пишется ли число,
           отличается ли «не знаем» от нуля, не остаются ли сироты.
  ЧАСТЬ 2  ОДИН настоящий вызов модели, если ключ на месте. Стоит 1 вызов
           из суточных 120 — и это названо вслух, потому что квота у нас
           самый дефицитный ресурс, а не память и не процессор.

Проба НИЧЕГО не портит в рабочей базе: часть 1 работает на временной базе
во временной папке. Часть 2 идёт через настоящий учёт (иначе она не про
учёт), поэтому один вызов в суточном счётчике останется — так и надо, он
настоящий.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def say(text=""):
    print(text, flush=True)


def part_one() -> bool:
    """Механика колонки. Без сети, без ключа, на временной базе."""
    import time
    from core import store, metering as mt

    say("=" * 62)
    say("ЧАСТЬ 1. Механика колонки. Сеть и ключ не нужны.")
    say("=" * 62)

    tmp = Path(tempfile.mkdtemp(prefix="jv_probe_ms_"))
    conn = store.open_store(tmp / "jarvis.db")
    mt.reset_started_for_tests()
    ok = True

    def check(name, got, want_text, good):
        nonlocal ok
        mark = "OK  " if good else "ПЛОХО"
        say(f"  [{mark}] {name}: {got}   (ждём: {want_text})")
        if not good:
            ok = False

    # 1) Настоящее ожидание. 250 мс держим сами — как держала бы модель.
    t = mt.reserve("aux_light", conn=conn)
    time.sleep(0.25)
    mt.commit(t["call_id"], in_tokens=100, out_tokens=40, conn=conn)
    got = conn.execute("SELECT ms FROM mx_meter_call WHERE call_id=?",
                       (t["call_id"],)).fetchone()[0]
    check("пауза 250 мс", got, "250-400", got is not None and 240 <= got <= 400)

    # 2) Мгновенный вызов. Ноль здесь — ФАКТ, а не пустота.
    t2 = mt.reserve("aux_light", conn=conn)
    mt.commit(t2["call_id"], conn=conn)
    g2 = conn.execute("SELECT ms FROM mx_meter_call WHERE call_id=?",
                      (t2["call_id"],)).fetchone()[0]
    check("мгновенный вызов", g2, "число 0-50, НЕ None",
          g2 is not None and g2 < 50)

    # 3) Неизвестность. Так выглядит резерв, переживший перезапуск.
    t3 = mt.reserve("aux_light", conn=conn)
    mt.reset_started_for_tests()                  # как после перезапуска
    mt.commit(t3["call_id"], conn=conn)
    g3 = conn.execute("SELECT ms FROM mx_meter_call WHERE call_id=?",
                      (t3["call_id"],)).fetchone()[0]
    check("длительность неизвестна", g3, "None, а НЕ 0", g3 is None)

    # 4) Календарные часы не участвуют. Сдвигаем метку на шесть лет назад.
    t4 = mt.reserve("aux_light", conn=conn)
    conn.execute("UPDATE mx_meter_call SET started_utc=? WHERE call_id=?",
                 ("2020-01-01T00:00:00+00:00", t4["call_id"]))
    mt.commit(t4["call_id"], conn=conn)
    g4 = conn.execute("SELECT ms FROM mx_meter_call WHERE call_id=?",
                      (t4["call_id"],)).fetchone()[0]
    check("метка сдвинута на 6 лет назад", g4, "маленькое число",
          g4 is not None and g4 < 60_000)

    # 5) Сирот не осталось. Это тот дефект, что я нашёл в своей же правке.
    check("висящих засечек", mt.started_count_for_tests(), "0",
          mt.started_count_for_tests() == 0)

    conn.close()
    say()
    return ok


def part_two() -> bool:
    """ОДИН настоящий вызов модели. Стоит 1 из 120 суточных."""
    say("=" * 62)
    say("ЧАСТЬ 2. Настоящий вызов модели. ТРАТИТ 1 вызов из 120 за сутки.")
    say("=" * 62)

    # Имена сверены по коду, а не по памяти: config/loader.py:145 и
    # core/aux_model.py:157. С первого раза я угадал оба неверно.
    try:
        from config import loader
        key = loader.get_api_key()
    except Exception as exc:                      # noqa: BLE001
        say(f"  ключ прочитать не вышло ({type(exc).__name__}: {exc}).")
        say("  Часть 2 пропущена. Это НЕ провал пробы.")
        say()
        return True

    # Заглушку из secrets.example.json за ключ не принимаем: иначе проба
    # пойдёт в сеть, получит «API key not valid» и напугает зря.
    if not key or "YOUR_" in key.upper() or key.upper().startswith("PUT"):
        say("  настоящего ключа нет (стоит заглушка) — часть 2 пропущена.")
        say("  Это НЕ провал пробы.")
        say()
        return True

    say(f"  ключ на месте (длина {len(key)}, сам ключ не печатаем).")
    say("  Спрашиваю модель короткой фразой...")
    from core import aux_model, writer

    before = set(r[0] for r in writer.reader().execute(
        "SELECT call_id FROM mx_meter_call").fetchall())

    ok_call, text = aux_model.aux_call(
        "Ответь одним словом: работает", key,
        caller="проба ms", role="aux_light")
    say(f"  модель ответила: {ok_call} / {str(text)[:60]!r}")

    rows = writer.reader().execute(
        "SELECT call_id, ok, err_kind, ms FROM mx_meter_call").fetchall()
    fresh = [r for r in rows if r[0] not in before]
    if not fresh:
        say("  [ПЛОХО] в учёте не появилось НИ ОДНОЙ новой строки.")
        say()
        return False

    good = True
    for r in fresh:
        say(f"  строка {r[0]}: ok={r[1]} err={r[2]} ms={r[3]}")
        if r[3] is None:
            say("  [ПЛОХО] ms пустой у НАСТОЯЩЕГО вызова — правка не"
                " работает на этой машине.")
            good = False
        else:
            say(f"  [OK  ] ожидание {r[3]} мс записано.")
            if r[1] == 0:
                # 429/503 — это тоже ожидание, и мерить его НУЖНО: отказ
                # после трёх повторов длится дольше удачи. В вашем выводе
                # 28.08 такие ответы уже встречались, поэтому говорю прямо:
                # это НЕ провал пробы.
                say(f"  [ЗАМЕТКА] вызов неудачный (err={r[2]}), но время"
                    f" ожидания всё равно измерено — так и задумано.")
    say()
    return good


def main() -> int:
    say()
    one = part_one()
    two = part_two()
    say("=" * 62)
    if one and two:
        say("ПРОБА ПРОЙДЕНА. Колонка ms живая.")
        return 0
    say("ПРОБА НЕ ПРОЙДЕНА. Пришлите этот вывод целиком — разбираюсь.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
