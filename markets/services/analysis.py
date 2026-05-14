"""Lightweight summaries on Binance-derived rows (no historical tick DB yet)."""


def movers_by_daily_change_pct(
    rows: list[dict],
    *,
    min_abs_change_pct: float,
    max_results: int = 80,
) -> list[dict]:
    """
    Filter rows where |24h % change| >= `min_abs_change_pct`, sort by magnitude desc.
    """
    if min_abs_change_pct < 0:
        raise ValueError("min_abs_change_pct must be non-negative")
    picked: list[tuple[float, dict]] = []
    for r in rows:
        try:
            p = float(r.get("price_change_percent", 0) or 0)
        except (TypeError, ValueError):
            continue
        ap = abs(p)
        if ap >= min_abs_change_pct:
            picked.append((ap, r))
    picked.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in picked[:max_results]]
