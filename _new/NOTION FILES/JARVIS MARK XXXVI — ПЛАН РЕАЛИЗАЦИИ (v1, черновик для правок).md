<aside>

⚠️



\*\*Статус:\*\* черновик v1. Источник истины по решениям — документ «JARVIS MARK XXXVI — Мультиагентная архитектура: живой дизайн-документ». Здесь — только план реализации. При расхождении «документ ↔ код» прав код; все расхождения собраны в Разделе 0.



</aside>



<aside>

📌



\*\*Как читать.\*\* Разделы 0–3 — фундамент (что есть на самом деле, куда идём, какие данные и контракты). Раздел 4 — фазы по порядку, это рабочий календарь. Разделы 5–8 — проверки (трассируемость, бюджеты, безопасность, тесты). Разделы 9–11 — что делать руками, где застрянешь, чего план не покрывает.



</aside>



\## Раздел 0. Сверка с реальностью



Проверено по распакованному архиву `jarvis\_stage3c\_step1b`. Правило: \*\*прав код\*\*.



\### 0.1. Расхождения «документ ↔ код»



| № | В документе/промпте | Фактически в коде | Что это меняет в плане |

| --- | --- | --- | --- |

| Р-1 | \~22.7k строк Python | рабочего кода ≈29–30k (22 903 основные + awareness 4 754 + `ui.py` 52 КБ + `tools/`); всех `.py` 40 383 | объём работ и объём чтения больше на треть; в оценках фаз считаем по 30k |

| Р-2 | шина `core/bus.py` — 11 событий | в файле `EVENTS` объявлен на строке 52, число не пересчитано \*\*\[ТРЕБУЕТ ПРОВЕРКИ В КОДЕ]\*\*: `python -c "import core.bus as b; print(len(b.EVENTS))"` | если событий уже много, новые события агентов добавляем в тот же реестр, а не создаём второй |

| \*\*Р-3\*\* | «I11 origin\_chain в гейте — уже есть» | \*\*`origin\_chain` в коде отсутствует полностью.\*\* Сигнатура: `dispatch(tool, params, \*, mode="interactive", screen\_control=False)`. Аудит пишет `ts, tool, action, mode, verdict, risk, policy, reason, param\_keys` — без `task\_id`, без агента, без цепочки | \*\*самое важное расхождение.\*\* I11 — это новая работа фазы 1б, а не «уже сделано». От неё зависят метеринг, чёрный ящик, атрибуция |

| Р-4 | Д36 (schema\_version, миграции) — предстоит сделать | сделано на \~80%: `PRAGMA user\_version`, таблица `applied\_migrations`, `migrate()` с явным BEGIN/COMMIT, \*\*fail-closed уже есть\*\* (`store.py:421` «Database user\_version=… is newer than this code»), `backup()` (`store.py:495`) | фаза 1 не строит миграции с нуля, а \*\*дописывает миграции 7–18\*\* в существующий механизм |

| Р-5 | «таблицы задач/отчётов» | фактически в `\~/.jarvis/jarvis.db`: `config\_kv`, `applied\_migrations`, `action\_journal`, `saga`, `undo\_stack`, `redo\_stack`, `execution\_log`, `memory\_fact`, `consent\_ticket`, `observations`  • FTS | новые таблицы получают префикс `mx\_`, чтобы никогда не столкнуться с существующими именами |

| Р-6 | напоминания в SQLite | `actions/reminder.py` хранит напоминания \*\*в JSON\*\* (`\_load\_reminders` / `\_save\_reminders`) | Д4 = не «написать таблицу», а \*\*миграция данных\*\* из JSON в `mx\_reminder` с сохранением старого файла |

| Р-7 | «очередь задач есть» | `agent/task\_queue.py` — очередь \*\*целиком в памяти\*\*, `max\_concurrent=1`, ни одного `CREATE TABLE` | I15 (персистентная очередь) — с нуля |

| \*\*Р-8\*\* | флаг `JARVIS\_AWARENESS` как образец | в `core/feature\_flags.py` (72 строки) есть только `fileops\_enabled` и `durable\_consent\_enabled`; флага `JARVIS\_AWARENESS` там нет | образец берём по смыслу, а не копированием кода |

| \*\*Р-9\*\* | флаги в `config/settings.json` | этот файл \*\*внутри папки проекта\*\*, и в коде уже зафиксирован урок: «каждая новая распаковка молча возвращала старый путь — механизм безопасности, зависящий от того, вспомнит ли пользователь команду в правильной папке, не является механизмом безопасности» | \*\*`JARVIS\_AGENTS` обязан жить в `\~/.jarvis/settings.json`\*\*, вне папки проекта. Иначе после каждой распаковки zip флаг тихо сбрасывается |

| Р-10 | два SDK одновременно | подтверждено в `pyproject.toml`; старый SDK живёт в `agent/executor.py` (`model.generate\_content` для перевода) | убираем точечно в фазе 0.5, а не «мигрируем весь проект» |

| \*\*Р-11\*\* | ключ утёк в архивы | ключ лежит \*\*в двух файлах\*\*: `config/api\_keys.json` и `config/secrets.json`; плюс `ARCHITECTURE.md:114` прямо пишет «Live Gemini key committed in plaintext… \*\*Rotate.\*\*» | ротация в фазе 0 должна вычистить \*\*оба\*\* файла, иначе «почистил» будет ложью |

| Р-12 | 52 тестовых файла, 784 passed | \*\*51 файл\*\* `test\_\*.py`  • `conftest.py`  • `tests/golden/test\_golden\_dispatch.py`; функций `def test\_` — \*\*747\*\* (784 passed набирается за счёт `@parametrize`) | эталон здоровья сверяем \*\*по выводу pytest\*\*, а не по числу функций. В песочнице pytest не запускается (нет `pyaudio`, `pywin32`) → цифру 784 подтверждает только владелец на своей машине |

| Р-13 | HITL предстоит построить | уже построен: `core/consent\_store.py` (mint / consume / decline / revoke\_session / sweep\_expired / pending / history), fingerprint + session\_id + expiry, `gate.\_consent\_verdict` — «нет ветки, которая исполняет при сомнении» | Д3 и I23 — это \*\*тест на существующее\*\*, а не новая подсистема |

| Р-14 | 9 файлов в `actions/` | \*\*20 файлов\*\*. Незаявленные: `google\_search\_api.py`, `web\_fetch.py`, `youtube\_video.py`, `screen\_processor.py`, `open\_path.py`, `open\_search\_source.py`, `desktop.py`, `code\_helper.py`, `weather\_report.py`, `fileops\_bridge.py`, `reminder.py` | allowlist агентов надо строить от фактических 20, иначе часть инструментов останется вне контроля |

| Р-15 | список модулей `core/` | незаявленные: `aux\_model.py` (92), `model\_guard.py` (123), `provider\_health.py` (128), `query\_rewriter.py` (696), `semantic\_interpreter.py` (351), `dialogue\_state.py` (250), `session\_manager.py` (359), `uncertainty\_policy.py` (163), `response\_composer.py` (299), `screen\_share\_manager.py` (596), `consent\*.py` (819), `search\_\*.py`, `time\_utils.py`, `date\_normalizer.py`; в корне `ui.py` (52 КБ) и `consent\_mode.py` | метеринг (I16) обязан обернуть \*\*`core/aux\_model.py`\*\*, потому что именно он ходит в модель. `provider\_health.py` — заготовка под Д33 |

| Р-16 | «6 ролей моделей» | все четыре рабочие роли сидят на моделях с \*\*20 RPD\*\* → суммарно 80 вызовов в сутки; комментарий в `registry.yaml` «NOT flash-lite to avoid quota» больше не верен; `live\_screen` отключён (close 1007) | фаза 0.5 обязательна и стоит \*\*до\*\* любых агентов: без неё агенты умрут на четвёртой задаче |

| Р-17 | — | `requires-python = "\~=3.12.0"`, `package = false`, `addopts = "-p no:capture"`, `pythonpath = \["."]` | тесты запускаются только как `python -m pytest` \*\*из корня проекта\*\*. Любая инструкция владельцу это учитывает |



\### 0.2. Пять замеров, которые владелец делает сам (в песочнице невозможно)



| Что замерить | Команда / действие | Зачем |

| --- | --- | --- |

| Фактическое ОЗУ Jarvis | запустить Jarvis, в Диспетчере задач посмотреть `python.exe` через 5 минут работы | \*\*ЗАМЕРЕНО 05.08.2026 (Ф15):\*\* 277 МБ в ОЗУ, 603 МБ выделено. Целевой бюджет 500 МБ уже превышен без агентов → ленивые импорты и вычисляемый потолок задач, см. 13.6.7–13.6.8 |

| Реальное число тестов | `python -m pytest -q` из корня | зафиксировать эталон здоровья одной строкой |

| Число событий шины | `python -c "import core.bus as b; print(len(b.EVENTS))"` | закрыть Р-2 |

| Список таблиц | `sqlite3 %USERPROFILE%\\.jarvis\\jarvis.db ".tables"` | подтвердить Р-5 перед миграциями |

| Текущий `user\_version` | `sqlite3 … "PRAGMA user\_version;"` | \*\*ЗАМЕРЕНО (Ф8):\*\* `user\_version = 6` → наши миграции начинаются с 7; файла `history.db` нет |



\## Раздел 1. Целевая архитектура одной картинкой



```jsx

&#x20;                   ┌──────────────── ВЛАДЕЛЕЦ ────────────────┐

&#x20;                   │  голос · горячая клавиша · меню в трее   │

&#x20;                   └───────┬──────────────────────┬───────────┘

&#x20;                           │ звук                 │ СТОП (3 пути, Д37/I39)

&#x20;                 ┌─────────▼──────────┐   ┌───────▼─────────┐

&#x20;                 │  main.py           │   │ core/redbutton  │

&#x20;                 │  JarvisLive        │◄──┤ (мгновенная     │

&#x20;                 │  ГЛАВНЫЙ АГЕНТ     │   │  заморозка)     │

&#x20;                 │  live-модель       │   └─────────────────┘

&#x20;                 │  function calling  │

&#x20;                 └───┬────────┬───────┘

&#x20;         мгновенные  │        │ delegate(...)  (Д11: у главного только фаст-пасы)

&#x20;         инструменты │        │

&#x20;                     │  ┌─────▼──────────────────┐

&#x20;                     │  │ agent/orchestrator.py  │  ← КОД, ноль токенов

&#x20;                     │  │ + agent/task\_queue     │     (менеджер задач)

&#x20;                     │  │ + core/scheduler.py    │

&#x20;                     │  └─────┬──────────────────┘

&#x20;                     │        │ задача (JSON v1, task\_id)

&#x20;                     │  ┌─────▼──────────────────────────────┐

&#x20;                     │  │ РАБОЧИЕ АГЕНТЫ = ПОТОКИ           │

&#x20;                     │  │ Оператор ПК · Файловый клерк (Д7) │

&#x20;                     │  │ allowlist из config/agents.yaml   │

&#x20;                     │  └─────┬──────────────────────────────┘

&#x20;                     │        │ отчёт (JSON v1) — ДАННЫЕ, не инструкции (I14)

═════════ ГРАНИЦА ДОВЕРИЯ Г-1 ═╪═══════════════════════════════════════════

&#x20;                     └────────┼─────────┐

&#x20;                              ▼         ▼

&#x20;                    ┌──────────────────────────┐

&#x20;                    │ core/gate.py  dispatch(  │  ЕДИНСТВЕННАЯ ДВЕРЬ

&#x20;                    │   tool, params, \*, mode, │  + ctx: TaskCtx  ← НОВОЕ

&#x20;                    │   screen\_control, ctx)   │  origin\_chain (I11)

&#x20;                    └───┬──────────────┬───────┘

&#x20;                        │              │ отказ/подтверждение

&#x20;               ┌────────▼───┐   ┌──────▼────────────┐

&#x20;               │ actions/\*  │   │ core/consent\_store│ (HITL, уже есть)

&#x20;               │ 20 файлов  │   └───────────────────┘

&#x20;               └────────────┘



СКВОЗНЫЕ ЛИНИИ (проходят через всё):

• core/metering.py   — перед КАЖДЫМ вызовом модели (I16)

• core/blackbox.py   — пишет всегда: тело 7 дней + шапка навсегда (Д38)

• core/speech\_queue  — ровно одна речь в момент времени (I29), говорит только главный (I20)

• core/resources.py  — микрофон/экран/мышь выдаются по одному (I13)

• core/store.py      — SQLite \~/.jarvis/jarvis.db (user\_version=6), WAL, наши миграции 7–18; вторая база \~/.jarvis/history.db со своей нумерацией — файла пока нет

```



\### 1.1. Пять границ доверия



| Граница | Между чем и чем | Что проверяется на границе | Почему именно так |

| --- | --- | --- | --- |

| Г-1 | отчёт агента → главный | отчёт разбирается как данные по JSON-схеме; любой текст внутри не может стать командой (I14) | иначе подчинённый агент, наглотавшийся текста из интернета, начинает управлять главным — это классическая инъекция |

| Г-2 | агент → действие | только `gate.dispatch` с `ctx`; инструменты вне allowlist агента не видны (I17) | одна дверь = одно место, где можно всё запретить, и один журнал |

| Г-3 | что-либо → память/личность/правила | пишет только главный по явной команде владельца; агенты — никогда (I12, I42) | память формирует будущее поведение; если её может править фон, поведение меняется незаметно |

| Г-4 | наружу (сеть, отправка, публикация) | классификатор чувствительности (I25) + предъявление полного текста + подтверждение (Д12) | «сказать» — тоже действие; утечка необратима, обратных билетов нет |

| Г-5 | чёрный ящик → диск | секреты заменяются заглушками до записи (I40) | запись, содержащая ключ, превращает журнал отладки в новую утечку |



\### 1.2. Что переиспользуется, что создаётся, что удаляется



\*\*Переиспользуется без переписывания (13 узлов):\*\* `core/gate.py` (расширяем сигнатуру), `core/security.py`, `core/store.py` (механизм миграций), `core/consent\_store.py` + `consent.py` + `consent\_runtime.py` (HITL готов), `core/journal.py`, `core/staging.py`, `core/recycle.py`, `core/bus.py`, `core/safe\_json.py`, `core/feature\_flags.py`, `config/loader.py`, `memory/fact\_store.py`, `tests/golden/test\_golden\_dispatch.py`.



\*\*Создаётся заново (сгруппировано по фазам):\*\*



| Группа | Файлы |

| --- | --- |

| Сквозной контекст и бюджет | `core/task\_context.py`, `core/metering.py`, `core/resources.py`, `core/memwatch.py` |

| Запись и воспроизведение | `core/blackbox.py`, `tools/replay\_session.py` |

| Агенты | `agent/orchestrator.py`, `agent/registry.py`, `agent/contracts.py`, `config/agents.yaml`, `config/task\_types.yaml` |

| Голос и внимание | `core/speech\_queue.py`, `core/scheduler.py`, `core/notify.py`, `core/redbutton.py` |

| Интерфейс без фокуса | `core/tray.py`, `core/status\_window.py`, `core/results.py` |

| Качество и правила | `core/acceptance.py`, `core/quality\_report.py`, `core/rules.py` |

| Безопасность | `core/fences.py`, `core/sensitivity.py` |

| Поставщики моделей | `core/provider/base.py`, `gemini.py`, `openai\_compat.py`, `raw\_http.py` |

| Служебное | `core/instance\_lock.py`, `core/spawned.py`, `tools/install\_autostart.py`, `tools/uninstall\_autostart.py`, `config/fastpass.yaml`, `config/budgets.yaml` |



\*\*Удаляется / вычищается:\*\* мёртвый `\_ask\_gemini` и `\_run\_visible` в `actions/cmd\_control.py`; недостижимая ветка `pip install`; вызов `wmic product get name`; `shell=True` в POSIX-ветке; роль `fix\_legacy` из `registry.yaml`; старый SDK `google-generativeai` из `pyproject.toml` после переезда `agent/executor.py`.



\## Раздел 2. Схема данных целиком и сразу



Все новые таблицы — с префиксом `mx\_` (Р-5). Миграции 7–18 добавляются в существующий механизм `core/store.py::migrate()` (Р-4). Правило Д36: \*\*только добавлять\*\*, никогда не удалять и не переименовывать колонки.



\### 2.1. DDL



```sql

\-- migration 7: задачи

CREATE TABLE IF NOT EXISTS mx\_task (

&#x20; task\_id      TEXT PRIMARY KEY,      -- 'T-20260804-001', сквозной идентификатор

&#x20; parent\_id    TEXT,                  -- task\_id родителя; NULL = от владельца

&#x20; depth        INTEGER NOT NULL,      -- 0 владелец, 1 агент, 2 предел рекурсии

&#x20; type         TEXT NOT NULL,         -- из закрытого списка config/task\_types.yaml

&#x20; form\_key     TEXT NOT NULL,         -- отпечаток "той же формы" для Д18

&#x20; title        TEXT NOT NULL,         -- как задачу назовёт голос при перечислении

&#x20; payload\_json TEXT NOT NULL,         -- задача по JSON-схеме v1

&#x20; state        TEXT NOT NULL,         -- автомат состояний, см. 3.4

&#x20; priority     INTEGER NOT NULL,      -- меньше = раньше; задачи со временем ниже 0 (Д19)

&#x20; due\_utc      TEXT,                  -- ISO; NULL если без времени

&#x20; agent\_role   TEXT,                  -- роль исполнителя, НЕ имя (I21)

&#x20; created\_utc  TEXT NOT NULL,

&#x20; updated\_utc  TEXT NOT NULL,

&#x20; finished\_utc TEXT,

&#x20; cancel\_reason TEXT                  -- 'owner\_stop' | 'superseded' | 'budget' | 'error'

);

CREATE INDEX IF NOT EXISTS mx\_task\_state\_idx ON mx\_task(state, priority, due\_utc);

CREATE INDEX IF NOT EXISTS mx\_task\_form\_idx  ON mx\_task(form\_key, state);



\-- migration 8: чек-лист приёмки (контур A, Д39)

CREATE TABLE IF NOT EXISTS mx\_task\_check (

&#x20; task\_id   TEXT NOT NULL,

&#x20; seq       INTEGER NOT NULL,

&#x20; source    TEXT NOT NULL,   -- 'owner\_said' | 'tech\_integrity'; иных значений нет (I41)

&#x20; quote     TEXT,            -- дословные слова владельца, если source='owner\_said'

&#x20; kind      TEXT NOT NULL,   -- 'file\_exists' | 'file\_nonempty' | 'count\_ge' | 'ext\_is' | 'no\_error'

&#x20; arg\_json  TEXT NOT NULL,

&#x20; result    TEXT,            -- 'pass' | 'fail' | 'unknown'; при сомнении 'unknown' и НЕ блокируем

&#x20; PRIMARY KEY (task\_id, seq)

);



\-- migration 9: отчёты

CREATE TABLE IF NOT EXISTS mx\_report (

&#x20; report\_id   TEXT PRIMARY KEY,

&#x20; task\_id     TEXT NOT NULL,

&#x20; schema\_ver  INTEGER NOT NULL,   -- версия контракта отчёта, обязательна с первого дня

&#x20; status      TEXT NOT NULL,      -- 'done' | 'partial' | 'failed' | 'refused'

&#x20; body\_json   TEXT NOT NULL,      -- данные, не инструкции (I14)

&#x20; created\_utc TEXT NOT NULL

);



\-- migration 10: метеринг вызовов моделей

CREATE TABLE IF NOT EXISTS mx\_meter\_call (

&#x20; call\_id     TEXT PRIMARY KEY,

&#x20; quota\_day   TEXT NOT NULL,   -- сутки по Pacific; сброс 11:00 МСК

&#x20; role        TEXT NOT NULL,   -- 'live\_voice' | 'aux\_light' | 'aux\_cheap' | 'vision' | 'embedder'

&#x20; model\_name  TEXT NOT NULL,

&#x20; task\_id     TEXT,            -- NULL для диалога без задачи

&#x20; bucket      TEXT NOT NULL,   -- 'dialog' | 'task' | 'background'  (заготовка под О23)

&#x20; in\_tokens   INTEGER,

&#x20; out\_tokens  INTEGER,

&#x20; ok          INTEGER NOT NULL, -- 1 успех, 0 отказ

&#x20; err\_kind    TEXT,             -- 'rpd' | 'rpm' | 'tpm' | 'network' | 'other'

&#x20; started\_utc TEXT NOT NULL,

&#x20; ms          INTEGER

);

CREATE INDEX IF NOT EXISTS mx\_meter\_day\_idx ON mx\_meter\_call(quota\_day, role);



\-- migration 11: чёрный ящик, ТЕЛО (7 дней)

CREATE TABLE IF NOT EXISTS mx\_bb\_body (

&#x20; rec\_id   TEXT NOT NULL,

&#x20; seq      INTEGER NOT NULL,

&#x20; kind     TEXT NOT NULL,   -- 'speech\_in'|'prompt'|'model\_out'|'tool\_call'|'gate\_verdict'|'report'|'spoken'

&#x20; payload  TEXT NOT NULL,   -- секреты уже заглушены (I40)

&#x20; ts\_utc   TEXT NOT NULL,

&#x20; PRIMARY KEY (rec\_id, seq)

);



\-- migration 12: чёрный ящик, ШАПКА (живёт долго, без свободного текста, I45)

CREATE TABLE IF NOT EXISTS mx\_bb\_head (

&#x20; rec\_id      TEXT PRIMARY KEY,

&#x20; task\_id     TEXT,

&#x20; code\_ver    TEXT NOT NULL,   -- версия из pyproject, чтобы отчёт можно было соотнести с кодом

&#x20; quota\_day   TEXT NOT NULL,

&#x20; calls\_n     INTEGER NOT NULL,

&#x20; tools\_n     INTEGER NOT NULL,

&#x20; blocked\_n   INTEGER NOT NULL,

&#x20; outcome     TEXT NOT NULL,   -- 'done'|'partial'|'failed'|'cancelled'

&#x20; body\_purged INTEGER NOT NULL DEFAULT 0,

&#x20; created\_utc TEXT NOT NULL

);



\-- migration 13: правила владельца (контур D запрещён; правила пишет только владелец)

CREATE TABLE IF NOT EXISTS mx\_owner\_rule (

&#x20; rule\_id     TEXT PRIMARY KEY,

&#x20; text        TEXT NOT NULL,        -- дословно, лимит символов проверяет код

&#x20; said\_utc    TEXT NOT NULL,

&#x20; state       TEXT NOT NULL,        -- 'active' | 'trashed'

&#x20; trashed\_utc TEXT                  -- корзина 30 дней

);



\-- migration 14: журнал памяти (Д31)

CREATE TABLE IF NOT EXISTS mx\_memory\_journal (

&#x20; entry\_id  TEXT PRIMARY KEY,

&#x20; fact\_id   TEXT,                   -- ссылка на memory\_fact

&#x20; op        TEXT NOT NULL,          -- 'add' | 'forget'

&#x20; text      TEXT NOT NULL,

&#x20; spoken    INTEGER NOT NULL,       -- 1 = проговорено владельцу (I35)

&#x20; ts\_utc    TEXT NOT NULL

);



\-- migration 15: индекс результатов (Д13, Д34)

CREATE TABLE IF NOT EXISTS mx\_result (

&#x20; result\_id  TEXT PRIMARY KEY,

&#x20; task\_id    TEXT NOT NULL,

&#x20; path       TEXT NOT NULL,         -- \~/jarvis/results/...

&#x20; keep       INTEGER NOT NULL,      -- 1 = 'сохрани', навсегда

&#x20; created\_utc TEXT NOT NULL,

&#x20; purge\_utc  TEXT                   -- created + 30 дней, потом в корзину Windows

);



\-- migration 16: статистика агентов + порождённые процессы + напоминания

CREATE TABLE IF NOT EXISTS mx\_agent\_stat (

&#x20; quota\_day TEXT NOT NULL, agent\_role TEXT NOT NULL,

&#x20; tasks\_n INTEGER NOT NULL, fail\_n INTEGER NOT NULL, calls\_n INTEGER NOT NULL,

&#x20; PRIMARY KEY (quota\_day, agent\_role)

);

CREATE TABLE IF NOT EXISTS mx\_spawned (

&#x20; pid INTEGER PRIMARY KEY, cmd\_kind TEXT NOT NULL, task\_id TEXT,

&#x20; started\_utc TEXT NOT NULL, reaped\_utc TEXT

);

CREATE TABLE IF NOT EXISTS mx\_reminder (

&#x20; rem\_id TEXT PRIMARY KEY, text TEXT NOT NULL, due\_utc TEXT NOT NULL,

&#x20; pre\_done INTEGER NOT NULL DEFAULT 0,   -- предупреждение за 15 минут

&#x20; main\_done INTEGER NOT NULL DEFAULT 0,  -- в момент

&#x20; retry\_done INTEGER NOT NULL DEFAULT 0, -- один повтор через 3 минуты

&#x20; state TEXT NOT NULL                    -- 'armed'|'done'|'cancelled'

);



\-- migration 17: счётчики (инициативные реплики, самонаблюдение)

CREATE TABLE IF NOT EXISTS mx\_counter (

&#x20; quota\_day TEXT NOT NULL, name TEXT NOT NULL, n INTEGER NOT NULL,

&#x20; PRIMARY KEY (quota\_day, name)

);

```



\### 2.2. Кто пишет и кто читает (главное)



| Таблица | Пишет | Читает | Срок жизни |

| --- | --- | --- | --- |

| `mx\_task` | `agent/orchestrator.py` | оркестратор, `core/status\_window.py`, голос при перечислении | до очистки вручную; завершённые старше 30 дней подчищает `core/scheduler.py` |

| `mx\_task\_check` | `core/acceptance.py` | `core/acceptance.py`, окно состояния | вместе с задачей |

| `mx\_report` | рабочий агент (поток) | главный через Г-1 | 30 дней |

| `mx\_meter\_call` | только `core/metering.py` | метеринг, окно состояния | 30 дней (для разбора расхода) |

| `mx\_bb\_body` | `core/blackbox.py` | `tools/replay\_session.py` | \*\*7 дней\*\*, затем `body\_purged=1` |

| `mx\_bb\_head` | `core/blackbox.py` | владелец через окно состояния | навсегда (данных мало, свободного текста нет) |

| `mx\_owner\_rule` | главный по явной команде владельца | сборка контекста запроса | активные — навсегда, корзина 30 дней |

| `mx\_result` | `core/results.py` | голос («покажи»), `core/scheduler.py` для уборки | 30 дней, `keep=1` — навсегда |



\### 2.3. Пять категорий атрибуции (контур C, Д39)



`world` (изменилась среда) · `budget` (кончилась квота) · `gate` (запретил гейт) · `own` (ошибка самого Jarvis) · `unknown`. \*\*Пятая категория обязательна:\*\* без неё система вынуждена выбрать виноватого, а угадывание запрещено (самопроверка п. 12).



\### 2.4. Цепочка миграций и обрыв посреди неё



Порядок: `7 → 8 → 9 → 10 → 11 → 12 → 13 → 14 → 15 → 16 → 17 → 18`. Механизм уже есть (Р-4): перед первой миграцией `store.backup()` кладёт копию базы рядом; каждая миграция идёт в своей транзакции `BEGIN … COMMIT`; номер пишется в `applied\_migrations` \*\*внутри той же транзакции\*\*.



| Что случилось | Что происходит фактически | Что видит владелец |

| --- | --- | --- |

| Свет выключили между 10 и 11 | миграции 7–10 зафиксированы, 11 не начата; при следующем старте `migrate()` продолжает с 11 | ничего, старт чуть дольше |

| Ошибка \*\*внутри\*\* миграции 11 | транзакция откатывается целиком, `user\_version` остаётся 10, старт \*\*останавливается\*\* с текстом ошибки | голос говорит «не могу запустить базу, нужна помощь»; работает только режим без базы |

| База из будущего (открыли новым кодом, потом старым) | fail-closed уже реализован (`store.py:421`) | «база новее, чем этот код» — и Jarvis не стартует, вместо тихой порчи данных |

| Копия базы нужна вручную | файл рядом с `jarvis.db`, положенный `backup()` | восстановление = переименовать файл, никакого git не требуется |



\*\*Почему именно так:\*\* одна транзакция на миграцию — единственный способ без git гарантировать, что база либо целиком старая, либо целиком новая. Промежуточных состояний не бывает, значит и «половинчатых» баг-репортов не бывает.



\## Раздел 3. Контракты



\### 3.1. JSON-схема задачи (v1)



```json

{

&#x20; "schema\_ver": 1,

&#x20; "task\_id": "T-20260804-001",

&#x20; "parent\_id": null,

&#x20; "depth": 0,

&#x20; "type": "file\_sort",

&#x20; "form\_key": "file\_sort:\~/Downloads",

&#x20; "title": "разобрать загрузки",

&#x20; "goal": "дословная формулировка владельца",

&#x20; "context\_digest": {

&#x20;   "source": "code",

&#x20;   "items": \[{"kind": "path", "value": "C:/Users/…/Downloads"}]

&#x20; },

&#x20; "acceptance": \[

&#x20;   {"source": "owner\_said", "quote": "чтобы pdf лежали отдельно",

&#x20;    "kind": "ext\_is", "arg": {"dir": "…/PDF", "ext": ".pdf"}}

&#x20; ],

&#x20; "limits": {"max\_llm\_calls": 4, "max\_seconds": 600, "bucket": "task"},

&#x20; "due\_utc": null,

&#x20; "agent\_role": "file\_clerk"

}

```



\*\*Четыре правила контракта.\*\* (1) `schema\_ver` есть с первого дня — иначе через полгода нельзя отличить старую запись от битой. (2) `context\_digest.source` всегда `"code"`: выжимку контекста собирает код, а «сколько» решает главный (Д29, I36). (3`)` `acceptance` может быть пустым — пустой чек-лист ничего не ограничивает (Д39). (4) `agent\_role` — роль, не имя: ядро имён агентов не знает (I21).



\### 3.2. JSON-схема отчёта (v1)



```json

{

&#x20; "schema\_ver": 1,

&#x20; "report\_id": "R-…",

&#x20; "task\_id": "T-20260804-001",

&#x20; "status": "partial",

&#x20; "facts": \[{"kind": "moved", "n": 14}, {"kind": "skipped", "n": 2}],

&#x20; "artifacts": \[{"path": "\~/jarvis/results/T-…-001.md"}],

&#x20; "blocked\_by": \[{"kind": "gate", "tool": "file\_delete", "reason": "needs\_confirm"}],

&#x20; "attribution": "gate",

&#x20; "llm\_calls": 2,

&#x20; "seconds": 41

}

```



Поля `facts`, `blocked\_by` — \*\*только перечисления с числами и кодами\*\*. Свободного текста, который главный мог бы принять за команду, в отчёте нет вообще (I14, Г-1).



\### 3.3. Сигнатуры (без реализации)



```python

\# core/task\_context.py

class TaskCtx:

&#x20;   task\_id: str; parent\_id: str | None; depth: int

&#x20;   agent\_role: str | None; bucket: str        # 'dialog'|'task'|'background'

&#x20;   origin\_chain: tuple\[str, ...]              # ('owner','main','file\_clerk')



\# core/gate.py — расширение существующей функции

def dispatch(tool: str, params: dict | None = None, \*,

&#x20;            mode: str = "interactive", screen\_control: bool = False,

&#x20;            ctx: TaskCtx | None = None) -> dict: ...



\# core/metering.py

def quota\_day(now\_utc) -> str: ...            # единственное место, где живёт "сброс 11:00 МСК"

def reserve(role: str, ctx: TaskCtx | None, est\_in\_tokens: int) -> dict: ...

def commit(call\_id: str, in\_tokens: int, out\_tokens: int, ok: bool, err\_kind: str | None) -> None: ...

def remaining(role: str) -> dict: ...



\# agent/orchestrator.py

def delegate(task: dict, \*, ctx: TaskCtx) -> str: ...      # возвращает task\_id, НОЛЬ токенов

def supersede(form\_key: str, new\_task\_id: str) -> list\[str]: ...   # Д18

def freeze\_all(reason: str) -> int: ...                    # Д17, мгновенно

def cancel\_all(reason: str) -> int: ...



\# core/resources.py

def acquire(resource: str, ctx: TaskCtx, timeout\_s: float) -> object | None: ...

def release(handle: object) -> None: ...



\# core/speech\_queue.py

def say(text: str, \*, priority: int, ttl\_s: float | None, ctx: TaskCtx | None) -> bool: ...

def silence(reason: str) -> None: ...



\# core/scheduler.py

def arm(rem\_id: str, due\_utc: str, text: str) -> None: ...  # Д4/Д30, ноль LLM

def tick(now\_utc) -> list\[dict]: ...



\# core/acceptance.py

def build(goal\_quotes: list\[str]) -> list\[dict]: ...        # только из слов владельца

def verify(task\_id: str) -> dict: ...                       # ноль вызовов модели (I43)



\# core/blackbox.py

def open\_rec(ctx: TaskCtx | None) -> str: ...

def write(rec\_id: str, kind: str, payload) -> None: ...     # секреты заглушает сам

def close\_rec(rec\_id: str, outcome: str) -> None: ...

```



\### 3.4. Автомат состояний задачи



`NEW → QUEUED → RUNNING → VERIFYING → DONE | PARTIAL`, плюс тупики и паузы: `FROZEN` (сказали «стоп»), `WAITING\_OWNER` (нужен HITL), `WAITING` (нет свободного слота из 10), `FAILED`, `CANCELLED`, `SUPERSEDED` (Д18). \*\*После рестарта любая задача в `RUNNING` переводится в `FAILED` с причиной `restart`, автоперезапуска нет\*\* (I15 fail-closed), а любой незакрытый HITL считается отказом (I23, Р-13 — механизм уже есть).



\## Раздел 4. Фазы



Порядок жёсткий: `0 → 0.5 → 1 → 1б → 2 → 2б → 3 → 4 → 5 → 6` и контрольная точка «+3 месяца». Каждая фаза описана по пунктам а–к.



\### Фаза 0. Гигиена и фундамент безопасности



\*\*а) Цель.\*\* Убрать утёкший ключ и сделать так, чтобы флаг `JARVIS\_AGENTS` нельзя было потерять при распаковке нового zip. После фазы: секретов в архиве нет, включатель агентов живёт вне проекта, известно фактическое ОЗУ.



\*\*б) Шаги по порядку.\*\*



1\. Отозвать ключ `AIzaSy…UFWg` в Google AI Studio, выпустить новый.

2\. Создать `\~/.jarvis/secrets.json` (вне папки проекта) и положить новый ключ туда.

3\. Вычистить ключ из `config/api\_keys.json` \*\*и\*\* `config/secrets.json` (Р-11 — их два), заменив на пустые заглушки.

4\. Научить `config/loader.py::get\_api\_key` читать сначала `\~/.jarvis/secrets.json`, потом переменную окружения, и только потом файлы проекта.

5\. Создать `\~/.jarvis/settings.json` и научить `core/feature\_flags.py` читать флаги оттуда (Р-9).

6\. Добавить флаг `JARVIS\_AGENTS`, по умолчанию \*\*выключен\*\*.

7\. Вычистить `actions/cmd\_control.py`: удалить `\_ask\_gemini`, `\_run\_visible`, недостижимую ветку `pip install`, вызов `wmic product get name`, убрать `shell=True` в POSIX-ветке.

8\. Добавить `core/instance\_lock.py` — файл-замок `\~/.jarvis/jarvis.lock` (I28).

9\. Замерить ОЗУ и записать цифру в этот план (0.2).



\*\*в) Файлы.\*\* Создать: `core/instance\_lock.py`, `\~/.jarvis/secrets.json`, `\~/.jarvis/settings.json`. Изменить: `config/loader.py` (`get\_api\_key`, `migrate\_legacy`), `core/feature\_flags.py`, `actions/cmd\_control.py`, `main.py` (вызов замка в `run`), `ARCHITECTURE.md` (снять пункт про ключ). Удалить: строки с ключом в `config/api\_keys.json`, `config/secrets.json`.



\*\*г) Реализуются.\*\* Д8, Д26 (проверка антивируса при старте — только сообщение, без админ-прав), I28. Начало Д2 (флаг).



\*\*д) Тесты.\*\* `tests/test\_secrets\_outside\_project.py` — ключ не находится нигде в папке проекта (поиск по файлам). `tests/test\_flag\_home\_settings.py` — флаг читается из домашней папки, а файл в проекте игнорируется. `tests/test\_instance\_lock.py` — второй запуск завершается сообщением, а не двумя Jarvis. `tests/test\_cmd\_control\_hygiene.py` — в модуле нет `shell=True` и нет `wmic product`.



\*\*е) Критерий готовности.\*\* Владелец удаляет папку проекта, распаковывает свежий zip, запускает Jarvis — и Jarvis работает с новым ключом, а флаг агентов остался в том же положении. Вслух: «Джарвис, ты меня слышишь?» — отвечает. Второй запуск из другой папки говорит «я уже запущен» и закрывается.



\*\*ж) Откат без git.\*\* Старый zip лежит в «Загрузках» — распаковать его в другую папку. Файлы `\~/.jarvis/secrets.json` и `\~/.jarvis/settings.json` не удалять: они переживают откат по своей природе.



\*\*з) Объём.\*\* 2–3 вечера.



\*\*и) Зависимости.\*\* Ничего не блокирует, блокирует \*\*всё\*\* остальное: без выноса секретов любой следующий архив снова унесёт ключ.



\*\*к) НЕ делаем.\*\* Не трогаем `registry.yaml`, не заводим ни одного агента, не меняем сигнатуру гейта.



\### Фаза 0.5. Модели и поставщики



\*\*а) Цель.\*\* Пересадить рабочие роли с моделей по 20 запросов в сутки на модели по 500 (Р-16). После фазы дневного запаса хватает на настоящую работу.



\*\*б) Шаги.\*\*



1\. В `config/registry.yaml`: `aux\_light`, `aux\_heavy`, `vision` → `gemini-3.5-flash-lite` (15 RPM / 500 RPD).

2\. Добавить роль `aux\_cheap` → `gemma-4-26b` (30 RPM / 14 400 RPD, \*\*TPM всего 16 000\*\* → жёсткий предел входа \~12 000 знаков).

3\. Добавить роль `embedder` → `models/text-embedding-004` (100 RPM / 1000 RPD).

4\. Удалить роль `fix\_legacy`, перевести её единственное место применения (`agent/executor.py`, перевод) на новый SDK и роль `aux\_cheap`.

5\. Убрать `google-generativeai` из `pyproject.toml` (Р-10).

6\. Создать слой адаптеров `core/provider/`: `base.py` (общий интерфейс), `gemini.py`, `openai\_compat.py`, `raw\_http.py`; у каждого поставщика поле доверия (Д33).

7\. Убрать из `registry.yaml` устаревший комментарий «NOT flash-lite to avoid quota».



\*\*в) Файлы.\*\* Создать: `core/provider/base.py`, `core/provider/gemini.py`, `core/provider/openai\_compat.py`, `core/provider/raw\_http.py`. Изменить: `config/registry.yaml`, `config/loader.py::get\_model`, `core/aux\_model.py`, `agent/executor.py`, `pyproject.toml`, `core/provider\_health.py`.



\*\*г) Реализуются.\*\* Д33, I37. Снимается РИСК-4.



\*\*д) Тесты.\*\* `tests/test\_registry\_roles.py` — в коде встречаются только имена ролей, ни одного имени модели (I37). `tests/test\_provider\_adapters.py` — три адаптера отвечают на один и тот же вызов одинаковой структурой (моки, ноль токенов). `tests/test\_tpm\_limit\_gemma.py` — вход длиннее предела обрезается \*\*до\*\* отправки.



\*\*е) Критерий готовности.\*\* Вслух: «Джарвис, переведи фразу на английский» — десять раз подряд, и на десятый раз ответ такой же живой. До фазы 0.5 на четвёртой–пятой попытке был бы отказ по квоте.



\*\*ж) Откат.\*\* `config/registry.yaml` — текстовый файл; сохранить копию `registry.yaml.bak` рядом и вернуть её.



\*\*з) Объём.\*\* 2–3 вечера.



\*\*и) Зависимости.\*\* После фазы 0. Блокирует фазу 2 (агенты без квоты бессмысленны).



\*\*к) НЕ делаем.\*\* Не подключаем сторонних поставщиков реально — только интерфейс и один рабочий (Gemini).



\### Фаза 1. Каркас: сквозной task\_id, данные, контракты, чёрный ящик



\*\*а) Цель.\*\* Заложить всё, \*\*что нельзя добавить задним числом\*\*. После фазы агентов ещё нет, но каждое действие уже имеет идентификатор задачи, версию схемы и запись.



\*\*б) Шаги.\*\*



1\. `core/task\_context.py` — класс `TaskCtx` (см. 3.3).

2\. Миграции 7–18 в `core/store.py::migrate()` (см. 2.1).

3\. `agent/contracts.py` — проверка JSON задачи и отчёта по схеме v1, `schema\_ver` обязателен.

4\. `core/metering.py` — `quota\_day`, `reserve`, `commit`, `remaining`; обернуть `core/aux\_model.py`, чтобы \*\*ни один\*\* вызов модели не проходил мимо (I16).

5\. `core/blackbox.py` — тело и шапка отдельно (`mx\_bb\_body`, `mx\_bb\_head`), заглушки секретов до записи.

6\. Персистентная очередь: переписать `agent/task\_queue.py` на `mx\_task`; после рестарта `RUNNING → FAILED('restart')`, автоперезапуска нет (I15).

7\. `config/task\_types.yaml` — \*\*закрытый\*\* список типов задач.

8\. Атомарная запись состояния: запись во временный файл и переименование (I24).



\*\*в) Файлы.\*\* Создать: `core/task\_context.py`, `core/metering.py`, `core/blackbox.py`, `agent/contracts.py`, `config/task\_types.yaml`. Изменить: `core/store.py` (миграции 7–18), `agent/task\_queue.py`, `core/aux\_model.py`, `core/bus.py` (новые события в существующий реестр).



\*\*г) Реализуются.\*\* Д36, Д38, I15, I16, I24, I45, а также все шесть пунктов «нельзя добавить задним числом»: сквозной `task\_id`, тело/шапка отдельно, чек-лист как поле задачи (структура), запись отчётов подчинённых, метеринг и версия кода в шапке, закрытый список типов задач, `schema\_version` в контракте отчёта.



\*\*д) Тесты.\*\* `tests/test\_migrations\_7\_18.py` — миграции применяются на пустой и на существующей базе, повторный запуск ничего не ломает. `tests/test\_migration\_abort.py` — искусственная ошибка в 11 оставляет `user\_version=10`. `tests/test\_metering\_no\_bypass.py` — поиск по исходникам: обращений к модели вне метеринга нет (I16). `tests/test\_blackbox\_no\_secrets.py` — подсунутый ключ в записи заменён заглушкой (I40). `tests/test\_queue\_restart\_failclosed.py` — после перезапуска задача в `FAILED`, а не снова в работе. `tests/test\_contracts\_schema\_ver.py` — отчёт без `schema\_ver` отвергается.



\*\*е) Критерий готовности.\*\* Вслух: «Джарвис, найди мне погоду» — обычный ответ. Затем владелец открывает `\~/.jarvis/jarvis.db` и видит: в `mx\_meter\_call` появилась строка с сегодняшним `quota\_day`, в `mx\_bb\_head` — запись с версией кода, в теле записи ключа нет. И `python -m pytest -q` показывает \*\*784 + новые\*\*.



\*\*ж) Откат.\*\* Распаковать старый zip; базу вернуть из копии, которую положил `store.backup()` перед миграциями. Флаг `JARVIS\_AGENTS` при этом остаётся выключенным, поэтому даже полусделанная фаза не влияет на поведение.



\*\*з) Объём.\*\* 8–12 вечеров. Это самая большая и самая скучная фаза: видимого результата нет, а работы много.



\*\*и) Зависимости.\*\* После 0.5. Блокирует \*\*все\*\* последующие фазы.



\*\*к) НЕ делаем.\*\* Ни одного агента. Не меняем сигнатуру `gate.dispatch` — это фаза 1б.



\### Фаза 1б. Гейт: origin\_chain и заборы



\*\*а) Цель.\*\* Сделать так, чтобы по журналу гейта всегда было видно, \*\*кто именно\*\* попросил действие. После фазы каждое действие подписано цепочкой происхождения.



\*\*б) Шаги.\*\*



1\. Добавить в `core/gate.py::dispatch` необязательный параметр `ctx: TaskCtx | None = None`. \*\*Значение по умолчанию `None` сохраняет старое поведение и не ломает 747 существующих тестов\*\* — это ключевое решение фазы.

2\. Расширить запись аудита `logs/gate-audit.jsonl` полями `task\_id`, `agent\_role`, `origin\_chain`, `depth`.

3\. `core/fences.py` — заборы: агенты не могут вызвать инструменты записи в память, личность, правила, а также vision без явного контекста (I12, Г-3).

4\. `core/sensitivity.py` — классификатор чувствительности перед отправкой наружу (I25, Д22).

5\. Провести «разглашение» через гейт как отдельный инструмент (I22).

6\. Фильтр записи в память: только главный, только по явной команде, всегда проговаривается (I35).



\*\*в) Файлы.\*\* Изменить: `core/gate.py`, `core/security.py`, `memory/memory\_manager.py`, `memory/personality\_engine.py`. Создать: `core/fences.py`, `core/sensitivity.py`.



\*\*г) Реализуются.\*\* Д12, Д22, I11, I12, I22, I25, I27, I32, I35.



\*\*д) Тесты.\*\* `tests/test\_gate\_origin\_chain.py` — при вызове с `ctx` в аудите появляется цепочка; без `ctx` формат старый. `tests/test\_fences\_memory.py` — попытка агента записать в память отклоняется с понятной причиной. `tests/test\_sensitivity\_block.py` — текст с паролем не уходит наружу. Обновляется golden-набор: `tests/golden/test\_golden\_dispatch.py` получает новые кейсы с `origin\_chain`.



\*\*е) Критерий готовности.\*\* Вслух: «Джарвис, запомни, что я не пью кофе после шести» — Jarvis проговаривает: «Записал: не пьёте кофе после шести. Скажите «забудь последнее», если не надо». В `logs/gate-audit.jsonl` последняя строка содержит `origin\_chain: \["owner","main"]`.



\*\*ж) Откат.\*\* Убрать вызовы с `ctx`; так как параметр необязательный, система возвращается к прежнему поведению без правки остального кода.



\*\*з) Объём.\*\* 4–6 вечеров.



\*\*и) Зависимости.\*\* После фазы 1 (нужен `TaskCtx`). Блокирует фазу 2: агентов нельзя пускать через гейт, который не помнит, кто просил.



\*\*к) НЕ делаем.\*\* Не строим менеджер ресурсов — он в фазе 2.



\### Фаза 2. Первые два агента



\*\*а) Цель.\*\* Появляются Оператор ПК и Файловый клерк (Д7) как \*\*потоки\*\*. После фазы владелец может сказать «разберись с загрузками» и продолжить играть.



\*\*б) Шаги.\*\*



1\. `agent/registry.py` — загрузка `config/agents.yaml`, проверка ядром: инструменты навыка ⊆ allowlist агента (I17). Проверка при загрузке, не при вызове.

2\. `config/agents.yaml` — две роли, allowlist от фактических 20 файлов `actions/` (Р-14).

3\. `agent/orchestrator.py` — `delegate`, `supersede`, `freeze\_all`, `cancel\_all`; \*\*ноль токенов\*\*, это чистый код.

4\. Инструмент `delegate` в декларациях `main.py` — решение «сам или делегирую» принимает live-модель через function calling, отдельного роутера нет.

5\. `core/resources.py` — микрофон, экран, мышь, буфер выдаются по одному (I13).

6\. `core/memwatch.py` — потолок одновременных задач вычисляется по свободной памяти перед каждым стартом (по умолчанию 2, верхняя граница из конфига), при нехватке — режимы M1/M2 и предложение закрыть программу-виновника (Д48); числа из 13.6.7–13.6.8.

7\. Выжимку контекста для агента собирает код, «сколько» просит главный (Д5, Д29, I36).

8\. Потолок активных задач вычисляется по памяти (по умолчанию 2, верхняя граница 10 в конфиге) + список ожидания, приоритет у задач со временем (Д19).



\*\*в) Файлы.\*\* Создать: `agent/orchestrator.py`, `agent/registry.py`, `core/resources.py`, `core/memwatch.py`, `config/agents.yaml`. Изменить: `main.py` (`TOOL\_DECLARATIONS`, `\_execute\_tool`), `agent/executor.py` (вызов гейта с `ctx`), `agent/planner.py`.



\*\*г) Реализуются.\*\* Д2, Д5, Д7, Д11, Д19, Д29, I13, I17, I21, I26, I36.



\*\*д) Тесты.\*\* `tests/test\_agent\_allowlist.py` — навык с инструментом вне allowlist не грузится вовсе. `tests/test\_orchestrator\_zero\_tokens.py` — при делегировании метеринг не получил ни одного вызова. `tests/test\_resource\_exclusive.py` — второй поток не получает мышь, пока держит первый. `tests/test\_ten\_active\_limit.py` — одиннадцатая задача уходит в `WAITING`. `tests/test\_core\_knows\_no\_names.py` — поиск по `core/` не находит ни одного имени агента (I21).



\*\*е) Критерий готовности.\*\* Вслух: «Джарвис, разбери папку загрузок по типам файлов» → голос отвечает «Взял, сэр» \*\*сразу\*\*, владелец продолжает работу, через несколько минут: «Готово, отчёт в файле». Файл лежит в `\~/jarvis/results`. Диспетчер задач показывает один процесс `python.exe` и свободной памяти в системе не стало меньше 200 МБ, а при искусственном сжатии памяти Jarvis переходит в облегчённый режим и продолжает отвечать голосом.



\*\*ж) Откат.\*\* Выключить `JARVIS\_AGENTS` в `\~/.jarvis/settings.json` — Jarvis мгновенно возвращается к монолитному поведению, весь код агентов остаётся на диске мёртвым.



\*\*з) Объём.\*\* 6–8 вечеров.



\*\*и) Зависимости.\*\* После 1б. Блокирует 2б.



\*\*к) НЕ делаем.\*\* Не даём агентам говорить (говорит только главный, I20). Не строим трей и окно состояния. Не даём агентам порождать под-агентов дальше второго уровня.



\### Фаза 2б. Голос, внимание, стоп



\*\*а) Цель.\*\* Jarvis перестаёт перебивать сам себя и получает три независимых пути остановки. После фазы работать рядом с ним комфортно.



\*\*б) Шаги.\*\*



1\. `core/speech\_queue.py` — ровно одна речь в момент времени, ответ владельцу вытесняет фоновую реплику (I29, Д14, 11 пунктов очереди).

2\. `core/redbutton.py` — «стоп» = мгновенно замереть всё, затем перечислить и спросить; «отмени всё» — без вопросов (Д17). Останов доступен \*\*голосом, горячей клавишей и через меню трея\*\* (Д37, I39).

3\. `core/tray.py` — иконка четырёх цветов, меню правой кнопкой; `core/status\_window.py` — окно только по требованию; \*\*ничто не выдёргивает фокус\*\*.

4\. `core/notify.py` — уведомление Windows и звук, когда сети нет (режим N).

5\. Право говорить первым по трём поводам, лимит 10 инициативных реплик в сутки, число в `config/settings` и правится владельцем в коде (Д14, Д20, I30).

6\. Реакция на движение мыши: замереть, спросить, молчание 30 с = свернуть (Д6).

7\. Д25: чистое закрытие — сразу; несохранённое — спросить и 15 с молчания = не закрывать; зависшее — явное «да»; молча не убивать никогда.



\*\*в) Файлы.\*\* Создать: `core/speech\_queue.py`, `core/redbutton.py`, `core/tray.py`, `core/status\_window.py`, `core/notify.py`. Изменить: `main.py` (`\_run\_session`, весь вывод речи идёт через очередь), `ui.py`.



\*\*г) Реализуются.\*\* Д6, Д14, Д17, Д20, Д25, Д37, I20, I29, I30, I39.



\*\*д) Тесты.\*\* `tests/test\_speech\_single.py` — две одновременные реплики выстраиваются в очередь, а не звучат вместе. `tests/test\_stop\_three\_paths.py` — каждый из трёх путей приводит к `freeze\_all`. `tests/test\_no\_focus\_steal.py` — окно создаётся без активации. `tests/test\_initiative\_cap.py` — одиннадцатая обычная реплика не произносится, срочная произносится всегда. `tests/test\_close\_unsaved.py` — 15 с молчания = не закрывать.



\*\*е) Критерий готовности.\*\* Владелец даёт две задачи подряд, потом говорит «стоп». Речь прерывается \*\*мгновенно\*\*, затем Jarvis перечисляет: «Остановил две задачи: разбор загрузок и поиск. Продолжать?». Иконка в трее становится жёлтой. Ни одно окно не забрало фокус из игры.



\*\*ж) Откат.\*\* Флаг `JARVIS\_AGENTS` выключить; очередь речи оставить включённой — она безопасна сама по себе и не зависит от агентов.



\*\*з) Объём.\*\* 5–7 вечеров.



\*\*и) Зависимости.\*\* После фазы 2.



\*\*к) НЕ делаем.\*\* Не оцениваем качество работы — это фазы 3 и 4.



\### Фаза 3. Приёмка (контур A) и запись задачи (контур B)



\*\*а) Цель.\*\* Jarvis перестаёт докладывать «готово», когда условие владельца не выполнено. После фазы у каждой задачи есть проверяемый чек-лист.



\*\*б) Шаги.\*\*



1\. `core/acceptance.py::build` — чек-лист собирается \*\*только\*\* из произнесённых владельцем условий плюс техническая целостность. Добавлять условия, которых владелец не называл, запрещено (Д39, I41).

2\. `verify` — сверяет код, \*\*ноль вызовов модели\*\* (I43); при сомнении результат `unknown` и задача не блокируется.

3\. `core/quality\_report.py` — одна запись на задачу: тело 7 дней, шапка без свободного текста с версией кода (I45). Мгновенные команды не пишутся вовсе.

4\. Уборка тела старше 7 дней в `core/scheduler.py`.



\*\*в) Файлы.\*\* Создать: `core/acceptance.py`, `core/quality\_report.py`. Изменить: `agent/orchestrator.py` (состояние `VERIFYING`), `core/blackbox.py`.



\*\*г) Реализуются.\*\* Д39 (контуры A и B), Д28, I41, I43, I45.



\*\*д) Тесты.\*\* `tests/test\_acceptance\_only\_owner\_words.py` — из фразы без условий получается \*\*пустой\*\* чек-лист. `tests/test\_acceptance\_zero\_llm.py` — проверка не вызвала модель. `tests/test\_acceptance\_unknown\_not\_block.py` — недоступный файл даёт `unknown`, задача завершается `PARTIAL`, а не висит. `tests/test\_head\_no\_freetext.py` — в шапке нет ни одного текстового поля произвольной длины.



\*\*е) Критерий готовности.\*\* Вслух: «Джарвис, разбери загрузки, и чтобы pdf лежали отдельно». В отчёте: «Разобрал. Условие про pdf выполнено: 14 файлов в папке PDF». Затем то же с условием, которое выполнить нельзя — Jarvis говорит `partial` и называет причину, а не «готово».



\*\*ж) Откат.\*\* Отключить проверку одним флагом в `\~/.jarvis/settings.json`; задачи продолжат завершаться как раньше.



\*\*з) Объём.\*\* 4–5 вечеров.



\*\*и) Зависимости.\*\* После 2б.



\*\*к) НЕ делаем.\*\* Не выводим никаких правил автоматически — контур D запрещён.



\### Фаза 4. Атрибуция (контур C) и правила владельца



\*\*а) Цель.\*\* Jarvis честно относит неудачу к одной из пяти категорий и хранит правила, которые владелец \*\*сказал сам\*\*. После фазы поведение можно менять словами и видеть списком.



\*\*б) Шаги.\*\*



1\. Атрибуция по пяти категориям (2.3), включая `unknown`.

2\. Оценка учитывается только по явным словам владельца плюс два бесспорных факта: отмена на середине и переформулировка в пределах 10 минут. Окно направленности реплики — 60 секунд.

3\. Перед привязкой оценки Jarvis \*\*предъявляет задачу\*\*: «Вы про разбор загрузок?» (I44).

4\. `core/rules.py` — правила пишет только владелец голосом, лимит символов, правила закреплены в контексте и не сокращаются; просмотр вместе с фактами памяти; корзина 30 дней (Д39-D, I42).

5\. Два счётчика самонаблюдения в `mx\_counter`; граница рекурсии — второй уровень.



\*\*в) Файлы.\*\* Создать: `core/rules.py`. Изменить: `core/quality\_report.py`, `memory/memory\_manager.py` (единый просмотр), `main.py` (декларации «запомни правило», «покажи правила», «удали правило»).



\*\*г) Реализуются.\*\* Д31, Д39 (контуры C и запрет D), I42, I44.



\*\*д) Тесты.\*\* `tests/test\_attribution\_unknown.py` — при неясной причине ставится `unknown`, а не «сам виноват». `tests/test\_rule\_never\_acceptance.py` — правило владельца никогда не попадает в чек-лист приёмки (I41). `tests/test\_rating\_needs\_confirm.py` — без подтверждения оценка не привязывается. `tests/test\_no\_inference\_from\_signals.py` — поиск по коду: нет ни одного места, где правило создаётся без явной команды (самопроверка п. 12).



\*\*е) Критерий готовности.\*\* Вслух: «Джарвис, правило: не открывай мне браузер без спроса», затем «покажи, что ты обо мне помнишь» — Jarvis перечисляет факты и правила одним списком с датами. Затем «забудь последнее» — правило уходит в корзину, и это видно в списке.



\*\*ж) Откат.\*\* Удалить строки из `mx\_owner\_rule` (данные, не код); выключить флаг проверки.



\*\*з) Объём.\*\* 4–6 вечеров.



\*\*и) Зависимости.\*\* После фазы 3.



\*\*к) НЕ делаем.\*\* Никакого автовывода правил из поведения. Возврат к этой идее — только на контрольной точке «+3 месяца».



\### Фаза 5. Результаты, напоминания, наблюдения



\*\*а) Цель.\*\* Всё, что Jarvis сделал, лежит понятным файлом и само убирается. Напоминания работают без единого вызова модели.



\*\*б) Шаги.\*\*



1\. `core/results.py` — файл в `\~/jarvis/results`, окно только по «покажи» (Д13); 30 дней → корзина Windows, «сохрани» = навсегда (Д34).

2\. `core/scheduler.py` — таймер и `mx\_reminder`, ноль LLM; за 15 минут + в момент + один повтор через 3 минуты; это \*\*не будильник\*\* (Д4, Д30).

3\. Миграция напоминаний из JSON в `mx\_reminder` (Р-6), старый файл сохраняется как есть.

4\. Д35: громкий ярус — семь поводов вслух; тихий ярус — файл `observations-ГГГГ-ММ-ДД.md`, читается только по запросу.

5\. Д27: повторная просьба → напомнить прежний результат и спросить «повторить?».

6\. Фаст-пасы: `config/fastpass.yaml`, у каждого тест времени ≤3 с и ноль LLM (Д15, I33).

7\. `core/spawned.py` — реестр порождённых процессов и уборка после аварии.



\*\*в) Файлы.\*\* Создать: `core/results.py`, `core/scheduler.py`, `core/spawned.py`, `config/fastpass.yaml`. Изменить: `actions/reminder.py`, `core/awareness/\_\_init\_\_.py` (тихий ярус), `core/journal.py`.



\*\*г) Реализуются.\*\* Д4, Д13, Д15, Д21, Д27, Д30, Д34, Д35, I33.



\*\*д) Тесты.\*\* `tests/test\_reminder\_zero\_llm.py` — сработавшее напоминание не вызвало модель. `tests/test\_reminder\_migration.py` — все напоминания из JSON оказались в таблице, ни одно не потеряно. `tests/test\_fastpass\_timing.py` — каждый фаст-пас укладывается в 3 секунды (тест времени, I33). `tests/test\_result\_purge\_30d.py` — файл старше 30 дней уходит в корзину, файл с `keep=1` остаётся.



\*\*е) Критерий готовности.\*\* Вслух: «Джарвис, напомни через пять минут позвонить клиенту». Через 5 минут Jarvis говорит сам, без всякого запроса, и повторяет один раз через 3 минуты, если владелец не ответил. Затем «покажи результаты» — открывается окно со списком файлов.



\*\*ж) Откат.\*\* Старый `actions/reminder.py` работает с JSON-файлом; вернуть его из прошлого zip, данные в JSON не удалялись.



\*\*з) Объём.\*\* 5–7 вечеров.



\*\*и) Зависимости.\*\* После фазы 4 (не технически, а по смыслу: сначала честность, потом удобство).



\*\*к) НЕ делаем.\*\* Не подключаем внешние календари — о встречах только со слов владельца (Д21).



\### Фаза 6. Автозапуск — строго последняя



\*\*а) Цель.\*\* Jarvis поднимается сам при включении ноутбука и не мешает загрузке. Делается \*\*только когда всё остальное работает неделю без вмешательства\*\*.



\*\*б) Шаги.\*\*



1\. `tools/install\_autostart.py` — ярлык в папке автозагрузки пользователя. \*\*Без прав администратора и без планировщика задач Windows\*\* (Д26).

2\. Отложенный старт: пауза после входа в систему, чтобы не бороться за диск.

3\. Тихий старт: ни одного окна, ни одной фразы, пока владелец не обратится.

4\. Уборка после аварии: снять зависший `\~/.jarvis/jarvis.lock`, подчистить `mx\_spawned`, задачи в `RUNNING` перевести в `FAILED('restart')`.

5\. `tools/uninstall\_autostart.py` — выключаемость одной командой.



\*\*в) Файлы.\*\* Создать: `tools/install\_autostart.py`, `tools/uninstall\_autostart.py`. Изменить: `main.py::run` (тихий режим и уборка), `core/instance\_lock.py`.



\*\*г) Реализуются.\*\* Д9, Д26, I28.



\*\*д) Тесты.\*\* `tests/test\_autostart\_no\_admin.py` — установка не обращается к правам администратора. `tests/test\_silent\_start.py` — при тихом старте нет ни речи, ни окон. `tests/test\_stale\_lock\_cleanup.py` — замок от погибшего процесса снимается, новый запуск проходит.



\*\*е) Критерий готовности.\*\* Владелец перезагружает ноутбук, ничего не нажимает, через минуту говорит: «Джарвис, ты здесь?» — и получает ответ. Ни одного окна при загрузке не появилось. Затем запускает `uninstall\_autostart` — после следующей перезагрузки Jarvis молчит, пока его не запустят вручную.



\*\*ж) Откат.\*\* Удалить ярлык из папки автозагрузки — одно действие мышкой.



\*\*з) Объём.\*\* 2 вечера.



\*\*и) Зависимости.\*\* После фазы 5 и недели спокойной работы.



\*\*к) НЕ делаем.\*\* Не ставим службу Windows, не трогаем реестр.



\### Контрольная точка «+3 месяца»



Не фаза, а свидание с самим собой. Ровно через три месяца живой работы владелец смотрит на цифры и решает \*\*один\*\* вопрос: возвращаться ли к контуру D (автовывод правил).



| Что смотрим | Где взять | Условие «да» |

| --- | --- | --- |

| Сколько правил владелец написал сам | `mx\_owner\_rule`, `state='active'` | больше 15 |

| Сколько раз он их правил | `mx\_memory\_journal`, `op='forget'` | меньше трети от числа правил |

| Доля задач с атрибуцией `unknown` | `mx\_report` | меньше 20% |

| Совпадает ли отмена задач с отсутствием правила | `mx\_task.cancel\_reason='owner\_stop'` | видна повторяющаяся картина |



Если хотя бы одно условие не выполнено — контур D \*\*не строим\*\* и переносим свидание ещё на три месяца. Почему так: автовывод правил, построенный на редких и противоречивых данных, начнёт угадывать мнение владельца — а это прямо запрещено.



Здесь же закрывается \*\*О23\*\*: после недели-двух живой работы в `mx\_meter\_call` видно фактическое распределение по `bucket`, и подбюджеты (`config/budgets.yaml`, `subbudgets.enabled: false`) можно включить с реальными числами вместо выдуманных.



\## Раздел 5. Матрица трассируемости



\### 5.1. Решения Д1–Д39



| Д | Фаза | Файлы | Тест |

| --- | --- | --- | --- |

| Д1 | — | заменено Д10, реализации не требует | строка-заглушка в `docs/INVARIANTS.md` |

| Д2 | 0, 2 | `core/feature\_flags.py`, `\~/.jarvis/settings.json` | `test\_flag\_home\_settings.py` |

| Д3 | 1б (тест на существующее) | `core/consent\_store.py`, `core/gate.py` | `test\_hitl\_timeout\_is\_refusal.py` |

| Д4 | 5 | `core/scheduler.py`, `mx\_reminder`, `actions/reminder.py` | `test\_reminder\_zero\_llm.py` |

| Д5 | 2 | `agent/orchestrator.py::delegate` | `test\_context\_digest\_by\_code.py` |

| Д6 | 2б | `core/redbutton.py`, `core/awareness/\_watchers.py` | `test\_mouse\_freeze\_ask\_30s.py` |

| Д7 | 2 | `config/agents.yaml` | `test\_agent\_allowlist.py` |

| Д8 | 0 | процесс работы, `core/store.py::backup` | `test\_backup\_before\_migrate.py` |

| Д9 | 6 | `tools/install\_autostart.py` | `test\_autostart\_no\_admin.py` |

| Д10 | — (риск принят) | — | `test\_no\_voice\_verification.py` (фиксация решения) |

| Д11 | 2 | `main.py::TOOL\_DECLARATIONS` | `test\_main\_only\_fastpass\_tools.py` |

| Д12 | 1б | `core/gate.py`, `core/sensitivity.py` | `test\_outbound\_needs\_full\_text.py` |

| Д13 | 5 | `core/results.py` | `test\_result\_file\_first.py` |

| Д14 | 2б | `core/speech\_queue.py` | `test\_initiative\_three\_reasons.py` |

| Д15 | 5 | `config/fastpass.yaml` | `test\_fastpass\_timing.py` |

| Д16 | 1б | `core/gate.py` (закрытый список) | `tests/golden/test\_golden\_dispatch.py` |

| Д17 | 2б | `core/redbutton.py::freeze\_all/cancel\_all` | `test\_stop\_three\_paths.py` |

| Д18 | 2 | `agent/orchestrator.py::supersede` | `test\_supersede\_same\_form.py` |

| Д19 | 2 | `agent/task\_queue.py`, `mx\_task.priority` | `test\_ten\_active\_limit.py` |

| Д20 | 2б | `core/speech\_queue.py`, `mx\_counter` | `test\_initiative\_cap.py` |

| Д21 | 5 | `core/scheduler.py` (нет интеграций) | `test\_no\_external\_calendar.py` |

| Д22 | 1б | `core/sensitivity.py`, `\~/.jarvis/private\_paths.txt` | `test\_sensitivity\_block.py` |

| Д23 | — (не делаем) | — | `test\_no\_guest\_mode.py` |

| Д24 | 1б | `core/gate.py`, `core/consent\_store.py` | `test\_voice\_confirm\_outbound.py` |

| Д25 | 2б | `actions/computer\_control.py`, `core/redbutton.py` | `test\_close\_unsaved.py` |

| Д26 | 0, 6 | `main.py::run`, `tools/install\_autostart.py` | `test\_autostart\_no\_admin.py` |

| Д27 | 5 | `mx\_result`, `core/results.py` | `test\_repeat\_asks\_first.py` |

| Д28 | 3 | `core/acceptance.py` (цифры из 3.3 документа) | `test\_quality\_thresholds.py` |

| Д29 | 2 | `agent/orchestrator.py`, `main.py` | `test\_context\_amount\_by\_main.py` |

| Д30 | 5 | `core/scheduler.py` | `test\_reminder\_15min\_and\_retry.py` |

| Д31 | 4 | `mx\_memory\_journal`, `memory/memory\_manager.py` | `test\_memory\_journal\_50.py` |

| Д32 | — (порядок работ) | этот план | — (процессное решение) |

| Д33 | 0.5 | `core/provider/\*`, `config/registry.yaml` | `test\_registry\_roles.py` |

| Д34 | 5 | `core/results.py`, `core/recycle.py` | `test\_result\_purge\_30d.py` |

| Д35 | 5 | `core/awareness/\_\_init\_\_.py`, `observations` | `test\_loud\_quiet\_tiers.py` |

| Д36 | 1 | `core/store.py::migrate` | `test\_migrations\_7\_18.py` |

| Д37 | 2б | `core/tray.py`, `core/status\_window.py` | `test\_no\_focus\_steal.py` |

| Д38 | 1 | `core/blackbox.py` | `test\_blackbox\_no\_secrets.py` |

| Д39 | 3, 4 | `core/acceptance.py`, `core/quality\_report.py`, `core/rules.py` | `test\_acceptance\_only\_owner\_words.py`, `test\_rule\_never\_acceptance.py` |



\### 5.2. Инварианты I11–I45



| I | Фаза | Тест |

| --- | --- | --- |

| I11 | 1б | `test\_gate\_origin\_chain.py` |

| I12 | 1б | `test\_fences\_memory.py` |

| I13 | 2 | `test\_resource\_exclusive.py` |

| I14 | 1 | `test\_report\_is\_data\_only.py` |

| I15 | 1 | `test\_queue\_restart\_failclosed.py` |

| I16 | 1 | `test\_metering\_no\_bypass.py` |

| I17 | 2 | `test\_agent\_allowlist.py` |

| I18 | 1 | `test\_trivial\_zero\_aux.py` |

| I19 | 6 (бюджет) / 1 | `test\_budget\_never\_silent.py` |

| I20 | 2б | `test\_only\_main\_speaks.py` |

| I21 | 2 | `test\_core\_knows\_no\_names.py` |

| I22 | 1б | `test\_disclosure\_through\_gate.py` |

| I23 | 1б | `test\_hitl\_restart\_is\_refusal.py` |

| I24 | 1 | `test\_atomic\_state\_write.py` |

| I25 | 1б | `test\_sensitivity\_block.py` |

| I26 | 2 | `test\_compensator\_exists.py` |

| I27 | 1б | `tests/golden/test\_golden\_dispatch.py` |

| I28 | 0 | `test\_instance\_lock.py` |

| I29 | 2б | `test\_speech\_single.py` |

| I30 | 2б | `test\_initiative\_cap.py` |

| I31 | 1б (переопределён Д24) | `test\_voice\_confirm\_outbound.py` |

| I32 | 1б | `test\_reversible\_no\_confirm.py` |

| I33 | 5 | `test\_fastpass\_timing.py` |

| I34 | 1б | `tests/golden/test\_golden\_dispatch.py` |

| I35 | 1б | `test\_memory\_write\_spoken.py` |

| I36 | 2 | `test\_context\_amount\_by\_main.py` |

| I37 | 0.5 | `test\_registry\_roles.py` |

| I38 | 1 | `test\_migration\_abort.py` |

| I39 | 2б | `test\_stop\_three\_paths.py` |

| I40 | 1 | `test\_blackbox\_no\_secrets.py`, `test\_replay\_offline.py` |

| I41 | 3 | `test\_rule\_never\_acceptance.py` |

| I42 | 4 | `test\_behavior\_change\_explicit.py` |

| I43 | 3 | `test\_acceptance\_zero\_llm.py` |

| I44 | 4 | `test\_rating\_needs\_confirm.py` |

| I45 | 1, 3 | `test\_head\_no\_freetext.py` |



\### 5.3. Реестр 27 пробелов (Часть XII)



\*\*Закрываются в фазах — 24 из 27:\*\*



| Фаза | Номера ДЫР |

| --- | --- |

| 0 | 11 (секреты), 21 (единственный экземпляр) |

| 0.5 | 23 частично (квота моделей), 25 (два SDK) |

| 1 | 1, 4, 6, 7, 16 |

| 1б | 17, 19, 20 |

| 2 | 2, 3, 5, 9 |

| 2б | 10, 12, 13 |

| 3 | 22, 26 |

| 4 | 15, 27 |

| 5 | 24 |

| 6 | 21 (уборка после аварии) |



\*\*Приняты осознанно — 3 штуки:\*\* ДЫРА-8 (голос не проверяется, Д10/Д24), ДЫРА-14 (подтверждение голосом от людей в комнате), ДЫРА-18 (деление дневной квоты, О23 — отложено до контрольной точки).



\## Раздел 6. Бюджеты и их защита



\### 6.1. Где стоят проверки



Одно место: `core/metering.py::reserve` вызывается \*\*до\*\* любого обращения к модели, `commit` — сразу после. Обёртка стоит внутри `core/aux\_model.py` и внутри адаптеров `core/provider/\*`. Почему так: если разрешить второе место, через полгода никто не сможет ответить, куда ушла квота.



\### 6.2. Как считается локально



| Лимит | Как считаем | Где храним |

| --- | --- | --- |

| RPD | `COUNT(\*)` по `mx\_meter\_call` за текущий `quota\_day` и роль | SQLite |

| RPM | скользящее окно 60 с в памяти процесса | ОЗУ (перезапуск сбрасывает — это допустимо) |

| TPM | сумма `in\_tokens + out\_tokens` за 60 с; для Gemma предел 16 000 → предобрезка до \~12 000 знаков | ОЗУ |



\*\*Сброс в 11:00 МСК\*\* живёт ровно в одной функции `metering.quota\_day(now\_utc)`. Почему так: квотные сутки Google идут по Pacific; если перевод времени размазать по коду, при переводе часов в США счётчики сойдутся с ума один раз в полгода, и понять это будет невозможно.



\### 6.3. Лестница деградации



| Режим | Когда | Что говорит вслух |

| --- | --- | --- |

| 0 | всё есть | ничего особенного |

| 1 | `aux\_light` израсходован на 80% | «сделаю попроще, сэр» |

| 2 | только Gemma и фаст-пасы | «остались только простые задачи» |

| 3 | всё исчерпано, голос жив (Live безлимитен по RPD) | «до 11 утра больше не могу, могу только говорить» |

| M | свободного ОЗУ меньше порога | «мало памяти, новые задачи подождут» |

| N | сети нет | уведомление Windows + звук (голос тоже недоступен) |



Главное правило I19: \*\*исчерпание никогда не молчаливое.\*\* Нет ни одного пути, где задача тихо умирает по квоте.



\### 6.4. Подбюджеты (заготовка под О23, выключена)



`config/budgets.yaml`: `subbudgets.enabled: false`, доли `dialog 0.40 / task 0.40 / background 0.15 / reserve 0.05`. Поле `bucket` в `mx\_meter\_call` заполняется \*\*с самого начала\*\*, даже пока деление выключено. Почему: без истории по `bucket` владелец будет выбирать доли наугад.



\### 6.5. Предобрезка, retry и аварийный выключатель



1\. \*\*Предобрезка\*\* — внутри `reserve`: если оценка входа превышает лимит роли, текст режется до отправки и в отчёт попадает факт `truncated`.

2\. \*\*Глобальный retry-бюджет\*\* — не более 3 повторов на задачу и не более 20 на сутки (`mx\_counter`).

3\. \*\*Circuit breaker\*\* — три подряд отказа одной роли → роль отключается на 10 минут, владелец слышит об этом один раз (`core/provider\_health.py`).



\### 6.6. Норма расхода как тест



Тривиальный запрос — 0 aux-вызовов (I18), типовая задача — 1–2, сложная — не более 4. Это проверяется `tests/test\_call\_budget\_norms.py` на моках и стоит ноль токенов.



\## Раздел 7. Безопасность как сквозная линия



\### 7.1. Что меняется в `gate.py` и `security.py`



| Что | Было | Станет |

| --- | --- | --- |

| Сигнатура | `dispatch(tool, params, \*, mode, screen\_control)` | то же + `ctx: TaskCtx |

| Аудит | `ts, tool, action, mode, verdict, risk, policy, reason, param\_keys` |   • `task\_id`, `agent\_role`, `origin\_chain`, `depth` |

| Отказы | по инструменту и риску |   • по allowlist агента и по заборам |

| `security.py` | классификация риска действия |   • класс «наружу» с обязательным предъявлением текста |



\### 7.2. Как устроен `origin\_chain`



Это просто список из двух–трёх слов: `("owner", "main")` или `("owner", "main", "file\_clerk")`. Попросту — «кто кого попросил», как подпись на приказе. Цепочка собирается кодом в `TaskCtx` в момент создания задачи и \*\*не может быть изменена тем, кто её передаёт\*\* — только дополнена в конец оркестратором.



Почему это важно именно сейчас, а не потом: когда через месяц в аудите обнаружится удалённая папка, единственный вопрос будет «кто это сделал — я, главный или агент?». Без цепочки ответа не будет никогда, потому что задним числом в старые записи её не впишешь.



\### 7.3. Заборы (`core/fences.py`)



| Что за забором | Кому нельзя | Почему |

| --- | --- | --- |

| Память (`memory\_fact`) | любому агенту, любому фону | запись в память меняет будущие ответы навсегда |

| Личность (`personality.json`) | всем, кроме явной команды владельца | характер не должен меняться сам |

| Правила (`mx\_owner\_rule`) | всем, кроме голоса владельца | контур D запрещён |

| Журнал гейта (`logs/gate-audit.jsonl`) | всем на запись, кроме самого гейта | журнал, который можно править, не журнал |

| Vision / скриншоты | агентам без явного разрешения в задаче | на экране могут быть банк и госуслуги (Д22) |



\### 7.4. Фильтр на запись в память



Три условия одновременно: (1) источник — главный; (2) есть явная команда владельца («запомни», «забудь»); (3) факт проговорён вслух и отменяем одной командой (I35). Запись без любого из трёх — отказ. Проверка стоит в `memory/memory\_manager.py` и дублируется в `core/fences.py`. Почему дважды: память — единственное место, где ошибка не видна сразу и вылезает через месяц.



\### 7.5. Классификатор чувствительности (`core/sensitivity.py`)



Стоит \*\*перед\*\* любой отправкой наружу и работает \*\*без модели\*\* — только правила и списки: шаблоны ключей и паролей, содержимое буфера обмена, заголовки окон банков и госуслуг, данные аккаунтов, переписки, пути из `\~/.jarvis/private\_paths.txt` (Д22). Почему без модели: проверка на утечку не имеет права зависеть от квоты и сети.



Точечное разрешение возможно: владелец говорит «это можно отправить» → разовый талон в `consent\_ticket` с отпечатком именно этого текста, а не «разрешить всё навсегда».



\### 7.6. Заглушки секретов в чёрном ящике



Одна функция `blackbox.\_redact(text)` вызывается внутри `write` — миновать её невозможно, потому что другого входа в запись нет. Заменяет: строки вида `AIza…`, значения из `\~/.jarvis/secrets.json`, содержимое полей с именами `password`, `token`, `key`, `secret`. В записи остаётся `<redacted:api\_key>` — видно, что здесь был секрет, но самого секрета нет. Воспроизведение (`tools/replay\_session.py`) работает оффлайн за \*\*ноль вызовов API\*\* (I40).



\## Раздел 8. Тестовая стратегия



\### 8.1. Как остаться на 784+ зелёных



Главный приём один: \*\*все новые параметры добавляются только со значением по умолчанию, сохраняющим старое поведение.\*\* `ctx=None` — именно это: 747 существующих тестовых функций вызывают `dispatch` без `ctx` и продолжают видеть тот же результат.



Четыре правила:



1\. Новое поведение — всегда за флагом `JARVIS\_AGENTS`, по умолчанию выключенным.

2\. После каждого вечера работы — `python -m pytest -q` из корня (Р-17). Цифра обязана только расти.

3\. Если старый тест всё-таки надо поменять — это отдельный шаг в плане фазы с записью в `docs/issues/`, а не походу.

4\. `tests/golden/test\_golden\_dispatch.py` — merge-гейт: расширение списка действий без подтверждения требует нового golden-кейса (I34).



\### 8.2. Тесты за ноль токенов



| Тип | Как устроен |

| --- | --- |

| Моки модели | `core/provider/\*` подменяются заглушкой, отдающей записанный ответ |

| Проверки по исходникам | поиск по тексту файлов (ключ в проекте, имена моделей в коде, `shell=True`) |

| Проверки базы | временная база через `JARVIS\_STATE\_DIR` — реальная `jarvis.db` не трогается |



\### 8.3. Воспроизведение сессии как тест



`tools/replay\_session.py` берёт `rec\_id` из чёрного ящика и прогоняет ту же последовательность оффлайн: ответы модели берутся из записи, действия не выполняются, а сверяются с журналом гейта. Любая жалоба «он сделал не то» превращается в постоянный тест одной командой. Почему это лучше обычных логов: лог надо читать глазами, а воспроизведение само говорит «ломается вот здесь».



\### 8.4. Канарейки на тихую деградацию модели



Три коротких проверки, которые владелец запускает вручную раз в неделю (три реальных вызова из 500 — не страшно):



1\. Классификация: три заранее известные фразы → ожидаемые три типа задач.

2\. Извлечение факта: фраза с датой → дата распознана верно.

3\. Формат ответа: модель вернула валидный JSON по схеме вызова инструмента.



Если любая из трёх падает — проблема не в коде, а в модели или её версии. Без канареек это выглядит как «Джарвис поглупел» и владелец неделю ищет ошибку у себя.



\### 8.5. Тест бюджета памяти



`tests/test\_memory\_budget.py`: запускает ядро без звука и без окон, создаёт 10 задач-заглушек, смотрит `psutil` на собственный процесс и требует \*\*не больше 500 МБ\*\*. Порог — константа в одном месте, чтобы владелец мог её править. \*\*\[ТРЕБУЕТ ПРОВЕРКИ В КОДЕ]\*\* ПРОВЕРЕНО в ПАТЧЕ 8 (Ф13): `psutil>=5.9.0` уже объявлен в `pyproject.toml`, дополнительная библиотека не нужна, оговорка про `tracemalloc` снята.



\### 8.6. Тесты времени для фаст-пасов



Каждый пункт `config/fastpass.yaml` получает отдельный тест: вызов с заглушенной файловой системой должен уложиться в 3 секунды и не сделать ни одного вызова модели. Порог берётся с запасом 2× на случай, когда владелец параллельно играет.



\## Раздел 9. Порядок работ владельца по дням



| День | Что делать физически | Что проверить руками после | Где остановиться |

| --- | --- | --- | --- |

| 1 | Отозвать ключ, выпустить новый, положить в `\~/.jarvis/secrets.json` | Jarvis запускается и отвечает голосом | Не идти дальше, пока старый ключ не отозван |

| 2 | Запустить 5 замеров из 0.2 и вписать цифры в этот план | 5 цифр записаны | Если ОЗУ >420 МБ — сначала обсудить, потом фаза 2 |

| 3–4 | Фаза 0 целиком | Критерий готовности фазы 0 (удалить папку, распаковать, запустить) | Флаг не сохранился → не идти дальше |

| 5–7 | Фаза 0.5 | 10 переводов подряд без отказа | Отказ по квоте → проверить `registry.yaml` |

| 8–19 | Фаза 1 (самая долгая) | После каждого вечера: `python -m pytest -q` | Цифра тестов упала → стоп, разбираться сегодня же |

| 20–25 | Фаза 1б | Последняя строка `logs/gate-audit.jsonl` содержит `origin\_chain` | Нет цепочки → не начинать агентов |

| 26–33 | Фаза 2 | Критерий фазы 2 + Диспетчер задач ≤500 МБ | Память выше → выключить флаг и думать |

| 34–40 | Фаза 2б | «Стоп» тремя способами работает в игре без потери фокуса | Окно украло фокус → чинить сразу |

| 41–45 | Фаза 3 | Задача с невыполнимым условием даёт `partial` | Сказал «готово» при невыполненном — стоп |

| 46–51 | Фаза 4 | «Покажи, что помнишь» — единый список с датами | Правило появилось само — немедленно стоп |

| 52–58 | Фаза 5 | Напоминание сработало без сети | Потерялось старое напоминание — вернуть из JSON |

| 59–65 | \*\*Неделя без кода\*\*: только пользоваться | Сколько раз вмешивался руками | Больше трёх раз — фазу 6 не начинать |

| 66–67 | Фаза 6 (автозапуск) | Перезагрузка → молчит, отвечает на голос | Появилось окно при старте — снять ярлык |



\*\*Правило одного вечера:\*\* в конце каждого вечера сделать zip всей папки с именем вида `jarvis\_2026-08-04\_faza1\_shag4.zip`. Это заменяет git полностью для наших целей: единственное, что нужно — вернуться на вечер назад.



\## Раздел 10. Реестр рисков плана



| № | Риск | Признак проблемы | Реакция |

| --- | --- | --- | --- |

| Р1 | Базовое ОЗУ уже близко к 500 МБ | замер дня 2 показал >420 МБ | сократить число одновременных агентов до одного и поднять порог `memwatch` |

| Р2 | Фаза 1 скучная, мотивация падаёт | пропущено три вечера подряд | разбить фазу 1 на восемь отдельных вечеров с отдельными галочками |

| Р3 | После правки гейта падают старые тесты | `pytest` меньше 784 | откатить правку, сделать `ctx` точно необязательным |

| Р4 | Миграция сломала базу | Jarvis говорит «не могу запустить базу» | вернуть копию от `backup()`, переименовав файл |

| Р5 | Записи чёрного ящика раздувают базу | `jarvis.db` больше 300 МБ | уменьшить срок тела с 7 дней до 3, шапки оставить |

| Р6 | Модели `3.5 Flash Lite` нет на ключе владельца | ошибка 404 или «model not found» | вернуться на `3.1 Flash Lite` (те же 15/500), правка одной строки `registry.yaml` |

| Р7 | Агенты встают в очередь за мышью | задачи висят в `WAITING` часами | сделать Оператора ПК единственным владельцем мыши и короткими окнами захвата |

| Р8 | Очередь речи даёт заметную задержку ответа | ответ владельцу позже 1 с | приоритет ответа владельцу сделать вытесняющим, а не очередным |

| Р9 | Владелец перепутал архивы и потерял вечер работы | в папке нет свежих правок | жёсткое имя zip с датой и номером шага (Раздел 9) |

| Р10 | В чёрный ящик попал секрет нового вида | греп по `mx\_bb\_body` нашёл похожее на ключ | добавить шаблон в `\_redact` и очистить тело за 7 дней вручную |



\### Три места, где владелец с наибольшей вероятностью застрянет



1\. \*\*Фаза 1, миграции и метеринг.\*\* Две недели работы — и никакого видимого результата. Соблазн «сделаю агента сейчас, а task\_id потом» будет огромным. \*\*Что делать:\*\* повесить на видное место список из шести пунктов «нельзя добавить задним числом» и вычёркивать их по одному.

2\. \*\*Правка гейта и страх сломать 784 теста.\*\* Гейт — единственная дверь, его страшно трогать. \*\*Что делать:\*\* добавлять только необязательные параметры со значением `None` и после каждого шага гонять тесты. Первый шаг — только параметр, без единого изменения логики; второй — только поля аудита.

3\. \*\*Очередь речи и ощущение «стал медленнее».\*\* Когда вся речь пошла через очередь, любая лишняя доля секунды станет заметной, и будет соблазн вернуть «как было». \*\*Что делать:\*\* сразу сделать ответ владельцу вытесняющим (прерывает фоновую реплику на полуслове), а не ставить его в очередь.



\## Раздел 11. Чего план сознательно НЕ покрывает



Чтобы не казалось, что забыто:



1\. \*\*Проверка голоса\*\* (кто именно говорит). Отказ осознанный (Д10), риск принят.

2\. \*\*Гостевой режим\*\* (Д23) — не делаем вообще.

3\. \*\*Автовывод правил\*\* (контур D) — только после контрольной точки и только по четырём проверяемым условиям.

4\. \*\*Внешние календари и почта\*\* (Д21) — о встречах только со слов владельца.

5\. \*\*Деление дневной квоты\*\* (О23) — только заготовка, выключенная по умолчанию.

6\. \*\*Дизайны A/B/C\*\* (Д32) — после всего этого, отдельным документом.

7\. \*\*Третий и дальше агент\*\* — только после того, как два первых проработают месяц.

8\. \*\*Глубина рекурсии больше двух уровней\*\* — запрещено по устройству, не по настройке.

9\. \*\*Перенос на другую машину или ОС\*\* — план рассчитан только на этот ноутбук и Windows.

10\. \*\*Сетевой доступ к Jarvis извне\*\* (телефон, веб-интерфейс) — вне области этого плана.

11\. \*\*Дообучение и локальные модели\*\* — запрещено жёстко (железо).

12\. \*\*Настоящая ротация ключей по расписанию\*\* — в плане только разовая ротация и вынос секретов.



\---



\## Подтверждение самопроверки (12 пунктов)



| № | Пункт | Статус | Где в плане |

| --- | --- | --- | --- |

| 1 | Каждое из 39 решений имеет строку в матрице | ✅ | 5.1 (39 строк) |

| 2 | Каждый инвариант I11–I45 имеет тест | ✅ | 5.2 (35 строк) |

| 3 | Все пункты «нельзя задним числом» в фазе 1 или раньше | ✅ | Фаза 1, пункт г) |

| 4 | Ни один шаг не требует git | ✅ | откаты — zip и `store.backup()` |

| 5 | Ни одного локального LLM и тяжёлого фреймворка | ✅ | только Gemini API + свои gate/bus/queue |

| 6 | Ни один шаг не превышает 500 МБ | ✅ (при базовом замере из 0.2) | 8.5 тест + `core/memwatch.py` |

| 7 | 784 зелёных не ломаются без явного плана | ✅ | 8.1 (`ctx=None` по умолчанию) |

| 8 | У каждой фазы есть критерий готовности и откат | ✅ | пункты е) и ж) во всех 10 фазах |

| 9 | Ротация утёкшего ключа — первым делом | ✅ | Фаза 0, шаг 1; Раздел 9, день 1 |

| 10 | Автозапуск — последним делом | ✅ | Фаза 6 |

| 11 | Дыры Части XII размещены или приняты | ✅ | 5.3 (24 в фазах, 3 приняты) |

| 12 | Ни одного механизма, угадывающего мнение владельца | ✅ | категория `unknown`, `test\_no\_inference\_from\_signals.py`, запрет контура D |



\---



\## Пять вопросов, без ответа на которые план придётся переделывать



\*\*Вопрос 1. Сколько памяти Jarvis ест СЕЙЧАС?\*\*



Запустите его, поработайте 5 минут, откройте Диспетчер задач и назовите цифру у `python.exe`.



\*Пример:\* «180 МБ» или «430 МБ».



\*Что сломается при неверном ответе:\* если уже 430 МБ, то второй агент в принципе не влезает, и всю фазу 2 надо переписать под одного агента с жёсткой очередью.



\*\*Вопрос 2. Какая из моделей реально доступна на вашем ключе — `3.5 Flash Lite`, `3.1 Flash Lite`, и есть ли `Gemma 4`?\*\*



Проверьте в Google AI Studio список моделей.



\*Пример:\* «есть 3.1, нет 3.5, Gemma есть».



\*Что сломается:\* вся фаза 0.5 построена на том, что есть модель с 500 запросами в сутки. Если на ключе только модели по 20 — агенты невозможны в принципе, и план меняется целиком (либо на вашу систему с многими аккаунтами, либо на платный ключ).



\*\*Вопрос 3. Сколько дней можно хранить ваши дословные фразы в чёрном ящике?\*\*



Сейчас в плане 7 дней (Д38). В теле записи будет всё, что вы говорили вслух.



\*Пример ответа:\* «7 дней ок» / «хватит 2 дней» / «пусть будет 30».



\*Что сломается:\* если потом решите «вообще не надо хранить мои слова», воспроизведение сессии как тест (8.3) перестанет работать, а на нём держится весь разбор жалоб «он сделал не то».



\*\*Вопрос 4. Можно менять сигнатуру главной функции безопасности `gate.dispatch` уже сейчас?\*\*



Попросту: разрешаете добавить ей один необязательный аргумент в фазе 1б, или хотите сначала увидеть отдельный маленький шаг с тестами только на это.



\*Пример:\* «да, меняйте сразу» / «только отдельным вечером и с отдельным zip».



\*Что сломается:\* если менять нельзя, то `origin\_chain` придётся протаскивать через глобальную переменную — это работает хуже и ломается на потоках, а агенты — именно потоки.



\*\*Вопрос 5. Готовы ли вы к 8–12 вечерам фазы 1 без видимого результата?\*\*



Фаза 1 не даёт ничего нового в голосе — только строки в базе.



\*Пример:\* «да, готов» / «нет, нужен видимый результат каждые два вечера».



\*Что сломается:\* если ответ «нет», порядок фаз надо пересобрать: делать узкую вертикаль «один агент на одном типе задач со всем каркасом сразу» вместо широкого фундамента — это медленнее в сумме, но выдерживаемо психологически.



\---



\# ПАТЧ 7 (виток 13). Проверка по коду, решения Д40–Д43 и двенадцать несущих решений



<aside>

🔧



Этот раздел добавлен после независимого ревизорского разбора плана (60 находок по 12 линзам) и второго прохода на 20 осей (74 новые дыры). Всё, что ниже, имеет приоритет над более ранними разделами плана при расхождении.



</aside>



\## 13.1. Семь фактов, проверенных в коде (правки к разделу 0)



| № | Факт | Что меняет |

| --- | --- | --- |

| Ф1 | `core/store.py:384`: `sqlite3.connect(path, isolation\_level=None, check\_same\_thread=False)` | Падения при работе из потоков не будет — будет тихая порча логической атомарности: автокоммит + ручные BEGIN + одно соединение на 10 потоков = транзакции наслаиваются. Решается Д41 |

| Ф2 | `JOURNAL\_MAX = 8` — это размер горячего срока для промпта и списка отмены, а НЕ потолок хранения. Таблица `action\_journal` не обрезается | Прошлая оценка «истории нет» неверна. Реальные дыры: журнал растёт вечно (нет purge) и к нему нет запросов кроме «последние N по id» — нет `by\_task`, `by\_session`, `by\_day`, `by\_path` |

| Ф3 | `JARVIS\_MIGRATIONS` содержит версии 1–6. Есть ВТОРОЙ список `HISTORY\_MIGRATIONS` для ДРУГОЙ базы, тоже с версии 1 | Номера «миграции 40–50» из раздела 2 неверны: следующая свободная — 7. И в плане не учтена вторая база со своей `user\_version`. См. 13.4 |

| Ф4 | `gate.py:160`: `session\_id = \_rt.get\_session\_id()`; `consent\_store` сверяет сессию только если оба значения не NULL | Фоновая задача получает `session\_id=None`, а талон с NULL подходит к ЛЮБОЙ сессии. Дыра не «фон не сможет спросить», а «старое разрешение закроет новый вопрос». Решается Д44 (см. 13.3, Р4) |

| Ф5 | `core/bus.py: EVENTS` — 12 событий, все про файлы и отмену. Про задачи, агентов, квоту, речь, HITL — ни одного | Д35 (события) — новый домен с нуля, а не расширение. Дубликаты имён невозможны (это словарь) — тест на уникальность не нужен |

| Ф6 | Утёкший ключ `AIzaSy…` лежит в трёх местах: `ARCHITECTURE.md`, `config/api\_keys.json`, `config/secrets.json` | Ротация — день 1. Тест ищет не «расположение секретов», а сам литерал по всему дереву |

| Ф7 | `agent/task\_queue.py`: `max\_concurrent=1`, потоки `daemon=True` (две точки) | `daemon=True` = при выходе процесса потоки обрубаются на полуслове без компенсации саг. Это прямое нарушение «молча не убивать никогда». Решается Р5 |



\## 13.2. Решения владельца Д40–Д43



\### Д40. Карантин исходящего: что уходит в облако и кто решает



Локальные модели запрещены (Д1), значит понимание речи делает Gemini на серверах Google. На бесплатном тарифе входные данные могут использоваться для улучшения моделей и просматриваться проверяющими. Отправленное не отзывается — поэтому политика фиксируется до первой фоновой задачи.



\*\*По умолчанию наружу уходит:\*\* фразы владельца, имена файлов, окон и приложений. Без содержимого.



\*\*Содержимое файла и скриншот\*\* уходят только по прямой просьбе владельца («перескажи», «что на экране»). Фоновая задача содержимое не отправляет никогда по своей инициативе.



\*\*Детектор конфиденциального предупреждает, но не решает.\*\* Он никогда не отказывает молча и никогда не решает за владельца: «Сэр, в файле похоже на паспортные данные. Отправлять в облако?» — вердикт владельца единственный пропуск. При «нет» Jarvis делает то, что может без облака, и говорит, чего именно не сможет.



\*\*Детектор обязан быть локальным, на правилах, без единого вызова модели\*\* — иначе, чтобы спросить у модели «это конфиденциально?», пришлось бы уже отправить данные наружу. Признаки: номера документов и карт (с контрольной суммой), IBAN, СНИЛС, ИНН, слова-маркеры (паспорт, пароль, seed, CVV, выписка, диагноз); имя файла и папка (паспорт, скан, банк, мед, договор); для скриншота — заголовок окна и адрес сайта, не содержимое картинки. \*\*При сомнении — спрашивать:\*\* «спросил зря» стоит три секунды, «не спросил» необратимо.



\*\*Журнал исходящего\*\* `mx\_outbound`: дата, роль, модель, категория, размер, вердикт владельца. Без содержимого.



\### Д41. Одна касса: запись в базу



Все задачи пишут в базу через один поток-писатель, по очереди. Чтение — своё соединение на поток, только чтение. Ошибка становится невозможной, а не маловероятной: альтернатива («каждый пишет сам, но аккуратно») требует, чтобы ни один из десятков будущих модулей не забыл про транзакцию, и цена забывчивости — испорченная история отмены, обнаруженная через неделю. Задержка в худшем случае около 10 мс — на требовании «1–3 секунды» не видна, тем более что фаст-пасы в базу почти не пишут.



\### Д42. Вопросы-подтверждения: группировка и потолок «в подряд»



Поток вопросов опаснее их отсутствия: на четвёртом однотипном вопросе владелец жмёт «да» не читая, и защита превращается в театр.



1\. Однотипные операции — \*\*один вопрос на все\*\*: «шесть файлов уже существуют. Перезаписать все шесть? Список показать?»

2\. Разных вопросов подряд — \*\*не больше двух\*\*. Остальное не делается и складывается списком в `\~/jarvis/results`; окно открывается только по просьбе (Д11).



\### Д43. Бюджет внимания: «столько, сколько нужно, но только когда необходимо» — в виде правил



Владелец сформулировал требование смыслом, а не числом. Ниже — перевод в правила, которые различает код. Разобраны сценарии дня: работа в наушниках, созвон с клиентом, игра в полный экран, отход от ноутбука, ночь, возвращение после сна ноутбука, день с десятью фоновыми задачами.



\*\*Три уровня, потолок только у среднего.\*\*



| Уровень | Что сюда попадает | Правило |

| --- | --- | --- |

| Срочное | встреча через 10 минут; истекает срок; опасное действие требует слова владельца; задача встала и без владельца не продолжится; исчерпание квоты; мало места на диске | \*\*Потолка нет.\*\* Говорит всегда, даже если это тридцатое обращение за день. Пропущенная встреча дороже раздражения |

| Полезное | «готово»; «нашёл»; «предлагаю сделать X»; отчёт по фоновой задаче | Не чаще \*\*одного раза в 15 минут\*\*. Всё, что накопилось внутри окна, объединяется в одну реплику: «готовы два дела и есть одно предложение» |

| Мелочь | «файл записан»; «квота 60%»; «задача стартовала»; «ключ сменился» | Голосом \*\*никогда\*\*. Только тихая строка в файл статуса и в окно, если владелец его сам открыл |



\*\*Правило тишины по обстановке.\*\* Полный экран (игра, видео, презентация), занятый другим приложением микрофон (созвон), заблокированный экран → голос не используется вообще. Срочное превращается в уведомление и \*\*повторяется голосом при возвращении\*\*, а не теряется. Мелочь и полезное — просто ждут.



\*\*Правило залпа.\*\* После сна или выключения ноутбука всё просроченное сжимается в одну реплику: «пока меня не было, было восемь напоминаний, самое важное — встреча в 15:00». Никогда не восемь реплик подряд.



\*\*Правило самонастройки.\*\* Раз в неделю, если «полезных» реплик было больше 15 в день, Jarvis сам спрашивает: «Сэр, я обращался к вам часто. Сделать меня тише?» Порог меняется одним словом и живёт в `\~/.jarvis/settings.json` — не в папке проекта, иначе распаковка zip его затрёт (урок из кода).



\*\*Почему выбран этот вариант, а не жёсткий суточный потолок.\*\* Любое фиксированное число («10 в сутки») ломается в двух противоположных сценариях: в день с четырьмя дедлайнами оно заставляет промолчать о важном, а в спокойный день разрешает 10 ненужных реплик, потому что «квота не израсходована». Ограничение по \*\*частоте\*\* для среднего уровня и \*\*отсутствие\*\* ограничения для срочного дают ровно то, что просил владелец: обращается столько, сколько нужно, но только когда действительно нужно.



\*\*Отменяет:\*\* ранний вариант «10 инициативных реплик в сутки» (Д12) в части жёсткого суточного числа. Сохраняется требование Д12: значение порога владелец может изменить сам, не залезая в код.



\## 13.3. Двенадцать несущих решений (Р1–Р12)



Не заплатки на отдельные дыры, а двенадцать конструкций, каждая из которых закрывает целую группу.



\### Р1. Карантин исходящего — вторая дверь



Гейт защищает компьютер от модели. Карантин защищает владельца от облака. Новый модуль `core/outbound.py`: ни один вызов модели не идёт напрямую. Разбор посылки на категории (фраза владельца · метаданные · содержимое файла · изображение экрана · текст из интернета) → политика Д40 на каждую категорию → единая редакция секретов на всю систему → запись в `mx\_outbound`. Сюда же естественно ложится выдача ключа (Р12) и выбор «прямо в Google или через прокси».



Закрывает: Х-L1…L4, Х-F3, Х-P2, Х-H2, Х-I2, Х-I5, L6-4, Х-B4. Цена 1,5 вечера, фаза 0.5. Тесты: `test\_no\_outbound\_without\_carantine` (ни один модуль не импортирует SDK напрямую), `test\_file\_content\_needs\_policy`, `test\_redaction\_is\_single\_point`, `test\_outbound\_journal\_has\_no\_payload`.



\### Р2. Один сквозной идентификатор



Сейчас четыре номерка (`session\_id`, `correlation\_id`, `saga\_id`, `ticket`), план добавлял ещё два. Формат: `run:<id>/task:<id>/step:<n>` — живёт в `TaskCtx`, проносится через гейт и записывается в \*\*уже существующее поле `correlation\_id`\*\* таблицы `action\_journal` (`journal.py:83-86`). Значит атрибуция не требует новой миграции и стоит полвечера, а не отдельной фазы. Плюс `run\_id` — номер запуска.



Закрывает: Х-B1, Х-B5, L6-2, L12-5, L8-2, L8-3. Фаза 0.5. Тесты: `test\_ctx\_reaches\_journal`, `test\_no\_action\_without\_correlation`.



\### Р3. Одна касса (техническое воплощение Д41)



Поток-писатель с очередью; все записи через `store.write(op)`; чтения — своё соединение на поток, только чтение; `PRAGMA quick\_check` раз в сутки при старте. Закрывает: Ф1, L3-1, L3-6, L3-7, Х-A5, частично Х-A4. Фаза 0.5, 1 вечер. Тесты: `test\_single\_writer`, `test\_10\_threads\_no\_interleaved\_tx`, `test\_saga\_atomic\_under\_load`.



\### Р4. Фоновая задача — гость в доме (Д44)



1\. У каждой фоновой задачи своя сессия `bg:<task\_id>`.

2\. Талон с пустой сессией запрещён кодом и тестом.

3\. Фоновая сессия не наследует разрешения диалога: разрешение, выданное в разговоре, действует только в разговоре.



Закрывает: Х-B2, Х-B3, L3-2, частично L10-1. Фаза 1. Тесты: `test\_no\_null\_session\_ticket`, `test\_bg\_does\_not\_inherit\_consent`, `test\_bg\_ticket\_needs\_live\_owner`.



\### Р5. Корректное завершение — техническое содержание «молча не убивать» (Д31)



`core/shutdown.py`, четыре шага всегда одинаковые: перестать принимать новое → дать текущим шагам до 15 секунд → незакрытые саги компенсировать → поставить метку «завершились нормально» и только потом закрыть базу. Потоки больше не `daemon`. Подписка на четыре события: закрытие окна · красная кнопка · Windows уходит на перезагрузку · ноутбук засыпает. Сон становится событием «мы спали X минут», на которое подписаны планировщик, метеринг и талоны. Если метки чистого выхода нет — при старте одна фраза о падении.



Закрывает: Х-C1…C4, Ф7, L3-4, L4-2, L4-5, L4-6, L10-6. Фаза 2а, 1,5 вечера. Тесты: `test\_no\_daemon\_threads`, `test\_shutdown\_compensates\_sagas`, `test\_clean\_exit\_marker`, `test\_sleep\_event\_collapses\_reminders`.



\### Р6. Одна версия состояния (замена git)



1\. `\~/.jarvis/STATE.json`: версия кода, версия каждой из двух баз, версия настроек, версия напоминаний.

2\. `BUILD.txt` внутри проекта, пишет \*\*код\*\*, а не владелец: дата, фаза, шаг, версии схем, число тестов.

3\. `tools/rollback\_state.py --to <версия>` — восстанавливает \*\*обе\*\* базы и приводит настройки к нужной версии.

4\. Бэкапы: 3 последних + один фазовый перед каждой фазой.

5\. Перенумерация миграций — см. 13.4.



Закрывает: Х-A1…A5, Х-D5, Х-J5, L5-1, L5-2, L5-6, L9-3, L1-9. Фаза 0, 1,5 вечера. Тесты: `test\_state\_version\_covers\_all\_stores`, `test\_rollback\_restores\_both\_dbs`, `test\_build\_txt\_written\_by\_code`.



\### Р7. Заборы по сущностям, а не по путям



Забор — это сущность: путь · окно · приложение · адрес сайта · область экрана. Один вопрос «мне можно смотреть на это?» задаётся из всех четырёх каналов: файлы, экран, окна, интернет. Плюс нормализация пути в одном месте: регистр (Windows не различает, сравнение строк различает — забор просто не срабатывает), длинные пути (>260), кириллица, №, пробелы.



Закрывает: Х-P1, Х-E2, Х-E4, Х-E5, L10-5. Нормализация — фаза 0 (полчаса, это безопасность), остальное фаза 3. Тесты: `test\_fence\_case\_insensitive`, `test\_fence\_blocks\_screen\_too`, `test\_long\_path`, `test\_cyrillic\_path`.



\### Р8. Бюджет внимания (техническое воплощение Д43 и Д42)



`core/attention.py`: три уровня из Д43, четыре канала вывода (тихо в файл · уведомление · голос · окно), выбор канала по обстановке, категории вопросов (крупное необратимое · мелкое необратимое · наружу · установка · командная строка), группировка однотипных вопросов, потолок двух разных подряд, состояние «помолчи два часа».



Закрывает: Х-R1…R4, Х-O3, Х-G5, L10-2, L10-9. Фаза 3, 1,5 вечера. Тесты: `test\_attention\_single\_budget`, `test\_reminders\_exempt`, `test\_no\_voice\_on\_locked\_screen`, `test\_max\_consecutive\_questions`, `test\_grouped\_question`.



\### Р9. Минимально полезный Jarvis и оффлайн-ядро (решение владельца)



До начала фазы 1 собрать то, что работает \*\*вообще без облака\*\* и уже полезно (выбрано владельцем целиком): напоминания · открыть файл/папку/приложение · найти файл · управление окнами и громкостью голосом · отмена последнего действия · «что ты делал» · записать заметку голосом в файл · сказать время/статус/остаток квоты. Правило: режим 3 (квота исчерпана) должен жить бесконечно, а не быть аварийным.



Новая \*\*фаза 0.7\*\* (1 вечер). Закрывает: Х-T1, Х-T3, Х-M1, Х-M2, L12-8, L9-5. Тест: `test\_offline\_core\_no\_network` — всё ядро проходит с запрещённой сетью.



\### Р10. Один рот



Единственный монопольный выход к колонкам: всё, включая живую голосовую сессию Gemini и локальную озвучку, идёт в одну очередь (без этого требование Д13 «голоса не дублируются» невыполнимо: каналов два и они друг о друге не знают). Плюс: смена устройства на ходу → переоткрыть поток, не удалось → текст/уведомление; микрофон занят созвоном → сказать один раз; пока говорит — микрофон приглушён, \*\*но слово «стоп» распознаётся\*\*; громкость 0 → важное дублируется уведомлением.



Закрывает: Х-G1…G5, L7-5, L2-2. Фаза 2б, 1 вечер. Тесты: `test\_single\_speech\_outlet`, `test\_device\_change\_fallback`, `test\_stop\_heard\_while\_speaking`.



\### Р11. Слой окружения



`tools/doctor.py` + `core/env.py`: принудительный UTF-8 везде (Windows-консоль cp1251 роняет задачу на печати русского имени файла); `requires-python` расширить до `>=3.12` (сейчас `\~=3.12.0` — на Python 3.13 установка падает); один файл зависимостей вместо четырёх; логи переезжают в `\~/.jarvis/logs/` с ротацией и единой редакцией секретов (сейчас `logs/` внутри проекта — та же болезнь, что с `config/settings.json`); сценарий первого запуска с человеческими фразами вместо падения; `run\_tests.cmd` в корне (тесты работают только как `python -m pytest` из корня); проверка «запущены из той же папки, что в прошлый раз»; убрать старый SDK `google-generativeai`; нормализовать переводы строк (в `agent/task\_queue.py` они `\\r\\n`, в остальных `\\n` — из-за этого замены «не находятся»); файл `\~/.jarvis/WHERE\_I\_STOPPED.md`, который пишет код в конце каждого шага.



Закрывает: Х-D1…D5, Х-E1, Х-E3, Х-F1…F4, Х-Q1…Q3, Х-K2, Х-T1, L9-7. Фаза 0, 2 вечера — самые дешёвые вечера плана по соотношению «затраты / сэкономленная боль».



\### Р12. Ключ — ресурс со состояниями



Состояния: `свежий → прогретый → активный → остывает → исчерпан на сегодня → забанен → мёртвый`. Жёсткая таблица «ответ сервера → вердикт по ключу» (бан / квота / своя ошибка) — иначе один плохой промпт сожжёт пятьдесят ключей за минуту. Ключ выдаётся потоку монопольно на время вызова. \*\*Учёт квоты на ключ, а не на систему\*\* — это меняет расчёты раздела 6: при ротации «RPD = 20» перестаёт что-либо значить, режимы 0–3 считаются по сумме активных ключей. Пробный дешёвый вызов при вводе ключа в ротацию. Ключи только в `\~/.jarvis/secrets`, никогда в папке проекта и никогда в архиве.



Риск, который кодом не лечится: много аккаунтов ради обхода квот противоречит условиям Google и может выключиться всё сразу — именно поэтому Р9 обязателен. Закрывает: Х-I1…I5, Х-M3, частично Х-M1. Фаза 1, 1,5 вечера. Тесты: `test\_key\_verdict\_table`, `test\_key\_exclusive\_per\_call`, `test\_quota\_per\_key`, `test\_bad\_prompt\_does\_not\_ban\_keys`.



\## 13.4. Что меняется в ранее написанных разделах



1\. \*\*Миграции перенумерованы: 7–17 вместо 40–50\*\* (ФФ3). Везде в разделах 2, 4 и 5 читать: 40→7, 41→8, 42→9, 43→10, 44→11, 45→12, 46→13, 47→14, 48→15, 49→16, 50→17. Добавляется миграция 18: `mx\_outbound` (Д40).

2\. \*\*Учтена вторая база\*\* (`HISTORY\_MIGRATIONS`): у неё своя `user\_version` и свой откат; обе базы всегда бэкапятся и откатываются вместе (Р6).

3\. \*\*Фаза 1б исчезает как отдельная.\*\* Сквозной идентификатор и `ctx` в гейте уезжают в фазу 0.5 (Р2): поле `correlation\_id` уже есть, менять сигнатуру дешевле до того, как агенты начали её использовать. Освобождается 5–6 вечеров. Заборы и чувствительность из бывшей 1б переезжают: нормализация путей в фазу 0, сами заборы — в фазу 3 (Р7).

4\. \*\*Фаза 0 растёт с 2–3 до 4–5 вечеров:\*\* туда входят Р6 (версия состояния), Р11 (слой окружения), нормализация путей, аудит-журнал без потолка и запросы к нему (Ф2).

5\. \*\*Новая фаза 0.7 «Оффлайн-ядро»\*\* (Р9, 1 вечер): первый видимый результат на 8-м вечере, а не на 30-м.

6\. \*\*Фаза 2 делится:\*\* 2а — оркестратор, ресурсы, корректное завершение, красная кнопка (заканчивается демонстрацией «одна фоновая задача от начала до конца»); 2б — один рот и стоп.

7\. \*\*Объём фазы 1 пересчитан:\*\* с учётом 70+ тестов реально 11–13 вечеров (было 8–12 без учёта тестов). Календарь раздела 9: ≈70–74 вечера вместо 67, но с работающей системой с 8-го.

8\. \*\*Правило шага:\*\* один шаг ≤ 40 минут; больше — дробить на этапе плана, а не в 23:40.

9\. \*\*Тесты делятся на два набора:\*\* быстрый (<60 с, гонять каждые 10 минут) и полный (критерий закрытия фазы); у всех тестов времени и памяти проставляются числа и перцентили (фаст-пас: p95 ≤ 3 с, максимум ≤ 5 с).

10\. \*\*Критерий готовности фазы\*\* теперь двойной: машинный (перечень тестов) и человеческий (фраза вслух). Закрытие — по машинному, чтобы сломанный микрофон не блокировал фазу.

11\. \*\*`mx\_meter\_day`\*\* (сутки × роль × модель × ключ: вызовы, токены, отказы, расчётная стоимость) — \*\*хранится бессрочно\*\*, подробность `mx\_meter\_call` — 30 дней. Без этого контрольная точка «+3 месяца» и О23 остаются без данных (данные удалялись на 60-й день).

12\. \*\*Автомат состояний дополнен:\*\* `FROZEN → QUEUED` (по «продолжай») и `FROZEN → CANCELLED` (TTL 24 ч с уведомлением); `PARTIAL → QUEUED` с пропуском выполненных пунктов (поле `done\_at` в `mx\_task\_check`); истечение талона → `FAILED('no answer')` со строкой в сводке; каскад отмены на детей; `FROZEN` не считается активной задачей в потолке 10. Отдельный модуль `core/task\_state.py` с таблицей разрешённых переходов и тест `test\_no\_illegal\_transition`.

13\. \*\*Манифест роли\*\* вместо пяти мест правки: allowlist + ресурсы + бюджет + модель + промпт в одном блоке `agents.yaml`; тест `test\_role\_manifest\_complete`. Аналогично типы задач: `test\_task\_type\_has\_acceptance`.

14\. \*\*Промпты выносятся из `main.py`\*\* в `prompts/<роль>.md` с номером версии; `model`, `prompt\_ver`, `code\_ver` пишутся в каждый отчёт и вызов; набор 25–30 эталонных фраз на моках (единственный способ узнать, что после правки промпта стало хуже); тест на размер списка инструментов главного (больше 12 — красный).



\### 13.5 Реестр дыр по 20 осям и решение для каждой



Второй аудит шёл по 20 осям (О-A…О-T). Ниже — все записи: что за дыра простыми словами, насколько опасна и что мы решили делать. Тяжесть: \*\*BLOCKER\*\* — без этого нельзя начинать фазу 1; \*\*HIGH\*\* — обязательно до конца фазы 2; \*\*MED\*\* — до конца фазы 4; \*\*LOW\*\* — можно позже или принять как есть.



> Обозначения: Ф1–Ф7 — проверенные факты о коде (13.1), Р1–Р12 — несущие решения (13.3), Д… — решения владельца (13.2 и Часть VII документа).

> 



\#### О-A. Хранилища (две базы, миграции, бэкапы)



| ID | Дыра простыми словами | Тяжесть | Решение |

| --- | --- | --- | --- |

| Х-A1 | Баз \*\*две\*\* (`jarvis.db` и история), а версия состояния считалась одна. Откатишь одну — вторая останется из будущего, и Jarvis будет читать несогласованные данные. | BLOCKER | Р1: единый `core/state\_version.py`, который знает версию \*\*обеих\*\* баз; откат только парой. Тесты `test\_state\_version\_covers\_all\_stores`, `test\_rollback\_restores\_both\_dbs`. |

| Х-A2 | Новые миграции планировались с номера 40, а в коде уже 6 (Ф1) → номера в документе неверные. | HIGH | Перенумерация: наши миграции — \*\*7–17\*\*, плюс 18 (`mx\_outbound`). Тест `test\_migrations\_40\_50` переименовать в `test\_migrations\_7\_18`. |

| Х-A3 | Бэкап перед миграцией делается копированием файла, но при WAL копия без `-wal` = битая база. | HIGH | Бэкап только через `sqlite3.backup()` (в коде уже есть в `store.py:503`) и для двух баз сразу; `tools/rollback\_state.py` восстанавливает пару. Тест `test\_backup\_before\_migrate`. |

| Х-A4 | После восстановления из бэкапа поисковый индекс (FTS) может не совпасть с данными → поиск по памяти врёт молча. | MED | После восстановления — принудительная перестройка FTS. Тест `test\_fts\_rebuild\_after\_restore`. |

| Х-A5 | Диск 477 ГБ, но папка `\~/.jarvis` растёт: чёрный ящик, отчёты, результаты, бэкапы. Кончится место — Jarvis умрёт посреди задачи. | MED | Р6: режим низкого диска (<2 ГБ) — стоп новым задачам, только чтение и голос, вслух предупреждение. Тест `test\_low\_disk\_mode`. |



\#### О-B. Идентичность: кто говорит и от чьего имени действует



| ID | Дыра простыми словами | Тяжесть | Решение |

| --- | --- | --- | --- |

| Х-B1 | Талон разрешения с `session\_id=None` (Ф4) — \*\*универсальный\*\*: сказанное однажды «да» работает во всех будущих сессиях. | BLOCKER | Запрет `NULL`: у фона своя сессия `bg:<run\_id>`. Тесты `test\_no\_null\_session\_ticket`, `test\_bg\_does\_not\_inherit\_consent`. |

| Х-B2 | `get\_session\_id()` берётся из runtime — если он не потоко-локальный, 10 агентов-потоков перепутают, чья сессия. | BLOCKER (проверить) | Замер на машине владельца: `inspect.getsource(core.consent\_runtime)`. Если не потоко-локальный — сессия передаётся явно через `TaskCtx`. Тест `test\_agent\_thread\_consent\_path`. |

| Х-B3 | Фоновая задача может унаследовать «да», сказанное владельцем час назад по другому поводу. | HIGH | Талон фона живёт только пока владелец за компьютером, и только на одну задачу. Тест `test\_bg\_ticket\_needs\_live\_owner`. |

| Х-B4 | Сквозной id (`run/task/step`) не доходил до журнала действий → в логах не видно, какой агент это сделал. | HIGH | `ctx` обязателен в `gate.dispatch`; журнал пишет `correlation\_id` всегда. Тесты `test\_ctx\_reaches\_journal`, `test\_no\_action\_without\_correlation`. |

| Х-B5 | Д44 «гостевой режим» — не решён; чужой человек за ноутбуком получает права владельца. | LOW (принято) | Гостевого режима \*\*нет\*\* (тест `test\_no\_guest\_mode`); физическая безопасность ноутбука — вне проекта. Пересмотр в контрольной точке «+3 месяца». |



\#### О-C. Корректное завершение процесса



| ID | Дыра простыми словами | Тяжесть | Решение |

| --- | --- | --- | --- |

| Х-C1 | Все рабочие потоки — `daemon=True` (Ф5): при закрытии Python убивает их на полуслове, файл может остаться переписанным наполовину. | BLOCKER | Р2: `core/shutdown.py` — единая остановка; потоки не daemon, ждём до 15 с (Д31), затем компенсация саг. Тесты `test\_no\_daemon\_threads`, `test\_shutdown\_compensates\_sagas`. |

| Х-C2 | Заморозка задачи (FROZEN) может произойти посреди шага → шаг выполнится дважды после разморозки. | HIGH | Заморозка только на границе шага. Тест `test\_freeze\_at\_step\_boundary`. |

| Х-C3 | Отмена родительской задачи не отменяла порождённые подзадачи — они продолжали жечь квоту. | HIGH | Каскадная отмена по `mx\_spawned`. Тест `test\_cancel\_cascade`. |

| Х-C4 | После аварийного выхода очередь задач восстанавливается — непонятно, что уже сделано. | MED | Fail-closed: незавершённые → `FAILED` с причиной, вслух короткая сводка при старте. Тесты `test\_queue\_restart\_failclosed`, `test\_restart\_summary`. |

| Х-C5 | Нет признака «вышли чисто» → нельзя отличить падение от нормального закрытия. | MED | Маркер чистого выхода в `STATE.json`; при его отсутствии — папка `crash/`. Тест `test\_clean\_exit\_marker`. |



\#### О-D. Установка и первый запуск



| ID | Дыра простыми словами | Тяжесть | Решение |

| --- | --- | --- | --- |

| Х-D1 | Установка зависимостей требовала прав администратора (ветка `pip install` в `cmd\_control`). | HIGH | Ветка мёртвая — удалить; всё в виртуальном окружении, прав администратора не просим никогда. Тест `test\_cmd\_control\_hygiene`. |

| Х-D2 | Автозапуск (Д9) через планировщик задач/реестр — способ, требующий администратора. | MED | Только папка «Автозагрузка» пользователя. Тесты `test\_autostart\_no\_admin`, `test\_silent\_start`. |

| Х-D3 | Второй запущенный Jarvis = две программы пишут в одну базу и говорят одновременно. | HIGH | `core/instance\_lock.py`: файл `\~/.jarvis/jarvis.lock`, вторая копия молча выходит; протухший замок чистится. Тесты `test\_instance\_lock`, `test\_stale\_lock\_cleanup`. |

| Х-D4 | Нет способа проверить «всё ли на месте» перед вечером работы. | MED | `tools/doctor.py`: версии баз, ключи, микрофон, свободный диск и ОЗУ — один вывод «зелёный/красный». |

| Х-D5 | Версия кода нигде не фиксируется (git нет) → непонятно, из какого zip запущено. | MED | `BUILD.txt` пишется \*\*кодом\*\* при старте (дата, имя папки, `code\_ver`); попадает в каждый отчёт. Тест `test\_build\_txt\_written\_by\_code`. |



\#### О-E. Кодировки, длинные пути, кириллица



| ID | Дыра простыми словами | Тяжесть | Решение |

| --- | --- | --- | --- |

| Х-E1 | Пути с русскими буквами и пробелами (папка «Загрузки» у пользователя Iman Usa) ломают склейку строк и `shell=True`. | HIGH | Только `pathlib`, никакой склейки строк; список аргументов вместо строки команды. Тесты `test\_cyrillic\_path`, `test\_no\_string\_path\_concat`. |

| Х-E2 | Windows-путь длиннее 260 символов молча обрывается. | MED | Проверка длины и длинный префикс пути; при невозможности — внятный отказ вслух. Тест `test\_long\_path`. |

| Х-E3 | `task\_queue.py` в CRLF, остальные файлы в LF (Ф6) → сравнение файлов и патчи дают ложные различия. | LOW | Один раз привести к LF, зафиксировать в `docs/`. |

| Х-E4 | Вывод консоли Windows в cp1251 → в логах «кракозябры». | MED | Все чтения/записи в UTF-8 с заменой битых символов; `core/env.py` задаёт кодировку один раз. |

| Х-E5 | Заборы по именам папок сравнивались с учётом регистра → «Загрузки» и «загрузки» считались разными. | HIGH | Сравнение без учёта регистра и с нормализацией пути; забор действует и на просмотр экрана. Тесты `test\_fence\_case\_insensitive`, `test\_fence\_blocks\_screen\_too`. |



\#### О-F. Логи и чёрный ящик



| ID | Дыра простыми словами | Тяжесть | Решение |

| --- | --- | --- | --- |

| Х-F1 | В чёрный ящик может попасть личное: содержимое файлов, переписка, номера. | BLOCKER | Р4: единая точка вычистки (`core/outbound.py`  • `sensitivity.py`), fail-closed — не смог вычистить, значит не пишем. Тесты `test\_blackbox\_no\_secrets`, `test\_blackbox\_redact\_failclosed`, `test\_redaction\_is\_single\_point`. |

| Х-F2 | Дословные фразы владельца хранились бессрочно. | HIGH | Тело чёрного ящика (`mx\_bb\_body`) — \*\*7 дней\*\*, шапка (`mx\_bb\_head`) — навсегда, но без свободного текста. Тест `test\_head\_no\_freetext`. |

| Х-F3 | Чистка старых записей могла удалить строку по открытой (незавершённой) задаче. | MED | Purge пропускает открытые записи. Тест `test\_purge\_skips\_open\_records`. |

| Х-F4 | Журнал действий ограничивался размером стека отмены (8, Ф3) → аудит терял историю. | MED | Аудит и отмена — разные лимиты; журнал не обрезается по `JOURNAL\_MAX`. Тест `test\_audit\_not\_capped\_by\_undo`. |



\#### О-G. Звук и голос



| ID | Дыра простыми словами | Тяжесть | Решение |

| --- | --- | --- | --- |

| Х-G1 | Два канала звука (живая сессия Gemini и локальная озвучка) не знают друг о друге → голоса налагаются. Требование Д13 «голоса не дублируются» было физически невыполнимо. | BLOCKER | Р10 «Один рот»: единственный монопольный выход к колонкам, всё через `core/speech\_queue.py`. Тест `test\_single\_speech\_outlet`. |

| Х-G2 | Наушники выдернули посреди фразы — поток звука умирает, Jarvis думает, что сказал. | HIGH | Переоткрытие потока на новом устройстве; не удалось — текст в окно/уведомление. Тест `test\_device\_change\_fallback`. |

| Х-G3 | Микрофон занят созвоном → Jarvis глухой и об этом не говорит. | MED | Один раз сказать «микрофон занят» и уйти в тишину; повторно не ныть. |

| Х-G4 | Пока Jarvis говорит, микрофон приглушён — слово «стоп» может не быть услышано (а владелец требует остановку голосом). | BLOCKER | Слово «стоп» распознаётся \*\*всегда\*\*, даже во время речи. Тест `test\_stop\_heard\_while\_speaking`. |

| Х-G5 | Громкость 0 или беззвучный режим → важное сказано в пустоту. | HIGH | Р8: важное дублируется уведомлением Windows; цепочка каналов тихо→уведомление→голос→окно. Тест `test\_notify\_fallback\_chain`. |



\#### О-H. Сеть



| ID | Дыра простыми словами | Тяжесть | Решение |

| --- | --- | --- | --- |

| Х-H1 | Интернет пропал посреди задачи — задача виснет молча. | HIGH | Режим N: задача в `PARTIAL` с атрибуцией `world`, уведомление + звук. I19: никогда молча. |

| Х-H2 | Голосовой канал — живая сессия: обрыв сети роняет всё общение целиком. | HIGH | Р9: оффлайн-ядро (фаза 0.7) — напоминания, файлы, окна, отмена работают без сети. Тест `test\_offline\_core\_no\_network`. |

| Х-H3 | Ответ сервера может идти минутами — задача держит слот из 10. | MED | Таймаут на вызов + `max\_seconds` в задаче; по истечении — `PARTIAL`, слот освобождается. |

| Х-H4 | Прокси/сторонние шлюзы видят всё содержимое запросов. | HIGH | Поле доверия у поставщика (Д33); через недоверенный канал личное не уходит никогда (Р4, Д40). |

| Х-H5 | Повторы при плохой сети сжигают дневную квоту за минуты. | HIGH | Retry ≤ 3 на задачу и ≤ 20 в сутки; circuit breaker: 3 отказа → роль отдыхает 10 минут. |



\#### О-I. Ключи и аккаунты



| ID | Дыра простыми словами | Тяжесть | Решение |

| --- | --- | --- | --- |

| Х-I1 | Живой ключ лежит в \*\*трёх\*\* файлах внутри проекта и уезжает в каждый zip. | BLOCKER | День 1: ротация ключа, всё только в `\~/.jarvis/secrets`. Тест `test\_no\_key\_literal\_anywhere`. |

| Х-I2 | При ротации сотен ключей один плохой промпт сожжёт десятки ключей за минуту (каждый получит бан). | BLOCKER | Р12: жёсткая таблица «ответ сервера → вердикт по ключу» (бан / квота / своя ошибка). Тесты `test\_key\_verdict\_table`, `test\_bad\_prompt\_does\_not\_ban\_keys`. |

| Х-I3 | Квота считалась «на систему», а при ротации она на ключ → все числа раздела 6 неверны. | HIGH | Учёт на ключ; режимы 0–3 — по сумме активных ключей. Тест `test\_quota\_per\_key`. |

| Х-I4 | Два потока могут взять один ключ одновременно → ложный перебор RPM. | HIGH | Ключ выдаётся монопольно на время вызова. Тест `test\_key\_exclusive\_per\_call`. |

| Х-I5 | Много аккаунтов ради обхода квот противоречит правилам Google — всё может выключиться одномоментно. | HIGH (не лечится кодом) | Именно поэтому Р9 обязателен: оффлайн-ядро остаётся полезным без любого облака. |



\#### О-J. Особенности Windows



| ID | Дыра простыми словами | Тяжесть | Решение |

| --- | --- | --- | --- |

| Х-J1 | OneDrive мог бы синхронизировать `\~/.jarvis` в облако и ломать SQLite. | СНЯТА | Владелец подтвердил: \*\*OneDrive не включён\*\*, оставляем как есть. |

| Х-J2 | Ноутбук засыпает — таймеры и сроки талонов считают, будто время шло. | HIGH | Сон — событие «мы спали X минут» (Р5): срок талона приостанавливается, накопившиеся напоминания склеиваются в одну фразу (Д43). Тесты `test\_hitl\_expiry\_paused\_on\_sleep`, `test\_sleep\_event\_collapses\_reminders`. |

| Х-J3 | Антивирус блокирует управление мышью/клавиатурой — задача падает без объяснения. | MED | Д26: проверка при старте, только сообщение, без админ-прав; атрибуция `world`. |

| Х-J4 | Полноэкранная игра — любое окно ворует фокус и выкидывает владельца из игры. | HIGH | Окна только без активации; в полный экран — только тихая строка/уведомление (Д43). Тест `test\_no\_focus\_steal`. |

| Х-J5 | Каждая распаковка zip в новую папку молча возвращает старые настройки и логи. | BLOCKER | Настройки, логи, секреты — только в `\~/.jarvis` (Р6, Р11); проверка «запущены из той же папки, что в прошлый раз» (`last\_run\_path`). |

| Х-J6 | На заблокированном экране Jarvis может вслух произнести личное при посторонних. | HIGH | На заблокированном экране голоса нет вообще; срочное превращается в уведомление и повторяется при возвращении (Д43). Тест `test\_no\_voice\_on\_locked\_screen`. |



\#### О-K. Цена вечера (темп работы)



| ID | Дыра простыми словами | Тяжесть | Решение |

| --- | --- | --- | --- |

| Х-K1 | Неизвестно, сколько идёт полный прогон тестов. Если 4 минуты — владелец перестанет их гонять. | BLOCKER (замер) | Замер `python -m pytest -q --durations=10`. Два набора: быстрый ≤ 60 с (каждые 10 минут) и полный (закрытие фазы). |

| Х-K2 | Шаги плана были размером в целый вечер → обрыв на половине в 23:40. | HIGH | Правило: один шаг ≤ 40 минут; `\~/.jarvis/WHERE\_I\_STOPPED.md` пишет код в конце каждого шага (Р11). |

| Х-K3 | 70+ новых тестов не были учтены в оценках фаз. | HIGH | Фаза 1 → 11–13 вечеров; всего ≈ 70–74 вечера (см. 13.4 п. 7). |

| Х-K4 | Критерий готовности был только «фраза вслух» — сломанный микрофон блокировал бы фазу. | MED | Двойной критерий: машинный (тесты) закрывает фазу, человеческий — приёмка владельцем. |



\#### О-L. Приватность в облаке



| ID | Дыра простыми словами | Тяжесть | Решение |

| --- | --- | --- | --- |

| Х-L1 | Всё, что Jarvis «понимает», уезжает в облако Google: голос, содержимое файлов, скрины экрана. | BLOCKER | Д40 «карантин исходящего»: `core/outbound.py` проверяет каждый исходящий кусок; нашёл личное (паспорт, карта, переписка) → \*\*сказать вслух и спросить разрешение\*\*, владелец решает сам. Запись в `mx\_outbound` (миграция 18). Тесты `test\_no\_outbound\_without\_carantine`, `test\_file\_content\_needs\_policy`. |

| Х-L2 | Классификатор чувствительности сам ходит в модель → чтобы проверить текст, отправляет его в облако. Парадокс. | BLOCKER | Классификатор работает \*\*только локально\*\* (правила и шаблоны, не модель); сомневается — считает чувствительным и спрашивает. Тест `test\_sensitivity\_offline\_fallback`. |

| Х-L3 | Голос главного может произнести вслух то, что классификатор запретил отправлять. | HIGH | Фильтр чувствительности стоит и на речи («сказать» — тоже действие, Г-4). Тест `test\_speech\_sensitivity\_filter`. |

| Х-L4 | Файлы результатов пишутся мимо гейта → заборы и журнал их не видят. | HIGH | Запись результатов только через `gate.dispatch`. Тесты `test\_results\_writes\_through\_gate`, `test\_result\_purge\_removes\_file`. |



\#### О-M. Правила поставщика



| ID | Дыра простыми словами | Тяжесть | Решение |

| --- | --- | --- | --- |

| Х-M1 | Весь проект держится на бесплатном уровне одного поставщика; правила могут поменяться в любой день. | HIGH | Р9 (оффлайн-ядро) + Д33 (адаптеры поставщиков): смена поставщика — один файл, не переписывание проекта. |

| Х-M2 | Модели в `registry.yaml` — preview-версии; их выключают без предупреждения (так уже случилось с `live\_screen`, close 1007). | HIGH | Роль ≠ модель (I37): в коде только имена ролей; у каждой роли запасная модель. Тест `test\_registry\_roles`. |

| Х-M3 | Сотни аккаунтов ради квот — нарушение условий; риск одномоментного отключения всего. | HIGH (риск принят) | Не лечится кодом — только страховкой: Р9 (оффлайн-ядро) и режим 3 живёт бесконечно. |



\#### О-N. Промпты как код



| ID | Дыра простыми словами | Тяжесть | Решение |

| --- | --- | --- | --- |

| Х-N1 | Промпты живут внутри `main.py` (2014 строк) — правка фразы = правка кода, истории изменений нет. | HIGH | `prompts/<роль>.md` с номером версии; `prompt\_ver` пишется в каждый вызов и отчёт (13.4 п. 14). |

| Х-N2 | После правки промпта нет способа узнать, что стало хуже. | HIGH | Набор 25–30 эталонных фраз на моках (ноль токенов), гоняется после любого изменения промпта. |

| Х-N3 | Список инструментов главного будет расти → модель начнёт путаться и терять точность. | MED | Д11: у главного только фаст-пасы + `delegate`; тест на размер списка (больше 12 — красный), `test\_main\_only\_fastpass\_tools`. |



\#### О-O. Многосессионность



| ID | Дыра простыми словами | Тяжесть | Решение |

| --- | --- | --- | --- |

| Х-O1 | Задача, начатая в одном разговоре, доживает до следующего — а разрешения уже другие. | HIGH | Сессия — часть `TaskCtx`; при смене сессии талоны не переносятся (Р4). |

| Х-O2 | После перезапуска владелец не знает, что было до этого. | MED | Одна фраза-сводка при старте (`test\_restart\_summary`) + `WHERE\_I\_STOPPED.md`. |

| Х-O3 | Несколько вопросов-подтверждений от разных задач налетают одновременно — владелец не понимает, на что отвечает. | HIGH | Д42 + Р8: однотипные вопросы группируются в один, не больше двух разных подряд, вопрос всегда называет задачу. Тесты `test\_max\_consecutive\_questions`, `test\_grouped\_question`. |



\#### О-P. Экран



| ID | Дыра простыми словами | Тяжесть | Решение |

| --- | --- | --- | --- |

| Х-P1 | Запрет смотреть на папку не мешает увидеть её содержимое на экране. | HIGH | Р7: забор — сущность (путь · окно · приложение · адрес · область экрана); один вопрос для всех четырёх каналов. Тест `test\_fence\_blocks\_screen\_too`. |

| Х-P2 | Просмотр экрана стоит дорого (vision) и легко съедает дневную квоту. | MED | Vision только по явному поводу, агентам без контекста — никогда (I12); учёт в метеринге как отдельная роль. |

| Х-P3 | Режим живого экрана в коде уже отключён (close 1007), но модули на 507  •  267  •  596 строк остались. | LOW | Не трогать до фазы 4; вернуть только когда будет живая preview-модель и свободная квота. |



\#### О-Q. Зависимости



| ID | Дыра простыми словами | Тяжесть | Решение |

| --- | --- | --- | --- |

| Х-Q1 | `requires-python = "\~=3.12.0"` — на Python 3.13 установка падает. | MED | Расширить до `>=3.12` (Р11). |

| Х-Q2 | Четыре способа описания зависимостей (`pyproject`, `requirements.txt`, `setup.py`, `uv.lock`) — окружения расходятся. | MED | Один источник истины (`pyproject`  • лок-файл), остальное удалить или сделать тонкой обёрткой. |

| Х-Q3 | Тесты требуют `pyaudio` и `pywin32`, без них не запускаются вообще (поэтому «784 passed» не подтверждено со стороны). | HIGH | Тесты ядра должны работать без звука и Windows-библиотек (моки); `run\_tests.cmd` в корне. |



\#### О-R. Внимание владельца



| ID | Дыра простыми словами | Тяжесть | Решение |

| --- | --- | --- | --- |

| Х-R1 | Жёсткий суточный потолок «10 инициативных реплик» съедался пустяками к обеду, а вечером важное молчало. | BLOCKER | \*\*Д43\*\* (решён в 13.2): три уровня — Срочное (без потолка), Полезное (не чаще 1 раза в 15 минут, со склейкой), Мелочь (только тихая строка в файл). Порог — в `\~/.jarvis/settings.json`, владелец меняет сам. Тест `test\_attention\_single\_budget`. |

| Х-R2 | Напоминания попадали под тот же потолок → могли не прозвучать вообще. | BLOCKER | Напоминания и ответы на вопрос владельца — \*\*вне потолка\*\* всегда. Тесты `test\_reminders\_exempt`, `test\_reminders\_exempt\_from\_cap`. |

| Х-R3 | Нет правила тишины по обстановке: в созвоне и в игре Jarvis всё равно говорит. | HIGH | Д43: полный экран / созвон / заблокированный экран → срочное становится уведомлением и повторяется голосом при возвращении. |

| Х-R4 | После сна или долгого отсутствия всё накопившееся высыпается залпом. | HIGH | Д43, правило залпа: одна фраза вида «было восемь напоминаний, самое важное — встреча в 15:00». Тест `test\_sleep\_event\_collapses\_reminders`. |



\#### О-S. Деньги



| ID | Дыра простыми словами | Тяжесть | Решение |

| --- | --- | --- | --- |

| Х-S1 | Проект должен быть бесплатным, но нет ни одного числа, показывающего, сколько бы он стоил. | MED | `mx\_meter\_day` считает расчётную стоимость по прайс-листу и \*\*хранится бессрочно\*\*; так видно, когда дешевле заплатить, чем возиться с ключами. |

| Х-S2 | Если владелец когда-нибудь купит API, весь код придётся переделывать. | LOW | Адаптеры `core/provider/\*` (Д33) уже готовы к платному ключу: меняется одна строка в `registry.yaml`. |



\#### О-T. Смерть проекта (самый вероятный исход)



| ID | Дыра простыми словами | Тяжесть | Решение |

| --- | --- | --- | --- |

| Х-T1 | Первый видимый результат по первоначальному плану появлялся на 30-м вечере. Проект умирает не от бага, а от скуки. | BLOCKER | Р9: \*\*фаза 0.7 оффлайн-ядро\*\* — работающий полезный Jarvis на 8-м вечере. Тест `test\_offline\_core\_no\_network`. |

| Х-T2 | После недели паузы владелец не помнит, где остановился — и не возвращается. | BLOCKER | `\~/.jarvis/WHERE\_I\_STOPPED.md` пишет \*\*код\*\* в конце каждого шага: что сделано, что дальше, какая команда запускает тесты (Р11). |

| Х-T3 | Если Google закроет бесплатный доступ, от проекта не останется ничего. | BLOCKER | Оффлайн-ядро и режим 3 — постоянное состояние, а не аварийное (Р9). Голос и напоминания — минимум, который всегда жив. |



\#### Итог по реестру



| Показатель | Значение |

| --- | --- |

| Всего записей по 20 осям | 82, из них 1 снята (Х-J1, OneDrive не включён) |

| BLOCKER (до фазы 1) | Х-A1, Х-B1, Х-B2, Х-C1, Х-F1, Х-G1, Х-G4, Х-I1, Х-I2, Х-J5, Х-K1, Х-L1, Х-L2, Х-R1, Х-R2, Х-T1, Х-T2, Х-T3 |

| Закрываются решениями Р1–Р12 | 61 запись |

| Требуют замера на машине владельца | Х-B2 (потоко-локальность), Х-K1 (время тестов), ОЗУ, версии двух баз |

| Приняты осознанно | Х-B5 (гость), Х-E3 (CRLF), Х-I5 и Х-M3 (правила Google), Х-P3 (живой экран), Х-S2 (платный API) |



> \*\*Главный вывод второго аудита.\*\* Из 82 записей опасны не те, что про безопасность и квоты (их легко посчитать и закрыть), а ось О-T: проект с высочайшей вероятностью умрёт от того, что 30 вечеров не будет ничего видно. Поэтому фаза 0.7 и правило «шаг ≤ 40 минут» важнее любого отдельного теста в этом списке.

> 



\---



\## 13.6 ПАТЧ 8 — замеры на живой машине (5 августа 2026)



Получены четыре из пяти замеров. Три закрыты полностью, один (ОЗУ) остаётся. Всё, что ниже — не рассуждения, а показания машины владельца.



\### 13.6.1 Новые проверенные факты Ф8–Ф13



| № | Что замерили | Результат | Что из этого следует |

| --- | --- | --- | --- |

| Ф8 | `PRAGMA user\_version` всех баз в `\~/.jarvis/` | `jarvis.db 6`. Файла `history.db` в папке \*\*нет\*\* | Нумерация наших миграций \*\*7–18 верна\*\* (следующая свободная — 7). Вторая база создаётся лениво: `open\_history()` в коде есть, но пока не вызывается ниоткуда («populated for real in Stage 7»). Значит Р1 и Р6 обязаны уметь состояние «второй базы ещё не существует» — иначе первый же откат упадёт на «файл не найден». Порождает Х-U3 |

| Ф9 | Полный прогон тестов | \*\*784 passed за 23.32 с\*\*, самый долгий тест 0.64 с | «784 passed» подтверждено на живой машине, `pyaudio` и `pywin32` установлены. \*\*Х-K1 закрыта на сегодня\*\*: делить тесты на «быстрый» и «полный» набор пока не нужно. Норматив: полный прогон ≤ 60 с. Как только превысит — только тогда вводим быстрый набор |

| Ф10 | Как выглядит вывод тестов | Сотни строк `\[GATE]`, `\[FileController]`, `\[Memory]`, битая кодировка в консоли, в `pyproject.toml` стоит `addopts = "-p no:capture"` (обход сломанного `pyreadline`) | Порождает Х-U1: при 900 тестах одно упавшее утонет в мусоре, и владелец перестанет читать вывод |

| Ф11 | Исходник `core/consent\_runtime.py` | `get\_session\_id()` — \*\*глобальная на процесс\*\*, в модульных переменных, комментарий прямо говорит «Stable for the life of the process». `get\_conn()` намеренно возвращает \*\*одно\*\* соединение fileops-журнала: «транзакция не может охватывать два соединения — совместное использование это не оптимизация, а то, что делает атомарность возможной». `set\_override()` — тоже глобальный | \*\*Х-B2 подтверждена фактом, а не догадкой\*\*: любой поток-агент получит session\_id владельца и вместе с ним его талоны согласия. \*\*Р3 «одна касса» совпадает с собственным принципом кода\*\* — это не наша выдумка, а то, на чём код уже стоит. Но сериализации записи между потоками в коде нет: её надо добавить (единый замок записи), и `get\_session\_id` / `set\_override` сделать потоко-локальными с откатом на глобальное значение |

| Ф12 | Доступность моделей в аккаунте | 3.5 Flash Lite 15 / 250K / \*\*500\*\*; 3.1 Flash Lite 15 / 250K / \*\*500\*\*; Gemma 4 \*\*26B и 31B\*\* 30 / 16K / 14 400 | Целевой `registry.yaml` фазы 0.5 подтверждён целиком, переделывать план под другие модели не нужно. Gemma 4 31B — бесплатный запасной для роли `aux\_cheap` |

| Ф13 | Окружение Python | Microsoft Store Python 3.12 (`...\\Packages\\PythonSoftwareFoundation.Python.3.12\_qbz5n2kfra8p0\\...`). В `pyproject.toml` уже есть `psutil>=5.9.0` | `psutil` есть — `test\_memory\_budget` пишется без оговорок, метка «требует проверки в коде» в 8.4 снята. Store-Python порождает Х-U2 |



\### 13.6.2 Новые дыры Х-U1 – Х-U3



| ID | Важность | Дыра | Что произойдёт | Решение | Тест |

| --- | --- | --- | --- | --- | --- |

| Х-U1 | HIGH | Тесты печатают всё подряд, перехват вывода отключён флагом `-p no:capture` | На 30-м вечере при \~900 тестах строка `1 failed` окажется в середине тысячи строк лога. Владелец перестанет запускать тесты — а на них держится вся защита от поломок | `run\_tests.cmd` в корне проекта: `chcp 65001` затем `python -m pytest -q --tb=short -r fE > logs\\tests\_last.txt 2>\&1`, после чего печатать только последние 15 строк файла. Новые тесты пишут через `logging`, а не `print`. Отдельно проверить, жив ли ещё баг `pyreadline`; если починился — вернуть штатный перехват вывода | `test\_runner\_prints\_summary\_only` |

| Х-U2 | MED | Python установлен из Microsoft Store | Автозапуск (Д9) через Планировщик задач, прописанный на «псевдоним» `python.exe` из `WindowsApps`, однажды после обновления Store молча перестанет срабатывать. Данные при этом в порядке: база лежит в обычном `C:\\Users\\rdrr\\.jarvis`, перенаправления папок не происходит | `tools/doctor.py` определяет Store-Python по `WindowsApps` или `Packages\\PythonSoftwareFoundation` в `sys.executable` и предупреждает. Задача автозапуска записывается на реальный `sys.executable` на момент установки; при каждом старте Jarvis проверяет, существует ли этот путь, и если нет — перерегистрирует задачу и говорит об этом вслух | `test\_store\_python\_detected`, `test\_autostart\_uses\_real\_exe` |

| Х-U3 | MED | Второй базы (`history.db`) физически не существует | Р6 (бэкап и откат «обеих баз») и Р1 (единая версия состояния) написаны так, будто обе базы есть. Первый же откат упадёт | В `STATE.json` каждая база — отдельная запись вида «есть/нет + версия». Бэкап молча пропускает отсутствующую, откат восстанавливает только то, что было в снимке. `open\_history()` не вызывается до той фазы, где вторая база действительно нужна | `test\_rollback\_when\_history\_absent`, `test\_state\_version\_handles\_absent\_db` |



\### 13.6.3 Что журнал прогона подтвердил бесплатно



Это не новые дыры, а хорошая новость: часть будущей конструкции уже работает в коде.



\- \*\*Талоны согласия ведут себя правильно.\*\* Одноразовость: `consent 'cst\_3f8c366d10' is already consumed and cannot be reused`. Привязка к конкретным параметрам: `this is not the operation that was approved: path: 'report.txt' != 'taxes.xlsx'`. Неизвестный id отвергается. Обратимое удаление помечается отдельной формулировкой «moves the file to the Recycle Bin and is REVERSIBLE». Значит Д42 и Р2 ложатся на существующий механизм, а не строятся с нуля.

\- \*\*Fail-closed работает.\*\* Искусственная поломка гейта даёт `gate error — refusing file\_controller (fail-closed): gate exploded`, то есть отказ, а не выполнение.

\- \*\*Автономный режим уже глухой.\*\* Всё, что требует подтверждения, в нём отклоняется: `autonomous/blocked ... confirm required (autonomous → deny)`.

\- \*\*Повреждённая память восстанавливается.\*\* Битый `long\_term.json` откатывается из `.bak1`, а если копий нет — старт с пустого состояния, и повреждённый файл НЕ удаляется.

\- \*\*Нехватка диска уже обработана.\*\* В логе есть `backup skipped: disk full` — сценарий Х-A5 частично закрыт в коде.



\### 13.6.4 Решения владельца Д45 и Д46



\*\*Д45. Дословные фразы владельца хранятся 30 дней.\*\*



\- Любая колонка, где лежит сказанное владельцем слово в слово (`verbatim`, тексты вопросов и подтверждений, исходные формулировки задач), чистится по возрасту 30 дней.

\- Сухие производные факты («задача выполнена», «файл создан», «правило добавлено») хранятся бессрочно — они не содержат речи.

\- Чистка запускается при старте и раз в сутки, вместе с уже запланированной чисткой `mx\_report` и `mx\_result`. Записи с `keep=1` не трогаются.

\- Голосовая команда «забудь всё, что я говорил сегодня» стирает дословные фразы за сутки немедленно.

\- Тест `test\_verbatim\_purge\_30d`: запись возрастом 31 день исчезает, возрастом 29 дней остаётся, производный факт остаётся всегда.



\*\*Д46. Модельный ряд подтверждён (Ф12).\*\* Целевой `registry.yaml` фазы 0.5 принимается без изменений: `aux\_light`, `aux\_heavy` и `vision` → `gemini-3.5-flash-lite` (15 RPM, 500 RPD), `aux\_cheap` → `gemma-4-26b`, запасной `gemma-4-31b`, `embedder` → `models/text-embedding-004`, роль `fix\_legacy` удаляется.



\### 13.6.5 Что этот патч уже поправил в ранее написанных разделах



1\. Везде «миграции 40–50» заменено на «миграции 7–18» (разделы 1, 2, 5).

2\. Файл теста миграций переименован в `test\_migrations\_7\_18.py` (было `test\_migrations\_40\_50.py`).

3\. Пример отката миграции: «ошибка в 44 оставляет `user\_version=43`» → «ошибка в 11 оставляет `user\_version=10`».

4\. В карте модулей рядом с `jarvis.db` указана вторая база и её состояние «файла ещё нет».

5\. В 8.4 снята метка «требует проверки в коде» про `psutil` — он есть в зависимостях.



\### 13.6.6 Остался один замер



ОЗУ. Прошлая команда не сработала, потому что была для PowerShell, а запускалась в `cmd`. Правильная для `cmd`: запустить Jarvis, поработать 5 минут, затем `powershell -NoProfile -Command "Get-Process python\* | Select Id,ProcessName,@{n='MB';e={\[math]::Round($\_.WorkingSet64/1MB)}}"`. Замер частично выполнен 05.08.2026 в 03:30 — см. 13.6.7 ниже. Результат оказался тяжелее всех предыдущих находок.



\## 13.6.7 ПАТЧ 8-бис — замер ОЗУ (05.08.2026, 03:30). Самый тяжёлый факт всего аудита



\*\*Ф14 (BLOCKER-факт).\*\* `systeminfo` на машине владельца: \*\*«Доступная физическая память: 270 МБ»\*\*. `Get-Process python\*` при этом вернул пустоту — \*\*Jarvis не был запущен\*\*. То есть 270 МБ — это не «сколько осталось Jarvis’у», а сколько свободно на машине \*\*до\*\* его старта. Ранее в документе стояла оценка владельца «бывает 1–2 ГБ свободных»; реальность оказалась в четыре–семь раз хуже.



Целевой бюджет «400–500 МБ» \*\*физически не помещается в свободную оперативную память\*\*. Jarvis запустится (Windows выдаст память за счёт сжатия и файла подкачки), но часть его будет жить на SSD, а не в ОЗУ. Симптомы: пауза 2–5 секунд на первой фразе после простоя, рывки голоса, износ SSD.



\*\*Х-U4 (BLOCKER, новая дыра, ось О-U).\*\* Весь раздел о параллельности построен на «до 10 активных задач» (Т4 фазы 2, п. 8) и на пороге `memwatch` как на страховке от редкого случая. При 270 МБ свободных верно обратное: \*\*нехватка памяти — не редкий случай, а нормальное состояние машины\*\*, а «10 активных» — число, недостижимое ни при каких условиях.



\*\*Что меняется в плане (обязательно, до фазы 2):\*\*



1\. \*\*Потолок одновременных задач перестаёт быть константой.\*\* Вместо «10» — вычисляемое значение: `memwatch` при каждой попытке старта смотрит `psutil.virtual\_memory().available` и разрешает столько задач, сколько влезает при норме 60 МБ на активную задачу, но не меньше 1 и не больше `max\_concurrent` из `config/agents.yaml`. Значение по умолчанию — \*\*2\*\*, а не 10. Число 10 остаётся в конфиге как верхняя граница на будущее железо. Требование владельца «важно, чтобы я мог сам изменить ограничение» соблюдено — одна строка в yaml.

2\. \*\*Режим M перестаёт быть экзотикой и становится штатным.\*\* Три порога по свободной памяти системы (числа уточнены замером Ф15, см. 13.6.8): больше 500 МБ — \*\*режим 0\*\* (всё как задумано); 200–500 МБ — \*\*M1\*\* (новые фоновые задачи не стартуют, очередь копится, зрение и браузер выключены, голос и фаст-пасы живы); меньше 200 МБ — \*\*M2\*\* (только голос и напоминания; кэши сбрасываются, память пишется в базу, вслух один раз «Работаю в облегчённом режиме, сэр — мало памяти»). Молча деградировать никогда (инвариант I19).

3\. \*\*Ленивые импорты становятся обязательным правилом, а не советом.\*\* `cv2`, `playwright`, `pyautogui`, `numpy`, `python-docx`, `openpyxl`, `mss` импортируются в момент первого использования, а не при старте. Один только `import cv2` стоит порядка 100–200 МБ — при 270 МБ свободных это разница между «работает» и «не работает». Новый тест `test\_no\_heavy\_imports\_at\_startup`: поднять ядро и проверить, что перечисленных модулей нет в `sys.modules`.

4\. \*\*Тяжёлое зрение выносится в короткоживущий отдельный процесс.\*\* Python не возвращает системе память после выгрузки таких модулей. Поэтому разбор скриншота (`cv2`/`mss`/`Pillow`) выполняет дочерний процесс, который \*\*умирает сразу после ответа\*\* и возвращает всю память системе. Это не противоречит решению «агенты — потоки»: агенты остаются потоками, отдельный процесс — только у зрения, как у браузера, который и так свой.

5\. \*\*Тест `test\_memory\_budget` переписывается.\*\* Сейчас он проверяет «наш процесс ≤500 МБ». Добавляется вторая половина, более важная: при искусственно заниженной свободной памяти ядро обязано войти в M1/M2 и не упасть, а не пытаться стартовать задачи.

6\. \*\*`tools/doctor.py` получает проверку памяти при каждом старте.\*\* Если свободной памяти меньше 500 МБ — назвать трёх главных пожирателей памяти и сказать вслух один раз: «Сэр, в системе мало свободной памяти. Работаю в облегчённом режиме». Файл подкачки трогать нельзя (нужны права администратора, а их не просим) — только предупредить.

7\. \*\*Критерий готовности фазы 2 переформулируется.\*\* Было: «Диспетчер задач показывает один `python.exe` в пределах 500 МБ». Становится: «свободной памяти в системе не стало меньше 200 МБ, а при искусственном сжатии памяти Jarvis переходит в облегчённый режим и продолжает отвечать голосом».



\*\*Замер, который всё ещё не сделан.\*\* Собственное потребление Jarvis при запущенном `main.py`. Он не отменяет ничего из семи пунктов выше — они нужны при любом его результате, — но определяет норму «60 МБ на задачу» и режим по умолчанию (2 или 1).



\*\*Почему это не повод сворачивать проект.\*\* 784 теста проходят за 23 секунды на этой же машине — значит, для разработки памяти хватает. Проблема касается только одновременной работы Jarvis и остальных приложений владельца. Ленивые импорты и потолок «2 вместо 10» — это два вечера работы, а не переделка архитектуры.



\## 13.6.8 ПАТЧ 8-тер — замер завершён (05.08.2026, 13:01). Диагноз изменился



Последний из пяти замеров выполнен. Паника из 13.6.7 частично снимается, но на её место приходит более точная и более неприятная цифра.



\*\*Ф15. Собственный расход Jarvis — два разных числа, и важно большее.\*\* `Get-Process python\*` при работающем `main.py` (один процесс `python3.12`, PID 3272, 340 с процессорного времени):



| Показатель | Значение | Что это на самом деле |

| --- | --- | --- |

| `WS(K)` — рабочий набор | 283 132 КБ = \*\*277 МБ\*\* | Сколько Jarvis держит в физической памяти сейчас. Это не аппетит, а то, что ему оставили |

| `PM(K)` — частная выделенная память | 617 660 КБ = \*\*603 МБ\*\* | Сколько Jarvis действительно выпросил у системы. \*\*Это и есть настоящий бюджет\*\* |

| Разница | \*\*326 МБ\*\* | Уже вытеснено в файл подкачки и сжатую память. То самое «живёт на SSD» из 13.6.7 — уже происходит |



Вывод, который легко пропустить: \*\*целевой бюджет «400–500 МБ» уже превышен сегодня, без единого агента\*\* — 603 МБ против 500. Судить по `WS` было бы самообманом: 277 МБ — это пайка, выданная теснотой, а не потребность.



\*\*Ф16. Виновник тесноты найден, и это не Jarvis.\*\* Топ-12 потребителей памяти:



| Программа | Память | Комментарий |

| --- | --- | --- |

| Chrome, шесть процессов в топе | \*\*≈ 3,5 ГБ\*\* | Один единственный процесс — \*\*2,4 ГБ\*\*. В списке только первая дюжина, реально процессов Chrome больше |

| `explorer` | 164 МБ | Проводник Windows, трогать нельзя |

| `msedgewebview2` | 127 МБ | Встроенный браузер внутри какого-то приложения |

| `Telegram` | 126 МБ | — |

| `WindowsTerminal`  • `powershell` | 109  •  82 МБ | Сами окна замера |

| `SearchHost` | 91 МБ | Поиск Windows |

| \*\*Jarvis (`python3.12`)\*\* | \*\*277 МБ в ОЗУ / 603 МБ выделено\*\* | Не попадает даже в тройку пожирателей |



\*\*Диагноз меняется.\*\* В 13.6.7 было записано «машина хронически переполнена». Фактически: \*\*машина переполнена тогда и только тогда, когда открыт Chrome\*\*. Закрытие лишних вкладок освобождает около 3 ГБ — это больше, чем нужно всем десяти агентам вместе взятым. Поэтому правильная архитектурная реакция — не «урезать всё навсегда», а \*\*подстраиваться под обстановку\*\*: при открытом Chrome — один-два агента, после его закрытия — сколько угодно в пределах конфига.



\### Что это меняет в решениях 13.6.7



| Пункт 13.6.7 | Было | Стало после Ф15/Ф16 |

| --- | --- | --- |

| 1. Потолок задач | по умолчанию 2, вычисляется по свободной памяти | \*\*Подтверждено и усилено.\*\* При открытом Chrome формула даст 1–2, при закрытом — верхнюю границу конфига. Агенты не отменяются |

| 2. Пороги режима M | 700 / 300 МБ | \*\*500 / 200 МБ\*\* — иначе при обычной работе с Chrome Jarvis вечно сидел бы в M2 и не делал ничего. При замеренных 270 МБ система попадает в M1: голос жив, фон ждёт — честное поведение |

| 3. Ленивые импорты | правило без числа | \*\*Появляется цель:\*\* выделенная память при старте ≤ 350 МБ вместо нынешних 603. Это главная техническая задача по памяти во всём плане |

| 6. `doctor.py` | предупреждает при нехватке | \*\*Называет виновника.\*\* «Сэр, свободно 270 мегабайт. Больше всего занимает Chrome — три с половиной гигабайта». Обвинять абстрактную «нехватку памяти» бесполезно |



\*\*Х-U5 (HIGH, новая дыра).\*\* Весь план говорит о памяти одним числом, а числа два, и они расходятся вдвое (277 против 603). Любой тест или порог, написанный только по `WS`, будет врать в благоприятную сторону именно тогда, когда машине тяжело. Решение: `test\_memory\_budget` проверяет \*\*оба\*\* числа — `psutil.Process().memory\_info().rss` и `.private`; порогом считается большее. Тесты `test\_startup\_footprint` (выделено ≤ 350 МБ сразу после старта) и `test\_memory\_budget\_reports\_both`.



\*\*Д47. Числа памяти живут в одном месте и меняются владельцем.\*\* В `\~/.jarvis/settings.json` блок `memory`: `mode0\_above\_mb: 500`, `mode2\_below\_mb: 200`, `per\_task\_mb: 60`, `max\_concurrent: 10`, `startup\_budget\_mb: 350`. Ни одно из этих чисел не зашито в код; тест `test\_memory\_numbers\_from\_config` ищет их литералы в исходниках и падает, если находит.



\*\*Д48. Новый повод говорить первым — «ресурсы».\*\* Добавляется к трём поводам Дд14/Д443 на ярус «Полезное» (не чаще раза в час, никогда в игре и созвоне): если владелец даёт долгую фоновую задачу, а свободной памяти меньше порога — одна фраза вида «Сэр, сделаю, но медленно: Chrome занял три с половиной гигабайта». Уточнено владельцем 05.08.2026: Jarvis не просто жалуется, а \*\*называет конкретного виновника и предлагает его закрыть\*\*: «Сэр, мало памяти. Больше всего занимает Chrome — три с половиной гигабайта. Закрыть его?». Пять жёстких ограничений. (1) Сам никогда не закрывает — только по явному «да» владельца. (2) Закрытие чужой программы идёт через гейт как необратимое действие, с талоном согласия на конкретный процесс и конкретный PID. (3) Действуют все правила Д25: если в программе есть несохранённые данные — сказать об этом и ждать второго подтверждения; молчание 15 секунд = не закрывать; зависшее окно — только по явному «да»; молча не убивать никогда. (4) Системные процессы в предложение не попадают вообще — чёрный список в конфиге: проводник, поиск Windows, антивирус, драйверы, любой процесс без окна. (5) Себя в список не включает. Почему именно так: закрытие чужой программы — единственное действие во всём плане, которое может уничтожить чужую работу за пределами Jarvis и не имеет корзины, куда можно было бы вернуть несохранённый документ. Тесты `test\_close\_offer\_needs\_confirm`, `test\_never\_closes\_system\_process`, `test\_close\_unsaved\_second\_confirm`.



\### Статус замеров: все пять закрыты



| Замер | Результат | Статус |

| --- | --- | --- |

| Версии баз | `jarvis.db` = 6, `history.db` нет | Закрыт (Ф8) |

| Тесты | 784 passed / 23,32 с | Закрыт (Ф9) |

| Потоко-локальность согласия | глобальная на процесс | Закрыт (Ф11), Х-B2 подтверждена |

| Модели | все четыре доступны | Закрыт (Ф12) |

| ОЗУ | 277 МБ в ОЗУ / 603 МБ выделено; свободно 270 МБ из-за Chrome | \*\*Закрыт (Ф15, Ф16)\*\* |



> \*\*Главный вывод по памяти.\*\* Нет ни одного непроверенного числа, на котором держится план. Агенты возможны, но при двух условиях: базовое выделение уменьшено с 603 до 350 МБ ленивыми импортами, и число одновременных задач вычисляется по обстановке, а не берётся из константы.

> 



\### 13.6.9 Ключ отозван, способ ввода изменён — Д49 (05.08.2026)



Владелец выпустил новый ключ и \*\*удалил старый в Google AI Studio\*\*. Все zip-архивы в «Загрузках», где ключ лежал открытым текстом, с этого момента безвредны. Самый дорогой пункт фазы 0 закрыт до начала работ.



\*\*Д49. Ключ вводится через встроенный setup-экран, а не руками в файлах.\*\* Проверено по коду: `ui.py:148` показывает `\_show\_setup\_ui()`, когда `config.loader.is\_configured()` ложно; `ui.py:1181` сохраняет введённое через `set\_secret`, то есть атомарной записью read-merge-write, а не прежней деструктивной перезаписью `ui.\_save\_api\_keys`. Значит шаг фазы 0 «создать файл с ключом руками» \*\*отменяется\*\*: владелец после каждой распаковки вставляет ключ в окно и нажимает INITIALISE. Побочная выгода: если в архиве ключа нет, то ни один zip больше не уносит секрет — сама собой закрывается Р-11.



\*\*Ловушка, из-за которой это не заработает само (проверено по коду).\*\* `is\_configured()` считает систему настроенной, если строка ключа просто \*\*не пуста\*\*; живость ключа никто не проверяет. А `\_secrets()` при отсутствии `config/secrets.json` молча читает legacy-файл `config/api\_keys.json`. Итог: в свежераспакованном архиве лежит \*\*мёртвый\*\* ключ, setup-экран не появляется вообще, Jarvis стартует и падает на первом обращении к модели с невнятной ошибкой поставщика. Поэтому один разовый шаг остаётся обязательным: в папке, из которой собираются архивы, значение `gemini\_api\_key` в обоих файлах должно стать пустой строкой.



\*\*Просроченный долг самого кода.\*\* В шапке `config/loader.py` написано: legacy-fallback живёт «лимит 2 недели с 2026-07-19, затем fallback удаляется — правило Старый путь УДАЛЁН, не выключен». Срок истёк \*\*2 августа\*\*, то есть просрочен. Удаление fallback в фазе 0 одновременно убирает ловушку выше и исполняет собственное правило проекта.



\*\*Что стало необязательным.\*\* Перенос `SECRETS\_FILE` из папки проекта в `\~/.jarvis/secrets.json` — одна строка в `config/loader.py`. Выгода: ключ вводится один раз навсегда, а не после каждой распаковки. Цена: нулевая. Решение отложено до фазы 0 по выбору владельца: одна вставка из буфера его не раздражает. Тесты: `test\_setup\_screen\_when\_key\_absent` (пустой ключ в обоих файлах → экран показан), `test\_no\_key\_literal\_anywhere`.



\## 13.7 ВТОРАЯ СИМУЛЯЦИЯ — решения против решений (05.08.2026)



\### 13.7.1 Чем она отличается от первой



Первая симуляция шла \*\*по времени\*\* — один день с 06:55 до 23:40 — и искала места, где документ молчит. Нашла 27 ДЫР.



Вторая идёт \*\*по столкновениям\*\*: каждое из 49 решений сверено с 45 инвариантами и с новыми фактами Ф14–Ф16. Искались не пробелы, а места, где \*\*два правильных правила требуют противоположного\*\*. Это опаснее пробелов: пробел виден сразу, а конфликт вскрывается на 30-м вечере, когда оба правила уже написаны и обвешаны тестами.



Найдено десять противоречий. Восемь из десяти порождены двумя самыми свежими решениями — Д47 (числа памяти) и Д48 (предложение закрыть программу-виновника). Это закономерно: самые молодые решения ни разу не прогонялись против остального документа.



\### 13.7.2 К1 (BLOCKER). Windows переиспользует номера процессов



\*\*Столкновение.\*\* Д48 выписывает талон согласия «на конкретный PID» — против устройства самой Windows.



\*\*Вечер.\*\* Jarvis предложил закрыть Chrome (PID 3272). Владелец отвлёкся на минуту. Chrome за это время закрылся сам, а номер 3272 достался Word с несохранённым документом. Владелец говорит «да» — и теряет текст. Корзины у этого действия нет, отката тоже.



\*\*Д50.\*\* Талон выписывается не на номер, а на тройку «имя процесса + PID + время его старта». Перед самим закрытием тройка сверяется заново; при любом расхождении — отказ и фраза «Сэр, программа уже закрылась сама». Окно жизни талона — 60 секунд.



\*\*Тесты:\*\* `test\_kill\_ticket\_binds\_start\_time`, `test\_kill\_ticket\_expires\_60s`.



\### 13.7.3 К2 (HIGH). Любой голос может подтвердить закрытие



\*\*Столкновение.\*\* Д10 (голос не проверяется, верим всем) против Д48.



\*\*Вечер.\*\* Принятый риск «верим любому голосу» был принят, когда самое худшее — удалённый файл в корзине. Д48 впервые даёт действие \*\*без корзины\*\*, уничтожающее чужую работу за пределами Jarvis. Гость в комнате, реклама из колонки или голос из YouTube-ролика говорит «да» — и чужой несохранённый документ исчез.



\*\*Решение (внутри Д50).\*\* Подтверждение на закрытие принимается \*\*только как ответ на собственный вопрос Jarvis\*\* и только в течение 60 секунд после него. Команда «закрой хром», пришедшая сама по себе, не исполняется сразу — она порождает тот же вопрос с названием программы и объёмом памяти. Это не проверка голоса (её владелец отклонил, Д10), а требование двух реплик подряд в узком окне — случайный звук такое не воспроизводит.



\*\*Тесты:\*\* `test\_kill\_only\_answers\_own\_question`, `test\_bare\_kill\_command\_asks\_first`.



\### 13.7.4 К3 (HIGH). Предложение закрыть съедает весь дневной лимит инициативы



\*\*Столкновение.\*\* Д48 против Д20/I30 (не более 10 инициативных реплик в сутки) и Д43 (бюджет внимания).



\*\*Вечер.\*\* По факту Ф14 свободной памяти мало \*\*почти всегда\*\* (270 МБ). Значит повод «ресурсы» будет срабатывать постоянно: либо Jarvis съест десять реплик до обеда и сорвёт вечернее напоминание о встрече, либо будет десять раз за день клянчить одно и то же про Chrome. Оба исхода — провал.



\*\*Д51.\*\* Ресурсное предложение живёт по собственным правилам: не более \*\*одного раза в сутки\*\*; отказ владельца = тишина по этому поводу до конца дня; счётчик отдельный и не трогает лимит десяти (иначе выживание системы конкурирует с пользой для владельца); не звучит во время полноэкранного режима и активного микрофона чужого приложения (созвон).



\*\*Тесты:\*\* `test\_resource\_offer\_once\_a\_day`, `test\_refusal\_silences\_until\_midnight`, `test\_offer\_not\_counted\_in\_ten`.



\### 13.7.5 К4 (MED). В самом тесном режиме запрещено единственное полезное действие



\*\*Столкновение.\*\* Д48 против режима M2 («меньше 200 МБ — только голос и напоминания»).



\*\*Вечер.\*\* Именно в M2 предложение закрыть прожорливую программу полезнее всего — но по букве режима запрещено. Jarvis будет задыхаться и молчать о единственном выходе.



\*\*Решение.\*\* В M2 разрешён ровно один дополнительный инструмент — ресурсное предложение и закрытие по подтверждению. Он ничего не стоит: ноль вызовов модели, один системный вызов, фраза из шаблона.



\*\*Тест:\*\* `test\_m2\_allows\_resource\_offer`.



\### 13.7.6 К5 (HIGH). Ленивые импорты ломают норматив фаст-паса



\*\*Столкновение.\*\* Ленивые импорты (Х-U4, обязательны из-за 270 МБ) против Д15/I33 (фаст-пас ≤ 3 секунды).



\*\*Вечер.\*\* Первый за сеанс вопрос «что у меня на экране?» теперь платит за `import cv2` (1–3 с) плюс запуск дочернего процесса (0,3–1 с). Тест `test\_fastpass\_timing` начнёт \*\*мигать\*\* — проходить или падать в зависимости от порядка тестов. Мигающий тест хуже отсутствующего: ему перестают верить, а потом перестают верить и соседним.



\*\*Д52.\*\* Норматив разделяется на два: \*\*холодный\*\* — ≤ 5 с (один раз за сеанс, и Jarvis обязан сказать «секунду, сэр», чтобы пауза не читалась как зависание), \*\*тёплый\*\* — ≤ 3 с. Предварительный прогрев зрения разрешён только в режиме 0 и только когда владелец активен.



\*\*Тесты:\*\* `test\_fastpass\_cold\_and\_warm`, `test\_no\_warmup\_in\_m1\_m2`.



\### 13.7.7 К6 (HIGH). Дочерний процесс зрения — тоже `python.exe`



\*\*Столкновение.\*\* Зрение в короткоживущем дочернем процессе (Х-U4) против I28 (один экземпляр) и против способа замера памяти.



\*\*Вечер.\*\* Замок единственного экземпляра, если он смотрит на имя процесса, увидит второй `python.exe` и решит, что запущен второй Jarvis. Отдельно: все будущие замеры через `Get-Process python\*` станут неоднозначными — две строки вместо одной, и бюджет памяти посчитан неверно в обе стороны.



\*\*Решение.\*\* Замок опознаёт экземпляр по файлу `jarvis.lock` с PID внутри, а не по имени процесса; дочерние процессы регистрируются в `mx\_spawned` с родительским PID; бюджет памяти считается как сумма родителя и живых детей.



\*\*Тесты:\*\* `test\_lock\_ignores\_child\_processes`, `test\_memory\_budget\_with\_children` (уже был в списке — теперь у него появилась причина).



\### 13.7.8 К7 (HIGH). Дочерний процесс не имеет права писать в базу



\*\*Столкновение.\*\* Тот же дочерний процесс против Д41 («одна касса» — единственный писатель в SQLite).



\*\*Вечер.\*\* Если дочерний процесс захочет записать свой вызов в метеринг или журнал, он откроет \*\*второе соединение\*\* — ровно то, что шапка `core/consent\_runtime.py` запрещает дословно: транзакция не может охватывать два соединения. Атомарность разрушается молча, и видно это будет только на редком сбое.



\*\*Решение.\*\* Дочерний процесс вообще не знает о базе: он получает на вход путь к картинке и возвращает JSON в `stdout`. Всё пишет родитель. Если дитя умерло — родитель записывает исход `FAILED` с причиной «дочерний процесс погиб», а не виснет в `RUNNING`.



\*\*Тесты:\*\* `test\_child\_never\_opens\_db`, `test\_child\_death\_is\_failed\_not\_hang`.



\### 13.7.9 К8 (HIGH). Пороги памяти будут дребезжать, а правило «никогда молча» сделает это слышным



\*\*Столкновение.\*\* Д47 (пороги 500/200 МБ) против I19 (исчерпание никогда не молчаливое).



\*\*Вечер.\*\* Свободная память в Windows колеблется на сотни мегабайт каждые несколько секунд — вкладка Chrome, предпросмотр в проводнике, индексация. Значение 270 МБ лежит \*\*ровно на границе\*\* между M1 и M2. Jarvis будет переключать режимы каждые полминуты, а по I19 каждый переход обязан быть озвучен. Получится говорящий будильник.



\*\*Д53.\*\* Гистерезис и тишина: вход в режим — только если порог пробит \*\*два замера подряд с интервалом 10 с\*\*; выход — только при превышении порога на 100 МБ. Голосом смена режима объявляется не чаще раза в 30 минут; в журнал — всегда. Отказ выполнить конкретную просьбу озвучивается всегда — I19 касается отказов, а не смены внутреннего состояния.



\*\*Тесты:\*\* `test\_mode\_hysteresis`, `test\_mode\_announce\_throttled`, `test\_refusal\_always\_spoken`.



\### 13.7.10 К9 (MED). Тест «числа только из конфига» упадёт на самом себе



\*\*Столкновение.\*\* Д47 (числа памяти живут в `settings.json`) против теста `test\_memory\_numbers\_from\_config`, который ищет числовые литералы в исходниках.



\*\*Вечер.\*\* Сам тест содержит числа 500 и 200 — иначе он не может проверить поведение. При наивной реализации он найдёт сам себя и упадёт. На это уйдёт полвечера недоумения.



\*\*Решение.\*\* Область поиска — только `core/` и `agent/`; папки `tests/` и `config/` исключены явно, и это записано в самом тесте комментарием. Общее правило для всех grep-тестов плана (их четыре: ключи, числа памяти, прямое чтение секретов, прямые вызовы поставщика).



\*\*Тест:\*\* `test\_grep\_tests\_exclude\_themselves`.



\### 13.7.11 К10 (MED). Через 31 день приёмка перестанет объяснять себя



\*\*Столкновение.\*\* Д45 (дословные фразы владельца хранятся 30 дней) против Д39 (чек-лист приёмки состоит из дословных слов владельца).



\*\*Вечер.\*\* Повторяющаяся задача (один `form\_key`) живёт месяцами. Чистка на 31-й день стирает цитату из `mx\_task\_check`, и на вопрос «почему ты счёл это невыполненным?» Jarvis ответит пустотой. Хуже: приёмка продолжит работать по пустому условию и начнёт принимать всё подряд.



\*\*Д54.\*\* Чистка не удаляет условие, а заменяет его текст на служебную формулировку вида «условие типа \*содержит слово\*, задано владельцем 05.08». Машинная часть условия (`kind`, `arg`) — не речь и не чистится никогда. Если условие осталось без машинной части — задача переходит в `WAITING\_OWNER` с вопросом, а не принимается молча.



\*\*Тесты:\*\* `test\_verbatim\_purge\_keeps\_machine\_part`, `test\_empty\_check\_never\_auto\_passes`.



\### 13.7.12 Что симуляция \*\*подтвердила\*\*



\- Разделение «один рот» (А1–А5) выдержало все десять столкновений: ни одно из них не потребовало второго говорящего.

\- Файл-первый результат (Д11) и талонная модель согласия оказались единственными местами, куда естественно легло решение Д50 — нового механизма придумывать не пришлось.

\- Оффлайн-ядро (Р9) покрывает все десять сценариев без единого вызова модели: ни одно из новых решений Д50–Д54 не требует квоты.



\### 13.7.13 Что симуляция \*\*сломала\*\*



Одно число из Д47 перестало быть простым порогом. Пороги 500/200 МБ теперь обязаны иметь гистерезис (Д53), иначе они не работают на реальной машине владельца, где свободная память колеблется вокруг границы постоянно.



И один вывод о самом Д48: \*\*это единственное решение во всём документе, которое породило четыре конфликта сразу\*\* (К1–К4). Не потому, что оно плохое, а потому, что это первое действие Jarvis \*\*за пределами своего мира\*\*: всё остальное он делает с файлами, текстом и собственной базой, где есть корзина и откат. Если на этапе реализации Д50–Д51 окажутся дорогими, честный запасной вариант — Д48 без закрытия: Jarvis называет виновника и молчит, а кнопку нажимает владелец.



\### 13.7.14 Решение владельца по К1–К4 и по бюджету памяти (05.08.2026, тот же день)



\*\*Д55. Инициатива разворачивается: Jarvis сообщает, жертву называет владелец.\*\* Дословно: «лучше если он просто скажет что заполнилось, а я ему скажу что стоит закрыть или оставить как есть».



Что это значит точно:



\- Jarvis \*\*никогда не предлагает жертву\*\* и никогда не спрашивает «закрыть его?». Он сообщает факт: «Сэр, память заполнена. Больше всего занимает Chrome — три с половиной гигабайта». И замолкает.

\- Закрытие происходит \*\*только по прямой команде владельца\*\*, где программа названа словами («закрой хром»). Это обычное необратимое действие через гейт со всеми правилами Д25: несохранённые данные — второе подтверждение, системные процессы — чёрный список, себя не трогает, молча не убивает никогда.

\- Имя процесса разрешается в PID \*\*в момент исполнения\*\*, а не заранее, и результат проговаривается: «Chrome, четыре процесса, три с половиной гигабайта. Закрыть все?»



Что это снимает:



\- \*\*К1 (BLOCKER) снят полностью.\*\* Заранее выписанного талона больше нет, значит нет и устаревания PID между выдачей и исполнением. Д50 упрощается до одной строки: имя → список PID в момент удара.

\- \*\*К4 снят.\*\* Команда владельца исполняется в любом режиме, включая M2; специальное исключение для M2 не нужно.

\- \*\*К2 снижен до общего уровня Д10.\*\* Любой голос всё ещё может сказать «закрой ворд», но это ровно тот же принятый риск, что и для любой другой команды, и он прикрыт подтверждением и проверкой несохранённых данных. Отдельного механизма не требует.

\- \*\*К3 остаётся\*\*, но в облегчённом виде: ограничивать надо не «предложения», а частоту самих уведомлений о памяти (Д51 без изменений).



Тесты: удаляются `test\_close\_offer\_needs\_confirm`, `test\_kill\_ticket\_binds\_start\_time`, `test\_kill\_ticket\_expires\_60s`, `test\_kill\_only\_answers\_own\_question`, `test\_bare\_kill\_command\_asks\_first`, `test\_m2\_allows\_resource\_offer`. Остаются и переименовываются: `test\_resource\_notice\_is\_informational` (уведомление не порождает ни одного действия и ни одного вопроса), `test\_never\_closes\_system\_process`, `test\_close\_unsaved\_second\_confirm`, `test\_resource\_offer\_once\_a\_day`.



\*\*Д56. Бюджет памяти самого Jarvis — до 550 МБ, старт — не более 350 МБ.\*\* Дословно: «400–550 мб я могу дать моему Джарвису, пусть будет максимум». В `\~/.jarvis/settings.json` блок `memory` получает `max\_total\_mb: 550` рядом со `startup\_budget\_mb: 350`. Смысл разницы: 350 — сколько весит простоящий Jarvis, оставшиеся 200 — запас на реальную работу (зрение, браузер, параллельные задачи). При выходе за 550 — не падение, а отказ брать новую задачу с произнесённой причиной.



\*\*Корректировка факта Ф14.\*\* Замер «270 МБ свободно» сделан в момент, когда в Chrome была открыта тяжёлая страница (слова владельца). Это \*\*пик, а не норма\*\*. Следствия: формулировка в К3 «свободно 270 МБ почти всегда» ошибочна и снимается; пороги M1/M2 (500/200 МБ) остаются, но помечены как \*\*предварительные\*\* до повторного замера в обычных условиях (Ф17, ожидается). Что \*\*не\*\* меняется: стартовые 603 МБ выделенной памяти измерены у самого Jarvis и от чужих программ не зависят — ленивые импорты остаются главной технической задачей по памяти (603 → 350).



\### 13.7.15 Замер в обычных условиях и калибровка порогов (05.08.2026, 15:46)



\*\*Ф17. Свободная память в обычной работе: 2 551 МБ и 2 124 МБ.\*\* Два замера подряд в `cmd`, без тяжёлой страницы в Chrome.



Три вывода, каждый меняет числа в плане:



1\. \*\*Норма в восемь раз выше пика.\*\* Ф14 (270 МБ) был не типичным состоянием, а худшим случаем. Пороги 500/200 из Д47 были откалиброваны по пику и потому бесполезны: в обычной работе они не сработают никогда, а в пике сработают оба одновременно.

2\. \*\*К8 подтверждён экспериментально.\*\* Два замера подряд, сделанные с разницей в десятки секунд, разошлись на \*\*427 МБ\*\*. Это больше, чем весь предполагавшийся зазор гистерезиса (+100 МБ из Д53). Зазор увеличивается вчетверо. Дребезг режимов был бы не гипотезой, а ежедневной реальностью.

3\. \*\*Ограничителем стала не система, а политика.\*\* Узкое место теперь не «сколько оставил Chrome», а потолок 550 МБ, назначенный владельцем (Д56). Это строго лучше: число предсказуемо, проверяемо тестом и не зависит от того, какую страницу открыли.



\*\*Д57. Калиброванные числа памяти.\*\* Заменяют предварительные значения Д47 везде, где те встречаются в документе.



| Ключ в `\~/.jarvis/settings.json` | Было (Д47) | Стало (Д57) | Откуда число |

| --- | --- | --- | --- |

| `mode0\_above\_mb` | 500 | \*\*800\*\* | При 800 свободных Jarvis может вырасти с 350 до потолка 550 и оставить системе 600 |

| `mode2\_below\_mb` | 200 | \*\*400\*\* | Ниже 400 даже стартовые 350 не помещаются без вытеснения на диск |

| `hysteresis\_mb` | 100 | \*\*400\*\* | Измеренный разброс между двумя замерами подряд — 427 МБ |

| `max\_total\_mb` | — | \*\*550\*\* | Назначено владельцем (Д56) |

| `startup\_budget\_mb` | 350 | 350 | Без изменений; сейчас фактически 603 (Ф15) |

| `per\_task\_mb` | 60 | 60 | Без изменений |



\*\*Прямое следствие для агентов.\*\* Честный потолок параллельности теперь выводится арифметикой, а не догадкой: (550 − 350) / 60 = \*\*3 задачи\*\*, и \*\*1 задача\*\*, если работает зрение (дочерний процесс 80–120 МБ). Значение по умолчанию остаётся 2, верхняя граница в конфиге — 10.



\*\*Где править в тексте плана.\*\* Раздел 6.3 «Лестница деградации», блок `memory` в 13.6.8, критерий готовности фазы 2 и Д53: всюду читать 500 → 800, 200 → 400, 100 → 400. Ручная синхронизация не нужна в коде: по Д47 все четыре числа живут в одном файле, а `test\_memory\_numbers\_from\_config` ловит любой литерал, просочившийся в `core/` или `agent/`.



\*\*Новые данные для теста.\*\* `test\_mode\_hysteresis` получает реальную пару значений: последовательность 2551 → 2124 МБ не должна вызывать ни смены режима, ни единого слова вслух.



\*\*Оставшийся пробел по памяти: ни одного.\*\* Все шесть чисел блока `memory` теперь либо измерены, либо назначены владельцем, либо выведены арифметикой из первых двух.



\### 13.7.16 К11: всплеск при запуске чужой программы и честный бюджет (05.08.2026, 15:56)



\*\*К11 (HIGH). Любой запуск тяжёлой программы загоняет Jarvis в аварийный режим на пустом месте.\*\* Формулировка владельца: «при запуске программы у меня оперативка высоко поднимется, а после запуска она станет нормально». Обратная сторона К8: там мы боролись с дребезгом вокруг порога, здесь — с честным, но коротким провалом ниже порога. Зазор в 400 МБ из Д57 здесь не помогает вообще: провал настоящий, просто короткий.



\*\*Д58. Выдержка вместо зазора.\*\* Отменяет `hysteresis\_mb: 400` из Д57 и заменяет его временем:



\- Замер свободной памяти — каждые \*\*10 секунд\*\*.

\- Вход в облегчённый режим — только если память держится ниже порога \*\*непрерывно 60 секунд\*\* (шесть замеров подряд). Один хороший замер обнуляет счётчик.

\- Выход — по \*\*первому же\*\* замеру выше порога. Асимметрия намеренная: ложная тревога стоит дороже поздней реакции.

\- Почему 60 секунд решают проблему: всплеск при запуске программы живёт единицы–десятки секунд, настоящая нехватка памяти длится минутами и часами. Выдержка отсекает первое и пропускает второе, чего пороговый зазор не умеет принципиально.



\*\*Д59. Голос не деградирует никогда — инвариант.\*\* Лестница деградации по памяти переписывается так, чтобы ошибка режима была незаметна:



| Режим | Голос, диалог, напоминания | Фоновые задачи | Зрение и браузер |

| --- | --- | --- | --- |

| M0 — обычный | Работают | До 3 одновременно | Работают |

| M1 — тесно | \*\*Работают без изменений\*\* | Новые не берёт, идущие доделывает | По подтверждению |

| M2 — в обрез | \*\*Работают без изменений\*\* | Не запускаются | Не запускаются |



Следствие: даже если режим переключился ошибочно, владелец этого не замечает, пока не попросит именно фоновую задачу или зрение. Объявление смены режима вслух отменяется полностью (Д53 урезан): вслух звучит только отказ от конкретной просьбы с причиной.



\*\*Д60. Честный бюджет по процессам.\*\* Одно число на всё было ошибкой: зрение и браузер — отдельные процессы и в 550 МБ главного процесса вмещаться не должны.



| Процесс | Потолок | Сколько живёт | Из чего складывается |

| --- | --- | --- | --- |

| Главный (`max\_total\_mb`) | \*\*550 МБ\*\* | Весь вечер | Ядро 300–350 (Python, Tk, аудио, сеть, SQLite) + до трёх задач по 60 + пики разбора ответов |

| Зрение (`vision\_child\_mb`) | \*\*250 МБ\*\* | Секунды | cv2 с зависимостями 120–180 + numpy + снимок экрана |

| Браузер (`browser\_child\_mb`) | \*\*500 МБ\*\* | Пока открыт | Chromium через playwright; это не код Jarvis, а браузер |



Итого: обычный вечер — \*\*350–450 МБ\*\*; самый тяжёлый мыслимый момент (три задачи + зрение + открытый браузер одновременно) — \*\*около 1,3 ГБ\*\* при измеренных 2,1–2,6 ГБ свободных (Ф17). Запаса хватает; увеличивать 550 не требуется.



\*\*Обновлённые пороги\*\* (заменяют таблицу Д57 в части режимов): `mode1\_below\_mb: 700`, `mode2\_below\_mb: 350`, `sample\_seconds: 10`, `sustain\_seconds: 60`. Ключи `mode0\_above\_mb` и `hysteresis\_mb` удаляются — выдержка делает их ненужными (правило «старый путь УДАЛЁН, не выключен»).



\*\*Собственный потолок важнее системного.\*\* Проверка `max\_total\_mb` работает всегда и мгновенно, без всякой выдержки: это собственное потребление Jarvis, оно не дрожит и не зависит от чужих программ. Системные пороги — вторичный тормоз, а не основной.



Тесты: `test\_mode\_needs\_60s\_sustain` (пять плохих замеров и один хороший → режим не меняется), `test\_launch\_spike\_does\_not\_switch` (профиль 2500 → 300 → 300 → 2200 МБ → ни смены режима, ни слова), `test\_exit\_is\_immediate`, `test\_voice\_never\_degraded` (во всех трёх режимах голос и напоминания доступны), `test\_child\_budgets\_separate` (зрение и браузер не учитываются в 550), `test\_no\_mode\_announcement` (смена режима не порождает речи). Удаляются `test\_mode\_hysteresis` и `test\_mode\_announce\_throttled`.



\### 13.7.17 Четыре числа, которые оставались пустыми



Заполнены архитектором — мнение владельца здесь не требуется, требуются замеры, и они есть.



\*\*1. `test\_fastpass\_timing`.\*\* Холодный — ≤ 5000 мс, тёплый — ≤ 3000 мс (Д52). Замер от конца речи владельца до начала речи Jarvis, а не до конца внутренней работы. Третье число: если холодный путь превысил 1500 мс, Jarvis обязан произнести «секунду, сэр» — молчаливое ожидание длиннее полутора секунд считается дефектом.



\*\*2. `test\_call\_budget\_norms`.\*\* Потолки вызовов к моделям, жёсткие, не рекомендательные:



\- Фаст-пас — \*\*0\*\* вызовов. Любой вызов в фаст-пасе — падение теста.

\- Простой вопрос — ≤ \*\*2\*\*.

\- Обычная задача агента — ≤ \*\*8\*\*.

\- Сложная задача с переделкой — ≤ \*\*15\*\*. Превышение → `WAITING\_OWNER`, не молча.

\- Сутки: ≤ \*\*120\*\* вызовов к моделям с лимитом 500 RPD (четырёхкратный запас на ошибки и повторы) и ≤ \*\*2000\*\* к Gemma из 14 400.

\- Повторы при ошибках — ≤ 3 на задачу и ≤ 20 в сутки, входят в потолки выше, а не сверх них.



\*\*3. `test\_quality\_thresholds`.\*\* Приёмка без процентов и без оценок модели:



\- `DONE` — выполнены \*\*все 100%\*\* обязательных пунктов чек-листа. Никаких «восемьдесят процентов достаточно».

\- `PARTIAL` — выполнен хотя бы один, но не все.

\- `FAILED` — ни одного.

\- Пункт со статусом «неизвестно» не считается выполненным и не считается проваленным: задача становится `PARTIAL`, в отчёте перечисляются именно неизвестные пункты.

\- Пустой чек-лист никогда не даёт `DONE` автоматически (Д54).



\*\*4. Состав `mx\_checkpoint\_metric`.\*\* Пишется кодом в конце каждого вечера работы, вручную не заполняется никогда: `ts`, `phase`, `step`, `ram\_main\_mb`, `ram\_children\_mb`, `startup\_ms`, `fastpass\_cold\_ms`, `fastpass\_warm\_ms`, `tests\_total`, `tests\_failed`, `suite\_seconds`, `calls\_paid\_today`, `calls\_gemma\_today`, `tasks\_done`, `tasks\_partial`, `tasks\_failed`, `db\_size\_mb`, `db\_user\_version`. Восемнадцать полей — это одна строка в сутки; за 90 вечеров таблица останется меньше ста килобайт.



\### 13.8 Готовность к внедрению (05.08.2026, 16:04)



\*\*Проектирование закрыто.\*\* Итог: 27 дыр симуляции дня, 60 находок первого аудита, 82 находки второго, 11 конфликтов второй симуляции, 17 измеренных фактов, 60 решений владельца. Незакрытых BLOCKER нет. Пустых чисел нет.



\*\*Что сознательно оставлено открытым\*\* (не блокирует старт):



\- О23 — подбюджеты внимания. `config/budgets.yaml` создаётся с `enabled: false`; решение принимается после месяца эксплуатации, на реальных цифрах.

\- Сигнатура `gate.dispatch` — фактически решена: `ctx=None` добавляется в фазе 1б, старые вызовы не ломаются.

\- Дизайны A/B/C интерфейса (Д32) — после всего.

\- Автозапуск (Д9) — последним шагом, как решил владелец.



\*\*Первый шаг фазы 0 — гигиена ключа\*\* (≤ 40 минут, Д49):



1\. Удалить просроченный legacy-fallback на `config/api\_keys.json` из `config/loader.py` (срок истёк 2026-08-02 по собственной шапке файла).

2\. Вычистить литералы ключа из `ARCHITECTURE.md:114` и обоих json.

3\. Написать `test\_setup\_screen\_when\_key\_absent` и `test\_no\_key\_literal\_anywhere`.

4\. Прогнать весь набор (сейчас 784 теста, 23,3 с) — должно стать 786 зелёных.

5\. Zip в Загрузки по шаблону `jarvis\_2026-08-05\_faza0\_shag1.zip`.



Критерий успеха шага: удалить `config/secrets.json`, запустить `python main.py` — должен показаться экран «INITIALISATION REQUIRED», а не упасть с `ConfigError`.

