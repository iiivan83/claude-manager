# Сессия 29-05: фиксация модели Claude Opus 4.8 в команде запуска CLI

## Резюме

Бот теперь явно передаёт в Claude Code CLI флаг `--model claude-opus-4-8` — точное имя версии модели Anthropic, выпущенной 28-05-2026. До изменения модель выбиралась дефолтом подписки и могла бы плавать при изменении политики Anthropic или релизе следующей модели. Изменения покрыты двумя новыми юнит-тестами и подтверждены контрактным тестом с реальным CLI.

## Изменённые файлы

- **`src/claude_manager/claude_code_backend.py`** — изменён — добавлена константа `CLAUDE_OPUS_MODEL_ID = "claude-opus-4-8"` (источник правды) и аргументы `"--model", CLAUDE_OPUS_MODEL_ID` в метод `compose_subprocess_command_args`. Это основной путь, по которому бот общается с Claude через backend-адаптер.
- **`src/claude_manager/claude_runner.py`** — изменён — импорт `from claude_manager.claude_code_backend import CLAUDE_OPUS_MODEL_ID` и добавление `"--model", CLAUDE_OPUS_MODEL_ID` в `_build_command_args`. Это legacy-путь через `start_process`, который активно используется из `process_manager.py` в трёх местах (строки 694, 776, 966) — оставлять его без модели нельзя.
- **`tests/test_claude_code_backend.py`** — изменён — добавлен тест `test_compose_args_pins_opus_4_8_model`, проверяющий присутствие флага `--model` и точное значение `claude-opus-4-8` (а не алиас).
- **`tests/test_claude_runner.py`** — изменён — добавлен тест `test_build_command_args_pins_opus_4_8_model` с тем же контрактом для legacy-пути.

## Решения

- **Решение:** фиксировать точное имя версии модели `claude-opus-4-8`, а не плавающий алиас `opus`. **Причина:** алиас `opus` сегодня резолвится в 4.8, но при выпуске Opus 4.9 автоматически переедет на неё; точная версия делает поведение бота воспроизводимым между релизами Anthropic.

- **Решение:** имя модели вынести в одну именованную константу `CLAUDE_OPUS_MODEL_ID` в `claude_code_backend.py`; `claude_runner.py` импортирует её. **Причина:** два модуля строят CLI-команды для Claude (это уже существующий технический долг), и дублировать литерал в двух местах — нарушение правила чистого кода. Единая константа даёт одну точку обновления при будущей смене версии.

- **Решение:** не настраивать fast mode (`/fast`) в этой сессии. **Причина:** Anthropic выкатил `/fast` пока только как slash-команду интерактивного TUI; для headless-режима (`-p --output-format stream-json`), на котором работает бот, официального CLI-флага нет. Пользователь явно попросил не трогать.

- **Решение:** не переопределять модель субагентов через `CLAUDE_CODE_SUBAGENT_MODEL`. **Причина:** в проекте ни у одного агента не задано явное `model:` (проверено grep'ом по всем frontmatter в `~/.claude`), поэтому все кастомные субагенты пойдут на `claude-opus-4-8` через наследование. Built-in утилитарные субагенты Claude Code (Explore, statusline-setup, claude-code-guide) имеют захардкоженную модель внутри CLI — это известный баг Anthropic `anthropics/claude-code#31490`, обходного пути нет.

- **Решение:** не менять `CLAUDE.md` проекта в этой сессии. **Причина:** скилл документирует изменения сессии, а не делает новые. Если правило про фиксацию модели нужно зафиксировать как принцип эксплуатации в `CLAUDE.md` — это отдельное решение пользователя, которое будет задокументировано отдельным CLAUDE.md Update Log.

- **Решение:** не создавать `architecture.md`. **Причина:** в этом проекте архитектурные принципы исторически живут в `CLAUDE.md` (раздел «Архитектурные принципы» и «Принципы эксплуатации»). В `dev/docs/docs-index.md` живыми документами явно перечислены `docs-index.md`, `CLAUDE.md`, `brd-user-journeys.md` — `architecture.md` отсутствует. Создавать его сейчас означало бы дублировать функцию `CLAUDE.md`.

## Контекст для следующей сессии

- **Рестарт бота не выполнен.** Изменения подтянутся только после `./restart-claude-manager.sh` из терминала или команды `/restart` в Telegram. Внутри сессии Claude рестарт не запускался — правило проекта запрещает самоперезапуск из собственного дерева процессов (инцидент 22-04-2026, exit 137).

- **Файлы за порогом размера.** Это существующее состояние до правок, рефакторить отдельной задачей.
  - `src/claude_manager/claude_runner.py` — 359 строк после правки (порог warning 300, error 500 — в пределах warning).
  - `tests/test_claude_runner.py` — 595 строк после правки (превышение error-порога 500). Этот файл был 579 строк до правки.
  - `src/claude_manager/claude_code_backend.py` — 295 строк (близко к порогу warning, но пока в пределах).
  - `tests/test_claude_code_backend.py` — 510 строк после правки (превышение error-порога 500). Файл был 484 до правки.

- **Если выйдет Opus 4.9** — правка одна: поменять значение константы `CLAUDE_OPUS_MODEL_ID` в `src/claude_manager/claude_code_backend.py:46`. После правки запустить контрактный тест `tests/integration/test_claude_cli_contract.py` — он проверит, что новая модель доступна подписке и CLI её принимает.

- **Если возникнет потребность экономить на субагентах** — установить `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` в `.env` или systemd unit бота. Это пустит всех кастомных субагентов на Sonnet 4.6, оставив основной бот на Opus 4.8. Поведение поля `inherit` в frontmatter агентов перебивается этой переменной окружения.

## Результаты тестирования

- **Целевые модули** — `python -m pytest tests/test_claude_runner.py tests/test_claude_code_backend.py -v` — **55 passed** за 0.22 сек, в том числе оба новых теста.

- **Юнит + integration** — `python -m pytest tests/ --ignore=tests/e2e --ignore=tests/integration/test_claude_cli_contract.py --ignore=tests/integration/test_codex_cli_contract.py -q` — **1016 passed** за 26.83 сек. Косвенно затронутые модули (`process_manager` мокает `start_process`, `session_watcher` использует тестовый FakeBackend) не сломались.

- **Контрактный тест с реальным CLI** — `python -m pytest tests/integration/test_claude_cli_contract.py -v` — **2 passed** за 10.92 сек. Это эмпирическое подтверждение: подписка пускает на модель `claude-opus-4-8`, CLI принимает флаг `--model claude-opus-4-8`, протокол `stream-json` (потоковый JSON через stdin/stdout, через который бот общается с CLI) реально работает с новой моделью.

## Выполненные команды

- `wc -l src/claude_manager/claude_runner.py src/claude_manager/claude_code_backend.py tests/test_claude_runner.py tests/test_claude_code_backend.py` — проверка размеров файлов перед правкой и после.
- `find /home/ivan/.claude/agents -name "*.md"` — проверка глобальных custom agents (пусто).
- `grep -r "^model:" ~/.claude` — проверка явного указания модели в frontmatter скиллов и агентов (ничего не найдено).
- `env | grep -E "ANTHROPIC_|CLAUDE_CODE_"` — проверка ENV-переменных переопределения модели (только служебные `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_ENTRYPOINT`, `CLAUDE_CODE_EXECPATH`).
- Три прогона pytest — целевые модули, полный набор юнит+integration, контрактный тест с реальным CLI.

## Проблемы и решения

- **Проблема:** жёсткий тест `test_compose_args_for_resume_session_appends_resume` требует, чтобы `--resume <id>` были ПОСЛЕДНИМИ двумя аргументами командной строки. **Решение:** флаг `--model claude-opus-4-8` добавлен ДО `--resume`, между блоком общих аргументов и резюмом. Тест прошёл без правки.
