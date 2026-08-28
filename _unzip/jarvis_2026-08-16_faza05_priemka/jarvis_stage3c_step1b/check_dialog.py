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

    print(f"\nвсего строк: {len(lines)}")
    print("\nПОСЛЕДНИЕ 5 РЕШЕНИЙ:")
    for line in lines[-5:]:
        print(human(line))

    problems = []

    # 1. Цепочка обязана быть в КАЖДОЙ строке — это ворота фазы 1б-1.
    print("\n" + "-" * 70)
    no_chain = [x for x in lines if not x.get("origin_chain")]
    if no_chain:
        print(f"[СБОЙ] строк без origin_chain: {len(no_chain)} из {len(lines)}")
        problems.append("есть строки без цепочки «кто просил»")
    else:
        print(f"[OK  ] цепочка «кто просил» есть во ВСЕХ {len(lines)} строках")

    # 2. Ваши собственные просьбы должны выглядеть как owner -> main.
    mine = [x for x in lines if (x.get("origin_chain") or []) == ["owner", "main"]]
    if mine:
        print(f"[OK  ] ваших личных просьб в журнале: {len(mine)}"
              "   (цепочка owner -> main)")
        print("       последняя:", mine[-1].get("tool"))
    else:
        print("[--  ] личных просьб (owner -> main) пока нет — скажите что-нибудь"
              " голосом, например про погоду")

    # 3. Работа под-агентов: цепочка длиннее двух звеньев.
    agents = [x for x in lines if len(x.get("origin_chain") or []) > 2]
    if agents:
        print(f"[OK  ] работы под-агентов в журнале: {len(agents)}")
        roles = sorted({x.get("agent_role") for x in agents if x.get("agent_role")})
        print("       роли:", ", ".join(roles) or "—")
    else:
        print("[--  ] под-агенты ещё не работали — это нормально, если вы не"
              " давали фоновых задач")

    # 4. След забора в журнале — если под-агенты вообще пытались.
    fenced = [x for x in lines
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

    # 5. Значения параметров не имеют права лежать в журнале.
    print("\n" + "-" * 70)
    leak = [x for x in lines if any(
        k not in ("schema_ver", "ts", "ts_utc", "tool", "action", "mode",
                  "verdict", "risk", "policy", "reason", "param_keys",
                  "task_id", "agent_role", "origin_chain", "depth")
        for k in x)]
    if leak:
        print(f"[СБОЙ] в журнале есть лишние поля — проверьте: {list(leak[0])}")
        problems.append("в строке журнала появились незапланированные поля")
    else:
        print("[OK  ] в строках только имена параметров, значений нет"
              "   (правило 6 core/audit_log.py)")

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
