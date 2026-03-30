# Спецификация модуля: bot

Дата: 30-03-2026
Слой: 4 (зависит от слоёв 0-3)
Файл: `src/claude_manager/bot.py`

## Назначение

Транспортный слой Telegram-бота — принимает сообщения и команды из Telegram, передаёт их в нижележащие модули (session_manager, process_manager) и отправляет ответы обратно пользователю. Знает о Telegram API, не знает как работает Claude внутри.

## Обслуживаемые сценарии

- **CJM-01: Первый запуск и настройка** — при инициализации регистрирует обработчики команд, устанавливает меню подсказок в Telegram, выполняет автоочистку received_files/ от файлов старше 7 дней
- **CJM-02: Отправка текстового сообщения** — проверяет доступ, определяет состояние пользователя (подключён к сессии / режим /all), передаёт сообщение в process_manager, получает ответ, форматирует через message_splitter, отправляет в Telegram
- **CJM-03: Отправка фотографии или файла** — скачивает файл из Telegram, сохраняет в received_files/, формирует текстовое задание для Claude, далее как CJM-02
- **CJM-04: Создание новой сессии (/new)** — создаёт новую сессию через session_manager и process_manager, резервирует дневной номер, отправляет подтверждение
- **CJM-05: Просмотр списка сессий (/sessions)** — получает список сессий через session_reader, регистрирует в daily_session_registry, форматирует список и отправляет без HTML (чтобы номера были кликабельными)
- **CJM-06: Переключение на сессию (/N)** — ищет сессию по дневному номеру (в реестре и среди всех видимых), переключает через session_manager, отправляет подтверждение
- **CJM-07: Мониторинг всех сессий (/all)** — переводит пользователя в режим мониторинга через session_manager, запускает session_watcher
- **CJM-08: Остановка Claude (/stop)** — останавливает процесс Claude через process_manager, прерывает цикл ретраев

## Публичный API

### `async setup_bot() -> Application`

Создаёт и настраивает экземпляр Telegram-бота: регистрирует все обработчики команд и сообщений, выполняет автоочистку received_files/, сохраняет ссылку на Application в переменную модуля `_application` (для доступа к `bot` из функций, не имеющих `context`), возвращает готовое приложение для запуска. Вызывается из main.py.

**Аргументы:** нет

**Возвращает:** объект `telegram.ext.Application` — настроенное приложение Telegram-бота, готовое к запуску polling

**Исключения:** не выбрасывает (ошибки автоочистки логируются, но не прерывают запуск)

---

### `async post_init(application: Application) -> None`

Callback, вызываемый после запуска бота (через `Application.post_init`). Устанавливает меню подсказок команд в Telegram (кнопка «/» в поле ввода).

**Аргументы:**
- `application` (Application) — экземпляр приложения Telegram-бота

**Возвращает:** ничего

**Исключения:** не выбрасывает (ошибка установки меню логируется на уровне warning)

---

### `async handle_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None`

Обработчик команды `/new` — создаёт новую сессию Claude и привязывает к текущему чату.

**Аргументы:**
- `update` (Update) — входящее обновление от Telegram
- `context` (ContextTypes.DEFAULT_TYPE) — контекст обработчика

**Возвращает:** ничего

---

### `async handle_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None`

Обработчик команды `/sessions` — показывает список последних сессий с кликабельными номерами.

**Аргументы:**
- `update` (Update) — входящее обновление от Telegram
- `context` (ContextTypes.DEFAULT_TYPE) — контекст обработчика

**Возвращает:** ничего

---

### `async handle_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None`

Обработчик команды `/stop` — принудительно останавливает текущий процесс Claude и прерывает цикл ретраев.

**Аргументы:**
- `update` (Update) — входящее обновление от Telegram
- `context` (ContextTypes.DEFAULT_TYPE) — контекст обработчика

**Возвращает:** ничего

---

### `async handle_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None`

Обработчик команды `/all` — переводит пользователя в режим мониторинга всех сессий.

**Аргументы:**
- `update` (Update) — входящее обновление от Telegram
- `context` (ContextTypes.DEFAULT_TYPE) — контекст обработчика

**Возвращает:** ничего

---

### `async handle_switch_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None`

Обработчик команды `/N` (числовой номер) — переключает пользователя на сессию с указанным дневным номером.

**Аргументы:**
- `update` (Update) — входящее обновление от Telegram
- `context` (ContextTypes.DEFAULT_TYPE) — контекст обработчика

**Возвращает:** ничего

---

### `async handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None`

Обработчик текстовых сообщений — передаёт сообщение в текущую сессию Claude.

**Аргументы:**
- `update` (Update) — входящее обновление от Telegram
- `context` (ContextTypes.DEFAULT_TYPE) — контекст обработчика

**Возвращает:** ничего

---

### `async handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None`

Обработчик фотографий — скачивает фото, сохраняет на диск, формирует задание для Claude.

**Аргументы:**
- `update` (Update) — входящее обновление от Telegram
- `context` (ContextTypes.DEFAULT_TYPE) — контекст обработчика

**Возвращает:** ничего

---

### `async handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None`

Обработчик документов (файлов) — скачивает файл, сохраняет на диск, формирует задание для Claude.

**Аргументы:**
- `update` (Update) — входящее обновление от Telegram
- `context` (ContextTypes.DEFAULT_TYPE) — контекст обработчика

**Возвращает:** ничего

---

### `async send_response(chat_id: int, text: str, session_number: int, is_final: bool, reply_markup: InlineKeyboardMarkup | None = None) -> None`

Форматирует и отправляет ответ Claude в Telegram. Добавляет заголовок с номером сессии, конвертирует Markdown в HTML, разбивает длинные сообщения, при ошибке HTML переключается на plain text. Повторяет отправку при сетевых ошибках.

**Аргументы:**
- `chat_id` (int) — идентификатор Telegram-чата
- `text` (str) — текст ответа Claude в формате Markdown
- `session_number` (int) — дневной номер сессии (для заголовка)
- `is_final` (bool) — True для финального ответа (галочка), False для промежуточного (песочные часы)
- `reply_markup` (InlineKeyboardMarkup | None) — кнопки для последнего сообщения (например, кнопка переключения сессии)

**Возвращает:** ничего

**Исключения:**
- `telegram.error.TelegramError` — если все попытки отправки исчерпаны (проксируется наверх)

---

### `async send_watcher_message(chat_id: int, text: str, session_id: str, session_number: int) -> None`

Отправляет сообщение от watcher (ответ из другой сессии). Формат: номер сессии как кликабельная ссылка (если это не текущая сессия чата). Вызывается из session_watcher.

**Аргументы:**
- `chat_id` (int) — идентификатор Telegram-чата
- `text` (str) — текст ответа Claude в формате Markdown
- `session_id` (str) — идентификатор сессии, из которой пришёл ответ
- `session_number` (int) — дневной номер сессии

**Возвращает:** ничего

## Внутренние функции

### `_check_access(update: Update) -> bool`

Проверяет, есть ли Telegram-ID отправителя в белом списке разрешённых пользователей.

**Аргументы:**
- `update` (Update) — входящее обновление от Telegram

**Возвращает:** True если доступ разрешён, False если нет. При отказе — логирует предупреждение с ID отправителя.

---

### `async _send_telegram_message(chat_id: int, text: str, parse_mode: str | None = "HTML", reply_markup: InlineKeyboardMarkup | None = None) -> None`

Отправляет одно сообщение в Telegram с обработкой ошибок и повторными попытками. Если HTML не удалось — переключается на plain text.

**Аргументы:**
- `chat_id` (int) — идентификатор Telegram-чата
- `text` (str) — текст сообщения
- `parse_mode` (str | None) — режим парсинга ("HTML" или None для plain text)
- `reply_markup` (InlineKeyboardMarkup | None) — кнопки для сообщения

**Возвращает:** ничего

---

### `async _download_and_save_file(update: Update) -> str`

Скачивает файл (фото или документ) из Telegram и сохраняет на диск в папку received_files/.

**Аргументы:**
- `update` (Update) — входящее обновление с фото или документом

**Возвращает:** абсолютный путь к сохранённому файлу на диске

**Исключения:**
- `FileDownloadError` — если не удалось скачать файл из Telegram

---

### `_build_file_task(file_path: str, caption: str | None, is_image: bool) -> str`

Формирует текстовое задание для Claude на основе скачанного файла, подписи пользователя и типа файла.

**Аргументы:**
- `file_path` (str) — абсолютный путь к сохранённому файлу
- `caption` (str | None) — подпись пользователя к фото/файлу (может отсутствовать)
- `is_image` (bool) — True если это изображение (фото), False если документ

**Возвращает:** текст задания для Claude

---

### `_format_session_header(session_number: int, is_final: bool) -> str`

Формирует заголовок ответа с номером сессии и статусом.

**Аргументы:**
- `session_number` (int) — дневной номер сессии
- `is_final` (bool) — True для финального (галочка), False для промежуточного (песочные часы)

**Возвращает:** строку заголовка (например, `"#3 ✅ "` или `"#3 ⏳ "`)

---

### `_format_clickable_session_number(session_number: int) -> str`

Форматирует номер сессии как Telegram-команду `/N` в bold, которую Telegram автоматически делает кликабельной — для сообщений из чужих сессий. При нажатии на команду в Telegram она отправляется мгновенно.

Формат `tg://msg?text=` НЕ используется — он показывает диалог подтверждения. Telegram-команды `/N` в plain text автоматически кликабельны и отправляются мгновенно.

**Аргументы:**
- `session_number` (int) — дневной номер сессии

**Возвращает:** HTML-строку с командой в bold (например, `"<b>/3</b>"`)

---

### `_is_current_session(chat_id: int, session_id: str) -> bool`

Проверяет, является ли данная сессия текущей активной сессией для данного чата.

**Аргументы:**
- `chat_id` (int) — идентификатор Telegram-чата
- `session_id` (str) — идентификатор сессии Claude

**Возвращает:** True если эта сессия привязана к данному чату, False если другая или нет привязки

---

### `async _clean_old_received_files() -> None`

Удаляет файлы старше 7 дней из папки received_files/. Вызывается при запуске бота (из setup_bot).

**Аргументы:** нет

**Возвращает:** ничего (ошибки логируются, не прерывают работу)

---

### `async _send_to_claude_and_respond(chat_id: int, text: str) -> None`

Отправляет сообщение в Claude через process_manager и обрабатывает ответ: форматирует, разбивает, отправляет в Telegram. Содержит основной цикл взаимодействия: отправка → ожидание → получение ответа → форматирование → отправка.

**Аргументы:**
- `chat_id` (int) — идентификатор Telegram-чата
- `text` (str) — текст сообщения для Claude

**Возвращает:** ничего

---

### `async _handle_process_response(chat_id: int, session_id: str) -> None`

Обрабатывает ответ от process_manager: получает финальный текст и промежуточные обновления, форматирует и отправляет в Telegram.

**Аргументы:**
- `chat_id` (int) — идентификатор Telegram-чата
- `session_id` (str) — идентификатор сессии Claude

**Возвращает:** ничего

---

### `async _find_session_by_number(day_number: int) -> str | None`

Ищет сессию по дневному номеру: сначала в дневном реестре, затем среди всех видимых сессий (через session_reader).

**Аргументы:**
- `day_number` (int) — дневной номер сессии (например, 3)

**Возвращает:** идентификатор сессии (str) или None если не найдена

---

### `_generate_file_name(original_name: str | None, extension: str) -> str`

Генерирует уникальное имя файла для сохранения в received_files/.

**Аргументы:**
- `original_name` (str | None) — оригинальное имя файла (может быть None для фото)
- `extension` (str) — расширение файла (например, "jpg", "pdf")

**Возвращает:** уникальное имя файла вида `file_20260328_143022_abc123.jpg`

## Алгоритм работы

### setup_bot

1. Создать экземпляр `ApplicationBuilder` с токеном из `config.BOT_TOKEN`
2. Сохранить ссылку на Application в переменную модуля `_application` — для доступа к `_application.bot` из функций `send_response`, `send_watcher_message`, `_send_telegram_message`, `_download_and_save_file`
3. Зарегистрировать `post_init` callback для установки меню команд
4. Зарегистрировать обработчики команд:
   - `CommandHandler("new", handle_new)` — создание сессии
   - `CommandHandler("sessions", handle_sessions)` — список сессий
   - `CommandHandler("stop", handle_stop)` — остановка Claude
   - `CommandHandler("all", handle_all)` — режим мониторинга
   - `MessageHandler(Regex(r"^/\d+$"), handle_switch_session)` — переключение по номеру
   - `MessageHandler(TEXT & ~COMMAND, handle_message)` — текстовые сообщения
   - `MessageHandler(PHOTO, handle_photo)` — фотографии
   - `MessageHandler(DOCUMENT, handle_document)` — документы
5. Вызвать `_clean_old_received_files()` — автоочистка файлов старше 7 дней
6. Вернуть собранное приложение

### post_init

1. Сформировать список команд: `[("new", "Новая сессия"), ("sessions", "Список сессий"), ("all", "Мониторинг всех сессий"), ("stop", "Остановить Claude")]`
2. Вызвать `application.bot.set_my_commands(commands)` для установки меню подсказок
3. Залогировать на уровне info: "Меню команд установлено"
4. При ошибке — залогировать на уровне warning и продолжить (меню команд не критично)

### handle_new

1. Проверить доступ через `_check_access(update)`. Если отказано — выйти молча
2. Получить chat_id из `update.effective_chat.id`
3. Создать новую сессию через `session_manager.create_session(chat_id)` — это резервирует дневной номер и создаёт процесс Claude
4. Получить дневной номер через `daily_session_registry.register_session(session_id)`
5. Отправить подтверждение пользователю: "Создана новая сессия #N"
6. При ошибке — отправить сообщение об ошибке пользователю и залогировать

### handle_sessions

1. Проверить доступ через `_check_access(update)`. Если отказано — выйти молча
2. Получить список сессий: `session_reader.get_recent_sessions(config.WORKING_DIR)`
3. Если список пуст — отправить "Нет сессий" и выйти
4. Для каждой сессии зарегистрировать в daily_session_registry: `register_session(session.session_id)`
5. Сформировать текст списка: для каждой сессии строка `/{номер} {превью}` (новые сверху)
6. Отправить список без HTML-форматирования (`parse_mode=None`), чтобы Telegram автоматически подсветил `/1`, `/2`, `/3` как кликабельные команды

### handle_stop

1. Проверить доступ через `_check_access(update)`. Если отказано — выйти молча
2. Получить chat_id из `update.effective_chat.id`
3. Получить текущую сессию: `session_manager.get_current_session(chat_id)`
4. Если пользователь в режиме /all (нет текущей сессии) — отправить: "Команда /stop работает только внутри сессии. Подключитесь к сессии через /sessions" и выйти
5. Проверить, работает ли Claude: `process_manager.is_running(session_id)`
6. Если Claude не работает — отправить: "Claude сейчас не работает, нечего останавливать" и выйти
7. Остановить процесс: `process_manager.stop_process(session_id)` — это также прерывает цикл ретраев
8. Отправить подтверждение: "Claude остановлен"

### handle_all

1. Проверить доступ через `_check_access(update)`. Если отказано — выйти молча
2. Получить chat_id из `update.effective_chat.id`
3. Перевести в режим мониторинга: `session_manager.set_all_mode(chat_id)`
4. Отправить подтверждение: "Режим мониторинга всех сессий"

### handle_switch_session

1. Проверить доступ через `_check_access(update)`. Если отказано — выйти молча
2. Извлечь номер из текста команды: `int(update.message.text[1:])` (убрать `/`, преобразовать в число)
3. Найти сессию: `_find_session_by_number(day_number)`
4. Если не найдена — отправить: "Сессия #{номер} не найдена" и выйти
5. Привязать чат к сессии: `session_manager.switch_session(chat_id, session_id)`
6. Получить превью сессии для подтверждения (из session_reader или daily_session_registry)
7. Отправить подтверждение: "Подключён к сессии #{номер}: {превью}"

### handle_message

1. Проверить доступ через `_check_access(update)`. Если отказано — выйти молча
2. Получить chat_id и текст сообщения
3. Получить текущее состояние: `session_manager.get_current_session(chat_id)`
4. Если пользователь в режиме /all — отправить: "Вы в режиме мониторинга. Для отправки сообщений подключитесь к сессии — нажмите на номер сессии или отправьте /new" и выйти
5. Если нет активной сессии (такое не должно быть при двух состояниях, но как защита) — отправить подсказку и выйти
6. Включить индикатор "печатает...": `context.bot.send_chat_action(chat_id, ChatAction.TYPING)`
7. Вызвать `_send_to_claude_and_respond(chat_id, text)`

### handle_photo

1. Проверить доступ через `_check_access(update)`. Если отказано — выйти молча
2. Получить chat_id
3. Проверить состояние (подключён к сессии / режим /all) — аналогично handle_message
4. Скачать и сохранить фото: `_download_and_save_file(update)` — берёт фото максимального размера
5. Получить подпись (caption) из `update.message.caption`
6. Сформировать задание: `_build_file_task(file_path, caption, is_image=True)`
7. Включить индикатор "печатает..."
8. Вызвать `_send_to_claude_and_respond(chat_id, task_text)`

### handle_document

1. Проверить доступ через `_check_access(update)`. Если отказано — выйти молча
2. Получить chat_id
3. Проверить состояние (подключён к сессии / режим /all) — аналогично handle_message
4. Скачать и сохранить документ: `_download_and_save_file(update)`
5. Получить подпись (caption) из `update.message.caption`
6. Определить тип файла: если расширение в `IMAGE_EXTENSIONS` — это изображение
7. Сформировать задание: `_build_file_task(file_path, caption, is_image)`
8. Включить индикатор "печатает..."
9. Вызвать `_send_to_claude_and_respond(chat_id, task_text)`

### send_response

1. Если текст пустой или равен "No response requested." — заменить на: "Claude обработал запрос, но не дал текстовый ответ"
2. Конвертировать текст (Markdown -> HTML) и разбить: `message_splitter.prepare_message(text)`
3. Если это промежуточное обновление (is_final=False) — обернуть каждую часть в курсив: `<i>{part}</i>`
4. Сформировать заголовок: `_format_session_header(session_number, is_final)` — заголовок всегда без курсива
5. Добавить заголовок в начало первой части: `parts[0] = header + parts[0]`
6. Для каждой части вызвать `_send_telegram_message(chat_id, part)`. Кнопки (reply_markup) прикрепить только к последней части
7. Обработка ошибки HTML-парсинга происходит внутри `_send_telegram_message` (fallback на plain text)

### send_watcher_message

1. Определить, является ли сессия текущей для этого чата: `_is_current_session(chat_id, session_id)`
2. Конвертировать текст (Markdown -> HTML) и разбить: `message_splitter.prepare_message(text)`
3. Если текущая сессия — сформировать заголовок без ссылки: `_format_session_header(session_number, is_final=True)`
4. Если не текущая — сформировать заголовок с кликабельной ссылкой: `_format_clickable_session_number(session_number)` + " ✅ "
5. Добавить заголовок в начало первой части
6. Для каждой части вызвать `_send_telegram_message(chat_id, part)`

### _send_telegram_message

1. Попытаться отправить сообщение через `_application.bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)`
2. Если получена ошибка `BadRequest` (проблема с HTML-парсингом) и parse_mode == "HTML":
   - Удалить HTML-теги: `message_splitter.strip_html_tags(text)`
   - Повторить отправку с `parse_mode=None`
3. Если получена ошибка `RetryAfter` — извлечь время ожидания из ошибки, подождать `await asyncio.sleep(retry_after_seconds)`, повторить отправку
4. Если получена сетевая ошибка (`TimedOut`, `NetworkError`) — повторить до `SEND_RETRY_COUNT` (3) раз с паузой `SEND_RETRY_DELAY_SECONDS` (2) секунды
5. Если все попытки исчерпаны — залогировать ошибку на уровне error и проксировать исключение

### _download_and_save_file

1. Определить тип файла: фото (update.message.photo) или документ (update.message.document)
2. Для фото — взять последний элемент из `update.message.photo` (максимальное разрешение), определить расширение как "jpg"
3. Для документа — взять `update.message.document`, получить оригинальное имя и расширение
4. Сгенерировать имя файла: `_generate_file_name(original_name, extension)`
5. Создать папку `received_files/` если не существует
6. Скачать файл: `file = await _application.bot.get_file(file_id)`, `await file.download_to_drive(save_path)`
7. Залогировать на уровне info: путь к сохранённому файлу
8. Вернуть абсолютный путь

### _build_file_task

1. Если подпись есть:
   - Вернуть: `"Пользователь отправил файл с подписью: {caption}. Файл: {file_path}. Прочитай файл инструментом Read и выполни задачу из подписи"`
2. Если подписи нет и это изображение:
   - Вернуть: `"Пользователь отправил фотографию без подписи. Файл: {file_path}. Прочитай файл и опиши, что на фотографии"`
3. Если подписи нет и это документ:
   - Вернуть: `"Пользователь отправил файл без подписи. Файл: {file_path}. Прочитай файл и опиши его содержимое"`

### _find_session_by_number

1. Искать в дневном реестре: `daily_session_registry.get_session_id_by_number(day_number)`
2. Если найдено — вернуть session_id
3. Если не найдено — получить все видимые сессии: `session_reader.get_recent_sessions(config.WORKING_DIR)`
4. Зарегистрировать каждую сессию в реестре: `daily_session_registry.register_session(session.session_id)`
5. Повторить поиск в реестре: `daily_session_registry.get_session_id_by_number(day_number)`
6. Вернуть session_id или None

### _send_to_claude_and_respond

1. Получить session_id текущей сессии: `session_manager.get_current_session(chat_id)`
2. Отправить сообщение в Claude: `process_manager.send_message(session_id, text)`
3. Координация с watcher: `process_manager.pause_watcher(session_id)` — приостановить watcher для текущей сессии, чтобы ответ не пришёл дважды
4. Обработать ответ: `_handle_process_response(chat_id, session_id)`
5. Возобновить watcher: `process_manager.resume_watcher(session_id)`
6. При ошибке — отправить сообщение об ошибке пользователю, залогировать, возобновить watcher

### _handle_process_response

1. Получить ответ от process_manager: `process_manager.get_response(session_id)` — может включать промежуточные обновления и финальный результат
2. Если session_id обновился (был временный, стал реальный) — обновить во всех местах: `session_manager.update_session_id(chat_id, old_id, new_id)`, `daily_session_registry.update_session_id(old_id, new_id)`
3. Получить дневной номер: `daily_session_registry.register_session(session_id)` (идемпотентно)
4. Для промежуточных обновлений — вызвать `send_response(chat_id, text, session_number, is_final=False)`
5. Для финального ответа — вызвать `send_response(chat_id, text, session_number, is_final=True)`
6. Если ответ пустой или "No response requested." — отправить сообщение о пустом ответе
7. Если произошла ошибка Claude — отправить уведомление пользователю. Цикл ретраев управляется process_manager

### _clean_old_received_files

1. Проверить существование папки `received_files/` — если не существует, выйти
2. Получить текущее время
3. Для каждого файла в папке — проверить дату модификации через `os.path.getmtime()`
4. Если файл старше 7 дней (`RECEIVED_FILES_MAX_AGE_DAYS`) — удалить через `os.remove()`
5. Залогировать количество удалённых файлов на уровне info
6. При ошибке удаления конкретного файла — залогировать warning и продолжить с остальными

### _check_access

1. Получить user_id из `update.effective_user.id`
2. Проверить: `user_id in config.ALLOWED_USER_IDS`
3. Если нет — залогировать warning: `"Неавторизованный доступ: user_id={user_id}"`
4. Вернуть результат проверки

### _generate_file_name

1. Получить текущую дату и время: `datetime.now().strftime(FILE_TIMESTAMP_FORMAT)` (формат `%Y%m%d_%H%M%S`)
2. Сгенерировать случайный суффикс: 6 символов из `string.ascii_lowercase + string.digits`
3. Собрать имя: `file_{timestamp}_{suffix}.{extension}`
4. Вернуть имя файла

## Внутреннее состояние модуля

- `_application: Application | None` — ссылка на экземпляр Telegram Application. Устанавливается в `setup_bot()`. Используется для доступа к `_application.bot` в функциях, которые не получают `context` (send_response, send_watcher_message, _send_telegram_message, _download_and_save_file)

## Зависимости

- **config** — `BOT_TOKEN` (токен для создания Application), `ALLOWED_USER_IDS` (проверка доступа), `WORKING_DIR` (путь к проекту для session_reader и received_files)
- **message_splitter** — `prepare_message()` (конвертация Markdown в HTML и разбивка), `strip_html_tags()` (fallback на plain text при ошибке HTML)
- **session_manager** — `create_session()`, `get_current_session()`, `switch_session()`, `set_all_mode()`, `update_session_id()` — управление привязками chat_id к сессиям. Примечание: спецификация session_manager ещё не создана, интерфейс предварительный и будет согласован
- **process_manager** — `send_message()`, `get_response()`, `stop_process()`, `is_running()`, `pause_watcher()`, `resume_watcher()` — управление процессами Claude и координация с watcher. Примечание: спецификация process_manager ещё не создана, интерфейс предварительный и будет согласован
- **session_watcher** — `start_watching()`, `stop_watching()` — запуск/остановка мониторинга. Watcher вызывает `send_watcher_message()` для отправки сообщений. Примечание: спецификация session_watcher ещё не создана, интерфейс предварительный и будет согласован
- **daily_session_registry** — `register_session()` (получение/создание дневного номера), `get_session_id_by_number()` (поиск по номеру), `update_session_id()` (обновление временного ID), `get_all_today_sessions()` (список сессий за сегодня)
- **session_reader** — `get_recent_sessions()` (получение списка сессий с диска), `SessionInfo` (тип данных о сессии)
- **telegram** (сторонний пакет python-telegram-bot 21.10) — `Update`, `Application`, `ApplicationBuilder`, `ContextTypes`, `CommandHandler`, `MessageHandler`, `InlineKeyboardMarkup`, `ChatAction`, `filters`, ошибки (`BadRequest`, `RetryAfter`, `TimedOut`, `NetworkError`, `TelegramError`)
- **asyncio** (стандартная библиотека) — `asyncio.sleep()` (пауза при RetryAfter и ретраях отправки)
- **os** (стандартная библиотека) — работа с файлами received_files/ (создание папки, удаление старых файлов, проверка существования)
- **datetime** (стандартная библиотека) — генерация имён файлов, проверка возраста файлов
- **logging** (стандартная библиотека) — логирование событий
- **html** (стандартная библиотека) — `html.escape()` — экранирование пользовательских данных
- **pathlib** (стандартная библиотека) — `Path` — работа с путями
- **string, random** (стандартная библиотека) — генерация случайного суффикса для имён файлов

## Обработка ошибок

- **Неавторизованный пользователь** — сообщение молча игнорируется (не отправляется ответ). В лог записывается warning с user_id отправителя
- **Сообщение в режиме /all** — пользователю отправляется: "Вы в режиме мониторинга. Для отправки сообщений подключитесь к сессии — нажмите на номер сессии или отправьте /new"
- **Сессия не найдена по номеру (/N)** — пользователю отправляется: "Сессия #{номер} не найдена"
- **/stop в режиме /all** — пользователю отправляется: "Команда /stop работает только внутри сессии. Подключитесь к сессии через /sessions"
- **/stop когда Claude не работает** — пользователю отправляется: "Claude сейчас не работает, нечего останавливать"
- **Пустой ответ от Claude** — пользователю отправляется: "Claude обработал запрос, но не дал текстовый ответ"
- **Ответ "No response requested."** — не пересылается пользователю, заменяется на сообщение о пустом ответе
- **Ошибка HTML-парсинга Telegram (BadRequest)** — fallback на plain text: удалить все HTML-теги через `message_splitter.strip_html_tags()` и отправить повторно без форматирования
- **RetryAfter от Telegram** — подождать указанное количество секунд (`await asyncio.sleep(retry_after)`), повторить отправку
- **Сетевая ошибка отправки (TimedOut, NetworkError)** — повторить до 3 раз (`SEND_RETRY_COUNT`) с паузой 2 секунды (`SEND_RETRY_DELAY_SECONDS`). После исчерпания попыток — залогировать ошибку на уровне error
- **Ошибка скачивания файла из Telegram** — пользователю отправляется: "Не удалось скачать файл. Попробуйте отправить ещё раз"
- **Ошибка создания сессии (/new)** — пользователю отправляется: "Не удалось создать сессию. Попробуйте ещё раз". В лог — error
- **Ошибка от Claude (сбой API, обрыв)** — process_manager управляет ретраями (до 10 раз, интервал 1 минута). Bot отправляет пользователю уведомления о каждой попытке. После 10 неудачных попыток: "Не удалось получить ответ от Claude после 10 попыток. Попробуйте снова"
- **Ошибка автоочистки received_files/** — логируется на уровне warning, запуск бота не прерывается. Каждый файл удаляется независимо — ошибка с одним файлом не останавливает очистку остальных

## Константы

- `SEND_RETRY_COUNT = 3` — количество попыток повторной отправки сообщения в Telegram при сетевых ошибках. 3 попытки достаточно, чтобы пережить короткий обрыв сети
- `SEND_RETRY_DELAY_SECONDS = 2` — пауза между попытками повторной отправки (в секундах). 2 секунды — разумный баланс между скоростью и нагрузкой
- `RECEIVED_FILES_DIR = "received_files"` — имя папки для сохранения скачанных фото и документов
- `RECEIVED_FILES_MAX_AGE_DAYS = 7` — максимальный возраст файлов в received_files/ (в днях). Файлы старше 7 дней удаляются при запуске бота (решение из валидации BRD, проблема 5.2)
- `FILE_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"` — формат временной метки в именах файлов
- `FILE_RANDOM_SUFFIX_LENGTH = 6` — длина случайного суффикса в именах файлов. 6 символов из 36-символьного алфавита — 2 миллиарда вариантов, коллизии практически невозможны
- `IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "tiff", "svg"}` — расширения, которые считаются изображениями. Используется для определения типа файла при формировании задания Claude
- `EMPTY_RESPONSE_TEXT = "Claude обработал запрос, но не дал текстовый ответ"` — сообщение для пользователя при пустом ответе Claude
- `NO_RESPONSE_MARKER = "No response requested."` — служебный ответ Claude, который не нужно пересылать пользователю
- `BOT_COMMANDS = [("new", "Новая сессия"), ("sessions", "Список сессий"), ("all", "Мониторинг всех сессий"), ("stop", "Остановить Claude")]` — список команд для меню подсказок в Telegram
- `MONITORING_MODE_MESSAGE = "Вы в режиме мониторинга. Для отправки сообщений подключитесь к сессии — нажмите на номер сессии или отправьте /new"` — сообщение при попытке написать в режиме /all

## Тест-план

### Юнит-тесты

- **test_check_access_allowed_user** — проверяет, что разрешённый пользователь проходит проверку
  - Вход: `update.effective_user.id = 123456789`, `config.ALLOWED_USER_IDS = {123456789}`
  - Ожидаемый результат: `True`
  - Тип: unit

- **test_check_access_denied_user** — проверяет, что неразрешённый пользователь отклоняется
  - Вход: `update.effective_user.id = 999999999`, `config.ALLOWED_USER_IDS = {123456789}`
  - Ожидаемый результат: `False`, в логах warning с user_id
  - Тип: unit

- **test_format_session_header_final** — проверяет формат заголовка для финального ответа
  - Вход: `session_number=3, is_final=True`
  - Ожидаемый результат: `"#3 ✅ "`
  - Тип: unit

- **test_format_session_header_intermediate** — проверяет формат заголовка для промежуточного обновления
  - Вход: `session_number=5, is_final=False`
  - Ожидаемый результат: `"#5 ⏳ "`
  - Тип: unit

- **test_format_clickable_session_number** — проверяет формат кликабельного номера сессии
  - Вход: `session_number=3`
  - Ожидаемый результат: строка содержит `<b>/3</b>`
  - Тип: unit

- **test_build_file_task_with_caption** — проверяет формирование задания с подписью
  - Вход: `file_path="/tmp/received_files/file_20260328_143022_abc123.jpg"`, `caption="Что здесь не так?"`, `is_image=True`
  - Ожидаемый результат: строка содержит "подписью", "Что здесь не так?", путь к файлу, "Прочитай файл"
  - Тип: unit

- **test_build_file_task_image_no_caption** — проверяет формирование задания для фото без подписи
  - Вход: `file_path="/tmp/received_files/photo.jpg"`, `caption=None`, `is_image=True`
  - Ожидаемый результат: строка содержит "фотографию без подписи", путь к файлу, "опиши, что на фотографии"
  - Тип: unit

- **test_build_file_task_document_no_caption** — проверяет формирование задания для документа без подписи
  - Вход: `file_path="/tmp/received_files/report.pdf"`, `caption=None`, `is_image=False`
  - Ожидаемый результат: строка содержит "файл без подписи", путь к файлу, "опиши его содержимое"
  - Тип: unit

- **test_generate_file_name_format** — проверяет формат генерируемого имени файла
  - Вход: `original_name="photo.jpg"`, `extension="jpg"`
  - Ожидаемый результат: имя файла соответствует формату `file_YYYYMMDD_HHMMSS_XXXXXX.jpg` (где X — буквенно-цифровые символы)
  - Тип: unit

- **test_is_current_session_true** — проверяет определение текущей сессии
  - Вход: `chat_id=123`, `session_id="abc-123"`, session_manager.get_current_session(123) возвращает "abc-123"
  - Ожидаемый результат: `True`
  - Тип: unit

- **test_is_current_session_false** — проверяет определение чужой сессии
  - Вход: `chat_id=123`, `session_id="def-456"`, session_manager.get_current_session(123) возвращает "abc-123"
  - Ожидаемый результат: `False`
  - Тип: unit

- **test_handle_new_creates_session** — проверяет, что /new создаёт сессию и отправляет подтверждение
  - Вход: mock update с разрешённым user_id, mock session_manager и process_manager
  - Ожидаемый результат: `session_manager.create_session()` вызван, пользователь получил сообщение "Создана новая сессия #N"
  - Тип: unit

- **test_handle_sessions_shows_list** — проверяет, что /sessions показывает список сессий
  - Вход: mock session_reader возвращает 3 SessionInfo, mock daily_session_registry
  - Ожидаемый результат: пользователь получил текст со строками `/1`, `/2`, `/3` и превью, parse_mode=None
  - Тип: unit

- **test_handle_stop_stops_process** — проверяет, что /stop останавливает Claude
  - Вход: mock update, пользователь подключён к сессии, process_manager.is_running() -> True
  - Ожидаемый результат: `process_manager.stop_process()` вызван, пользователь получил "Claude остановлен"
  - Тип: unit

- **test_handle_all_switches_to_monitoring** — проверяет, что /all переводит в мониторинг
  - Вход: mock update с разрешённым user_id
  - Ожидаемый результат: `session_manager.set_all_mode()` вызван, пользователь получил "Режим мониторинга всех сессий"
  - Тип: unit

- **test_handle_switch_session_connects** — проверяет, что /3 подключает к сессии
  - Вход: mock update с text="/3", daily_session_registry возвращает session_id
  - Ожидаемый результат: `session_manager.switch_session()` вызван, пользователь получил подтверждение
  - Тип: unit

- **test_handle_message_sends_to_claude** — проверяет, что текстовое сообщение отправляется в Claude
  - Вход: mock update с text="Посмотри файл main.py", пользователь подключён к сессии
  - Ожидаемый результат: `process_manager.send_message()` вызван с текстом "Посмотри файл main.py"
  - Тип: unit

- **test_send_response_formats_html** — проверяет форматирование и отправку ответа
  - Вход: `text="**Ответ** Claude"`, `session_number=3`, `is_final=True`
  - Ожидаемый результат: `message_splitter.prepare_message()` вызван, результат отправлен в Telegram
  - Тип: unit

- **test_setup_bot_registers_handlers** — проверяет, что setup_bot регистрирует все обработчики
  - Вход: вызов `setup_bot()` с mock config
  - Ожидаемый результат: Application создан, содержит обработчики для new, sessions, stop, all, текстовых сообщений, фото, документов
  - Тип: unit

- **test_clean_old_received_files_deletes_old** — проверяет удаление старых файлов
  - Вход: папка received_files/ с файлами: один 10-дневной давности, один 3-дневной давности
  - Ожидаемый результат: 10-дневной удалён, 3-дневной остался
  - Тип: unit

- **test_post_init_sets_commands** — проверяет, что post_init устанавливает меню команд
  - Вход: mock application с mock bot
  - Ожидаемый результат: `bot.set_my_commands()` вызван с 4 командами (new, sessions, all, stop)
  - Тип: unit

- **test_handle_photo_sends_to_claude** — проверяет полный цикл обработки фото
  - Вход: mock update с фото (PhotoSize), пользователь подключён к сессии
  - Ожидаемый результат: фото скачано, `_build_file_task` вызван с `is_image=True`, `process_manager.send_message()` вызван
  - Тип: unit

- **test_handle_document_sends_to_claude** — проверяет полный цикл обработки документа
  - Вход: mock update с документом (Document, file_name="report.pdf"), пользователь подключён к сессии
  - Ожидаемый результат: документ скачан, `_build_file_task` вызван с `is_image=False`, `process_manager.send_message()` вызван
  - Тип: unit

### Граничные случаи

- **test_handle_message_in_all_mode** — попытка написать в режиме /all
  - Вход: пользователь в режиме /all, отправляет текст
  - Ожидаемый результат: пользователь получает MONITORING_MODE_MESSAGE, сообщение не отправлено в Claude
  - Тип: edge case

- **test_handle_stop_in_all_mode** — /stop в режиме /all
  - Вход: пользователь в режиме /all, отправляет /stop
  - Ожидаемый результат: пользователь получает сообщение о необходимости подключиться к сессии
  - Тип: edge case

- **test_handle_stop_claude_not_running** — /stop когда Claude не обрабатывает запрос
  - Вход: пользователь подключён к сессии, process_manager.is_running() -> False
  - Ожидаемый результат: пользователь получает "Claude сейчас не работает, нечего останавливать"
  - Тип: edge case

- **test_handle_switch_session_not_found** — /99 с несуществующим номером
  - Вход: update.message.text="/99", ни в реестре, ни среди видимых сессий номер 99 не найден
  - Ожидаемый результат: пользователь получает "Сессия #99 не найдена"
  - Тип: edge case

- **test_handle_sessions_empty** — /sessions когда сессий нет
  - Вход: session_reader.get_recent_sessions() возвращает пустой список
  - Ожидаемый результат: пользователь получает "Нет сессий"
  - Тип: edge case

- **test_send_response_empty_text** — ответ Claude с пустым текстом
  - Вход: `text=""`, `session_number=1`, `is_final=True`
  - Ожидаемый результат: пользователь получает EMPTY_RESPONSE_TEXT
  - Тип: edge case

- **test_send_response_no_response_marker** — ответ "No response requested."
  - Вход: `text="No response requested."`, `session_number=1`, `is_final=True`
  - Ожидаемый результат: пользователь получает EMPTY_RESPONSE_TEXT
  - Тип: edge case

- **test_send_watcher_message_current_session** — watcher-сообщение из текущей сессии
  - Вход: session_id совпадает с текущей сессией чата
  - Ожидаемый результат: номер сессии без кликабельной ссылки
  - Тип: edge case

- **test_send_watcher_message_other_session** — watcher-сообщение из другой сессии
  - Вход: session_id не совпадает с текущей сессией чата
  - Ожидаемый результат: номер сессии как кликабельная ссылка
  - Тип: edge case

- **test_handle_photo_in_all_mode** — отправка фото в режиме /all
  - Вход: пользователь в режиме /all, отправляет фото
  - Ожидаемый результат: пользователь получает MONITORING_MODE_MESSAGE, фото не обработано
  - Тип: edge case

- **test_handle_document_image_by_extension** — файл с расширением изображения, отправленный как документ
  - Вход: документ с именем "screenshot.png"
  - Ожидаемый результат: `_build_file_task` вызван с `is_image=True`
  - Тип: edge case

- **test_find_session_by_number_in_visible_sessions** — номер не в реестре, но среди видимых сессий
  - Вход: `day_number=5`, в реестре нет, но session_reader возвращает сессии, среди которых одна получает номер 5 при регистрации
  - Ожидаемый результат: session_id этой сессии
  - Тип: edge case

- **test_clean_old_received_files_no_directory** — очистка когда папки received_files/ нет
  - Вход: папка received_files/ не существует
  - Ожидаемый результат: функция завершается без ошибок
  - Тип: edge case

- **test_clean_old_received_files_all_fresh** — очистка когда все файлы свежие
  - Вход: все файлы младше 7 дней
  - Ожидаемый результат: ни один файл не удалён
  - Тип: edge case

- **test_text_commands_sent_to_claude** — текстовые команды "стоп/stop/отмена/cancel" отправляются как обычные сообщения
  - Вход: пользователь подключён к сессии, отправляет "стоп"
  - Ожидаемый результат: сообщение "стоп" передано в Claude как обычный текст (не обрабатывается как команда)
  - Тип: edge case

- **test_typing_indicator_shown** — индикатор "печатает..." включается при обработке
  - Вход: пользователь отправляет текстовое сообщение
  - Ожидаемый результат: `send_chat_action(ChatAction.TYPING)` вызван до обращения к Claude
  - Тип: edge case

### Тесты ошибок

- **test_send_telegram_message_html_fallback** — fallback на plain text при ошибке HTML
  - Вход: mock bot.send_message выбрасывает BadRequest при HTML, но успешно при plain text
  - Ожидаемый результат: `message_splitter.strip_html_tags()` вызван, сообщение отправлено без форматирования
  - Тип: error

- **test_send_telegram_message_retry_after** — ожидание при RetryAfter
  - Вход: mock bot.send_message выбрасывает RetryAfter с retry_after=5
  - Ожидаемый результат: `asyncio.sleep(5)` вызван, затем повторная отправка
  - Тип: error

- **test_send_telegram_message_network_retry** — повторные попытки при сетевой ошибке
  - Вход: mock bot.send_message выбрасывает TimedOut 2 раза, затем успешно
  - Ожидаемый результат: 3 вызова send_message, последний успешный
  - Тип: error

- **test_send_telegram_message_all_retries_failed** — все попытки отправки исчерпаны
  - Вход: mock bot.send_message выбрасывает NetworkError все 3 раза
  - Ожидаемый результат: ошибка залогирована на уровне error
  - Тип: error

- **test_download_file_failure** — ошибка скачивания файла
  - Вход: mock bot.get_file выбрасывает TelegramError
  - Ожидаемый результат: пользователь получает "Не удалось скачать файл. Попробуйте отправить ещё раз"
  - Тип: error

- **test_handle_new_creation_error** — ошибка создания сессии
  - Вход: session_manager.create_session() выбрасывает исключение
  - Ожидаемый результат: пользователь получает "Не удалось создать сессию. Попробуйте ещё раз", ошибка залогирована
  - Тип: error

- **test_claude_error_notification** — уведомление пользователя об ошибке Claude
  - Вход: process_manager.get_response() сигнализирует об ошибке Claude
  - Ожидаемый результат: пользователь получает уведомление об ошибке
  - Тип: error

- **test_clean_old_received_files_permission_error** — ошибка удаления одного файла не останавливает очистку
  - Вход: 3 файла старше 7 дней, удаление второго вызывает PermissionError
  - Ожидаемый результат: первый и третий файлы удалены, для второго залогирован warning
  - Тип: error

- **test_check_access_denied_silent** — неавторизованный пользователь не получает ответ
  - Вход: update от неразрешённого пользователя
  - Ожидаемый результат: handle_message не отправляет никакого ответа, не вызывает process_manager
  - Тип: error
