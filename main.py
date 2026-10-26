"""EM seviye bildirim botu: Yahoo 1m fiyatlar -> dokunuş/kırılım -> Telegram."""
import json
import logging
import math
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

import feed
from engine import Engine
from levels import LABEL, LEVELS, Symbol, fmt, parse_week
from tg import Telegram

ROOT = Path(__file__).parent
WEEK_FILE = ROOT / "data" / "week.json"
log = logging.getLogger("em-bot")

HELP = """EM bot komutları
Haftalık veri: sembol + PWC + EM listesini olduğu gibi yapıştır (komut gerekmez).
/durum  - pariteler EM içinde nerede
/seviye NVDA - bir paritenin tüm seviyeleri
/sessiz 30 - 30 dk bildirim kapalı (varsayılan 60)
/ac - bildirimleri aç
/test - saate bildirim düşüyor mu dene"""


class Bot:
    def __init__(self):
        load_dotenv(ROOT / ".env")
        allowed = {int(x) for x in os.getenv("ALLOWED_CHAT_IDS", "").replace(" ", "").split(",") if x}
        self.tg = Telegram(os.environ["TELEGRAM_TOKEN"], allowed)
        self.poll = int(os.getenv("POLL_SECONDS", "30"))
        self.rearm = float(os.getenv("REARM_EM", "0.25"))
        self.swing_len = int(os.getenv("SWING_LEN", "2"))
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
        threading.Thread(target=self.commands, daemon=True).start()
        log.info("başladı, %d parite", len(self.engine.symbols))
        self.notify(f"EM bot başladı · {len(self.engine.symbols)} parite")
        while True:
            if feed.market_open():
                self.tick()
            time.sleep(self.poll)

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
                if reply:
                    self.tg.send(chat, reply)

    def handle(self, text: str) -> str:
        parts = text.split()
        cmd, arg = parts[0].lower().split("@")[0], (parts[1] if len(parts) > 1 else "")
        if cmd in ("/start", "/yardim", "/help"):
            return HELP
        if cmd == "/durum":
            return self.status()
        if cmd == "/seviye":
            return self.levels(arg.upper())
        if cmd == "/sessiz":
            mins = int(arg) if arg.isdigit() else 60
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
                if z >= 3:
                    zone = "3X TOP üstü"
                elif z < -3:
                    zone = "3X BOTTOM altı"
                else:
                    lo = math.floor(z * 2) / 2
                    zone = f"{LABEL[lo]}↔{LABEL[lo + 0.5]}"
                rows.append((z, f"{s.name} {fmt(prices[s.name])} · {z:+.2f} EM · {zone}"))
            else:
                rows.append((-99, f"{s.name} fiyat yok"))
        head = f"Hafta verisi: {self.set_at:%d.%m}" if self.set_at else ""
        if time.time() < self.muted_until:
            head += f" · 🔕 {datetime.fromtimestamp(self.muted_until):%H:%M}'e kadar"
        return "\n".join([head] + [r for _, r in sorted(rows, reverse=True)])

    def levels(self, name: str) -> str:
        s = self.engine.symbols.get(name)
        if not s:
            return f"Parite yok: {name or '?'}. Örnek: /seviye NVDA"
        return f"{s.name} (EM {s.em})\n" + "\n".join(f"{label}: {fmt(s.level(k))}" for k, label, _ in LEVELS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    Bot().run()
