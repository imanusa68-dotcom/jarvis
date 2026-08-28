# tests/test_perception_live_defects.py
# Три дефекта, пойманные в живом диалоге на сборке step7.
#
#   20. На переднем плане служебное окошко приложения («Возобновить чтение»)
#       → ответ был «открытого документа не вижу», хотя документ был рядом.
#   21. Один и тот же new_file.json — то «документ», то «файл с кодом».
#   22. «Своё окно я за ответ не считаю…» в каждом ответе подряд.
#
# Всё подаётся фикстурами: тесты не читают настоящий экран.

from core.awareness import _perception as pc
from core.awareness import _inspectors as ins

DOC_PATH = r"C:\Users\rdrr\Desktop\Отчет.docx"
JSON_PATH = r"C:\Users\rdrr\Desktop\new_file.json"


def _w(title, process="unknown.exe", hwnd=1000, **extra):
    window = {"title": title, "process": process, "hwnd": hwnd,
              "owner": 0, "toolwindow": False}
    window.update(extra)
    return window


class Sources:
    def __init__(self, **overrides):
        self.overrides = overrides

    def __enter__(self):
        pc.reset()
        defaults = {
            "active_window": lambda: None,
            "list_windows": lambda: [],
            "active_document": lambda deadline_s=None: ins.new_result(),
            "recent_candidates": lambda name, process="": [],
            "path_exists": lambda path: False,
            "is_own_window": lambda title, process="", hwnd=0: False,
            "snapshot": lambda: {},
        }
        defaults.update(self.overrides)
        pc.set_sources(**defaults)
        return pc

    def __exit__(self, *exc):
        pc.reset()
        return False


# ── Дефект 20 ───────────────────────────────────────────────────

def test_a_service_popup_does_not_hide_the_document_behind_it():
    bubble = _w("Возобновить чтение", process="WINWORD.EXE", hwnd=11)
    real = _w("Отчет.docx - Word", process="WINWORD.EXE", hwnd=12)
    with Sources(active_window=lambda: bubble,
                 list_windows=lambda: [bubble, real],
                 path_exists=lambda path: path == DOC_PATH,
                 recent_candidates=lambda name, process="": [DOC_PATH]):
        subject = pc.describe("foreground")
        spoken = pc.render_subject(subject)
    assert (subject["artifact"] or {}).get("name") == "Отчет.docx"
    assert subject["behind"] == "Возобновить чтение"
    assert "Отчет.docx" in spoken
    # и честно говорит, что спереди было другое окно
    assert "Возобновить чтение" in spoken


def test_the_neighbour_must_belong_to_the_same_program():
    bubble = _w("Возобновить чтение", process="WINWORD.EXE", hwnd=11)
    stranger = _w("Смета.xlsx - Excel", process="EXCEL.EXE", hwnd=13)
    with Sources(active_window=lambda: bubble,
                 list_windows=lambda: [bubble, stranger]):
        subject = pc.describe("foreground")
    # чужое приложение не подставляется никогда
    assert subject["behind"] is None
    assert "Смета" not in (subject["window"]["title"] or "")


def test_a_normal_window_never_triggers_the_neighbour_search():
    calls = {"n": 0}

    def listing():
        calls["n"] += 1
        return []

    notepad = _w("test.txt - Блокнот", process="notepad.exe", hwnd=20)
    with Sources(active_window=lambda: notepad, list_windows=listing):
        subject = pc.describe("foreground")
    assert (subject["artifact"] or {}).get("name") == "test.txt"
    assert calls["n"] == 0        # лишних обходов окон нет


# ── Дефект 21 ───────────────────────────────────────────────────

def test_one_file_has_one_name_in_the_whole_assistant():
    assert pc.noun_for(JSON_PATH) == "файл с кодом"
    assert pc.noun_for("Отчет.docx") == "документ"
    assert pc.noun_for("a.7z") == "архив"
    assert pc.noun_for("безрасширения") == "файл"
    assert pc.noun_for(None) == "файл"


def test_both_renderers_use_the_same_word_for_the_same_file():
    cascade = ins.render({"path": JSON_PATH, "confidence": ins.CONF_PROBABLE})
    assert pc.noun_for(JSON_PATH) in cascade
    assert "документ" not in cascade          # больше не два словаря
    assert JSON_PATH in cascade

    docx = ins.render({"path": DOC_PATH, "confidence": ins.CONF_EXACT})
    assert "документ" in docx


def test_a_name_without_a_path_is_also_named_correctly():
    text = ins.render({"path": None, "name": "new_file.json",
                       "reason": "", "candidates": []})
    assert "файл с кодом" in text
    assert "new_file.json" in text


# ── Дефект 22 ───────────────────────────────────────────────────

def _substituted(title, process="notepad.exe", hwnd=31):
    window = _w(title, process=process, hwnd=hwnd)
    window["substituted"] = True
    return window


def test_the_substitution_is_explained_once_not_every_turn():
    window = _substituted("test.txt - Блокнот")
    with Sources(active_window=lambda: window):
        first = pc.render_subject(pc.describe("foreground"))
        second = pc.render_subject(pc.describe("foreground"))
        third = pc.render_subject(pc.describe("foreground"))
    assert "Своё окно" in first
    assert "Своё окно" not in second
    assert "Своё окно" not in third
    # сам ответ при этом не пропадает
    assert "test.txt" in second and "test.txt" in third


def test_a_new_substituted_window_is_explained_again():
    first_window = _substituted("test.txt - Блокнот", hwnd=31)
    second_window = _substituted("Отчет.docx - Word", process="WINWORD.EXE", hwnd=32)
    with Sources(active_window=lambda: first_window):
        pc.render_subject(pc.describe("foreground"))
        pc.set_sources(active_window=lambda: second_window)
        text = pc.render_subject(pc.describe("foreground"))
    assert "Своё окно" in text


def test_a_fresh_session_explains_the_substitution_again():
    window = _substituted("test.txt - Блокнот")
    with Sources(active_window=lambda: window):
        pc.render_subject(pc.describe("foreground"))
    with Sources(active_window=lambda: window):
        text = pc.render_subject(pc.describe("foreground"))
    assert "Своё окно" in text
