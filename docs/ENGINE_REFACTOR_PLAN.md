# Engine Refactor Plan

## Цель

Довести `TradingCore` до состояния, где:

- `engine.py` остаётся только тонким orchestrator-layer
- каждая группа ответственности изолирована в отдельном модуле
- risky trading logic легче тестировать отдельно
- trailing / confirm / recovery / deny / safety paths можно менять без каскадных поломок
- архитектура понятна без чтения одного огромного файла

## Что уже вынесено

Текущее состояние после завершённых шагов:

- `engine_trade_lifecycle.py` — fill / initial SL / finalize / MFE-MAE
- `engine_deny_policy.py` — deny / cooldown / directional block
- `engine_clockwork.py` — clock-driven confirm / watchdog / fallback trailing
- `engine_runtime_guard.py` — preflight / reconnect / recovery / safe mode
- `engine_control_plane.py` — telegram / core commands / event log / UI payload
- `engine_safety_hooks.py` — retcode escalation / BE-storm / double-trigger / API restriction symptoms
- `engine_state_machine.py` — ARMED / DENY / CONFIRM / ACTIVE branches
- `engine_builders.py` — subsystem builders for `__init__`
- `engine_market_pipeline.py` — terminal / tick / spread / ATR pre-pipeline
- `engine_runtime_init.py` — runtime timers / flags / counters / run identity
- `engine_cycle_orchestration.py` — reconcile / invariants / fill orchestration before state machine

## Целевое состояние

Финальная форма `TradingCore`:

- запускает loop
- собирает market context
- вызывает orchestration steps
- вызывает state machine
- публикует UI/update side effects

То есть `engine.py` должен читаться примерно так:

1. Инициализация подсистем
2. Инициализация runtime state
3. Start/stop lifecycle
4. Main loop
5. High-level cycle pipeline

Без длинных доменных веток и без низкоуровневой служебной логики внутри.

## План работ

### Phase 1. Закрепить текущую декомпозицию

Задачи:

- проверить, что каждый новый mixin закрывает одну понятную область ответственности
- убрать остаточные дубли описаний в README и привести терминологию к одному словарю
- зафиксировать карту модулей и зависимостей

Критерий готовности:

- по имени файла понятно, зачем он существует
- нет модулей со смешанной ответственностью

### Phase 2. Добить тонкий orchestrator в `engine.py`

Задачи:

- ещё сильнее сократить `_cycle()` до последовательности шагов верхнего уровня
- при необходимости ввести маленькие private-step methods уровня orchestrator
- убедиться, что `engine.py` не знает деталей реализации отдельных policy/service blocks

Критерий готовности:

- `engine.py` можно прочитать сверху вниз как сценарий работы ядра
- внутри файла минимум ветвистой доменной логики

### Phase 3. Усилить тестируемость новых слоёв

Задачи:

- добавить unit tests на `engine_state_machine.py`
- добавить unit tests на `engine_cycle_orchestration.py`
- добавить unit tests на `engine_market_pipeline.py`
- при необходимости сделать lightweight fakes/fixtures для adapter/state/order manager

Критерий готовности:

- новые выносы покрыты не только косвенно через старые engine tests
- падение в одном mixin легче локализовать по тестам

### Phase 4. Нормализовать контракты между mixin-слоями

Задачи:

- привести Protocol contracts к единому стилю
- уменьшить количество `Any`, где это реально возможно
- выделить повторяемые host-capabilities в более компактные Protocol blocks

Критерий готовности:

- статическая типизация помогает, а не просто не мешает
- новые выносы не плодят editor noise

### Phase 5. Выделить высокорисковые торговые зоны в отдельные policy/service blocks

Это уже не обязательно для красоты, а для будущих изменений.

Кандидаты:

- early-exit policy
- trailing activation / trailing progression policy
- recovery decision policy
- invariant handling policy

Критерий готовности:

- самые хрупкие торговые решения изолированы отдельно и меняются локально

### Phase 6. Подготовить архитектуру к следующему уровню

Задачи:

- решить, остаётся ли mixin-подход финальной формой или это промежуточный этап
- если это промежуточный этап, спланировать переход к composition/service objects
- отделить pure decision logic от side-effect logic там, где это даст реальную пользу

Критерий готовности:

- понятно, это конечная архитектура или staging architecture
- следующие изменения не ведут обратно к монолиту

### Phase 7. Документация и эксплуатация

Задачи:

- обновить архитектурную схему в README под финальную форму
- описать порядок дебага: где искать deny, где recovery, где trailing, где watchdog
- описать безопасный порядок дальнейших выносов и проверок

Критерий готовности:

- новый разработчик понимает, куда идти по каждому типу проблемы

## Приоритеты

Если делать строго по полезности, а не по красоте:

1. `Phase 3` — тестируемость новых выносов
2. `Phase 4` — стабильные контракты и typing
3. `Phase 5` — изоляция хрупкой trading logic
4. `Phase 2` — финальная чистка orchestrator
5. `Phase 7` — документация
6. `Phase 6` — решение про future architecture

Если делать строго по чистоте структуры:

1. `Phase 2`
2. `Phase 4`
3. `Phase 3`
4. `Phase 5`
5. `Phase 7`
6. `Phase 6`

## Основные риски

- mixin architecture может стать слишком фрагментированной и перестать быть очевидной
- Protocol typing может разрастись сильнее, чем реальная польза
- без targeted tests вынос даёт чистый вид, но не даёт уверенности в поведении
- trailing / confirm / safety logic остаётся самой рискованной областью даже после декомпозиции

## Definition of Done

Рефакторинг можно считать завершённым, когда одновременно выполнено всё ниже:

- `engine.py` остаётся компактным orchestrator file
- каждый engine_* модуль отвечает за один понятный слой
- на новые слои есть прямые unit tests
- Pylance/diagnostics по основным модулям чистые
- полный `pytest` стабильно зелёный
- README и этот план соответствуют фактической структуре

## Практический порядок продолжения

Рекомендуемый следующий execution order:

1. Добавить targeted tests для `engine_cycle_orchestration.py`
2. Добавить targeted tests для `engine_state_machine.py`
3. Подчистить Protocol contracts и уменьшить `Any`
4. Вынести trailing/early-exit policy в отдельный layer, если хотим дальше улучшать торговую часть
5. После этого уже решать, оставляем mixin architecture как финальную или идём дальше в composition