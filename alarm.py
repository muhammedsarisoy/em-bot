"""Fiyat alarmları: /alarm NVDA 185.5 -> fiyat o seviyeye gelince bildirim.

Yön sorulmaz: alarm kurulduktan sonraki 1m mumlardan biri (önceki kapanış dahil,
gap'ler de sayılır) seviyeyi içerirse tetiklenir, yani yukarıdan da aşağıdan da
gelse çalışır. Tetiklenen alarm silinir. Alarmlar data/alarms.json'da durur,
bot yeniden başlasa da kaybolmaz.
"""
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from levels import YAHOO, fmt

MAX_ALARMS = 30
NAME = re.compile(r"[\w^.=\-]{1,15}")


@dataclass
class Alarm:
    name: str
    price: float
    created: str               # ISO, UTC
    note: str = ""
    ref: float | None = None   # kurulduğu andaki fiyat (yön oku için)

    @property
    def ticker(self) -> str:
        return Alarms.ticker_of(self.name)

    @property
    def arrow(self) -> str:
        if self.ref is None:
            return ""
        return " ↑" if self.ref < self.price else " ↓"

    def line(self) -> str:
        return f"{self.name} {fmt(self.price)}{self.arrow}" + (f" · {self.note}" if self.note else "")


def parse_price(s: str) -> float | None:
    """'185.5', '185,5', '1,234.5', '4.250,75' gibi yazımları okur."""
    s = s.strip()
    if "," in s and "." in s:
        # Hangisi sondaysa ondalık ayracı odur
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


class Alarms:
    def __init__(self, path: Path):
        self.path = path
        self.items: list[Alarm] = []
        if path.exists():
            self.items = [Alarm(**a) for a in json.loads(path.read_text(encoding="utf-8"))]

    def save(self) -> None:
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text(json.dumps([asdict(a) for a in self.items], indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def ticker_of(name: str) -> str:
        return YAHOO.get(name, name)

    def tickers(self) -> list[str]:
        return list(dict.fromkeys(a.ticker for a in self.items))

    def add(self, name: str, price: float, ref: float | None, note: str = "") -> Alarm:
        a = Alarm(name, price, datetime.now(timezone.utc).isoformat(timespec="seconds"), note, ref)
        self.items.append(a)
        self.save()
        return a

    def remove(self, arg: str) -> list[Alarm]:
        """'hepsi' -> tümü, '2' -> listedeki 2. alarm, 'NVDA' -> NVDA'nın tüm alarmları."""
        arg = arg.strip().upper()
        if arg in ("HEPSI", "HEPSİ", "TUMU", "TÜMÜ", "ALL"):
            gone, self.items = self.items, []
        elif arg.isdigit() and 1 <= int(arg) <= len(self.items):
            gone = [self.items.pop(int(arg) - 1)]
        else:
            gone = [a for a in self.items if a.name == arg]
            self.items = [a for a in self.items if a.name != arg]
        if gone:
            self.save()
        return gone

    def listing(self) -> str:
        if not self.items:
            return "Kurulu alarm yok.\nÖrnek: /alarm NVDA 185.5"
        return "⏰ Alarmlar\n" + "\n".join(f"{i}. {a.line()}" for i, a in enumerate(self.items, 1))

    def check(self, data: dict[str, pd.DataFrame]) -> list[str]:
        """data: ticker -> 1m mumlar. Tetiklenen alarmların mesajları; tetiklenenler silinir."""
        msgs, keep = [], []
        for a in self.items:
            df = data.get(a.ticker)
            hit = None if df is None or df.empty else self._hit(a, df)
            if hit is None:
                keep.append(a)
                continue
            # Ok, fiyatın seviyeye gerçekte geldiği yön (kurulduğundan beri geçmiş olabilir)
            arrow = " ↑" if hit < a.price else " ↓"
            last = float(df["Close"].iloc[-1])
            msg = f"⏰ ALARM {a.name} {fmt(a.price)}{arrow}\nŞu an {fmt(last)}"
            msgs.append(msg + (f"\n{a.note}" if a.note else ""))
        if len(keep) != len(self.items):
            self.items = keep
            self.save()
        return msgs

    @staticmethod
    def _hit(a: Alarm, df: pd.DataFrame) -> float | None:
        """Seviyeye ilk değen mumun önceki kapanışı (yön için), değmediyse None."""
        prevs = df["Close"].shift(1).fillna(df["Open"])
        after = df.index > pd.Timestamp(a.created)
        hi = df["High"].where(df["High"] > prevs, prevs)[after]
        lo = df["Low"].where(df["Low"] < prevs, prevs)[after]
        touched = (lo <= a.price) & (a.price <= hi)
        if not touched.any():
            return None
        return float(prevs[after][touched].iloc[0])
