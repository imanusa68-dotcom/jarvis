# -*- coding: utf-8 -*-
"""
Проверка фазы 1б ПОСЛЕ РАЗГОВОРА: читает настоящий журнал двери.

    python check_faza1b.py     — проверяет код, модели не касается
    python check_dialog.py     — проверяет, что осталось в журнале ПОСЛЕ того,
                                 как вы поговорили с Джарвисом

Этот файл НИЧЕГО НЕ ПИШЕТ. Он только читает
    %USERPROFILE%\\.jarvis\\logs\\gate-audit.jsonl
и переводит его на человеческий язык. Запускать можно сколько угодно раз,
в том числе при работающем Джарвисе.

ПОРЯДОК РАБОТЫ
    1. Запустить Джарвиса.
    2. Сказать вслух: «Джарвис, какая погода в Москве?»
    3. Не закрывая Джарвиса, в другом окне: python check_dialog.py

ПОЧЕМУ ЗДЕСЬ НЕ «ЗАПОМНИ, ЧТО Я НЕ ПЬЮ КОФЕ ПОСЛЕ ШЕСТИ»
Критерий плана (строка 1045) просит именно эту фразу и обещает, что после
неё в журнале появится строка с origin_chain. ПРОВЕРЕНО ЗАМЕРОМ 28.08.2026 —
не появится, и виноват не забор:

    main.py, метод _execute_tool
        строка 1260   if name == "save_memory": ...
        строка 1281   return  <- диалог ЗАКАНЧИВАЕТСЯ ЗДЕСЬ
        строка 1331   комментарий: "save_memory is handled above and is
                      intentionally not gated here"
        строка 1334   дверь

То есть голосовое «запомни» в диалоге до двери НЕ ДОХОДИТ вовсе, а значит
и строки в журнале не оставляет. Это не дыра в защите: писать в память
разрешено только вам, а в диалоге просите именно вы. Это пункт 6 плана
(фильтр записи в память, I35), и он требует правки ЗАМОРОЖЕННОГО main.py —
на что я без вашего слова не пойду.

Поэтому диалоговый тест здесь сделан на инструменте, который через дверь
проходит по-настоящему (погода), плюс проверка забора на фоновой задаче.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_DIR = ".jarvis"
LOG = Path(os.environ.get("JARVIS_STATE_DIR", "").strip()
           or (Path.home() / APP_DIR)) / "logs" / "gate-audit.jsonl"


def read_lines() -> list[dict]:
    if not LOG.exists():
        return []
    out = []
    for raw in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except Exception:
            # Незнакомую строку пропускаем, а не падаем на ней: правило 5
            # core/audit_log.py — формат только дополняется.
            continue
    return out


def human(line: dict) -> str:
    chain = " -> ".join(line.get("origin_chain") or []) or "нет цепочки"
    role = line.get("agent_role") or "вы (владелец)"
    return (f'  {line.get("ts", "?")}  {line.get("tool")}'
            f'{"/" + line["action"] if line.get("action") else ""}\n'
            f'      вердикт : {line.get("verdict")}   режим: {line.get("mode")}\n'
            f'      просил  : {chain}\n'
            f'      роль    : {role}   глубина: {line.get("depth")}\n'
            f'      параметры: {line.get("param_keys")}'
            + (f'\n      причина : {line["reason"]}' if line.get("reason") else ""))


def main() -> int:
    print("=" * 70)
    print("ЖУРНАЛ ДВЕРИ ПОСЛЕ РАЗГОВОРА")
    print("=" * 70)
    print(f"файл: {LOG}")

    lines = read_lines()
    if not lines:
        print("\nЖурнал пуст или его нет.")
        print("Это значит одно из двух:")
        print("  - Джарвис ещё ни разу не вызывал инструмент в этом доме;")
        print("  - вы ещё ничего не просили голосом.")
        print("\nСкажите: «Джарвис, какая погода в Москве?» и запустите снова.")
        return 1

    # РАЗДЕЛЕНИЕ СТРОК ПО ПИСАТЕЛЮ — ИСПРАВЛЕНИЕ МОЕЙ ОШИБКИ, 28.08.2026.
    # Первая версия считала, что в gate-audit.jsonl пишет только дверь. Прогон
    # владельца это опроверг на 78 строках настоящего журнала: пришло
    #     [СБОЙ] лишние поля: event, tool_ver, to, pre_rollback, restored,
    #            side_removed, fts, state, refused
    # Это НЕ утечка и НЕ поломка. Это `tools/rollback_state.py:276` — откат
    # состояния пишет в тот же журнал свою строку с `event="state_rollback"`,
    # и правильно делает: журнал один, свидетельства о состоянии тоже.
    # У строк двери поля `event` нет вовсе — по нему и различаем.
    doors = [x for x in lines if "event" not in x]
    others = [x for x in lines if "event" in x]

    print(f"\nвсего строк: {len(lines)}"
          + (f"   (решений двери {len(doors)}, прочих событий {len(others)})"
             if others else ""))
    print("\nПОСЛЕДНИЕ 5 РЕШЕНИЙ ДВЕРИ:")
    for line in (doors[-5:] or lines[-5:]):
        print(human(line))

    problems = []

    # 1. Цепочка обязана быть в каждой строке двери — но ТОЛЬКО В НОВЫХ.
    #
    # ВТОРАЯ МОЯ ОШИБКА, найденная тем же прогоном: пришло
    #     [СБОЙ] строк без origin_chain: 75 из 78
    # и это было ЛОЖНОЕ ОБВИНЕНИЕ. Журнал владельца живёт с 23 августа, а
    # подпись «кто просил» появилась 28-го, в фазе 1б-1. Семьдесят пять старых
    # строк цепочки не имеют просто потому, что тогда её не существовало.
    #
    # Требовать её от них — значит требовать, чтобы прошлое переписалось. Хуже:
    # такой скрипт кричит «СБОЙ» вечно, и владелец перестаёт ему верить —
    # ровно то, чем красный сторож опаснее отсутствующего.
    #
    # Правило 5 `core/audit_log.py` прямо это предусматривает: «формат только
    # дополняется, никогда не переписывается», и читатель обязан пропускать
    # незнакомое, а не падать. Поэтому граница — ПЕРВАЯ строка с цепочкой:
    # всё после неё обязано её иметь, всё до неё — история.
    print("\n" + "-" * 70)
    first_new = next((i for i, x in enumerate(doors) if x.get("origin_chain")),
                     None)
    if not doors:
        # Отдельная ветка, иначе печаталось «ни в одной из 0 строк» — фраза,
        # которая выглядит как обвинение, а на деле означает «нечего смотреть».
        print("[--  ] решений двери в журнале пока нет — есть только служебные"
              " события")
        print("       Скажите «Джарвис, какая погода в Москве?» и запустите"
              " снова.")
        problems.append("дверь ещё не принимала решений — проверять нечего")
    elif first_new is None:
        print(f"[СБОЙ] ни в одной из {len(doors)} строк двери нет origin_chain")
        print("       Похоже, запущена версия ДО фазы 1б-1.")
        problems.append("подписи «кто просил» нет ни в одной строке")
    else:
        old, new = doors[:first_new], doors[first_new:]
        holes = [x for x in new if not x.get("origin_chain")]
        if holes:
            print(f"[СБОЙ] среди новых строк {len(holes)} без origin_chain")
            problems.append("новые строки двери без цепочки «кто просил»")
        else:
            print(f"[OK  ] цепочка есть во ВСЕХ {len(new)} строках двери,"
                  " записанных после установки фазы 1б")
        if old:
            print(f"[--  ] {len(old)} строк старше фазы 1б подписи не имеют —"
                  " так и должно быть")
            print("       журнал только дополняется, прошлое не переписывается"
                  "  (правило 5 core/audit_log.py)")

    # 2. Ваши собственные просьбы должны выглядеть как owner -> main.
    mine = [x for x in doors if (x.get("origin_chain") or []) == ["owner", "main"]]
    if mine:
        print(f"[OK  ] ваших личных просьб в журнале: {len(mine)}"
              "   (цепочка owner -> main)")
        print("       последняя:", mine[-1].get("tool"))
    else:
        print("[--  ] личных просьб (owner -> main) пока нет — скажите что-нибудь"
              " голосом, например про погоду")

    # 3. Работа под-агентов: цепочка длиннее двух звеньев.
    agents = [x for x in doors if len(x.get("origin_chain") or []) > 2]
    if agents:
        print(f"[OK  ] работы под-агентов в журнале: {len(agents)}")
        roles = sorted({x.get("agent_role") for x in agents if x.get("agent_role")})
        print("       роли:", ", ".join(roles) or "—")
    else:
        print("[--  ] под-агенты ещё не работали — это нормально, если вы не"
              " давали фоновых задач")

    # 4. След забора в журнале — если под-агенты вообще пытались.
    fenced = [x for x in doors
              if x.get("verdict") == "blocked" and "I12" in (x.get("reason") or "")]
    if fenced:
        print(f"[OK  ] забор фазы 1б-2 срабатывал: {len(fenced)} раз")
        for x in fenced[-3:]:
            print(f"       {x.get('tool')}: {x.get('reason')}")
    else:
        print("[--  ] следов забора в журнале нет — это НЕ ответ на вопрос"
              " «работает ли он»")

    # 4b. А ТЕПЕРЬ СОБСТВЕННО ВОПРОС: ЗАБОР-ТО НА МЕСТЕ?
    #
    # ЭТОТ БЛОК ПОЯВИЛСЯ ПОСЛЕ МОЕЙ ЖЕ ОШИБКИ, 28.08.2026. Первая версия
    # скрипта заканчивалась пунктом 4 выше, и я проверил её мутацией: подменил
    # в журнале строку отказа на «run» — то есть изобразил СЛОМАННЫЙ ЗАБОР.
    # Скрипт напечатал «ИТОГ: журнал в порядке». Он не соврал по букве (следа
    # действительно не было), но ответил не на тот вопрос, который вы задаёте,
    # запуская его. Отсутствие следа и отсутствие забора выглядели одинаково.
    #
    # Это грабли №4 проекта (core/metering.py:199): «проверял полпути, а не
    # результат». Поэтому здесь забор спрашивают НАПРЯМУЮ. Вызов чистый:
    # fences.check — вопрос, а не действие, он ничего не пишет и ничего не
    # запускает, так что правило «этот файл только читает» не нарушено.
    print("\n" + "-" * 70)
    print("САМ ЗАБОР — НА МЕСТЕ? (спрашиваем его прямо, ничего не выполняя)")
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from core import fences
        from core.task_context import TaskCtx

        sub = TaskCtx(run_id="check", bucket="task",
                      origin_chain=("owner", "main")).child(agent_role="probe")
        mem = fences.check("save_memory", {"key": "k"}, ctx=sub)
        vis = fences.check("screen_process", {}, ctx=sub)
        # Владелец: пропуска нет вовсе -> забор не про него.
        own = fences.check("save_memory", {"key": "k"}, ctx=None)
        work = fences.check("web_search", {"query": "q"}, ctx=sub)
    except Exception as err:
        print(f"[СБОЙ] забор не отвечает вовсе: {err}")
        problems.append(f"core/fences.py не работает: {err}")
    else:
        for good, text in (
            (mem.blocked, "под-агенту память ЗАКРЫТА  (I12, Г-3)"),
            (vis.blocked, "под-агенту экран ЗАКРЫТ  (I12, Х-P2)"),
            (not own.blocked, "вам память ОТКРЫТА — вы пишете без вопросов"),
            (not work.blocked, "обычная работа под-агента не задета"),
        ):
            print(f"  [{'OK  ' if good else 'СБОЙ'}] {text}")
            if not good:
                problems.append(text)

    # 5. ЗНАЧЕНИЯ параметров не имеют права лежать в журнале (правило 6).
    #
    # ЭТА ПРОВЕРКА БЫЛА НАПИСАНА НЕВЕРНО, и прогон владельца это показал.
    # Я перечислял РАЗРЕШЁННЫЕ поля и звал «СБОЙ» на всё остальное. На живом
    # журнале это обвинило `tools/rollback_state.py` — законного второго
    # писателя — и напечатало «лишние поля: event, tool_ver, to, restored...».
    #
    # Ошибка не в списке, а в самой мысли. Правило 6 запрещает не «новые
    # поля», а ЗНАЧЕНИЯ ПАРАМЕТРОВ пользователя. Белый список полей ломается
    # при каждом честном расширении формата — а правило 5 расширения прямо
    # разрешает. То есть мой сторож запрещал то, что документация позволяет,
    # и при этом НЕ ЛОВИЛ то, что она запрещает: строка с `params` прошла бы,
    # если бы я забыл вписать её в чёрный список.
    #
    # Теперь проверяется ровно запрет: у решений двери значений нет, есть
    # только `param_keys` — список ИМЁН. Ищем поле, где под ключом лежит не
    # список имён, а словарь со значениями.
    print("\n" + "-" * 70)
    VALUE_FIELDS = ("params", "parameters", "args", "arguments", "kwargs",
                    "param_values", "value", "values", "payload")
    leak = [x for x in doors if any(k in x for k in VALUE_FIELDS)]
    # `param_keys` обязан быть списком строк-имён, а не словарём.
    bad_keys = [x for x in doors
                if x.get("param_keys") is not None
                and not isinstance(x.get("param_keys"), list)]
    if leak or bad_keys:
        who = (leak or bad_keys)[0]
        print("[СБОЙ] в строке двери лежат ЗНАЧЕНИЯ параметров, а не только"
              f" имена: {sorted(set(who) & set(VALUE_FIELDS)) or 'param_keys'}")
        problems.append("в журнал попали значения параметров (правило 6)")
    else:
        print(f"[OK  ] в {len(doors)} строках двери только ИМЕНА параметров,"
              " значений нет   (правило 6 core/audit_log.py)")

    if others:
        # Не «лишние поля», а другой писатель. Журнал один нарочно.
        events = sorted({x.get("event") for x in others if x.get("event")})
        print(f"[--  ] в журнале есть {len(others)} строк не от двери:"
              f" {', '.join(events)}")
        print("       это tools/rollback_state.py — откат состояния пишет"
              " сюда же, и правильно")

    print("\n" + "=" * 70)
    if problems:
        print("ИТОГ: СБОЙ.")
        for p in problems:
            print("   -", p)
        return 1
    print("ИТОГ: журнал в порядке — фаза 1б работает на живом разговоре.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
