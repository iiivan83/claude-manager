# Граф зависимостей модулей

Дата генерации: 2026-03-29
Источники: BRD (dev/docs/brd/brd-user-journeys.md), CLAUDE.md
Корректировки: dev/docs/brd/brd-validation-report_29-03-23-47.md

## Ключевые решения из валидации BRD

Следующие решения из отчёта валидации влияют на декомпозицию:

- **Два состояния вместо трёх** — убрано Состояние 1 «Чистый лист». Бот всегда либо подключён к сессии, либо в режиме /all. При запуске/перезапуске без сохранённой привязки — автоматически в /all
- **/new сразу запускает процесс Claude** — process_manager создаёт процесс немедленно при /new, не ждёт первого сообщения
- **/N ищет сессию везде** — не только в дневном реестре, но и среди всех видимых сессий (session_reader + daily_session_registry)
- **/stop прерывает цикл ретраев** — process_manager должен поддерживать отмену цикла повторных попыток
- **Автоочистка received_files/** — при запуске бот удаляет файлы старше 7 дней
- **При запуске/перезапуске — автоматически в режим /all** — bot.py при инициализации устанавливает режим мониторинга

## Список CJM

- **CJM-01** — Первый запуск и настройка
- **CJM-02** — Отправка текстового сообщения
- **CJM-03** — Отправка фотографии или файла
- **CJM-04** — Создание новой сессии (/new)
- **CJM-05** — Просмотр списка сессий (/sessions)
- **CJM-06** — Переключение на сессию (/N)
- **CJM-07** — Мониторинг всех сессий (/all)
- **CJM-08** — Остановка Claude (/stop)
- **CJM-09** — Защита от двойного запуска бота

## Слой 0 (нет зависимостей от других модулей проекта)

- **config** — загрузка настроек из .env (токен бота, разрешённые пользователи, рабочая директория). CJM: 01. Зависимости: нет
- **message_splitter** — разбивка длинных сообщений (>4096 символов) на части с починкой HTML-тегов, конвертация Markdown в HTML. CJM: 02, 03. Зависимости: нет

## Слой 1 (зависит от слоя 0)

- **claude_runner** — обёртка для запуска Claude Code CLI через subprocess, протокол stream-json (отправка через stdin, чтение из stdout). CJM: 02, 03, 04. Зависимости: config
- **session_reader** — чтение файлов сессий Claude с диска (~/.claude/projects/...), извлечение метаданных (время создания, первое сообщение, ID сессии). CJM: 05, 06, 07. Зависимости: config
- **daily_session_registry** — дневная нумерация сессий (#1, #2, #3...), сброс нумерации в полночь, персистентность через daily_sessions.json. CJM: 02, 03, 04, 05, 06. Зависимости: config

## Слой 2 (зависит от слоёв 0-1)

- **session_manager** — связка chat_id с session_id, сохранение/восстановление привязок через sessions.json, управление состояниями (подключён к сессии / режим /all). CJM: 02, 03, 04, 05, 06, 07. Зависимости: config, daily_session_registry, session_reader
- **process_manager** — управление процессами Claude (создание, остановка, ретраи до 10 раз), координация с watcher через механизм паузы, отправка сообщений в процесс и чтение ответов. CJM: 02, 03, 04, 08. Зависимости: claude_runner, config

## Слой 3 (зависит от слоёв 0-2)

- **session_watcher** — мониторинг файлов сессий в реальном времени (каждые 2 секунды), отслеживание новых ответов Claude, координация с process_manager для избежания дублей. CJM: 02, 03, 07. Зависимости: config, session_reader, daily_session_registry, session_manager

## Слой 4 (зависит от слоёв 0-3)

- **bot** — обработчики Telegram-команд (/new, /sessions, /all, /stop, /N), приём сообщений и фото, проверка доступа, отправка ответов в Telegram, управление состоянием пользователя, автоочистка received_files/. CJM: 01, 02, 03, 04, 05, 06, 07, 08. Зависимости: config, message_splitter, session_manager, process_manager, session_watcher, daily_session_registry, session_reader

## Слой 5 (зависит от слоёв 0-4)

- **main** — точка входа: проверка настроек, защита от двойного запуска (файл-замок bot.pid через fcntl.flock), восстановление состояния, запуск Telegram polling. CJM: 01, 09. Зависимости: config, bot, session_manager

## Порядок реализации

1. [параллельно] config, message_splitter
2. [параллельно] claude_runner, session_reader, daily_session_registry
3. [параллельно] session_manager, process_manager
4. [последовательно] session_watcher
5. [последовательно] bot
6. [последовательно] main

## Сквозные механизмы

- **Проверка доступа** — проверка Telegram-ID по белому списку перед каждым действием. CJM: все. Модули: bot (реализация), config (хранение списка)
- **Промежуточные обновления (прогресс)** — рассуждения Claude отправляются пользователю не чаще раза в 30 секунд, формат: #N + песочные часы + текст. CJM: 02, 03. Модули: process_manager (извлечение), bot (отправка), message_splitter (форматирование)
- **Координация watcher/handler** — механизм паузы (счётчик active_requests), чтобы ответ не приходил дважды — от watcher и от handler. CJM: 02, 03, 07. Модули: session_watcher, process_manager, bot
- **Защита от одновременной записи** — блокировка при записи в sessions.json и daily_sessions.json через asyncio Lock. CJM: 02, 03, 04, 05, 06. Модули: session_manager, daily_session_registry
- **Обновление session_id** — при получении первого ответа Claude реальный session_id заменяет временный (_new_XXXX) во всех словарях. CJM: 02, 03, 04. Модули: process_manager, session_manager, daily_session_registry, bot
- **Обработка ошибок Claude** — ретраи до 10 раз с интервалом 1 минута, уведомления пользователю, /stop прерывает цикл. CJM: 02, 03, 08. Модули: process_manager, bot
- **Отправка сообщений в Telegram** — HTML-форматирование, fallback на plain text, разбивка, ретраи при ошибках сети, ожидание при RetryAfter. CJM: 02, 03, 04, 05, 06, 07, 08. Модули: bot, message_splitter
- **Восстановление после перезапуска** — чтение sessions.json, восстановление привязок, сканирование сессий на диске, автопереход в /all если привязки нет. CJM: 01. Модули: main, session_manager, daily_session_registry, session_reader
- **Автоочистка received_files/** — при запуске бот удаляет файлы старше 7 дней. CJM: 03. Модули: bot (при инициализации)
- **Атомарная запись файлов** — запись через временный .tmp файл + переименование. CJM: 02, 03, 04, 05, 06. Модули: session_manager, daily_session_registry

## Граф зависимостей (текстовая визуализация)

```
main
 └── bot
 │    ├── config
 │    ├── message_splitter
 │    ├── session_manager
 │    │    ├── config
 │    │    ├── daily_session_registry
 │    │    │    └── config
 │    │    └── session_reader
 │    │         └── config
 │    ├── process_manager
 │    │    ├── claude_runner
 │    │    │    └── config
 │    │    └── config
 │    ├── session_watcher
 │    │    ├── config
 │    │    ├── session_reader
 │    │    ├── daily_session_registry
 │    │    └── session_manager
 │    ├── daily_session_registry
 │    └── session_reader
 ├── config
 └── session_manager
```

## Трейсабельность CJM → модули

- **CJM-01** (Первый запуск) → config, main, session_manager, daily_session_registry, session_reader, bot
- **CJM-02** (Текстовое сообщение) → bot, session_manager, process_manager, claude_runner, message_splitter, daily_session_registry, session_watcher
- **CJM-03** (Фото/файл) → bot, session_manager, process_manager, claude_runner, message_splitter, daily_session_registry, session_watcher
- **CJM-04** (Новая сессия /new) → bot, session_manager, process_manager, claude_runner, daily_session_registry
- **CJM-05** (Список сессий /sessions) → bot, session_reader, daily_session_registry, session_manager
- **CJM-06** (Переключение /N) → bot, session_manager, daily_session_registry, session_reader
- **CJM-07** (Мониторинг /all) → bot, session_watcher, session_reader, session_manager, daily_session_registry
- **CJM-08** (Остановка /stop) → bot, process_manager
- **CJM-09** (Защита от двойного запуска) → main, config
