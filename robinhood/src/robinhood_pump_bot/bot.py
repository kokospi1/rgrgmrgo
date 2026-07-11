from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from .blockscout import BlockscoutClient
from .dex import DexScreenerClient
from .dexpaprika import DexPaprikaClient
from .filters import passes_filters
from .formatting import format_alert
from .http_client import HttpJsonClient
from .models import TokenCandidate
from .rate_limiter import ApiKeyPool
from .relay import RelayClient
from .scanner import RobinhoodRpcClient, TokenScanner
from .storage import Storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

# Load config/secrets from a .env file (see .env.example). Values already present
# in the real environment take precedence over the file.
load_dotenv(override=False)


def _env_keys(name: str) -> list[str]:
    """Read a comma/space/newline separated list of API keys from an env var."""
    raw = os.getenv(name, "")
    return [k.strip() for k in raw.replace("\n", ",").replace(" ", ",").split(",") if k.strip()]


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_TELEGRAM_USER_ID = int(os.getenv("ALLOWED_TELEGRAM_USER_ID", "8025094859"))
BLOCKSCOUT_API_KEYS = _env_keys("BLOCKSCOUT_API_KEYS")
RELAY_API_KEYS = _env_keys("RELAY_API_KEYS")
DEXPAPRIKA_NETWORK = os.getenv("DEXPAPRIKA_NETWORK", "robinhood")
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DB_PATH = DATA_DIR / "bot.db"

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")


def restricted(fn):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or user.id != ALLOWED_TELEGRAM_USER_ID:
            if update.message:
                await update.message.reply_text("Access denied")
            return
        return await fn(update, context)
    return wrapper


class BotRuntime:
    def __init__(self):
        self.storage = Storage(DB_PATH)
        for key in BLOCKSCOUT_API_KEYS:
            self.storage.add_api_key("blockscout", key)
        for key in RELAY_API_KEYS:
            self.storage.add_api_key("relay", key)
        self.blockscout_pool: ApiKeyPool | None = None
        self.relay_pool: ApiKeyPool | None = None
        self.scanner: TokenScanner | None = None
        self.monitor_task: asyncio.Task | None = None

    async def start_clients(self) -> aiohttp.ClientSession:
        session = aiohttp.ClientSession()
        self.blockscout_pool = ApiKeyPool(self.storage.get_api_key_values("blockscout"), per_second_limit=5, per_hour_limit=100_000)
        # Relay docs: free mode exists. With API key: mostly 10 rps for core endpoints, 200/min for other endpoints.
        self.relay_pool = ApiKeyPool(self.storage.get_api_key_values("relay"), per_second_limit=3, per_hour_limit=12_000)
        free_pool = ApiKeyPool([], per_second_limit=3, per_hour_limit=12_000)
        blockscout_http = HttpJsonClient(session, self.blockscout_pool, key_mode="query", query_name="apikey")
        relay_http = HttpJsonClient(session, self.relay_pool, key_mode="header", header_name="x-api-key")
        dex_http = HttpJsonClient(session, free_pool, key_mode="none")
        dexpaprika_http = HttpJsonClient(session, ApiKeyPool([], per_second_limit=5, per_hour_limit=100_000), key_mode="none")
        # Instantiate Blockscout client so API-key rotation is wired and available for next data-source expansion.
        self.blockscout = BlockscoutClient(blockscout_http)
        self.scanner = TokenScanner(
            storage=self.storage,
            rpc=RobinhoodRpcClient(session=session),
            relay=RelayClient(relay_http),
            dex=DexScreenerClient(dex_http),
            blockscout=self.blockscout,
            dexpaprika=DexPaprikaClient(dexpaprika_http, network=DEXPAPRIKA_NETWORK),
        )
        return session

    def reload_key_pools(self) -> None:
        if self.blockscout_pool:
            self.blockscout_pool = ApiKeyPool(self.storage.get_api_key_values("blockscout"), per_second_limit=5, per_hour_limit=100_000)
        if self.relay_pool:
            self.relay_pool = ApiKeyPool(self.storage.get_api_key_values("relay"), per_second_limit=3, per_hour_limit=12_000)


runtime = BotRuntime()


@restricted
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Robinhood pump bot запущен. /status для настроек.")


@restricted
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = runtime.storage.load_config()
    text = (
        f"Статус: {'PAUSED' if cfg.paused else 'RUNNING'}\n"
        f"Возраст токена <= {cfg.max_age_minutes} мин\n"
        f"Pump >= {cfg.min_pump_percent}%\n"
        f"Dev share filter: {cfg.dev_share_filter_enabled}, max {cfg.max_dev_share_percent}%\n"
        f"Top10 filter: {cfg.top10_filter_enabled}, max {cfg.max_top10_share_percent}%\n"
        f"В наблюдении (watchlist): {runtime.storage.watchlist_size()}\n"
        f"Blockscout keys: {len(runtime.storage.list_api_keys('blockscout'))}\n"
        f"Relay keys: {len(runtime.storage.list_api_keys('relay'))}\n"
    )
    await update.message.reply_text(text)


async def _set_number(update: Update, context: ContextTypes.DEFAULT_TYPE, field: str, cast=float):
    if not context.args:
        await update.message.reply_text("Укажи значение")
        return
    cfg = runtime.storage.load_config()
    setattr(cfg, field, cast(context.args[0]))
    runtime.storage.save_config(cfg)
    await update.message.reply_text(f"OK: {field} = {getattr(cfg, field)}")


@restricted
async def set_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_number(update, context, "max_age_minutes", int)


@restricted
async def set_pump(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_number(update, context, "min_pump_percent", float)


@restricted
async def set_dev_share(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_number(update, context, "max_dev_share_percent", float)


@restricted
async def set_top10_share(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_number(update, context, "max_top10_share_percent", float)


async def _toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, field: str):
    if not context.args or context.args[0].lower() not in {"on", "off"}:
        await update.message.reply_text("Используй on/off")
        return
    cfg = runtime.storage.load_config()
    setattr(cfg, field, context.args[0].lower() == "on")
    runtime.storage.save_config(cfg)
    await update.message.reply_text(f"OK: {field} = {getattr(cfg, field)}")


@restricted
async def toggle_dev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _toggle(update, context, "dev_share_filter_enabled")


@restricted
async def toggle_top10(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _toggle(update, context, "top10_filter_enabled")


@restricted
async def pause_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = runtime.storage.load_config(); cfg.paused = True; runtime.storage.save_config(cfg)
    await update.message.reply_text("Мониторинг остановлен")


@restricted
async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = runtime.storage.load_config(); cfg.paused = False; runtime.storage.save_config(cfg)
    await update.message.reply_text("Мониторинг возобновлен")


@restricted
async def add_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2 or context.args[0] not in {"blockscout", "relay"}:
        await update.message.reply_text("Используй: /add_api_key blockscout|relay KEY")
        return
    runtime.storage.add_api_key(context.args[0], context.args[1])
    runtime.reload_key_pools()
    await update.message.reply_text("API key добавлен")


@restricted
async def list_api_keys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = []
    for provider in ("blockscout", "relay"):
        lines.append(provider + ":")
        for item in runtime.storage.list_api_keys(provider):
            lines.append(f"  {item['id']}: {item['masked']}")
    await update.message.reply_text("\n".join(lines))


@restricted
async def remove_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2 or context.args[0] not in {"blockscout", "relay"}:
        await update.message.reply_text("Используй: /remove_api_key blockscout|relay ID")
        return
    runtime.storage.remove_api_key(context.args[0], int(context.args[1]))
    runtime.reload_key_pools()
    await update.message.reply_text("API key удален")


async def _discover_into_watchlist() -> None:
    """Find freshly minted tokens and put them under observation."""
    candidates = await runtime.scanner.discover_new_tokens(runtime.storage.load_config().lookback_blocks)
    for token in candidates:
        if runtime.storage.was_alerted(token.address) or runtime.storage.in_watchlist(token.address):
            continue
        runtime.storage.add_to_watchlist(token.address, token.to_json(), token.created_at.isoformat())
        log.info("Watching new token %s (%s)", token.symbol, token.address)


async def _process_watchlist(app: Application, cfg) -> None:
    """Re-check every watched token each cycle: refresh the live price, compute the
    pump as growth from the token's INITIAL (launch) price, alert on a match, and
    drop tokens once they expire."""
    for entry in runtime.storage.get_watchlist():
        token = TokenCandidate.from_json(entry["data"])

        # Expire tokens older than the configured age window.
        if token.age_minutes > cfg.max_age_minutes:
            runtime.storage.remove_from_watchlist(token.address)
            continue

        token = await runtime.scanner.enrich_metrics(token)
        current_price = token.metrics.price_usd

        # Initial (launch) price: the token's very first traded price, fetched once
        # from the oldest pool's first OHLCV candle and cached in the watchlist.
        initial_price = entry["initial_price"]
        if initial_price is None:
            initial_price = await runtime.scanner.get_launch_price(token)
            if initial_price:
                runtime.storage.set_watchlist_initial_price(token.address, initial_price)

        # Pump = growth from the token's initial launch price.
        if initial_price and current_price and initial_price > 0:
            token.metrics.price_change_percent = (current_price / initial_price - 1.0) * 100.0

        if passes_filters(token, cfg) and not runtime.storage.was_alerted(token.address):
            await app.bot.send_message(
                chat_id=ALLOWED_TELEGRAM_USER_ID,
                text=format_alert(token),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            runtime.storage.mark_alerted(token.address)
            runtime.storage.remove_from_watchlist(token.address)


async def monitor_loop(app: Application):
    await asyncio.sleep(3)
    while True:
        cfg = runtime.storage.load_config()
        try:
            if not cfg.paused and runtime.scanner:
                await _discover_into_watchlist()
                await _process_watchlist(app, cfg)
            await asyncio.sleep(cfg.scan_interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("monitor loop error: %s", exc)
            await asyncio.sleep(30)


async def post_init(app: Application) -> None:
    # Runs after the bot is initialized and the event loop is running, but before
    # polling starts. Start HTTP clients and launch the background monitor here
    # (PTB 21+ removed the separate post_startup hook).
    app.bot_data["session"] = await runtime.start_clients()
    runtime.monitor_task = asyncio.create_task(monitor_loop(app), name="robinhood-monitor")


async def post_shutdown(app: Application) -> None:
    if runtime.monitor_task:
        runtime.monitor_task.cancel()
        try:
            await runtime.monitor_task
        except asyncio.CancelledError:
            pass
    session = app.bot_data.get("session")
    if session:
        await session.close()


def build_application() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    for cmd, handler in {
        "start": start_cmd,
        "status": status_cmd,
        "set_age": set_age,
        "set_pump": set_pump,
        "set_dev_share": set_dev_share,
        "set_top10_share": set_top10_share,
        "toggle_dev_share": toggle_dev,
        "toggle_top10_share": toggle_top10,
        "pause": pause_cmd,
        "resume": resume_cmd,
        "add_api_key": add_api_key,
        "list_api_keys": list_api_keys,
        "remove_api_key": remove_api_key,
    }.items():
        app.add_handler(CommandHandler(cmd, handler))
    return app


def main() -> None:
    build_application().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
