# -*- coding: utf-8 -*-
"""Сторожа на детерминированную отмену ВТОРОЙ реплики в ходу.

ОТКУДА ЭТО ВЗЯЛОСЬ
Владелец 29.08.2026, дословно: «Я не хочу чтобы это было реже, я хочу чтобы
эта проблема полностью исчезла и больше никогда не появилась и чтобы наше
исправление не привело к новым проблемам».

«Реже» действительно не решение: до этой правки вторую реплику отменяла
только ПРОСЬБА в промпте, а просьбу модель вправе истолковать иначе — тем
более что остальные семь правил промпта («just call it, then report») её как
раз заказывают.

ЧТО ИЗМЕНИЛОСЬ
Найден штатный механизм протокола (зонд 41 по SDK владельца 2.12.1):
  * `behavior=NON_BLOCKING` в объявлении инструмента;
  * `scheduling=SILENT` + `will_continue=False` в ответе.
Документация `will_continue` описывает нашу задачу дословно: «To avoid
triggering the generation and finish the function call, additionally set
`scheduling` to `SILENT`». Работают только ВМЕСТЕ: без объявления сервер
поля молча проигнорирует («Only applicable to NON_BLOCKING function calls»),
без полей объявление ничего не меняет.

ЧЕГО ЭТИ ТЕСТЫ НЕ ДОКАЗЫВАЮТ — ЧЕСТНО
Что сервер послушается. Ключа и микрофона в песочнице нет, живую модель
судит только прогон на машине владельца. Тесты стерегут ДРУГОЕ, и это
ровно вторая половина просьбы владельца («чтобы не привело к новым
проблемам»):
  1. связка не разъедется — оба конца на месте;
  2. правка осталась ТОЧЕЧНОЙ: 22 остальных инструмента не затронуты;
  3. отказ памяти по-прежнему СЛЫШЕН;
  4. путь отступления существует и работает.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MAIN = (ROOT / "main.py").read_text(encoding="utf-8")
BEHAVIOR = (ROOT / "core" / "prompts" / "06_behavior.txt").read_text(
    encoding="utf-8", errors="replace"
)
# Промпт свёрстан по ~80 знаков: проверяемая фраза может быть разорвана
# переносом. Сверяем по тексту со сведёнными пробелами, иначе сторож будет
# краснеть от переформатирования, а не от потери правила.
BEHAVIOR_FLAT = re.sub(r"\s+", " ", BEHAVIOR)


def _declarations():
    """Достаёт TOOL_DECLARATIONS, не импортируя main.py.

    main.py тянет pyaudio и окна, которых в песочнице нет; импорт здесь
    сделал бы тест непроверяемым именно там, где он нужен.
    """
    start = MAIN.index("TOOL_DECLARATIONS = [")
    depth, i = 0, start + len("TOOL_DECLARATIONS = ")
    while True:
        if MAIN[i] == "[":
            depth += 1
        elif MAIN[i] == "]":
            depth -= 1
            if depth == 0:
                break
        i += 1
    ns: dict = {}
    exec(MAIN[start: i + 1], ns)  # noqa: S102 — свой же литерал
    return ns["TOOL_DECLARATIONS"]


def _code_only(text: str) -> str:
    """Тот же текст без комментариев.

    ЗАЧЕМ ЭТО ВООБЩЕ ПОНАДОБИЛОСЬ (найдено полу-мутацией, а не догадкой)
    Сторожа сначала искали слово `SILENT` в тексте ветки — и пропустили
    снятие настоящего поля, потому что рядом лежит длинный комментарий,
    где `SILENT` упоминается раз пять. Сторож, который зеленеет от
    комментария, стережёт объяснение вместо кода: правку удалят, а он
    промолчит. Ровно то, чего владелец просил не допустить.

    Кавычки учитываются, чтобы `#` внутри строки не обрезал строку.
    """
    out = []
    for line in text.splitlines():
        res, q, i = [], None, 0
        while i < len(line):
            ch = line[i]
            if q:
                res.append(ch)
                if ch == "\\":
                    if i + 1 < len(line):
                        res.append(line[i + 1])
                        i += 1
                elif ch == q:
                    q = None
            elif ch in "'\"":
                q = ch
                res.append(ch)
            elif ch == "#":
                break
            else:
                res.append(ch)
            i += 1
        out.append("".join(res))
    return "\n".join(out)


def _save_memory_body():
    """Тело ветки `if name == "save_memory":` внутри _execute_tool."""
    i_save = MAIN.index('if name == "save_memory":')
    i_forget = MAIN.index('if name == "forget_memory":', i_save)
    return MAIN[i_save:i_forget]


def _branch_body(name: str) -> str:
    """Тело ветки `if name == "<name>":` до следующей такой же ветки.

    Окно «1200 знаков наугад» было здесь раньше и плохо в обе стороны:
    короткое — пропустит SILENT в конце ветки, длинное — заглянет в
    соседнюю и покраснеет на чужом коде. Граница берётся из файла.
    """
    i = MAIN.index(f'if name == "{name}":')
    nxt = re.search(r'\n        if name (?:==|in) ', MAIN[i + 10:])
    end = i + 10 + nxt.start() if nxt else len(MAIN)
    return MAIN[i:end]


def _gate_body():
    """Блок двери безопасности — ДО успешной записи."""
    i_gate = MAIN.index(
        'if name in ("save_memory", "forget_memory", "recall_memory")')
    return MAIN[i_gate: MAIN.index('if name == "save_memory":', i_gate)]


# ── 1. Оба конца связки на месте ─────────────────────────────────────────────

def test_save_memory_is_declared_non_blocking():
    """Без этого поля сервер молча проигнорирует SILENT в ответе."""
    decl = next(d for d in _declarations() if d["name"] == "save_memory")
    assert decl.get("behavior") == "NON_BLOCKING", (
        "у save_memory пропало behavior=NON_BLOCKING — SILENT в ответе "
        "перестанет действовать, и двойной ответ вернётся"
    )


def test_the_successful_answer_carries_both_fields():
    """SILENT без will_continue=False по документации может не сработать."""
    body = _code_only(_save_memory_body())
    assert '"scheduling": "SILENT"' in body, "пропал scheduling=SILENT"
    assert '"will_continue": False' in body, "пропал will_continue=False"


def test_the_two_halves_cannot_drift_apart_silently():
    """Одна половина без другой бесполезна — обе обязаны быть в одном файле.

    Самый вероятный способ потерять правку: «почистить» объявление или
    «упростить» ответ, не заметив, что это связка. Тест держит их вместе.
    """
    decl = next(d for d in _declarations() if d["name"] == "save_memory")
    declared = decl.get("behavior") == "NON_BLOCKING"
    answered = "SILENT" in _code_only(_save_memory_body())
    assert declared == answered, (
        f"связка разъехалась: объявление NON_BLOCKING={declared}, "
        f"SILENT в ответе={answered} — работают только вместе"
    )


def test_the_sdk_accepts_our_declarations():
    """Если SDK не примет объявления, Джарвис не поднимется вообще.

    Это самая дорогая из возможных новых проблем: не «двойной ответ», а
    «не запускается». Проверяем сборкой настоящего конфига.
    """
    types = pytest.importorskip(
        "google.genai.types",
        reason="google-genai не установлен — проверять нечего",
    )
    decls = _declarations()
    cfg = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        tools=[{"function_declarations": decls}],
    )
    got = {
        fd.name: fd.behavior
        for fd in cfg.tools[0].function_declarations
        if fd.behavior and "NON_BLOCKING" in str(fd.behavior)
    }
    assert list(got) == ["save_memory"], (
        f"после разбора SDK NON_BLOCKING оказался у {list(got)}"
    )


# ── 2. Правка осталась точечной ─────────────────────────────────────────────

def test_no_other_tool_became_non_blocking():
    """22 остальных инструмента обязаны сохранить прежнее поведение.

    Для них ответ инструмента И ЕСТЬ ответ владельцу: у web_search без
    генерации после результата разговор оборвётся на полуслове.
    """
    others = [d["name"] for d in _declarations()
              if d["name"] != "save_memory" and "behavior" in d]
    assert not others, f"behavior уехал на чужие инструменты: {others}"


def test_recall_memory_still_speaks():
    """recall_memory обязан озвучить найденное — молчание там = «забыл»."""
    body = _code_only(_branch_body("recall_memory"))
    assert "SILENT" not in body, (
        "recall_memory замолчал — Джарвис будет искать в памяти и не "
        "сообщать результат"
    )
    assert '"silent": True' not in body, "recall_memory потерял право говорить"


def test_forget_memory_still_speaks():
    """Владелец сказал «забудь» и обязан услышать, забылось ли.

    Промпт прямо требует сообщить результат («Only say "done, forgotten"
    AFTER forget_memory returns "Forgotten:"»). Немое забывание сделало бы
    это правило невыполнимым.
    """
    body = _code_only(_branch_body("forget_memory"))
    assert "SILENT" not in body, (
        "forget_memory замолчал — владелец не узнает, удалилось ли"
    )


# ── 3. Отказ памяти обязан остаться слышным ─────────────────────────────────

def test_a_refused_write_is_never_silent():
    """САМАЯ ОПАСНАЯ из возможных новых проблем.

    Если поставить SILENT всем ответам save_memory без разбора, отказ
    станет немым: память НЕ записана, а владелец услышит обычный ответ и
    уйдёт уверенным, что факт сохранён. Двойной ответ раздражает, немой
    отказ — обманывает.
    """
    assert "SILENT" not in _code_only(_gate_body()), (
        "отказ двери стал немым — владелец не узнает, что память не тронута"
    )


def test_silent_is_applied_after_the_write_actually_happened():
    """SILENT стоит ПОСЛЕ update_memory, а не вместо него."""
    body = _code_only(_save_memory_body())
    # Порядок проверок важен: без явного сообщения сторож падал ValueError'ом
    # («substring not found»), и читающий отчёт не понимал, что пропало.
    assert "SILENT" in body, "в ветке save_memory вообще нет SILENT"
    assert "update_memory(" in body, "в ветке save_memory нет самой записи"
    assert body.index("update_memory(") < body.index("SILENT"), (
        "SILENT оказался раньше самой записи — молчание о том, чего не было"
    )


# ── 4. Путь отступления ─────────────────────────────────────────────────────

def test_the_switch_exists_and_defaults_to_on():
    """Включён по умолчанию: владелец просил лечения, а не настройки."""
    from core.feature_flags import silent_memory_write_enabled
    assert silent_memory_write_enabled() is True


def test_the_switch_is_read_at_answer_time_not_at_import():
    """Прочитанный один раз на старте флаг нельзя выключить без перезапуска.

    Владелец правит ~/.jarvis/settings.json — значение обязано читаться в
    момент ответа, иначе «путь отступления» существует только на бумаге.
    """
    body = _code_only(_save_memory_body())
    assert "silent_memory_write_enabled()" in body, (
        "флаг не проверяется в момент ответа"
    )
    assert "from core.feature_flags import" in body, (
        "импорт вынесен из ветки — значение может закешироваться"
    )


def test_a_broken_switch_does_not_break_the_write():
    """Сломанный выключатель не имеет права уронить ход.

    Факт к этому моменту уже на диске; исключение здесь убило бы сессию
    ПОСЛЕ успешной записи — худший из возможных исходов.
    """
    body = _code_only(_save_memory_body())
    i = body.index("silent_memory_write_enabled")
    around = body[max(0, i - 300): i + 500]
    assert "try:" in around and "except Exception" in around, (
        "проверка флага не защищена try/except"
    )


# ── 5. Промпт согласован с протоколом ───────────────────────────────────────

def test_the_prompt_tells_the_model_to_speak_before_saving():
    """РИСК, НАЙДЕННЫЙ ДО ПРАВКИ (зонд 42, риск 5).

    SILENT убирает возможность ответить ПОСЛЕ. Если промпт по-прежнему
    разрешает выбор «до или после», модель может промолчать до вызова,
    рассчитывая доложить после, — и владелец не услышит НИЧЕГО. Правка
    протокола без правки промпта создаёт новый дефект вместо старого.
    """
    low = BEHAVIOR_FLAT.lower()
    assert "save_memory is the one tool that ends in silence" in low, (
        "из промпта пропало правило «отвечай ДО вызова save_memory» — "
        "модель может замолчать совсем"
    )
    i = low.index("save_memory is the one tool that ends in silence")
    block = low[i: i + 900]
    assert "before calling it" in block, "не сказано отвечать ДО вызова"
    assert "silence" in block, "не сказано, что после вызова речи не будет"


def test_the_prompt_no_longer_offers_a_choice_for_save_memory():
    """Раньше стояло «speak ONCE — before or after, never both».

    Для save_memory выбора больше нет: «после» физически не доходит.
    Разрешение выбирать = разрешение промолчать.
    """
    low = BEHAVIOR_FLAT.lower()
    i = low.index("one answer per turn")
    block = low[i: i + 700]
    assert not ("save_memory" in block and "before or after" in block), (
        "промпт снова предлагает save_memory выбор «до или после» — "
        "модель вправе выбрать «после», и владелец услышит тишину"
    )


def test_the_receipt_example_survived():
    """«Отмечено, сэр» — расписка, а не ответ. Образец обязан остаться."""
    low = BEHAVIOR_FLAT.lower()
    assert "noted, sir" in low, "пропал образец лишнего доклада"


def test_the_slow_tool_rule_is_untouched():
    """Медленным инструментам речь ДО вызова по-прежнему нужна.

    Иначе владелец получит тишину на 30 секунд поиска — замена одного
    дефекта другим, худшим.
    """
    low = BEHAVIOR_FLAT.lower()
    assert "for a slow tool, speak before" in low, (
        "потеряно правило про медленные инструменты"
    )


def test_the_old_claim_that_nothing_can_be_done_is_corrected():
    """В шапке echo_guard стояло «SILENT нам не подходит». Это была ошибка.

    Оставить её — значит через месяц снова прочитать «лечить нельзя» и не
    лечить. Неверное объяснение в комментарии живёт дольше кода.
    """
    doc = (ROOT / "core" / "echo_guard.py").read_text(encoding="utf-8")
    flat = re.sub(r"\s+", " ", doc)
    assert "ИСПРАВЛЕНИЕ МОЕЙ ЖЕ ОШИБКИ" in flat, (
        "в шапке echo_guard.py снова утверждается, что лечения нет"
    )
    # Важно не наличие слов, а сохранность ВЫВОДА: поле объявления — наше,
    # значит условие для SILENT выполнимо. Без этой фразы шапка снова
    # читается как «механизм не для нас».
    assert "объявляем МЫ" in flat, (
        "из шапки исчезло объяснение, что behavior — поле НАШЕГО объявления; "
        "без него правка через месяц выглядит случайной"
    )
    # И отдельно — что оговорка про тред Google не выдаётся за приговор всему
    # дефекту. Она касается только дословного дубля (случай 1).
    assert "касается ДРУГОГО случая" in flat, (
        "оговорка про тред Google снова звучит как приговор обоим случаям"
    )
    # Про старую формулировку нельзя проверять «её тут нет»: она тут ЕСТЬ и
    # должна быть — как цитата, иначе непонятно, что именно исправлено.
    # (Эта проверка сначала была написана как «not in» и честно упала на
    # собственной цитате. Правильный сторож — наличие опровержения РЯДОМ.)
    if "SILENT` нам не подходит" in flat:
        assert "Посылка верна, вывод — НЕТ" in flat, (
            "старое утверждение про SILENT осталось в шапке БЕЗ опровержения "
            "— читается как приговор, хотя оно уже опровергнуто зондом 41"
        )
