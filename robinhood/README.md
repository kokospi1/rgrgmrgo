# Robinhood Chain Pump Telegram Bot

Бот мониторит новые ERC-20 токены в Robinhood Chain, проверяет возраст токена, pump %, market cap/liquidity, долю разработчика и долю топ-10 holders, затем отправляет алерт в Telegram.

## Что проверено по API

- Robinhood Chain RPC работает:
  - RPC: `https://rpc.mainnet.chain.robinhood.com`
  - chainId: `4663`
- Blockscout PRO API работает именно через chainId `4663`:
  - REST route: `https://api.blockscout.com/4663/api/v2/...?...&apikey=KEY`
  - Etherscan-compatible route: `https://api.blockscout.com/v2/api?chain_id=4663&...&apikey=KEY`
- Проверенные рабочие Blockscout PRO endpoints:
  - `GET https://api.blockscout.com/4663/api/v2/stats?apikey=KEY`
  - `GET https://api.blockscout.com/4663/api/v2/tokens/{token}?apikey=KEY`
  - `GET https://api.blockscout.com/4663/api/v2/tokens/{token}/holders?apikey=KEY`
  - `GET https://api.blockscout.com/v2/api?chain_id=4663&module=account&action=tokenbalance&contractaddress={token}&address={holder}&apikey=KEY`
- Некоторые агрегирующие/list endpoints у Robinhood Blockscout могут отдавать `500 upstream`, например `/tokens?type=ERC-20` или `/addresses/{address}/transactions` для отдельных адресов. Поэтому основной discovery новых токенов сделан через Robinhood RPC logs, а Blockscout используется для token info/holders/balances.
- Relay API можно использовать бесплатно без ключа, но с лимитами:
  - `/quote`: 50 req/min
  - `/requests`, `/transactions/status`, other: 200 req/min
- Relay API key передается через header `x-api-key`; с ключом лимиты выше. Robinhood Chain есть в `/chains`, но `/currencies/token/price` для многих новых токенов может отдавать `400`, если токен не в их базе.
- DexPaprika (публичная бета, без ключа) — **основной источник** цены, market cap (FDV) и ликвидности для Robinhood Chain:
  - `GET https://api.dexpaprika.com/networks/robinhood/tokens/{token}` → `summary.price_usd`, `summary.fdv`, `summary.liquidity_usd`, `summary.pools`.
  - Проверено вживую на токенах Robinhood Chain (например VEX/WETH). DexScreener и Relay остаются как fallback.
  - DexPaprika **не** отдаёт готовый % изменения цены, поэтому pump считается от **начальной (стартовой) цены токена** (см. ниже).

## Логика обнаружения пампа (watchlist)

- Как только найден новый токен (mint Transfer в RPC logs), он попадает в SQLite-таблицу `watchlist` и наблюдается **весь период `max_age_minutes`** (по умолчанию 30 мин).
- Каждый цикл бот перепроверяет все токены из watchlist: обновляет текущую цену через DexPaprika и считает **pump = рост от начальной (стартовой) цены токена**.
- Начальная цена (`initial_price`) — это самая первая цена токена в момент запуска торгов: берётся из `open` первой OHLCV-свечи самого старого пула токена (`GET /networks/robinhood/pools/{pool}/ohlcv`, интервал 5m, т.к. 1m на этой сети не заполняется). Значение кэшируется в watchlist.
- Памп: `pump% = (текущая_цена / начальная_цена − 1) × 100`. Проверено вживую (например VEX: старт `4.49e-06` → текущая `~8.0e-04` ≈ +17750%).
- Как только токен проходит фильтры — уходит алерт, токен помечается в `alerted_tokens` и удаляется из watchlist. По истечении возраста токен просто удаляется из watchlist.
- Это исправляет ситуацию, когда токен пампится не сразу в момент создания, а через несколько минут, и меряет памп именно от стартовой цены, а не от той, что бот случайно увидел первой.

## Структура проекта

```text
C:\robinhood
├─ bot.py
├─ requirements.txt
├─ requirements-dev.txt
├─ pyproject.toml
├─ README.md
├─ data\bot.db
├─ src\robinhood_pump_bot\
└─ tests\
```

## Установка и запуск на Windows

Ниже команды для обычного Windows `cmd.exe`. Если используешь PowerShell — команды почти те же, только активация venv: `.venv\Scripts\Activate.ps1`.

### 1. Установи Python

Поставь Python 3.11+ с официального сайта:

```text
https://www.python.org/downloads/windows/
```

Важно: при установке включи галочку:

```text
Add python.exe to PATH
```

Проверь в новом окне `cmd.exe`:

```bat
python --version
pip --version
```

Если `python` не найден — закрой/открой терминал заново или переустанови Python с галкой PATH.

### 2. Перейди в папку проекта

```bat
cd C:\robinhood
```

### 3. Создай виртуальное окружение

```bat
python -m venv .venv
```

### 4. Активируй окружение

Для `cmd.exe`:

```bat
.venv\Scripts\activate.bat
```

Для PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Если PowerShell ругается на execution policy, выполни:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Потом снова:

```powershell
.venv\Scripts\Activate.ps1
```

### 5. Установи зависимости

```bat
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Для разработки/тестов дополнительно:

```bat
pip install -r requirements-dev.txt
```

### 6. Проверь, что код рабочий

```bat
pytest -q
python -m compileall -q src bot.py
```

Ожидаемый результат тестов:

```text
14 passed
```

### 7. Запусти бота

```bat
python bot.py
```

Оставь окно открытым. Если хочешь держать бота постоянно — ниже есть варианты автозапуска.

## Автозапуск на Windows

### Вариант A: простейший `.bat`

Создай файл:

```text
C:\robinhood\run_bot.bat
```

Содержимое:

```bat
@echo off
cd /d C:\robinhood
call .venv\Scripts\activate.bat
python bot.py
pause
```

Запуск двойным кликом.

### Вариант B: автозапуск через Task Scheduler

1. Открой `Task Scheduler` / `Планировщик заданий`.
2. `Create Task` / `Создать задачу`.
3. Вкладка `General`:
   - Name: `Robinhood Pump Bot`
   - включи `Run whether user is logged on or not`, если нужно.
4. Вкладка `Triggers`:
   - `New`
   - `At startup` или `At log on`.
5. Вкладка `Actions`:
   - `New`
   - Program/script:
     ```text
     C:\robinhood\.venv\Scripts\python.exe
     ```
   - Add arguments:
     ```text
     bot.py
     ```
   - Start in:
     ```text
     C:\robinhood
     ```
6. Сохрани задачу.
7. Правой кнопкой по задаче → `Run`.

## Команды Telegram

Доступ разрешен только user id `8025094859`.

- `/status` — текущие настройки
- `/set_age 30` — максимальный возраст токена в минутах
- `/set_pump 50` — минимальный pump %
- `/set_dev_share 10` — максимум % токенов у разработчика
- `/toggle_dev_share on|off` — включить/выключить фильтр dev share
- `/set_top10_share 60` — максимум % у топ-10 кошельков
- `/toggle_top10_share on|off` — включить/выключить фильтр top-10
- `/add_api_key blockscout KEY`
- `/add_api_key relay KEY`
- `/list_api_keys`
- `/remove_api_key blockscout ID`
- `/remove_api_key relay ID`
- `/pause`
- `/resume`

## Защита от повторного сканирования

SQLite таблица `scanned_tokens` делает atomic `INSERT OR IGNORE` по адресу токена. Если токен уже был увиден/просканирован, повторный pump с 50% до 60% не запускает новый scan. Таблица `alerted_tokens` дополнительно защищает от повторного алерта.

## API key rotation

Реализован `ApiKeyPool`:

- неограниченное количество ключей в SQLite;
- round-robin;
- per-second limit;
- per-hour limit;
- при `401/403/429` ключ временно выключается и используется следующий.

## Настройка ключей через `.env`

Ключи и токен бота теперь читаются из переменных окружения (файл `.env`), а не хардкодятся в коде.

1. Скопируй `.env.example` в `.env`:

   ```bat
   copy .env.example .env
   ```

2. Заполни значения в `.env`:

   ```text
   TELEGRAM_BOT_TOKEN=токен_от_BotFather
   ALLOWED_TELEGRAM_USER_ID=8025094859
   BLOCKSCOUT_API_KEYS=key1,key2,key3
   RELAY_API_KEYS=key1,key2
   DEXPAPRIKA_NETWORK=robinhood
   ```

   - `BLOCKSCOUT_API_KEYS` и `RELAY_API_KEYS` принимают несколько ключей через запятую — бот их ротирует.
   - Файл `.env` добавлен в `.gitignore` и не попадёт в git.

> Важно по безопасности: ключи, которые ранее лежали в чате/коде, считай скомпрометированными — перевыпусти токен бота у @BotFather и ротируй API-ключи.

## Что делать при ошибках

### `python is not recognized`

Python не добавлен в PATH. Переустанови Python и включи `Add python.exe to PATH`, либо попробуй:

```bat
py --version
py -m venv .venv
```

### `No module named telegram` или `No module named aiohttp`

Окружение не активировано или зависимости не установлены:

```bat
cd C:\robinhood
.venv\Scripts\activate.bat
pip install -r requirements.txt
```

### `pytest is not recognized`

Установи dev requirements:

```bat
pip install -r requirements-dev.txt
```

### Telegram `Conflict: terminated by other getUpdates request`

Где-то уже запущена вторая копия бота. Закрой старое окно/процесс Python или перезапусти сервер.

### Blockscout отдает `500 upstream`

Это не всегда ошибка кода. У Robinhood Blockscout некоторые list/transactions endpoints реально отвечают `500 upstream`. В коде поэтому:

- discovery идет через RPC logs;
- Blockscout PRO используется для точечных `token_info`, `holders`, `tokenbalance`;
- если один endpoint падает, бот продолжает через другие источники.

### Перед тем как писать, что “не работает”

Сначала запусти:

```bat
cd C:\robinhood
.venv\Scripts\activate.bat
pytest -q
python -m compileall -q src bot.py
python bot.py
```

И пришли полный текст ошибки из консоли.
