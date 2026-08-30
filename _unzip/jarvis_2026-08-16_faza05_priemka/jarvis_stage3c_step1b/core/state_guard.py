"""Громкий отказ при старте: данные новее программы — не рабочее состояние.

Шаг 33.2 (Р6). Шаг 33.1 научил Джарвиса ЗНАТЬ версии всех хранилищ. Этот
модуль учит его НЕ МОЛЧАТЬ, когда версии не сходятся.

Почему отдельный модуль, а не десять строк в main.py:
  Решение «не начинать работу» должно приниматься в одном месте. Иначе через
  пять шагов таких проверок станет три: одна в main.py, одна в ui.py, одна
  в первом же агенте — и каждая со своей формулировкой и своим порогом.

Правила, которые здесь нельзя переигрывать молча:

1. Модуль ничего не открывает и не мигрирует. Он читает только то, что собрал
   core.state_version.collect() (PRAGMA user_version на только-чтение).
   Диагностика, которая сама открывает базу рабочим путём (с миграциями),
   умирает ровно в том случае, для которого написана.

   Внимание: два теста шага 33 читают этот файл как текст и запрещают в нём
   несколько слов целиком — даже в комментариях. Греп тупой нарочно: так
   он никогда не пропустит реальный вызов.
2. Модуль НИКОГДА не бросает исключений наружу. Сторож, который падает сам,
   запирает дом от хозяина. Если проверка не выполнилась — запуск продолжается,
   а причина печатается.
3. Отсутствие хранилища — не претензия. В доме владельца сейчас нет ни
   history.db, ни settings.json, ни personality.json, и это законно.
4. Запуск из другой папки — замечание, а не запрет: владелец каждый вечер
   распаковывает новый архив.
5. Ни одного слова про «ошибку 6 != 999». Фразы человеческие, с числами и с
   ответом на вопрос «что мне теперь делать».
6. Модуль сам не пишет файл состояния. Записью занимается state_version — там
   единственный писатель, и сторож это правило не ломает.

Голоса на старте может ещё не быть: живая модель поднимается позже, а окно
создаётся после этой проверки. Поэтому отказ идёт в консоль, а вызывающий
вправе продублировать те же строки на экран.
"""

from __future__ import annotations

from pathlib import Path

# Единственный порог решения. Данные новее кода — стоп; всё остальное — текст.
REFUSE_ON_FUTURE = True

HEADER = "Джарвис не начал работу: состояние в доме новее программы."
WHAT_TO_DO = (
    "Что делать: вернуть ту папку сборки, из которой Джарвис работал в прошлый "
    "раз, — её код понимает эти данные."
)
NOTHING_LOST = (
    "Ничего не удалено и не изменено: программа просто не начала работу."
)
ROLLBACK_SCRIPT = ("tools", "rollback_state.py")


def _sv():
    """Ленивый импорт: сторож зовётся до окна, тянуть лишнее сюда нельзя."""
    from core import state_version
    return state_version


def rollback_hint() -> str | None:
    """Подсказка про откат данных — только если инструмент уже существует.

    Обещать команду, которой нет в этой сборке, — врать в самый неудобный
    момент. Инструмент появится в шаге 33.5; до тех пор строки просто нет.
    """
    try:
        sv = _sv()
        script = sv.project_dir().joinpath(*ROLLBACK_SCRIPT)
        if script.exists():
            return ("Либо откатить данные на снимок нужной версии: "
                    "python tools/rollback_state.py --to <версия>")
    except Exception:
        return None
    return None


def notes() -> list:
    """Замечания, которые НЕ блокируют старт."""
    out = []
    try:
        changed = _sv().path_changed()
        if changed:
            out.append(changed)
    except Exception:
        pass
    return out


def refusal_lines(problems: list, extra: list) -> list:
    """Текст отказа: заголовок, по строке на претензию, что делать."""
    lines = ["", HEADER, ""]
    for item in problems:
        lines.append(f"  * {item}")
    lines.append("")
    lines.append(WHAT_TO_DO)
    hint = rollback_hint()
    if hint:
        lines.append(hint)
    lines.append(NOTHING_LOST)
    for item in extra:
        lines.append(item)
    lines.append("")
    return lines


def check(state: dict | None = None) -> dict:
    """Решение одно из двух: можно работать или нельзя, и почему.

    Возвращает словарь: ok, problems, notes, lines. Ничего не печатает —
    печать отделена, чтобы то же самое можно было показать на экране.
    """
    sv = _sv()
    data = state if state is not None else sv.collect()
    problems = list(sv.problems(data))
    extra = notes()
    ok = not (REFUSE_ON_FUTURE and problems)
    if ok:
        lines = [f"[JARVIS] {item}" for item in extra]
    else:
        lines = refusal_lines(problems, extra)
    return {"ok": ok, "problems": problems, "notes": extra, "lines": lines}


def _say(printer, line: str) -> None:
    try:
        printer(line)
    except UnicodeEncodeError:
        # Экзотическая кодовая страница консоли: сказать хоть как-то важнее,
        # чем сказать красиво.
        try:
            printer(line.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass
    except Exception:
        pass


def verify_or_refuse(*, printer=None, state: dict | None = None) -> bool:
    """True — можно работать. False — владельцу уже сказано, почему нельзя.

    Никогда не бросает. Если сама проверка сломалась, ответ True: сломанный
    сторож не имеет права не пускать владельца к его же программе.
    """
    say = printer if printer is not None else print
    try:
        result = check(state=state)
    except Exception as exc:
        _say(say, f"[JARVIS] проверка версий не выполнена: {exc}")
        return True
    for line in result["lines"]:
        _say(say, line)
    return bool(result["ok"])


def record_start() -> Path | None:
    """Отметить запуск в файле состояния. Зовётся только после успешной проверки.

    Пишет не этот модуль, а state_version.write() — единственный писатель.
    Без этой отметки last_run пустеет, и «запущено из другой папки» не
    сработает никогда.
    """
    try:
        sv = _sv()
        sv.write()
        return sv.path()
    except Exception as exc:
        try:
            print(f"[JARVIS] отметка о запуске не записана: {exc}")
        except Exception:
            pass
        return None
