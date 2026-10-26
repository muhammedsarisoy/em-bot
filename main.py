"""EM seviye bildirim botu: Yahoo 1m fiyatlar -> dokunuş/kırılım -> Telegram."""
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

import chart
import feed
import fmp
import news
from engine import Engine
from levels import LEVELS, Symbol, fmt, parse_week, zone
from tg import Telegram

ROOT = Path(__file__).parent
WEEK_FILE = ROOT / "data" / "week.json"
NEWS_FILE = ROOT / "data" / "news_sent"  # son sabah haber bildiriminin tarihi
log = logging.getLogger("em-bot")

# Telegram'da '/' yazınca açılan menü
COMMANDS = [
    ("durum", "Pariteler EM içinde nerede · /durum NVDA: grafik"),
    ("haber", "Önümüzdeki 24 saatin kırmızı/turuncu haberleri"),
    ("seviye", "Bir paritenin tüm seviyeleri · /seviye NVDA"),
    ("sessiz", "Bildirimleri kapat · /sessiz 30 (dk)"),
    ("ac", "Bildirimleri aç"),
    ("test", "Saate bildirim düşüyor mu dene"),
    ("yardim", "Komut listesi"),
]

HELP = """EM bot komutları
Haftalık veri: sembol + PWC + EM listesini olduğu gibi yapıştır (komut gerekmez).
/durum - pariteler EM içinde nerede
/durum NVDA - bu haftanın 15m grafiği, EM seviyeleri çizili
/durum NVDA 5m - zaman dilimi seç: 5m, 15m, 1h
/haber - önümüzdeki 24 saatin kırmızı/turuncu haberleri (her hafta içi sabah da otomatik gelir)
/seviye NVDA - bir paritenin tüm seviyeleri
/sessiz 30 - 30 dk bildirim kapalı (varsayılan 60)
/ac - bildirimleri aç
/test - saate bildirim düşüyor mu dene"""

# /durum NVDA <tf> için kabul edilen yazımlar -> Yahoo aralığı
INTERVALS = {"5": "5m", "5m": "5m", "15": "15m", "15m": "15m", "60": "60m", "60m": "60m", "1h": "60m"}


@dataclass
class Photo:
    png: bytes
    caption: str


class Bot:
    def __init__(self):
        load_dotenv(ROOT / ".env")
        allowed = {int(x) for x in os.getenv("ALLOWED_CHAT_IDS", "").replace(" ", "").split(",") if x}
        self.tg = Telegram(os.environ["TELEGRAM_TOKEN"], allowed)
        self.poll = int(os.getenv("POLL_SECONDS", "30"))
        self.rearm = float(os.getenv("REARM_EM", "0.25"))
        self.swing_len = int(os.getenv("SWING_LEN", "2"))
        self.news_hour = int(os.getenv("NEWS_HOUR", "8"))
        self.news_currencies = {c.strip().upper() for c in os.getenv("NEWS_CURRENCIES", "USD,EUR").split(",") if c.strip()}
        self.news_before = int(os.getenv("NEWS_REMIND_MINUTES", "5"))
        self.news_retry_at = 0.0
        self.news_reminded: set[str] = set()
        self.fmp_key = os.getenv("FMP_API_KEY", "").strip()
        self.fmp_next = 0.0
        self.results_sent: set[str] = set()
        self.lock = threading.Lock()
        self.muted_until = 0.0
        self.set_at: datetime | None = None
        self.engine = Engine([], self.rearm)
        self.reminded = None
        self.failures = 0
        self.last_fetch = None
        if WEEK_FILE.exists():
            d = json.loads(WEEK_FILE.read_text(encoding="utf-8"))
            self.set_at = datetime.fromisoformat(d["set_at"])
            self.engine = Engine([Symbol(**s) for s in d["symbols"]], self.rearm, swing_len=self.swing_len)

    def notify(self, text: str) -> None:
        if time.time() < self.muted_until:
            log.info("sessizde: %s", text.replace("\n", " | "))
            return
        self.tg.broadcast(text)

    # ── fiyat döngüsü ─────────────────────────────────────
    def run(self) -> None:
        self.tg.set_commands(COMMANDS)
        threading.Thread(target=self.commands, daemon=True).start()
        log.info("başladı, %d parite", len(self.engine.symbols))
        self.notify(f"EM bot başladı · {len(self.engine.symbols)} parite")
        while True:
            self._morning_news()
            self._news_reminders()
            self._news_results()
            if feed.market_open():
                self.tick()
            time.sleep(self.poll)

    def _morning_news(self) -> None:
        """Hafta içi her sabah NEWS_HOUR'da (TR) önümüzdeki 24 saatin haberleri, günde bir kez.

        Sadece NEWS_HOUR'dan sonraki 2 saat içinde gönderilir; bot gün ortasında açılırsa
        o günün sabah mesajı geç gelmesin (gerekirse /haber var).
        """
        now = datetime.now(news.TR)
        in_window = self.news_hour <= now.hour < self.news_hour + 2
        if now.weekday() >= 5 or not in_window or time.time() < self.news_retry_at:
            return
        today = now.date().isoformat()
        if NEWS_FILE.exists() and NEWS_FILE.read_text().strip() == today:
            return
        try:
            msg = news.message(self.news_currencies, now.replace(hour=self.news_hour, minute=0, second=0, microsecond=0))
        except Exception as e:
            log.warning("haber takvimi alınamadı: %s", e)
            self.news_retry_at = time.time() + 300  # siteyi sıkıştırma, 5 dk sonra tekrar
            if now.hour < self.news_hour + 1:  # ilk saat boyunca sessizce tekrar dene
                return
            msg = "⚠️ ForexFactory haber takvimi alınamadı"
        NEWS_FILE.parent.mkdir(exist_ok=True)
        NEWS_FILE.write_text(today)
        log.info(msg.replace("\n", " | "))
        self.notify(msg)

    def _news_reminders(self) -> None:
        """Açıklanmasına NEWS_REMIND_MINUTES kalan kırmızı/turuncu haberler için hatırlatma."""
        if self.news_before <= 0:
            return
        now = datetime.now(news.TR)
        try:
            # +1 sn: tam N dakika kala olan haber de pencereye girsin (bitiş hariç)
            due = news.upcoming(self.news_currencies, now, timedelta(minutes=self.news_before, seconds=1))
        except Exception as e:
            log.debug("hatırlatma için takvim yok: %s", e)
            return
        new = [e for e in due if news.key(e) not in self.news_reminded]
        if not new:
            return
        self.news_reminded.update(news.key(e) for e in new)
        for at in sorted({e["at"] for e in new}):
            msg = news.reminder(self.news_currencies, [e for e in new if e["at"] == at])
            log.info(msg.replace("\n", " | "))
            self.notify(msg)

    def _news_results(self) -> None:
        """Açıklanan haberlerin sonucu (FMP). Haber saatinden sonra 30 dk boyunca dakikada bir bakılır."""
        if not self.fmp_key or time.time() < self.fmp_next:
            return
        now = datetime.now(news.TR)
        try:
            recent = news.upcoming(self.news_currencies, now - timedelta(minutes=30), timedelta(minutes=30))
        except Exception:
            return
        # Konuşma/basın toplantısı gibi rakamı olmayan haberlerin sonucu da olmaz
        pending = [e for e in recent if (e["forecast"] or e["previous"]) and news.key(e) not in self.results_sent]
        if not pending:
            return
        self.fmp_next = time.time() + 60  # günlük istek sınırı var
        fmp_events = []
        try:
            for day in sorted({e["at"].astimezone(timezone.utc).date() for e in pending}):
                fmp_events += fmp.fetch(self.fmp_key, day)
        except fmp.PlanError as e:
            # Plan yükseltilene kadar boşuna istek atma; bot yeniden başlayınca tekrar denenir
            self.fmp_key = ""
            log.warning("FMP planı ekonomik takvimi kapsamıyor, sonuç bildirimi kapatıldı: %s", e)
            self.notify("⚠️ FMP planın ekonomik takvimi kapsamıyor, haber sonuçları gönderilmeyecek")
            return
        except Exception as e:
            log.warning("FMP alınamadı: %s", e)
            return
        found = []
        for i, d in fmp.match_all(pending, fmp_events).items():
            if fmp.has_actual(d):
                found.append((pending[i], d))
                self.results_sent.add(news.key(pending[i]))
        for at in sorted({e["at"] for e, _ in found}):
            msg = fmp.result_message(self.news_currencies, [(e, d) for e, d in found if e["at"] == at])
            log.info(msg.replace("\n", " | "))
            self.notify(msg)

    def tick(self) -> None:
        with self.lock:
            engine = self.engine
        if not engine.symbols:
            return
        self._remind_if_stale()
        try:
            data = feed.fetch([s.ticker for s in engine.symbols.values()], period="2d")
            self.failures = 0
        except Exception as e:
            self.failures += 1
            log.warning("fiyat alınamadı (%d): %s", self.failures, e)
            if self.failures == 5:
                self.notify("⚠️ EM bot: 5 kez üst üste fiyat verisi alınamadı")
            return
        # Her poll'da değil, sadece gelen parite sayısı değişince logla (yeni hafta, eksik veri)
        got = (id(engine), len(data))
        if got != self.last_fetch:
            self.last_fetch = got
            log.info("fiyatlar geliyor: %d/%d parite", len(data), len(engine.symbols))
        msgs = []
        with self.lock:
            if engine is not self.engine:  # bu arada yeni hafta verisi geldi
                return
            for s in engine.symbols.values():
                if s.ticker in data:
                    msgs += engine.on_bars(s.name, data[s.ticker])
        for m in msgs:
            log.info(m.replace("\n", " | "))
            self.notify(m)

    def _remind_if_stale(self) -> None:
        today = datetime.now().date()
        if self.set_at and (datetime.now() - self.set_at).days >= 5 and self.reminded != today:
            self.reminded = today
            self.notify(f"⚠️ EM verisi eski ({self.set_at:%d.%m}). Bu haftanın verisini yapıştır.")

    # ── Telegram komutları ────────────────────────────────
    def commands(self) -> None:
        offset = 0
        while True:
            try:
                updates = self.tg.updates(offset)
            except Exception as e:
                log.warning("getUpdates: %s", e)
                time.sleep(5)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                chat, text = msg.get("chat", {}).get("id"), msg.get("text", "")
                if not chat or not text:
                    continue
                if chat not in self.tg.allowed:
                    # Kurulum kolaylığı: izinli liste boşken chat ID'yi söyle, yoksa yabancıları yok say
                    if not self.tg.allowed:
                        self.tg.send(chat, f"Chat ID: {chat}\n.env içinde ALLOWED_CHAT_IDS'e ekle, botu yeniden başlat.")
                    continue
                try:
                    reply = self.handle(text)
                except Exception as e:
                    log.exception("komut hatası")
                    reply = f"Hata: {e}"
                if isinstance(reply, Photo):
                    self.tg.send_photo(chat, reply.png, reply.caption)
                elif reply:
                    self.tg.send(chat, reply)

    def handle(self, text: str) -> str | Photo:
        parts = text.split()
        cmd, arg = parts[0].lower().split("@")[0], (parts[1] if len(parts) > 1 else "")
        if cmd in ("/start", "/yardim", "/help"):
            return HELP
        if cmd == "/durum":
            return self.chart(arg.upper(), parts[2].lower() if len(parts) > 2 else "15m") if arg else self.status()
        if cmd == "/haber":
            return news.message(self.news_currencies, datetime.now(news.TR))
        if cmd == "/seviye":
            return self.levels(arg.upper())
        if cmd == "/sessiz":
            mins = min(int(arg), 24 * 60) if arg.isdigit() else 60  # en fazla 1 gün
            self.muted_until = time.time() + mins * 60
            return f"🔕 {mins} dk sessiz"
        if cmd == "/ac":
            self.muted_until = 0
            return "🔔 Bildirimler açık"
        if cmd == "/test":
            return "🔴 TEST EM TOP 123.45 ↑\nShort ara · 5-15m kırılım bekle"
        symbols, missing = parse_week(text)
        if symbols:
            return self.set_week(symbols, missing)
        return "Anlamadım. /yardim"

    def set_week(self, symbols: list[Symbol], missing: list[str]) -> str:
        WEEK_FILE.parent.mkdir(exist_ok=True)
        set_at = datetime.now()
        WEEK_FILE.write_text(
            json.dumps({"set_at": set_at.isoformat(timespec="seconds"), "symbols": [vars(s) for s in symbols]}, indent=2),
            encoding="utf-8",
        )
        with self.lock:
            self.set_at = set_at
            self.engine = Engine(symbols, self.rearm, swing_len=self.swing_len)
        lines = [f"✅ {len(symbols)} parite kaydedildi"]
        lines += [f"{s.name}: TOP {fmt(s.level(1))} · PWC {fmt(s.pwc)} · BOT {fmt(s.level(-1))}" for s in symbols]
        if missing:
            lines.append(f"⚠️ Eksik (PWC/EM yok): {', '.join(missing)}")
        return "\n".join(lines)

    def status(self) -> str:
        with self.lock:
            engine, prices = self.engine, dict(self.engine.prices)
        if not engine.symbols:
            return "Haftalık veri yok. Sembol + PWC + EM listesini yapıştır."
        if len(prices) < len(engine.symbols):  # bot yeni açıldı veya piyasa kapalı
            data = feed.fetch([s.ticker for s in engine.symbols.values()], period="5d", interval="5m")
            for s in engine.symbols.values():
                if s.name not in prices and s.ticker in data:
                    prices[s.name] = float(data[s.ticker]["Close"].iloc[-1])
        rows = []
        for s in engine.symbols.values():
            if s.name in prices:
                z = s.z(prices[s.name])
                rows.append((z, f"{s.name} {fmt(prices[s.name])} · {z:+.2f} EM · {zone(z)}"))
            else:
                rows.append((-99, f"{s.name} fiyat yok"))
        head = f"Hafta verisi: {self.set_at:%d.%m}" if self.set_at else ""
        if time.time() < self.muted_until:
            head += f" · 🔕 {datetime.fromtimestamp(self.muted_until):%H:%M}'e kadar"
        return "\n".join([head] + [r for _, r in sorted(rows, reverse=True)])

    def chart(self, name: str, tf: str) -> str | Photo:
        s = self.engine.symbols.get(name)
        if not s:
            return f"Parite yok: {name}. Örnek: /durum NVDA"
        interval = INTERVALS.get(tf)
        if not interval:
            return "Zaman dilimi: 5m, 15m veya 1h. Örnek: /durum NVDA 5m"
        df = feed.fetch([s.ticker], period="5d", interval=interval).get(s.ticker)
        if df is None:
            return f"{name} için fiyat verisi alınamadı"
        # Bu haftanın mumları; hafta yeni başladıysa (az mum) son 5 gün
        monday = (datetime.now(feed.NY) - timedelta(days=datetime.now(feed.NY).weekday())).date()
        week = df[df.index.date >= monday]
        if len(week) >= 10:
            df = week
        z = s.z(float(df["Close"].iloc[-1]))
        caption = f"{s.name} {fmt(float(df['Close'].iloc[-1]))} · {z:+.2f} EM · {zone(z)}"
        return Photo(chart.render(s, df, interval.replace("60m", "1h")), caption)

    def levels(self, name: str) -> str:
        s = self.engine.symbols.get(name)
        if not s:
            return f"Parite yok: {name or '?'}. Örnek: /seviye NVDA"
        return f"{s.name} (EM {s.em})\n" + "\n".join(f"{label}: {fmt(s.level(k))}" for k, label, _ in LEVELS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    Bot().run()
