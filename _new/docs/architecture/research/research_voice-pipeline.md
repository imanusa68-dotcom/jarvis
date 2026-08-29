# Голосовой конвейер для русскоязычного desktop-ассистента

**Wake word.** Для слова «Джарвис» ситуация удобная: openWakeWord из коробки поставляет
предобученную модель «hey jarvis» (подтверждено README). Код — Apache 2.0, предобученные
модели — CC BY-NC-SA 4.0 (для личного проекта не проблема). На Windows — через onnxruntime;
кадры по 80 мс (16 кГц PCM), одно ядро Raspberry Pi 3 тянет 15–20 моделей — на десктопном
CPU нагрузка пренебрежима. Кастомные слова обучаются на синтетике через Colab (~час).
Porcupine: бесплатный тариф пригоден для персонального использования, но русского нет;
обходной путь — английское «Hey Jarvis» (Picovoice рекомендует фразы от 6 фонем — «Эй
Джарвис» надёжнее одиночного «Джарвис»). Для 5-летнего проекта openWakeWord предпочтительнее:
нет вендор-лока и платформо-специфичных .ppn.

**VAD.** Silero VAD — фактический стандарт: MIT, ~2 МБ, <1 мс на чанк (512 сэмплов / 32 мс
при 16 кГц), ONNX без PyTorch. VADIterator — стейт-машина с гистерезисом (порог 0.5, выход
= порог − 0.15, min_silence_duration_ms, speech_pad_ms). Asyncio-паттерн: буферизовать чанки
при речи, считать кадры тишины, эмитить «конец фразы» после ~300–500 мс.

**Локальный STT для русского.** Главная находка — **GigaAM** (Сбер): MIT с v2 (дек. 2024),
официальный ONNX, WER на русском ~вдвое лучше Whisper large-v3 (бенчмарк Habr: 3.3% CPU
против 7.9% у large-v3-turbo на RTX 4090; v3-e2e-rnnt — с пунктуацией и нормализацией).
CTC-модель 225 МБ, работает на CPU через onnx-asr; 700K часов русской речи. faster-whisper:
large-v3-turbo int8 — ~1.5 ГБ VRAM, ~2.7× быстрее large-v3, для русского turbo близок к
полному large-v3; RTX 4070 — ~12× реального времени; CPU large-v3 int8 — RTF ~2.5
(непригодно для диалога), medium — RTF ~1.0 (на грани). Русские файнтюны с CT2-весами:
antony66/whisper-large-v3-russian (WER 9.84→6.39), coriollon turbo-russian (пунктуация).
Vosk — стриминговые partial <500 мс на CPU, но проигрывает в точности. Moonshine русского
НЕ поддерживает. Итог: half-cascade — GigaAM v3 (ONNX, CPU) как основной русский STT,
faster-whisper turbo при наличии GPU.

**TTS — узкое место локального русского стека.** Kokoro русского не имеет. XTTS v2 —
русский живой, но CPML (строго некоммерческая), Coqui закрылась в 2024, апстрим мёртв
(поддерживаемый форк — coqui-tts от idiap) — тупиковая ветка для 5-летнего горизонта.
Piper: разработка переехала в OHF-Voice/piper1-gpl (GPL-3.0 из-за espeak-ng; старый MIT
rhasspy/piper архивирован окт. 2025); русские голоса ru_RU denis/dmitri/irina/ruslan,
medium-качество, быстрый CPU, просодия «синтетичная». **Silero TTS v4/v5** — флагманский
локальный русский: естественные голоса (xenia и др., 48 кГц), автоударения и омографы,
быстрый CPU, вплоть до SAPI5; лицензия CC BY-NC (для личного проекта ок). **edge-tts** —
бесплатный облачный: нейроголоса ru-RU-Dmitry/SvetlanaNeural, async-стриминг; риск ToS и
поломки эндпоинта (библиотека стабильна ~3 года). Платные: Yandex SpeechKit — эталон
русского; ElevenLabs Flash — TTFB ~75 мс.

**Фреймворки.** Pipecat подтверждает целевую архитектуру: типизированные фреймы через
цепочку процессоров, GeminiLiveLLMService как S2S-узел, каскад с задержкой 500–800 мс за
счёт конвейеризации. Документированный нюанс: Gemini Live-сервис в Pipecat НЕ эмитит
UserStarted/StoppedSpeakingFrame (API даёт только interrupted) — прямой аргумент за
capability-флаги в VoiceSession. Pipecat — источник идей (его transports/workers избыточны
для single-user десктопа). LiveKit Agents: console-режим локально, встроенный Silero VAD,
StreamAdapter для нестриминговых STT — справочник паттернов. Wyoming — микропротокол
(JSONL + PCM поверх TCP/stdio), роли wake/STT/TTS как сменные сервисы.

**Latency-бюджет каскада**: VAD 30–50 мс → end-of-utterance 100–900 мс (дефолт ~500 мс
тишины) → финализация STT 200–950 мс → LLM TTFT 300–500 мс → TTS TTFB 80–150 мс.
Воспринимаемая задержка = EOU + LLM TTFT + TTS TTFB; цель ~800 мс, отлично <500 мс.
Рычаги: sentence-chunked TTS (пунктуация конца предложения + минимум ~10 символов + флаш
по окончании стрима; −200–500 мс), кэш готовых аудиофраз («Слушаю», «Сделано»; −240 мс и
полное устранение TTFB), «звук размышления». Barge-in — тройная отмена (флаш TTS-очереди,
отмена LLM-генерации, рестарт STT) — проектируется в интерфейсе VoiceSession с самого
начала.

**Гибрид wake→Live**: локальный детектор гейтит открытие тарифицируемой WebSocket-сессии
(обсуждение Gemini CLI #19830, гайд Picovoice); официального hotword на стороне Live API
нет. Подводные камни: задержка установления сессии (сотни мс — маскируется звуковым
сигналом), ложные срабатывания открывают платные сессии (refractory-период + опционально
verifier-модель), нужен idle-timeout закрытия.

## Идеи

- **GigaAM v3 как основной русский STT** [github.com/salute-developers/GigaAM]: MIT, ONNX,
  WER ~вдвое лучше Whisper large-v3 на русском, 225 МБ, реальное время на CPU | high
- **openWakeWord с готовой моделью «hey jarvis»** [github.com/dscripka/openWakeWord]:
  нулевая стоимость входа, нет вендор-лока | high
- **Silero VAD + VADIterator как ядро endpointing** [github.com/snakers4/silero-vad]:
  один VAD-слой на гейтинг Gemini Live и на каскад | high
- **Silero TTS v4/v5 как локальный русский голос** [github.com/snakers4/silero-models]:
  лучшее локальное русское качество без GPU; CC BY-NC | high
- **edge-tts как облачный TTS-fallback** [github.com/rany2/edge-tts]: бесплатные нейроголоса
  ru-RU, async-стриминг | medium (риск ToS)
- **Sentence-chunked TTS-мост**: −200–500 мс воспринимаемой задержки | high
- **Кэш аудио частых фраз**: устраняет TTS полностью на самых частых репликах | high
- **Гибрид wake→Live-сессия по требованию**: приватность + экономия квоты | high
- **Vosk-partials + GigaAM-finals**: «живые субтитры» распознавания в UI | medium
- **Русские файнтюны faster-whisper (antony66, coriollon)**: замена модели без кода при
  GPU | medium
- **Wyoming как образец микропротокола** | medium
- **Pipecat: документированное отсутствие turn-фреймов у Gemini Live** — подтверждение
  правильности capability-флагов в VoiceSession | high (как источник идей)

## Анти-паттерны

- **Porcupine для русского wake word**: язык не поддерживается, вендор-лок.
- **Kokoro / Moonshine в русском конвейере**: русского нет ни в TTS, ни в STT.
- **faster-whisper large-v3 на CPU в диалоге**: RTF ~2.5 — ответ ждёт ~12 с.
- **XTTS v2 как долгосрочная основа**: CPML некоммерческая, апстрим мёртв.
- **Cloud-ASR вместо локального wake word**: дорого, медленно, уничтожает приватность.
- **Одиночное короткое слово-триггер**: мало фонем → ложные срабатывания → платные сессии.
- **Опора на turn-события при S2S-драйвере**: Gemini Live не даёт границ хода — закрывать
  capability-флагом.
- **Barge-in задним числом**: тройная отмена дорого вкручивается потом; поломки «молчаливые».
- **Дефолтный endpointing ~500 мс + нестриминговый STT**: задержки складываются и съедают
  бюджет до LLM.
- **Пиннинг архивного MIT rhasspy/piper**: код заморожен, багфиксы только в GPL-форке.

## Источники

- https://github.com/dscripka/openWakeWord
- https://picovoice.ai/blog/complete-guide-to-wake-word/
- https://github.com/snakers4/silero-vad
- https://github.com/salute-developers/GigaAM
- https://huggingface.co/ai-sage/GigaAM-v3
- https://habr.com/ru/articles/1002260/
- https://github.com/SYSTRAN/faster-whisper/issues/1030
- https://huggingface.co/antony66/whisper-large-v3-russian
- https://github.com/OHF-Voice/piper1-gpl
- https://github.com/snakers4/silero-models
- https://huggingface.co/coqui/XTTS-v2
- https://github.com/rany2/edge-tts
- https://github.com/pipecat-ai/pipecat
- https://reference-server.pipecat.ai/en/latest/_modules/pipecat/services/google/gemini_live/llm.html
- https://docs.livekit.io/agents/server/startup-modes/
- https://www.home-assistant.io/integrations/wyoming/
- https://huggingface.co/blog/dvalle08/voice-agent-latency-playbook
- https://futureagi.com/blog/how-to-optimize-voice-agent-latency-2026/
- https://github.com/google-gemini/gemini-cli/discussions/19830

