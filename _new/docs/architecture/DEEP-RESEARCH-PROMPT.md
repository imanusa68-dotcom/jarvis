# Deep-research промпт для внешних AI

> Скопируйте текст ниже целиком в AI с функцией глубокого поиска (Gemini Deep Research,
> ChatGPT Deep Research, Perplexity и т.п.). Промпт самодостаточен.

---

Ты — опытный software architect, проводящий глубокое исследование для конкретного проекта.
Работай только как архитектор ПО и системный дизайнер: анализируй архитектуру, инженерные
решения, open-source код и публикации. Ищи первоисточники на английском (документация,
GitHub, инженерные блоги, arXiv, post-mortem'ы), но итоговый отчёт пиши на русском.

## Контекст проекта

Персональный AI-ассистент уровня «Jarvis» для ОДНОГО пользователя на его собственном ПК
(Windows 11). Локальная установка, сопровождает один разработчик, горизонт жизни 5+ лет.
Естественный язык (голос — русский! — и текст) — основной интерфейс к компьютеру. Система
поддерживает актуальную модель рабочего окружения (файлы, окна, приложения, проекты,
история, связи) и выполняет обычные пользовательские действия (открыть, найти, переименовать,
переместить, организовать, автоматизировать) с подтверждениями и откатом.

Архитектура уже спроектирована; твоя задача — НЕ предлагать её заново, а собрать материалы,
которые её улучшат или опровергнут отдельные решения. Ключевые принятые решения:

- Python 3.12+/asyncio; два долгоживущих процесса (движок-демон idle ≤150 МБ / <1% CPU +
  тонкий UI-клиент по WebSocket) + ленивый ingest-воркер (фоновый приоритет
  PROCESS_MODE_BACKGROUND_BEGIN + EcoQoS) + Playwright в отдельном MCP-процессе (stdio) +
  эфемерные воркеры (эмбеддинги).
- Собственный компактный агентный цикл (~2k строк) вместо фреймворков (LangChain/CrewAI
  отвергнуты осознанно): трёхуровневый роутер (прямой ответ / лёгкий цикл ≤3 tool-вызовов /
  Plan-and-Execute), план — данные в SQLite (execution_log: intent до, результат после),
  durable-подтверждения как interrupt (WAITING_FOR_SIGNAL + хэш-пиннинг payload + TTL),
  бюджеты (шаги/replan/стоимость) в ядре, steering-очередь.
- Единственная точка исполнения dispatch() для всех путей (голос, текст, план, плагины):
  пайплайн deny → circuit-breaker-пол → allow → ask → профиль; 4 уровня риска (read/
  reversible/external/irreversible); capability-объекты ScopedRoot вместо строк путей.
- Откат: saga-журнал с компенсациями + staging-копии + undo-стек; нижний слой —
  IFileOperation (корзина, undo-record Explorer, выделенный COM STA-поток) и ReplaceFile.
- Хранение: SQLite (WAL, FTS5, sqlite-vec) — jarvis.db (вечное) + history.db (вытесняемое,
  писатель — ingest-воркер); миграции PRAGMA user_version; бэкап backup API/VACUUM INTO.
- World model: Everything (voidtools) как основной файловый индекс (его служба решает
  elevation для MFT/USN), свой USN-ридер — резерв; окна — SetWinEventHook; активность —
  heartbeat-слияние (ActivityWatch); только производные данные, доступ через модуль-шлюз;
  квоты/retention/индикатор наблюдения.
- Память: капированные core-блоки в промпте (генерируются из БД) + эпизоды (append-only) +
  би-темпоральные семантические факты (valid_from/valid_to) + процедурные сценарии;
  гибридный поиск BM25+вектор (RRF); sleep-time консолидация в простое локальной моделью.
- LLM-слой: два адаптера над официальными SDK (google-genai; openai-совместимый для
  OpenAI/Ollama/LM Studio/OpenRouter/llama.cpp); registry.yaml «роль→модель» с fallback-
  цепочками; LiteLLM отвергнут как зависимость.
- Голос: интерфейс VoiceSession (семантические события + capability-флаги); драйвер №1 —
  Gemini Live API (resumption-хэндлы, GoAway, compression); драйвер №2 — half-cascade:
  openWakeWord («hey jarvis») + Silero VAD + GigaAM (русский STT, MIT, ONNX) + Silero TTS /
  edge-tts; цель отклика ≤500 мс на лёгком пути; barge-in в контракте.
- Плагины: in-process на pluggy + manifest.toml (ленивая активация, без импорта кода),
  MCP — граница для чужого/тяжёлого; каталог ~15 типизированных событий шины (frozen,
  аддитивная эволюция, генерируемый EVENTS.md); команды по шине запрещены.

## Что уже изучено (не повторяй, только углубляй)

Claude Code / Claude Agent SDK (цикл, permission-пайплайн), LangGraph (interrupt/checkpoint),
AutoGen v0.4, CrewAI, Semantic Kernel (депрекация планировщиков), smolagents, Open
Interpreter, OpenManus, MCP; MemGPT/Letta (sleep-time), Zep/Graphiti, mem0 v3, CoALA,
Generative Agents; Everything (MFT/USN), ActivityWatch, screenpipe, Windows Recall
(редизайн), Rewind; Home Assistant (шина, service registry, ConfigEntry, Recorder), VS Code
(extension host, activation events), Obsidian, pluggy, Raycast; Terraform plan/apply,
Ansible check mode, saga/компенсации, Nautilus undo, PowerRename, IFileOperation/ReplaceFile,
WASI capabilities, Zellij/Extism; LiteLLM (паттерны и причины отказа), OpenRouter, any-llm,
Continue.dev (роли моделей), GBNF/llama.cpp; Pipecat, LiveKit Agents, Wyoming, Willow,
openWakeWord, Silero VAD/TTS, GigaAM, faster-whisper, Piper (GPL), XTTS (CPML); Mycroft→OVOS,
Rhasspy v2→v3, Siri (Linwood), Cortana, AutoGPT/BabyAGI, Copilot Workspace; PEP 810 (lazy
imports), EcoQoS, SQLite-тюнинг (WAL/FTS5/sqlite-vec), fastembed, психология poll-циклов;
пирамида тестирования агентов (aider mock_send, gptme EvalSpec, Pipecat Evals, crash-injection
SQLite, hypothesis stateful, import-linter, pass^k Anthropic); наблюдаемость (OTel gen_ai.*,
llm CLI Уиллисона, Datasette, flight recorder); жизненный цикл (uv.lock, Task Scheduler,
Squirrel-слоты, user_version-миграции, backup API, restore-тест, лицензии стека).

## Направления исследования (в порядке приоритета)

### 1. Открытые вопросы, блокирующие детали реализации

1.1. **SQLCipher-стек на Python в 2026**: состояние sqlcipher3-wheels / apsw-sqlite3mc /
SQLite3MultipleCiphers; совместимость шифрованных БД с FTS5 и загружаемыми расширениями
(sqlite-vec); измеренные накладные расходы (%, латентность); опыт долгоживущих проектов.
Альтернатива: EFS на каталоге данных — поведение с WAL/SHM-файлами, доступность в Windows 11
Home vs Pro.

1.2. **USN Journal без прав администратора**: точные привилегии для FSCTL_READ_USN_JOURNAL
vs FSCTL_ENUM_USN_DATA; возможности FSCTL_READ_UNPRIVILEGED_USN_JOURNAL (ограничения,
достаточность для инкрементального индекса); как именно устроена служба Everything (клиент
1.4/1.5 ↔ служба); лицензионные условия использования Everything SDK/ES.exe из стороннего
приложения; паттерны крошечной elevated-службы-хелпера на Python с узким IPC.

1.3. **Gemini Live API: эволюция 2026**: актуальные лимиты сессий/resumption, изменения
протокола, function calling внутри live-сессий, цены; опыт долгоживущих
always-on-интеграций; сравнение с OpenAI Realtime и новыми realtime-моделями (появились ли
у кого-то resumption, дешёвый idle, серверный wake word).

1.4. **UI Automation для чтения экрана по требованию**: практика CacheRequest/TreeScope на
Python (comtypes vs pywin32 vs uiautomation-пакет), реальные латентности, работа с Electron/
Chromium-приложениями; когда OCR (например, через Windows.Media.Ocr) выигрывает.

### 2. Архитектурные идеи, которые могут улучшить проект

2.1. **Персональные desktop-агенты 2025–2026**: новые open-source проекты уровня
OS-ассистента (агентные лаунчеры, «AI shell», локальные копилоты рабочего стола, проекты
вокруг Windows Agent Arena / UFO² от Microsoft) — какие инженерные решения у них лучше
наших: представление состояния рабочего стола для LLM, grounding кликов, кэширование
UI-деревьев, восстановление после ошибок GUI-действий.

2.2. **Контекст-инжиниринг для tool-heavy агентов**: свежие практики динамического скоупа
инструментов, tool search/lazy loading, KV-cache-дружелюбные промпты, компактация длинных
сессий, recitation; цифры деградации выбора при N инструментов.

2.3. **Экстракция знаний из активности пользователя**: как проекты превращают поток событий
(файлы, окна, время) в полезные факты/привычки без LLM-вызова на каждое событие; эвристики
сегментации «рабочих сессий»; определение «текущего проекта» пользователя.

2.4. **Надёжность многошаговых файловых операций**: свежие работы/код по транзакционным
batch-операциям на десктопе, resumable file operations, конфликты с параллельными
изменениями (файл изменился между preview и apply — стратегии).

2.5. **Русскоязычный голосовой стек**: новые локальные STT/TTS для русского (после GigaAM
v3 и Silero v5), стриминговые русские STT, качество русских голосов новых открытых TTS
(в т.ч. neural codec модели), latency-инженерия каскадов.

### 3. Практический опыт (post-mortem'ы и разборы)

3.1. Долгоживущие соло-проекты десктоп-софта: что позволило одному человеку сопровождать
систему 5+ лет (архитектурные и процессные практики).
3.2. Опыт эксплуатации always-on Python-демонов на Windows: утечки, GIL vs аудио,
ProactorEventLoop, сон/гибернация ноутбука (как переживать suspend/resume — таймеры, сокеты,
USN-catch-up).
3.3. Инциденты «AI-ассистент повредил данные пользователя» — публичные разборы: какой
механизм защиты отсутствовал, что сработало бы.
3.4. Опыт миграций strangler в соло/малых проектах: что заставляло «вечный гибрид»
завершиться.

## Формат результата

Инженерный отчёт на русском: по каждому направлению — (а) что найдено (проекты, статьи,
код — с URL); (б) что из этого подтверждено первоисточником, а что — твоя оценка;
(в) конкретная рекомендация для проекта: «взять / взять идею / не брать» с обоснованием;
(г) противоречия с принятыми решениями проекта, если найдены — с аргументами. Приоритет —
глубина по направлениям 1.x (они блокируют реализацию), затем 2.x, затем 3.x. Не пересказывай
маркетинг; ищи, КАК устроено внутри и ПОЧЕМУ.


