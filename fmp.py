"""Financial Modeling Prep ekonomik takvimi: açıklanan değerler (actual).

ForexFactory'nin JSON'unda açıklanan değer olmadığı için haber saati geçince
FMP'den çekilir ve FF haberiyle para birimi + saat + isim benzerliğine göre eşleştirilir.
"""
import difflib
import re
from datetime import date, datetime, timezone

import requests

from news import ICON

URLS = [
    "https://financialmodelingprep.com/stable/economic-calendar",
    "https://financialmodelingprep.com/api/v3/economic_calendar",
]
MAX_TIME_DIFF = 120  # sn: FF ve FMP saatleri arasında kabul edilen fark
MIN_SIMILARITY = 0.3  # kelime örtüşmesi (Jaccard)

# Kelime eşdeğerleri (normalize edilmiş, küçük harf)
WORDS = {"adv": "advance", "prel": "preliminary", "prelim": "preliminary", "flash": "preliminary",
         "m": "mom", "y": "yoy", "q": "qoq", "unemployment": "jobless", "initial": "jobless",
         "refinancing": "rate", "decision": "rate", "interest": "rate"}


class PlanError(RuntimeError):
    """Abonelik planı ekonomik takvimi kapsamıyor (ücretsiz plan: HTTP 402)."""


def fetch(api_key: str, day: date) -> list[dict]:
    """O günün (UTC) FMP takvimi. Hata mesajlarına URL konmaz: içinde API anahtarı var."""
    last, codes = "", set()
    for url in URLS:
        try:
            r = requests.get(url, params={"from": day.isoformat(), "to": day.isoformat(), "apikey": api_key}, timeout=20)
        except requests.RequestException as e:
            raise RuntimeError(f"FMP bağlantı hatası: {type(e).__name__}") from None
        if r.status_code == 200 and isinstance(r.json(), list):
            return r.json()
        codes.add(r.status_code)
        last = f"HTTP {r.status_code}: {r.text[:200]}"
    # 402: güncel adres planda yok, 403: eski adres sadece eski abonelere açık
    if codes <= {402, 403}:
        raise PlanError(last)
    raise RuntimeError(f"FMP: {last}")


def _tokens(name: str) -> set[str]:
    name = name.lower().replace("m/m", "mom").replace("y/y", "yoy").replace("q/q", "qoq")
    return {WORDS.get(w, w) for w in re.sub(r"[^a-z0-9]+", " ", name).split()}


def _score(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    jaccard = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    # eşit Jaccard'da karakter benzerliği ayırsın
    return jaccard + 0.01 * difflib.SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()


def _when(d: dict) -> datetime | None:
    try:
        return datetime.strptime(d["date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return None


def match_all(ff_events: list[dict], fmp_events: list[dict]) -> dict[int, dict]:
    """FF haberlerini (at: tz'li datetime) FMP kayıtlarıyla eşleştirir: {ff listesindeki sıra: FMP kaydı}.

    Aynı saatte birden çok haber olabildiği için en yüksek benzerlikten başlayarak
    açgözlü atama yapılır; her FMP kaydı en fazla bir habere gider.
    """
    pairs = []
    for i, ff in enumerate(ff_events):
        for j, d in enumerate(fmp_events):
            at = _when(d)
            if d.get("currency") != ff["country"] or at is None or abs((at - ff["at"]).total_seconds()) > MAX_TIME_DIFF:
                continue
            score = _score(ff["title"], d.get("event", ""))
            if score >= MIN_SIMILARITY:
                pairs.append((score, i, j))
    out, used = {}, set()
    for _, i, j in sorted(pairs, reverse=True):
        if i not in out and j not in used:
            out[i] = fmp_events[j]
            used.add(j)
    return out


def has_actual(d: dict) -> bool:
    return d.get("actual") not in (None, "")


_SCALE = {"": 1, "%": 1, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def _num(v, unit: str = "") -> float | None:
    """'198K' -> 198000, 0.4 + '%' -> 0.4"""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v) * _SCALE.get(unit.strip().upper(), 1)
    m = re.match(r"\s*(-?[\d.,]+)\s*([%KMBT]?)", str(v), re.I)
    if not m:
        return None
    return float(m.group(1).replace(",", "")) * _SCALE[m.group(2).upper()]


def _show(d: dict) -> str:
    unit = (d.get("unit") or "").strip()
    v = d["actual"]
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return f"{v}{unit if unit and unit not in str(v) else ''}"


def result_message(currencies: set[str], items: list[tuple[dict, dict]]) -> str:
    """items: (FF haberi, eşleşen FMP kaydı), hepsi aynı saatte."""
    lines = [f"📢 {items[0][0]['at']:%H:%M} sonuçlar"]
    for ff, d in sorted(items, key=lambda x: (x[0]["impact"] != "High", x[0]["title"])):  # kırmızılar üstte
        cc = "" if currencies == {ff["country"]} else f"{ff['country']} "
        actual, forecast = _num(d["actual"], d.get("unit") or ""), _num(ff["forecast"])
        if ff["forecast"]:
            ref = f"bkl {ff['forecast']}"
            arrow = "" if actual is None or forecast is None else \
                " ▲" if actual > forecast else " ▼" if actual < forecast else " ="
        else:
            ref, arrow = f"önc {ff['previous']}", ""
        lines.append(f"{ICON[ff['impact']]} {cc}{ff['title']}: {_show(d)} ({ref}){arrow}")
    return "\n".join(lines)
