# XAUUSD Scalper Bot

Автоматический скальпер для MetaTrader 5 на Python и PySide6. Бот работает только с XAUUSD, ставит пару отложенных стоп-ордеров в обе стороны, ждёт импульсный пробой, подтверждает движение и затем управляет уже открытой позицией через break-even и trailing.

---

## 🚀 Быстрый старт (Установка с нуля)

### Требования

| Компонент | Версия | Примечание |
|-----------|--------|------------|
| **Windows** | 10 / 11 | Linux/macOS не поддерживаются (MT5 only Windows) |
| **Python** | 3.11+ (рекомендуется 3.14) | [python.org/downloads](https://python.org/downloads) |
| **MetaTrader 5** | любая сборка | Терминал вашего брокера |

### Шаг 1: Установка Python

1. Скачайте Python с [python.org](https://python.org/downloads)
2. При установке **обязательно включите**:
   - ✅ "Add Python to PATH"
   - ✅ "Install pip"

### Шаг 2: Установка и настройка MetaTrader 5

1. Установите терминал MT5 от вашего брокера
2. Залогиньтесь в терминал
3. Меню **Сервис → Настройки → Советники**:
   - ✅ Разрешить алгоритмическую торговлю
   - ✅ Разрешить DLL (для Python API)

### Шаг 3: Запуск бота

1. Распакуйте ZIP-архив проекта в любую папку
2. Откройте папку проекта
3. **Первый раз**: правой кнопкой по `reset_for_new_user.ps1` → **"Выполнить с помощью PowerShell"**
   - Скрипт очистит старые логи и credentials
4. **Правой кнопкой** по `run_gui.ps1` → **"Выполнить с помощью PowerShell"**

Скрипт автоматически:
- Создаст виртуальное окружение (`venv`)
- Установит все зависимости
- Проверит подключение к MT5
- Запустит GUI

### Если PowerShell блокирует скрипт

Откройте PowerShell от администратора и выполните:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Первый запуск

1. Убедитесь, что MT5 терминал **запущен и залогинен**
2. В GUI откройте **Settings** и укажите:
   - Login / Password / Server (из MT5)
   - Path к терминалу (обычно `C:\Program Files\...\terminal64.exe`)
3. Нажмите **Start** для запуска торговли

---

## Документация

Документ ниже описывает текущее состояние проекта: архитектуру, торговый цикл, защитные механизмы, конфигурацию и тесты.

Дополнительный roadmap по выносу логики из монолитного движка находится в [docs/ENGINE_REFACTOR_PLAN.md](docs/ENGINE_REFACTOR_PLAN.md).

---

## Что делает бот

Основная идея простая:

- выставляет одновременно BUY STOP выше рынка и SELL STOP ниже рынка;
- при срабатывании одного ордера отменяет противоположный;
- переводит сделку в стадию confirm и проверяет, что импульс не ложный;
- после подтверждения включает активное сопровождение позиции;
- двигает SL по правилам break-even и trailing либо закрывает позицию защитными механизмами.

Бот не пытается предсказать направление. Он ждёт фактическое ускорение цены и входит по уже начавшемуся движению.

## Ключевые свойства

- Windows-only стек из-за зависимости от MetaTrader5 terminal и Python-пакета MetaTrader5.
- Единственный поток для торгового ядра и всех вызовов MT5 API.
- GUI работает отдельно и общается с ядром через очередь команд и callback-события.
- Основные операционные артефакты пишутся в JSONL, SQLite и snapshot state.
- Торговая логика покрыта unit/integration/stress тестами, live MT5 тесты помечены отдельно.

## Архитектура

Высокоуровневая схема:

```text
PySide6 GUI
  -> CoreWorker / command queue / UI callbacks
TradingCore
  -> market pipeline
  -> deny policy
  -> state machine
  -> order manager
  -> position manager
  -> persistence / analytics / telegram
MT5Adapter
  -> MetaTrader 5 terminal
```

### Основные слои

`app/src/main.py`

- GUI entrypoint.
- Загружает `config/default.yaml`.
- Создаёт каталоги логов.
- Поднимает `QApplication` и открывает `MainWindow`.

`app/src/core/engine.py`

- Центральный orchestrator `TradingCore`.
- Держит основной цикл и связывает policy/mixin-модули.
- Является единственной точкой, из которой вызывается MT5 API.

`app/src/adapters/mt5_adapter.py`

- Тонкий адаптер над MetaTrader5 Python API.
- Строит request-объекты на выставление, отмену, модификацию и закрытие ордеров.
- Возвращает снапшоты терминала, ордеров, позиций и рыночных данных.

`app/src/core/state.py`

- Каноническое runtime-состояние через `StateStore`.
- Хранит mode/state, активные тикеты, данные позиции, deny-reasons, таймеры, cooldown и recovery-поля.

`app/src/core/persistence.py`

- JSONL event log.
- SQLite trade ledger.
- Подготовка данных для истории сделок и анализа.

### Модули ядра

Ядро разбито на mixin/policy-модули, чтобы `engine.py` оставался оркестратором, а не монолитом.

| Модуль | Назначение |
| --- | --- |
| `engine_market_pipeline.py` | чтение terminal/tick snapshot, spread/ATR preprocessing, micro-guard gating |
| `engine_deny_policy.py` | spread/ATR/session/cooldown/rate-limit/directional deny |
| `engine_state_machine.py` | ветки `ARMED`, `DENY`, `POSITION_CONFIRM`, `POSITION_ACTIVE` |
| `engine_trade_lifecycle.py` | обработка fill, initial SL, finalize close, MFE/MAE |
| `engine_clockwork.py` | tick-independent таймауты, confirm watchdog, fallback clock logic |
| `engine_runtime_guard.py` | preflight, reconnect, startup recovery, safe mode |
| `engine_control_plane.py` | команды из GUI/Telegram, UI payload, event notifications |
| `engine_safety_hooks.py` | retcode escalation, emergency guards, BE-storm protection |
| `engine_cycle_orchestration.py` | reconcile, инварианты, fill detection, pre-state orchestration |
| `engine_runtime_init.py` | runtime-поля, таймеры, counters, snapshot-path |
| `engine_builders.py` | сборка моделей, менеджеров, persistence, gateway |
| `engine_exit_policy.py` | отдельная политика early-exit/followthrough |

### Менеджеры и модели

`app/src/core/order_manager.py`

- Управляет парой BUY STOP / SELL STOP.
- Считает offset и rearm thresholds.
- При дрейфе цены сначала пытается модифицировать pending in-place.
- Если broker-side modify не проходит, откатывается на cancel + replace.
- Контролирует TTL отложек и directional cooldown по сторонам.

`app/src/core/position_manager.py`

- Ведёт позицию через confirm, break-even и trailing.
- Поддерживает двухстадийный break-even.
- Поддерживает ранний перевод в BE, если позиция удерживает положительный profit заданное время.
- Содержит runtime guards для hold/throttle и отдельные policy-блоки по confirm/BE/trailing.

`app/src/core/models_atr.py`

- Wilder ATR для текущего символа.
- Используется как фильтр входа и как масштабирование для SL/offset/trailing.

`app/src/core/models_spread.py`

- Роллинг-модель спреда.
- Детектирует spikes и помогает рассчитывать допустимые лимиты по входу.

`app/src/core/micro_guard.py`

- Защита от плохого канала связи и подвисшего терминала.
- Жёсткие триггеры опираются на stale ticks и IPC latency.
- `ping_last` терминала используется как advisory-сигнал, а не как единственный hard blocker.

`app/src/core/session_control.py`

- Временные окна запрета торговли.
- Блокировки вокруг market open/close и пользовательские trading sessions.

## Машина состояний

Основные `TradingState` определены в `app/src/core/state.py`:

- `IDLE` — нет активной позиции и нет armed pending lifecycle.
- `ARMED` — pending orders активны и сопровождаются.
- `DENY` — вход временно запрещён политикой фильтров или cooldown.
- `POSITION_CONFIRM` — позиция уже открыта, но движение ещё подтверждается.
- `POSITION_ACTIVE` — позиция подтверждена и сопровождается по BE/trailing правилам.
- `RECOVERY` — запуск после рестарта при уже существующей позиции или состоянии терминала.
- `EMERGENCY` — аварийный режим при серьёзной несогласованности или критическом сбое.

Отдельно есть `SystemMode`, включая `SAFE`, в котором торговля приостановлена до ручного или автоматического восстановления.

Упрощённый жизненный цикл:

```text
IDLE
  -> filters OK
ARMED
  -> one pending filled, opposite canceled
POSITION_CONFIRM
  -> move confirmed
POSITION_ACTIVE
  -> BE / trailing / exit
IDLE
```

## Торговый цикл

Каждый цикл ядра, в нормальном режиме, делает следующее:

1. Проверяет terminal health и получает новый tick/snapshot.
2. Обновляет spread и ATR модели.
3. Прогоняет `micro_guard` и runtime safety checks.
4. Считает deny-reasons: spread, ATR, session, cooldown, rate limit, directional cooldown.
5. Если торговля разрешена, обслуживает lifecycle pending orders.
6. Если есть активная позиция, обслуживает confirm, BE, trailing и exits.
7. Пишет события и обновляет snapshot состояния.

Цикл проектировался как ограниченный по времени. В конфиге есть `timing.cycle_interval_ms` и `timing.cycle_budget_ms`.

## Вход в сделку

Вход строится на dual pending strategy:

- BUY STOP ставится выше ask.
- SELL STOP ставится ниже bid.
- Базовый отступ зависит от spread, ATR, рыночного шума и flat detector.
- Есть жёсткий нижний порог `entry.min_total_offset_points`, чтобы ордера не оказывались слишком близко к рынку.
- Rearm работает по hysteresis, чтобы ордера не дёргались на каждом микродвижении.

Актуальное поведение rearm:

- при смещении цены бот сначала делает MODIFY существующего pending order;
- если modify отвергнут или недоступен, бот делает fallback на cancel + replace;
- это уменьшает визуальное мигание ордеров и сохраняет способность двигать их при broker-side отказах.

## Управление позицией

После fill бот проходит несколько стадий.

### Confirm

- Позиция не считается полноценной сразу после входа.
- Нужны минимальное движение и укладывание в confirm window.
- Confirm поддерживается не только тиками, но и clock-driven логикой, чтобы не зависеть полностью от частоты тиков.

### Break-even

В проекте несколько уровней BE-защиты:

- двухстадийный break-even по points/spread правилам;
- USD-based activation thresholds;
- ранний перевод в BE, если сделка держится в положительном profit в течение `breakeven.profit_hold_ms`;
- BE старается не срабатывать слишком рано, пока не покрываем spread/buffer.

### Trailing

- Trailing может быть классическим или в режимах APT.
- Есть отдельные gap/step/min-hold/throttle параметры.
- Hold guard для trailing и BE разделён: BE может сработать раньше, даже если trailing ещё под защитной задержкой.

## Защитные механизмы

В проекте несколько независимых контуров безопасности.

### Рыночные фильтры

- минимальный ATR;
- ограничение по spread;
- burst/noise gating;
- flat detector и flat freeze;
- session windows.

### Trade frequency guards

- cooldown после win/loss/close;
- directional cooldown по стороне;
- rate limit на число сделок в коротком окне.

### Runtime guards

- reconnect с backoff;
- startup recovery после рестарта;
- state snapshot и выравнивание состояния ядра с терминалом;
- emergency handling при инвариантных нарушениях.

### MicroGuard

- реагирует на stale ticks и IPC latency;
- может поставить ядро на паузу через `pause_on_trigger_ms`;
- после паузы использует окно стабильности `recovery_stability_ms`, прежде чем снова разрешить rearm pending orders;
- высокий `ping_last` сам по себе не блокирует торговлю, если тики и IPC остаются здоровыми.

### Retcode policy

Логика MT5 retcodes централизована вокруг safety hooks и адаптера.

- `10008`, `10009`, `10010` считаются успешным результатом;
- `10004` и похожие временные ошибки обрабатываются через retry/backoff;
- `10025` (`NO_CHANGES`) допускается как безопасный сценарий для некоторых операций;
- тяжёлые ошибки могут переводить систему в `SAFE` или жёстко блокировать торговлю.

## GUI

GUI реализован на PySide6.

Основные файлы:

- `app/src/ui/main_window.py` — главное окно и orchestration UI;
- `app/src/ui/widgets_dashboard.py` — текущий runtime-статус;
- `app/src/ui/widgets_logs.py` — просмотр event log;
- `app/src/ui/widgets_trades.py` — история сделок из SQLite;
- `app/src/ui/widgets_ml.py` — ML/feature-таблица;
- `app/src/ui/settings_dialog.py` — редактирование конфигурации;
- `app/src/ui/theme.py` — тема и styling.

GUI не должен напрямую вызывать MT5. Все торговые команды проходят через ядро.

## Конфигурация

Основной конфиг находится в `config/default.yaml`.

Наиболее важные секции:

| Секция | За что отвечает |
| --- | --- |
| `symbol` | имя символа и magic |
| `mt5` | login/password/server/path/timeout |
| `risk` | объём, целевой риск, emergency SL |
| `entry` | offset, hysteresis, flat freeze, burst/noise gating |
| `confirm` | confirm window, минимальное движение, fail cooldown |
| `breakeven` | параметры BE, stage1/stage2, USD activation, `profit_hold_ms` |
| `trailing` | trailing mode, gap, step, hold, throttle |
| `micro_guard` | stale/latency thresholds, pause, recovery stability |
| `session` | торговые окна и market open/close блокировки |
| `retcode` | retry/backoff/operation deadline |
| `logging` | пути до JSONL и SQLite |
| `timing` | частота цикла и budget |

Перед live-запуском достаточно проверить минимум:

- `mt5.login`, `mt5.password`, `mt5.server`, `mt5.path`;
- `symbol.name`;
- `risk.volume` и `risk.target_risk_usd`;
- `logging.*` пути;
- `telegram.*`, если требуется удалённое управление.

## Логи и артефакты

По умолчанию проект использует:

- `logs/events.jsonl` — поток событий и runtime decision trail;
- `logs/trades.db` — история сделок для UI и аналитики;
- `logs/state_snapshot.json` — snapshot состояния для recovery после рестарта.

Если бот ведёт себя неожиданно, сначала нужно смотреть именно `events.jsonl`: там обычно видно deny-reasons, rearm, BE transitions, micro-guard и retcode события.

## Структура проекта

```text
Scalper/
  app/src/main.py
  app/src/preflight.py
  app/src/adapters/
  app/src/core/
  app/src/ui/
  app/src/tests/
  config/default.yaml
  logs/
  run_gui.ps1
  requirements.txt
  pytest.ini
```

Тесты разбиты на три уровня:

- `app/src/tests/unit` — основная логика policy/state/order/position;
- `app/src/tests/integration` — связки жизненного цикла;
- `app/src/tests/stress` — сценарии деградации и spike-поведения.

## Запуск

### Рекомендуемый способ

Используйте PowerShell launcher:

```powershell
./run_gui.ps1
```

Скрипт:

1. проверяет Python 3.11+;
2. создаёт `venv`, если его нет;
3. проверяет и при необходимости ставит зависимости;
4. проверяет наличие MetaTrader 5 terminal;
5. запускает `app/src/preflight.py`;
6. поднимает GUI.

### Ручной запуск

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe app\src\preflight.py
.\venv\Scripts\python.exe app\src\main.py
```

## Проверка и тесты

Базовый запуск тестов:

```powershell
pytest
```

По умолчанию `pytest.ini` исключает тесты с маркером `mt5`, которым нужен живой терминал.

Если нужно запустить только live-зависимые сценарии:

```powershell
pytest -m mt5
```

Если нужен только preflight:

```powershell
.\venv\Scripts\python.exe app\src\preflight.py
```

## Типовой operational flow

Перед стартом live-сессии:

1. Убедиться, что терминал MetaTrader 5 запущен и залогинен.
2. Проверить корректность `config/default.yaml`.
3. Запустить `run_gui.ps1`.
4. Убедиться, что preflight не вернул blocking errors.
5. Проверить в GUI symbol, state, mode и доступность логирования.

Во время работы:

- следить за `DENY`/`SAFE` событиями;
- отслеживать частоту rearm и причины отмен;
- смотреть, не срабатывает ли `micro_guard` слишком часто;
- анализировать `BE_MOVED`, trailing и close-события через JSONL и trade ledger.

## Ограничения и замечания

- Проект рассчитан на Windows и MetaTrader 5 terminal.
- Основной symbol в текущем виде — XAUUSD.
- Конфиг в репозитории содержит рабочие дефолты, но не должен считаться универсальным для любого брокера.
- Любая live-эксплуатация требует ручной валидации на конкретных broker rules: stops level, filling mode, freeze level, latency, spread profile.

## Куда смотреть дальше

- [docs/ENGINE_REFACTOR_PLAN.md](docs/ENGINE_REFACTOR_PLAN.md) — план дальнейшей декомпозиции движка.
- `app/src/tests` — фактическое покрытие сценариев.
- `config/default.yaml` — оперативные торговые настройки.
