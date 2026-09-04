# Para Birimi Çevirme Aracı (Currency Conversion Tool)

Avrupa Merkez Bankası (ECB) döviz kurlarını Frankfurter üzerinden kullanan ve bir yapay zekâ (AI) ajanının araç (tool) olarak çağırabileceği hafif bir HTTP servisidir.

---

## Hızlı Başlangıç (1 Dakikanın Altında)

### 1. Bağımlılıkları Yükleyin
```bash
pip install -r requirements.txt
```

### 2. Servisi Çalıştırın
Varsayılan olarak `8080` portunda başlar (`$PORT` ve `$FX_UPSTREAM_BASE` ortam değişkenlerini okur):
```bash
./run.sh
# veya: uvicorn main:app --port 8080
```

### 3. Testleri Çalıştırın (%100 Çevrimdışı / Offline)
```bash
./test.sh
# veya: pytest test_main.py -v
```
*Not: Testler harici ağa hiç çıkmadan, taklit edilmiş (mocked) HTTP katmanı üzerinden tamamen çevrimdışı çalışır.*

---

## API Dokümantasyonu

### Endpoint
`GET /tools/convert`

**Sorgu Parametreleri (Query Parameters):**
* `amount` *(zorunlu, ondalık/decimal)*: Çevrilecek miktar. Sıfırdan büyük olmalı ve en fazla 10 ondalık basamak içermelidir.
* `from` *(isteğe bağlı, metin, varsayılan: "EUR")*: Kaynak para biriminin 3 harfli kodu.
* `to` *(isteğe bağlı, metin, varsayılan: "TRY")*: Hedef para biriminin 3 harfli kodu.
* `date` *(isteğe bağlı, metin, varsayılan: "latest")*: `YYYY-MM-DD` biçiminde ISO tarih veya `"latest"`. (`on` parametresi de takma ad olarak desteklenir).

#### Örnek İstek
```bash
curl "http://localhost:8080/tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28"
```

#### Başarılı Yanıt (`200 OK`)
```json
{
  "amount": 250,
  "from": "EUR",
  "to": "TRY",
  "rate": 47.1234,
  "result": 11780.85,
  "rate_date": "2026-08-28",
  "asked_date": "2026-08-28",
  "source": "ECB via frankfurter.dev"
}
```

---

## Uç Durumların (Edge Cases) Ele Alınışı

| Senaryo | Davranış / Yanıt | Gerekçe |
| :--- | :--- | :--- |
| **Hafta Sonu / Resmî Tatiller** (ECB kuru yayımlanmamışsa) | En son yayımlanmış olan ECB iş gününün kuru ile `200 OK` döner. `rate_date` kurun gerçekten ait olduğu günü (örn. Cuma), `asked_date` ise kullanıcının talep ettiği tarihi (örn. Cumartesi) gösterir. | Asla hayali bir kur uydurmaz; tarih farkını şeffafça belirterek ajanın müşteriyi doğru bilgilendirmesini sağlar. |
| **Gelecek Tarih** | `400 Bad Request` döner (`error: future_date`). | Gelecekteki finansal kurlar tahmin edilemez veya uydurulamaz. |
| **Veri Başlangıcından Önceki Tarih** (< 1999-01-04) | `400 Bad Request` döner (`error: date_too_early`). | ECB euro referans kurları 4 Ocak 1999 tarihinde başlamıştır. |
| **Aynı Para Birimleri** (`from == to`) | `400 Bad Request` döner (`error: same_currency`). | Reddetmek, belirsiz sıfır marjlı işlemleri ve ajanın gereksiz istek atmasını önler. |
| **Desteklenmeyen / Olmayan Para Birimi** | `404 Not Found` döner (`error: not_found`). | İlgili para birimi ECB tarafından desteklenmediğinde şeffaf hata döner. |
| **Hatalı Para Birimi Formatı** | `400 Bad Request` döner (`error: invalid_currency`). | Para birimi kodları tam olarak 3 harfli alfabetik ISO kodu olmalıdır. |
| **Miktar Parametresi Eksik** | `400 Bad Request` döner (`error: invalid_input`). | Zorunlu parametre eksiktir. |
| **Miktar Sıfır veya Negatif** | `400 Bad Request` döner (`error: invalid_amount`). | Finansal çevrim tutarları kesinlikle sıfırdan büyük ve pozitif olmalıdır. |
| **Miktar Ondalık Basamak Sayısı** | 10 basamağa kadar tam `Decimal` hassasiyetiyle işlenir. 10 basamaktan fazlası için `400 Bad Request` döner (`error: too_many_decimals`). | Hassasiyet suiistimallerine ve kayan noktalı sayı (float) serileştirme hatalarına karşı korur. |
| **Yavaş Upstream / Zaman Aşımı** | `504 Gateway Timeout` döner (`error: upstream_timeout`). | 5 saniyelik zaman aşımı (timeout), istemcinin süresiz kilitlenmesini engeller. |
| **Upstream 500 / JSON Dışı Yanıt** | `502 Bad Gateway` döner (`error: upstream_error` / `invalid_upstream_response`). | Dış servis altyapı hataları ile istemci girdi hatalarını birbirinden net olarak ayırır. |
| **Upstream Erişilemez** | `503 Service Unavailable` döner (`error: upstream_unreachable`). | Ana sunucuya bağlantı kurulamamıştır. |

---

## Makine Hata Kodları (Machine Error Codes)

Tüm hatalar 2xx dışı bir HTTP statüsü ve aşağıdaki şema ile döner:
```json
{
  "error": "<kisa_makine_kodu>",
  "message": "<insanlarin_okuyabilecegi_aciklama>"
}
```

| HTTP Statüsü | Hata Kodu | Açıklama |
| :--- | :--- | :--- |
| `400` | `invalid_input` | Eksik veya geçersiz sorgu parametresi yapısı. |
| `400` | `invalid_amount` | Miktar sıfır veya negatif. |
| `400` | `too_many_decimals` | Miktar 10 basamaktan fazla ondalık içeriyor. |
| `400` | `invalid_currency` | Para birimi kodu boş veya 3 harfli geçerli bir kod değil. |
| `400` | `same_currency` | Kaynak ve hedef para birimleri birbiriyle aynı. |
| `400` | `invalid_date_format` | Tarih `YYYY-MM-DD` biçiminde değil. |
| `400` | `future_date` | Talep edilen tarih gelecekte bir tarih. |
| `400` | `date_too_early` | Talep edilen tarih ECB veri serisinin başlangıcından (04.01.1999) önce. |
| `404` | `not_found` | Para birimi kodu bulunamadı, ECB tarafından desteklenmiyor veya belirtilen tarihte kur verisi yok. |
| `502` | `upstream_error` | Upstream servisi beklenmedik bir 4xx/5xx statüsü döndü. |
| `502` | `invalid_upstream_response`| Upstream servisi bozuk veya JSON olmayan (HTML vb.) bir gövde döndü. |
| `503` | `upstream_unreachable` | Upstream sunucusuna ulaşılamıyor veya bağlantı reddedildi. |
| `504` | `upstream_timeout` | Upstream isteği zaman aşımına uğradı (> 5 saniye). |

---

## Önbellekleme (Caching) Stratejisi

* **Bellek İçi TTL Önbelleği (In-Memory TTL Cache)**: `cachetools.TTLCache(maxsize=1024, ttl=3600)` altyapısıyla çalışır. İstekler `(tarih, kaynak_para_birimi, hedef_para_birimi)` anahtarıyla saklanır.
* **Gün Sınırı Geçersizleştirmesi (Day-Boundary Invalidation)**: `"latest"` için yapılan istekler, önbellek anahtarına o günün tarihini gömer (`latest-YYYY-MM-DD-FROM-TO`). Böylece önbelleğe alınan kurlar gece yarısını asla aşmaz ve ertesi güne bayat kur aktarılmaz.
* **Ağ Yükünün Azaltılması**: Aynı tarih ve para birimi çifti için tekrarlanan istekler, dış API'ye tekrar gitmeden doğrudan bellekten yanıtlanır.
* **Yanıt Başlığı**: Önbellekten dönen yanıtlarda `X-Cache: HIT` (önbellekte yoksa `X-Cache: MISS`) başlığı yer alır; gövdedeki `"source": "ECB via frankfurter.dev"` alanı ise tutarlılık adına aynen korunur.
