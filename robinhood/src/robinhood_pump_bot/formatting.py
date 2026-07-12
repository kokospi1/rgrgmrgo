from __future__ import annotations

from html import escape
from typing import Optional

from .filters import ALERT_ACCELERATION, ALERT_EARLY, ALERT_NEW_HIGH, ALERT_PUMP
from .models import TokenCandidate

_HEADERS = {
    ALERT_PUMP:         "🚀 PUMP",
    ALERT_ACCELERATION: "⚡ ACCELERATION",
    ALERT_EARLY:        "👀 WATCH",
    ALERT_NEW_HIGH:     "📈 NEW HIGH",
}


def money(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    if value >= 1_000_000:
        return f"${value/1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value/1_000:.2f}K"
    return f"${value:.4g}"


def pct(value: Optional[float]) -> str:
    return "N/A" if value is None else f"{value:+.2f}%"


def format_alert(
    token: TokenCandidate,
    alert_type: str = ALERT_PUMP,
    alert_count: int = 1,
) -> str:
    m = token.metrics
    header = _HEADERS.get(alert_type, alert_type)

    # Wave suffix for cooldown multi-alerts.
    wave = f" (волна {alert_count})" if alert_count > 1 else ""

    # Choose which pump figure to highlight in the header line.
    if alert_type == ALERT_ACCELERATION and m.acceleration_percent is not None:
        pump_line = f"⚡ <b>УСКОРЕНИЕ (последние свечи):</b> {pct(m.acceleration_percent)}"
    elif alert_type == ALERT_EARLY:
        pump_line = f"👀 <b>РАННИЙ СИГНАЛ:</b> {pct(m.price_change_percent)} от старта"
    elif alert_type == ALERT_NEW_HIGH:
        pump_line = f"📈 <b>НОВЫЙ МАКСИМУМ:</b> {pct(m.price_change_percent)} от старта"
    else:
        pump_line = f"📈 <b>PUMP:</b> {pct(m.price_change_percent)} от старта"

    lines = [
        f"<b>[{header}]{wave}</b>",
        "",
        f"🪙 <b>ТИКЕР:</b> {escape(token.symbol or 'UNKNOWN')}",
        f"⏱ <b>АКТИВЕН:</b> {token.age_minutes:.1f} мин",
        pump_line,
    ]

    # Show acceleration separately when it's available but not the primary trigger.
    if alert_type != ALERT_ACCELERATION and m.acceleration_percent is not None:
        lines.append(f"⚡ <b>УСКОРЕНИЕ:</b> {pct(m.acceleration_percent)}")

    lines += [
        f"💰 <b>МАРКЕТ КАП:</b> {money(m.market_cap_usd)}",
        f"💧 <b>ЛИКВИДНОСТЬ:</b> {money(m.liquidity_usd)}",
        f"👨‍💻 <b>РАЗРАБ:</b> {pct(m.developer_share_percent)}",
        f"🐳 <b>ТОП-10:</b> {pct(m.top10_share_percent)}",
        "",
        f"📄 <b>КОНТРАКТ:</b> <code>{escape(token.address)}</code>",
        f"🔗 <b>DEX:</b> {escape(token.dex_url or 'N/A')}",
    ]
    return "\n".join(lines)
