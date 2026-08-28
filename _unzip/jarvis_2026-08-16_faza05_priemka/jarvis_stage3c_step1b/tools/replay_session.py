# tools/replay_session.py
"""
Воспроизведение записи чёрного ящика (фаза 1, блок 6, шаг 12).

ЧТО ЭТО ДЕЛАЕТ ПРОСТЫМИ СЛОВАМИ
-------------------------------
Отсматривает запись, как видеорегистратор. Даёт два действия:

    python tools\\replay_session.py              -- список последних записей
    python tools\\replay_session.py B-...        -- показать одну запись

и главное — даёт МЕХАНИЗМ, которым любая жалоба «он сделал не то»
превращается в постоянный тест: поставщика из записи (`RecordedProvider`)
можно поставить вместо настоящего, и код пойдёт по своему обычному пути, но
ответы модели получит из записи. Ноль обращений к облаку, ноль расхода квоты.

ПОЧЕМУ ПОДМЕНА ИМЕННО ЗДЕСЬ, А НЕ ВНУТРИ ДВЕРИ
----------------------------------------------
Договор поставщика — ровно два метода, и шов подмены в проекте УЖЕ ЕСТЬ
(`core.provider.set_provider`, им пользуются сторожа слоя поставщика). Поэтому
воспроизведению не нужна ни одна правка в двери к модели: мы не переписываем
путь, мы подставляем другой источник ответов на том же пути. Это же значит,
что воспроизводится настоящий код, а не его пересказ.

Собирает и отправляет запрос ОДИН И ТОТ ЖЕ экземпляр поставщика — так велит
договор. Поэтому форму тела запроса поставщик выбирает себе сам, и наш
возвращает сам промпт: ни SDK, ни сети здесь нет вовсе.

ПОЧЕМУ ИСКАТЬ НАДО ПО ОТПЕЧАТКУ, А НЕ ПО ПОРЯДКУ
------------------------------------------------
Порядок вызовов при повторном прогоне может сбиться: часть шагов не
выполнится, часть выполнится иначе. Отпечаток полного промпта не зависит ни
от порядка, ни от обрезки текста в записи — поэтому ключ именно он.

ТРИ ПРАВИЛА, БЕЗ КОТОРЫХ ВОСПРОИЗВЕДЕНИЕ ВРЕДНЕЕ СВОЕГО ОТСУТСТВИЯ
------------------------------------------------------------------
1. ПРОМАХ — ГРОМКИЙ ОТКАЗ, А НЕ ПОХОД В СЕТЬ. Если на промахе уйти к модели,
   воспроизведение начнёт тратить квоту и перестанет быть бесплатным, а
   владелец узнает об этом по исчерпанному лимиту.
2. ОБРЕЗАННЫЙ ОТВЕТ НЕ ОТДАЁТСЯ НИКОГДА. Отдать обрубок за настоящий ответ —
   значит соврать: код пойдёт по ветке, по которой в тот раз не шёл.
   Отказываемся вслух.
3. НЕ ТРОГАТЬ НАСТОЯЩИЙ ДОМ. Причина замерена, а не придумана: дверь к модели
   ВСЕГДА берёт талон у учёта расхода, поэтому наивное воспроизведение
   записывало бы в учёт вызовы, КОТОРЫХ НЕ БЫЛО (замер 19.08.2026: два
   прогона -> «истрачено 2 из 120»). Прокрутите запись пятьдесят раз, и
   Джарвис скажет «осталось 70» при полном запасе — то есть блок 5 начнёт
   врать ровно там, где он ценен. Поэтому этот инструмент, как и доктор,
   СМОТРИТ, НО НЕ ТРОГАЕТ: читает настоящую базу только на чтение, а любую
   запись увозит в отдельную папку.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _say(line: str) -> None:
    """Печать, которая никогда не роняет вызов (грабли про cp1251)."""
    try:
        print(line)
    except UnicodeEncodeError:
        try:
            print(line.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass
    except Exception:
        pass


class ReplayMiss(LookupError):
    """В записи нет ответа на этот промпт. Наверх летит громко — см. правило 1."""


class RecordedProvider:
    """Поставщик, который отвечает ИЗ ЗАПИСИ. Договор — те же два метода.

    Ставится через `core.provider.set_provider(...)`; после работы прежнего
    поставщика обязательно вернуть — иначе следующий тест или следующий вызов
    в этом процессе тоже пойдёт в запись.
    """

    name = "recorded"

    def __init__(self, rows):
        # Пары «отпечаток промпта -> ответ». Промпт и ответ идут в теле
        # соседними строками, поэтому собираем их парами по порядку.
        self.answers: dict = {}
        self.misses: list = []
        self.served = 0
        pending = None
        for row in rows or []:
            kind, body = row.get("kind"), row.get("body") or {}
            if kind == "prompt":
                pending = body.get("h")
            elif kind == "model_out":
                if pending:
                    self.answers[pending] = body
                pending = None

    def build_payload(self, prompt, image_parts=None):
        """Тело запроса — сам промпт. Картинки нам не нужны: в записи их нет
        и никогда не было (в тело пишется текст, а не байты кадра)."""
        return prompt

    def generate(self, model, payload, api_key) -> str:
        text = payload if isinstance(payload, str) else str(payload)
        key = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
        got = self.answers.get(key)
        if got is None:
            self.misses.append(key)
            raise ReplayMiss(
                "в записи нет ответа на этот промпт "
                "(запись старее кода, или промпт собрался иначе)")
        if got.get("hidden"):
            raise ReplayMiss(
                "ответ есть, но его текст не записан: не удалось вычистить секреты")
        if got.get("cut"):
            # Правило 2: обрубок за настоящий ответ не выдаём.
            raise ReplayMiss(
                "записанный ответ обрезан по потолку — воспроизводить нельзя")
        if not got.get("ok", False):
            raise RuntimeError("записанный отказ модели: " + str(got.get("e")))
        return str(got.get("t") or "")


def sandbox_home() -> Path:
    """Отдельная папка под любую запись. См. правило 3 в шапке.

    Настоящий дом остаётся неприкосновенным: всё, что код захочет записать
    (учёт расхода, журналы, снимки), уедет сюда и умрёт вместе с папкой.
    """
    home = Path(tempfile.mkdtemp(prefix="jv_replay_"))
    os.environ["JARVIS_STATE_DIR"] = str(home)
    return home


def real_db_path() -> Path:
    """Где лежит настоящая база. Считается ДО подмены дома."""
    from core.safe_json import state_dir
    return state_dir() / "jarvis.db"


def open_readonly(path):
    """Открыть базу ТОЛЬКО НА ЧТЕНИЕ, ничего не создавая.

    Сначала режим «только чтение» по адресу; если путь в адрес не лезет
    (пробелы, кириллица, решётка в имени папки — у владельца такое бывало),
    падаем на обычное открытие: файл уже существует, создавать нечего.
    Образец взят у core/state_version.
    """
    from urllib.parse import quote
    target = Path(path)
    if not target.exists():
        return None
    uri = "file:" + quote(target.as_posix(), safe="/:") + "?mode=ro"
    for opener in (lambda: sqlite3.connect(uri, uri=True),
                   lambda: sqlite3.connect(str(target))):
        try:
            conn = opener()
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error:
            continue
    return None


def load_rows(conn, rec_id) -> list:
    """Тело записи по порядку. Разбор — свой, чтобы не открывать базу дважды."""
    out = []
    try:
        rows = conn.execute(
            "SELECT seq, kind, payload, ts_utc FROM mx_bb_body "
            "WHERE rec_id=? ORDER BY seq", (str(rec_id),)).fetchall()
    except sqlite3.Error:
        return out
    for row in rows:
        try:
            body = json.loads(row["payload"])
        except Exception:
            body = {}
        out.append({"seq": int(row["seq"]), "kind": str(row["kind"]),
                    "body": body, "ts_utc": str(row["ts_utc"])})
    return out


def provider_for(rec_id, *, db=None) -> RecordedProvider:
    """Готовый поставщик из записи — то, что ставят в тесте одной строкой."""
    conn = open_readonly(db or real_db_path())
    if conn is None:
        return RecordedProvider([])
    try:
        return RecordedProvider(load_rows(conn, rec_id))
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


# -- Показ владельцу ------------------------------------------------------

def _short(text, limit: int = 160) -> str:
    one = " ".join(str(text).split())
    return one if len(one) <= limit else one[:limit] + "..."


def _live_calls(conn, rec_id) -> int:
    """Сколько ответов модели уже легло в ЕЩЁ ОТКРЫТУЮ запись.

    Зачем считать на месте, а не читать из шапки: число в шапке появляется
    только при ЗАКРЫТИИ записи (тело живёт считанные дни, шапка вечно, поэтому
    число материализуется до того, как тело умрёт). У открытой записи там
    честный ноль — но показать владельцу «вызовов 0», когда их уже три, значит
    соврать. Считаем по телу и НЕ храним второе число: две копии одного числа
    рано или поздно расходятся.
    """
    try:
        row = conn.execute(
            "SELECT count(*) FROM mx_bb_body WHERE rec_id=? AND kind='model_out'",
            (str(rec_id),)).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


def show_list(conn) -> int:
    try:
        rows = conn.execute(
            "SELECT rec_id, quota_day, calls_n, outcome, body_purged, "
            "created_utc, closed_utc FROM mx_bb_head "
            "ORDER BY created_utc DESC LIMIT 20").fetchall()
    except sqlite3.Error as exc:
        _say("Не смог прочитать записи: " + str(exc))
        return 1
    if not rows:
        _say("Записей пока нет. Они появятся после первого обращения к модели.")
        return 0
    _say("Последние записи чёрного ящика:")
    _say("")
    for row in rows:
        if row["closed_utc"] is None:
            state = "идёт сейчас"
            calls = f"{_live_calls(conn, row['rec_id'])} (пока идёт)"
        else:
            state = str(row["outcome"])
            calls = str(row["calls_n"])
        body = "тело убрано" if row["body_purged"] else "тело на месте"
        _say(f"  {row['rec_id']}")
        _say(f"      создана {row['created_utc']} - сутки {row['quota_day']}")
        _say(f"      вызовов к модели: {calls} - {state} - {body}")
    _say("")
    _say("Показать одну: python tools\\replay_session.py <номер записи>")
    return 0


def show_one(conn, rec_id) -> int:
    rows = load_rows(conn, rec_id)
    if not rows:
        _say(f"У записи {rec_id} тела нет: либо номер неверен, либо тело убрано "
             f"по возрасту (цифры при этом остались в шапке).")
        return 1
    _say(f"Запись {rec_id}: строк в теле {len(rows)}")
    _say("")
    for row in rows:
        body = row["body"]
        if row["kind"] == "prompt":
            mark = " [обрезан]" if body.get("cut") else ""
            if body.get("hidden"):
                mark = " [текст не записан: не удалось вычистить секреты]"
            _say(f"  {row['seq']:>3}. вопрос к модели ({body.get('n', '?')} знаков){mark}")
            _say(f"       {_short(body.get('t', ''))}")
        elif row["kind"] == "model_out":
            if not body.get("ok", False):
                _say(f"  {row['seq']:>3}. ОТКАЗ модели: {body.get('e')}")
            else:
                mark = " [обрезан, воспроизвести нельзя]" if body.get("cut") else ""
                if body.get("hidden"):
                    mark = " [текст не записан]"
                _say(f"  {row['seq']:>3}. ответ модели{mark}")
                _say(f"       {_short(body.get('t', ''))}")
        else:
            _say(f"  {row['seq']:>3}. {row['kind']}")
    provider = RecordedProvider(rows)
    _say("")
    _say(f"Готовых к воспроизведению пар «вопрос-ответ»: {len(provider.answers)}")
    _say("Воспроизведение идёт БЕЗ обращения к облаку и не тратит квоту.")
    return 0


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    db = real_db_path()
    conn = open_readonly(db)
    if conn is None:
        _say(f"Базы нет или она не читается: {db}")
        return 1
    try:
        if not args:
            return show_list(conn)
        return show_one(conn, args[0])
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
