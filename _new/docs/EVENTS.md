# Event catalog (generated)

Generated from `core/bus.py` (`EVENTS`). Do not edit by hand -- run:

```bash
python -m core.bus > docs/EVENTS.md
```

Rules: events are **facts in the past tense**, never commands (all actions go through the gate). The catalog is **frozen**: add new events or optional fields, never repurpose existing ones.

**11 events**

| Event | Required | Optional | Meaning |
| --- | --- | --- | --- |
| `file.copied` | `src`, `dst` | `saga_id` | A file was copied; the copy is removable by undo. |
| `file.created` | `path` | `saga_id` | A new file was created on disk. |
| `file.deleted` | `path` | `saga_id`, `recoverable`, `method` | A file was deleted after its bytes were stashed, so undo restores it. |
| `file.moved` | `src`, `dst` | `saga_id` | A file was moved to another location. |
| `file.op_failed` | `op`, `error` | `path` | A file operation failed and was rolled back / never applied. |
| `file.overwritten` | `path` | `saga_id` | An existing file's contents were atomically replaced (reversible). |
| `file.renamed` | `src`, `dst` | `new_name`, `saga_id` | A file was renamed in place. |
| `folder.created` | `path` | `saga_id` | A folder was created; undo removes it while it is still empty. |
| `redo.performed` | `ok` | `label`, `path`, `kind`, `saga_id` | One previously undone step was re-applied. |
| `session.started` | `session_id` | -- | A fresh Jarvis run began; unscoped undo is limited to this session. |
| `undo.performed` | `ok` | `label`, `path`, `kind`, `saga_id`, `deleted` | One step was undone; 'deleted' marks undoing an original creation. |

