# Robinhood Pump Bot — контекст чата

## Репозиторий

- GitHub: `kokospi1/rgrgmrgo`, ветка `robinhood-folder`
- Папка с ботом: `robinhood/` (src-layout: пакет в `robinhood/src/robinhood_pump_bot/`)
- Запуск: `python bot.py` из `C:\robinhood` (корневой `bot.py` сам добавляет `src` в `sys.path`)
- Python 3.11+, venv в `C:\robinhood\.venv`

---

## Что делает бот

Telegram-бот на Python, мониторит Robinhood Chain (EVM L2, chainId 4663) на новые ERC-20 токены и шлёт алерт когда токен пампит.

**Стек:** `python-telegram-bot>=21` (проверен на 22.8), `aiohttp`, `python-dotenv`, SQLite (хранилище).

**Владелец / единственный пользователь:** Telegram user id `8025094859`.

---

## Архитектура (файлы)

```
robinhood/
├── bot.py                        # точка входа, добавляет src в path
├── .env                          # секреты (в .gitignore)
├── .env.example                  # шаблон
├── requirements.txt              # aiohttp, python-telegram-bot, python-dotenv
├── requirements-dev.txt          # pytest, pytest-asyncio
├── pyproject.toml                # src-layout, pythonpath = ["src"] для pytest
└── src/robinhood_pump_bot/
    ├── bot.py          # Application PTB, хендлеры, monitor_loop, post_init/post_shutdown
    ├── scanner.py      # RobinhoodRpcClient (eth_getLogs), TokenScanner
    ├── blockscout.py   # BlockscoutClient — тип токена, holders, supply
    ├── dex.py          # DexScreenerClient — цена/ликвидность (fallback)
    ├── dexpaprika.py   # DexPaprikaClient — основной источник цены, launch_price
    ├── relay.py        # RelayClient — цена (2й fallback)
    ├── models.py       # TokenCandidate, TokenMetrics, to_json/from_json
    ├── storage.py      # SQLite: config, alerted_tokens, watchlist, api_keys
    ├── filters.py      # passes_filters (возраст, памп, dev_share, top10)
    ├── formatting.py   # format_alert — текст Telegram-сообщения
    ├── http_client.py  # HttpJsonClient + HttpStatusError (4xx не ретраятся)
    └── rate_limiter.py # ApiKeyPool — ротация ключей, лимиты 5/сек 100k/час
```

---

## Ключевые технические решения

### Источники данных

| Источник | Роль | Примечание |
|----------|------|-----------|
| Robinhood RPC `https://rpc.mainnet.chain.robinhood.com` | обнаружение токенов (eth_getLogs) | |
| Blockscout `https://robinhoodchain.blockscout.com/api/v2` | тип токена, holders, supply | **не даёт цену** (exchange_rate=null) |
| DexPaprika `https://api.dexpaprika.com` | цена, FDV, ликвидность — **основной** | бесплатный, без ключа, слаг сети: `robinhood` |
| DexScreener | цена/ликвидность — fallback | |
| Relay API | цена — 2й fallback | |

### Логика watchlist (исправленная)

1. Обнаружен новый токен через RPC → попадает в таблицу `watchlist`
2. Проверка через Blockscout: если тип не `ERC-20` (NFT ERC-721/1155) → пропускаем (NFT-минты имеют тот же Transfer-event)
3. Каждый цикл: обновляем цену через DexPaprika → считаем памп
4. **Памп = рост от `initial_price`** (начальная/стартовая цена токена, а НЕ первая увиденная ботом)
5. `initial_price` берётся из `open` первой OHLCV-свечи (5m) старейшего пула: `GET /networks/robinhood/pools/{pool}/ohlcv?start={unix}&interval=5m&limit=100`
6. Кэшируется в колонке `watchlist.initial_price`
7. Если фильтры прошли → алерт → токен в `alerted_tokens`, удаляется из watchlist
8. Если возраст > `max_age_minutes` → удаляется из watchlist

### Важные фиксы в этом чате

- **`post_startup` удалён в PTB 21+** → монитор запускается в `post_init`
- **NFT-ложняки**: ERC-721 (Uniswap V3 Positions и др.) имеют тот же Transfer event → фильтруем по `blockscout.token_type()`
- **404 ретраился 5 раз с WARNING** → теперь `HttpStatusError` кидается сразу, без retry, логируется на DEBUG
- **eth_getLogs 10k-limit**: `get_logs` рекурсивно дробит диапазон при превышении, `MAX_LOG_BLOCKS_PER_TICK=20`
- **src-layout**: `python bot.py` работает без `pip install -e .` — корневой `bot.py` сам добавляет `src` в `sys.path`

---

## Переменные окружения (.env)

```env
TELEGRAM_BOT_TOKEN=токен_от_BotFather
ALLOWED_TELEGRAM_USER_ID=8025094859
BLOCKSCOUT_API_KEYS=key1,key2        # несколько через запятую, ротируются
RELAY_API_KEYS=key1,key2
DEXPAPRIKA_NETWORK=robinhood
```

Без `TELEGRAM_BOT_TOKEN` бот падает с понятной ошибкой при старте.

---

## Telegram-команды бота

| Команда | Действие |
|---------|---------|
| `/status` | текущий конфиг + размер watchlist |
| `/set_age <мин>` | максимальный возраст токена |
| `/set_pump <процент>` | минимальный % пампа |
| `/toggle_dev_share` | вкл/выкл фильтр доли разработчика |
| `/toggle_top10` | вкл/выкл фильтр топ-10 холдеров |
| `/set_dev_share <процент>` | порог доли разработчика |
| `/set_top10 <процент>` | порог топ-10 |
| `/pause` / `/resume` | пауза/возобновление мониторинга |
| `/add_api_key <provider> <key>` | добавить ключ в пул |

---

## Что НЕ сделано (запланировано, ждёт решения)

Пользователь выбрал следующие улучшения для реализации (ещё не закодированы):

### Точно берём:
1. **Детектор ускорения по последним N свечам** — два триггера алерта:
   - Глобальный: рост от `initial_price` ≥ порога (текущий)
   - Локальный: рост за последние 3 свечи (15 мин) ≥ 20%
   - В алерте указывается какой триггер сработал: `[PUMP]` или `[ACCELERATION]`
2. **Адаптивный интервал опроса watchlist**:
   - < 5 мин: каждые 30 сек
   - 5–15 мин: каждые 60 сек
   - > 15 мин: каждые 2 мин

### Обсуждаются (ещё не решено):
3. **Cooldown на повторные алерты** — не удалять из watchlist после алерта, ставить cooldown 10 мин, максимум 3 алерта на токен, метки `[2-я волна]`, `[3-я волна]`
4. **Предупредительный алерт `[WATCH]`** — рост ≥ 15% И ликвидность растёт → ранний сигнал до основного движения
5. **Детектор `[NEW HIGH]`** — токен обновил хай после коррекции → сигнал возобновления
6. **Динамический порог пампа по ликвидности**:
   - < $1k: порог 100%
   - $1k–$10k: порог 50%
   - > $10k: порог 30%
   - Команда `/set_liq_tiers`

---

## Запуск тестов

```bat
cd C:\robinhood
.venv\Scripts\activate.bat
pip install -r requirements.txt
pip install -r requirements-dev.txt
python -m pytest -q        # 17 passed
python -m compileall -q src bot.py
python bot.py
```

Pytest запускать через `python -m pytest` (не просто `pytest`), иначе не подхватит `src`.
