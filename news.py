"""ForexFactory ekonomik takvimi: kırmızı (High) ve turuncu (Medium) haberler.

Kaynak, ForexFactory'nin haftalık JSON dışa aktarımı (beklenti ve önceki değer var,
açıklanan değer yok). Sık istek atılırsa engelliyor (HTTP 429); bu yüzden takvim
30 dakika önbellekte tutulur, hata olursa 5 dakika beklenir ve eldeki veriyle devam edilir.
"""
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
TR = ZoneInfo("Europe/Istanbul")
ICON = {"High": "🔴", "Medium": "🟠"}
CACHE_SECONDS = 30 * 60
RETRY_SECONDS = 5 * 60

_cache: tuple[float, list[dict]] = (0.0, [])
_retry_at = 0.0


def _week() -> list[dict]:
    global _cache, _retry_at
    fresh = time.time() - _cache[0] < CACHE_SECONDS
    if fresh or time.time() < _retry_at:
        if fresh or _cache[1]:
            return _cache[1]
        raise RuntimeError("takvim henüz alınamadı, tekrar denenecek")
    try:
        r = requests.get(URL, timeout=20, headers={"User-Agent": "em-bot"})
        r.raise_for_status()
        _cache = (time.time(), r.json())
    except Exception:
        _retry_at = time.time() + RETRY_SECONDS
        if not _cache[1]:
            raise
    return _cache[1]


def key(e: dict) -> str:
    return f"{e['date']}|{e['country']}|{e['title']}"


def upcoming(currencies: set[str], start: datetime, span: timedelta = timedelta(hours=24)) -> list[dict]:
    """start'tan itibaren span içindeki kırmızı/turuncu haberler (TR saatine çevrilmiş)."""
    end = start + span
    out = []
    for e in _week():
        if e["impact"] not in ICON or ("ALL" not in currencies and e["country"] not in currencies):
            continue
        at = datetime.fromisoformat(e["date"]).astimezone(TR)
        if start <= at < end:
            out.append({**e, "at": at})
    return sorted(out, key=lambda e: e["at"])


def _label(currencies: set[str]) -> str:
    return "tüm pariteler" if "ALL" in currencies else ", ".join(sorted(currencies))


def _line(e: dict, currencies: set[str], when: str) -> str:
    extra = " · ".join(x for x in (f"bkl {e['forecast']}" if e["forecast"] else "",
                                     f"önc {e['previous']}" if e["previous"] else "") if x)
    cc = "" if currencies == {e["country"]} else f"{e['country']} "
    return f"{ICON[e['impact']]} {when}{cc}{e['title']}" + (f" · {extra}" if extra else "")


def message(currencies: set[str], start: datetime) -> str:
    """Sabah bülteni / /haber: önümüzdeki 24 saat."""
    events = upcoming(currencies, start)
    head = f"📅 {start:%d.%m} haberleri ({_label(currencies)})"
    if not events:
        return f"{head}\nKırmızı/turuncu haber yok"
    lines = [head]
    for e in events:
        when = e["at"].strftime("%H:%M")
        if e["at"].date() != start.date():
            when = ("gece " if e["at"].hour < 8 else "yarın ") + when
        lines.append(_line(e, currencies, when + " "))
    return "\n".join(lines)


def reminder(currencies: set[str], events: list[dict]) -> str:
    """Açıklanmasına birkaç dakika kalan haberler (aynı saattekiler tek mesajda)."""
    mins = max(1, round((events[0]["at"] - datetime.now(TR)).total_seconds() / 60))
    head = f"⏰ {mins} dk sonra · {events[0]['at']:%H:%M}"
    return "\n".join([head] + [_line(e, currencies, "") for e in events])
