# Спецификация модуля: current_backend_registry

Дата: 06-05-2026
Слой: 1 (зависит от слоя 0 — `coding_agent_backend` для `BackendName`, `config` для пути к файлу)
Файл: `src/claude_manager/current_backend_registry.py`

**Связанные спеки:**
- `coding_agent_backend_spec.md` — абстрактный интерфейс CLI-бэкенда (источник `BackendName`)
- `claude_code_backend_spec.md` — конкретная реализация для Claude Code CLI (одно из значений Enum)
- `codex_backend_spec.md` — конкретная реализация для Codex CLI (второе значение Enum, спека ещё не написана)

## Назначение

Глобальное персистентное хранилище имени текущего CLI-бэкенда (Claude Code или Codex). Один файл `~/.claude-manager-current-backend` на пользователя — содержит JSON `{"backend": "claude"}` или `{"backend": "codex"}`. Модуль предоставляет три синхронные функции (`get_current`, `set_current`, `load_state`) поверх module-level переменной — это полный аналог архитектуры существующего `silence_mode_registry.py`, но хранит не `bool`, а enum `BackendName`. Гранулярность переключения — глобальная (одна переменная на бот, не per-project и не per-session, см. концептуальное решение в `dev/docs/session-reports/06-05/06-38_codex-backend-concept-planning.md`).

**Атомарный контракт `set_current`.** Переключение бэкенда атомарно по принципу «диск + память либо вместе, либо никак». При ошибке записи на диск (`OSError`) in-memory переменная откатывается на предыдущее значение, и исключение пробрасывается наружу. При невозможности переключения из-за неудачной загрузки на старте (`_loaded_from_disk == False`) — выбрасывается `RuntimeError` ДО любых изменений памяти и диска. Цель — `bot.py` всегда говорит пользователю правду: либо «переключилось» (память и диск согласованы, переживёт рестарт), либо «не получилось» (ничего не изменилось). Промежуточного «вроде переключилось, но не сохранилось» — не существует.

## Расхождение с концепцией от 06-05-2026

В концептуальной сессии 06-05 (`dev/docs/session-reports/06-05/06-38_codex-backend-concept-planning.md`) было зафиксировано: «один файл `~/.claude-manager-current-backend` с **содержимым `claude` или `codex`**» — то есть plain text, без JSON-обёртки. В этой спецификации формат изменён на JSON-объект `{"backend": "claude"}` или `{"backend": "codex"}`.

**Причина изменения:**
- **Единообразие со всеми остальными state-файлами проекта.** `silence_mode_registry.py` (`{"enabled": true}`), `daily_session_registry` (`daily_sessions.json`), `session_manager` (`sessions.json`), `unread_buffer` — все используют JSON. Plain text был бы единственным исключением, что усложнило бы навигацию по коду («что за формат у этого файла?»).
- **Расширяемость без миграции.** Если в будущем понадобится записать рядом с именем бэкенда дополнительные поля (например, `last_changed_at: ISO-8601` для аудита, или `forced_until: ISO-8601` для временного локапа), JSON позволит это без миграции старых файлов — `load_state` будет игнорировать отсутствующие поля.
- **Безопаснее парсится.** `BackendName(plain_text.strip())` молча примет невидимые пробелы или BOM как часть значения и упадёт с малопонятным `ValueError`. `json.loads` явно отфильтровывает мусор и даёт диагностируемую ошибку.

Семантика и UX-эффект концепции не меняются — пользователь всё так же видит бэкенд через `/agent`, переключение глобальное, файл лежит в той же директории под тем же именем. Меняется только внутренний формат — пользователь его обычно не открывает руками.

## Обслуживаемые сценарии

Модуль не обслуживает ни один из текущих CJM напрямую — он инфраструктура для нового сценария, которого в BRD пока нет:

- **CJM-NEW: Переключение бэкенда (/agent)** — будет добавлен в `dev/docs/brd/brd-user-journeys.md` параллельно с реализацией команды `/agent` в `bot.py`. Сценарий: пользователь шлёт `/agent` → бот показывает inline-клавиатуру с двумя кнопками (текущий — отмеченный галочкой, второй — для переключения) → пользователь жмёт на нужный → бот вызывает `set_current(BackendName.X)` → следующая новая сессия идёт через выбранный CLI. На момент написания этой спеки CJM-NEW ещё не зафиксирован в BRD; реализация модуля не блокируется этим — модуль независим от текста BRD, но при добавлении CJM-NEW спека потребителя (`bot.py`) должна быть обновлена.

Также модуль участвует во всех существующих сценариях, требующих запуска subprocess CLI, через верхний Telegram-слой:

- **CJM-02 / CJM-03 / CJM-04 / CJM-06** — `bot.py` / `claude_interaction.py` выбирают backend до вызова `process_manager`. Для новой сессии верхний слой читает `current_backend_registry.get_current()` и передаёт явный `backend` в `session_manager.create_new_session(...)` и `process_manager.send_message(..., backend=...)`. Для существующей сессии backend берётся из `ActiveSession` или `DailySessionEntry`. `process_manager` не импортирует `current_backend_registry` и не делает fallback при `backend=None`.
- **CJM-05 / CJM-07** — `bot.py` (для `/sessions`) и `session_watcher` (для `/all`) могут использовать `get_current()` опционально для отображения значка/метки текущего бэкенда; основной канал чтения сессий идёт через цикл по `get_all_backends()`, а не через `get_current()`

## Публичный API

### `get_current() -> BackendName`

Возвращает имя текущего активного бэкенда. Синхронная функция, не делает I/O (читает только in-memory переменную модуля).

**Аргументы:** нет.

**Возвращает:** `BackendName`. Если `load_state()` ещё не вызывался или упал с непредвиденной ошибкой — возвращает дефолт `BackendName.CLAUDE` (см. константу `DEFAULT_BACKEND`).

**Исключения:** не выбрасывает.

### `set_current(name: BackendName) -> None`

Устанавливает новый активный бэкенд по принципу «диск + память либо вместе, либо никак». Реализация — временное обновление in-memory переменной + атомарная запись на диск + откат памяти при ошибке записи (детали — в разделе «Алгоритм работы → set_current»). С точки зрения внешнего наблюдателя гарантия одна: после возврата управления состояние памяти и диска всегда согласовано — либо оба содержат новый бэкенд (успех), либо оба содержат старый (любое исключение). Синхронная функция, делает один атомарный I/O (запись `tmp` → `rename`).

Промежуточного состояния «in-memory переключено, на диск не записано» снаружи модуля не существует — окно временного обновления in-memory закрыто внутри одного синхронного вызова `set_current` и не наблюдаемо вызывающей стороной. Это сознательный отказ от прежнего «soft-fail» поведения: бот не должен говорить пользователю «переключилось», если на диске изменение не закреплено.

**Аргументы:**
- `name` (`BackendName`) — имя бэкенда из enum. Тип строго `BackendName`, не `str` (тип-подсказка обязательна; отдать строку напрямую нельзя — это контрактное нарушение, рантайм-проверки isinstance в коде нет, ответственность вызывающей стороны)

**Возвращает:** `None`.

**Исключения:**
- `RuntimeError` — если `_loaded_from_disk == False` (загрузка с диска не прошла из-за `PermissionError` или `OSError` при старте). In-memory переменная НЕ обновляется, на диск ничего не пишется, `_save_state` не вызывается, никакого «soft-fail» нет. Сообщение исключения: `"Текущий бэкенд не загружен с диска — переключение невозможно до перезапуска бота. Подробности — в логах load_state."`. Цель — `bot.py` ловит исключение и сообщает пользователю в Telegram точную причину отказа, а не врёт «переключилось только в памяти».
- `OSError` — при сбое записи (диск переполнен, права отозваны, raid disconnect и т. п.). Перехватывается узким `except OSError` внутри `set_current`: in-memory переменная откатывается на `previous_backend` (значение, запомненное в начале вызова), затем `OSError` пробрасывается наружу через `raise`. После исключения in-memory `_current_backend` снова равен старому значению, что согласовано с тем, что лежит на диске и что увидит будущий рестарт — «как будто `set_current` не вызывался».
- В нормальном режиме (`_loaded_from_disk == True`, диск пишется без ошибок) не выбрасывает.

### `load_state() -> None`

Читает состояние с диска и обновляет in-memory переменную. Вызывается ровно один раз при старте бота — из `bot.post_init` (как `silence_mode_registry.load_state()`). Синхронная, делает один атомарный I/O (чтение файла).

**Аргументы:** нет.

**Возвращает:** `None`.

**Исключения:** не выбрасывает — все ошибки чтения логируются и в `get_current()` возвращается дефолт. Это требование к функциям инициализации бота: они не должны падать при старте из-за нечитаемого state-файла, иначе перезапуск превращается в lockout. Полный разбор ситуаций — в разделе «Обработка ошибок».

## Внутренние функции

### `_save_state() -> None`

Атомарно записывает текущее значение `_current_backend` в файл `config.CURRENT_BACKEND_FILE`, используя паттерн «запись в `.tmp` → `os.replace()`». **Не проверяет** `_loaded_from_disk` — guard на «не затереть данные, которые мы не смогли прочитать» лежит на `set_current`. Так разделена ответственность: `_save_state` отвечает только за байты на диск, `set_current` — за политику «когда вообще можно писать». При прямом вызове `_save_state` (минуя `set_current`) защиты от затирания нет — приватная функция, новые вызывающие обязаны сами выполнить guard.

**Аргументы:** нет.

**Возвращает:** `None`.

**Исключения:** прокидывает наружу `OSError` от `write_text` или `os.replace` (это аномалия уровня файловой системы — диск переполнен, права отозваны, raid-disconnect; пользователь должен увидеть это как ошибку команды `/agent` в Telegram). Уровень обработки — `set_current` (откат in-memory) и далее `bot.py` (сообщение пользователю).

## Алгоритм работы

### get_current

1. Вернуть значение module-level переменной `_current_backend`. Никаких побочных эффектов, никакого I/O.

### set_current

Алгоритм построен по принципу «временное присвоение + откат при сбое», что даёт атомарный контракт «диск + память либо вместе, либо никак» с точки зрения внешнего наблюдателя:

1. **Guard «загружено ли с диска».** Если `_loaded_from_disk == False` — выбросить `RuntimeError` с сообщением, зафиксированным в разделе «Публичный API → set_current → Исключения». Никаких изменений в памяти, никаких записей на диск. Память остаётся на дефолте `BackendName.CLAUDE` (или на том, что было до неудачной загрузки), пользователь получает понятный отказ. Это сознательный отказ от прежнего «soft-fail» поведения, при котором in-memory обновлялся, а диск оставался нетронутым.
2. **Запомнить старое значение.** `previous_backend = _current_backend`. Используется для отката, если запись на диск провалится.
3. **Временно обновить in-memory.** `_current_backend = name`. Это присваивание делается ради того, чтобы `_save_state` (который читает `_current_backend.value` для формирования JSON) увидел новое значение. Окно «память обновлена, диск ещё не записан» существует ровно на время одного синхронного вызова `_save_state` и снаружи модуля **не наблюдаемо**: `set_current` не отдаёт управление вызывающей стороне между присваиванием и попыткой записи. Альтернатива (передавать `name` в `_save_state` параметром) равноценна по результату, но усложняет сигнатуру приватной функции; выбран более простой путь.
4. **Попытаться записать на диск.** Вызвать `_save_state()`:
   - Успех (запись прошла, `os.replace` отработал) — `_current_backend` уже равен `name`, состояние памяти и диска согласовано. Ничего больше не делаем, возвращаем `None`. На этой точке в логи пишется `info`-сообщение «Текущий бэкенд переключён: %s → %s».
   - `OSError` (запись провалилась — диск переполнен, права отозваны, raid disconnect, ошибка `os.replace` после успешного `write_text`) — выполнить откат: `_current_backend = previous_backend`. Затем пробросить исключение наружу через `raise`. С точки зрения вызывающей стороны после этого сценария состояние памяти и диска согласованы (оба содержат старый бэкенд) — «как будто `set_current` не вызывался».

Откат покрывает только `OSError`. Любое другое исключение из `_save_state` (например, `AttributeError` от попытки прочитать `.value` у строки, если вызывающая сторона нарушила контракт типа) пробрасывается без отката — это сознательное решение (см. раздел «Обработка ошибок → Дополнительные решения»).

### load_state

1. Получить путь к файлу: `file_path = config.CURRENT_BACKEND_FILE`.
2. Попытаться прочитать содержимое: `content = file_path.read_text("utf-8")`.
3. Распарсить как JSON: `data = json.loads(content)`.
4. Извлечь имя бэкенда: `raw_name = data["backend"]` (ожидается строка `"claude"` или `"codex"`).
5. Сконвертировать в Enum: `backend = BackendName(raw_name)`. Конструктор `BackendName(...)` бросает `ValueError`, если строка не соответствует ни одному значению — обрабатывается на следующем шаге.
6. Присвоить `_current_backend = backend`, установить `_loaded_from_disk = True`. Записать `info`-лог: «Текущий бэкенд загружен с диска: %s».
7. Обработка ветвлений (см. также раздел «Обработка ошибок»):
   - `FileNotFoundError` (файла нет, штатно при первом запуске) → дефолт `BackendName.CLAUDE`, `_loaded_from_disk = True` (запись будущим `set_current` разрешена; ничего не теряем — файла никогда не было).
   - `json.JSONDecodeError` (битый JSON), `KeyError` (нет ключа `"backend"`), `ValueError` (значение не из enum, например `"gemini"`) → дефолт `BackendName.CLAUDE`, `_loaded_from_disk = True` (запись разрешена; файл уже сломан, перезапись только улучшит). Лог уровня `warning` с описанием причины.
   - `Exception` (любая другая, в том числе `PermissionError`, `OSError` от транзиентного `EDEADLK` на macOS) → дефолт `BackendName.CLAUDE`, `_loaded_from_disk = False` (запись заблокирована; данные на диске могут быть корректны, не затираем). Лог уровня `error`.

### _save_state

1. Получить путь: `file_path = config.CURRENT_BACKEND_FILE`.
2. Сформировать путь временного файла: `temp_path = Path(str(file_path) + ".tmp")`.
3. Сформировать JSON-содержимое: `json_content = json.dumps({"backend": _current_backend.value})`. Важно: используется `.value`, а не `repr` или `.name` — `BackendName.CLAUDE.value == "claude"`, что и нужно (см. контракт с самим собой при последующем чтении через `BackendName(raw_name)`).
4. Атомарная запись: `temp_path.write_text(json_content, "utf-8")`, затем `os.replace(str(temp_path), str(file_path))`. `os.replace` атомарен на macOS APFS (POSIX rename), что гарантирует: сторонний процесс, читающий файл одновременно, увидит либо старое, либо новое содержимое целиком — без промежуточного состояния.

Проверка `_loaded_from_disk` сюда **не входит** — она перенесена в `set_current` и превращена в `RuntimeError` ДО любых изменений памяти. `_save_state` всегда пытается писать; если у вызывающей стороны нет права писать (флаг `False`), это её ответственность не вызывать `_save_state`.

## Зависимости

**От модулей проекта:**

- **`coding_agent_backend`** — `BackendName` (Enum) — единственный импорт, нужен для типа значения `_current_backend` и для конверсии строки из файла в Enum. Если спецификация `coding_agent_backend_spec.md` уже зафиксирована (а она зафиксирована, см. EXTRA-1 в `pipeline-state.json`) — интерфейс стабилен.
- **`config`** — константа `CURRENT_BACKEND_FILE: Path`. **На момент написания спеки этой константы в `config.py` ещё нет** — она добавляется при реализации этого модуля. Расположение: рядом с `SILENCE_MODE_FILE` (см. `config.py:33`), значение: `Path.home() / ".claude-manager-current-backend"`. Имя константы и значение зафиксированы в этой спеке, реализация — задача `implement-module current_backend_registry`.

**Стандартная библиотека:**
- `json` — для `json.dumps` и `json.loads`
- `logging` — для логирования через `logger = logging.getLogger(__name__)`
- `os` — для `os.replace` (атомарное переименование)
- `pathlib.Path` — для формирования пути временного файла

**Никаких сторонних пакетов** — модуль чисто-stdlib. Это упрощает тестирование и избавляет от зависимостей.

## Обработка ошибок

Каждый пункт ниже описывает: **ситуация → реакция модуля → состояние флага `_loaded_from_disk` после → уровень лога**. Категории сгруппированы по последствиям для записи (разрешена / заблокирована / прокидывается наружу).

**Категория «Запись разрешена» (`_loaded_from_disk = True`)**

- **Файл не существует (первый запуск)** — модуль ставит дефолт `BackendName.CLAUDE`, разрешает запись. Это штатная ситуация при первом запуске бота после внедрения двух-бэкендной архитектуры. Лог `info`: «Файл не найден, используется дефолт».
- **Файл содержит битый JSON** (`json.JSONDecodeError`) — дефолт `BackendName.CLAUDE`, запись разрешена (файл уже сломан, перезапись только улучшит). Лог `warning`: «Повреждённый JSON, используем дефолт: <error>».
- **В JSON нет ключа `"backend"`** (`KeyError`) — дефолт `BackendName.CLAUDE`, запись разрешена. Лог `warning`: «В файле нет ключа 'backend', используем дефолт».
- **Значение `"backend"` не из enum, например `"gemini"`** (`ValueError` от `BackendName(raw_name)`) — дефолт `BackendName.CLAUDE`, запись разрешена. Лог `warning`: «Неизвестный бэкенд '%s' в файле, используем дефолт: claude».

**Категория «Загрузка не удалась → последующий `set_current` отклоняется» (`_loaded_from_disk = False`)**

Эти ситуации возникают на этапе `load_state` (старт бота, чтение файла с диска). `load_state` сам исключений не выбрасывает — фиксирует проблему в логе и оставляет `_loaded_from_disk = False`, чтобы любая последующая попытка `set_current` была отклонена `RuntimeError` (см. категорию «Hard-fail без побочных эффектов» ниже). Защита от затирания: данные на диске могут быть валидными, мы их просто не смогли прочитать — нельзя поверх них писать вслепую.

- **`PermissionError` при чтении** — дефолт `BackendName.CLAUDE`, флаг `_loaded_from_disk = False`. Лог `error`: «Нет прав на чтение, set_current будет отклонён до рестарта: <error>».
- **Транзиентный `OSError` (включая `EDEADLK` errno 11 на macOS) при чтении** — дефолт `BackendName.CLAUDE`, флаг `_loaded_from_disk = False`. Лог `error`: «Ошибка ОС при чтении, set_current будет отклонён до рестарта: <error>». Эта ошибка задокументирована в CLAUDE.md → «Транзиентная ошибка EDEADLK (errno 11) на macOS» как известная для всех модулей, читающих state-файлы при переключении проектов; здесь применимость ниже (модуль читает файл только один раз при старте), но оборона добавлена для согласованности.

**Категория «Hard-fail с откатом, исключение наружу»**

- **`OSError` при записи (диск переполнен, права отозваны, raid disconnect, сбой `os.replace` после успешного `write_text`)** — `OSError` пробрасывается из `set_current` наружу. Перед `raise` `set_current` выполняет атомарный rollback: восстанавливает `_current_backend = previous_backend` (значение, запомненное в начале вызова на шаге 2 алгоритма). После исключения in-memory согласован с диском (старый бэкенд везде). Состояние флага `_loaded_from_disk` не меняется. Уровень лога — на стороне вызывающей стороны (`bot.py` обработчик команды `/agent`), который должен сообщить пользователю в Telegram «не удалось переключить бэкенд: <причина>».

**Категория «Hard-fail без побочных эффектов»**

- **`set_current()` при `_loaded_from_disk == False`** — `set_current` выбрасывает `RuntimeError` ДО того, как тронет память или диск. In-memory переменная остаётся на дефолте (`BackendName.CLAUDE`) или на том значении, что было до неудачной загрузки; файл на диске не трогается; состояние флага не меняется; `_save_state` не вызывается. Текст исключения зафиксирован в разделе «Публичный API → set_current → Исключения». Цель — `bot.py` ловит `RuntimeError` и сообщает пользователю в Telegram точную причину отказа («не загружено с диска, переключение невозможно до рестарта бота»), а не врёт «переключилось только в памяти». Это сознательный отказ от прежнего soft-fail поведения: бот, который рапортует «переключилось» и при этом теряет переключение после рестарта — мина для пользователя.

**Дополнительные решения**:

- Модуль **не реализует retry для `OSError` при чтении** (одна попытка → дефолт + блокировка). Причина: для маленького файла на ~50 байт повторная гонка с APFS-блокировкой даст тот же результат за миллисекунды; рестарт бота решит проблему естественно. Усложнение по образцу `daily_session_registry` (где есть retry) оправдано размером файла (сотни сессий) и частотой обращений (каждое сообщение) — для текущего модуля это перебор.
- Модуль **не делает рантайм-проверку `isinstance(name, BackendName)` в `set_current`**. Тип-подсказка `BackendName` и контракт документации считаются достаточными. Если вызывающая сторона передаст строку — `_save_state` упадёт на `_current_backend.value` с `AttributeError` (у строки нет атрибута `value`). Это явно укажет на ошибку контракта.
- **Откат in-memory ловит только `OSError`.** В `set_current` блок `try/except` имеет узкий тип — `except OSError`. Все остальные исключения из `_save_state` (например, `AttributeError` от попытки `<str>.value` при нарушении контракта типа, `RecursionError` или иное непредвиденное) пробрасываются БЕЗ отката, и `_current_backend` остаётся в промежуточном состоянии. Это сознательный выбор: расширение except до `Exception` маскировало бы программные баги контракта (вызывающая сторона передала не тот тип — должна получить громкое исключение, а не молчаливый rollback). В нормальной работе `_save_state` бросает только `OSError`; любое другое исключение указывает на ошибку контракта вызывающей стороны и должно стать видимым.

## Контракты с внешними системами

Модуль **не работает** с внешним инструментом (Claude CLI, Codex CLI, Telegram API). Все его контракты — внутренние, с другими модулями проекта. Единственный внешний контракт — с файловой системой macOS:

### Файловая система — атомарное переименование

**Источник правды:** POSIX `rename(2)`, реализация в Python — `os.replace()` (`Lib/os.py`). На macOS APFS `os.replace` гарантирует атомарность переименования: либо старый файл целиком, либо новый файл целиком. Сторонний читатель НЕ увидит промежуточного состояния (полу-записанного JSON).

**Алгоритм** (применяется во всех state-модулях проекта — `silence_mode_registry`, `daily_session_registry`, `session_manager`):
1. Записать содержимое в временный файл `<file>.tmp`.
2. Атомарно переименовать `<file>.tmp` → `<file>` через `os.replace()`.

**Тест-план для проверки контракта:** не требуется — это широко зафиксированное поведение POSIX, повторная эмпирическая проверка избыточна. Однако в тест-план юнит-тестов входит проверка наличия `tmp`-файла промежуточно (через моки `Path.write_text` и `os.replace`), чтобы исключить регрессию (например, кто-то заменил атомарную запись на прямое `file_path.write_text(...)`).

### Файл `~/.claude-manager-current-backend` — формат и контракт

**Источник правды:** эта спецификация (модуль читает и пишет файл сам — он же диктует формат).

**Формат:** JSON-объект с одним обязательным полем:

```json
{"backend": "claude"}
```

или

```json
{"backend": "codex"}
```

Дополнительные ключи (если кто-то вручную добавит) **игнорируются** при чтении. Расширение формата (например, добавление поля `last_changed_at`) — задача отдельной миграции; сейчас формат минимальный.

**Кодировка:** UTF-8 без BOM. (В Python `json.dumps` без `ensure_ascii=False` даст ASCII-only, что меньше — но допустимо.)

**Расположение:** `~/.claude-manager-current-backend` (хоум-директория пользователя). Не зависит от рабочей директории бота, не зависит от выбранного проекта (`/projects`) — глобальный для всего бота. Это согласуется с решением «гранулярность переключения LLM — глобальная» (концепция 06-38).

## Константы

- `DEFAULT_BACKEND: BackendName = BackendName.CLAUDE` — дефолтное значение, используется при первом запуске бота (когда файла ещё нет) и при любой ошибке чтения. Выбор значения: Claude — текущий бэкенд бота, миграция на новую двух-бэкендную архитектуру должна быть бесшовной для существующих пользователей. Если бы дефолт был Codex, после обновления бот неожиданно бы начал использовать другой CLI.
- `_current_backend: BackendName = DEFAULT_BACKEND` — module-level state, текущее значение. Меняется только в `load_state()` и `set_current()`. Тип жёстко `BackendName`, не `str`.
- `_loaded_from_disk: bool = False` — флаг защиты от затирания. `True` после успешной загрузки с диска (включая «файла нет» и «файл повреждён, но мы готовы перезаписать»). `False` пока загрузка не выполнена или упала с непредвиденной ошибкой. Контролирует поведение `set_current`: при `False` любая попытка переключения отклоняется с `RuntimeError`, никаких записей на диск и никаких изменений в памяти не происходит. На `_save_state` напрямую флаг не влияет — `_save_state` всегда пишет; ответственность за guard лежит на `set_current`.

В `config.py` дополнительно появляется (при реализации этого модуля):

- `CURRENT_BACKEND_FILE: Path = Path.home() / ".claude-manager-current-backend"` — путь к файлу персистентности. Размещается рядом с `SILENCE_MODE_FILE` (см. `config.py:33`). Имя файла — без расширения, в стиле dotfile. Согласуется с другими файлами бота: `.claude-manager.lock`, `.claude-manager-silence-mode`, `.claude-manager-current-project`.

## Тест-план

Все тесты — синхронные (модуль синхронный), не требуют `asyncio_mode = "auto"`. Тестовый файл: `tests/test_current_backend_registry.py`. Фикстуры — `tmp_path` для изоляции файлов, `monkeypatch` для подмены `config.CURRENT_BACKEND_FILE` и module-level state переменных.

### Юнит-тесты

- **test_get_current_returns_default_before_load** — что возвращает get_current до вызова load_state.
  - Вход: модуль импортирован, `load_state()` ещё не вызван (или сброшен через monkeypatch).
  - Ожидаемый результат: `get_current() == BackendName.CLAUDE` (дефолт).
  - Тип: unit.

- **test_default_backend_is_claude** — проверка значения константы.
  - Вход: импорт `DEFAULT_BACKEND`.
  - Ожидаемый результат: `DEFAULT_BACKEND is BackendName.CLAUDE`.
  - Тип: unit.

- **test_set_current_updates_in_memory** — переключение обновляет переменную модуля.
  - Вход: `load_state()` вызван при отсутствующем файле (чтобы `_loaded_from_disk = True`), затем `set_current(BackendName.CODEX)`.
  - Ожидаемый результат: `get_current() == BackendName.CODEX`.
  - Тип: unit.

- **test_set_current_persists_to_disk** — переключение пишет в файл.
  - Вход: `load_state()` без файла, `set_current(BackendName.CODEX)`, прочитать `tmp_path / ".claude-manager-current-backend"`.
  - Ожидаемый результат: файл существует, содержимое — валидный JSON `{"backend": "codex"}`.
  - Тип: unit.

- **test_set_current_writes_value_not_repr** — проверка использования `.value`, а не `repr`.
  - Вход: `load_state()` без файла (чтобы поднять `_loaded_from_disk = True`), затем `set_current(BackendName.CLAUDE)`, прочитать сырой файл.
  - Ожидаемый результат: содержимое — `{"backend": "claude"}` (не `{"backend": "BackendName.CLAUDE"}` и не `{"backend": "<BackendName.CLAUDE: 'claude'>"}`).
  - Тип: unit.

- **test_set_current_uses_atomic_rename** — проверка паттерна tmp + rename.
  - Вход: `load_state()` без файла (чтобы поднять `_loaded_from_disk = True`), затем замокить `Path.write_text` и `os.replace`, вызвать `set_current(BackendName.CODEX)`.
  - Ожидаемый результат: `Path.write_text` вызван на `<file>.tmp`, затем `os.replace(<tmp>, <file>)`. Прямой записи в `<file>` нет.
  - Тип: unit.

- **test_load_state_reads_from_existing_file** — корректное чтение валидного файла.
  - Вход: записать `{"backend": "codex"}` в `tmp_path / ".claude-manager-current-backend"`, вызвать `load_state()`.
  - Ожидаемый результат: `get_current() == BackendName.CODEX`, `_loaded_from_disk == True`.
  - Тип: unit.

- **test_load_state_missing_file_uses_default_and_unblocks_writes** — отсутствующий файл (первый запуск).
  - Вход: `tmp_path` пуст, вызвать `load_state()`.
  - Ожидаемый результат: `get_current() == BackendName.CLAUDE`, `_loaded_from_disk == True` (запись разрешена). Лог `info`.
  - Тип: unit.

### Граничные случаи

- **test_load_state_corrupted_json_uses_default_and_unblocks_writes** — битый JSON в файле.
  - Вход: записать `not a json {{{` в файл, вызвать `load_state()`.
  - Ожидаемый результат: `get_current() == BackendName.CLAUDE`, `_loaded_from_disk == True`, в логах `warning` с причиной (`json.JSONDecodeError`).
  - Тип: edge case.

- **test_load_state_missing_backend_key_uses_default_and_unblocks_writes** — JSON без ключа `"backend"`.
  - Вход: `{"foo": "bar"}` в файле, вызвать `load_state()`.
  - Ожидаемый результат: `get_current() == BackendName.CLAUDE`, `_loaded_from_disk == True`, в логах `warning`.
  - Тип: edge case.

- **test_load_state_unknown_backend_value_uses_default_and_unblocks_writes** — неизвестное имя бэкенда.
  - Вход: `{"backend": "gemini"}` в файле (значение не из enum), вызвать `load_state()`.
  - Ожидаемый результат: `get_current() == BackendName.CLAUDE`, `_loaded_from_disk == True`, в логах `warning` с упоминанием значения "gemini" и списка валидных.
  - Тип: edge case.

- **test_load_state_empty_file_uses_default_and_unblocks_writes** — пустой файл.
  - Вход: создать файл нулевого размера, вызвать `load_state()`.
  - Ожидаемый результат: `get_current() == BackendName.CLAUDE`, `_loaded_from_disk == True` (пустая строка не парсится как JSON → `JSONDecodeError`, путь обрабатывается как «битый файл»).
  - Тип: edge case.

- **test_set_current_raises_runtime_error_when_load_failed** — set_current при `_loaded_from_disk == False` бросает `RuntimeError` без побочных эффектов.
  - Вход: подменить `Path.read_text` на функцию, бросающую `PermissionError`, вызвать `load_state()` (получим `_loaded_from_disk = False`, `_current_backend = BackendName.CLAUDE`), затем `set_current(BackendName.CODEX)`.
  - Ожидаемый результат: `set_current` бросает `RuntimeError`, текст исключения содержит фразу «Текущий бэкенд не загружен с диска». In-memory переменная **не изменилась**: `get_current() == BackendName.CLAUDE` (остался дефолт, не Codex). Файл на диске НЕ создан (`tmp_path / ".claude-manager-current-backend"` не существует). Лог `error` от `load_state` есть; от `_save_state` логов нет (он не вызывался).
  - Тип: edge case.

- **test_load_state_does_not_overwrite_in_memory_on_double_call** — повторный вызов `load_state()` после успешной загрузки и `set_current` не теряет переключение.
  - Вход: `load_state()` (читает `claude` с диска), `set_current(BackendName.CODEX)` (записывает `codex` на диск), затем повторный `load_state()`.
  - Ожидаемый результат: `get_current() == BackendName.CODEX` (потому что `set_current` записал `codex` на диск, и второй `load_state` его и прочитал).
  - Тип: edge case.

### Тесты ошибок

- **test_load_state_permission_error_makes_set_current_raise_runtime** — `PermissionError` при чтении приводит к `_loaded_from_disk == False`, и любой последующий `set_current` отклоняется без побочных эффектов.
  - Вход: подменить `Path.read_text` на функцию, бросающую `PermissionError("denied")`, вызвать `load_state()`. Затем вызвать `set_current(BackendName.CODEX)`.
  - Ожидаемый результат: после `load_state` — `_loaded_from_disk == False`, `get_current() == BackendName.CLAUDE`, в логах `error` от `load_state` с упоминанием «denied». `set_current(BackendName.CODEX)` бросает `RuntimeError`. In-memory `get_current()` остаётся `BackendName.CLAUDE`. Файл на диске не создан.
  - Тип: error.

- **test_load_state_oserror_makes_set_current_raise_runtime** — транзиентный `OSError` (имитация EDEADLK) при чтении приводит к тому же поведению, что и `PermissionError`.
  - Вход: подменить `Path.read_text` на функцию, бросающую `OSError(11, "Resource deadlock avoided")`, вызвать `load_state()`. Затем вызвать `set_current(BackendName.CODEX)`.
  - Ожидаемый результат: `_loaded_from_disk == False`, `get_current() == BackendName.CLAUDE` после `load_state`, в логах `error` с упоминанием «Resource deadlock avoided». `set_current(BackendName.CODEX)` бросает `RuntimeError`. In-memory остаётся `BackendName.CLAUDE`. Файл на диске не создан.
  - Тип: error.

- **test_set_current_rolls_back_in_memory_when_disk_write_fails** — ошибка записи на диск приводит к атомарному откату in-memory переменной.
  - Вход: `load_state()` при отсутствующем файле (получаем `_loaded_from_disk = True`, `_current_backend = BackendName.CLAUDE`). Подменить `Path.write_text` на функцию, бросающую `OSError(28, "No space left on device")`. Вызвать `set_current(BackendName.CODEX)`.
  - Ожидаемый результат: `set_current` бросает `OSError` (чтобы `bot.py` мог сообщить пользователю «не удалось переключить бэкенд: нет места на диске»). In-memory переменная **откатывается**: `get_current() == BackendName.CLAUDE` (НЕ Codex — это главное отличие от старого «soft-fail» поведения). Файл на диске не изменён (либо отсутствует, либо содержит прежнее значение). В логах нет `info`-сообщения «Текущий бэкенд переключён» — переключение не состоялось.
  - Тип: error.

- **test_set_current_rolls_back_in_memory_when_os_replace_fails** — то же поведение для ошибки на стадии rename, не на write.
  - Вход: `load_state()` без файла, подменить `os.replace` на функцию, бросающую `OSError(13, "Permission denied")`, вызвать `set_current(BackendName.CODEX)`.
  - Ожидаемый результат: `set_current` бросает `OSError`. In-memory `get_current() == BackendName.CLAUDE` (откат). Целевой файл на диске не появился (хотя `.tmp`-файл мог остаться — это нормально, тест на чистоту `.tmp` не делает).
  - Тип: error.

- **test_set_current_does_not_rollback_on_non_oserror_from_save_state** — узкий тип `except OSError` сознательно не ловит другие исключения.
  - Вход: `load_state()` без файла (получаем `_loaded_from_disk = True`, `_current_backend = BackendName.CLAUDE`). Подменить `_save_state` на функцию, бросающую `ValueError("simulated programming bug")` (тип `ValueError` выбран намеренно — он не пересекается с собственным `RuntimeError` модуля, который выбрасывается guard'ом при `_loaded_from_disk == False`). Вызвать `set_current(BackendName.CODEX)`.
  - Ожидаемый результат: `set_current` пробрасывает `ValueError("simulated programming bug")` без отката in-memory. После исключения `get_current() == BackendName.CODEX` (значение, временно присвоенное на шаге 3 алгоритма; rollback на шаге 4 не сработал, потому что `except OSError` не ловит `ValueError`). Это закрепляет архитектурное решение из раздела «Обработка ошибок → Дополнительные решения»: rollback покрывает только `OSError`, программные баги (передача неправильного типа и т.п.) пробрасываются громко.
  - Тип: error.

### Резюме тест-плана

- Юнит-тесты: 8
- Граничные случаи: 6 (один переименован в `test_set_current_raises_runtime_error_when_load_failed`, поведение поменялось со «soft-fail» на «hard-fail без побочных эффектов»)
- Тесты ошибок: 5 (старые два по `load_state` переписаны под новый контракт; старый `test_save_state_propagates_oserror_on_write` переписан в `test_set_current_rolls_back_in_memory_when_disk_write_fails`; добавлены `test_set_current_rolls_back_in_memory_when_os_replace_fails` и `test_set_current_does_not_rollback_on_non_oserror_from_save_state`)
- Итого: **19 тест-кейсов**

Контрактных интеграционных тестов нет — модуль не работает с внешними CLI или сетевыми API; единственный «внешний» контракт (POSIX rename) проверяется через моки.
