# Анализ первопричины: API 400 про thinking-блоки в сессии #18

**Дата анализа:** 29-05-2026
**Проект:** Claude Manager — Telegram-бот, через который Ivan управляет Claude Code и Codex с телефона
**Статус:** Первопричина найдена

## Симптом

На скриншоте бот показывает сообщение:

`#18 Ошибка Claude, повтор 1/10: API Error: 400 messages.1.content.3: thinking or redacted_thinking blocks in the latest assistant message cannot be modified...`

Простыми словами: это не ошибка Telegram и не ошибка Codex. Это Claude API отказался продолжать старую Claude-сессию #18. Бот увидел отказ как обычный сбой и включил повтор.

Три термина, которые важны:

- **Сессия** — один разговор с CLI-агентом, сохранённый в JSONL-файле на диске.
- **`thinking`-блок** — внутренний блок рассуждений Claude. В API он подписан, и при продолжении сессии его нельзя менять ни на один байт.
- **`--resume`** — режим Claude CLI «продолжи уже существующую сессию из файла». Бот использует его для каждого следующего сообщения в Claude-сессии.

## Цепочка причин

1. **Ivan увидел ошибку `thinking blocks cannot be modified` в сессии #18** → потому что →
2. **Claude CLI попытался продолжить сессию #18 через `--resume`** и отправил в API историю, где последний assistant-turn содержит signed `thinking`-блоки → потому что →
3. **Эта сессия уже была повреждена длинным/оборванным ходом Claude**: в JSONL-файле есть несколько assistant-записей со `stop_reason=max_tokens` и content только `thinking`/tool-use, без нормального финального `result` → потому что →
4. **Бот запускает Claude с `--effort max`**, а это создаёт большие signed thinking-блоки. Когда ход упирается в `max_tokens` или завершается без финального события, следующий `--resume` может нарушить API-инвариант «последний thinking-блок должен остаться ровно как был» → потому что →
5. **Текущий классификатор постоянных ошибок не знает этот конкретный текст 400-ошибки.** Он уже распознаёт `Prompt is too long` и `hit your limit`, но не распознаёт `thinking or redacted_thinking blocks ... cannot be modified` → поэтому →
6. **КОРНЕВАЯ ПРИЧИНА:** ошибка `thinking blocks cannot be modified` уже известна как постоянная resume-ошибка Claude, но она не включена в `ClaudeCodeBackend.classify_permanent_error`. Из-за этого бот считает её временной и запускает обычный retry-цикл.

## Корневая причина

Корень в связке двух вещей.

Первая часть — состояние самой Claude-сессии #18. В файле `/home/ivan/.claude/projects/-home-ivan-claude-sandbox-claude-manager/95c88bbd-d7dc-4365-9c72-28f21e616cff.jsonl` видно:

- сессия #18 создана как Claude-сессия, не Codex;
- в ней есть assistant-записи со `stop_reason=max_tokens`;
- несколько записей состоят только из `thinking`-блока с подписью;
- после этого появляется synthetic assistant error: `API Error: 400 messages.1.content.3...`.

Вторая часть — поведение бота при такой ошибке. Файл `src/claude_manager/claude_code_backend.py` — это адаптер Claude CLI: он знает, как запускать Claude, читать его события и распознавать ошибки. Сейчас там есть:

- `CLAUDE_CONTEXT_OVERFLOW_ERROR_MARKERS = ("prompt is too long",)`;
- `CLAUDE_USAGE_LIMIT_ERROR_MARKERS = ("hit your limit",)`.

Но маркера для `thinking or redacted_thinking blocks in the latest assistant message cannot be modified` нет. Поэтому `process_manager.py` (модуль, который управляет CLI-процессами и повторами) получает ошибку как обычный `is_error=True` и запускает `_retry_loop`.

Важное уточнение: новая сессия #19 на Codex здесь не виновата. Логи показывают, что `/agent` переключил текущий backend на Codex в 13:54:10, `/new` создал #19 в 13:54:12, а ошибка на скриншоте пришла из старой активной Claude-сессии #18.

## Почему проблема не была предотвращена

Эта ошибка не новая для проекта. В отчёте `dev/docs/session-reports/29-05/03-36_judge-tuner-bot-session-crash-diagnosis.md` уже записано, что `API Error 400 ... thinking blocks cannot be modified` — постоянная resume-ошибка: повтор того же `--resume` снова загрузит ту же сломанную историю и снова упадёт.

Позже был сделан правильный архитектурный шаг: в `dev/docs/adr/29.05_12.29-session-change-documenter-backend-error-retryability-classification.md` описана классификация ошибок backend по повторяемости. Код действительно уже умеет останавливать retry для `Prompt is too long` и `hit your limit`.

Но конкретно этот marker из утреннего отчёта не попал в классификатор. Получился частичный фикс: механизм есть, но список известных постоянных Claude-ошибок неполный.

### Проверенные документы

- `CLAUDE.md` — подтвердил архитектуру: бот локально запускает Claude CLI через stream-json, использует `--resume`, хранит сессии в JSONL.
- `dev/docs/session-reports/29-05/03-36_judge-tuner-bot-session-crash-diagnosis.md` — уже фиксирует эту же 400-ошибку как постоянную.
- `dev/docs/logs/root-cause-reports/29-05_11-26_session-overflow-retry-spam.md` — показывает тот же класс проблемы: постоянная ошибка не должна уходить в 10 повторов.
- `dev/docs/adr/29.05_12.29-session-change-documenter-backend-error-retryability-classification.md` — описывает уже внедрённый механизм классификации, но без marker-а thinking-block invariant.
- `dev/docs/specs/realised/process_manager_spec.md` — уже говорит, что retry нужен только для временных ошибок.

### Проверенные скиллы

- `root-cause-analysis` — применён для этого разбора.
- Проектных `.claude/skills/*/SKILL.md` в `claude_manager` сейчас нет, поэтому проверять локальные проектные скиллы было нечего.

## Рекомендации по исправлению

1. **Добавить новый вид постоянной ошибки.**
   В `src/claude_manager/coding_agent_backend.py` добавить `PermanentErrorKind` для сломанного состояния сессии, например `SESSION_STATE_CORRUPTION` или `RESUME_STATE_CORRUPTION`.

2. **Добавить Claude marker в классификатор.**
   В `src/claude_manager/claude_code_backend.py` вынести строку в константу, например:
   `CLAUDE_SESSION_STATE_ERROR_MARKERS = ("thinking or redacted_thinking blocks in the latest assistant message cannot be modified",)`.

3. **Показать Ivan понятное сообщение вместо retry.**
   В `src/claude_manager/claude_interaction.py` добавить текст для нового `PermanentErrorKind`: «Claude-сессия повреждена при продолжении. Повтор не поможет. Начни новую через /new».

4. **Добавить тесты.**
   В `tests/test_claude_code_backend.py` проверить, что exact marker возвращает новый permanent kind. В `tests/test_process_manager.py` проверить, что такая ошибка не вызывает retry callback.

## Рекомендации по предотвращению

1. **Расширить список известных permanent Claude errors из всех инцидентов.**
   Перед изменением классификатора сделать grep по `dev/docs/session-reports`, `dev/docs/logs/root-cause-reports` и логам на `API Error: 400`, `Prompt is too long`, `hit your limit`. Это не переименование, а аудит потребителей знания: иначе снова попадёт только последний инцидент.

2. **Зафиксировать thinking-block invariant в справочнике протокола.**
   В `dev/docs/claude-cli-stream-json-protocol.md` добавить короткий раздел: signed `thinking`/`redacted_thinking` в последнем assistant message нельзя модифицировать; при такой 400-ошибке retry через тот же `--resume` бессмыслен.

3. **Подумать о снижении `--effort max` для bot-mode Claude.**
   Сейчас `src/claude_manager/claude_code_backend.py` всегда запускает Claude с `--effort max`. Это повышает шанс длинных signed thinking-блоков. Менять нужно аккуратно, потому что это повлияет на качество сложных задач, но риск теперь подтверждён уже несколькими инцидентами.

## Верификация выводов

- Проверил лог бота вокруг 13:47-13:58: #18 создана как Claude-сессия, позже #19 создана как Codex-сессия. Ошибка идёт из #18.
- Проверил JSONL сессии #18: ошибка записана в строке с synthetic assistant error после нескольких `thinking`/`max_tokens` записей.
- Проверил код классификации: marker для thinking-block 400 отсутствует.
- Проверил уже существующий ADR: механизм классификации permanent errors есть, значит рекомендация ложится в текущую архитектуру, а не требует нового механизма.

Рассмотренные альтернативы:

- **Codex #19 сломался.** Отвергнуто: #19 создан после `/new`, а ошибка на скриншоте явно подписана #18 Claude.
- **Telegram неправильно доставил сообщение.** Отвергнуто: Telegram только показывает текст, источник ошибки в Claude JSONL и логе process_manager.
- **Бот перепутал активную сессию.** Отвергнуто: логи показывают корректные привязки. Проблема в том, что старая #18 продолжала свой retry параллельно.

## Верификация решений

- **Рекомендация:** добавить новый `PermanentErrorKind` для resume/session-state corruption.
  - **Вердикт:** ОДОБРЕНО
  - **Обоснование:** Это точнее, чем притворяться, что ошибка является переполнением контекста. Поведение для пользователя похоже — начать новую сессию, но причина другая.
  - **Корректировка:** имя выбрать по существующему стилю enum в коде.

- **Рекомендация:** добавить marker в `ClaudeCodeBackend.classify_permanent_error`.
  - **Вердикт:** ОДОБРЕНО
  - **Обоснование:** Это ровно тот слой, где уже живут backend-specific тексты ошибок Claude.
  - **Корректировка:** не хардкодить строку внутри метода, а вынести в именованную константу.

- **Рекомендация:** пересмотреть `--effort max`.
  - **Вердикт:** ОДОБРЕНО С ЗАМЕЧАНИЯМИ
  - **Обоснование:** Риск подтверждён, но изменение влияет на качество ответов. Это отдельное решение, не часть быстрого фикса.
  - **Корректировка:** сначала закрыть классификацию permanent error, потом отдельно сравнить режимы.

## Чек-лист исправлений

### Изменения в документацию [DOC]

- [ ] Обновить `dev/docs/claude-cli-stream-json-protocol.md`: добавить known issue про `thinking/redacted_thinking blocks ... cannot be modified` и указать, что retry через тот же `--resume` бессмыслен.

### Изменения в коде [CODE]

- [ ] В `src/claude_manager/coding_agent_backend.py` добавить новый `PermanentErrorKind` для повреждённого resume/session state.
- [ ] В `src/claude_manager/claude_code_backend.py` добавить marker `thinking or redacted_thinking blocks in the latest assistant message cannot be modified`.
- [ ] В `src/claude_manager/claude_interaction.py` добавить понятное сообщение для Ivan: сессия Claude повреждена, повтор не поможет, нужна `/new`.
- [ ] В `tests/test_claude_code_backend.py` и `tests/test_process_manager.py` добавить проверки, что эта ошибка не запускает retry.

### Изменения в скиллах [SKILL]

- [ ] В будущих RCA/fix-пайплайнах перед расширением классификатора permanent errors делать grep по прошлым RCA/session reports, чтобы не брать только последний incident marker.
