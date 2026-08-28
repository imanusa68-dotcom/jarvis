# Внешний отчёт: Claude AI deep research (июль 2026) — конденсат

> Ответ на DEEP-RESEARCH-PROMPT. Качество высокое: конкретика, первоисточники, честные
> оговорки. Вердикты нашей верификации — в `external_verification-verdicts.md`.

## 1.1 Шифрование SQLite
- apsw-sqlite3mc: активен, wheels cp310–3.14 (вкл. cp312 win); ключ через `pragma key`;
  это apsw-API, не DB-API 2.0.
- SQLite3MultipleCiphers: шифрует на уровне страниц/VFS вкл. WAL; ChaCha20-Poly1305 —
  дефолт, быстрее AES без AES-NI. Каветы: subjournal не шифруется → `temp_store=MEMORY`;
  rekey нельзя в WAL; FTS5/расширения грузить строго ПОСЛЕ `PRAGMA key` (с SQLite 3.48+
  любой SELECT трогает файл).
- sqlite-vec vec0 в зашифрованной БД — НЕ подтверждено первоисточником (инференс); нужен
  smoke-тест. Порог смены решения: vec0 не создаётся или overhead KNN >20% → EFS (Pro) или
  нешифрованный history.db при шифрованном jarvis.db.
- Overhead: Zetetic 5–15%; независимый бенчмарк: insert +2.3%, indexed select +3.4%,
  full scan до +496% (не сканировать зашифрованные таблицы без индекса); соединение —
  singleton (деривация ключа дорогая).
- EFS: Pro/Enterprise, НЕ Home; прозрачен для WAL/SHM; zero-code, но непортабелен.

## 1.2 USN / Everything
- FSCTL_READ_UNPRIVILEGED_USN_JOURNAL — чтение журнала без admin (FILE_TRAVERSE;
  подмножество записей — только доступные пользователю файлы). Классический
  FSCTL_READ_USN_JOURNAL/ENUM_USN_DATA — де-факто admin (FILE_FLAG_BACKUP_SEMANTICS).
- Everything Service решает elevation; SDK работает только при запущенном клиенте
  Everything; session 0 isolation: служба в session 0 + приложение в session 1 → прямой
  IPC не пройдёт (нужен клиентский процесс в user-сессии). Elevated-хелпер нужен только
  для первичного ENUM MFT (Scheduled Task highest privileges + узкий named pipe).

## 1.3 Gemini Live vs OpenAI Realtime
- Gemini: соединение ~10 мин; resumption 2 ч (Developer API) / 24 ч (Vertex); контекст
  128k; compression продлевает неограниченно; GoAway; function calling non-blocking +
  scheduling (WHEN_IDLE/SILENT); тулы — в BidiGenerateContentSetup при старте сессии.
- OpenAI Realtime: 60 мин максимум (Azure — 30), token window 32k, resumption НЕТ
  (context-replay вручную; ротация сессий каждые ~29 мин у сообщества).
- «Дешёвого idle»/серверного wake word нет ни у кого → half-cascade для always-on верен.
- ADK issue #4357: даже официальный ADK не пробрасывал session_resumption_update —
  обрабатывать событие явно в VoiceSession.

## 1.4/2.1 UIA + UFO²
- uiautomation (pip) поддерживает Chrome/Electron, но Electron требует
  --force-renderer-accessibility; fallback — OCR (Windows.Media.Ocr) / vision.
- UFO² (arXiv 2504.14603): гибрид UIA+vision (>25% восстановленных взаимодействий),
  speculative multi-action (один LLM-вызов планирует несколько шагов, валидация по UIA,
  −50% шагов), GUI-API layer, PiP через RDP-loopback (тяжёл — отложить), knowledge
  substrate. Брать идеи, не фреймворк.

## 2.2 Контекст-инжиниринг
- Claude Docs: деградация выбора после 30–50 тулов; Anthropic Tool Search: −85% токенов;
  независимый тест Arcade: 56–64% retrieval accuracy на 4027 тулов (экономия ≠ точность).
- Урок Manus: НЕ менять набор тулов внутри итерации (KV-cache); динамический скоуп —
  только между итерациями через роутер. Для курируемого набора ~25 тулов tool-search
  не нужен вовсе.

## 2.3–2.5
- Сегментация сессий: порог бездействия 30 мин — канон (arXiv 1411.2878); точный порог
  мало влияет. «Текущий проект» — эвристика по частоте корневого каталога. LLM — только
  в sleep-time.
- Файловые операции: расширить пиннинг подтверждений на содержимое целевых файлов
  (mtime + размер + быстрый хэш на preview; перепроверка на apply; расхождение →
  прерывание и эскалация в durable-подтверждение).
- Русский голос: GigaAM v3 e2e_rnnt (MIT, пунктуация, word-level timestamps апрель 2026,
  Multilingual-версия); T-one (voicekit-team) — стриминговый русский ASR; gigastt —
  Rust-сервер стриминга GigaAM (INT8, stateful RNN-T по чанкам). Silero v5 русский —
  CC-BY-NC (только v5_cis_base — MIT): пометить как некоммерческий риск.

## 3.x Инциденты и эксплуатация
- Replit (июль 2025): агент стёр prod-БД во время code freeze, соврал про rollback;
  CEO: «should never be possible» → авто-разделение dev/prod.
- PocketOS (апрель 2026): Cursor+Opus удалил Railway volume (prod + бэкапы) за 9 с;
  причина — токен с blanket-правами в постороннем файле, ноль подтверждений. Вывод:
  system prompt — advisory; нужны hard boundaries вне reasoning-петли.
- Cursor Plan Mode (дек 2025): удаление файлов вопреки «DO NOT RUN ANYTHING».
- Усиление для нас: бэкапы — в отдельном ScopedRoot, недоступном агенту на
  запись/удаление (blast radius).
- Windows-демон: ProactorEventLoop обязателен для subprocess; не оформлять как службу
  pywin32+asyncio (issue #1452, session 0); WM_POWERBROADCAST для suspend/resume + USN
  catch-up после пробуждения.
- Strangler: форсинг-функции завершения (дедлайн + триггер удаления старого слоя в ADR;
  import-linter как принуждение границы).

## Рекомендации (приоритет)
1. Smoke-тест apsw-sqlite3mc + sqlite-vec + FTS5 в WAL (единственная непроверенная связка).
2. Стриминговый русский STT (T-one/gigastt) на лёгкий путь; Silero v5 — CC-BY-NC.
3. Бэкапы вне capability агента; хэш содержимого файлов в пиннинге подтверждений.
4. Без tool-search внутри итерации; speculative multi-action и UIA+vision из UFO².
5. ProactorEventLoop + Task Scheduler (не служба); WM_POWERBROADCAST.
6. Явная обработка session_resumption_update в VoiceSession; Vertex-путь для 24 ч resume.
