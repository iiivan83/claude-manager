# Сессия 10-04: синхронизация 13 скиллов с эталонными шаблонами

## Резюме

Заменены 13 проектных скиллов в `.claude/skills/` на эталонные версии из `/Users/ivan/.claude/skill-templates/`. Цель — подтянуть обновления шаблонов (новые агенты, правки в SKILL.md, фикс валидатора) и одновременно вычистить макос-дубликаты `SKILL 2.md`, висевшие в untracked. Цель достигнута, все скиллы побайтово идентичны шаблонам и прошли structural-validator.

## Изменённые файлы

### Бэкап и структура

| Файл / папка | Действие | Что сделано |
|------|----------|-------------|
| `.claude/skills/.backup-2026-04-10/` | создана | Бэкап 13 скиллов перед заменой (рядом с существующей `.backup-2026-04-09/`, её не трогали) |
| `.claude/skills/pipeline-implementer/SKILL 2.md` | удалён | Макос-дубликат, висел как `??` в git status |
| `.claude/skills/pipeline-implementer/agents 2/` | удалена | Макос-дубликат |
| `.claude/skills/pipeline-implementer/references 2/` | удалена | Макос-дубликат |
| `.claude/skills/script-creator-pipeline/SKILL 2.md` | удалён | Макос-дубликат |
| `.claude/skills/script-creator-pipeline/agents 2/` | удалена | Макос-дубликат |

### Обновлённые SKILL.md и файлы (modified в git)

| Файл | Действие | Что сделано |
|------|----------|-------------|
| `.claude/skills/apply-root-cause-fixes/SKILL.md` | изменён | Из шаблона |
| `.claude/skills/fast-skill-updater/SKILL.md` | изменён | Из шаблона |
| `.claude/skills/feature-pipeline/SKILL.md` | изменён | Из шаблона |
| `.claude/skills/feature-pipeline/agents/feature-finalizer.md` | изменён | Из шаблона |
| `.claude/skills/pipeline-designer/SKILL.md` | изменён | Из шаблона |
| `.claude/skills/pipeline-implementer/SKILL.md` | изменён | Из шаблона |
| `.claude/skills/pipeline-implementer/scripts/structural-validator.sh` | изменён | Из шаблона (тот же валидатор, которым проверялись обновлённые скиллы) |
| `.claude/skills/script-creator-pipeline/SKILL.md` | изменён | Из шаблона |
| `.claude/skills/script-creator-pipeline/agents/coder.md` | изменён | Из шаблона |
| `.claude/skills/script-creator-pipeline/agents/fixer-code.md` | изменён | Из шаблона |
| `.claude/skills/skill-creator/SKILL.md` | изменён | Из шаблона |
| `.claude/skills/universal-bug-fixer/agents/executor.md` | изменён | Из шаблона |
| `.claude/skills/update-skill/SKILL.md` | изменён | Из шаблона |
| `.claude/skills/update-skill/references/schemas.md` | изменён | Из шаблона |

### Новые файлы (untracked в git)

| Файл | Действие | Что сделано |
|------|----------|-------------|
| `.claude/skills/pipeline-explorer/agents/requirements-collector.md` | создан | Новый агент, которого не было в проектной версии скилла |
| `.claude/skills/pipeline-explorer/agents/stage-test-creator.md` | создан | Новый агент, которого не было в проектной версии скилла |
| `.claude/skills/update-skill/agents/change-applier.md` | создан | Новый агент — отдельная стадия применения изменений после `change-planner`; 16174 байт |

### Не тронуты

Из 37 скиллов проекта 20 оставлены без изменений — это проектные скиллы, у которых нет соответствия в шаблонах: `autofix-e2e`, `brd-decompose`, `create-phase-gate-checker`, `create-test-runner`, `create-user-test-scenarios`, `dev-script-implement`, `implement-module`, `make-script-spec`, `pipeline-run`, `project-setup`, `rename-entity`, `review-code`, `run-user-testing`, `session-report`, `spec-module`, `test-e2e`, `test-integration`, `update-project-docs`, `update-session-report`, `validate-brd`.

## Выполненные команды

- `mkdir -p .backup-2026-04-10 && for skill in …; do cp -R "$skill" ".backup-2026-04-10/$skill"; done` — бэкап 13 скиллов перед заменой
- `for skill in …; do rm -rf "$skill"; done` — удаление 13 старых папок (с автоудалением макос-дубликатов внутри)
- `for skill in …; do cp -R "/Users/ivan/.claude/skill-templates/$skill" "./$skill"; done` — копирование 13 скиллов из шаблонов
- `for skill in …; do diff -rq "/Users/ivan/.claude/skill-templates/$skill" ".claude/skills/$skill"; done` — верификация побайтовой идентичности (результат: `✓ IDENTICAL`)
- `bash pipeline-implementer/scripts/structural-validator.sh <skill>` в цикле по 13 скиллам — все вернули `status: PASS` и exit code 0
- `git status --short | grep '.claude/skills'` — подтверждение трек-изменений (14 M + 3 ??, 0 D)

## Решения

- **Полная замена папки, а не точечная замена SKILL.md**. **Причина**: в шаблоне `update-skill` нашёлся новый агент `change-applier.md` (16174 байт), которого нет в проектной версии. Если бы заменили только SKILL.md — получили бы рассинхрон: скилл ссылается на агента, которого нет в папке. Снос папки целиком и копирование заново — единственный способ гарантировать консистентность.
- **Два бэкапа в одной папке: `.backup-2026-04-09/` и `.backup-2026-04-10/`**. **Причина**: вчерашний бэкап уже лежал рядом — перезаписывать не стали, сохранили хронологию. Название с датой ISO-стиля (`DD-MM-YYYY`) само упорядочивает бэкапы в `ls`.
- **20 проектных скиллов не трогать**. **Причина**: шаблоны в `~/.claude/skill-templates/` — универсальные движки, а `autofix-e2e`, `test-e2e`, `brd-decompose` и прочие 20 штук живут только в этом проекте и не имеют эталонного аналога.

## Проблемы и решения

- **Проблема**: первая попытка цикла по валидатору упала с `zsh: read-only variable: status`. **Решение**: `status` — зарезервированная переменная в zsh, переименовали локальную переменную в `tail_line`, после чего всё 13 раз прошло за один прогон.
- **Проблема**: в `git status` не было `deleted` файлов, хотя я снёс 13 папок целиком. **Объяснение**: git сравнивает содержимое по путям, а не по inode. Если путь пересоздан с побайтово идентичным файлом — он считается неизменным. Отсутствие `D` в диффе означает, что шаблоны не удалили ни один файл по сравнению со старыми проектными версиями, только обновили/добавили.

## Контекст для следующей сессии

- **Состояние скиллов**: 13 скиллов в `.claude/skills/` побайтово идентичны `/Users/ivan/.claude/skill-templates/`. Остальные 20 проектных — в прежнем состоянии.
- **Новый агент `update-skill/agents/change-applier.md`**: отдельная стадия применения изменений к скиллу. При первом запуске `update-skill` на каком-то SKILL.md в логе появится новый этап между `change-planner` и `update-evaluator`. Если что-то пойдёт не так в этой новой стадии — смотреть там.
- **Новые агенты в `pipeline-explorer`**: `requirements-collector.md` и `stage-test-creator.md` — раньше их не было в проекте, значит логика пайплайн-эксплорера тоже теперь другая, стала многоэтапной.
- **Обновлён `structural-validator.sh`** в `pipeline-implementer/scripts/` — ровно он же прогнался по всем 13 скиллам и выдал PASS, значит рабочий.
- **Бэкап**: полная копия прошлой версии в `.claude/skills/.backup-2026-04-10/` (13 папок). Откат одного скилла — `rm -rf X && cp -R .backup-2026-04-10/X X`.
- **Коммит сессии**: будет сделан в конце этой же сессии одним коммитом после генерации отчёта — формулировка в духе «refactor(skills): синхронизация с эталонными шаблонами».
