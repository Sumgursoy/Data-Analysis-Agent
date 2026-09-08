# Agentic Data Analyst — Mimari

> **Proje nedir:** Kullanıcı bir veri kaynağı veriyor (Excel, CSV, SQL
> veritabanı, web sayfası, PDF, hatta hiç tanınmayan bir dosya); sistem onu
> otomatik olarak tanıyor, temizliyor, özetliyor; sonra kullanıcının Türkçe
> sorduğu soruyu **kendi Python ve SQL kodunu yazarak** cevaplıyor, grafik
> çiziyor ve rapor veriyor.
>
> **Değerlendirme biçimi:** jüriye canlı demo. Bu tek cümle mimarinin
> yarısını belirledi — üç şey öncelikli oldu: *şeffaflık* (jüri ne olduğunu
> görsün), *hız* (kimse 40 saniye beklemez), *hatadan toparlanma* (canlı
> demoda bir şey mutlaka ters gider).

---

## Bu dosya nasıl okunur

| Dosya | Neyi anlatır |
|---|---|
| **MIMARI.md** (bu dosya) | Sistem **nasıl tasarlandı**, hangi parça neden var. |
| **YAPILANLAR.md** | **Ne yapıldı, ne çıktı**, hangi hatalar yakalandı. |

Teknik terimlerin çoğu ilk geçtiği yerde bir cümleyle açıklanıyor; uzun
halleri en sonda [Sözlük](#18-sözlük) bölümünde. Yeni başlıyorsan sırayla
oku; belirli bir parçayı arıyorsan içindekilerden git.

---

## İçindekiler

1. [Sistem ne yapıyor — bir örnek üstünden](#1-sistem-ne-yapıyor--bir-örnek-üstünden)
2. [Tasarım ilkeleri](#2-tasarım-ilkeleri)
3. [Genel mimari](#3-genel-mimari)
4. [Klasör yapısı](#4-klasör-yapısı)
5. [Katman A — Kaynak adaptörleri (ingest)](#5-katman-a--kaynak-adaptörleri-ingest)
6. [Katman B — Profiling ve şema kartı](#6-katman-b--profiling-ve-şema-kartı)
7. [Katman C — Katalog](#7-katman-c--katalog)
8. [Katman D — Sandbox](#8-katman-d--sandbox)
9. [Katman E — Agent döngüsü ve tool seti](#9-katman-e--agent-döngüsü-ve-tool-seti)
10. [Katman F — API ve SSE](#10-katman-f--api-ve-sse)
11. [Katman G — Arayüz](#11-katman-g--arayüz)
12. [Teknoloji seçimleri](#12-teknoloji-seçimleri)
13. [LLM sunucusu seçenekleri — bulut mu, yerel mi](#13-llm-sunucusu-seçenekleri--bulut-mu-yerel-mi)
14. [Türkçe veri tuzakları](#14-türkçe-veri-tuzakları)
15. [Güvenlik modeli](#15-güvenlik-modeli)
16. [Çalıştırma ve işletim](#16-çalıştırma-ve-işletim)
17. [Fazlar ve demo kontrol listesi](#17-fazlar-ve-demo-kontrol-listesi)
18. [Sözlük](#18-sözlük)

---

## 1. Sistem ne yapıyor — bir örnek üstünden

Soyut anlatmadan önce somut bir tur. Jüri elinde bir Excel dosyasıyla geliyor.

**1. Dosya yükleniyor.** `satislar_2024.xlsx`. Sistem uzantıya bakmıyor,
dosyanın ilk baytlarına bakıyor (bir xlsx dosyası her zaman `PK\x03\x04` ile
başlar — buna *magic byte* deniyor). Excel olduğunu böyle anlıyor.

**2. Dosya "düzeltiliyor".** Kurumsal Excel dosyaları düz tablo değildir:
üstte iki satır logo boşluğu, altta "GENEL TOPLAM" satırı, birden fazla
sayfa (sheet), birleştirilmiş hücreler. Sistem başlık satırının aslında 4.
satır olduğunu buluyor, toplam satırını veriden ayırıyor, boş sayfayı
atıyor. `"1.234,56 ₺"` metnini `1234.56` sayısına, `"31.12.2024"` metnini
gerçek tarihe çeviriyor.

**3. Veri tek bir formata iniyor.** Kaynak ne olursa olsun sonuç **parquet**
dosyası oluyor (sıkıştırılmış, tip bilgisini saklayan tablo formatı; CSV'ye
göre 5-10 kat küçük ve çok daha hızlı okunur). Bundan sonra sistemin geri
kalanı "bu bir Excel'di" bilgisini hiç bilmiyor.

**4. Veri özetleniyor — asıl numara burada.** Modele ham veri
gösterilmiyor. Bunun yerine bir **şema kartı** üretiliyor: kolon adları,
tipleri, ne kadarının boş olduğu, sayıların dağılımı, 5 örnek satır ve
uyarılar ("tutar kolonunda 115 negatif değer var"). Bu kart yaklaşık 1.500
token; aynı verinin ham hali yüz binlerce token olurdu.

**5. Kullanıcı soruyor:** *"Bölgelere göre ciroyu bul ve grafikle."*

**6. Agent çalışıyor.** Model önce şema kartını istiyor (kolon adı
uydurmamak için), sonra SQL yazıp bölge kırılımını alıyor, sonra Python
yazıp grafiği çiziyor. Yazdığı **kodun tamamı, çıktısı ve varsa hatası**
ekranda canlı akıyor. Kod, backend'in içinde değil; internetsiz, belleği
sınırlı, sadece iki klasörü görebilen bir **Docker container**'ı içinde
çalışıyor.

**7. Sonuç.** Grafik + sayılarla desteklenmiş bulgu listesi + tek tıkla
indirilebilen `.ipynb` (Jupyter notebook), yani analizin baştan
çalıştırılabilir hali. Ölçülen gerçek tur: **4-5 adım, 8-21 saniye.**

Bundan sonraki bölümler bu yedi adımın her birini açıklıyor.

---

## 2. Tasarım ilkeleri

Beş kural. Bir karar bunlardan biriyle çelişiyorsa karar yanlıştır. Bu
ilkeler tartışma sırasında değil, uygulama sırasında işe yaradı — kod
büyüdükçe "burada ne yapmalıyım" sorusunu bunlar cevapladı.

### İlke 1 — Agent kaynak tipini bilmez

Excel mi, Postgres mi, kazınmış bir web tablosu mu — agent için hepsi aynı:
kataloğa kayıtlı bir **Dataset**. Kaynak tipi bilgisi adaptör katmanında
kalır, agent'a sızmaz.

*Neden önemli:* Beklemediğin bir format geldiğinde tek bir adaptör dosyası
yazarsın; prompt'a, tool'lara, döngüye dokunmazsın. Uygulamada gerçekten
böyle oldu: PDF ve ZIP desteği en sonda, sadece iki yeni dosyayla eklendi.

### İlke 2 — Model ham veriyi görmez

Modele giden şey şema kartıdır (bölüm 6). Ham CSV'yi prompt'a dökmek en
yaygın ve en pahalı hatadır: hem para yakar, hem model uzun listede
kaybolur, hem de context penceresi dolduğunda analiz yarıda kalır.

### İlke 3 — Tek güçlü tool, çok sayıda dar tool değil

`compute_correlation`, `plot_histogram`, `groupby_agg` gibi 40 ayrı tool
yazmadık. Böyle bir tasarımda model, listede karşılığı olmayan bir soruyla
karşılaşınca duvara toslar. Bunun yerine **kalıcı bir Python kernel'ı**
(içinde değişkenlerin yaşadığı, Jupyter gibi çalışan bir Python oturumu) +
birkaç dar yardımcı tool var. Model ne isterse yazabiliyor.

### İlke 4 — Her şey şeffaf

Agent'ın yazdığı kod, aldığı çıktı, yaptığı hata ve düzeltmesi kullanıcıya
canlı akar. Jüri değerlendirmesinde en çok fark yaratan şey bu.
**Hataları gizleme** — hata yapıp kendini düzelttiğini görmek güven artırır;
gizlemek "acaba başka ne gizliyor" sorusunu doğurur.

### İlke 5 — Analiz birikimlidir

Agent temizlenmiş bir tablo ürettiğinde onu kataloğa yazar (`add_dataset`).
Sonraki sorular sıfırdan başlamaz, üstüne inşa eder. Büyük SQL sonuçları da
otomatik olarak yeni birer veri seti olur.

---

## 3. Genel mimari

```
 KAYNAK                    ADAPTÖR                KANONİK KATMAN          AGENT
─────────────────────────────────────────────────────────────────────────────────
 CSV / TSV           ─┐
 Excel (xlsx/xls…)   ─┤
 JSON / JSONL        ─┤    ingest/adapters/*      data/*.parquet
 ZIP / arşiv         ─┼──→   sniff                       +           ──→  katalog
 Web sayfası (HTML)  ─┤      extract               catalog.json           + şema
 SQL veritabanı      ─┤      normalize                   +               kartları
 PDF tablosu         ─┘      materialize          schema_card                │
                                                                             │
 Tanınmayan format   ────→  raw passthrough  ────────────────────────────────┤
                            (agent kendi parser'ını yazar)                   │
                                                                             ▼
                                          ┌──────────────────────────────────────┐
                                          │            AGENT DÖNGÜSÜ             │
                                          │   ReAct + hata düzeltme + bütçe      │
                                          └──────────────────────────────────────┘
                                                   │                    │
                                        run_sql (DuckDB)        run_python (sandbox)
                                          backend'de              container'da
                                                   │                    │
                                                   └────────┬───────────┘
                                                            ▼
                                              SSE akışı → Arayüz
                                              (kod · çıktı · grafik · rapor)
```

**Akışın özeti:** Kaynak ne olursa olsun adaptörden geçer, parquet'e iner,
profillenir, kataloğa girer. Agent sadece kataloğu görür ve iki motorla
çalışır: DuckDB (SQL) ve sandbox içindeki Python kernel'ı.

**Neden iki motor?** SQL büyük veriyi ucuza daraltmak için (3 milyon satırı
Python'a çekmek yerine önce grupla), Python derin analiz ve grafik için.
Agent prompt'ta bu sıraya yönlendiriliyor: *önce daralt, sonra derinleş.*

**SSE nedir:** *Server-Sent Events* — sunucudan tarayıcıya tek yönlü canlı
veri akışı. WebSocket'e göre çok daha basit; burada iki yönlü konuşmaya
ihtiyaç yok, sadece "her adımı anında ekrana bas" gerekiyor.

---

## 4. Klasör yapısı

Aşağıdaki ağaç **projenin gerçek hali** (plan değil).

```
Data Analysis/
├── .env                        # API anahtarı ve ayarlar — asla paylaşma
├── .env.example                # şablon
├── run_dev.py                  # geliştirme sunucusu (bkz. bölüm 16)
├── MIMARI.md                   # bu dosya
├── YAPILANLAR.md               # inşa günlüğü
│
├── backend/
│   ├── main.py                 # FastAPI uygulaması, router'lar, loglama
│   ├── config.py               # TÜM ayarlar tek yerde
│   ├── query.py                # DuckDB motoru + SQL güvenlik denetimi
│   ├── requirements.txt
│   │
│   ├── api/                    # dış dünyaya açılan HTTP uçları
│   │   ├── health.py           # "her şey ayakta mı"
│   │   ├── sessions.py         # oturum aç/kapat, katalog, şema kartı
│   │   ├── sources.py          # dosya / URL / veritabanı / örnek veri ekleme
│   │   ├── chat.py             # POST /api/chat → SSE akışı + trace okuma
│   │   ├── artifacts.py        # üretilen grafikleri servis eder
│   │   └── export.py           # analizi .ipynb olarak indir
│   │
│   ├── agent/                  # karar veren kısım
│   │   ├── llm.py              # LLM soyutlaması (chat / responses)
│   │   ├── loop.py             # ReAct döngüsü, bütçe, hata düzeltme
│   │   ├── tools.py            # 7 tool şeması + handler'ları
│   │   ├── prompts.py          # sistem prompt'u
│   │   ├── events.py           # SSE event tipleri
│   │   └── session.py          # oturum bağlamı + trace.jsonl
│   │
│   ├── ingest/                 # veriyi içeri alan boru hattı
│   │   ├── router.py           # sniff → doğru adaptör → parquet → katalog
│   │   ├── normalize.py        # kolon adı, tip, Türkçe sayı/tarih
│   │   ├── profile.py          # şema kartı üretimi
│   │   ├── catalog.py          # Dataset kayıtları (catalog.json)
│   │   ├── sample.py           # demo için hazır örnek veri üretici
│   │   └── adapters/
│   │       ├── base.py         # ortak tipler (Extracted, UnsupportedSource)
│   │       ├── tabular.py      # csv, tsv, json, jsonl, parquet
│   │       ├── excel.py        # xlsx/xls/xlsb/ods + başlık tespiti
│   │       ├── sql.py          # SQLAlchemy şema keşfi, DSN maskeleme
│   │       ├── web.py          # DOM haritası + tablo çıkarma
│   │       ├── pdf.py          # pdfplumber ile tablo çıkarma
│   │       ├── archive.py      # zip → çoklu dataset (özyinelemeli)
│   │       └── raw.py          # kaçış kapısı (hex önizleme)
│   │
│   ├── sandbox/                # kodun güvenle koştuğu yer
│   │   ├── base.py             # PipeKernel — ortak protokol gövdesi
│   │   ├── docker_kernel.py    # asıl sandbox
│   │   ├── local_kernel.py     # subprocess yedeği (izole değil)
│   │   ├── manager.py          # yaşam döngüsü, prewarm, hata teşhisi
│   │   └── protocol.py         # stdin/stdout JSON protokolü + sürüm
│   │
│   ├── fetch/client.py         # httpx + playwright, SSRF koruması, robots
│   │
│   └── storage/
│       ├── app.log             # dönen log dosyası (dosyada her zaman DEBUG)
│       └── sessions/<id>/
│           ├── raw/            # yüklenen orijinal dosyalar
│           ├── data/           # parquet — sandbox'a salt-okunur bağlanır
│           ├── artifacts/      # grafikler — sandbox'a yazılabilir bağlanır
│           ├── catalog.json    # Dataset kayıtları
│           └── trace.jsonl     # her adım (.ipynb export'un kaynağı)
│
├── sandbox_image/              # container imajının içeriği
│   ├── Dockerfile
│   ├── requirements.txt        # SADECE analiz kütüphaneleri
│   ├── kernel_server.py        # container içinde koşan kalıcı REPL
│   └── analyst.mplstyle        # tek grafik stili
│
├── tests/
│   ├── run_all.py              # hepsini koştur → 147 kontrol
│   ├── test_*.py               # normalize · ingest · agent · sql · web · export
│   ├── test_grafik_bicim.py    # AYRI koşar (kernel gerektirir)
│   ├── smoke_sandbox.py        # AYRI koşar (Docker izolasyon sınırları)
│   └── veri/                   # kasten bozuk test dosyaları
│
└── frontend/src/
    ├── App.tsx                 # akış durumu, event → ekran
    ├── api.ts                  # fetch + SSE ayrıştırma
    ├── styles.css
    └── components/
        ├── DatasetPanel.tsx    # veri setleri, kaynak ekleme, şema kartı
        ├── ToolCallCard.tsx    # kod + çıktı bloğu
        └── NotebookPanel.tsx   # biriken kod + .ipynb indirme
```

**Önemli ayrım:** `backend/requirements.txt` ile
`sandbox_image/requirements.txt` **ayrıdır**. Sandbox imajında OpenAI
SDK'sının ve FastAPI'nin işi yok; backend'de scikit-learn'e gerek yok.
Karıştırılırsa imaj gereksiz şişer ve container açılışı yavaşlar.

---

## 5. Katman A — Kaynak adaptörleri (ingest)

*Ingest = veriyi sisteme alma.* Altı adımlık bir huni; her kaynak aynı
adımlardan geçer ve aynı biçimde çıkar.

```
sniff  →  extract  →  normalize  →  materialize  →  profile  →  catalog
tanı      ayıkla      düzelt        parquet'e yaz    özetle     kaydet
```

### Adım 0 — Alım

Kaynak tipine göre ayrı uçlar var, ama hepsi aynı huniye bağlanıyor:

```
POST /api/sessions/{id}/sources/file      → dosya yükleme (multipart)
POST /api/sessions/{id}/sources/url       → web sayfası
POST /api/sessions/{id}/sources/sql       → veritabanı (DSN + tablo listesi)
POST /api/sessions/{id}/sources/sample    → hazır demo verisi
```

### Adım 1 — Tanıma (sniff)

**Uzantıya güvenme, magic byte'a bak.** Yarışmada `.txt` uzantılı bir Excel
veya uzantısız bir parquet gelebilir; uzantı bir dosya adı, magic byte bir
gerçektir.

```python
IMZALAR = [
    (b"PK\x03\x04",          "zip_family"),   # xlsx, ods, docx → içine bakılır
    (b"PAR1",                "parquet"),
    (b"SQLite format 3\x00", "sqlite"),
    (b"\xd0\xcf\x11\xe0",    "xls"),          # eski OLE2 Excel
    (b"%PDF",                "pdf"),
    (b"\x1f\x8b",            "gzip"),
]
```

İmza eşleşmezse ilk 8 KB çözülüp içeriğe bakılıyor: JSON mu (`{`/`[` ile
başlıyor mu), HTML mi (`<html`, `<table` geçiyor mu), sınırlandırılmış metin
mi (satırlar arasında tutarlı sayıda ayıraç var mı).

### Adım 2 — Ayıklama (extract)

**Excel — en çok burada patlanır.** Üç heuristik:

| Problem | Çözüm |
|---|---|
| Çoklu sheet | Her sheet ayrı dataset adayı; boş ve 2 satırdan küçük olanlar elenir. |
| Başlık 1. satırda değil | İlk 20 satır puanlanır: "en çok dolu + en çok metin + altındaki satırlarla tip tutarsızlığı en yüksek" olan satır başlık seçilir. |
| Alt toplam satırı | İlk kolonda `TOPLAM / GENEL TOPLAM / ARA TOPLAM` kalıbı yakalanır → veriden ayrılır, şema kartına not düşülür. |

**Web — ucuzdan pahalıya sıralı.** Agent'a prompt'ta bu sıra öğretiliyor:

1. `pandas.read_html` — sayfada gerçek bir `<table>` varsa iş bir satırda biter
2. `selectolax` + CSS seçici — yapısal ama tablo olmayan içerik
3. Sayfanın arkada çektiği JSON API — genelde en temiz veri
4. `render=true` → Playwright ile tarayıcı açma — sadece içerik JavaScript
   olmadan gelmiyorsa (pahalı, son çare)

**SQL —** SQLAlchemy'nin `inspect`'i ile şema keşfediliyor (tablolar,
kolonlar, anahtarlar), sonra tablolar parquet'e indiriliyor.

**PDF —** iki strateji: önce çizgilere göre tablo tespiti, olmazsa metin
hizasına göre (kurumsal PDF'lerin çoğunda tablo çerçevesi yoktur). Çıkan her
tabloya "el ile doğrula" notu düşülüyor; PDF tabloları güvenilmezdir.

**ZIP —** her üye dosya normal huniden **yeniden** geçiyor (özyineleme).
Zip içindeki Excel de, CSV de, tanınmayan dosya da aynı muameleyi görüyor.

### Adım 3 — Normalize

Bu adım atlanırsa agent doğru kodu yazar ama **yanlış sonuç** alır
(ayrıntı: [bölüm 14](#14-türkçe-veri-tuzakları)).

```python
# Kolon adları
"Müşteri No "     → "musteri_no"
"TOPLAM TUTAR(₺)" → "toplam_tutar"

# Değerler
"1.234,56"        → 1234.56       # binlik nokta, ondalık virgül
"%12,5"           → 0.125
"1.234,56 ₺"      → 1234.56
"(1.250)"         → -1250.0       # muhasebe negatifi
"31.12.2024"      → datetime(2024, 12, 31)
```

Tip kararı **kolonun tamamına bakılarak, çoğunluk kuralıyla** veriliyor;
satır satır değil. Tamamen boş satır ve kolonlar atılıyor, tek değerli
(sabit) kolonlar işaretleniyor. Kimlik kolonları `Int64` olarak tutuluyor —
aksi halde `1001` yerine `1001.0` yazılıp join'ler ve raporlar bozuluyor.

### Adım 4 — Materyalize

Sonuç `data/<ad>.parquet` olarak yazılıyor.

> **1 milyon satırdan büyükse** ayrıca `data/<ad>__sample.parquet` (100k
> satır) yazılıyor. Profiling ve agent'ın ilk denemeleri örneklem üstünde
> koşuyor — hız demoda puan getirir. Şema kartında "ÖRNEKLEM" uyarısı
> duruyor; agent kesin sonuç için tam dosyayı okuyor.

### Adım 5-6 — Profil ve katalog

Bkz. [bölüm 6](#6-katman-b--profiling-ve-şema-kartı) ve
[bölüm 7](#7-katman-c--katalog).

### Kaçış kapısı — projenin en kritik 100 satırı

Hiçbir adaptör eşleşmezse dosya olduğu gibi kopyalanıyor, kataloğa
`kind="raw"` olarak giriyor ve şema kartı yerine şu veriliyor:

```
kayit.dat — 4,2 MB, tanınmayan format
  İlk 500 byte (hex + ascii dökümü): ...
  Tahmini satır sayısı: 12.400
  Tekrar eden karakter kalıpları: '|' (satır başına ~7)
```

Agent kendi parser'ını `run_python` içinde yazıyor. Sabit genişlikli metin,
log dosyası, egzotik ayıraç, hiç görülmemiş uzantı — hepsi bu kapıdan
geçiyor. **"Bilinmeyen veri tipi" riskini sıfıra indiren tek mekanizma bu.**

---

## 6. Katman B — Profiling ve şema kartı

*Profiling = veriyi tarayıp özetini çıkarma.* Burada LLM kullanılmıyor;
tamamen deterministik kod, hedef süre 300 milisaniyenin altı.

> `ydata-profiling` bilerek **kullanılmadı**: yavaş, devasa bir HTML raporu
> üretiyor ve token'a çevrilebilir bir çıktı vermiyor. Buradaki amaç insanı
> etkilemek değil, modelin doğru kodu ilk seferde yazması.

### Üretilen çıktı

```
sales — 1.240.117 satır × 18 kolon   (kaynak: satislar_2024.xlsx → Sheet1)

  order_date    datetime   boş %0.0    2021-01-03 → 2024-11-28  (günlük, 3 boşluk)
  region        category   boş %0.0    6 uniq: Marmara, Ege, İç Anadolu, …
  revenue       float      boş %2.1    p50 412  p90 3.100  p99 18.240
                                       sağa çarpık (skew 4,2), 34 aykırı değer
  customer_id   int        boş %0.0    84.102 uniq (tekil) ← anahtar adayı
  status        category   boş %0.0    3 uniq: tamamlandı %88, iptal %9, beklemede %3
  notes         text       boş %71.3   serbest metin, ort. 84 karakter

  İlk 5 satır:
    2021-01-03 | Ege | 1250.00 | 40021 | tamamlandı | NULL
    ...

  ⚠ Uyarılar:
    - `notes` kolonu %71 boş
    - `revenue` içinde 12 negatif değer var (iade olabilir)
    - `order_date` 2023-07 ayında hiç kayıt yok

  🔗 İlişki adayları:
    sales.customer_id ⟷ customers.id   (%99,4 değer örtüşmesi)
```

**Buradaki her satırın bir işlevi var.** `p50/p90/p99` (yüzdelikler) modele
dağılımın şeklini söyler — çarpık veride ortalama yanıltır, medyan gerçeği
anlatır. "Uyarılar" bölümü olmasa agent negatif tutarları fark etmez.
"İlişki adayları" olmasa join'i tahmin etmeye çalışır.

### Kolon başına hesaplananlar

| Alan | Detay |
|---|---|
| tip | Gerçek tip çıkarımı — pandas'ın `object` dediği kolon aslında tarih olabilir |
| boş % | Tamamen boşsa özel işaret |
| kardinalite | Az sayıda farklı değer → kategori, değerleri listele. Çok → anahtar adayı |
| sayısal özet | min, p25, p50, p75, p90, p99, maks, çarpıklık, aykırı değer sayısı |
| tarih özet | aralık, frekans (günlük/aylık), boşluklar |
| metin özet | ortalama/maksimum uzunluk, örnek değerler |
| uyarılar | sabit kolon, negatif değer, aşırı boşluk, tekil olmayan "id" |

Çok geniş tablolarda (60+ kolon) kart bilerek kısaltılıyor; yoksa kartın
kendisi context'i doldurmaya başlıyor.

### İlişki adayı tespiti (çok tablolu veride kritik)

İki sinyalin kesişimi kullanılıyor:

1. **Ad benzerliği** — `customer_id` ↔ `id`, `musteri_no` ↔ `no`
2. **Değer örtüşmesi** — iki kolondan örnek alınıp kesişim oranı hesaplanıyor

Ayrıca bir tarafın gerçek anahtar (tekil) olması şart. Keyfi bir eşik yerine
bu kural kullanılıyor: hem küçük tabloları yakalıyor hem uydurma join
önermiyor. **Join'i modele tahmin ettirme** — en sık ve en sessiz hata
kaynağı budur.

---

## 7. Katman C — Katalog

Katalog, oturumdaki tüm veri setlerinin kaydı (`catalog.json`). Dosya da,
SQL tablosu da, kazınmış web tablosu da, agent'ın kendi ürettiği tablo da
aynı yapıda duruyor:

```python
@dataclass
class Dataset:
    name: str          # "satis"
    kind: str          # "file" | "sql_table" | "web" | "pdf" | "raw" | "derived"
    location: str      # "/data/satis.parquet"
    row_count: int | None
    col_count: int | None
    schema_card: str   # bölüm 6'daki metin
    origin: str        # "satislar_2024.xlsx → Sheet1"
    summary: str       # tek satırlık özet
    sampled: bool      # örneklem mi, tam veri mi
    notes: list[str]   # ingest sırasında düşülen uyarılar
    created_by: str    # "ingest" | "agent"
```

`catalog.json` atomik yazılıyor (önce geçici dosya, sonra yer değiştirme) ve
Windows/OneDrive'da dosya kilitlendiğinde kısa aralıklarla birkaç kez
deneniyor — bu gerçekten yaşandı, bkz. YAPILANLAR.md hata #23.

### Prompt bütçesi kuralı

Sistem prompt'unda **sadece isim + tek satırlık özet** duruyor:

```
Mevcut veri setleri:
  satis      — 6.000 satır × 9 kolon, 2024 satış kayıtları
  musteriler — 240 satır × 5 kolon, müşteri ana verisi
```

Detaylı şema kartı ancak `get_schema(ad)` çağrıldığında geliyor.

*Neden:* 20 tablolu bir veritabanında tüm şemayı prompt'a basarsan hem her
istekte para yakarsın hem model dağılır. Neye bakacağını model seçsin.

---

## 8. Katman D — Sandbox

### Sandbox nedir, neden var

Agent Python kodu yazıyor; birinin bu kodu **çalıştırması** lazım. Backend
sürecinde `exec()` demek şu risklerin hepsini açar:

| Ne olabilir | Sonuç |
|---|---|
| `while True:` | Backend donar, demo biter |
| 5 GB dosyayı belleğe alır | Makine kilitlenir |
| Yanlış yola `shutil.rmtree()` | Dosya kaybı |
| `open(".env").read()` → cevaba basar | API anahtarı ekranda |
| `pip install` | Ortam bozulur |

Model saldırmıyor; **kaza yeterince olası.** Sandbox = kodun yalıtılmış bir
kutuda koşması. Bu, güvenlik kadar **çökme yalıtımı** meselesi: içerideki
kod ne yaparsa yapsın backend ayakta kalıyor.

### Docker yapılandırması

```bash
docker run -i --rm \
  --name sandbox-<session_id> \
  --network none \                    # internet yok → veri sızmaz, anahtar güvende
  --memory 2g --memory-swap 2g \      # RAM tavanı
  --cpus 2 \
  --pids-limit 128 \                  # fork bomb koruması
  --read-only \                       # kök dosya sistemi salt-okunur
  --tmpfs /tmp:size=512m \            # tek yazılabilir geçici alan
  --user 1000:1000 \                  # root değil
  --security-opt no-new-privileges \
  -v <session>/data:/data:ro \        # veri — salt okunur
  -v <session>/artifacts:/artifacts:rw \
  analyst-sandbox:latest
```

```
  SENİN MAKİNEN                    │   CONTAINER (sandbox)
  ─────────────────────────────────┼──────────────────────────────
  backend, .env, API anahtarı,     │   python + pandas + duckdb …
  bütün diskin, internet           │   /data      (salt okunur)
                                   │   /artifacts (yazılabilir)
                                   │   internet YOK
                                   │   maks 2 GB RAM
                                   │   kod başına 30 sn timeout
```

### Kalıcı kernel — neden şart

*Kernel = container içinde ayakta duran, sıradaki kodu bekleyen Python
süreci.* Her kod parçası için yeni bir Python açsaydık:

```python
# 1. çağrı:  df = pd.read_parquet(...)
# 2. çağrı:  df.head()   →  NameError: name 'df' is not defined
```

Bu yüzden container ayakta kalıyor ve içindeki Python oturumu değişkenleri
koruyor. Jupyter'da hücreleri sırayla çalıştırmakla aynı davranış.

### Protokol

Backend ile container `stdin`/`stdout` üzerinden, satır başına bir JSON ile
konuşuyor:

```jsonc
// backend → container
{"id": "c7", "code": "df.groupby('bolge').tutar.sum()"}

// container → backend
{"id": "c7", "ok": true,
 "stdout": "Marmara  4235120.5\nEge  2811004.0\n…",
 "result_repr": "bolge\nMarmara  4235120.5\n…",
 "new_artifacts": ["grafik_03.png"],
 "duration_ms": 412}

// hata durumunda
{"id": "c8", "ok": false,
 "error_type": "KeyError",
 "traceback": "Traceback (most recent call last):\n  …"}
```

`kernel_server.py` her kod bloğunu aynı global sözlükte çalıştırıyor,
`stdout`/`stderr`'i yakalıyor, `/artifacts` klasörünü çalıştırma
öncesi/sonrası karşılaştırıp yeni grafikleri tespit ediyor.

### Kernel'ın hazır verdikleri

```python
data_path('satis.parquet')      # salt-okunur veri klasörü
artifact_path('grafik.png')     # yazılabilir çıktı klasörü
tr_sayi(1234567.5, 1)           # → '1.234.567,5'
tr_eksen(ax, 'y')               # ekseni Türkçe biçime çevirir
```

Ayrıca `savefig` **kancalanmış** durumda: her grafik kaydedildiğinde sayısal
eksenler otomatik olarak Türkçe ve kısa biçime çevriliyor (`500 bin`,
`4 mn`). Modele "şu yardımcıyı kullan" demenin yetmediği ölçüldüğü için
böyle yapıldı — bkz. YAPILANLAR.md hata #19. Görünüm altyapının işi, modelin
değil.

### Dosya yolları — sabit yol YOK

> **Plandan sapma (uygulamada çıktı).** Şema kartları başta `/data/x.parquet`
> yazıyordu. Bu yol yalnızca Docker'da var; `SANDBOX_BACKEND=local` yedeğine
> düşünce her `run_python` patlıyordu — yani yedek tam ihtiyaç duyulan anda
> çalışmayacaktı. Soyutlama sızmıştı.

Çözüm yukarıdaki iki yardımcı: Docker'da `/data` ve `/artifacts`, yerel
yedekte gerçek host klasörleri. **Aynı kod iki modda da çalışıyor.**

### Soğuk başlangıç

Container'ın ilk açılışı 3-8 saniye sürüyor. Jüri ilk mesajı yazdığında
bunu beklerse izlenim bozulur.

> **Plandan sapma.** İlk plan "önceden açılmış container havuzu" (warm pool)
> öngörüyordu. Uygulanabilir değil: her container o oturumun `data/` ve
> `artifacts/` klasörlerine bağlanmak zorunda. Oturumdan bağımsız açılmış
> bir container ya hiçbir veriyi görür ya hepsini — ikincisi oturumlar arası
> **veri sızıntısı** demek.

Yerine konan çözüm aynı faydayı veriyor, izolasyonu bozmadan:

1. **Kernel, oturum açılır açılmaz başlıyor** — sohbetin ilk mesajında
   değil. Jüri dosyayı yüklerken container zaten ayağa kalkıyor.
2. **Açılışta bir kez `prewarm()`** — mount'suz, hemen ölen tek bir
   container çalıştırıp Docker'ın imaj katmanlarını ısıtıyor
   (`SANDBOX_PREWARM=true`). Bu bir optimizasyon; başarısız olursa
   uygulamayı **asla** düşürmüyor.

### Yedek mod

```python
SANDBOX_BACKEND = os.getenv("SANDBOX_BACKEND", "docker")   # docker | local
```

Sunum makinesinde Docker takılırsa tek satırla subprocess moduna
düşülüyor. İzolasyon yok ama analiz çalışıyor. İki backend de aynı
`PipeKernel` gövdesini paylaşıyor: protokol, zaman aşımı, çökme toparlama,
kilitleme tek yerde. Böylece "iki mod aynı davranır" bir temenni değil,
yapısal garanti.

### Bayat imaj koruması

`kernel_server.py` imaja **kopyalanıyor**. Dosyayı değiştirip imajı yeniden
build etmezsen container eski sürümü çalıştırır ve sessizce yanlış davranır
— bulunması en zor hata türü. Bunu yakalamak için kernel açılışta protokol
sürümünü bildiriyor (`PROTOCOL_VERSION`, şu an **6**); backend uyuşmazlığı
görürse net bir hata veriyor:

```
Sandbox imajı güncel değil (imaj protokolü v5, beklenen v6). Yeniden build et:
  docker build -t analyst-sandbox:latest ./sandbox_image
```

`kernel_server.py`'a dokunduğunda **sürümü artır.**

---

## 9. Katman E — Agent döngüsü ve tool seti

### Önce kavram: "agent" ne demek

Model tek başına hiçbir şey çalıştırmaz. Yaptığı tek şey **"şu tool'u şu
argümanlarla çağır" diyen bir JSON üretmek** — bir niyet beyanı. Çağrıyı
bizim kodumuz (`tools.py`) yapıyor, sonucu modele geri veriyoruz, model
sonuca bakıp bir sonraki adıma karar veriyor. Bu döngüye **ReAct** deniyor
(*Reason + Act*: düşün → bir şey yap → sonucu gör → tekrar düşün).

Baştan tam plan yapmak yerine adım adım ilerlemek veri analizinde doğru
desendir, çünkü bir sonraki adım önceki adımın çıktısına bağlıdır: kolonun
tipini görmeden hangi grafiği çizeceğini bilemezsin.

### Tool seti — 7 tane, birbirine dik

```python
list_datasets()                        # katalogtaki veri setleri
get_schema(name)                       # detaylı şema kartı
run_sql(query, save_as=None)           # DuckDB — backend'de koşar
run_python(code)                       # sandbox'taki kalıcı kernel
fetch_url(url, render=False)           # backend'de koşar, /data'ya yazar
add_dataset(filename, name, note)      # türetilmiş veriyi kataloğa ekle
finish(summary)                        # nihai rapor
```

"Birbirine dik" = işlevleri çakışmıyor, her biri diğerinin yapamadığını
yapıyor. Yedi tool bir modelin rahatça hatırlayabileceği bir sayı.

**`run_sql` neden sandbox'ın dışında?**
Sandbox `--network none` ile koşuyor; oradan veritabanına bağlanılamaz ve
bağlanılmamalı. Bu yüzden SQL backend'de çalışıyor:

- Her veri seti kendi adıyla sorgulanıyor (`SELECT * FROM satis`); DuckDB
  view'ları otomatik kuruluyor, agent dosya yolu bilmek zorunda değil
- Sorgu `sqlglot` ile **parse edilip** ifade tipi doğrulanıyor
- Limit koyulmamışsa otomatik `LIMIT 1000` ekleniyor
- Sonuç küçükse (≤200 satır) tablo doğrudan modele dönüyor; büyükse
  parquet'e yazılıp **kataloğa yeni bir veri seti olarak** giriyor ve modele
  *"kaydettim, şu adla oku"* deniyor

Bu, agent'ı doğal olarak **"SQL ile daralt → Python ile derinleş"** desenine
itiyor. Analitik olarak doğru, token açısından ucuz, jüriye anlatması kolay.

**`fetch_url` de aynı sebeple backend'de.** Sayfayı indirip `/data`'ya
yazıyor ve modele HTML'i değil bir **DOM haritası** dönüyor:

```
page_01.html — 412 KB
  <table> × 3   → satır sayıları: 1 (başlık), 240, 12
  <ul>    × 8
  Ana metin (trafilatura ile): 4.200 karakter
  1. tablo başlıkları: Tarih | Şehir | Tutar | Durum
  Sayfanın çektiği XHR uçları: /api/v2/records?page=1  (JSON, 240 kayıt)
```

Ham HTML'i modele basmak 100k token yakar ve modeli dağıtır. Ölçüldü:
sayfa 100 katına çıkarıldığında (843 bayt → 122 KB) harita 2 KB'da kaldı.

**`add_dataset` neden kritik:** agent temizlenmiş bir tablo ürettiğinde onu
kataloğa yazıyor; sonraki adımlar SQL ile üstünde çalışıyor. İlke 5 böyle
hayata geçiyor.

### Döngünün gerçek akışı

```python
async def run(session, user_message, backend):
    mesajlar = [sistem_prompt + katalog_ozeti] + gecmis + [user_message]
    ardisik_hata = 0

    for adim in range(1, config.MAX_ITERATIONS + 1):        # 25
        sonuc = await backend.complete(mesajlar, tools=TOOLS)

        if sonuc.text:                       # modelin düz metni
            yield thinking(...) if sonuc.tool_calls else message(...)

        if not sonuc.tool_calls:             # tool istemiyorsa cevap veriyor
            yield done(...); return

        for cagri in sonuc.tool_calls:
            yield tool_call(cagri)           # KODU ANINDA GÖSTER
            tool_sonuc = await dispatch(session, cagri)
            yield tool_result(...) veya tool_error(...)
            mesajlar.append(tool_sonuc)

            if tool_sonuc.basarisiz:
                ardisik_hata += 1
                if ardisik_hata >= 3:        # aynı duvara toslamayı kes
                    yield status("kernel sıfırlanıyor…")
                    await kernel.restart()
                    mesajlar.append(KURTARMA_NOTU)
                    ardisik_hata = 0
            else:
                ardisik_hata = 0

        if session.finished:                 # finish tool'u çağrıldı
            yield message(ozet); yield done(...); return

    yield done(reason="iteration_budget_exhausted")
```

Dikkat edilecek üç nokta:

**1. Bütçe (`MAX_ITERATIONS = 25`).** Sonsuz döngü koruması. Hata düzeltme
turları da bu bütçeden yiyor, o yüzden dar tutulmuyor. Bütçe bitince
kullanıcıya açıkça söyleniyor ("soruyu daraltarak tekrar sor").

**2. Üç ardışık hatada kurtarma.** Kernel yeniden başlatılıyor ve modele
*"tüm değişkenler silindi, aynı yolu deneme, önce `get_schema` ile doğrula"*
notu gidiyor. Modeller aynı hatayı tekrarlamaya meyillidir; bu not döngüyü
kırıyor.

**3. Altyapı hatası ölümcül sayılıyor.** Docker kapalıysa model bunu kod
yazarak çözemez. `SandboxUnavailable` yakalanınca döngü 25 iterasyon
yakmak yerine **duruyor** ve eyleme dönük bir mesaj veriyor
("Docker Desktop çalışmıyor olabilir; ya başlat ya `SANDBOX_BACKEND=local`").

### Hata düzeltme

Traceback modele **kısaltılmadan** gidiyor. Modeller traceback okumakta iyi;
özetlemeye çalışırsan bilgi kaybedersin. Sadece çok uzunsa (>4000 karakter,
`MAX_TOOL_OUTPUT_CHARS`) ortası kırpılıyor, baş ve son korunuyor — asıl hata
sonda olur.

### LLM soyutlama katmanı

`agent/llm.py` döngü ile model sunucusu arasında duruyor. Döngü sadece şunu
görüyor:

```python
class LLMBackend(Protocol):
    async def complete(self, messages, tools=None) -> LLMResult: ...
```

İki uygulama var:

| Uygulama | Uç | Kim konuşur |
|---|---|---|
| `ChatCompletionsBackend` | `/v1/chat/completions` | Evrensel standart — Ollama, vLLM, LM Studio, OpenRouter, OpenAI |
| `ResponsesBackend` | `/v1/responses` | OpenAI'a özel |

Varsayılan kaynağa göre seçiliyor: OpenAI'da `responses`, `LLM_BASE_URL`
doluysa `chat`. Sebebi ölçülerek bulundu (YAPILANLAR.md hata #16):
`gpt-5.6` ailesi chat ucunda function tool ile akıl yürütmeyi **birlikte**
kabul etmiyor, HTTP 400 veriyor. Kod yazıp hatasını düzelten bir agent'ta
akıl yürütmeyi kapatmak kabul edilemez.

Bu katmanın değeri: sunucu değiştirmek `loop.py`, `tools.py`, `prompts.py`
dosyalarına dokunmayı gerektirmiyor. Bkz. [bölüm 13](#13-llm-sunucusu-seçenekleri--bulut-mu-yerel-mi).

### Sistem prompt'unun çekirdeği

Tek bir kural her şeyden çok fark yaratıyor:

> **Bir kolonun varlığını veya tipini asla varsayma. Önce `get_schema` çağır.**

Buna ek olarak prompt'ta duran ve **hepsi gerçek bir hatadan doğmuş**
kurallar:

- **Aritmetiği kafadan yapma, koda yaptır ve çıktısını gör.** Bir turda
  kafadan hesaplanan pay %39,2 çıktı; kodda hesaplanan doğru değer %42,2'ydi.
  Rapordaki tek yanlış sayı tüm analizin güvenilirliğini götürür.
- **Kırılımları hedefe göre koşullu oran ver.** "Hedefi seçenlerin %98'inde
  şu özellik var" değil, "**özellik olanların %27,5'i seçiyor, olmayanların
  %0,6'sı**". Ters yön kulağa çarpıcı gelir ama karar verdirmez.
- **Kolon adını yazmadan önce şema kartında gör.** Bir kolonun sayısını
  başka bir kolona atfetme; sayısal bir kolona "High/Low" gibi kategori adı
  uydurma.
- Grafiklerde eksen biçimlendirmesine karışma (otomatik), başlık ve birim
  koy, `plt.close(fig)` çağır.
- Türkçe, kısa, net konuş; sayıları binlik ayıraçla yaz; varsayımını açıkça
  söyle; bitince `finish` çağır.

### Reprodüksiyon — `trace.jsonl`

Kernel'a giden her kod, her çıktı, her grafik ve her SQL adımı
`trace.jsonl` dosyasına yazılıyor:

```jsonc
{"ts": "…", "type": "code", "code": "df = pd.read_parquet(…)"}
{"ts": "…", "type": "stdout", "text": "…"}
{"ts": "…", "type": "sql", "query": "SELECT …", "rows": 6}
{"ts": "…", "type": "artifact", "path": "grafik_01.png"}
```

`.ipynb` export'u buradan **bedava** geliyor. Sonradan eklemeye kalksan
çalışan kodu geri toplamak neredeyse imkânsız olurdu. Çoğu yarışmacı
reprodüksiyon sunmaz; "sonuç doğrulanabilir" diyebilmek ciddi fark yaratır.

---

## 10. Katman F — API ve SSE

### Uçlar (gerçek liste)

| Method | Yol | İş |
|---|---|---|
| `GET` | `/api/health` | model, anahtar durumu, sandbox backend, Docker durumu, engeller |
| `POST` | `/api/sessions` | yeni oturum (+ kernel'ı arka planda başlatır) |
| `DELETE` | `/api/sessions/{id}` | oturumu kapat, kernel'ı durdur |
| `GET` | `/api/sessions/{id}/catalog` | veri seti listesi + prompt bloğu |
| `GET` | `/api/sessions/{id}/schema/{ad}` | tek bir şema kartı |
| `POST` | `/api/sessions/{id}/sources/file` | dosya yükle |
| `POST` | `/api/sessions/{id}/sources/sample` | hazır örnek veri |
| `POST` | `/api/sessions/{id}/sources/url` | web sayfası ekle |
| `POST` | `/api/sessions/{id}/sources/sql/discover` | veritabanındaki tabloları listele |
| `POST` | `/api/sessions/{id}/sources/sql` | seçilen tabloları indir |
| `POST` | `/api/chat` | **SSE akışı** — agent döngüsü |
| `GET` | `/api/sessions/{id}/trace` | biriken adımlar (defter paneli) |
| `GET` | `/api/artifacts/{id}/{dosya}` | grafik servisi |
| `GET` | `/api/export/{id}.ipynb` | notebook indir |

### SSE event şeması — demonun kalbi

Agent döngüsü HTTP bilmiyor; sadece event üretiyor. `api/chat.py` bunları
SSE satırlarına çeviriyor. Gerçek event tipleri:

```jsonc
{"type": "status",        "text": "Analiz başlıyor…"}
{"type": "thinking",      "text": "Önce şemaya bakayım…"}
{"type": "tool_call",     "id": "c3", "name": "run_python",
                          "code": "df.groupby('bolge').tutar.sum()",
                          "executable": true, "args": {}}
{"type": "tool_result",   "id": "c3", "name": "run_python",
                          "text": "Marmara  4.235.120…", "duration_ms": 412}
{"type": "tool_error",    "id": "c4", "name": "run_python",
                          "error_type": "KeyError", "text": "KeyError: 'bolg'"}
{"type": "artifact",      "kind": "chart", "filename": "grafik_03.png",
                          "url": "/api/artifacts/ab12/grafik_03.png", "caption": ""}
{"type": "dataset_ready", "name": "bolge_ozet", "rows": 6, "cols": 3}
{"type": "message",       "text": "Marmara toplam cironun %42,2'sini…"}
{"type": "error",         "text": "Hesapta kredi kalmadı…", "fatal": true}
{"type": "done",          "steps": 5, "reason": "completed",
                          "usage": {"input": 11776, "output": 681}}
```

İki ayrıntı:

- **`executable` bayrağı** bir adımın notebook'ta hücre oluşturup
  oluşturmayacağını söylüyor (`run_python` ve `run_sql`). Defter paneli
  bununla sayıyor; olmadığında sadece-SQL analizlerde ".ipynb indir" butonu
  yanlışlıkla pasif kalıyordu.
- **`run_sql` sorgu metni** varsayılan olarak ekranda gizli
  (`SHOW_SQL_IN_CHAT=false`) ama loga, `trace.jsonl`'a ve `.ipynb`'ye tam
  gidiyor. **Jüri sunumunda `true` yap** — İlke 4 şeffaflık diyor ve
  "SQL'i agent kendi yazdı" demenin en güçlü yolu sorguyu göstermek.

**Hataları da yayınla.** Agent'ın `KeyError` alıp bir sonraki adımda
düzelttiğini görmek jüride güven artırır; gizlemek kaybettirir.

### Grafikler dosya olarak

Sandbox `/artifacts`'a PNG yazıyor, tool sadece **yolu** dönüyor. Base64
görüntüyü modelin context'ine sokmak hem pahalı hem gereksiz.

> İsteğe bağlı ileri seviye: üretilen grafiği vision destekli bir modele
> geri gösterip "okunaklı mı" diye sordurmak. Etkileyici ama pahalı;
> yapılmadı.

---

## 11. Katman G — Arayüz

React 19 + Vite. Üç panel:

```
┌──────────────────┬────────────────────────────────┬──────────────────┐
│  VERİ SETLERİ    │        SOHBET AKIŞI            │   ANALİZ DEFTERİ │
│                  │                                │                  │
│  ▸ satis         │  🔧 run_python                 │  [1] import ...  │
│    6.000 × 9     │  ┌──────────────────────────┐  │  [2] df = pd...  │
│    şema göster   │  │ df.groupby('bolge')...   │  │  [3] df.group... │
│                  │  └──────────────────────────┘  │                  │
│  ▸ musteriler    │  → Marmara  4.235.120          │  ⬇ .ipynb indir  │
│    240 × 5       │    Ege      2.811.004          │                  │
│                  │                                │                  │
│  + Kaynak ekle   │  📊 [grafik: Bölge cirosu]     │                  │
│    dosya·URL·DB  │                                │                  │
│    örnek veri    │  Marmara toplam cironun        │                  │
│                  │  %42,2'sini oluşturuyor…       │                  │
└──────────────────┴────────────────────────────────┴──────────────────┘
```

- **DatasetPanel** — sürükle-bırak dosya, URL yapıştırma, veritabanı DSN'i,
  tek tıkla örnek veri; şema kartını pencerede gösterir.
- **ToolCallCard** — kod bloğu + katlanabilir çıktı. Hatalar kırmızı
  çerçeveli; agent'ın toparlanışı görünür.
- **NotebookPanel** — biriken çalıştırılabilir adımlar + `.ipynb` indirme.
- **Başlıkta canlı rozet** — model adı ve sandbox modu. `local`'e düşülünce
  turuncu yanıyor; demo günü hangi modda olduğunu bir bakışta görürsün.

**Teknik not:** SSE için tarayıcının standart `EventSource` API'si
kullanılamadı — o yalnızca GET yapabiliyor, biz mesajı POST gövdesinde
yolluyoruz. `fetch` + `ReadableStream` ile çerçeveler elle ayrıştırılıyor
(`api.ts`).

Grafik stili tek bir `.mplstyle` dosyasından geliyor; tutarlı görünüm
demoda fark ettiriyor.

---

## 12. Teknoloji seçimleri

### Omurga: DuckDB

Projedeki en yüksek getirili karar. DuckDB bir **veritabanı motoru** —
SQLite gibi kurulumsuz, kütüphane olarak çalışır; farkı analiz için
tasarlanmış olması (tek kayıt getirmek değil, milyonlarca satırı gruplamak).

**1. Dosyayı doğrudan tablo gibi okur** — import yok, `CREATE TABLE` yok:

```sql
SELECT bolge, sum(tutar) FROM 'data/satis.parquet' GROUP BY bolge;
```

**2. Pandas ile iç içe çalışır** — kopya yok:

```python
df = pd.read_parquet("data/satis.parquet")
duckdb.sql("SELECT * FROM df WHERE tutar > 1000").df()
#                       ↑ df bir Python değişkeni, DuckDB onu görüyor
```

**3. Başka veritabanlarına `ATTACH` ile bağlanabilir.** Bu projede
kullanılmadı — tablolar parquet'e indiriliyor (gerekçe bölüm 17'de).

> ⚠ **Yanlış anlaşılmasın:** DuckDB "her SQL lehçesini anlayan bir dil"
> değildir. SQL'in lehçeleri farklıdır (Postgres, MySQL, MSSQL, Oracle) ve
> DuckDB'nin de kendi lehçesi vardır (Postgres'e çok yakın). Lehçeler arası
> **çeviri** yapan araç `sqlglot`'tur — ayrı bir kütüphane.

### Katman katman

| Katman | Seçim | Neden |
|---|---|---|
| **Motor** | `duckdb` | Yukarıdaki üç madde. Tek sorgu dili, dosya üstünde doğrudan SQL. |
| **Tablo** | `pandas` + `pyarrow` | Ekosistem hakimiyeti. **Model pandas'ı en iyi biliyor** — bu bir LLM tercihi, mühendislik tercihi değil. |
| **Excel** | `openpyxl` · `xlrd` · `pyxlsb` · `odfpy` | Dördü de kurulu, 5 MB eder. Format sürpriziyle demo günü uğraşma. |
| **Encoding** | `charset-normalizer` | Türkçe veride cp1254 / iso-8859-9 kesin çıkar. |
| **HTML tablo** | `pandas.read_html` + `lxml` | Tablo varsa iş bir satırda biter. **Her zaman ilk bunu dene.** |
| **HTTP** | `httpx` | async, HTTP/2, retry, timeout. `requests`'in modern yerine geçeni. |
| **HTML parse** | `selectolax` | BeautifulSoup'tan 10-30× hızlı, CSS seçici API'si. |
| **İçerik ayıklama** | `trafilatura` | Sayfadan boilerplate'i atıp ana metni verir. |
| **JS render** | `playwright` (chromium) | Pahalı, sadece gerekince açılır. |
| **SQL erişim** | `sqlalchemy` 2.x + sürücüler | Şema keşfi (`inspect`) ve DuckDB'nin kapsamadığı veritabanları. |
| **SQL güvenlik** | `sqlglot` | Sorguyu parse edip ifade tipini doğrula. |
| **PDF tablo** | `pdfplumber` | Saf Python. `camelot` daha güçlü ama Ghostscript bağımlılığı → demo riski. |
| **İstatistik** | `scipy` · `statsmodels` · `scikit-learn` | Hipotez testi, zaman serisi, kümeleme, regresyon. |
| **Grafik** | `matplotlib` (Agg backend) | Deterministik, ekransız, PNG. Tek `.mplstyle` → tutarlı görünüm. |
| **Profiling** | **kendi yazıldı** | `ydata-profiling` yavaş ve token'a çevrilemez. |
| **Sandbox** | Docker + subprocess yedeği | Bkz. bölüm 8. |
| **Backend** | `fastapi` + `uvicorn` | SSE için `StreamingResponse`. |
| **Frontend** | React 19 + Vite | Zaten kuruluydu. |

### Bağımlılıkta tek sabitleme: `pandas>=2.2,<3`

Agent sürekli pandas kodu yazıyor ve modellerin pandas akıcılığı ezici
çoğunlukla 2.x üzerine kurulu. pandas 3.0'da `applymap` kaldırıldı,
`inplace=` büyük ölçüde gitti, copy-on-write ve string dtype değişti. Her
uyumsuzluk bir hata → düzeltme turu demek: iterasyon ve demo saniyesi.

`backend/requirements.txt` ile `sandbox_image/requirements.txt` **aynı
aralıkta kalmalı** — ayrışırsa şema kartı ile agent'ın gördüğü veri uyuşmaz.

---

## 13. LLM sunucusu seçenekleri — bulut mu, yerel mi

Bu bölüm, "yarışma yerel model isterse ne yapacağız" sorusunun cevabı.
Kod tarafı hazır: `LLM_BASE_URL` ve `LLM_BACKEND` iki `.env` satırı.

### Önce kavram: model ≠ sunucu

- **Model** ağırlıklardan ibaret bir dosyadır (`llama-3.1-8b`, `qwen2.5-32b`).
  Tek başına hiçbir şey yapmaz.
- **Sunucu (inference server)** o dosyayı belleğe/GPU'ya yükleyip "şu
  mesajlara cevap ver" isteklerini karşılayan programdır.

Aşağıdaki üçü de sunucudur ve üçü de **OpenAI uyumlu** bir HTTP arayüzü
sunar (`/v1/chat/completions`). Bu yüzden bizim kodumuz için üçü de
"base_url'i değiştir" meselesinden ibaret.

### Ollama

**Ne:** Yerel makinede model çalıştırmanın en kolay yolu. Arka planda
`llama.cpp` kullanır, modelleri `ollama pull llama3.1` gibi tek komutla
indirir, RAM/GPU arasında otomatik bölüştürür.

**Kime uygun:** Tek kullanıcı, kişisel makine, "çalışsın yeter" senaryosu.
GPU şart değil — CPU'da da koşar (yavaş ama koşar). Windows/Mac/Linux.

**Artı:** Kurulumu 5 dakika. Bellek yetmezse modeli otomatik olarak
niceleştirilmiş (quantized, yani ağırlıkları küçültülmüş) haliyle indirir.

**Eksi:** Aynı anda çok istek geldiğinde yavaş — tek kullanıcı için
tasarlanmış. Tool calling (fonksiyon çağırma) desteği modele göre değişir ve
büyük bulut modelleri kadar güvenilir değil.

```bash
ollama pull qwen2.5:14b
# .env:
LLM_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama          # değer önemsiz ama boş olmamalı
OPENAI_MODEL=qwen2.5:14b
```

### LM Studio

**Ne:** Aynı işi yapan **masaüstü uygulaması**. Arayüzden model arıyorsun,
indiriyorsun, "Local Server" düğmesine basınca OpenAI uyumlu sunucu açılıyor.

**Kime uygun:** Terminal sevmeyen kullanıcı; model denemek, karşılaştırmak,
"bu model tool çağırabiliyor mu" diye hızlıca bakmak.

**Artı:** Görsel arayüz, kaç GB VRAM gerektiğini önceden gösteriyor, sohbet
ekranından hemen test edebiliyorsun.

**Eksi:** Ollama'yla aynı sınırlar (tek kullanıcı, orta hız) + kapalı kaynak
bir uygulama; sunucuya bağımlı bir demo için ekstra bir hareketli parça.

```
# .env:
LLM_BASE_URL=http://localhost:1234/v1
```

### vLLM

**Ne:** Üretim seviyesinde, **GPU odaklı** bir çıkarım sunucusu. Asıl
numarası *PagedAttention* denen bellek yönetimi ve *continuous batching* —
yani gelen istekleri sürekli birleştirip GPU'yu boş bırakmama. Aynı donanımda
Ollama'ya göre kat kat daha yüksek toplam verim (throughput) verir.

**Kime uygun:** Çok kullanıcı, sunucu ortamı, ciddi GPU (genelde 16 GB+
VRAM; büyük modeller için çok daha fazlası).

**Artı:** Hız ve eşzamanlılık; OpenAI uyumlu uç; tool calling için ayrıca
parser desteği var.

**Eksi:** Kurulum ağır (CUDA, uygun sürücü, Linux pratikte şart). Dizüstü
bilgisayarda demo için uygun değil. Model ağırlıkları genelde tam boyutta
indirilir — disk ve VRAM ister.

```bash
vllm serve Qwen/Qwen2.5-14B-Instruct --port 8000
# .env:
LLM_BASE_URL=http://localhost:8000/v1
```

### Karşılaştırma

| | Ollama | LM Studio | vLLM |
|---|---|---|---|
| Biçim | CLI + servis | Masaüstü uygulaması | Sunucu (Python paketi) |
| Donanım | CPU veya GPU | CPU veya GPU | GPU (pratikte zorunlu) |
| Kurulum | çok kolay | çok kolay | zor |
| Eşzamanlı istek | zayıf | zayıf | çok güçlü |
| Kullanım yeri | kişisel makine | model deneme | üretim / çok kullanıcı |
| OpenAI uyumlu uç | ✅ `:11434/v1` | ✅ `:1234/v1` | ✅ `:8000/v1` |

### Bu proje için pratik sonuç

1. **Kod tarafında yapılacak iş yok.** `LLM_BASE_URL` dolduğunda backend
   otomatik olarak `chat` arayüzüne geçiyor; `loop.py`, `tools.py`,
   `prompts.py` hiç değişmiyor.
2. **Asıl risk model kalitesi.** Bu agent tool çağırıyor, kod yazıyor,
   traceback okuyup kendini düzeltiyor. Küçük yerel modeller (4-8 GB VRAM'e
   sığanlar) tool çağırmayı sık sık beceremiyor ya da bozuk JSON üretiyor.
   O yüzden yerel model şart koşulursa **erken öğrenmek** gerekiyor:
   en az 14B sınıfı, tool calling desteği doğrulanmış bir model hedefle.
3. **Ölçmeden karar verme.** "Ucuz/küçük model" ile "az token yakan model"
   aynı şey değil — ölçümde `gpt-5-nano` tek bir çağrı için 415 çıkış
   tokeni yaktı, `gpt-5.4-mini` 19. Aynı sürpriz yerel modellerde de olur.

---

## 14. Türkçe veri tuzakları

Bu adım atlanırsa agent doğru kodu yazar ama **yanlış sonuç** alır. Sessiz
hatalar en tehlikelisidir: program patlamaz, sadece rakam yanlış çıkar.

### Sayı formatı

```
"1.234,56"      → 1234.56      # binlik nokta, ondalık virgül
"1,234.56"      → 1234.56      # aynı dosyada ikisi karışık olabilir
"%12,5"         → 0.125
"1.234,56 ₺"    → 1234.56      # para birimi soyulur
"(1.250)"       → -1250.0      # muhasebe negatifi
"-"  /  ""      → NaN          # boş değer
```

Karar kuralı: son ayıraçtan sonra tam 3 hane varsa o binlik ayıracıdır;
2 hane varsa ondalıktır. Karar **kolon bazında, çoğunluğa göre** verilir —
satır bazında değil, yoksa aynı kolonda iki farklı yorum çıkar.

### Tarih

```
"31.12.2024"  → gg.aa.yyyy   ✓ belli
"01.02.2024"  → 1 Şubat mı, 2 Ocak mı?   ← kolonun tamamına bakılır
```

Kolondaki tüm değerler taranır: birinci bileşen 12'yi aşıyorsa gün-önce,
ikinci bileşen aşıyorsa ay-önce. İkisi de aşmıyorsa Türkçe veride
`dayfirst=True` varsayılır ve **bu varsayım şema kartına yazılır.**

### Kolon adı — `.lower()` tuzağı

Python'da:

```python
"İ".lower()   # → 'i̇'  — İKİ karakter! (i + birleşen nokta U+0307)
"I".lower()   # → 'i'   — Türkçede 'ı' olmalıydı
```

`MÜŞTERİ_NO` kolonunu `.lower()` ile normalize edip sonra `musteri_no` diye
ararsan **bulamazsın**. Bu yüzden açık harf eşlemesi kullanılıyor:

```python
TR_MAP = str.maketrans("İIıŞşĞğÜüÖöÇçÂâÎîÛû", "IIiSsGgUuOoCcAaIiUu")
```

### Encoding

Uzantı `.csv` diye UTF-8 varsayma. `charset-normalizer` ile tespit ediliyor;
Türkiye'den gelen dosyalarda `cp1254` ve `iso-8859-9` çok yaygın. Yanlış
encoding sessizce `Ã¼` gibi bozuk metin üretir, patlamaz.

> Gerçek hata: tespit kütüphanesi `cp1254` (Türkçe) yerine `cp1257`
> (Baltık) diyordu ve `Şube Adı` kolonu `zube_adz` oluyordu. Türkçe lehine
> ek puanlama konuldu — YAPILANLAR.md hata #1.

### Sıralama

Türkçe alfabetik sıralama `sorted()` ile doğru çıkmaz (`ç` `c`'den sonra,
`ı` `i`'den önce gelmeli). Rapor çıktısında sıralama önemliyse `locale`
veya açık sıra tablosu gerekir.

---

## 15. Güvenlik modeli

Dört ayrı sınır. Her biri farklı bir riski kapatıyor. Not: buradaki tehdit
"kötü niyetli model" değil, **kaza ve dikkatsizlik**; ama savunmalar ikisine
de çalışıyor.

### 1. Kod çalıştırma → sandbox

Bölüm 8'deki container yapılandırması. Özet: ağ yok, RAM tavanı, timeout,
salt-okunur kök, root değil, sadece `/data` (salt okunur) ve `/artifacts`
(yazılabilir) görünüyor.

### 2. SQL → parse ederek doğrula

```python
def assert_readonly(query: str, dialect: str = "duckdb"):
    ifadeler = sqlglot.parse(query, read=dialect)
    if len(ifadeler) > 1:
        raise UnsafeQuery("Tek seferde tek sorgu.")        # zincirli ifade
    if not isinstance(ifadeler[0], (Select, Union, Intersect, Except, Subquery)):
        raise UnsafeQuery("Sadece SELECT çalıştırılabilir.")
```

> Metinde `"DROP"` aramak güvenlik **değildir**:
> `SELECT * FROM t WHERE not = 'DROP'` yanlış alarm verir,
> `sELeCt/**/1; drop table x` ise kaçar. Tek doğru yöntem ayrıştırıp ifade
> tipine bakmaktır.

Buna ek olarak: otomatik `LIMIT` enjeksiyonu ve — canlı veritabanı
kullanılırsa — salt-okunur DB kullanıcısı (asıl savunma odur).

### 3. Ağ erişimi → SSRF koruması + nezaket

`fetch_url` backend'de koşuyor, yani ağa çıkan tek yer orası. Orada bir açık
bırakmak iç ağa kapı açmak demek. Yapılanlar:

- Şema kontrolü (sadece http/https)
- **DNS çözümü + özel adres reddi** — `127.0.0.1`, `10.x`, `192.168.x`,
  bulut metadata uçları engelleniyor
- **Yönlendirmeler elle takip ediliyor**, her adımda yeniden denetleniyor —
  açık yönlendirmeyle iç ağa sıçramayı engeller
- `robots.txt` kontrolü, alan adı başına gecikme, makul User-Agent
- Yanıt boyutu, süre ve yönlendirme sayısı tavanı
- İstenirse alan adı allowlist'i (`FETCH_ALLOWED_DOMAINS`)

### 4. Sır yönetimi

`.env` sandbox'a **mount edilmiyor**, container ortam değişkenlerine API
anahtarı **geçirilmiyor** (zaten container'ın interneti de yok). Veritabanı
bağlantı dizesi backend'de kalıyor: yanıtlara sızmıyor, agent'a hiç
gösterilmiyor, hata mesajında parola maskeleniyor. Üçü de test edildi.

---

## 16. Çalıştırma ve işletim

### İlk kurulum

```bash
# 1) Python ortamı
pip install -r backend/requirements.txt

# 2) Sandbox imajı (Docker Desktop açık olmalı)
docker build -t analyst-sandbox:latest ./sandbox_image

# 3) Ayarlar
cp .env.example .env      # sonra OPENAI_API_KEY'i doldur

# 4) Frontend
cd frontend && npm install
```

### Günlük çalıştırma

```bash
python run_dev.py                  # backend  → http://127.0.0.1:8000
cd frontend && npm run dev         # arayüz   → http://localhost:5173
```

Vite, `/api` isteklerini backend'e proxy'liyor; tarayıcıda tek adres
kullanıyorsun, CORS derdi çıkmıyor.

### ⚠ Windows'ta `uvicorn --reload` KULLANMA

Bu, projedeki en sinsi tuzaklardan biriydi. `--reload` verildiğinde uvicorn
Windows'ta `SelectorEventLoop` seçiyor; o loop `create_subprocess_exec`
desteklemiyor. Sandbox'ın tamamı alt süreç açmaya dayandığı için
(docker kernel, local kernel, prewarm) sonuç: **sandbox hiç çalışmıyor.**
Üstelik fırlatılan `NotImplementedError`'ın mesajı boş olduğu için hata
ekranı hiçbir şey söylemiyordu.

`run_dev.py` bu yüzden var: yeniden yüklemeyi `watchfiles` ile **süreç
seviyesinde** yapıyor, uvicorn'u `--reload` olmadan başlatıyor. Dosya
değişince tüm süreç yeniden kalkıyor — biraz daha yavaş ama doğru çalışıyor.
`storage/` klasörü izlemeden hariç tutuluyor (yoksa oturum açmak sunucuyu
yeniden başlatıyordu).

`/api/health` bu durumu ayrıca raporluyor (`sandbox.blocker`): Docker ayakta
olsa bile sandbox ölüyse orada yazıyor.

### Ayar anahtarları (`.env`)

| Anahtar | Varsayılan | Ne yapar |
|---|---|---|
| `OPENAI_API_KEY` | — | Zorunlu. Yerel modelde de bir değer gerekir (`ollama` yazman yeter). |
| `OPENAI_MODEL` | `gpt-5.6-terra` (config), `.env`'de `gpt-5.4-mini` | Kullanılacak model. |
| `LLM_BASE_URL` | boş = OpenAI | Başka bir sunucu (Ollama/vLLM/LM Studio/OpenRouter). |
| `LLM_BACKEND` | otomatik | `responses` (OpenAI) veya `chat` (evrensel). |
| `CHAT_REASONING_EFFORT` | `none` | Sadece chat ucu için; açık modeller yok sayar. |
| `SANDBOX_BACKEND` | `docker` | Docker takılırsa `local`. |
| `SANDBOX_PREWARM` | `true` | Açılışta imajı ısıt. |
| `SHOW_SQL_IN_CHAT` | `false` | **Jüri sunumunda `true` yap** — SQL sorgusu ekranda görünsün. |
| `RESPECT_ROBOTS_TXT` | `true` | Web kazıma nezaketi. |
| `FETCH_ALLOWED_DOMAINS` | boş | Doluysa sadece bu alan adlarına çıkılır. |
| `FETCH_ALLOW_PRIVATE_HOSTS` | `false` | Sadece yerel testte açılır (SSRF koruması). |
| `STORAGE_DIR` | `backend/storage` | Testler burayı ayırıyor. |
| `LOG_LEVEL` | `INFO` | Sorun ararken `DEBUG`. |

Ayrıca kodda sabit duran ama bilinmesi gerekenler: `MAX_ITERATIONS=25`,
`MAX_CONSECUTIVE_ERRORS=3`, `MAX_TOOL_OUTPUT_CHARS=4000`,
`SANDBOX_TIMEOUT_SEC=30`, `SANDBOX_MEMORY=2g`,
`SAMPLE_THRESHOLD_ROWS=1.000.000`, `MAX_SQL_INGEST_ROWS=500.000`.

### Log

Konsol sade (`LOG_LEVEL`), dosya her zaman **DEBUG**:
`backend/storage/app.log` (10 MB × 3 dosya döner). Konsol kaydırıp
gittiğinde hatanın öncesini geri okumanın tek yolu bu. Her satırda oturum
kimliği ve adım numarası var:

```
[759ba7eed805] adım 6/25 · token 4937 giriş / 299 çıkış · istenen tool: run_sql
[759ba7eed805] SQL:
SELECT bolge, COUNT(*) AS n FROM satis GROUP BY 1
[759ba7eed805] adım 6 · run_sql OK · 90 ms · çıktı 964 karakter
```

### Testler

```bash
python tests/run_all.py          # 147 kontrol, Docker gerekmez (local kernel)
python tests/smoke_sandbox.py    # Docker izolasyon sınırları — Docker gerekir
python tests/test_grafik_bicim.py # eksen biçimlendirmesi — kernel gerekir
```

---

## 17. Fazlar ve demo kontrol listesi

### Yapım sırası (hepsi tamam)

| Faz | İş | Neden bu sırada | Bitti sayılır |
|---|---|---|---|
| **1 ✅** | Sandbox: Dockerfile, kernel protokolü, manager, prewarm, yerel yedek | **En riskli parça.** Çalışmazsa hiçbir şey çalışmaz. | `execute("x=5")` sonra `execute("print(x)")` → `5` |
| **2 ✅** | Ingest: sniff → adaptörler → normalize → parquet + profil + katalog | Agent kalitesinin ön şartı; agent'sız test edilebilir. | Rastgele bir Excel at → doğru şema kartı çıksın |
| **3 ✅** | Agent döngüsü + tool'lar + SSE + `trace.jsonl` | Faz 1-2 hazırsa hızlı gider. | "Bölgelere göre ciroyu grafikle" → grafik gelsin |
| **4 ✅** | SQL: veritabanı adaptörü + `run_sql` + sqlglot güvenliği | Bağımsız; Faz 3 ile paralel yazılabilir. | Veritabanı tablosu ile parquet birlikte sorgulansın |
| **5 ✅** | Web: `fetch_url`, DOM haritası, SSRF koruması | Bağımsız. | Bir sayfadan tablo çıkarıp kataloğa girsin |
| **6 ✅** | Arayüz: akış render, grafik gösterimi, veri paneli | Demo cilası. | Üç panelli ekran çalışsın |
| **7 ✅** | `.ipynb` export, `.mplstyle`, örnek veri, PDF/ZIP, testler | Son rötuş. | Notebook indir → Jupyter'da baştan koşsun |

> **Not (Faz 4):** Plan DuckDB `ATTACH` ile canlı veritabanı sorgusu
> öngörüyordu. Uygulamada tablolar SQLAlchemy ile parquet'e **indiriliyor**:
> İlke 1'e daha uygun (agent kaynak tipini bilmez), profiling/örnekleme/
> ilişki tespiti bedava geliyor ve DuckDB eklenti kurulumuna bağımlılık
> kalmıyor. `ATTACH` çok büyük veritabanları için sonradan eklenebilecek bir
> optimizasyon.

**Durum:** `python tests/run_all.py` → **147 kontrol, hepsi geçiyor.**

### Demo günü kontrol listesi

- [ ] Docker imajı **önceden build edilmiş** (`docker images | grep analyst-sandbox`)
- [ ] Backend `python run_dev.py` ile açıldı (**`uvicorn --reload` değil**)
- [ ] `GET /api/health` yeşil: `status: ok`, `docker.image_ready: true`,
      `sandbox.blocker: null`
- [ ] `SANDBOX_BACKEND=local` yedeği bir kez denendi ve çalışıyor
- [ ] `.env` dolu; kredi var mı kontrol edildi (kota hatası 429 döner ve
      beklemekle geçmez)
- [ ] `SHOW_SQL_IN_CHAT=true` yapıldı — jüri SQL'i görsün
- [ ] Hazır örnek veri butonu denendi (jüri kendi dosyasını getirmezse)
- [ ] İnternet kesilirse ne olur? Demo dosyaları **yerelde** hazır
- [ ] Bozuk veri senaryosu denendi: başlığı 5. satırda olan Excel, cp1254
      CSV, virgüllü ondalık, boş sheet
- [ ] Büyük dosya senaryosu denendi (>1M satır) — örneklem devreye giriyor mu
- [ ] Agent hata yapıp düzeltirken ekran nasıl görünüyor, bir kez izlendi
- [ ] `.ipynb` indirilip Jupyter'da baştan koştu
- [ ] Grafiklerde Türkçe karakterler ve binlik ayıraç düzgün
- [ ] Ekran çözünürlüğü / yansıtma denendi, panel taşması yok

---

## 18. Sözlük

**Agent** — Bir hedef verildiğinde kendi adımlarına karar veren, tool
çağırarak ilerleyen program. Burada: soruyu alıp şema okuyan, SQL/Python
yazan, hata alırsa düzelten döngü.

**Tool (function calling)** — Modele "şu işlevleri çağırabilirsin" diye
tanıtılan fonksiyon listesi. Model sadece *çağrılsın* der; çağrıyı bizim
kodumuz yapar.

**ReAct** — *Reason + Act.* Düşün → bir tool çağır → sonucu gör → tekrar
düşün. Baştan tam plan yapmak yerine adım adım ilerler; veri analizinde
doğru desen budur, çünkü sonraki adım öncekinin çıktısına bağlıdır.

**Sandbox** — Güvenilmeyen kodun yalıtılmış bir ortamda çalıştırılması.
Burada: ağı olmayan, RAM'i sınırlı, sadece iki klasörü gören bir Docker
container'ı. Amaç hem güvenlik hem çökme yalıtımı.

**Kernel (kalıcı)** — Container içinde ayakta duran ve sıradaki kodu
bekleyen Python süreci. Değişkenler (`df` gibi) çağrılar arasında yaşar;
Jupyter'daki "hücreleri sırayla çalıştırma" davranışının aynısı.

**Parquet** — Kolon bazlı, sıkıştırılmış tablo dosya formatı. CSV'ye göre
5-10× küçük, çok daha hızlı okunur ve tip bilgisini saklar (CSV'de her şey
metindir). Bu projenin kanonik depolama formatı.

**Şema kartı** — Bir veri setinin modele gösterilen metin özeti: kolonlar,
tipler, boş oranı, dağılım, örnek satırlar, uyarılar. Ham verinin yerine
geçer, ~1,5k token.

**Katalog** — Oturumdaki tüm veri setlerinin kaydı (`catalog.json`). Kaynağı
ne olursa olsun her şey burada aynı yapıda durur.

**DuckDB** — Kurulumsuz, süreç-içi analitik veritabanı motoru. Dosyaları
doğrudan SQL ile sorgular, pandas ile kopyasız veri alışverişi yapar.
*"Her SQL lehçesini anlayan dil" değildir* — kendi lehçesi vardır.

**sqlglot** — SQL parse ve lehçe çeviri kütüphanesi. Burada sorgunun
gerçekten `SELECT` olduğunu doğrulamak için kullanılıyor.

**SSE (Server-Sent Events)** — Sunucudan tarayıcıya tek yönlü canlı veri
akışı. WebSocket'ten basit; tek yön yeterli olduğu için burada doğru seçim.

**Magic byte** — Dosyanın ilk birkaç baytındaki format imzası. Uzantıdan
daha güvenilir: `.txt` uzantılı bir Excel'i uzantı yakalamaz, magic byte
yakalar.

**SSRF** — *Server-Side Request Forgery.* Sunucuyu kandırıp iç ağa istek
attırma saldırısı. `fetch_url`'ün özel IP'leri reddetmesinin sebebi.

**Warm pool** — Önceden ayağa kaldırılmış hazır container havuzu. Bu projede
**kullanılmadı**: her container bir oturumun klasörlerine bağlı olmak
zorunda olduğu için havuz veri sızıntısı riski doğuruyordu (bölüm 8).

**Prewarm** — Açılışta imajı bir kez çalıştırıp Docker katmanlarını
ısıtmak. Warm pool'un yerine konan, izolasyonu bozmayan çözüm.

**Token** — Modelin metni işlerken kullandığı birim (kabaca bir kelimenin
parçası). Maliyet ve context sınırı token üzerinden hesaplanır; bu yüzden
"modele ham veri basma" kuralı paranın ve doğruluğun ortak sebebi.
