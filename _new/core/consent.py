# core/consent.py
"""
Stage 3A - consent: what the user ACTUALLY agreed to.

Today a confirmation is a boolean the MODEL puts into its own call
(`confirmed=true`). Nothing binds that boolean to a specific operation, so the
agreement we show out loud and the operation we execute can differ. Every
reviewed incident class in the research (TOCTOU on bot-delegated approvals)
comes from exactly that gap: an approval must be an atomic bind between the
intent the human saw and the payload that runs.

This module is step 1 of Stage 3A and does ONE thing: turn a tool call into a
stable fingerprint. Nothing imports it yet; nothing changes behaviour yet.

Why a fingerprint is the hard part
----------------------------------
The ticket table, the TTL and the single-use consume are ordinary SQL. The
fingerprint is where this feature dies in production, in BOTH directions:

  too strict -> `C:\\Users` vs `c:/users/` are the same folder on Windows but
                different bytes, so Jarvis re-asks forever. Confirmation
                fatigue is a SECURITY defect, not a UX annoyance: a user who
                says "yes" reflexively has stopped reviewing anything.
  too loose  -> two genuinely different operations collide and a "yes" for one
                silently authorises the other. That is the bug we are here to
                kill.

So the rules below are deliberate and each one is pinned by a test.

What is normalised (and why)
----------------------------
  volatile keys   dropped   `confirmed` / `consent_id` are ABOUT the consent,
                            not part of the operation. If they were hashed the
                            fingerprint could never match itself.
  unicode         NFC       Windows and Python hand us the same Cyrillic path
                            in different normal forms; NFD "й" is two code
                            points and would not equal NFC "й".
  paths           folded    separators unified, `.`/`..` collapsed lexically,
                            trailing separator dropped, case folded (Windows
                            is case-insensitive), env vars and `~` expanded.
  path lists      sorted    deleting [a, b] is the same operation as [b, a].
                            Non-path lists KEEP their order - for those the
                            order can be the meaning.
  "true"/"false"  -> bool   Gemini stringifies booleans at random.
  1.0             -> 1      JSON round-trips turn ints into floats.
  "" and None     dropped   "absent" and "empty" mean the same thing here.
  False           KEPT      an explicit False is a real instruction.

What is deliberately NOT done here
----------------------------------
  - No filesystem access. This function must stay pure and instant: it runs on
    the hot path twice per confirmed action, and it must give the same answer
    when the file is already gone (we still have to recognise the ticket for a
    delete whose target vanished).
  - Because of that, 8.3 short names (`C:\\PROGRA~1`) and symlinks are NOT
    resolved; that needs the disk. Resolution happens once, when the ticket is
    MINTED, so the stored payload is already the real path.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from typing import Any


# -- Keys ---------------------------------------------------------------------

# About the consent, never part of the operation being consented to.
VOLATILE_KEYS: frozenset[str] = frozenset({
    "confirmed",
    "consent_id",
    "consent_token",
    "request_id",
    "timestamp",
    "session_id",
})

# Values under these keys are filesystem paths and get path folding.
# Anything not listed is treated as opaque text and only trimmed/NFC'd, so a
# new tool can never get silently case-folded (which would merge two distinct
# operations into one fingerprint).
PATH_KEYS: frozenset[str] = frozenset({
    "path", "paths",
    "file", "files", "filename", "file_path", "file_paths",
    "folder", "folders", "directory", "dir",
    "source", "sources", "src",
    "destination", "destinations", "dst", "dest",
    "target", "targets",
    "old_path", "new_path",
    "location",
})

_SEP_RUN = re.compile(r"[\\/]+")
_DRIVE = re.compile(r"^[A-Za-z]:$")
_ENV_VAR = re.compile(r"%([^%]+)%")


class ConsentError(RuntimeError):
    """Consent could not be established (always fail CLOSED, never open)."""


# -- Path folding -------------------------------------------------------------

def _expand_env(s: str) -> str:
    """Expand %VAR% ourselves instead of trusting os.path.expandvars.

    os.path.expandvars is platform-dependent: on Windows it understands %VAR%,
    on Linux it only understands $VAR. The fingerprint must not change meaning
    with the platform, or the sandbox tests would be quietly testing different
    rules than the ones that run on the user's machine - a test suite that
    lies is worse than no test suite. Windows env names are case-insensitive,
    so the lookup is too. An unknown variable is left as-is: guessing is how a
    consent for one folder starts matching another.
    """
    if "%" not in s:
        return s
    lookup = {k.casefold(): v for k, v in os.environ.items()}

    def sub(m: re.Match) -> str:
        val = lookup.get(m.group(1).casefold())
        return m.group(0) if val is None else val

    return _ENV_VAR.sub(sub, s)


def _collapse(parts: list[str]) -> list[str]:
    """Resolve `.` and `..` lexically, without touching the disk."""
    out: list[str] = []
    for p in parts:
        if p == "." or p == "":
            continue
        if p == "..":
            if out and out[-1] != "..":
                out.pop()
            else:
                out.append(p)
            continue
        out.append(p)
    return out


def normalize_path(value: str) -> str:
    """Fold a Windows-ish path into one canonical spelling.

    Pure and offline on purpose - see the module docstring. The goal is not a
    "correct" path, it is a STABLE one: the same target must always produce the
    same string, and two different targets must never produce the same string.
    """
    s = unicodedata.normalize("NFC", value).strip()
    # Voice transcription and the model both like to wrap paths in quotes.
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        s = s[1:-1].strip()
    if not s:
        return ""

    s = _expand_env(s)
    if s.startswith("~"):
        s = os.path.expanduser(s)

    is_unc = s[:2] in ("\\\\", "//")
    is_abs_root = (not is_unc) and s[:1] in ("\\", "/")

    raw = [p for p in _SEP_RUN.split(s) if p != ""]

    prefix = ""
    if is_unc:
        # \\server\share is the root and must survive `..` collapsing.
        head, raw = raw[:2], raw[2:]
        prefix = "\\\\" + "\\".join(head)
    elif raw and _DRIVE.match(raw[0]):
        prefix, raw = raw[0], raw[1:]
    elif is_abs_root:
        prefix = ""

    body = "\\".join(_collapse(raw))

    if prefix and _DRIVE.match(prefix):
        out = prefix + "\\" + body if body else prefix + "\\"
    elif prefix:
        out = prefix + ("\\" + body if body else "")
    elif is_abs_root:
        out = "\\" + body
    else:
        out = body

    # Windows compares paths case-insensitively; so must we, or "Downloads"
    # and "downloads" become two different consents for one folder.
    return out.casefold()


# -- Value folding ------------------------------------------------------------

def _norm_scalar(key: str, value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if float(value).is_integer() else value
    if value is None:
        return None
    if isinstance(value, str):
        s = unicodedata.normalize("NFC", value).strip()
        low = s.casefold()
        if low in ("true", "false"):
            return low == "true"
        if key in PATH_KEYS:
            return normalize_path(s)
        return s
    # Unknown type: stringify rather than crash. Fingerprinting must never be
    # the reason a tool call fails.
    return unicodedata.normalize("NFC", str(value)).strip()


def _is_empty(v: Any) -> bool:
    # False and 0 are REAL values and must survive.
    return v is None or v == "" or v == [] or v == {}


def _norm_value(key: str, value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            k2 = str(k).strip()
            if k2 in VOLATILE_KEYS:
                continue
            nv = _norm_value(k2, v)
            if _is_empty(nv):
                continue
            out[k2] = nv
        return out
    if isinstance(value, (list, tuple)):
        items = [_norm_value(key, v) for v in value]
        items = [v for v in items if not _is_empty(v)]
        if key in PATH_KEYS:
            # [a, b] and [b, a] are the same set of files.
            try:
                items = sorted(items, key=lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False))
            except TypeError:
                pass
        return items
    return _norm_scalar(key, value)


# -- Public API ---------------------------------------------------------------

def canonical_params(parameters: dict | None) -> dict:
    """The operation's parameters, reduced to one spelling."""
    if not parameters:
        return {}
    result = _norm_value("", dict(parameters))
    return result if isinstance(result, dict) else {}


def canonical_payload(tool: str, action: str | None, parameters: dict | None) -> str:
    """Deterministic text form of "what is about to happen".

    sort_keys makes dict order irrelevant; ensure_ascii=False keeps Cyrillic
    readable so the audit log stays human-inspectable.
    """
    doc = {
        "tool": unicodedata.normalize("NFC", str(tool or "")).strip().casefold(),
        "action": unicodedata.normalize("NFC", str(action or "")).strip().casefold(),
        "params": canonical_params(parameters),
    }
    return json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def payload_fingerprint(tool: str, action: str | None, parameters: dict | None) -> str:
    """SHA-256 of the canonical payload. This is what a consent ticket binds to."""
    return hashlib.sha256(
        canonical_payload(tool, action, parameters).encode("utf-8")
    ).hexdigest()


def explain_mismatch(
    tool_a: str, action_a: str | None, params_a: dict | None,
    tool_b: str, action_b: str | None, params_b: dict | None,
) -> list[str]:
    """Say WHICH field broke the match.

    Without this, a fingerprint mismatch is an unexplainable "Jarvis keeps
    asking again" bug report. With it, the audit line names the field, and a
    re-ask storm becomes a five-minute fix instead of a rollback.
    """
    diffs: list[str] = []
    ta = str(tool_a or "").strip().casefold()
    tb = str(tool_b or "").strip().casefold()
    if ta != tb:
        diffs.append(f"tool: {ta!r} != {tb!r}")
    aa = str(action_a or "").strip().casefold()
    ab = str(action_b or "").strip().casefold()
    if aa != ab:
        diffs.append(f"action: {aa!r} != {ab!r}")

    pa = canonical_params(params_a)
    pb = canonical_params(params_b)
    for key in sorted(set(pa) | set(pb)):
        if key not in pa:
            diffs.append(f"{key}: <missing> != {pb[key]!r}")
        elif key not in pb:
            diffs.append(f"{key}: {pa[key]!r} != <missing>")
        elif pa[key] != pb[key]:
            diffs.append(f"{key}: {pa[key]!r} != {pb[key]!r}")
    return diffs


# -- The question the user hears ----------------------------------------------

_VERB = {
    "delete": "Delete",
    "move": "Move",
    "rename": "Rename",
    "overwrite": "Overwrite",
    "write": "Write to",
    "append": "Append to",
    "copy": "Copy",
}


def _ends_with(path_value: str, name: str) -> bool:
    """True when `path` already names this item, so we must not append it twice."""
    parts = [p for p in re.split(r"[\\/]+", str(path_value).strip()) if p]
    return bool(parts) and parts[-1].casefold() == str(name).strip().casefold()


def _short(path_value: str) -> str:
    """Last two segments - enough to recognise, short enough to say out loud."""
    parts = [p for p in re.split(r"[\\/]+", str(path_value).strip()) if p]
    return "\\".join(parts[-2:]) if parts else str(path_value)


def describe(tool: str, action: str | None, parameters: dict | None) -> str:
    """Build the sentence that will be read to the user AND stored on the ticket.

    WE generate this, never the model, and the model is required to quote it
    verbatim. That rule exists because of a failure we have already seen in this
    project: asked to summarise, a model invented detail around a two-word value
    and the user acted on the invention. The same habit applied to a
    confirmation turns "340 files" into "a few files" - and the user would then
    be agreeing to something that was never described.

    Note the counts are stated plainly. Volume IS the danger signal: one file is
    a mistake you shrug off, 340 is an incident.
    """
    params = dict(parameters or {})
    verb = _VERB.get((action or "").strip().casefold(), (action or "do").capitalize())

    targets: list[str] = []
    for key in ("path", "paths", "file", "files", "target", "targets", "source", "folder"):
        val = params.get(key)
        if isinstance(val, str) and val.strip():
            targets.append(val.strip())
        elif isinstance(val, (list, tuple)):
            targets.extend(str(v).strip() for v in val if str(v).strip())
        if targets:
            break

    # `path` is often only the CONTAINER ("desktop") while `name` carries the
    # actual item. Describing only the container produced "Delete desktop?" for
    # a call that deleted one file - a sentence that is both frightening and
    # false. The thing being destroyed MUST appear in the sentence read aloud,
    # so join them. A preview that names the wrong object is worse than no
    # preview: it buys a truthful-looking "yes" for an untrue description.
    name = params.get("name") or params.get("filename") or params.get("file_name")
    if isinstance(name, str) and name.strip():
        name = name.strip()
        if not targets:
            targets = [name]
        elif len(targets) == 1 and not _ends_with(targets[0], name):
            targets = [targets[0].rstrip("\\/") + "\\" + name]

    if not targets:
        detail = str(params.get("task") or params.get("query") or "").strip()
        base = f"{verb} via {tool}" + (f": {detail}" if detail else "")
    elif len(targets) == 1:
        base = f"{verb} {_short(targets[0])}"
    else:
        # Say the number FIRST: it is the part that must not be missed.
        base = f"{verb} {len(targets)} items, including {_short(targets[0])}"

    dest = params.get("destination") or params.get("dest") or params.get("new_path")
    if isinstance(dest, str) and dest.strip():
        base += f" to {_short(dest)}"
    if params.get("recursive") in (True, "true", "True"):
        base += ", including everything inside it"
    return base + "?"
