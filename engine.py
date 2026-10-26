"""Dokunuş ve kırılım (CHoCH / BOS) takibi.

Dokunuş: 1m mumun aralığı (önceki kapanış dahil, gap'ler de sayılır) seviyeyi
içeriyorsa. Aynı seviye için tekrar bildirim, fiyat seviyeden REARM_EM * EM
uzaklaştıktan sonra gelir.

Kırılım (sadece rev seviyeleri), dokunuştan sonra 5m ve 15m mumlarda ayrı ayrı:
  Swing tepe/dip: iki yanındaki swing_len mumdan yüksek/düşük olan mum (pivot).
  TOP tarafı (short):
    CHoCH : mum gövdesi son swing low'un altında kapanır      -> erken teyit
    BOS   : CHoCH'tan sonra oluşan swing low'un altında kapanış -> güçlü teyit
    İptal : CHoCH'tan sonra dokunuştan beri görülen tepenin üstünde kapanış
            -> tekrar CHoCH beklenir
  BOTTOM tarafı (long): tam tersi.
"""
from dataclasses import dataclass, field

import pandas as pd

from levels import LABEL, LEVELS, Symbol, fmt, target


@dataclass
class _Setup:
    touched: pd.Timestamp
    choch: pd.Timestamp | None = None  # None: CHoCH bekleniyor
    extreme: float | None = None       # dokunuştan beri en uç fiyat (TOP: tepe, BOTTOM: dip)


@dataclass
class _State:
    last_bar: pd.Timestamp
    last_close: float
    armed: dict[float, bool]
    touched_at: dict[float, pd.Timestamp] = field(default_factory=dict)
    pending: dict[tuple[float, int], _Setup] = field(default_factory=dict)
    last_candle: dict[int, pd.Timestamp] = field(default_factory=dict)


class Engine:
    def __init__(self, symbols: list[Symbol], rearm_em: float = 0.25, tfs: tuple[int, ...] = (5, 15),
                 swing_len: int = 2):
        self.symbols = {s.name: s for s in symbols}
        self.rearm_em = rearm_em
        self.tfs = tfs
        self.swing_len = swing_len
        self.states: dict[str, _State] = {}
        self.prices: dict[str, float] = {}

    def on_bars(self, name: str, df: pd.DataFrame) -> list[str]:
        """df: son günlerin 1m mumları (son satır henüz kapanmamış olabilir)."""
        if df.empty:
            return []
        sym = self.symbols[name]
        self.prices[name] = float(df["Close"].iloc[-1])
        st = self.states.get(name)
        if st is None:
            self._warm_up(sym, df)
            return []

        msgs: list[str] = []
        prevs = df["Close"].shift(1)
        new_day = df.index[0] > st.last_bar
        prevs.iloc[0] = st.last_close if new_day else df["Open"].iloc[0]
        rearm = self.rearm_em * sym.em

        for ts in df.index[df.index >= st.last_bar]:
            h, l, c = df.at[ts, "High"], df.at[ts, "Low"], df.at[ts, "Close"]
            prev = prevs.at[ts]
            hi, lo = max(h, prev), min(l, prev)
            for k, label, kind in LEVELS:
                lvl = sym.level(k)
                if st.armed[k] and lo <= lvl <= hi and st.touched_at.get(k) != ts:
                    st.armed[k] = False
                    st.touched_at[k] = ts
                    msgs.append(self._touch_msg(sym, k, label, kind, lvl, prev))
                    if kind == "rev":
                        self._arm_break(st, k, ts)
                elif not st.armed[k] and abs(c - lvl) > rearm:
                    st.armed[k] = True

        st.last_bar = df.index[-1]
        st.last_close = float(df["Close"].iloc[-1])
        msgs += self._check_breaks(sym, st, df)
        return msgs

    def _warm_up(self, sym: Symbol, df: pd.DataFrame) -> None:
        # Başlangıçta geçmiş mumlar için bildirim atma; fiyata yakın seviyeleri kapalı başlat.
        price = float(df["Close"].iloc[-1])
        st = _State(
            last_bar=df.index[-1],
            last_close=price,
            armed={k: abs(price - sym.level(k)) > self.rearm_em * sym.em for k, _, _ in LEVELS},
        )
        for tf in self.tfs:
            closed = self._closed_candles(df, tf)
            if not closed.empty:
                st.last_candle[tf] = closed.index[-1]
        self.states[sym.name] = st

    def _arm_break(self, st: _State, k: float, ts: pd.Timestamp) -> None:
        # Daha dıştaki seviyeye ulaşıldıysa aynı taraftaki içteki seviyenin takibi biter.
        for key in [key for key in st.pending if key[0] * k > 0 and abs(key[0]) < abs(k)]:
            del st.pending[key]
        for tf in self.tfs:
            st.pending[(k, tf)] = _Setup(touched=ts)

    @staticmethod
    def _closed_candles(df: pd.DataFrame, tf: int) -> pd.DataFrame:
        candles = (
            df.resample(f"{tf}min", label="left", closed="left")
            .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
            .dropna()
        )
        # Son 1m mum açık; bitişi o mumun başlangıcına <= olan mumlar kapanmıştır.
        return candles[candles.index + pd.Timedelta(minutes=tf) <= df.index[-1]]

    def _swings(self, candles: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        # Pivot: kendisi dahil 2*swing_len+1 mumluk pencerenin tepesi/dibi.
        # Sağında swing_len mum kapanmadan NaN kalır, yani sadece onaylı swing'ler işaretlenir.
        w = 2 * self.swing_len + 1
        highs = candles["High"].rolling(w, center=True).max() == candles["High"]
        lows = candles["Low"].rolling(w, center=True).min() == candles["Low"]
        return highs, lows

    def _check_breaks(self, sym: Symbol, st: _State, df: pd.DataFrame) -> list[str]:
        msgs = []
        for tf in self.tfs:
            closed = self._closed_candles(df, tf)
            if closed.empty:
                continue
            if any(key[1] == tf for key in st.pending):
                swing_hi, swing_lo = self._swings(closed)
                last = st.last_candle.get(tf)
                for i in range(len(closed)):
                    if last is None or closed.index[i] > last:
                        msgs += self._step(sym, st, tf, closed, swing_hi, swing_lo, i)
            st.last_candle[tf] = closed.index[-1]
        return msgs

    def _step(self, sym, st, tf, closed, swing_hi, swing_lo, i) -> list[str]:
        """Kapanan i. mumu bu zaman dilimindeki açık kurulumlara uygula."""
        msgs = []
        start = closed.index[i]
        cdl = closed.iloc[i]
        # i. mum kapandığında onaylı olan swing'ler: sağında swing_len mum kapanmış olanlar
        confirmed = closed.index[: max(i - self.swing_len + 1, 0)]
        for (k, ktf), s in list(st.pending.items()):
            if ktf != tf or start + pd.Timedelta(minutes=tf) <= s.touched:
                continue
            short = k > 0
            lvl = sym.level(k)
            swings = swing_lo if short else swing_hi
            col = "Low" if short else "High"
            beyond = (lambda a, b: a < b) if short else (lambda a, b: a > b)
            pts = [t for t in confirmed if swings.at[t]]

            if s.choch is None:
                ref = pts[-1] if pts else None
                if ref is not None and beyond(cdl["Close"], closed.at[ref, col]):
                    s.choch = start
                    msgs.append(self._choch_msg(sym, k, tf, closed.at[ref, col]))
            else:
                after = [t for t in pts if t >= s.choch]
                if after and beyond(cdl["Close"], closed.at[after[-1], col]):
                    msgs.append(self._bos_msg(sym, k, tf, closed.at[after[-1], col]))
                    del st.pending[(k, tf)]
                    continue
                # yeni tepe (short) / yeni dip (long) üstünde/altında kapanış -> yapı bozuldu
                if s.extreme is not None and beyond(s.extreme, cdl["Close"]):
                    s.choch = None
                    msgs.append(f"❌ {sym.name} {LABEL[k]} {tf}m kurulum iptal\nYeni {'tepe' if short else 'dip'} {fmt(cdl['Close'])}")
            # dokunuştan beri en uç nokta (iptal kontrolü bir sonraki mumdan itibaren bunu kullanır)
            ext = cdl["High"] if short else cdl["Low"]
            s.extreme = ext if s.extreme is None else (max(s.extreme, ext) if short else min(s.extreme, ext))
        return msgs

    @staticmethod
    def _touch_msg(sym: Symbol, k: float, label: str, kind: str, lvl: float, prev: float) -> str:
        arrow = "↑" if prev < lvl else "↓"
        head = f"{sym.name} {label} {fmt(lvl)} {arrow}"
        if kind == "rev":
            side = "Short ara" if k > 0 else "Long ara"
            note = "5-15m CHoCH bekle" if abs(k) == 1 else "güçlü tepki bölgesi"
            return f"{'🔴' if k > 0 else '🟢'} {head}\n{side} · {note}"
        if kind == "quad":
            return f"🎯 {head}\nQuadrant · kâr al bölgesi"
        return f"⚪ {head}\nHaftalık pivot"

    @staticmethod
    def _target(sym: Symbol, k: float) -> str:
        t = target(k)
        return f"hedef {LABEL[t]} {fmt(sym.level(t))}"

    def _choch_msg(self, sym: Symbol, k: float, tf: int, ref: float) -> str:
        side, where = ("SHORT", "swing low altı") if k > 0 else ("LONG", "swing high üstü")
        return f"⚡ {sym.name} CHoCH {tf}m {side}\n{LABEL[k]} · {where} {fmt(ref)} · {self._target(sym, k)}"

    def _bos_msg(self, sym: Symbol, k: float, tf: int, ref: float) -> str:
        side = "SHORT" if k > 0 else "LONG"
        return f"✅ {sym.name} BOS {tf}m {side} TEYİT\n{LABEL[k]} · kırılan {fmt(ref)} · {self._target(sym, k)}"
