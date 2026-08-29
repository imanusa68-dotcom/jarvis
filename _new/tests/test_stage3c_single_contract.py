# -*- coding: utf-8 -*-
"""Stage 3C — один контракт подтверждения.

Почему этот файл вообще существует.

Stage 3A заменил булев `confirmed` на долговечные талоны согласия, но поле
осталось в схеме ВСЕХ инструментов и в описании file_controller. Модель читал
контракт, а не системный промпт, спрашивала пользователя сама, ставила confirmed=true —
и гейт требовал спросить второй раз. Двойное подтверждение родилось не из ошибки
модели, а из двух живых механизмов одновременно.

Инвариант 14: контракт сильнее промпта. Объявить механизм устаревшим — значит
удалить его из контракта, а не написать о нём в инструкции.

Runner-style (pytest-free): module-level test_* + _run().
"""
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import main as jmain


def _props(decl):
    return (decl.get("parameters") or {}).get("properties") or {}


def test_no_tool_advertises_confirmed():
    """Главная страховка шага 1: поле не должно вернуться никогда."""
    offenders = [d.get("name") for d in jmain.TOOL_DECLARATIONS
                 if "confirmed" in _props(d)]
    assert not offenders, (
        "confirmed вернулся в схему инструментов: " + ", ".join(map(str, offenders))
    )


def test_every_tool_still_has_consent_id():
    """Убрав старый механизм, нельзя остаться вообще без механизма."""
    missing = [d.get("name") for d in jmain.TOOL_DECLARATIONS
               if "consent_id" not in _props(d)]
    assert not missing, "инструменты без consent_id: " + ", ".join(map(str, missing))


def test_file_controller_description_does_not_teach_confirmed():
    """Описание инструмента — тоже контракт, и оно ближе к вызову."""
    fc = next(d for d in jmain.TOOL_DECLARATIONS if d.get("name") == "file_controller")
    desc = fc.get("description") or ""
    assert "confirmed=true" not in desc.lower(), \
        "описание file_controller снова учит модель ставить confirmed=true"
    assert "consent_id" in desc, \
        "описание file_controller должно называть единственный живой механизм"


def test_description_does_not_promise_ask_once_per_series():
    """Обещание снято до шага 3.

    При включённых талонах окно серии не открывается никогда (open_delete_burst
    живёт внутри needs_confirmation, куда гейт больше не доходит). Промпт,
    обещающий то, чего система не делает, хуже молчания: модель начинает
    оправдываться перед пользователем за поведение, которого нет.
    """
    fc = next(d for d in jmain.TOOL_DECLARATIONS if d.get("name") == "file_controller")
    desc = (fc.get("description") or "").upper()
    assert "ASK ONCE PER SERIES" not in desc, \
        "обещание серии вернулось в промпт раньше, чем шаг 3 сделал его правдой"


def test_consent_id_tells_the_model_to_omit_it_on_the_first_call():
    """Живой дефект, найденный на первом прогоне шага 1.

    Модель выдумала consent_id в формате UUID и получила отказ гейта.
    Причина была не в глупости модели, а в описании поля: оно говорило,
    что id нужен после согласия пользователя, но НИГДЕ не говорило, что на
    ПЕРВОМ вызове поле надо просто опустить. Модель честно вывела:
    «нужен id → нужно согласие → спрошу сама → сочиню id». Отсюда и вопрос
    заранее, и выдуманный талон, и лишний круг.
    """
    for decl in jmain.TOOL_DECLARATIONS:
        desc = (_props(decl).get("consent_id") or {}).get("description") or ""
        low = desc.lower()
        assert "omit" in low and "first call" in low, (
            f"{decl.get('name')}: описание consent_id не говорит модели опустить "
            "поле на первом вызове — модель снова начнёт сочинять талоны"
        )
        assert "cst_" in low, (
            f"{decl.get('name')}: описание должно показывать форму настоящего id"
        )


def test_legacy_confirmation_text_no_longer_mentions_confirmed():
    """Путь деградации не должен просить несуществующее поле."""
    from core.security import format_confirmation_request
    msg = format_confirmation_request("file_controller", "reason")
    assert "CONFIRMATION_REQUIRED" in msg
    assert "confirmed=true" not in msg.lower(), \
        "легаси-текст снова учит модель полю, которого нет в схеме"


def test_legacy_path_still_understands_confirmed():
    """Симметричная страховка: мы убрали поле из контракта, НЕ сломав код.

    При выключенном флаге и при недоступном хранилище талонов живёт старый
    путь. Он должен остаться рабочим, иначе откат на легаси = система без
    подтверждений вообще.
    """
    from core.security import _is_confirmed
    assert _is_confirmed({"confirmed": True}) is True
    assert _is_confirmed({"confirmed": "true"}) is True
    assert _is_confirmed({}) is False


def _run():
    tests = [
        test_no_tool_advertises_confirmed,
        test_every_tool_still_has_consent_id,
        test_file_controller_description_does_not_teach_confirmed,
        test_description_does_not_promise_ask_once_per_series,
        test_consent_id_tells_the_model_to_omit_it_on_the_first_call,
        test_legacy_confirmation_text_no_longer_mentions_confirmed,
        test_legacy_path_still_understands_confirmed,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
    print(f"[stage3c] {len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
