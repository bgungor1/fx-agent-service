

## Tasarım Kararları ve Tercihler (Design Decisions & Trade-Offs)

### 1. Finansal Hassasiyet: `float` Yerine `Decimal`
* **Karar:** Miktar (`amount`) ve hesaplamalarda Python'ın `Decimal` modülü kullanıldı. Sonuç, `ROUND_HALF_UP` yöntemiyle 2 ondalık basamağa yuvarlandı.
* **Gerekçe:** IEEE 754 kayan noktalı sayı (float) aritmetiği sessiz hassasiyet hatalarına yol açar (örn. `0.1 + 0.2 != 0.3`). Döviz çevrimlerinde bu yuvarlama hataları birikerek gerçek maddi tutarsızlıklara dönüşür.
* **Trade-off:** Doğal `float` türüne kıyasla göz ardı edilebilir bir işlemci yükü getirir; ancak müşteri güveni için vazgeçilmezdir.

### 2. Şeffaf Tarihlendirme (Temel Gereksinim)
* **Karar:** Talep edilen tarih için ECB kuru yayımlanmamışsa (hafta sonları, resmî tatiller), servis en son yayımlanmış kuru döner ve gövdede hem `asked_date` (istenen tarih) hem de `rate_date` (kurun ait olduğu tarih) alanlarını açıkça belirtir.
* **Gerekçe:** Gerçek para ile işlem yapan bir müşteri, kurunun hangi güne ait olduğunu bilmek zorundadır. Hafta sonu kuru uydurmak veya Cuma gününün kurunu Pazar diye sunmak müşteriyi yanıltır. Tarihi dürüstçe belirtmek, yapay zekâ modelinin kullanıcıya şunu söyleyebilmesini sağlar: *"Cuma günkü 47.12 kapanış kuru kullanılarak..."*.

### 3. Aynı Para Birimi Çevrimlerinin Reddedilmesi (`from == to`)
* **Karar:** Kur olarak `1.0` dönmek yerine `400 same_currency` hatası fırlatılır.
* **Gerekçe:** Bu araç, ECB döviz kurlarını sorgulamak için tasarlanmıştır. EUR'dan EUR'a veya USD'den USD'ye çevrim yapılması, ajanın prompt'u yanlış anladığına veya bir mantık hatası yaptığına işaret eder. İsteği reddetmek, gereksiz bir işlemi sessizce yürütmek yerine ajan iş akışına anında geri bildirim sağlar.

### 4. Bellek İçi Önbellekleme Stratejisi
* **Karar:** `cachetools.TTLCache(maxsize=1024, ttl=3600)` kullanıldı. Anahtarlar `(tarih, kaynak_para_birimi, hedef_para_birimi)` şeklinde oluşturuldu. `"latest"` isteklerinde o günün tarihi anahtara enjekte edildi (`latest-YYYY-MM-DD-FROM-TO`).
* **Gerekçe:** Aynı sorgular için dış Frankfurter API'sinin gereksiz yere boğulmasını önler, gece yarısı geçişlerinde kurların otomatik geçersiz kılınmasını sağlar ve `maxsize` ile sınırsız bellek büyümesinin (OOM) önüne geçer.
* **(Trade-off):** Süreç içi (in-process) önbellek, servis yeniden başlatıldığında sıfırlanır ve birden çok çalışan (worker) arasında paylaşılamaz.

### 5. Hata Sözleşmesi ve Upstream Hata İzolasyonu
* **Karar:** İstemci kaynaklı hatalar (`400`, `404`) ile dış servis hataları (`502`, `503`, `504`) birbirinden kesin olarak ayrıldı; tüm yanıtlar standart `{"error": "<snake_case_kod>", "message": "<insanca_metin>"}` formatında döndürüldü.
* **Gerekçe:** Bir yapay zekâ ajanı, hatayı telafi etme stratejisini (kullanıcıya tekrar sormak, yeniden denemek veya mühendise bildirmek) belirlemek için öngörülebilir makine hata kodlarına ihtiyaç duyar.

---

## Daha Fazla Zamanım Olsaydı Neler Yapardım? (What I Would Do Next)

Bu servisi kurumsal, yüksek trafikli bir üretim ortamına hazırlarken:

1. **Dağıtık Önbellekleme (Redis)**: Süreç içi önbellek yerine Redis kullanılır; geçmiş iş günleri için uzun (örn. 1 gün), gün içi kurlar için kısa (örn. 15 dakika) TTL'ler ve LRU tahliye mekanizması kurulur.
2. **Devre Kesici (Circuit Breaker)**: Dış servise giden çağrılarda devre kesici deseni (`pybreaker`) uygulanır. Frankfurter art arda 5 kez hata verirse devre açılarak kuyruk oluşması engellenir ve anında hızlı başarısızlık (fail-fast) sağlanır.
3. **Gözlemlenebilirlik ve Metrikler (Observability & Metrics)**:
   * Prometheus metrikleri: İstek süresi histogramı, önbellek isabet/ıskalama (hit/miss) sayacı, upstream hata kodu dağılımları.
   * Ajan çağrıları boyunca izlenebilirlik sağlayan korelasyon ID'li (`trace_id`) yapılandırılmış JSON loglama.
4. **Sağlık Kontrolü Endpoint'leri (Health Checks)**: Kubernetes için `/healthz` (liveness) ve `/readyz` (readiness) kontrolleri eklenir.
5. **İstek Sınırlama (Rate Limiting)**: Olası kötüye kullanımları önlemek için IP ve API anahtarı bazlı hız sınırlama (token bucket algoritması) uygulanır.

---

## Ön Araştırma ve Yapay Zekâ Destekli Geliştirme Yaklaşımı

Kodu yazmaya veya yapay zekâyı yönlendirmeye başlamadan önce, finansal/döviz sistemlerinin üretim ortamındaki kritik risklerini ve Python/FastAPI'ın temiz kod standartlarını araştırdım:

1. **Alan (Domain) Araştırması:** Döviz araçlarının yapay zekâ çalışma zamanlarında en çok nerede patladığını analiz ettim: IEEE 754 kayan noktalı sayı (float) sapmaları, hafta sonlarında kur tarihlerinin uydurulması ve ajanın halüsinasyon görmesine yol açan belirsiz hata mesajları.
2. **Ekosistem ve Temiz Kod Standartları:** TypeScript altyapımı uyarlamak adına modern Python ve FastAPI'ın en iyi pratiklerini araştırdım: uygulama yaşam döngüsü (`lifespan`), `Decimal` ile tam finansal hassasiyet ve ağsız test mimarisi (`respx`).
3. **Kuralları Belirleyip AI'ı Yönlendirme:** Bu araştırmadan elde ettiğim gereksinimlerle tavizsiz mühendislik kuralları belirledim. Yapay zekâya kısıtsız kod yazdırmak yerine, aracı bu katı kurallar çerçevesinde adım adım yönlendirdim.
4. **Kritik İnceleme ve Doğrulama:** Üretilen her parçayı uç durumlara (gece yarısı önbellek zehirlenmesi, 10 basamak sınırı vb.) karşı bizzat denetledim ve %100 çevrimdışı testlerle doğruladım.

---

## Kişisel Deneyimler ve Yaşanan Zorluklar (TS/Node.js'ten Python'a)

Brief'te talep edildiği üzere, TypeScript/Node.js bakış açımı Python ve FastAPI dünyasına uyarlarken üzerinde durup araştırdığım noktalar:

**Bölüm A (Geliştirme) Aşamasındaki Zorluklar:**
1. **Mock ve Test Paradigması:** Jest/Node ortamında HTTP isteklerini yakalamak için sıklıkla `nock` veya `jest.spyOn()` kullanırım. Python'daki karşılığı bulmak (`httpx` ile entegre `respx`) ve `pytest` fixture mekanizmasının bağımlılıkları nasıl enjekte ettiğini kavramak zihinsel bir adaptasyon gerektirdi. Anladıktan sonra `pytest` yapısını son derece pratik ve zarif buldum.
2. **Tip Doğrulama (Pydantic vs. Zod/Class-Validator):** NestJS tarafında yoğun olarak DTO ve dekoratör yapılarına dayanırım. FastAPI'nin Pydantic ile `Query(...)` ve takma ad (`alias="from"`) entegrasyonunu nasıl pürüzsüz çözdüğünü öğrenmek keyifli bir deneyim oldu; TypeScript alternatiflerine göre çok daha az kodla temiz bir sonuç sağladı.
3. **Kayan Nokta (Floating Point) Güvenliği:** JavaScript doğası gereği tek bir `Number` (Float64) tipine dayanır. Python'ın `Decimal` modülünü açıkça kullanmak ve 10 basamak sınırını bilimsel gösterim tuzaklarına (örn. `1e-15`) düşmeden `.as_tuple().exponent` ile garanti altına almak kritik bir öğrenme noktasıydı.

**Bölüm B (Kod İnceleme / Review) Aşamasındaki Zorluklar:**
1. **Sözdiziminin (Syntax) Ötesine Bakmak:** Katı TypeScript disiplininden geldiğim için `tool.py` dosyasını ilk gördüğümde refleks olarak tip ipuçlarının veya formatın eksikliğine odaklanmak istedim. Buradaki asıl meydan okuma, "linter" zihniyetinden çıkıp "iş mantığı ve müşteri etkisi" gözlüğüyle koddaki gerçek tehlikeleri (sınırsız büyüyen sözlük, uydurulan tarihler) görebilmek oldu.
2. **"Tek Bir Düzeltme" İkilemi:** Bu gece canlıya çıkmadan önce düzeltilecek *tek bir* soruna karar vermek ciddi bir mimari tercihti. Sınırsız bellek önbelleği (`_cache = {}`) servisi çökertebilecek bir saatli bomba. Ancak ben sessizce dönen `0.0` sonucunu seçtim. Çünkü **çöken (500 veren) bir servis, müşteriye yanlış fiyat veren ve yalan söyleyen bir servisten her zaman daha güvenlidir.** Bu çalışmadan çıkardığım en değerli mühendislik kazanımı bu oldu.
