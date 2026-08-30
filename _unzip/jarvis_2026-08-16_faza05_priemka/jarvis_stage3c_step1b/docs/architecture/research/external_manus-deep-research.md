# Внешний отчёт: Manus AI deep research (июль 2026) — конденсат с оценкой достоверности

> ⚠️ Качество смешанное: полезные идеи вперемешку с сомнительными утверждениями и слабыми
> источниками (SEO-блоги, Facebook, Reddit, нерелевантные ссылки). Не принимать без
> верификации — вердикты в `external_verification-verdicts.md`.

## Полезное (согласуется с другими источниками)
- APSW + SQLite3MultipleCiphers как шифрование, совместимое с расширениями (совпадает с
  отчётом Claude).
- Everything SDK для взаимодействия со службой; sidecar-паттерн для привилегированного
  чтения USN с узким IPC.
- uiautomation + CacheRequest/TreeScope; --force-renderer-accessibility для Electron;
  гибрид UIA + Windows.Media.Ocr.
- UFO²/Windows Agent Arena: семантический grounding, инкрементальное кэширование
  UI-деревьев (обновлять только изменившиеся ветки по WinEventHook), self-correction loop
  (действие не дало ожидаемого изменения UI-состояния за таймаут → откат и альтернативный
  путь).
- Prefix Caching (статический префикс промпта + динамика в конце); Optimistic Locking
  (проверка mtime+hash между preview и apply); Saga с компенсациями.
- Сегментация активности: графы близости (VS Code + браузер + терминал в одном каталоге =
  одна Project Session); «текущий проект» = кластер файлов с наибольшим весом изменений
  за 30 мин; heartbeat slicing (пауза >5 мин или резкая смена фокуса = граница сегмента).
- Windows-демон: Worker Recycling, SetThreadExecutionState для критических операций,
  USN catch-up после resume.
- Strangler: «удорожание» старого пути + жёсткие дедлайны как форсинг-функции.

## Сомнительное / вероятные галлюцинации (проверено верификацией)
- «Always-on тарифный план Gemini с дисконтом на idle» — источник: SEO-блог laozhang.ai;
  в официальном прайсинге отсутствует.
- «EFS работает на Windows 11 Home» — противоречит отчёту Claude (Pro/Enterprise only).
- «AF_UNIX поддерживается в Windows 10+ для Python IPC» — CPython socket.AF_UNIX на
  Windows не поддержан.
- «Everything SDK — MIT-подобная лицензия» — источник Wikipedia, не текст лицензии.
- tus-протокол для локальных файловых операций — нерелевантен (это HTTP-upload протокол).
- Ссылки [41]-[65] частично мусорные (Facebook-пост, Reddit r/Architects про архитекторов
  зданий, нерелевантные YouTube).
- «Латентность STT <200 мс» и «gc.collect() как лекарство от утечек» — упрощения.

## Итог
Брать: инкрементальное кэширование UI-деревьев, self-correction loop, графы близости для
сегментации, optimistic locking, форсинг-функции strangler. Отклонить: Always-on тариф,
EFS-на-Home, AF_UNIX, tus. Tool RAG — только между итерациями (см. вердикты).
