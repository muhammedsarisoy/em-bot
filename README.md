# EM Bot

Haftalık Expected Move seviyelerinde Telegram bildirimi (oradan Xiaomi Smart Band'e).

## Seviyeler ve bildirimler

| Seviye | Bildirim |
|---|---|
| EM TOP / BOTTOM (±1) | Dokunuşta: short/long ara. Sonra 5m ve 15m'de CHoCH ve BOS. |
| 2X, 3X (±2, ±3) | Aynı, "güçlü tepki bölgesi" notuyla |
| Quadrant (±0.5, ±1.5, ±2.5) | Dokunuşta: kâr al bölgesi |
| PWC | Dokunuşta: haftalık pivot |

**Kırılım: CHoCH ve BOS** (dokunuştan sonra, 5m ve 15m'de ayrı ayrı)

Swing tepe/dip: iki yanındaki `SWING_LEN` (varsayılan 2) mumdan yüksek/düşük olan mum. Sağındaki mumlar kapanınca onaylanır.

TOP tarafı (short). BOTTOM tarafı bunun tam tersi (swing high, long):
1. **CHoCH** (erken teyit): mum gövdesi son swing low'un altında kapanır.
2. **BOS** (güçlü teyit): CHoCH'tan sonra oluşan swing low'un altında kapanış gelir.
3. **İptal**: CHoCH'tan sonra dokunuştan beri görülen tepenin üstünde kapanış gelir. Bot tekrar CHoCH bekler.

Mesajlarda hedef olarak bir sonraki quadrant yazar (TOP için Q+0.5).

Aynı seviyeye tekrar bildirim gelmesi için fiyatın seviyeden `REARM_EM` (varsayılan 0.25 EM) uzaklaşması gerekir.

## Kurulum

1. Telegram'da @BotFather'a yaz: `/newbot`. Verdiği token'ı kopyala.
2. `.env.example` dosyasını `.env` olarak kopyala ve `TELEGRAM_TOKEN` alanını doldur.
3. `run.bat` ile botu başlat. Telegram'da bota herhangi bir mesaj yaz: sana chat ID'ni söyler.
4. Bu ID'yi `.env` içindeki `ALLOWED_CHAT_IDS` alanına yaz ve botu yeniden başlat. Bot bundan sonra yalnızca bu ID'ye cevap verir.
5. Haftalık veriyi (sembol, PWC, EM) olduğu gibi bota yapıştır.

## Komutlar

`/durum` `/seviye NVDA` `/sessiz 30` `/ac` `/test` `/yardim`

## Sunucu

Bot, Docker kurulu herhangi bir Linux sunucuda container olarak çalışır (örneğin AWS Lightsail). Dışarıya port açmaz.

- İlk kurulum: `deploy.env.example` dosyasını `deploy.env` olarak kopyala, sunucu adresini ve SSH anahtarını yaz, sonra `./deploy.sh --env` çalıştır.
- Kod güncellemek için: `./deploy.sh`. `.env` değiştiyse `./deploy.sh --env`.
- Logları görmek için sunucuda: `cd em-bot && docker compose logs -f`
- Hafta verisi sunucudaki `~/em-bot/data/week.json` dosyasında durur.
- Sunucu bot çalışırken `run.bat` ile yerelde **çalıştırma**. Aynı token'la iki bot çalışırsa Telegram çakışır ve bildirimler iki kez gelir.

## Notlar

- Fiyatlar Yahoo Finance'ten gelir. Ücretsizdir ama resmi bir kaynak değildir ve birkaç saniye gecikebilir.
- Bot yalnızca ABD seans saatinde (09:30-16:00 New York) fiyat kontrol eder. Bu saatlerde laptop açık olmalı ve uyku moduna geçmemeli.
- SPX ve NDX, Yahoo'da `^GSPC` ve `^NDX` olarak çekilir. Yeni bir eşleme gerekirse `levels.py` içindeki `YAHOO` sözlüğüne ekle.
