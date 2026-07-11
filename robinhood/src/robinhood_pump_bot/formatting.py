from __future__ import annotations

from html import escape
from typing import Optional

from .models import TokenCandidate


def money(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    if value >= 1_000_000:
        return f"${value/1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value/1_000:.2f}K"
    return f"${value:.4g}"


def pct(value: Optional[float]) -> str:
    return "N/A" if value is None else f"{value:.2f}%"


def format_alert(token: TokenCandidate) -> str:
    m = token.metrics
    return "\n".join([
        f"🚀 <b>ТИКЕР:</b> {escape(token.symbol or 'UNKNOWN')}",
        f"⏱ <b>СКОЛЬКО ТОКЕН АКТИВЕН:</b> {token.age_minutes:.1f} мин",
        f"📈 <b>PUMP:</b> {pct(m.price_change_percent)}",
        f"💰 <b>МАРКЕТ КАП:</b> {money(m.market_cap_usd)}",
        f"💧 <b>ЛИКВИДНОСТЬ:</b> {money(m.liquidity_usd)}",
        f"👨‍💻 <b>КОЛ-ВО ТОКЕНОВ У РАЗРАБА:</b> {pct(m.developer_share_percent)}",
        f"🐳 <b>ТОП-10 КОШЕЛЬКОВ:</b> {pct(m.top10_share_percent)}",
        "",
        f"📄 <b>КОНТРАКТ ТОКЕНА:</b> <code>{escape(token.address)}</code>",
        f"🔗 <b>ССЫЛКА НА ДЕКСЕ НА ЭТОТ ТОКЕН:</b> {escape(token.dex_url or 'N/A')}",
    ])
