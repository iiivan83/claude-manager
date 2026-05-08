# Спецификация модуля: session_manager

Дата: 30-03-2026
Слой: 2 (зависит от слоёв 0-1)
Файл: `src/claude_manager/session_manager.py`

## Назначение

Управляет связкой Telegram-чата (chat_id) с сессией Claude (session_id): привязывает чат к конкретной сессии, переключает между сессиями, управляет двумя состояниями пользователя ("подключён к сессии" и "/all мониторинг"), сохраняет и восстанавливает привязки через файл `sessions.json` с атомарной записью и защитой от параллельного доступа.

## Обслуживаемые сценарии

- **CJM-02: Отправка текстового сообщения** — проверяет, подключён ли чат к сессии; если подключён — возвращает session_id текущей сессии; если в режиме /all — сообщает, что нет активной сессии; при получении ответа Claude сохраняет привязку на диск
- **CJM-03: Отправка фотографии или файла** — аналогично CJM-02: проверяет привязку чата к сессии перед отправкой файла в Claude
- **CJM-04: Создание новой сессии (/new)** — привязывает чат к новой сессии с временным session_id (вида `_new_XXXX`), регистрирует сессию в дневном реестре, сохраняет привязку на диск
- **CJM-05: Просмотр списка сессий (/sessions)** — предоставляет информацию о текущей привязке чата (чтобы bot мог пометить текущую сессию в списке)
- **CJM-06: Переключение на сессию (/N)** — ищет сессию по дневному номеру (сначала в дневном реестре, затем среди всех видимых сессий через session_reader), привязывает чат к найденной сессии, сохраняет на диск
- **CJM-07: Мониторинг всех сессий (/all)** — отвязывает чат от конкретной сессии, переводит в режим мониторинга, сохраняет состояние на диск

## Публичный API

### `async def bind_session(chat_id: int, session_id: str) -> int`

Привязывает Telegram-чат к сессии Claude и регистрирует сессию в дневном реестре. Если чат уже был привязан к другой сессии — перепривязывает. Сохраняет привязку на диск.

**Аргументы:**
- `chat_id` (int) — идентификатор Telegram-чата (например, `123456789`)
- `session_id` (str) — идентификатор сессии Claude (например, `"eb5ac5bc-2ac6-45ad-8ca9-bb3a1a741f1e"` или временный `"_new_0001"`)

**Возвращает:** дневной номер сессии (int), полученный от `daily_session_registry.register_session()`. Например, `3` означает третью сессию за сегодня.

**Исключения:**
- `OSError` — не удалось записать файл привязок на диск

### `async def unbind_session(chat_id: int) -> None`

Отвязывает Telegram-чат от сессии (переводит в режим /all мониторинга). Если чат не был привязан — ничего не делает (идемпотентность). Сохраняет состояние на диск.

**Аргументы:**
- `chat_id` (int) — идентификатор Telegram-чата

**Возвращает:** ничего.

**Исключения:**
- `OSError` — не удалось записать файл привязок на диск

### `def get_bound_session(chat_id: int) -> str | None`

Возвращает идентификатор сессии, к которой привязан чат. Если чат в режиме /all — возвращает None.

**Аргументы:**
- `chat_id` (int) — идентификатор Telegram-чата

**Возвращает:** идентификатор сессии (str) или None, если чат не привязан к сессии (в режиме /all).

**Исключения:** нет. Это чтение из памяти — не может упасть.

### `def is_monitoring_mode(chat_id: int) -> bool`

Проверяет, находится ли чат в режиме /all (мониторинг без привязки к сессии). Вспомогательная функция для удобства — эквивалент `get_bound_session(chat_id) is None`.

**Аргументы:**
- `chat_id` (int) — идентификатор Telegram-чата

**Возвращает:** `True` если чат в режиме мониторинга (/all), `False` если привязан к конкретной сессии.

**Исключения:** нет.

### `async def switch_to_session(chat_id: int, day_number: int) -> SwitchResult`

Переключает чат на сессию по дневному номеру. Сначала ищет в дневном реестре, затем (если не нашёл) — среди всех видимых сессий через session_reader. Привязывает чат к найденной сессии.

**Аргументы:**
- `chat_id` (int) — идентификатор Telegram-чата
- `day_number` (int) — дневной номер сессии (например, `3` для команды `/3`)

**Возвращает:** объект `SwitchResult` с информацией о результате переключения.

**Исключения:**
- `OSError` — не удалось записать файл привязок на диск

### `class SwitchResult`

Результат переключения на сессию по номеру.

**Поля:**
- `found` (bool) — найдена ли сессия с указанным номером
- `session_id` (str) — идентификатор сессии (пустая строка `""` если не найдена)
- `day_number` (int) — дневной номер сессии
- `preview` (str) — превью первого сообщения сессии (пустая строка `""` если нет превью или сессия не найдена)

### `async def update_session_id(chat_id: int, old_session_id: str, new_session_id: str) -> None`

Обновляет идентификатор сессии во всех внутренних структурах: в привязке чата и в дневном реестре. Используется, когда Claude возвращает реальный session_id вместо временного `_new_XXXX`.

**Аргументы:**
- `chat_id` (int) — идентификатор Telegram-чата, привязанного к обновляемой сессии
- `old_session_id` (str) — текущий (временный) идентификатор сессии (например, `"_new_0001"`)
- `new_session_id` (str) — новый (реальный) идентификатор сессии от Claude (например, `"eb5ac5bc-2ac6-45ad-8ca9-bb3a1a741f1e"`)

**Возвращает:** ничего.

**Исключения:**
- `OSError` — не удалось записать файл привязок на диск

### `async def create_new_session(chat_id: int) -> NewSessionResult`

Создаёт новую сессию: генерирует временный идентификатор, регистрирует в дневном реестре, привязывает чат к новой сессии.

**Аргументы:**
- `chat_id` (int) — идентификатор Telegram-чата

**Возвращает:** объект `NewSessionResult` с информацией о созданной сессии.

**Исключения:**
- `OSError` — не удалось записать файл привязок на диск

### `class NewSessionResult`

Результат создания новой сессии.

**Поля:**
- `session_id` (str) — временный идентификатор новой сессии (вида `"_new_0001"`)
- `day_number` (int) — присвоенный дневной номер (например, `4`)

### `async def load_bindings() -> None`

Загружает привязки из файла `sessions.json` в память. Вызывается один раз при запуске бота (из main.py) для восстановления состояния после перезапуска. Для каждого чата с сохранённой привязкой восстанавливает состояние "подключён к сессии". Чаты без привязки остаются в режиме /all.

**Аргументы:** нет.

**Возвращает:** ничего.

**Исключения:**
- Файл не существует — молча создаёт пустые привязки (первый запуск)
- Файл повреждён (невалидный JSON) — логирует warning, создаёт пустые привязки

### `def get_all_bindings() -> dict[int, str]`

Возвращает копию всех текущих привязок. Нужна модулю session_watcher для определения, какие сессии сейчас активно используются.

**Аргументы:** нет.

**Возвращает:** словарь `{chat_id: session_id}`. Чаты в режиме /all не включаются. Если привязок нет — пустой словарь `{}`.

**Исключения:** нет.

## Внутренние функции

### `def _generate_temp_session_id() -> str`

Генерирует уникальный временный идентификатор сессии вида `_new_XXXX`, где XXXX — порядковый номер.

**Аргументы:** нет.

**Возвращает:** строку вида `"_new_0001"`, `"_new_0002"` и т.д. Счётчик увеличивается при каждом вызове.

### `async def _save_bindings() -> None`

Сохраняет привязки из памяти в файл `sessions.json`. Использует атомарную запись: сначала пишет во временный файл `.tmp`, затем переименовывает в основной.

**Аргументы:** нет.

**Возвращает:** ничего.

**Исключения:**
- `OSError` — не удалось записать или переименовать файл

### `async def _find_session_among_visible(day_number: int) -> tuple[str, str] | None`

Ищет сессию по дневному номеру среди всех видимых сессий (через session_reader), когда дневной реестр не содержит этот номер. Регистрирует найденные сессии в дневном реестре и проверяет, совпадает ли присвоенный номер с запрашиваемым.

**Аргументы:**
- `day_number` (int) — дневной номер сессии, который ищем

**Возвращает:** кортеж `(session_id, preview)` если сессия найдена, или `None` если нет.

## Алгоритм работы

### bind_session

1. Захватить asyncio Lock (защита от параллельной записи)
2. Записать в словарь привязок: `_bindings[chat_id] = session_id`
3. Зарегистрировать сессию в дневном реестре: `day_number = await daily_session_registry.register_session(session_id)` — это присвоит дневной номер (или вернёт существующий, если сессия уже зарегистрирована)
4. Вызвать `_save_bindings()` для записи на диск
5. Залогировать через logging.info: `"Чат {chat_id} привязан к сессии {session_id} (#{day_number})"`
6. Освободить Lock
7. Вернуть `day_number`

### unbind_session

1. Захватить asyncio Lock
2. Удалить привязку из словаря: `_bindings.pop(chat_id, None)` — pop с default не вызывает ошибку, если ключа нет
3. Вызвать `_save_bindings()` для записи на диск
4. Залогировать через logging.info: `"Чат {chat_id} переведён в режим мониторинга (/all)"`
5. Освободить Lock

### get_bound_session

1. Вернуть `_bindings.get(chat_id)` — None, если чат не привязан (режим /all)

### is_monitoring_mode

1. Вернуть `chat_id not in _bindings`

### switch_to_session

1. **Поиск в дневном реестре** — вызвать `daily_session_registry.get_session_id_by_number(day_number)`
2. **Если найден** — session_id известен. Получить превью: вызвать `session_reader.get_recent_sessions(config.WORKING_DIR)`, найти среди результатов сессию с этим session_id и взять preview (или пустая строка, если не нашли)
3. **Если не найден в реестре** — вызвать `_find_session_among_visible(day_number)` для поиска среди всех видимых сессий
4. **Если сессия найдена (любым способом)** — вызвать `bind_session(chat_id, session_id)` для привязки чата
5. **Сформировать результат** — вернуть `SwitchResult(found=True/False, session_id=..., day_number=..., preview=...)`

### _find_session_among_visible

1. Получить список сессий с диска: `sessions = await session_reader.get_recent_sessions(config.WORKING_DIR)`
2. Для каждой сессии из списка зарегистрировать в дневном реестре: `number = await daily_session_registry.register_session(session.session_id)`
3. Если `number == day_number` — сессия найдена, вернуть `(session.session_id, session.preview)`
4. Если ни одна сессия не получила нужный номер — вернуть None

### update_session_id

1. Захватить asyncio Lock
2. Проверить, привязан ли чат к old_session_id: `if _bindings.get(chat_id) == old_session_id`
3. Если да — обновить привязку: `_bindings[chat_id] = new_session_id`
4. Если нет — залогировать debug (чат не привязан к этой сессии, обновление не нужно)
5. Вызвать `daily_session_registry.update_session_id(old_session_id, new_session_id)` — обновить в дневном реестре
6. Вызвать `_save_bindings()` для записи на диск
7. Залогировать через logging.info: `"Session ID обновлён в привязках: {old_session_id} → {new_session_id}"`
8. Освободить Lock

### create_new_session

1. Захватить asyncio Lock
2. Вызвать `_generate_temp_session_id()` для генерации временного ID
3. Освободить Lock (перед вызовом bind_session, который захватывает Lock самостоятельно)
4. Вызвать `day_number = await bind_session(chat_id, temp_session_id)` — привязка и регистрация в реестре
5. Залогировать через logging.info: `"Создана новая сессия {temp_session_id} (#{day_number}) для чата {chat_id}"`
6. Вернуть `NewSessionResult(session_id=temp_session_id, day_number=day_number)`

### load_bindings

1. Получить из config путь к рабочей директории: `config.WORKING_DIR`
2. Построить полный путь к файлу: `{WORKING_DIR}/sessions.json`. Сохранить в `_bindings_path`
3. Попытаться прочитать файл через `asyncio.to_thread()` (чтение с диска — блокирующая операция)
4. Если файл не существует — залогировать info `"Файл привязок не найден, начинаю с чистого состояния"`, инициализировать пустой словарь
5. Если файл повреждён (невалидный JSON) — залогировать warning `"Файл привязок повреждён, начинаю с чистого состояния"`, инициализировать пустой словарь
6. Если файл прочитан — десериализовать JSON, преобразовать строковые ключи в int (JSON не поддерживает числовые ключи), записать в `_bindings`
7. Также вызвать `daily_session_registry.load_registry()` — загрузить дневной реестр
8. Залогировать info: `"Загружено {N} привязок из sessions.json"`

### get_all_bindings

1. Вернуть копию словаря `dict(_bindings)` — чтобы внешний код не мог изменить внутреннее состояние

### _generate_temp_session_id

1. Увеличить внутренний счётчик `_temp_counter` на 1
2. Вернуть строку `f"_new_{_temp_counter:04d}"` (с ведущими нулями до 4 цифр)

### _save_bindings

1. Подготовить данные: преобразовать словарь `_bindings` с ключами int в словарь с ключами str (JSON не поддерживает числовые ключи): `{str(chat_id): session_id for chat_id, session_id in _bindings.items()}`
2. Сериализовать в JSON с отступами (indent=2) и ensure_ascii=False
3. Записать во временный файл `sessions.json.tmp` через `asyncio.to_thread()`
4. Переименовать `sessions.json.tmp` в `sessions.json` через `os.replace()` (атомарная операция на macOS)

## Зависимости

- **config** — `WORKING_DIR` — путь к рабочей директории проекта, используется для определения пути к `sessions.json` и передачи в `session_reader.get_recent_sessions()`
- **daily_session_registry** — `register_session()`, `get_session_id_by_number()`, `update_session_id()`, `load_registry()` — управление дневными номерами сессий. session_manager координирует вызовы: регистрирует сессии при привязке, ищет по номеру при переключении, обновляет ID при замене временного на реальный
- **session_reader** — `get_recent_sessions()` — получение списка видимых сессий для поиска при переключении по номеру (/N), когда сессия не найдена в дневном реестре; также для получения превью при переключении
- **asyncio** (стандартная библиотека) — `asyncio.Lock` для защиты от параллельной записи, `asyncio.to_thread()` для блокирующих файловых операций
- **json** (стандартная библиотека) — сериализация/десериализация привязок в sessions.json
- **os** (стандартная библиотека) — `os.replace()` для атомарного переименования файлов, `os.path.join()` для построения путей
- **pathlib** (стандартная библиотека) — `Path` для работы с путями к файлам
- **logging** (стандартная библиотека) — логирование событий
- **dataclasses** (стандартная библиотека) — `@dataclass` для определения классов `SwitchResult` и `NewSessionResult`

## Формат файла sessions.json

```json
{
  "123456789": "eb5ac5bc-2ac6-45ad-8ca9-bb3a1a741f1e",
  "987654321": "_new_0003"
}
```

**Структура:**
- Ключ — chat_id в виде строки (JSON не поддерживает числовые ключи)
- Значение — session_id (UUID реальной сессии или временный `_new_XXXX`)
- Чаты в режиме /all не записываются в файл — отсутствие chat_id означает режим мониторинга

## Внутреннее состояние модуля

- `_bindings: dict[int, str]` — словарь привязок `{chat_id: session_id}`. Чаты в режиме /all отсутствуют в словаре
- `_lock: asyncio.Lock` — блокировка для защиты от параллельного чтения/записи
- `_bindings_path: Path | None` — путь к файлу `sessions.json` (определяется при `load_bindings()`, до вызова — None)
- `_temp_counter: int` — счётчик для генерации временных session_id. Начинается с 0, увеличивается при каждом вызове `_generate_temp_session_id()`

## Обработка ошибок

- **Файл sessions.json не существует** — нормальная ситуация при первом запуске. Создаются пустые привязки, все чаты будут в режиме /all. Логирование: info
- **Файл sessions.json повреждён (невалидный JSON)** — создаются пустые привязки. Логирование: warning. Повреждённый файл будет перезаписан при следующей операции привязки
- **Ошибка записи на диск (OSError)** — ошибка проксируется наверх (в вызывающий код: bot или main). Данные в памяти остаются консистентными — привязка уже записана в `_bindings`. При следующей успешной записи состояние синхронизируется с диском. Вызывающий код должен залогировать ошибку и сообщить пользователю
- **Параллельный доступ** — защита через asyncio.Lock. Два одновременных вызова bind_session или unbind_session не потеряют данные
- **Дневной номер не найден при переключении** — `switch_to_session` сначала ищет в дневном реестре, затем среди всех видимых сессий. Если не найден нигде — возвращает `SwitchResult(found=False, ...)`. Вызывающий код (bot) сообщает пользователю об ошибке
- **update_session_id с несуществующим old_session_id** — если чат не привязан к old_session_id, привязка не меняется. Обновление в daily_session_registry тоже идемпотентно. Логирование: debug
- **load_bindings вызван до config.load_config** — WORKING_DIR будет не инициализирован. Вызывающий код (main.py) обязан сначала загрузить конфигурацию
- **Запись во временный файл прошла, но переименование не удалось** — временный файл остаётся на диске. При следующей записи он будет перезаписан. Основной файл `sessions.json` остаётся нетронутым (целостность данных)
- **chat_id из sessions.json не является числом** — при загрузке привязок из JSON ключи конвертируются в int через `int()`. Если конвертация не удалась — запись пропускается с логированием warning

## Константы

- `BINDINGS_FILENAME = "sessions.json"` — имя файла привязок. Вынесено в константу для единообразия с daily_session_registry и возможности изменения
- `BINDINGS_TEMP_SUFFIX = ".tmp"` — суффикс временного файла при атомарной записи. Временный файл: `sessions.json.tmp`
- `TEMP_SESSION_PREFIX = "_new_"` — префикс временных session_id. Используется при генерации и может использоваться для проверки, является ли session_id временным
- `TEMP_SESSION_ID_WIDTH = 4` — ширина числовой части временного session_id (количество цифр с ведущими нулями). `_new_0001`, не `_new_1`

## Тест-план

### Юнит-тесты

- **test_bind_session_stores_binding** — привязка сохраняется в памяти
  - Вход: `bind_session(123456789, "abc-def-123")`
  - Ожидаемый результат: `get_bound_session(123456789)` возвращает `"abc-def-123"`
  - Тип: unit

- **test_bind_session_returns_day_number** — привязка возвращает дневной номер от реестра
  - Вход: `bind_session(123456789, "abc-def-123")` (мок daily_session_registry.register_session возвращает 3)
  - Ожидаемый результат: возвращает `3`
  - Тип: unit

- **test_bind_session_saves_to_disk** — после привязки данные записываются в файл
  - Вход: `bind_session(123456789, "abc-def-123")`
  - Ожидаемый результат: файл `sessions.json` существует и содержит `{"123456789": "abc-def-123"}`
  - Тип: unit

- **test_bind_session_overwrites_previous** — повторная привязка перезаписывает предыдущую сессию
  - Вход: `bind_session(123456789, "first-session")`, затем `bind_session(123456789, "second-session")`
  - Ожидаемый результат: `get_bound_session(123456789)` возвращает `"second-session"`
  - Тип: unit

- **test_unbind_session_removes_binding** — отвязка удаляет привязку
  - Вход: `bind_session(123456789, "abc-def-123")`, затем `unbind_session(123456789)`
  - Ожидаемый результат: `get_bound_session(123456789)` возвращает `None`
  - Тип: unit

- **test_unbind_session_saves_to_disk** — после отвязки файл обновляется
  - Вход: `bind_session(123456789, "abc-def-123")`, затем `unbind_session(123456789)`
  - Ожидаемый результат: файл `sessions.json` содержит `{}`
  - Тип: unit

- **test_get_bound_session_returns_none_for_unbound** — для непривязанного чата возвращается None
  - Вход: `get_bound_session(999999999)` (чат никогда не привязывался)
  - Ожидаемый результат: `None`
  - Тип: unit

- **test_is_monitoring_mode_true_when_unbound** — в режиме /all is_monitoring_mode возвращает True
  - Вход: `unbind_session(123456789)`, затем `is_monitoring_mode(123456789)`
  - Ожидаемый результат: `True`
  - Тип: unit

- **test_is_monitoring_mode_false_when_bound** — при привязке к сессии возвращает False
  - Вход: `bind_session(123456789, "abc-def-123")`, затем `is_monitoring_mode(123456789)`
  - Ожидаемый результат: `False`
  - Тип: unit

- **test_switch_to_session_found_in_registry** — переключение на сессию, найденную в дневном реестре
  - Вход: мок daily_session_registry.get_session_id_by_number(3) возвращает `"abc-def-123"`, `switch_to_session(123456789, 3)`
  - Ожидаемый результат: `SwitchResult(found=True, session_id="abc-def-123", day_number=3, preview=...)`
  - Тип: unit

- **test_switch_to_session_not_found** — переключение на несуществующий номер
  - Вход: мок daily_session_registry.get_session_id_by_number(99) возвращает None, мок session_reader.get_recent_sessions возвращает пустой список; `switch_to_session(123456789, 99)`
  - Ожидаемый результат: `SwitchResult(found=False, session_id="", day_number=99, preview="")`
  - Тип: unit

- **test_create_new_session_generates_temp_id** — создание новой сессии генерирует временный ID
  - Вход: `create_new_session(123456789)`
  - Ожидаемый результат: `NewSessionResult` с session_id начинающимся на `"_new_"` и day_number > 0
  - Тип: unit

- **test_create_new_session_increments_counter** — каждый вызов генерирует уникальный ID
  - Вход: `create_new_session(123456789)` дважды
  - Ожидаемый результат: session_id первого != session_id второго
  - Тип: unit

- **test_update_session_id_updates_binding** — обновление session_id меняет привязку
  - Вход: `bind_session(123456789, "_new_0001")`, затем `update_session_id(123456789, "_new_0001", "real-session-id")`
  - Ожидаемый результат: `get_bound_session(123456789)` возвращает `"real-session-id"`
  - Тип: unit

- **test_update_session_id_calls_registry_update** — обновление вызывает daily_session_registry.update_session_id
  - Вход: `update_session_id(123456789, "_new_0001", "real-session-id")`
  - Ожидаемый результат: `daily_session_registry.update_session_id` вызван с аргументами `("_new_0001", "real-session-id")`
  - Тип: unit

- **test_load_and_save_roundtrip** — данные сохраняются и корректно восстанавливаются
  - Вход: привязать 2 чата к сессиям, сбросить внутреннее состояние, вызвать `load_bindings()`
  - Ожидаемый результат: обе привязки восстановлены с правильными значениями
  - Тип: unit

- **test_get_all_bindings_returns_copy** — возвращается копия, не ссылка на внутренний словарь
  - Вход: привязать чат, получить `get_all_bindings()`, изменить возвращённый словарь
  - Ожидаемый результат: `get_bound_session()` по-прежнему возвращает исходное значение
  - Тип: unit

- **test_get_all_bindings_excludes_monitoring** — чаты в режиме /all не включаются
  - Вход: привязать чат 111 к сессии, отвязать чат 222
  - Ожидаемый результат: `get_all_bindings()` содержит только `{111: "..."}`
  - Тип: unit

### Граничные случаи

- **test_unbind_already_unbound_chat** — отвязка непривязанного чата не вызывает ошибку (идемпотентность)
  - Вход: `unbind_session(999999999)` (чат никогда не привязывался)
  - Ожидаемый результат: функция завершается без ошибки
  - Тип: edge case

- **test_bind_same_session_twice** — привязка к той же сессии не дублирует записи
  - Вход: `bind_session(123456789, "abc-def-123")` дважды
  - Ожидаемый результат: `get_bound_session(123456789)` возвращает `"abc-def-123"`, файл содержит одну запись
  - Тип: edge case

- **test_multiple_chats_same_session** — несколько чатов могут быть привязаны к одной сессии
  - Вход: `bind_session(111, "abc-def-123")` и `bind_session(222, "abc-def-123")`
  - Ожидаемый результат: оба чата привязаны к одной сессии, `get_all_bindings()` содержит обе записи
  - Тип: edge case

- **test_concurrent_bind_and_unbind** — параллельные операции не теряют данные
  - Вход: `asyncio.gather(bind_session(111, "aaa"), unbind_session(222), bind_session(333, "ccc"))`
  - Ожидаемый результат: все операции применены корректно
  - Тип: edge case

- **test_switch_to_session_found_among_visible** — сессия не в реестре, но найдена среди видимых
  - Вход: мок daily_session_registry.get_session_id_by_number(5) возвращает None; мок session_reader.get_recent_sessions возвращает список с 5 сессиями; мок daily_session_registry.register_session возвращает последовательно 1, 2, 3, 4, 5; `switch_to_session(123456789, 5)`
  - Ожидаемый результат: `SwitchResult(found=True, session_id=..., day_number=5, preview=...)`
  - Тип: edge case

- **test_update_session_id_chat_not_bound_to_old** — обновление, когда чат привязан к другой сессии
  - Вход: `bind_session(123456789, "other-session")`, затем `update_session_id(123456789, "_new_0001", "real-id")`
  - Ожидаемый результат: привязка чата не меняется (остаётся `"other-session"`), но daily_session_registry.update_session_id всё равно вызывается
  - Тип: edge case

- **test_generate_temp_id_format** — формат временного ID соответствует ожиданиям
  - Вход: вызвать `_generate_temp_session_id()` один раз
  - Ожидаемый результат: строка начинается с `"_new_"`, после неё ровно 4 цифры
  - Тип: edge case

- **test_load_bindings_with_string_chat_ids** — ключи из JSON корректно преобразуются в int
  - Вход: файл sessions.json с содержимым `{"123456789": "abc-def"}`
  - Ожидаемый результат: `get_bound_session(123456789)` возвращает `"abc-def"` (ключ-число, не строка)
  - Тип: edge case

### Тесты ошибок

- **test_load_missing_file_creates_empty_bindings** — отсутствие файла не вызывает ошибку
  - Вход: `load_bindings()` при несуществующем файле sessions.json
  - Ожидаемый результат: пустые привязки, все чаты в режиме /all, logging.info вызван
  - Тип: error

- **test_load_corrupted_json_creates_empty_bindings** — повреждённый JSON не ломает модуль
  - Вход: файл sessions.json с содержимым `"not valid json {{{"`
  - Ожидаемый результат: пустые привязки, logging.warning вызван с сообщением о повреждении
  - Тип: error

- **test_save_to_readonly_directory_raises_oserror** — ошибка записи проксируется наверх
  - Вход: `_bindings_path` указывает на read-only директорию, вызов `bind_session(123456789, "abc")`
  - Ожидаемый результат: `OSError` проксируется вызывающему коду
  - Тип: error

- **test_atomic_write_preserves_original_on_failure** — при ошибке записи оригинальный файл не повреждён
  - Вход: существующий sessions.json. Мокнуть `os.replace()` чтобы кинул OSError. Вызвать `bind_session(123456789, "new-session")`
  - Ожидаемый результат: оригинальный sessions.json не изменён, ошибка проксируется наверх
  - Тип: error

- **test_load_bindings_invalid_chat_id_key** — невалидный ключ в JSON пропускается
  - Вход: файл sessions.json с содержимым `{"not_a_number": "abc-def", "123456789": "ghi-jkl"}`
  - Ожидаемый результат: загружена только привязка для 123456789, logging.warning вызван для `"not_a_number"`
  - Тип: error
