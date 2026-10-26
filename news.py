"""ForexFactory ekonomik takvimi: kırmızı (High) ve turuncu (Medium) haberler.

Kaynak, ForexFactory'nin haftalık JSON dışa aktarımı. Sık istek atılırsa
engelliyor; bu yüzden takvim 30 dakika önbellekte tutulur.
"""
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
TR = ZoneInfo("Europe/Istanbul")
ICON = {"High": "🔴", "Medium": "🟠"}
CACHE_SECONDS = 30 * 60

_cache: tuple[float, list[dict]] = (0.0, [])


def _week() -> list[dict]:
    global _cache
    if time.time() - _cache[0] > CACHE_SECONDS:
        r = requests.get(URL, timeout=20, headers={"User-Agent": "em-bot"})
        r.raise_for_status()
        _cache = (time.time(), r.json())
    return _cache[1]


def upcoming(currencies: set[str], start: datetime, hours: int = 24) -> list[dict]:
    """start'tan itibaren hours saat içindeki kırmızı/turuncu haberler (TR saatine çevrilmiş)."""
    end = start + timedelta(hours=hours)
    out = []
    for e in _week():
        if e["impact"] not in ICON or ("ALL" not in currencies and e["country"] not in currencies):
            continue
        at = datetime.fromisoformat(e["date"]).astimezone(TR)
        if start <= at < end:
            out.append({**e, "at": at})
    return sorted(out, key=lambda e: e["at"])


def message(currencies: set[str], start: datetime) -> str:
    events = upcoming(currencies, start)
    cur = "tüm pariteler" if "ALL" in currencies else ", ".join(sorted(currencies))
    head = f"📅 {start:%d.%m} haberleri ({cur})"
    if not events:
        return f"{head}\nKırmızı/turuncu haber yok"
    lines = [head]
    for e in events:
        when = e["at"].strftime("%H:%M")
        if e["at"].date() != start.date():
            when = ("gece " if e["at"].hour < 8 else "yarın ") + when
        extra = " · ".join(x for x in (f"bkl {e['forecast']}" if e["forecast"] else "",
                                         f"önc {e['previous']}" if e["previous"] else "") if x)
        cc = "" if currencies == {e["country"]} else f"{e['country']} "
        lines.append(f"{ICON[e['impact']]} {when} {cc}{e['title']}" + (f" · {extra}" if extra else ""))
    return "\n".join(lines)
