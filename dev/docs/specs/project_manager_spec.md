# Спецификация модуля: project_manager

Дата: 10-04-2026
Слой: 3 (зависит от слоёв 0-2: config, process_manager, session_manager, daily_session_registry, session_watcher)
Файл: `src/claude_manager/project_manager.py`

## Назначение

Управляет проектами в Telegram-боте: сканирует корневую папку с проектами и возвращает список, атомарно переключает бот между проектами без перезапуска, запоминает последний выбранный проект и восстанавливает его при старте. Это центральный модуль фичи «Переключение между проектами».

Модуль не знает о Telegram API — он только работает с файловой системой и координирует state-модули при переключении. Верхний слой (`bot.py`) вызывает его функции в ответ на команды пользователя `/projects` и `/pN`.

## Обслуживаемые сценарии

- **CJM-11: Переключение между проектами** — пользователь вызывает команду `/projects`, получает список всех доступных проектов из `PROJECTS_ROOT_DIR`, кликает на нужный командой `/pN`, бот атомарно переключается на выбранный проект

## Публичный API

### `async def scan_available_projects() -> list[ProjectInfo]`

Возвращает отсортированный по имени список доступных проектов из `config.PROJECTS_ROOT_DIR`. Фильтрует содержимое папки: только директории (не файлы), не скрытые (имя не начинается с точки), не символические ссылки.

**Аргументы:** нет.

**Возвращает:** список `ProjectInfo` — каждый элемент описывает один проект (имя, абсолютный путь, флаг текущего). Если папка не существует или нет прав на чтение — возвращается пустой список, в лог пишется warning.

**Исключения:** нет. Все ошибки обрабатываются внутри и приводят к возврату пустого списка.

### `async def switch_project(target_path: str) -> SwitchResult`

Атомарно переключает бота на новый проект. Проверяет валидность пути, останавливает все запущенные процессы Claude, сбрасывает внутреннее состояние state-модулей, обновляет `config.WORKING_DIR`, перезагружает файлы состояния нового проекта, сохраняет выбор в `LAST_PROJECT_FILE`.

Использует `asyncio.Lock` (`_switch_lock`) — параллельные вызовы выполняются последовательно. При сбое в середине переключения пытается откатить `config.WORKING_DIR` к старому значению.

**Аргументы:**
- `target_path` (str) — абсолютный путь к целевой папке проекта

**Возвращает:** `SwitchResult` с полями `success`, `already_active`, `old_path`, `new_path`, `stopped_processes_count`, `error_message`.

**Исключения:** нет. Ошибки (невалидный путь, сбой сброса state) возвращаются через поле `error_message`, `success=False`.

### `def get_current_project_path() -> str`

Возвращает абсолютный путь к текущему активному проекту (тонкая обёртка над `config.WORKING_DIR`).

**Аргументы:** нет.

**Возвращает:** строка — абсолютный путь.

**Исключения:** нет.

### `async def save_selected_project(path: str) -> None`

Атомарно записывает путь к выбранному проекту в `config.LAST_PROJECT_FILE` (файл `~/.claude-manager-current-project`). Используется `switch_project` после успешного переключения для сохранения выбора между запусками бота.

**Аргументы:**
- `path` (str) — абсолютный путь к проекту для сохранения

**Возвращает:** ничего.

**Исключения:** нет. Сбой записи логируется на уровне error, но исключение наверх не пробрасывается — неспособность сохранить файл не должна отменять успешное переключение.

### `async def load_last_selected_project() -> str | None`

Читает `config.LAST_PROJECT_FILE` и возвращает путь к последнему выбранному проекту. Валидирует путь — если проект удалён или вне `PROJECTS_ROOT_DIR`, возвращает None. Вызывается в `main.py` при старте бота для восстановления выбранного проекта.

**Аргументы:** нет.

**Возвращает:** строка с путём или None при любых ошибках (файла нет, файл повреждён, путь невалиден).

**Исключения:** нет.

## Внутренние функции

### `_paths_point_to_same_dir(first_path: str, second_path: str) -> bool`

Сравнивает два пути по их реальному расположению (после раскрытия символических ссылок через `os.path.realpath`). Нужна для проверки «уже в этом проекте» в `switch_project`.

### `_is_path_inside_root(target_path: str, root_path: str) -> bool`

Проверяет, что `target_path` строго внутри `root_path` — защита от path traversal. Сравнивает realpath обоих путей и использует `startswith(root + os.sep)` — это гарантирует что `/root/foo-bar` не совпадёт с `/root/foo`. Совпадение с самим корнем возвращает False — нельзя переключиться на сам `PROJECTS_ROOT_DIR`.

### `_validate_target_path(target_path: str) -> None`

Проверяет путь перед переключением: существует, это директория, внутри `PROJECTS_ROOT_DIR`, доступна на чтение. При любой ошибке бросает `ProjectSwitchError` с понятным сообщением на русском.

### `_should_include_project(entry_name: str, entry_full_path: str) -> bool`

Решает, нужно ли включать запись в результат `scan_available_projects`. Возвращает False для скрытых папок (начинаются с точки), символических ссылок, не-директорий.

### `_list_project_entries() -> list[str]`

Блокирующая обёртка над `os.listdir(config.PROJECTS_ROOT_DIR)` — выносится в поток через `asyncio.to_thread`.

### `_build_project_info(entry_name: str) -> ProjectInfo`

Собирает `ProjectInfo` для одной записи: вычисляет полный путь, определяет `is_current` через `_paths_point_to_same_dir`.

### `_reset_all_state_modules() -> None`

Последовательно вызывает `reset_state()` у четырёх state-модулей: `session_manager`, `daily_session_registry`, `session_watcher`. Используется `switch_project` и `_rollback_switch`.

### `_perform_switch(target_path: str) -> int`

Основное действие переключения: останавливает все процессы Claude через `process_manager.stop_all_processes`, меняет `config.WORKING_DIR`, сбрасывает state. Возвращает количество остановленных процессов. Если бросит исключение — вызывается `_rollback_switch`.

### `_rollback_switch(old_path: str) -> None`

Пытается восстановить старое значение `config.WORKING_DIR` и перезагрузить state старого проекта после сбоя в `_perform_switch`. Ошибка в самом откате логируется, но наверх не поднимается — иначе пользователь не получит осмысленное сообщение об исходной ошибке.

## Классы данных

### `ProjectInfo` (frozen dataclass)

Информация об одном проекте. Поля:
- `name: str` — имя папки проекта (последний компонент пути)
- `absolute_path: str` — абсолютный путь к папке
- `is_current: bool` — True если это текущий активный проект бота

### `SwitchResult` (frozen dataclass)

Результат попытки переключения. Поля:
- `success: bool` — True если переключение удалось (или это был already_active)
- `already_active: bool` — True если целевой проект совпадает с текущим (no-op)
- `old_path: str` — путь к проекту ДО переключения
- `new_path: str` — путь к целевому проекту
- `stopped_processes_count: int` — сколько процессов Claude было остановлено (0 при already_active или ошибке)
- `error_message: str` — причина ошибки или пустая строка при успехе

### `ProjectSwitchError` (Exception)

Исключение для ошибок валидации пути в `_validate_target_path`. Содержит понятное сообщение на русском.

## Алгоритм работы

### scan_available_projects

1. Проверить что `config.PROJECTS_ROOT_DIR` существует (`os.path.isdir`). Если нет — залогировать warning и вернуть пустой список
2. Прочитать содержимое папки через `asyncio.to_thread(_list_project_entries)`. При `OSError` — залогировать warning, вернуть пустой список
3. Для каждой записи собрать полный путь, применить фильтр `_should_include_project`. Если прошёл — создать `ProjectInfo` через `_build_project_info`
4. Отсортировать результат по имени (lowercase) для стабильного порядка
5. Вернуть список

### switch_project

1. Войти в `async with _switch_lock` — сериализация параллельных вызовов
2. Запомнить `old_path = config.WORKING_DIR`
3. Валидировать `target_path` через `_validate_target_path`. При `ProjectSwitchError` — залогировать warning и вернуть `SwitchResult(success=False, error_message=...)`
4. Проверить `_paths_point_to_same_dir(target_path, old_path)`. Если True — вернуть `SwitchResult(success=True, already_active=True)` без выполнения переключения
5. Вызвать `_perform_switch(target_path)`. При любом `Exception` — вызвать `_rollback_switch(old_path)`, залогировать error, вернуть `SwitchResult(success=False, error_message=...)`
6. Вызвать `save_selected_project(target_path)` — ошибка внутри не отменяет успех переключения
7. Залогировать info с указанием старого и нового путей и количества остановленных процессов
8. Вернуть `SwitchResult(success=True, already_active=False, old_path, new_path, stopped_processes_count)`

### load_last_selected_project

1. Проверить `config.LAST_PROJECT_FILE.exists()`. Если нет — вернуть None
2. Прочитать файл через `asyncio.to_thread(last_file.read_text, 'utf-8')`. При `OSError` — залогировать warning, вернуть None
3. Получить `stored_path = content.strip()`. Если пусто — вернуть None
4. Валидировать через `_validate_target_path(stored_path)`. При `ProjectSwitchError` — залогировать warning (проект удалён или вне корня), вернуть None
5. Вернуть `stored_path`

### save_selected_project

1. Вычислить `temp_file = last_file.with_name(last_file.name + '.tmp')`
2. Записать путь в temp_file через `asyncio.to_thread(temp_file.write_text, path, 'utf-8')`
3. Переименовать через `asyncio.to_thread(os.replace, temp_file, last_file)` — атомарное переименование
4. При `OSError` в любом шаге — залогировать error с exc_info, НЕ бросать исключение

## Зависимости

Модуль зависит от стандартной библиотеки и пяти других модулей проекта:

- **asyncio** (стандартная библиотека) — `Lock`, `to_thread` для сериализации и выноса блокирующих операций
- **os** (стандартная библиотека) — `path.exists`, `path.isdir`, `path.islink`, `path.realpath`, `path.join`, `access`, `listdir`, `replace`
- **logging** (стандартная библиотека) — логирование результатов и ошибок
- **dataclasses** (стандартная библиотека) — `dataclass` для `ProjectInfo` и `SwitchResult`
- **pathlib.Path** (стандартная библиотека) — работа с `LAST_PROJECT_FILE`
- **claude_manager.config** — `WORKING_DIR`, `PROJECTS_ROOT_DIR`, `LAST_PROJECT_FILE`
- **claude_manager.process_manager** — `stop_all_processes`
- **claude_manager.session_manager** — `reset_state`
- **claude_manager.daily_session_registry** — `reset_state`
- **claude_manager.session_watcher** — `reset_state`

## Обработка ошибок

- **`PROJECTS_ROOT_DIR` не существует** — `scan_available_projects` возвращает пустой список, warning в логе. Обработчик `/projects` в `bot.py` показывает сообщение `Проекты не найдены в папке <root>`
- **Нет прав на чтение `PROJECTS_ROOT_DIR`** — `OSError` внутри `_list_project_entries` ловится в `scan_available_projects`, возвращается пустой список
- **Целевая папка не существует** — `_validate_target_path` бросает `ProjectSwitchError("Папка не существует: <путь>")`. `switch_project` возвращает `SwitchResult(success=False)`
- **Целевой путь указывает на файл** — `ProjectSwitchError("Это не папка: <путь>")`
- **Целевой путь вне `PROJECTS_ROOT_DIR`** (path traversal или внешний симлинк) — `ProjectSwitchError("Путь вне корневой папки проектов: <путь>")`
- **Нет прав на чтение целевой папки** — `ProjectSwitchError("Нет прав на чтение папки: <путь>")`
- **Ошибка в `_reset_all_state_modules` (сброс state)** — `switch_project` ловит исключение, вызывает `_rollback_switch(old_path)`, возвращает `SwitchResult(success=False, error_message="Ошибка переключения: ...")`
- **Ошибка во время отката (`_rollback_switch`)** — логируется на уровне error с `exc_info`, но не пробрасывается — иначе пользователь не получит исходное сообщение об ошибке
- **Ошибка записи `LAST_PROJECT_FILE`** — логируется, но `switch_project` продолжает работу и возвращает успех — неспособность сохранить выбор не должна отменять переключение
- **Повреждённый `LAST_PROJECT_FILE`** — `load_last_selected_project` возвращает None, бот стартует с `CLAUDE_WORKING_DIR` из `.env`
- **Путь в `LAST_PROJECT_FILE` невалиден** (проект удалён, вне корня) — `_validate_target_path` бросает, `load_last_selected_project` ловит и возвращает None

## Константы

- `_LAST_PROJECT_TEMP_SUFFIX = ".tmp"` — суффикс временного файла при атомарной записи
- `_switch_lock = asyncio.Lock()` — блокировка параллельных вызовов `switch_project`

## Тест-план

### Юнит-тесты (tests/test_project_manager.py)

**TestScanAvailableProjects (6 тестов):**

- **test_returns_only_directories** — файлы (`.txt`, `.zip`) отфильтровываются, возвращаются только папки. Вход: папка с тремя директориями и двумя файлами. Ожидаемый результат: три ProjectInfo. Тип: unit
- **test_filters_hidden_dirs** — папки начинающиеся с точки (`.git`, `.DS_Store`) не попадают в список. Тип: unit
- **test_filters_symlinks** — символические ссылки исключаются (защита от выхода за границы). Тип: unit
- **test_empty_root_returns_empty_list** — пустая корневая папка даёт пустой список. Тип: edge case
- **test_nonexistent_root_returns_empty_list** — несуществующая папка даёт пустой список без исключения. Тип: edge case
- **test_marks_current_project** — проект, путь которого совпадает с `config.WORKING_DIR`, помечен `is_current=True`. Тип: unit

**TestSwitchProject (11 тестов):**

- **test_happy_path** — успешное переключение на валидный проект: `success=True`, `config.WORKING_DIR` обновился. Тип: unit
- **test_already_active** — переключение на текущий проект: `already_active=True`, state не сбрасывается (проверяется через моки). Тип: unit
- **test_path_traversal_blocked** — попытка переключиться на папку вне `PROJECTS_ROOT_DIR`: `success=False`, сообщение про границы корня. Тип: error
- **test_nonexistent_path_fails** — несуществующий путь: `success=False`, сообщение про отсутствие папки. Тип: error
- **test_path_is_file_not_dir_fails** — путь на файл: `success=False`, сообщение что это не папка. Тип: error
- **test_stops_all_processes** — мокается `process_manager.stop_all_processes`, проверяется вызов и передача количества. Тип: unit
- **test_resets_all_state_modules** — мокаются `reset_state` у трёх модулей, проверяется что вызваны ровно один раз. Тип: unit
- **test_saves_to_last_project_file** — после успешного переключения файл `LAST_PROJECT_FILE` содержит путь к новому проекту. Тип: unit
- **test_rollback_on_reset_error** — один из `reset_state` бросает исключение, `config.WORKING_DIR` восстанавливается к старому значению, `success=False`, файл `LAST_PROJECT_FILE` НЕ создан. Тип: error
- **test_concurrent_switches_serialized** — два параллельных `switch_project` через `asyncio.gather` выполняются последовательно, без перемешивания state. Тип: edge case

**TestLoadLastSelectedProject (4 теста):**

- **test_no_file_returns_none** — файла нет, возвращается None. Тип: edge case
- **test_empty_file_returns_none** — пустой файл возвращает None. Тип: edge case
- **test_invalid_path_in_file_returns_none** — файл содержит несуществующий путь, возвращается None. Тип: error
- **test_valid_path_returns_path** — валидный путь возвращается как есть. Тип: unit

**TestSaveSelectedProject (2 теста):**

- **test_writes_file_with_path** — файл создан, содержит путь. Тип: unit
- **test_io_error_logged_not_raised** — при ошибке записи функция не бросает исключение. Тип: error

**TestGetCurrentProjectPath (1 тест):**

- **test_returns_config_working_dir** — возвращает текущее значение `config.WORKING_DIR`. Тип: unit

### Интеграционные тесты (tests/integration/test_project_switching.py)

- **test_switch_between_two_projects** — полный цикл: создать два проекта во временной директории, зарегистрировать привязку и сессию в A, переключиться на B (проверить сброс), вернуться в A (проверить восстановление привязки и сессии). Тип: integration
- **test_switch_to_same_project_is_noop** — переключение на текущий проект возвращает `already_active=True`, привязки сохраняются. Тип: integration
- **test_switch_stops_running_processes** — создать фейковые процессы в `process_manager._processes`, переключиться, проверить что `_processes` пустой и `stopped_processes_count` корректен. Тип: integration
- **test_switch_to_nonexistent_fails_gracefully** — несуществующий путь возвращает `success=False`, `config.WORKING_DIR` не меняется, привязки остаются. Тип: integration
- **test_path_traversal_attack_blocked** — попытка переключиться на папку вне корня блокируется, state не меняется. Тип: security
