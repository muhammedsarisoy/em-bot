"""Paritenin mum grafiği + EM seviyeleri, PNG olarak."""
import io

import matplotlib

matplotlib.use("Agg")  # ekransız sunucu
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from levels import LEVELS, Symbol, fmt, zone  # noqa: E402

BG, FG, GRID = "#131722", "#d1d4dc", "#2a2e39"
UP, DOWN = "#26a69a", "#ef5350"
DAYS = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]


def _style(k: float, kind: str) -> tuple[str, float, str]:
    """(renk, kalınlık, çizgi tipi)"""
    if kind == "pwc":
        return "#ff9800", 1.4, "-"
    if kind == "quad":
        return "#787b86", 0.9, "--"
    return (DOWN if k > 0 else UP), 1.6 if abs(k) == 1 else 1.2, "-"


def render(sym: Symbol, df: pd.DataFrame, tf: str) -> bytes:
    # Gece boşlukları görünmesin diye x ekseni zaman değil mum sırası
    df = df.reset_index(names="ts")
    x = range(len(df))
    last = float(df["Close"].iloc[-1])

    fig, ax = plt.subplots(figsize=(12, 6.5), dpi=110)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    colors = [UP if c >= o else DOWN for o, c in zip(df["Open"], df["Close"])]
    ax.vlines(x, df["Low"], df["High"], color=colors, linewidth=0.8)
    lo, hi = float(df["Low"].min()), float(df["High"].max())
    body = (df["Close"] - df["Open"]).abs().clip(lower=(hi - lo) * 0.002)  # doji de görünsün
    ax.bar(x, body, bottom=df[["Open", "Close"]].min(axis=1), width=0.7, color=colors, linewidth=0)

    # Fiyat aralığındaki seviyeler + iki yandaki ilk seviye
    above = [k for k, _, _ in LEVELS if sym.level(k) > hi]
    below = [k for k, _, _ in LEVELS if sym.level(k) < lo]
    keep = {k for k, _, _ in LEVELS if lo <= sym.level(k) <= hi}
    if above:
        keep.add(min(above))
    if below:
        keep.add(max(below))

    # Sağda boşluk: mumların hemen yanında fiyat etiketi, en sağda seviye etiketleri
    xmax = len(df) * 1.28 + 2
    for k, label, kind in LEVELS:
        if k not in keep:
            continue
        lvl = sym.level(k)
        color, width, ls = _style(k, kind)
        ax.axhline(lvl, color=color, linewidth=width, linestyle=ls, alpha=0.9)
        ax.text(xmax, lvl, f"{label}  {fmt(lvl)}", color=color, va="center", ha="right", fontsize=9,
                bbox={"facecolor": BG, "edgecolor": "none", "pad": 1.5})

    ax.axhline(last, color=FG, linewidth=0.6, linestyle=":")
    ax.text(len(df) + 0.5, last, fmt(last), color=BG, va="center", fontsize=9,
            bbox={"facecolor": FG, "edgecolor": "none", "pad": 1.5})

    # Önceki haftanın mumları da varsa bu haftanın başlangıcını işaretle
    monday = (df["ts"].iloc[-1] - pd.Timedelta(days=df["ts"].iloc[-1].weekday())).date()
    this_week = (df["ts"].dt.date >= monday).to_numpy()
    if this_week.any() and not this_week.all():
        start = int(this_week.argmax())
        ax.axvline(start - 0.5, color=FG, linewidth=0.8, linestyle="--", alpha=0.5)
        ax.text(start, 1, " bu hafta", color=FG, alpha=0.7, fontsize=9, va="top",
                transform=ax.get_xaxis_transform())

    levels = [sym.level(k) for k in keep] + [lo, hi]
    span = max(levels) - min(levels)
    ax.set_ylim(min(levels) - span * 0.04, max(levels) + span * 0.04)
    ax.set_xlim(-1, xmax)

    # x ekseni: her günün ilk mumu
    days = df["ts"].dt.date
    starts = [i for i in range(len(df)) if i == 0 or days.iloc[i] != days.iloc[i - 1]]
    ax.set_xticks(starts)
    ax.set_xticklabels([f"{DAYS[df['ts'].iloc[i].weekday()]} {df['ts'].iloc[i].day}" for i in starts])

    z = sym.z(last)
    ax.set_title(f"{sym.name} · {tf} · {fmt(last)} · {z:+.2f} EM · {zone(z)}", color=FG, loc="left", fontsize=12)
    ax.grid(color=GRID, linewidth=0.5)
    ax.tick_params(colors=FG, labelsize=9)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.yaxis.set_visible(False)  # fiyatlar sağdaki seviye etiketlerinde yazıyor

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
