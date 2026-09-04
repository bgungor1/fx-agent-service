# Kod İncelemesi (Code Review) — `tool.py`

**Tarih:** 2026-09-04  
**Dosya:** `tool.py` — 86 satırlık FastAPI döviz çevrim servisi

---

## Özet

Servis çalışıyor ve temel iş mantığı işliyor. Ancak para ödeyen gerçek bir müşteriye doğrudan zarar verebilecek dört kritik sorun var; bu gece canlıya çıkmadan önce bunlardan en az biri kesinlikle düzeltilmelidir.

---

## Bulgular (En tehlikeliden başlayarak)

### 1 · Hata anında sessizce `0.0` dönülmesi — Servis istemciye yalan söylüyor 🔴 BU GECE DÜZELTİLMELİ

**Sorun nedir:** 71. satırdaki `except Exception` bloğu tüm olası hataları (zaman aşımı, bozuk JSON, ağ kopması) sessizce yakalıyor ve HTTP 200 statüsüyle `rate: 0.0` ve `result: 0.0` döndürüyor.

**Müşteriye zararı nedir:** Ajan çalışma zamanı başarılı bir yanıt aldığını sanarak müşteriye "250 EUR = 0 TRY" der. Finansal bir serviste emin bir şekilde verilen yanlış bir cevap, dürüstçe dönülen bir hatadan çok daha tehlikelidir. Müşteri hayali bir kur üzerinden işlem gerçekleştirebilir; HTTP statüsü 200 olduğu ve JSON şeması geçerli göründüğü için hiçbir sistem alarmı tetiklenmez.

**Nasıl doğrulanır:**
```bash
# UPSTREAM değişkenini geçersiz bir adrese çekin, servisi başlatın ve çağırın:
curl "http://localhost:8000/tools/convert?amount=250&from_=EUR&to=TRY"
# Dönen yanıt: HTTP 200 {"rate": 0.0, "result": 0.0}
```

**Çözüm (Tek satır):** `except` bloğunu tamamen kaldırıp FastAPI'nin 500 dönmesine izin verin veya `raise HTTPException(status_code=502, detail=str(exc))` ile istemciye açık bir hata fırlatın.

---

### 2 · Önbellek anahtarının tarihi yok sayması — Bir sorgu gelecekteki tüm sorguları zehirler 🔴

**Sorun nedir:** 28. satırda önbellek anahtarı `f"{base}-{target}"` şeklinde kuruluyor. İstenen tarih anahtarın bir parçası değil.

**Müşteriye zararı nedir:** `2010-01-15` tarihi için yapılan bir EUR→TRY isteği (kur ≈ 2.1), önbelleğe `2.1` değerini yazar. Hemen ardından yapılan güncel EUR→TRY sorgusu (kur ≈ 35) bu önbelleğe çarpar ve kullanıcıya `2.1` döner. Müşteri hiçbir hata sinyali almadan 15 kat hatalı bir kur üzerinden işlem yapar.

**Nasıl doğrulanır:**
```bash
curl "http://localhost:8000/tools/convert?amount=1&from_=EUR&to=TRY&on=2010-01-15"
curl "http://localhost:8000/tools/convert?amount=1&from_=EUR&to=TRY"
# İkinci istek bugünün kuru yerine 2010 yılı kurunu döner
```

**Çözüm:** `key = f"{base}-{target}-{on or 'latest'}"` şeklinde tarihi anahtara ekleyin ve bir TTL tanımlayın (bkz. Bulgu 3).

---

### 3 · "Latest" (Güncel) önbelleğinin hiç süresinin dolmaması — Kurlar süresiz bayatlar 🟠

**Sorun nedir:** `_cache` hiçbir tahliye ve süre aşımı (TTL) mekanizması olmayan düz bir Python sözlüğüdür (`dict`). Bir kez yazılan `"EUR-TRY"` girdisi asla tazelenmez.

**Müşteriye zararı nedir:** Servis 3 gün boyunca açık kalırsa, 3. gün gelen tüm "güncel" istekler Pazartesi gününün kapanış kurunu döner. Müşteri hiçbir uyarı almadan bayat kurlar üzerinden işlem yapmaya devam eder. Ayrıca `rate_date` alanı da hatalı olduğundan (bkz. Bulgu 4), istemci kurların bayat olduğunu fark edemez.

**Nasıl doğrulanır:** Servisi başlatın, dönen kuru kaydedin, dış servisi durdurun (veya sistem saatini 24 saat ileri alın) ve tekrar sorgulayın; dış servise hiç gitmeden aynı eski kur dönecektir.

**Çözüm:** `from cachetools import TTLCache; _cache = TTLCache(maxsize=512, ttl=3600)` kullanarak 1 saatlik TTL uygulayın.

---

### 4 · `rate_date` alanının uydurulması — Servis hiç okumadığı bir tarihi raporluyor 🟠

**Sorun nedir:** 44. satırda `str(on or date.today())` dönülüyor; yani dış servisin yayımladığı gerçek tarih değil, *istemcinin talep ettiği tarih* yansıtılıyor. Dış servis yanıtındaki `"date"` alanı hiç okunmuyor.

**Müşteriye zararı nedir:** Müşteri Cumartesi gününün kurunu ister. ECB hafta sonları veri yayımlamadığı için fallback çalışır ve Cuma gününün kurunu getirir. Ancak servis Cuma gününün doğru kurunu "Cumartesi" etiketiyle sunar. Müşteri ve tüm denetim logları, ECB'nin Cumartesi gününe özel bir kur açıkladığına inanır. Gerçek ECB verileriyle yapılacak mutabakatlar başarısız olur.

**Nasıl doğrulanır:**
```bash
# Yakın geçmişteki herhangi bir Pazar gününü sorgulayın:
curl "http://localhost:8000/tools/convert?amount=1&from_=EUR&to=TRY&on=2026-08-30"
# Yanıttaki rate_date: "2026-08-30" (Pazar — ECB veri yayımlamaz)
# Frankfurter gövdesindeki gerçek yayımlanma tarihi: 2026-08-29 (Cuma)
```

**Çözüm:** Dış servis yanıtındaki `payload["date"]` alanını okuyup yanıta onu yazın.

---

## Şüpheli Görünen Ama Aslında Doğru Olan Şey

**Hafta Sonu / Tatil Geri Dönüşü (Fallback - 36–40. satırlar):** İstenen tarih için kur bulunamadığında kodun sessizce `"latest"` ile tekrar denemesi ilk bakışta hataları yutuyormuş gibi görünebilir. Ancak bu doğru bir davranıştır: ECB iş günü olmayan günlerde veri yayımlamaz ve bir döviz çevrim aracı için mevcut en son kuru dönmek doğru bir semantiktir. Buradaki tek gerçek hata Bulgu 4'tür (tarihin yanlış etiketlenmesi). Fallback mekanizmasının kendisi bir kusur değildir.

---

## Bu Gece Canlıya Çıkmadan Önce Düzeltilecek Tek Şey

**Bulgu 1 — Sessiz `0.0` dönüşünün kaldırılması.**

Diğer tüm bulgular yanlış bir sayı üretir; ancak bu bulgu **doğru gibi görünen yanlış bir sayı** üretir. Hiçbir izleme sistemi, hiçbir alarm mekanizması ve hiçbir istemci `rate: 0.0` değerini meşru bir sıfır sonucundan ayıramaz. Düzeltmesi 30 saniye sürer, sıfır gerileme (regression) riski taşır ve "müşteri işlem yaparken servisin görünmez biçimde iflas etmesi" felaketini kökünden engeller.
