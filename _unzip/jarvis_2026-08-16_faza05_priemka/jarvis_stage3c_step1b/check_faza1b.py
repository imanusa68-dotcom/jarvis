# -*- coding: utf-8 -*-
"""
Самопроверка фазы 1б на ВАШЕЙ машине. Запустить и прочитать вывод.

    python check_faza1b.py

Ничего не меняет, никуда не отправляет, в настоящий журнал не пишет: берёт
временный дом под себя и убирает его за собой. Ни одного вызова модели —
значит ни копейки квоты.

Что проверяется (то же, что и сторожа, но глазами владельца):
    1б-1  цепочка «кто просил» доезжает до строки журнала;
    1б-2  под-агент не пишет в память и не смотрит на экран, а вы — пишете.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

HOME = tempfile.mkdtemp(prefix="jarvis-check-")
os.environ["JARVIS_STATE_DIR"] = HOME
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OK, BAD = "OK  ", "СБОЙ"
failures = []


def show(good: bool, text: str, detail: str = "") -> None:
    global failures
    print(f"  [{OK if good else BAD}] {text}")
    if detail:
        print(f"         {detail}")
    if not good:
        failures.append(text)


def main() -> int:
    from core import gate, audit_log, task_context
    audit_log.reset()

    task = task_context.TaskCtx(run_id="check", task_id="T-check",
                                bucket="task", origin_chain=("owner", "main"))
    sub = task.child(agent_role="pc_operator")

    def ask(tool, params, ctx, mode):
        """Спросить настоящую дверь, проглотив её вывод в консоль."""
        with contextlib.redirect_stdout(io.StringIO()):
            with task_context.bind(ctx):
                return gate.dispatch(tool, params, mode=mode)

    print("=" * 70)
    print("ПРОВЕРКА ФАЗЫ 1б  (дом под проверку: временный, будет удалён)")
    print("=" * 70)

    # ── 1б-1: цепочка в журнале ──────────────────────────────────────────
    print("\n1б-1  ЦЕПОЧКА «КТО ПРОСИЛ» В ЖУРНАЛЕ")
    ask("web_search", {"query": "погода"}, sub, "autonomous")
    path = os.path.join(HOME, "logs", "gate-audit.jsonl")
    line = json.loads(open(path, encoding="utf-8").read().strip().splitlines()[-1])

    show("origin_chain" in line,
         "в строке журнала есть origin_chain  (ворота плана, строка 1925)",
         f"origin_chain = {line.get('origin_chain')}")
    show(line.get("origin_chain") == ["owner", "main", "pc_operator"],
         "цепочка полная: владелец -> главный -> роль",
         f"agent_role = {line.get('agent_role')!r}, depth = {line.get('depth')}")
    show(line.get("schema_ver") == 1,
         "версия формата НЕ сдвинулась — старые читатели живы")

    # Цепочка обязана доезжать БЕЗ явной передачи пропуска: живые вызовы
    # двери его не передают, и без этого поля были бы пусты всегда.
    show(line.get("agent_role") == "pc_operator",
         "цепочка доехала без явной передачи ctx (главная проверка 1б-1)")

    # ── 1б-2: заборы ─────────────────────────────────────────────────────
    print("\n1б-2  ЗАБОРЫ: ЧЕГО ПОД-АГЕНТ НЕ ДЕЛАЕТ НИКОГДА")
    r_mem = ask("save_memory", {"content": "секрет владельца"}, sub, "autonomous")
    show(not r_mem.allowed,
         "под-агент НЕ пишет в память  (I12, Г-3)",
         f"вердикт: {r_mem.verdict}")

    r_eye = ask("screen_process", {"text": "что на экране"}, sub, "autonomous")
    show(not r_eye.allowed,
         "под-агент НЕ смотрит на экран  (I12, Х-P2)",
         f"вердикт: {r_eye.verdict}")

    r_work = ask("web_search", {"query": "погода"}, sub, "autonomous")
    show(r_work.allowed,
         "обычная работа под-агента НЕ задета  (забор не мешает делу)")

    print("\n       ...А ВЫ — ПИШЕТЕ, И БЕЗ ЛИШНИХ ВОПРОСОВ")
    r_own = ask("save_memory", {"content": "не пью кофе после шести"}, None,
                "interactive")
    show(r_own.allowed and r_own.verdict == "run",
         "владелец пишет в память СРАЗУ, без подтверждения",
         f"вердикт: {r_own.verdict}")

    r_task = ask("save_memory", {"content": "перешёл на чай"}, task,
                 "autonomous")
    show(r_task.allowed,
         "задача, поставленная вами, тоже пишет  (забор смотрит на роль, "
         "не на режим)")

    # ── Отказ должен быть понятным и не течь ─────────────────────────────
    print("\n       КАЧЕСТВО ОТКАЗА")
    show(bool(r_mem.message) and "память" in r_mem.message.lower(),
         "отказ объяснён человеческими словами")
    print(f'         Джарвис скажет: "{r_mem.message}"')

    raw = open(path, encoding="utf-8").read()
    show("секрет владельца" not in raw,
         "текст записи НЕ утёк в журнал — только ключи параметров")

    blocked = [json.loads(x) for x in raw.strip().splitlines()
               if json.loads(x)["verdict"] == "blocked"]
    show(bool(blocked) and "I12" in blocked[0].get("reason", ""),
         "в журнале видно, ЧТО именно запретило",
         f'причина: {blocked[0].get("reason") if blocked else "—"}')

    # ── Fail-closed ──────────────────────────────────────────────────────
    print("\n       САМОЕ ВАЖНОЕ: СЛОМАННЫЙ ЗАБОР ОТКАЗЫВАЕТ, А НЕ ПРОПУСКАЕТ")
    from core import fences
    saved = fences.check
    fences.check = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("сломан"))
    try:
        r_broken = ask("web_search", {"query": "x"}, None, "interactive")
    finally:
        fences.check = saved
    show(not r_broken.allowed,
         "забор сломался -> действие НЕ выполнено (fail-closed)",
         f"вердикт: {r_broken.verdict}")

    print("\n" + "=" * 70)
    if failures:
        print(f"ИТОГ: СБОЙ. Не сошлось пунктов: {len(failures)}")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("ИТОГ: всё сошлось. Фаза 1б (пункты 1-3) на месте.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(HOME, ignore_errors=True)
    sys.exit(code)
