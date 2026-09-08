# Yapılanlar — İnşa Günlüğü

> [MIMARI.md](MIMARI.md) **ne yapılacağını** anlatır (tasarım).
> Bu dosya **ne yapıldığını, neden öyle yapıldığını ve neyin ters gittiğini**
> anlatır (uygulama).
>
> Acelen varsa iki bölüme bak: [Bir bakışta durum](#1-bir-bakışta-durum) ve
> [Testlerin yakaladığı hatalar](#10-testlerin-yakaladığı-hatalar). İkincisi
> dosyanın en değerli kısmı — oradaki hataların hiçbiri kodu okuyarak fark
> edilemezdi, hepsi sistemi çalıştırıp zorlayınca çıktı.

---

## İçindekiler

1. [Bir bakışta durum](#1-bir-bakışta-durum)
2. [Nereden başladık](#2-nereden-başladık)
3. [Faz 1 — Sandbox](#3-faz-1--sandbox)
4. [Faz 2 — Ingest](#4-faz-2--ingest)
5. [Faz 3 — Agent](#5-faz-3--agent)
6. [Faz 4 — SQL](#6-faz-4--sql)
7. [Faz 5 — Web](#7-faz-5--web)
8. [Faz 6 — Arayüz](#8-faz-6--arayüz)
9. [Faz 7 — Rötuş](#9-faz-7--rötuş)
10. [Plandan sapmalar](#10-plandan-sapmalar-ve-gerekçeleri)
11. [Testlerin yakaladığı hatalar](#11-testlerin-yakaladığı-hatalar)
12. [Dosya haritası](#12-dosya-haritası)
13. [Test envanteri](#13-test-envanteri)
14. [Canlı doğrulama ve model seçimi](#14-canlı-doğrulama-ve-model-seçimi)
15. [Kalanlar](#15-kalanlar)

---

## 1. Bir bakışta durum

| | |
|---|---|
| Backend + sandbox imajı | 42 Python dosyası, ~6.100 satır |
| Testler | 9 dosya, ~1.350 satır |
| Frontend | `frontend/src` altında 7 dosya, ~1.000 satır |
| Otomatik kontrol | **147, hepsi geçiyor** (`python tests/run_all.py`) |
| Tamamlanan faz | 1, 2, 3, 4, 5, 6, 7 — **hepsi** |
| Canlı doğrulama | Gerçek modelle uçtan uca çalıştırıldı, 4-5 adım / 8-21 sn |

Çalışan uçtan uca akış:

```
dosya / URL / veritabanı / PDF / ZIP / tanınmayan dosya
   ↓  tanı · ayıkla · normalize et · parquet yaz · profille · kataloğa koy
şema kartı  (~1,5k token — modele giden tek veri)
   ↓  agent: düşün → tool çağır → sonucu gör → hatayı düzelt
kod · çıktı · grafik · rapor
   ↓  SSE ile canlı
üç panelli arayüz + indirilebilir .ipynb
```

---

## 2. Nereden başladık

Elde **Faz 0** vardı: tool'suz, tek turluk bir FastAPI sohbet ucu ve boş bir
React iskeleti. Önce mimariyi konuşup [MIMARI.md](MIMARI.md)'yi yazdık,
sonra mevcut kodu silip plandan sıfır başlamayı seçtin.

Konuşarak belirlenen ve sonraki her kararı etkileyen dört tercih:

| Karar | Sonucu ne oldu |
|---|---|
| Değerlendirme canlı demo | Şeffaflık, hız ve hatadan toparlanma öncelikli oldu. Ekranda kod ve hata göstermek bir "özellik" değil, temel tasarım kararı. |
| Veri tipi önceden bilinmiyor | Adaptör katmanı ve **kaçış kapısı** zorunlu hale geldi. |
| Docker sandbox | İzolasyon kazanıldı; yedek olarak subprocess modu baştan yazıldı. |
| Excel + SQL + web (sonra PDF + ZIP) | Beş ayrı adaptör, tek kanonik çıktı: parquet + şema kartı. |

---

## 3. Faz 1 — Sandbox

**Sorun:** Agent Python kodu yazıyor, birinin bunu çalıştırması gerekiyor.
Backend sürecinde `exec()` demek şu riskleri açar: sonsuz döngü backend'i
dondurur, 5 GB'lık okuma RAM'i bitirir, `open('.env')` API anahtarını cevaba
basar. Model saldırmıyor — **kaza yeterince olası.**

**Yapılan:** Ağsız, RAM tavanlı, salt-okunur köke sahip, root olmayan bir
Docker container; içinde stdin/stdout üzerinden JSON konuşan **kalıcı** bir
Python kernel'ı (`df` çağrılar arasında yaşıyor).

**Neden ortak gövde:** Docker ve subprocess yedeği aynı `PipeKernel` sınıfını
kullanıyor. Protokol, zaman aşımı, çökme toparlama ve kilitleme tek yerde.
Böylece "iki backend aynı davranır" bir temenni değil, **yapısal garanti** —
demo günü yedeğe düşersen davranış değişmiyor.

Fiilen doğrulananlar:

```
kalıcılık   x=5 → print(x) → 5 · df çağrılar arası yaşıyor
mount       Türkçe karakterli yol (Masaüstü) sorunsuz
izolasyon   internet OSError · /data yazma OSError · kök fs OSError · uid 1000
kaynak      sonsuz döngü kesildi · 3 GB istendi → OOM → kernel toparlandı
grafik      /artifacts'a düştü, host tarafında 12 KB olarak göründü
```

---

## 4. Faz 2 — Ingest

**Sorun:** Yarışmada hangi formatın geleceği bilinmiyor. Excel de olabilir,
cp1254 kodlu CSV de, hiç görülmemiş bir uzantı da.

**Yapılan:** Altı adımlık huni — `sniff → extract → normalize → materialize
→ profile → catalog`. Kaynak ne olursa olsun çıktı aynı: parquet + şema
kartı. Agent için "bu bir Excel'di" bilgisi yok oluyor.

### Kritik alt kararlar

**Her şey önce metin olarak okunuyor.** pandas'ın kendi tip çıkarımına
bırakılırsa Türkçe veride sessizce yanlış sonuç alınır: `"1.234"` float
`1.234` olur (bin iki yüz otuz dört değil). Tip kararını `normalize.py`
veriyor — kolonun tamamına bakıp çoğunluk kuralıyla.

**Excel'de üç heuristik:** başlık satırı puanlanarak bulunuyor (kurumsal
dosyalarda ilk satırlar logo/başlık olur), alt toplam satırları veriden
ayrılıyor, boş sheet'ler eleniyor.

**Kaçış kapısı.** Hiçbir adaptör eşleşmezse dosya ham kopyalanıyor ve şema
kartı yerine hex önizleme + tahmini satır sayısı + ayıraç ipuçları veriliyor.
Agent kendi parser'ını yazıyor. *"Bilinmeyen veri tipi" riskini sıfıra
indiren tek mekanizma bu.*

**Profiling kendi yazıldı.** `ydata-profiling` yavaş, devasa HTML üretiyor
ve token'a çevrilemiyor. ~350 satırlık kendi `profile.py`'ımız ~300 ms'de
~1,5k token'lık kart üretiyor.

Örnek çıktı (kasten bozulmuş bir Excel'den):

```
satislar_2024 — 5 satır × 5 kolon   (kaynak: satislar_2024.xlsx → Satışlar 2024)
  musteri_no      int       boş %0.0   5 uniq (tekil) ← anahtar adayı
  siparis_tarihi  datetime  boş %0.0   2024-01-15 → 2024-12-31 (12 ayın 7'sinde kayıt yok)
  tutar_tl        float     boş %0.0   min -500  p50 3.100  maks 12.000
  ⚠ başlık 4. satırda bulundu · 1 alt toplam satırı ayrıldı · Boş Sayfa atlandı
  🔗 satislar_2024.musteri_no ⟷ satislar_2024_musteriler.musteri_no (%100 örtüşme)
```

Bu üç satır uyarının her biri, olmasa agent'ın gözden kaçıracağı bir şey.

---

## 5. Faz 3 — Agent

**Sorun:** Faz 1 ve 2 birbirine bağlı değildi — ortada karar veren kimse
yoktu.

**Yapılan:** ReAct döngüsü (düşün → tool çağır → sonucu gör → tekrar),
7 tool, SSE akışı, `trace.jsonl`.

### Kritik alt kararlar

**Tek güçlü tool.** `compute_correlation`, `plot_histogram` gibi 40 dar tool
yazmadık. Böyle bir sette model, karşılığı olmayan bir soruyla karşılaşınca
duvara toslar. Bunun yerine kalıcı kernel + birkaç dar yardımcı.

**Hatalar gizlenmiyor.** Traceback kısaltılmadan modele gidiyor, aynı anda
ekranda kırmızı çerçeveyle gösteriliyor. Jüri agent'ın hata yapıp bir sonraki
adımda düzelttiğini görmeli — bu güven artırır.

**3 ardışık hatada kernel sıfırlanıyor** ve modele "tüm değişkenler silindi,
aynı yolu deneme, önce şemayı doğrula" notu gidiyor. Modeller aynı hatayı
tekrarlamaya meyillidir; bu not döngüyü kırıyor.

**Altyapı hatası ölümcül sayılıyor.** Docker kapalıysa model bunu kod
yazarak çözemez; döngü 25 iterasyon yakmak yerine duruyor ve eyleme dönük
bir mesaj veriyor.

**`trace.jsonl` baştan yazılıyor.** Notebook export'u buradan bedava
geliyor. Sonradan eklemeye kalksak çalışan kodu geri toplamak imkânsıza
yakın olurdu.

**LLM soyutlama katmanı** (`agent/llm.py`) — planda yoktu, sonradan eklendi.
Yarışma "yerel model kullanın" derse döngüyü baştan yazmayalım diye. Döngü
sadece `LLMBackend` arayüzünü görüyor; sunucu değişimi tek `.env` satırı.
Bu kararın karşılığını beklenmedik bir yerde verdi — bkz. hata #16.

---

## 6. Faz 4 — SQL

**İki parça var.**

**`run_sql`:** Her veri seti kendi adıyla sorgulanıyor (`SELECT * FROM satis`),
DuckDB view'ları otomatik kuruluyor. Sonuç küçükse (≤200 satır) tablo
doğrudan modele dönüyor; büyükse parquet'e yazılıp kataloğa giriyor ve modele
"kaydettim, şu adla oku" deniyor. Limit yoksa otomatik `LIMIT 1000`
ekleniyor.

Bu tasarım agent'ı **"SQL ile daralt → Python ile derinleş"** desenine
itiyor: 3 milyon satırı Python'a çekmek yerine önce grupla, sonra işle.
Canlı testte agent bu deseni kendiliğinden izledi.

Güvenlik **parse ederek** yapılıyor, metinde kelime arayarak değil:

```
engellendi:   DROP · DELETE · UPDATE · INSERT · CREATE · ATTACH · COPY
              SELECT 1; DROP TABLE satis          ← zincirli ifade
izin verildi: WITH … SELECT · UNION ALL
              SELECT * FROM satis WHERE durum = 'DROP TABLE'   ← yanlış alarm yok
```

> Metinde `"DROP"` aramak güvenlik değildir: `WHERE not = 'DROP'` yanlış
> alarm verir, `sELeCt/**/1; drop table x` ise kaçar. Tek doğru yöntem
> ayrıştırıp ifade tipine bakmaktır.

**Veritabanı kaynağı:** SQLAlchemy ile şema keşfediliyor, tablolar parquet'e
inip kataloğa giriyor. Bağlantı dizesi backend'de kalıyor: yanıtlara
sızmıyor, agent'a hiç gösterilmiyor, hata mesajında parola maskeleniyor.
Üçü de ayrı ayrı test edildi.

---

## 7. Faz 5 — Web

**Sorun:** Sandbox ağsız, yani ağa çıkan tek yer backend. Orada bir SSRF
açığı bırakırsak (sunucuyu kandırıp iç ağa istek attırma) kendi ağımıza kapı
açmış oluruz.

**Yapılan:** Şema kontrolü, DNS çözümü + özel adres reddi, **elle takip
edilen yönlendirmeler** (her adımda yeniden denetim — açık yönlendirmeyle iç
ağa sıçramayı engeller), `robots.txt`, alan adı başına gecikme, boyut ve süre
tavanı.

**DOM haritası.** Modele ham HTML basmıyoruz; sayfanın yapısını
özetliyoruz: tablolar ve satır sayıları, başlıklar, ana metin, arka planda
çağrılan API uçları. Ölçüldü: sayfa 100 katına çıkarıldığında (843 bayt →
122 KB) **harita 2 KB'da kaldı.** Ham HTML gitseydi 30k+ token yanardı.

**İki giriş yolu:** `POST /sources/url` kullanıcı için (tabloları otomatik
kataloğa alır), `fetch_url` tool'u agent için (sayfayı kaydeder, haritayı
döner, agent kendi seçicisini yazar).

---

## 8. Faz 6 — Arayüz

Üç panel: **veri setleri** (sürükle-bırak, URL, veritabanı, örnek veri, şema
göster) · **akış** (kod bloğu + çıktı + grafik; hatalar kırmızı, tıklayınca
katlanır) · **analiz defteri** (biriken kod + `.ipynb` indirme).

Başlıkta canlı rozet: model adı ve sandbox modu. `local`'e düşünce turuncu
yanıyor — demo günü hangi modda olduğunu bir bakışta görürsün.

**Teknik engel:** SSE için tarayıcının standart `EventSource` API'si
kullanılamadı; o yalnızca GET yapabiliyor, biz mesajı POST gövdesinde
yolluyoruz. `fetch` + `ReadableStream` ile çerçeveler elle ayrıştırılıyor.

Tarayıcıda gerçekten çalıştırılıp doğrulandı: dosya yüklendi, iki veri seti
panele düştü, şema kartı penceresi açıldı.

---

## 9. Faz 7 — Rötuş

**Notebook export backend'e taşındı.** Artık `trace.jsonl`'dan üretiliyor:
çıktılar gömülüyor, SQL adımları giriyor, veri setleri listeleniyor, bulgular
bölümü ekleniyor. Frontend'in elindeki kod listesinden üretmek sayfa
yenilenince kayıp veriyordu.

**Tek grafik stili** (`analyst.mplstyle`) — agent sadece veriyi çiziyor,
görünüm buradan geliyor. Her grafiği model kendi biçimlendirirse çıktı
dağınık olur; sunumda tutarlılık fark yaratıyor. Türkçe karakterler için
DejaVu Sans.

**Hazır örnek veri** — jüri kendi dosyasını getirmezse tek tıkla 6.000
satırlık gerçekçi veri. Kasten "temiz değil": iadeler (negatif tutar), aykırı
değerler, boş kolonlar, bir aylık tarih boşluğu. Böylece şema kartındaki
uyarılar ve agent'ın bunları fark etmesi demoda görünüyor.

**PDF ve ZIP adaptörleri.** PDF'te iki strateji: önce çizgiye göre, olmazsa
metin hizasına göre (kurumsal PDF'lerin çoğunda tablo çerçevesi yoktur).
ZIP'te her üye normal hattan yeniden geçiyor; zip slip ve sıkıştırma bombası
korumalı.

**Testler `tests/` altına toplandı**, tek komutla koşuyor:

```
python tests/run_all.py     →  147 kontrol
```

---

## 10. Plandan sapmalar ve gerekçeleri

Üç yerde plandan ayrıldık. Üçü de uygulama sırasında ortaya çıkan gerçek
kısıtlar yüzünden; hiçbiri "kolayına kaçma" değil.

### Warm pool → oturum açılışında başlatma

Plan, önceden açılmış container havuzu öngörüyordu. **Uygulanabilir değil:**
her container o oturumun klasörlerine bağlanmak zorunda. Oturumdan bağımsız
açılmış bir container ya hiçbir veriyi görür ya hepsini — ikincisi
oturumlar arası **veri sızıntısı** demek.

Yerine: kernel oturum açılır açılmaz başlıyor (jüri dosyayı yüklerken
container kalkıyor) + açılışta bir kez `prewarm()` ile Docker katmanları
ısıtılıyor. Aynı fayda, izolasyon bozulmadan.

### DuckDB ATTACH → tabloları parquet'e indirme

Plan canlı veritabanı sorgusu öngörüyordu. İndirme seçildi çünkü: İlke 1'e
daha uygun (agent kaynak tipini bilmez), profiling/örnekleme/ilişki tespiti
bedava geliyor, DuckDB eklenti kurulumuna bağımlılık kalmıyor. `ATTACH` çok
büyük veritabanları için sonradan eklenebilecek bir optimizasyon.

### Sabit yol → `data_path()` / `artifact_path()`

Şema kartları başta `/data/x.parquet` yazıyordu. Bu yol **yalnızca
Docker'da var**; yerel yedeğe düşünce her `run_python` patlıyordu — yani
yedek, tam ihtiyaç duyulan anda çalışmayacaktı. Soyutlama sızmıştı.

```python
df = pd.read_parquet(data_path('satis.parquet'))   # iki modda da çalışır
fig.savefig(artifact_path('grafik.png'))
```

---

## 11. Testlerin yakaladığı hatalar

**Dosyanın en önemli bölümü.** Hiçbiri kod okuyarak fark edilemezdi; hepsi
çalışan sistemi zorlayınca çıktı. Çoğu *sessiz* hata — program patlamıyor,
sadece yanlış sonuç üretiyor. Numaralar ilk bulundukları sıradan geliyor.

### A. Sessiz veri bozulmaları — en tehlikeli grup

Bu hataların ortak özelliği: hiçbiri hata mesajı vermiyor. Fark etmezsen
rapor yanlış çıkıyor ve kimse anlamıyor.

| # | Ne oldu | Neden tehlikeliydi / ne yapıldı |
|---|---|---|
| 1 | Türkçe dosya `cp1254` yerine `cp1257` (Baltık) sanıldı | `Şube Adı` kolonu `zube_adz` oluyordu. Tespit kütüphanesi iki kod sayfasını karıştırıyordu; Türkçe lehine ek puanlama konuldu. |
| 3 | Kimlik kolonları float oldu (`1001.0`) | Join'ler tutmuyor, raporda `1001.0` görünüyordu. Artık `Int64` tipi kullanılıyor. |
| 4 | Başlıksız dosyada ilk kayıt uçuyordu | **Veri kaybı.** İlk satırın tip profili gövdeyle karşılaştırılıyor artık: gövdeye benziyorsa veridir, benzemiyorsa başlıktır. |
| 5 | Önsöz/açıklama satırı başlık sanıldı | 4 alandan 3'ü sessizce düşüyordu. |
| 8 | Aynı adlı ikinci veri seti birincinin dosyasını eziyordu | Katalog `satis_2` diyordu ama parquet yine `satis.parquet` olarak yazılıyordu — birinci veri seti yok oluyordu. |
| 9 | İlişki (join) tespiti keyfi bir eşikteydi | "En az 5 farklı değer" yerine "bir taraf gerçek anahtar olmalı" kuralı kondu. Hem küçük tabloları yakalıyor hem uydurma join önermiyor. |
| 10 | `read_html` `"1,250"` değerini `1250` yapıyordu | pandas virgülü binlik ayıracı sayıyor; doğrusu **1,25**. `thousands=None` ile kapatıldı. |
| 11 | İlişki bloğu ilgisiz şema kartlarına da yazılıyordu | Model alakasız join'lere itiliyordu. |
| 13 | Boş metin (`""`) NaN'a çevrilmiyordu | `if v` koşulu boş metni listeden eliyordu. PDF/HTML çıkarımında hücreler `""` gelir; "tamamen boş satır" tespiti çalışmayınca tabloya **hayalet satırlar** sızıyordu. |
| 15 | Yabancı anahtara çeyreklik (medyan) gösteriliyordu | `musteri_no` için `p50 10.121` yazıyordu — müşteri numarasının medyanı anlamsız. Tekillik oranı düşük olduğu için kimlik sayılmıyordu (6.000 siparişte 240 müşteri). Artık kolon adı da sinyal: `240 uniq (%4 tekil ← yabancı anahtar adayı)`. |

### B. Altyapı — "çalışıyor sanıyordun, çalışmıyordu"

| # | Ne oldu | Neden tehlikeliydi / ne yapıldı |
|---|---|---|
| 2 | `/data/...` yolu yalnızca Docker'da vardı | Yedek mod tam ihtiyaç duyulduğu anda çalışmayacaktı. `data_path()` / `artifact_path()` eklendi. |
| 7 | Docker kapalıyken döngü 25 iterasyon yakıyordu | Model Docker'ı başlatamaz; altyapı hatası ölümcül sayılmalı. Artık tek net mesajla duruyor. |
| 12 | Bayat Docker imajı sessizce yanlış davranıyordu | `kernel_server.py` imaja kopyalanıyor; dosyayı değiştirip build etmezsen eski sürüm koşuyor. Artık protokol sürümü uyuşmazlığı açılışta yakalanıyor. |
| 14 | Grafik stili kernel'ı öldürüyordu | `matplotlib.style` alt modülü `import matplotlib` ile gelmiyor → `AttributeError` → kernel açılışta ölüyordu. Stil yükleme ayrı `try` içine alındı: bozuk stil grafikleri çirkinleştirir, analizi durdurmaz. |
| 17 | Test temizliği gerçek oturumları siliyordu | `run_all.py` tüm oturum klasörlerini siliyordu ve canlı bir analizin trace'ini yok etti. Testler artık `tests/.storage` altına yazıyor (`STORAGE_DIR`). |
| 20 | **`uvicorn --reload` Windows'ta sandbox'ı tamamen öldürüyordu** | `--reload` → uvicorn `SelectorEventLoop` seçiyor, o loop alt süreç açamıyor. Sandbox'ın tamamı alt sürece dayandığı için docker/local kernel ve prewarm `NotImplementedError` ile ölüyordu. Üstelik `str(e)` **boş** olduğundan hata mesajı hiçbir şey söylemiyordu. `main.py`'ın kendi docstring'i `--reload` öneriyordu — yani belgelenmiş kullanım bozuktu. Çözüm: `run_dev.py`. |
| 21 | prewarm bir optimizasyonken açılışı çökertiyordu | `except` listesinde `NotImplementedError` yoktu; ısıtma başarısız olunca uygulama hiç kalkmıyordu. **Optimizasyon asla açılışı düşürmemeli.** |
| 23 | `catalog.json` atomik yazımı OneDrive'da patlıyordu | `os.replace` Windows'ta dosya başka süreçte açıksa `PermissionError` veriyor. Proje OneDrive klasöründe; testte gerçekten oldu ve isteği 500'e düşürdü. Kısa aralıklı 5 deneme eklendi. |

### C. Model davranışı — "doğru kod, yanlış cümle"

Bu grup özellikle öğretici: kod çalışıyor, sayı doğru üretiliyor, ama model
onu **yanlış aktarıyor**. Çözümlerin çoğu prompt'a giren birer kural.

| # | Ne oldu | Neden tehlikeliydi / ne yapıldı |
|---|---|---|
| 6 | Kredi bitişi "rate limit" sanıldı | OpenAI ikisini de HTTP 429 döndürüyor. Demo günü "biraz bekle" deyip zaman kaybettirecekti. Artık ayrı mesaj: "kredi yükle, beklemek çözmez". |
| 16 | **Chat ucunda tool calling hiç çalışmıyordu** | `gpt-5.6` ailesi chat ucunda function tool + akıl yürütmeyi birlikte kabul etmiyor (HTTP 400). Kredi yüklenene kadar fark edilemezdi. Ayrıntı aşağıda. |
| 19 | Modele "şu yardımcıyı kullan" demek yetmedi | Prompt'ta `tr_eksen()` tarif edilmesine rağmen model kendi biçimlendiricisini yazıp ekseni ham bıraktı (`1000000`). Biçimlendirme artık `savefig` kancasıyla **otomatik** — modelin hatırlamasına bağlı değil. |
| 22 | **Model yüzdeyi kafadan hesaplayıp yanlış söyledi** | Aynı soru, aynı SQL: bir turda payı kodda hesapladı (%42,2 — doğru), başka turda tabloya bakıp kafadan söyledi (**%39,2 — yanlış**). Raporda tek yanlış sayı tüm analizin güvenilirliğini götürür ve jüri kontrol ederse yakalar. Prompt'a "aritmetiği koda yaptır, çıktısını gör" kuralı eklendi; doğrulandı. |
| 24 | **Model ters koşullu olasılık veriyordu** | Gerçek veride "hedefi seçenlerin %98,8'inde şu özellik var" dedi. Kulağa çarpıcı gelir ama karar verdirmez. Doğru yön: "özellik olanların %27,5'i seçiyor, **olmayanların %0,6'sı**". Aynı şey ortalamalarda: tek ortalama (4,38 vs 2,63) yerine gerçek tablo seviye 1'de %0,6 → seviye 5'te %51,8. Prompt'a `P(hedef \| özellik)` kuralı eklendi; 14 oranın 14'ü doğrulandı. |
| 25 | Model iki kolonun sayılarını birbirine attı | `range_anxiety_level='High'` sayılarını (3 / 2.191) `environmental_concern_level`'a atfetti — o kolon 1-5 arası sayısal, "High" kategorisi yok. Üstelik cümle kendi içinde çelişiyordu. Prompt'a "kolon adını yazmadan önce şema kartında gör" kuralı eklendi. |

### D. Sunum ve çıktı

| # | Ne oldu | Neden tehlikeliydi / ne yapıldı |
|---|---|---|
| 18 | **Grafik ekseninde yanlış sayı** | Kısaltmada eşik bölen olarak kullanılmıştı: `500.000` ekseni **"50 bin"** gösteriyordu. Jüri yanlış rakam okurdu. Eşik ve bölen ayrıldı; `tests/test_grafik_bicim.py` ile 8 değer sabitlendi. |
| 26 | **`.ipynb` indirilemiyordu** | Defter paneli sadece `run_python` adımlarını sayıyordu; sadece SQL kullanan bir analizde "0 hücre" deyip indirmeyi engelliyordu. Oysa `export.py` SQL adımlarını zaten hücreye çeviriyordu — gerçek bir oturumda **16 hücre** üretilebiliyordu ama buton pasifti. Artık `executable` bayrağıyla sayılıyor. |
| 27 | Her tool "0 ms" gösteriyordu | Süreyi handler'lar tek tek ölçüyordu ve çoğu unutmuştu; yavaş adımı bulmak imkânsızdı. Ölçüm `dispatch()`'e taşındı — handler kendi gerçek süresini yazdıysa (sandbox) ona dokunulmuyor. |

### Hata 16 detay — LLM soyutlamasının karşılığını verdiği an

Kredi yüklenip ilk gerçek çağrı yapıldığında çıktı:

```
400  Function tools with reasoning_effort are not supported for
     gpt-5.6-terra in /v1/chat/completions.
     To use function tools, use /v1/responses or set reasoning_effort to 'none'.
```

Tahmin etmek yerine ölçüldü:

| Yol | Sonuç |
|---|---|
| chat + tools | ❌ HTTP 400 |
| chat + tools + `reasoning_effort=none` | ✅ ama **akıl yürütme kapalı** |
| chat + tools + `reasoning_effort=low` | ❌ HTTP 400 |
| **responses + tools** | ✅ **akıl yürütme açık**, 55 vs 137 girdi token'ı |

Kod yazıp hatasını düzelten bir agent'ta akıl yürütmeyi kapatmak kabul
edilemez. `ResponsesBackend` yazıldı; durum sunucuda tutulmuyor
(`previous_response_id` yok), geçmiş her turda gönderiliyor — ölçülerek
doğrulandı.

**Katmanın değeri burada görüldü:** `loop.py`, `tools.py`, `prompts.py`,
`events.py` — hiçbirine dokunulmadı. Değişiklik tek dosyada kaldı.
Varsayılan artık kaynağa göre seçiliyor: OpenAI'da `responses`,
`LLM_BASE_URL` doluysa `chat`.

---

## 12. Dosya haritası

```
run_dev.py             Windows'ta çalışan geliştirme sunucusu (uvicorn --reload YERİNE)

backend/
  config.py            Tüm ayarlar tek yerde
  main.py              FastAPI, router'lar, loglama, lifespan (prewarm/shutdown)
  query.py             DuckDB motoru + sqlglot ile SELECT doğrulama

  agent/
    llm.py             LLM soyutlaması — chat/responses, kota hatası ayrımı
    loop.py            ReAct döngüsü, bütçe, hata düzeltme, kurtarma
    tools.py           7 tool şeması + handler'ları + dispatch (süre ölçümü)
    prompts.py         Sistem prompt'u (şema doğrula · aritmetiği koda yaptır · koşullu oran)
    events.py          SSE event tipleri (executable bayrağı, SQL gizleme)
    session.py         Çalışma bağlamı + trace.jsonl

  ingest/
    router.py          sniff + boru hattı (dosya / veritabanı / html)
    normalize.py       Türkçe sayı-tarih-kolon adı; İ.lower() tuzağı
    profile.py         Şema kartı + ilişki adayı tespiti
    catalog.py         Dataset kayıtları (atomik yazım + OneDrive yeniden deneme)
    sample.py          Demo için kasten kirli örnek veri üretici
    adapters/
      base.py          Ortak tipler (Extracted, UnsupportedSource)
      tabular.py       csv/json/parquet + encoding & ayıraç & başlık tespiti
      excel.py         sheet ayırma, başlık puanlama, alt toplam ayıklama
      sql.py           SQLAlchemy şema keşfi, DSN maskeleme
      web.py           DOM haritası + tablo çıkarma
      pdf.py           pdfplumber — çizgi ve metin hizası stratejileri
      archive.py       ZIP → çoklu dataset, zip slip + bomba koruması
      raw.py           Kaçış kapısı (hex önizleme)

  sandbox/
    base.py            PipeKernel — protokol, timeout, çökme toparlama
    docker_kernel.py   Asıl sandbox
    local_kernel.py    Yedek (izole değil)
    manager.py         Yaşam döngüsü, prewarm, SandboxUnavailable, Windows teşhisi
    protocol.py        JSON protokolü + sürüm kontrolü

  fetch/client.py      SSRF koruması, robots.txt, yönlendirme denetimi
  api/                 health · sessions · sources · chat (SSE) · artifacts · export

sandbox_image/
  Dockerfile           Ağsız, root olmayan, Türkçe fontlu imaj
  kernel_server.py     Container içindeki kalıcı kernel (PROTOCOL_VERSION = 6)
  analyst.mplstyle     Tek grafik stili

frontend/src/
  App.tsx              Akış durumu, event → ekran
  api.ts               fetch + SSE ayrıştırma (EventSource kullanılamadı)
  components/          DatasetPanel · ToolCallCard · NotebookPanel

tests/                 run_all.py + 6 test + smoke_sandbox + test_grafik_bicim + veri/
```

---

## 13. Test envanteri

```
python tests/run_all.py
```

| Test | Kontrol | Kapsam |
|---|---|---|
| `test_normalize` | 25 | Türkçe sayı/tarih/kolon adı, `İ.lower()` tuzağı |
| `test_ingest` | uçtan uca | Bozuk Excel, cp1254 CSV, tanınmayan dosya |
| `test_agent` | 31 | Döngü, hata düzeltme, kernel restart, SSE, trace |
| `test_sql` | 36 | SQL güvenliği, veritabanı ingest, DSN sızıntısı |
| `test_web` | 29 | SSRF, robots, DOM haritası, tablo çıkarma |
| `test_export_sources` | 26 | Örnek veri, notebook export, PDF, ZIP güvenliği |
| **Toplam** | **147** | son çalıştırmada hepsi geçti |

Bu testler `SANDBOX_BACKEND=local` ile koşuyor, yani **Docker gerekmiyor** —
hızlı geri bildirim için bilinçli tercih.

**Ayrı koşan iki test var** (ikisi de gerçek kernel gerektirdiği için
`run_all.py`'ın içinde değil):

```
python tests/smoke_sandbox.py       # Docker izolasyon sınırları:
                                    # ağ yok · salt-okunur mount · RAM tavanı
                                    # · timeout · OOM sonrası toparlanma
python tests/test_grafik_bicim.py   # eksen biçimlendirmesi (hata #18'in testi)
```

Docker imajına veya `kernel_server.py`'a dokunduğunda bu ikisini elle koş.

---

## 14. Canlı doğrulama ve model seçimi

### Uçtan uca gerçek çalıştırma ✅

Docker imajı build edildi, kredi yüklendi, sistem **gerçek modelle** uçtan
uca çalıştırıldı:

```
soru : "Bölgelere göre toplam ciroyu bul ve grafikle. Dikkat çeken bir şey var mı?"
veri : 6.000 satırlık örnek satış verisi
──────────────────────────────────────────────────────────────
 3.7s  get_schema × 2      iki tablonun şemasını okudu
 6.2s  run_sql             bölge kırılımını SQL ile aldı, kataloğa kaydetti
14.7s  run_python          grafiği çizdi
20.9s  finish              tablo + 4 maddelik bulgu
──────────────────────────────────────────────────────────────
4 adım · 21 saniye · 12.030 giriş / 1.242 çıkış token · $0,039 (~1,64 TL)
```

Tasarlanan deseni kendiliğinden izledi: **SQL ile daralt → Python ile
derinleş.** Grafikte başlık, birimli eksenler, değer etiketleri ve yüzde
payları var; Türkçe karakterler sorunsuz. Notebook 7 hücre olarak üretildi
(soru → SQL → Python → grafik notu → bulgular).

### Model karşılaştırması (aynı soru, aynı veri)

| | terra | luna |
|---|---|---|
| Adım | 4 | 4 |
| Süre | 21 sn | 20-25 sn |
| Sayısal sonuç | aynı | aynı |
| Maliyet | $0,039 | **$0,004** |
| $5 ile | ~125 analiz | **~1.250 analiz** |

Luna kalite kaybı göstermedi; bir turda negatif tutarları fark edip
*"finansal raporlama için yalnızca tamamlanmış siparişler ayrıca izlenmeli"*
uyarısını ekledi — terra'nın atladığı bir gözlem.

### Daha aşağısı — `gpt-5.4-mini`'ye geçiş

Hesaptaki 125 modelin tamamı tarandı, ucuz adaylar **gerçek bir tool
çağrısıyla** denendi (responses ucu + function tool — projenin ihtiyacı olan
tam kombinasyon). Hepsi tool çağırıyor, yani teknik engel yok:

| model | süre | çıkış tok. | not |
|---|---|---|---|
| **gpt-5.4-mini** | **1,0 sn** | 19 | ← seçildi |
| gpt-5.4-nano | 1,4 sn | 19 | |
| gpt-4.1-mini | 1,7 sn | 16 | |
| gpt-5-mini | 2,5 sn | 143 | |
| gpt-5.6-luna | 4,5 sn | 31 | önceki |
| gpt-5-nano | 5,3 sn | **415** | akıl yürütme israfı — kaçın |

`gpt-5-nano` dersi: **"ucuz model" ile "az token yakan model" aynı şey
değil.** Tek bir sıradan çağrı için 415 çıkış tokeni yaktı — luna'nın 13 katı.

**Uçtan uca doğrulandı** (aynı soru, aynı örnek veri, gerçek sandbox, 2 tur):

| | luna (önceki) | gpt-5.4-mini |
|---|---|---|
| Adım | 4 | 5 |
| Süre | 21 sn | **8,5 – 11,7 sn** |
| Tool hatası | — | **0** |
| Token | 12.030 / 1.242 | **11.776 / 681** |
| Sayısal sonuç | — | aynı (Marmara %42,2) |

> **Dikkat — tur-arası varyans.** İlk turda model şema kartındaki
> *"`tutar`: 115 negatif değer"* uyarısına hiç değinmedi; ikinci turda
> değindi. Yani veri kalitesi gözlemi bu modelde **garanti değil, olasılık**.
> Kritikse soruya açıkça ekle ("veri kalitesi sorunlarına da bak") ya da
> demo günü luna/terra'ya çık. Tek turdan model kalitesi çıkarımı yapma.

**Karar: `gpt-5.4-mini`** (`.env` içinde ayarlı). Ölçülen fayda hız —
maliyet zaten luna seviyesinde düşüktü, asıl kazanç demo saniyesi.
Bilinen-iyi geri dönüş noktaları: `gpt-5.6-luna` (uçtan uca doğrulanmış),
`gpt-5.6-terra` (referans kalite). Değişiklik tek satır.

### Sunum/hata ayıklama anahtarları

| Ayar | Varsayılan | Ne yapar |
|---|---|---|
| `SHOW_SQL_IN_CHAT` | `false` | SQL sorgu METNİ sohbet ekranında görünsün mü. Kapalıyken kart ve sonuç tablosu görünür, sorgu görünmez. **Jüri sunumunda `true` yap** — MIMARI İlke 4 "her şey şeffaf" diyor ve "SQL'i agent kendi yazdı" demenin en güçlü yolu bu. Sorgu her hâlükârda loga, `trace.jsonl`'a ve `.ipynb`'ye tam gider. |
| `LOG_LEVEL` | `INFO` | Konsol seviyesi. Sorun ararken `DEBUG`. |

Log ayrıca `backend/storage/app.log`'a yazılıyor (dosyada **her zaman
DEBUG**, 10 MB × 3 dosya döner). Konsol kaydırıp gittiğinde hatanın
öncesindeki adımları geri okumanın tek yolu buydu. Her satırda oturum
kimliği + adım numarası var:

```
[759ba7eed805] adım 6/25 · token 4937 giriş / 299 çıkış (toplam 20454/1004) · istenen tool: run_sql
[759ba7eed805] SQL:
SELECT charging_stations_near_work, COUNT(*) AS n, AVG(...) AS buy_rate FROM train GROUP BY 1
[759ba7eed805] adım 6 · run_sql OK · 90 ms · çıktı 964 karakter
```

---

## 15. Kalanlar

Planlanan yedi fazın hepsi tamam. Sırada **isteğe bağlı** iyileştirmeler var;
hiçbiri demo için zorunlu değil:

- Playwright'ın gerçek bir JavaScript sitesinde denenmesi (kod hazır, saha
  testi yapılmadı)
- Çok büyük dosyada (>1M satır) örnekleme yolunun ölçülmesi — mekanizma
  yazıldı ama gerçek bir 1M+ satırlık dosyayla süre ölçülmedi
- Üretilen grafiği vision destekli modele geri gösterip "okunaklı mı" diye
  kontrol ettirmek (etkileyici ama pahalı)
- `test_grafik_bicim.py`'ı da tek komuta bağlamak (şu an ayrı koşuyor, çünkü
  gerçek kernel gerektiriyor)

### Şartname çıkınca ilk bakılacaklar

- **Yerel/çevrimdışı model zorunlu mu?** `llm.py` hazır — `LLM_BASE_URL` ile
  Ollama, LM Studio, vLLM ya da OpenRouter'a bağlanılıyor (ayrıntı:
  [MIMARI.md bölüm 13](MIMARI.md#13-llm-sunucusu-seçenekleri--bulut-mu-yerel-mi)).
  Asıl risk kod değil **model kalitesi**: küçük yerel modeller tool çağırmayı
  ve traceback okuyup düzelmeyi sık sık beceremiyor. 4 GB VRAM ile risk
  yüksek; en az 14B sınıfı ve tool calling'i doğrulanmış bir model hedefle.
  Bunu erken öğrenmek gerekiyor.
- **MCP isteniyor mu?** Şu an yok; handler imzaları (`(session, **args)`)
  sarmalamaya uygun, eklenmesi zor değil.
- **Veri formatı ve boyutu** — 1M satır üstü isteniyorsa örnekleme yolunu
  gerçek veriyle bir kez ölç.
