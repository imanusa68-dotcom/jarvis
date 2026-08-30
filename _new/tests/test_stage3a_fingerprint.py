# tests/test_stage3a_fingerprint.py
"""
Stage 3A, step 1 - the operation fingerprint.

This file exists because the fingerprint has TWO opposite failure modes and
both are silent:

  MUST-MATCH pairs   guard against re-ask storms. Every pair here is a
                     spelling difference that a human would call "the same
                     thing". If one of these ever stops matching, Jarvis starts
                     asking the same question twice and the user learns to say
                     "yes" without listening - which destroys the whole point
                     of confirmations.
  MUST-DIFFER pairs  guard against the real security bug. Every pair here looks
                     similar but is a DIFFERENT operation. If one of these ever
                     collides, a "yes" for the harmless one authorises the
                     dangerous one.

When the fingerprint rules change, this file is the contract that has to be
updated on purpose - never "fixed" by loosening an assertion.
"""
import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import consent  # noqa: E402


def fp(tool, action, params):
    return consent.payload_fingerprint(tool, action, params)


# -- MUST MATCH ---------------------------------------------------------------

def test_same_call_is_stable():
    a = fp("file_controller", "delete", {"path": r"C:\Users\rdrr\Downloads\a.txt"})
    b = fp("file_controller", "delete", {"path": r"C:\Users\rdrr\Downloads\a.txt"})
    assert a == b


def test_path_case_is_ignored():
    # Windows does not distinguish these; neither may we, or we re-ask forever.
    a = fp("file_controller", "delete", {"path": r"C:\Users\Rdrr\Downloads"})
    b = fp("file_controller", "delete", {"path": r"c:\users\rdrr\downloads"})
    assert a == b


def test_separator_style_is_ignored():
    a = fp("file_controller", "delete", {"path": r"C:\Users\rdrr\Downloads"})
    b = fp("file_controller", "delete", {"path": "C:/Users/rdrr/Downloads"})
    c = fp("file_controller", "delete", {"path": r"C:\Users\\rdrr\\\Downloads"})
    assert a == b == c


def test_trailing_separator_is_ignored():
    a = fp("file_controller", "delete", {"path": r"C:\Users\rdrr\Downloads"})
    b = fp("file_controller", "delete", {"path": "C:/Users/rdrr/Downloads/"})
    assert a == b


def test_dot_segments_are_collapsed():
    a = fp("file_controller", "delete", {"path": r"C:\Users\rdrr\Downloads"})
    b = fp("file_controller", "delete", {"path": r"C:\Users\rdrr\.\Music\..\Downloads"})
    assert a == b


def test_quoted_path_matches_bare_path():
    # Voice transcripts and the model both like to quote paths.
    a = fp("file_controller", "delete", {"path": r"C:\Users\rdrr\a.txt"})
    b = fp("file_controller", "delete", {"path": '"C:\\Users\\rdrr\\a.txt"'})
    assert a == b


def test_unicode_normal_form_is_ignored():
    nfc = unicodedata.normalize("NFC", "C:/\u041f\u0430\u043f\u043a\u0430/\u0439\u043e\u0433\u0430.txt")
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd  # genuinely different bytes
    assert fp("file_controller", "delete", {"path": nfc}) == \
           fp("file_controller", "delete", {"path": nfd})


def test_env_var_and_tilde_expand(monkeypatch=None):
    home = os.path.expanduser("~")
    os.environ["JARVIS_TEST_HOME"] = home
    try:
        a = fp("file_controller", "delete", {"path": os.path.join(home, "a.txt")})
        b = fp("file_controller", "delete", {"path": "%JARVIS_TEST_HOME%/a.txt"})
        c = fp("file_controller", "delete", {"path": "~/a.txt"})
        assert a == b == c
    finally:
        os.environ.pop("JARVIS_TEST_HOME", None)


def test_confirmed_flag_is_not_part_of_the_operation():
    # The whole point: the payload the user heard has no `confirmed` in it, and
    # the re-call does. They must still be the same operation.
    a = fp("file_controller", "delete", {"path": "C:/tmp/a.txt"})
    b = fp("file_controller", "delete", {"path": "C:/tmp/a.txt", "confirmed": True})
    c = fp("file_controller", "delete", {"path": "C:/tmp/a.txt", "consent_id": "abc"})
    assert a == b == c


def test_key_order_is_ignored():
    a = fp("file_controller", "move", {"source": "C:/a.txt", "destination": "C:/b.txt"})
    b = fp("file_controller", "move", {"destination": "C:/b.txt", "source": "C:/a.txt"})
    assert a == b


def test_stringified_booleans_and_int_floats():
    a = fp("file_controller", "delete", {"path": "C:/a", "recursive": True, "depth": 2})
    b = fp("file_controller", "delete", {"path": "C:/a", "recursive": "true", "depth": 2.0})
    assert a == b


def test_absent_equals_empty():
    a = fp("file_controller", "delete", {"path": "C:/a"})
    b = fp("file_controller", "delete", {"path": "C:/a", "note": "", "other": None})
    assert a == b


def test_path_list_order_is_ignored():
    a = fp("file_controller", "delete", {"paths": ["C:/a.txt", "C:/b.txt"]})
    b = fp("file_controller", "delete", {"paths": ["C:/b.txt", "C:/a.txt"]})
    assert a == b


def test_tool_and_action_case_is_ignored():
    a = fp("file_controller", "delete", {"path": "C:/a"})
    b = fp("File_Controller", "DELETE", {"path": "C:/a"})
    assert a == b


# -- MUST DIFFER --------------------------------------------------------------

def test_different_file_differs():
    assert fp("file_controller", "delete", {"path": "C:/a.txt"}) != \
           fp("file_controller", "delete", {"path": "C:/b.txt"})


def test_parent_folder_is_not_the_child():
    # The classic escalation: "yes" for one file must never cover its folder.
    assert fp("file_controller", "delete", {"path": r"C:\Users\rdrr\Downloads"}) != \
           fp("file_controller", "delete", {"path": r"C:\Users\rdrr\Downloads\a.txt"})


def test_different_action_differs():
    assert fp("file_controller", "delete", {"path": "C:/a"}) != \
           fp("file_controller", "move", {"path": "C:/a"})


def test_different_tool_differs():
    assert fp("file_controller", "delete", {"path": "C:/a"}) != \
           fp("cmd_control", "delete", {"path": "C:/a"})


def test_swapped_source_and_destination_differs():
    # Same two paths, opposite meaning. A set-like fingerprint would collide.
    assert fp("file_controller", "move", {"source": "C:/a", "destination": "C:/b"}) != \
           fp("file_controller", "move", {"source": "C:/b", "destination": "C:/a"})


def test_extra_file_in_the_list_differs():
    # 3 files vs 4 files is the "340 files" incident in miniature.
    assert fp("file_controller", "delete", {"paths": ["C:/a", "C:/b"]}) != \
           fp("file_controller", "delete", {"paths": ["C:/a", "C:/b", "C:/c"]})


def test_recursive_flag_differs():
    assert fp("file_controller", "delete", {"path": "C:/a", "recursive": False}) != \
           fp("file_controller", "delete", {"path": "C:/a", "recursive": True})


def test_false_is_not_the_same_as_absent():
    # An explicit "no, not recursive" is a real instruction and must be bound.
    assert fp("file_controller", "delete", {"path": "C:/a"}) != \
           fp("file_controller", "delete", {"path": "C:/a", "recursive": False})


def test_non_path_text_keeps_its_case():
    # Only PATH_KEYS get folded. A command's case can change its meaning, and
    # folding unrelated text would merge two different operations.
    assert fp("cmd_control", None, {"task": "Delete Logs"}) != \
           fp("cmd_control", None, {"task": "delete logs"})


def test_non_path_list_order_matters():
    assert fp("agent_task", None, {"steps": ["backup", "delete"]}) != \
           fp("agent_task", None, {"steps": ["delete", "backup"]})


# -- Diagnostics --------------------------------------------------------------

def test_explain_mismatch_names_the_field():
    diffs = consent.explain_mismatch(
        "file_controller", "delete", {"path": "C:/a.txt"},
        "file_controller", "delete", {"path": "C:/b.txt"},
    )
    assert len(diffs) == 1
    assert "path" in diffs[0]
    assert "a.txt" in diffs[0] and "b.txt" in diffs[0]


def test_explain_mismatch_is_empty_for_equal_calls():
    assert consent.explain_mismatch(
        "file_controller", "delete", {"path": r"C:\A.TXT"},
        "file_controller", "delete", {"path": "c:/a.txt", "confirmed": True},
    ) == []


def test_explain_mismatch_reports_missing_key():
    diffs = consent.explain_mismatch(
        "file_controller", "delete", {"path": "C:/a"},
        "file_controller", "delete", {"path": "C:/a", "recursive": True},
    )
    assert any("recursive" in d and "missing" in d for d in diffs)


def test_fingerprint_is_a_sha256_hex():
    f = fp("file_controller", "delete", {"path": "C:/a"})
    assert len(f) == 64 and all(c in "0123456789abcdef" for c in f)


def test_canonical_payload_is_readable_for_audit():
    text = consent.canonical_payload("file_controller", "delete", {"path": "C:/\u041f\u0430\u043f\u043a\u0430"})
    assert "\u041f\u0430\u043f\u043a\u0430".casefold() in text  # not \uXXXX escaped
    assert '"tool":"file_controller"' in text


def test_missing_params_do_not_crash():
    assert fp("file_controller", None, None) == fp("file_controller", "", {})


def test_unhashable_value_does_not_crash():
    # Fingerprinting must never be the reason a tool call fails.
    f = fp("file_controller", "delete", {"paths": [{"path": "C:/a"}, {"path": "C:/b"}]})
    assert len(f) == 64
