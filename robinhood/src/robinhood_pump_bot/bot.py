from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from .blockscout import BlockscoutClient
from .dex import DexScreenerClient
from .dexpaprika import DexPaprikaClient
from .filters import ALERT_EARLY, check_alert_trigger
from .formatting import format_alert
from .http_client import HttpJsonClient
from .models import TokenCandidate
from .rate_limiter import ApiKeyPool
from .relay import RelayClient
from .scanner import RobinhoodRpcClient, TokenScanner
from .storage import Storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

load_dotenv(override=False)


def _env_keys(name: str) -> list[str]:
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
        self.blockscout_pool = ApiKeyPool(
            self.storage.get_api_key_values("blockscout"), per_second_limit=5, per_hour_limit=100_000
        )
        self.relay_pool = ApiKeyPool(
            self.storage.get_api_key_values("relay"), per_second_limit=3, per_hour_limit=12_000
        )
        free_pool = ApiKeyPool([], per_second_limit=3, per_hour_limit=12_000)
        blockscout_http = HttpJsonClient(session, self.blockscout_pool, key_mode="query", query_name="apikey")
        relay_http = HttpJsonClient(session, self.relay_pool, key_mode="header", header_name="x-api-key")
        dex_http = HttpJsonClient(session, free_pool, key_mode="none")
        dexpaprika_http = HttpJsonClient(
            session, ApiKeyPool([], per_second_limit=5, per_hour_limit=100_000), key_mode="none"
        )
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
            self.blockscout_pool = ApiKeyPool(
                self.storage.get_api_key_values("blockscout"), per_second_limit=5, per_hour_limit=100_000
            )
        if self.relay_pool:
            self.relay_pool = ApiKeyPool(
                self.storage.get_api_key_values("relay"), per_second_limit=3, per_hour_limit=12_000
            )


runtime = BotRuntime()


# ------------------------------------------------------------------ commands

@restricted
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Robinhood pump bot запущен. /status для настроек.")


@restricted
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = runtime.storage.load_config()
    text = (
        f"Статус: {'PAUSED' if cfg.paused else 'RUNNING'}\n"
        f"Возраст: {'<= ' + str(cfg.max_age_minutes) + ' мин' if cfg.age_filter_enabled else 'ВЫКЛ (без ограничений)'}\n"
        f"Pump >= {cfg.min_pump_percent}%\n"
        f"Dev share: {cfg.dev_share_filter_enabled}, max {cfg.max_dev_share_percent}%\n"
        f"Top10: {cfg.top10_filter_enabled}, max {cfg.max_top10_share_percent}%\n"
        f"\n--- Acceleration ---\n"
        f"Включено: {cfg.acceleration_enabled}\n"
        f"Порог: {cfg.min_acceleration_percent}% за {cfg.acceleration_candles} свечи(5м)\n"
        f"\n--- Early Warning ---\n"
        f"Включено: {cfg.early_warning_enabled}\n"
        f"Порог: {cfg.early_warning_percent}%, мин.ликв: ${cfg.early_warning_min_liquidity:.0f}\n"
        f"\n--- Cooldown ---\n"
        f"Включено: {cfg.cooldown_enabled}, {cfg.cooldown_minutes} мин, макс {cfg.max_alerts_per_token} алерт\n"
        f"\n--- New High ---\n"
        f"Включено: {cfg.new_high_enabled}, мин.коррекция {cfg.new_high_min_correction_percent}%\n"
        f"\n--- Liq Tiers ---\n"
        f"Включено: {cfg.liq_tiers_enabled}\n"
        f"<${cfg.liq_tier_low_usd:.0f}: {cfg.liq_tier_low_pump}%, "
        f"${cfg.liq_tier_low_usd:.0f}-{cfg.liq_tier_mid_usd:.0f}: {cfg.liq_tier_mid_pump}%, "
        f">${cfg.liq_tier_mid_usd:.0f}: {cfg.liq_tier_high_pump}%\n"
        f"\n--- Прочее ---\n"
        f"В наблюдении: {runtime.storage.watchlist_size()}\n"
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


async def _toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, field: str):
    if not context.args or context.args[0].lower() not in {"on", "off"}:
        await update.message.reply_text("Используй on/off")
        return
    cfg = runtime.storage.load_config()
    setattr(cfg, field, context.args[0].lower() == "on")
    runtime.storage.save_config(cfg)
    await update.message.reply_text(f"OK: {field} = {getattr(cfg, field)}")


@restricted
async def toggle_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _toggle(update, context, "age_filter_enabled")

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

# --- new feature commands ---

@restricted
async def toggle_accel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _toggle(update, context, "acceleration_enabled")

@restricted
async def set_accel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /set_accel 20 3  (threshold% candles)"""
    cfg = runtime.storage.load_config()
    if context.args and len(context.args) >= 1:
        cfg.min_acceleration_percent = float(context.args[0])
    if context.args and len(context.args) >= 2:
        cfg.acceleration_candles = int(context.args[1])
    runtime.storage.save_config(cfg)
    await update.message.reply_text(
        f"Acceleration: {cfg.min_acceleration_percent}% за {cfg.acceleration_candles} свечи"
    )

@restricted
async def toggle_early(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _toggle(update, context, "early_warning_enabled")

@restricted
async def set_early(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /set_early 15 [min_liq_usd]"""
    cfg = runtime.storage.load_config()
    if context.args and len(context.args) >= 1:
        cfg.early_warning_percent = float(context.args[0])
    if context.args and len(context.args) >= 2:
        cfg.early_warning_min_liquidity = float(context.args[1])
    runtime.storage.save_config(cfg)
    await update.message.reply_text(
        f"Early warning: {cfg.early_warning_percent}%, мин.ликв ${cfg.early_warning_min_liquidity:.0f}"
    )

@restricted
async def toggle_cooldown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _toggle(update, context, "cooldown_enabled")

@restricted
async def set_cooldown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /set_cooldown 10 [max_alerts]"""
    cfg = runtime.storage.load_config()
    if context.args and len(context.args) >= 1:
        cfg.cooldown_minutes = int(context.args[0])
    if context.args and len(context.args) >= 2:
        cfg.max_alerts_per_token = int(context.args[1])
    runtime.storage.save_config(cfg)
    await update.message.reply_text(
        f"Cooldown: {cfg.cooldown_minutes} мин, макс {cfg.max_alerts_per_token} алерт/токен"
    )

@restricted
async def toggle_new_high(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _toggle(update, context, "new_high_enabled")

@restricted
async def toggle_liq_tiers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _toggle(update, context, "liq_tiers_enabled")

@restricted
async def set_liq_tiers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /set_liq_tiers low_usd mid_usd low_pump mid_pump high_pump
    Example: /set_liq_tiers 1000 10000 100 50 30"""
    cfg = runtime.storage.load_config()
    if context.args and len(context.args) >= 5:
        cfg.liq_tier_low_usd = float(context.args[0])
        cfg.liq_tier_mid_usd = float(context.args[1])
        cfg.liq_tier_low_pump = float(context.args[2])
        cfg.liq_tier_mid_pump = float(context.args[3])
        cfg.liq_tier_high_pump = float(context.args[4])
        runtime.storage.save_config(cfg)
        await update.message.reply_text(
            f"Liq tiers: <${cfg.liq_tier_low_usd:.0f}={cfg.liq_tier_low_pump}%, "
            f"${cfg.liq_tier_low_usd:.0f}-{cfg.liq_tier_mid_usd:.0f}={cfg.liq_tier_mid_pump}%, "
            f">${cfg.liq_tier_mid_usd:.0f}={cfg.liq_tier_high_pump}%"
        )
    else:
        await update.message.reply_text("Нужно 5 аргументов: low_usd mid_usd low_pump mid_pump high_pump")

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


# --------------------------------------------------------------- monitor loop

def _poll_interval(age_minutes: float, base_interval: int) -> float:
    """Adaptive polling interval based on token age (feature 2).

    Fresh tokens are polled most aggressively so the bot catches the very first
    candles, then backs off as the token ages.
      < 5 min  -> 30s  (every tick regardless of base_interval)
      5-15 min -> 60s
      > 15 min -> base_interval (default 120s, user-configurable)
    """
    if age_minutes < 5:
        return 30
    if age_minutes < 15:
        return 60
    return float(base_interval)


async def _discover_into_watchlist() -> None:
    candidates = await runtime.scanner.discover_new_tokens(runtime.storage.load_config().lookback_blocks)
    for token in candidates:
        if runtime.storage.was_alerted(token.address) or runtime.storage.in_watchlist(token.address):
            continue
        runtime.storage.add_to_watchlist(token.address, token.to_json(), token.created_at.isoformat())
        log.info("Watching new token %s (%s)", token.symbol, token.address)


async def _process_one(app: Application, entry: dict, cfg) -> None:
    """Process a single watchlist entry: enrich, detect trigger, send alert."""
    token = TokenCandidate.from_json(entry["data"])

    if cfg.age_filter_enabled and token.age_minutes > cfg.max_age_minutes:
        runtime.storage.remove_from_watchlist(token.address)
        return

    # Adaptive polling: skip this token if it was checked too recently.
    last_checked_key = f"last_checked:{token.address}"
    now_ts = datetime.now(timezone.utc).timestamp()
    last_checked = runtime.storage.get_kv(last_checked_key, 0.0)
    required_interval = _poll_interval(token.age_minutes, cfg.scan_interval_seconds)
    if now_ts - last_checked < required_interval:
        return
    runtime.storage.set_kv(last_checked_key, now_ts)

    # Enrich live metrics.
    token = await runtime.scanner.enrich_metrics(token)
    current_price = token.metrics.price_usd

    # Cache initial (launch) price.
    initial_price = entry["initial_price"]
    if initial_price is None:
        initial_price = await runtime.scanner.get_launch_price(token)
        if initial_price:
            runtime.storage.update_watchlist_state(token.address, initial_price=initial_price)

    # Pump from launch price.
    if initial_price and current_price and initial_price > 0:
        token.metrics.price_change_percent = (current_price / initial_price - 1.0) * 100.0

    # Acceleration: growth over the last N candles.
    if cfg.acceleration_enabled and runtime.scanner.dexpaprika:
        candles = await runtime.scanner.dexpaprika.recent_candles(
            token.address, count=cfg.acceleration_candles
        )
        if len(candles) >= 2:
            c_open = candles[0].get("open")
            c_close = candles[-1].get("close")
            if c_open and c_close and float(c_open) > 0:
                token.metrics.acceleration_percent = (float(c_close) / float(c_open) - 1.0) * 100.0

    # Carry forward previous liquidity for early-warning liq check.
    token.metrics.prev_liquidity_usd = entry.get("prev_price")  # reused field

    # Update all-time high.
    ath = entry["all_time_high"]
    if current_price and (ath is None or current_price > ath):
        ath = current_price
        runtime.storage.update_watchlist_state(token.address, all_time_high=ath)

    alert_count = entry["alert_count"] or 0
    early_warned = bool(entry["early_warned"])

    # Cooldown check: skip if still within the silence window.
    if cfg.cooldown_enabled and entry["last_alerted_at"] and alert_count >= 1:
        last_alert_dt = datetime.fromisoformat(entry["last_alerted_at"])
        if last_alert_dt.tzinfo is None:
            last_alert_dt = last_alert_dt.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last_alert_dt).total_seconds() / 60
        if elapsed < cfg.cooldown_minutes:
            return

    # Max alerts per token reached -> retire from watchlist.
    if cfg.cooldown_enabled and alert_count >= cfg.max_alerts_per_token:
        runtime.storage.remove_from_watchlist(token.address)
        return

    # Determine which trigger fires (if any).
    trigger = check_alert_trigger(
        token,
        cfg,
        all_time_high=entry["all_time_high"],   # use pre-update ATH for comparison
        prev_price=entry["prev_price"],
        early_warned=early_warned,
    )
    if trigger is None:
        # Persist current price as prev_price for next cycle.
        if current_price:
            runtime.storage.update_watchlist_state(token.address, prev_price=current_price)
        return

    # --- send alert ---
    new_alert_count = alert_count + 1
    is_early = trigger == ALERT_EARLY

    await app.bot.send_message(
        chat_id=ALLOWED_TELEGRAM_USER_ID,
        text=format_alert(token, alert_type=trigger, alert_count=new_alert_count),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    log.info("Alert [%s] sent for %s (%s) alert#%d", trigger, token.symbol, token.address, new_alert_count)

    now_iso = datetime.now(timezone.utc).isoformat()

    if is_early:
        # Early warning: keep watching, just mark it sent.
        runtime.storage.update_watchlist_state(
            token.address,
            early_warned=True,
            prev_price=current_price,
        )
    elif cfg.cooldown_enabled and new_alert_count < cfg.max_alerts_per_token:
        # Cooldown mode: stay in watchlist, update counters.
        runtime.storage.update_watchlist_state(
            token.address,
            alert_count=new_alert_count,
            last_alerted_at=now_iso,
            prev_price=current_price,
        )
        runtime.storage.mark_alerted(token.address)  # prevent duplicate PUMP alerts
    else:
        # Single-shot or max alerts reached: retire.
        runtime.storage.mark_alerted(token.address)
        runtime.storage.remove_from_watchlist(token.address)


async def _process_watchlist(app: Application, cfg) -> None:
    for entry in runtime.storage.get_watchlist():
        try:
            await _process_one(app, entry, cfg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("Error processing token %s: %s", entry.get("address"), exc)


async def monitor_loop(app: Application):
    await asyncio.sleep(3)
    while True:
        cfg = runtime.storage.load_config()
        try:
            if not cfg.paused and runtime.scanner:
                await _discover_into_watchlist()
                await _process_watchlist(app, cfg)
            await asyncio.sleep(30)  # base tick; per-token intervals handled in _process_one
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("monitor loop error: %s", exc)
            await asyncio.sleep(30)


async def post_init(app: Application) -> None:
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
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    commands = {
        # core
        "start": start_cmd,
        "status": status_cmd,
        "toggle_age": toggle_age,
        "set_age": set_age,
        "set_pump": set_pump,
        "set_dev_share": set_dev_share,
        "set_top10_share": set_top10_share,
        "toggle_dev_share": toggle_dev,
        "toggle_top10_share": toggle_top10,
        "pause": pause_cmd,
        "resume": resume_cmd,
        # acceleration
        "toggle_accel": toggle_accel,
        "set_accel": set_accel,
        # early warning
        "toggle_early": toggle_early,
        "set_early": set_early,
        # cooldown
        "toggle_cooldown": toggle_cooldown,
        "set_cooldown": set_cooldown,
        # new high
        "toggle_new_high": toggle_new_high,
        # liq tiers
        "toggle_liq_tiers": toggle_liq_tiers,
        "set_liq_tiers": set_liq_tiers,
        # api keys
        "add_api_key": add_api_key,
        "list_api_keys": list_api_keys,
        "remove_api_key": remove_api_key,
    }
    for cmd, handler in commands.items():
        app.add_handler(CommandHandler(cmd, handler))
    return app


def main() -> None:
    build_application().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
