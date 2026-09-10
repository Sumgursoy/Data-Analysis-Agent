"""Uygulama ayarları. Her şey tek yerden okunur — sihirli sabit yok."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "evet"}


# ─────────────────────────────────────────────────────────────────
#  LLM
# ─────────────────────────────────────────────────────────────────

API_KEY = os.getenv("OPENAI_API_KEY")

# Model .env'den gelir (OPENAI_MODEL). Buradaki varsayılan sadece .env
# yoksa devreye girer — bilerek muhafazakâr, çünkü ilk çalıştırmada
# demonun patlamaması bir kaç sentten önemli.
#
# Ölçülmüş adaylar (responses ucu, function tool ile — hepsi tool çağırıyor):
#   gpt-5.6-sol    frontier, en pahalı
#   gpt-5.6-terra  referans kalite; uçtan uca doğrulandı
#   gpt-5.6-luna   terra ile aynı sayısal sonuç, ~1/10 maliyet
#   gpt-5.4-mini   ölçümde en hızlısı (1,0 sn / 19 çıkış tokeni)
#   gpt-5-nano     KAÇIN — tek çağrıda 415 çıkış tokeni yaktı
#
# Agent kod yazıp traceback okuyarak kendini düzeltiyor; model küçüldükçe
# asıl maliyet token değil, başarısız düzeltme turlarının yediği DEMO
# SANİYESİ olur. Aşağı inerken bunu ölç, varsayma.
MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-terra")

# None → OpenAI. Başka bir sunucu için tam URL ver (bkz. .env.example).
LLM_BASE_URL = os.getenv("LLM_BASE_URL") or None

# Hangi API arayüzü:
#   "responses" → /v1/responses — OpenAI'a özel.
#   "chat"      → /v1/chat/completions — evrensel standart.
#                 Ollama, vLLM, LM Studio, OpenRouter hepsi bunu konuşur.
#
# Varsayılan kaynağa göre seçiliyor. Sebep ÖLÇÜLDÜ: gpt-5.6 ailesi chat
# ucunda function tool ile akıl yürütmeyi birlikte kabul etmiyor (HTTP 400,
# "set reasoning_effort to 'none'"). Kod yazıp hatasını düzelten bir agent
# için akıl yürütmeyi kapatmak ciddi kayıp — o yüzden OpenAI'da responses.
LLM_BACKEND = os.getenv("LLM_BACKEND") or ("chat" if LLM_BASE_URL else "responses")

# Sadece chat backend'i için. gpt-5.6 ailesinde tool kullanırken "none"
# ZORUNLU. Açık kaynak sunucular bu parametreyi yok sayar.
# Boş bırakılırsa parametre hiç gönderilmez.
CHAT_REASONING_EFFORT = os.getenv("CHAT_REASONING_EFFORT", "none").strip()

MAX_OUTPUT_TOKENS = 8000

# ─────────────────────────────────────────────────────────────────
#  Agent
# ─────────────────────────────────────────────────────────────────

# Tek turda kaç kez tool çağrılabilir (sonsuz döngü koruması).
# Hata düzeltme döngüsü de bu bütçeden yiyor — dar tutma.
MAX_ITERATIONS = 25

# Aynı hataya üst üste kaç kez takılırsa kernel restart + yaklaşım değişimi.
MAX_CONSECUTIVE_ERRORS = 3

# Tool çıktısı bundan uzunsa ortası kırpılır (baş ve son korunur —
# traceback'te asıl hata sonda olur).
MAX_TOOL_OUTPUT_CHARS = 4000

# SQL sorgu METNİ sohbet ekranında görünsün mü?
#
# Kapalıyken: kart ve sonuç tablosu görünür, sorgunun kendisi görünmez.
# Sorgu her hâlükârda loga ve trace.jsonl'a tam olarak yazılır; indirilen
# .ipynb de sorguları içerir — yani doğrulanabilirlik kaybolmuyor.
#
# DEMO NOTU: MIMARI.md İlke 4 "her şey şeffaf" diyor ve jüriye "SQL'i
# agent kendi yazdı" demenin en güçlü yolu sorguyu ekranda göstermek.
# Ekran kalabalığı sorun olduğu için varsayılan kapalı; sunumda
# .env'e SHOW_SQL_IN_CHAT=true yazıp geri aç.
SHOW_SQL_IN_CHAT = _flag("SHOW_SQL_IN_CHAT", False)

# ─────────────────────────────────────────────────────────────────
#  Sandbox
# ─────────────────────────────────────────────────────────────────

# "docker" → izole container (önerilen)
# "local"  → subprocess fallback. Demo günü Docker takılırsa buna düş.
SANDBOX_BACKEND = os.getenv("SANDBOX_BACKEND", "docker")

SANDBOX_IMAGE = "analyst-sandbox:latest"
SANDBOX_TIMEOUT_SEC = 30          # 1M satırlık groupby 15 sn'yi aşabiliyor
SANDBOX_MEMORY = "2g"
SANDBOX_CPUS = 2.0
SANDBOX_PIDS_LIMIT = 128

# Kernel ile konuşulan borunun satır tamponu.
#
# NEDEN VAR: protokol "satır başına bir JSON" (sandbox/protocol.py) ve kernel
# bir çalıştırmanın çıktısını 200.000 karakterde kırpıyor
# (sandbox_image/kernel_server.py: MAX_STREAM_CHARS). Türkçe metinde UTF-8 +
# JSON escape bunu ~2 katına çıkarıyor → tek satır 400 KB olabiliyor.
# asyncio'nun varsayılanı ise 64 KB (asyncio.streams._DEFAULT_LIMIT); aşılınca
# readline() `ValueError: Separator is found, but chunk is longer than limit`
# fırlatıyor ve run_python sessizce ölüyordu.
#
# Düzeltme bilerek SADECE bu tarafta: kernel_server.py'daki tavanı düşürmek
# imaj rebuild'i gerektirirdi ve build edilmezse eski değer sessizce çalışmaya
# devam ederdi — PROTOCOL_VERSION koruması bunu yakalamaz (protokol değişmiyor).
SANDBOX_PIPE_LIMIT = 8 * 1024 * 1024

# Açılışta imajı bir kez çalıştırıp Docker katmanlarını ısıt.
# Session'dan bağımsız genel bir container havuzu YOK — her container
# o session'ın klasörlerine bağlı olmak zorunda (gerekçe: sandbox/manager.py).
# Bunun yerine kernel, session açılır açılmaz başlatılıyor.
SANDBOX_PREWARM = _flag("SANDBOX_PREWARM", True)

# ─────────────────────────────────────────────────────────────────
#  Ingest
# ─────────────────────────────────────────────────────────────────

# Bu satır sayısını aşan veri setleri için ayrıca __sample.parquet yazılır;
# profiling ve agent'ın ilk denemeleri örneklem üstünde koşar.
SAMPLE_THRESHOLD_ROWS = 1_000_000
SAMPLE_ROWS = 100_000

# Sniff aşamasında dosyanın ilk kaç baytına bakılır.
SNIFF_BYTES = 8192

# Excel'de başlık satırı ilk kaç satır içinde aranır.
HEADER_SCAN_ROWS = 20

MAX_UPLOAD_MB = 500

# SQL tablosu indirilirken satır tavanı. Aşılırsa şema kartına
# "sonuçlar KISMİ" uyarısı düşer — agent bunu görüp ona göre konuşur.
MAX_SQL_INGEST_ROWS = 500_000

# ─────────────────────────────────────────────────────────────────
#  Fetch (Faz 5)
# ─────────────────────────────────────────────────────────────────

FETCH_TIMEOUT_SEC = 20
FETCH_MAX_BYTES = 20 * 1024 * 1024
FETCH_MAX_REDIRECTS = 5
FETCH_USER_AGENT = "AgenticDataAnalyst/0.1 (yarisma projesi)"
RESPECT_ROBOTS_TXT = _flag("RESPECT_ROBOTS_TXT", True)

# Özel ağ adreslerine (127.0.0.1, 10.x, 192.168.x, bulut metadata uçları)
# istek atmayı engeller — SSRF koruması. Sadece yerel test için açılır.
FETCH_ALLOW_PRIVATE_HOSTS = _flag("FETCH_ALLOW_PRIVATE_HOSTS", False)

# Boşsa her (özel olmayan) adrese izin verilir. Doluysa sadece bu alan
# adları ve alt alan adları. Yarışma tek bir siteyi kazıtıyorsa buraya yaz.
FETCH_ALLOWED_DOMAINS: list[str] = [
    d.strip().lower()
    for d in os.getenv("FETCH_ALLOWED_DOMAINS", "").split(",")
    if d.strip()
]

# Playwright ile JS render — pahalı, sadece gerekince.
FETCH_RENDER_TIMEOUT_SEC = 30

# ─────────────────────────────────────────────────────────────────
#  Depolama
# ─────────────────────────────────────────────────────────────────

# STORAGE_DIR env ile taşınabilir. Testler bunu kullanıp ayrı bir köke
# yazıyor — aksi halde test temizliği gerçek oturumları siler (bir kez
# canlı bir analizin trace'ini yok etti).
STORAGE_DIR = Path(os.getenv("STORAGE_DIR") or (BASE_DIR / "backend" / "storage"))
SESSIONS_DIR = STORAGE_DIR / "sessions"


def session_dir(session_id: str) -> Path:
    """Bir session'ın kök klasörü. Alt klasörleriyle birlikte oluşturulur."""
    d = SESSIONS_DIR / session_id
    for sub in ("raw", "data", "artifacts"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


# ─────────────────────────────────────────────────────────────────
#  CORS
# ─────────────────────────────────────────────────────────────────

ALLOWED_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

DEBUG = _flag("DEBUG")

# ─────────────────────────────────────────────────────────────────
#  Loglama
# ─────────────────────────────────────────────────────────────────

# Konsol seviyesi. Sorun ararken .env'e LOG_LEVEL=DEBUG yaz.
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG" if DEBUG else "INFO").upper()

# Her şey ayrıca dosyaya yazılır — konsol kaydırıp gittiğinde ya da
# demo sırasında geriye dönüp bakman gerektiğinde tek kaynak burası.
# Dosyada seviye her zaman DEBUG: konsolu sade tut, dosyada her şey olsun.
LOG_FILE = STORAGE_DIR / "app.log"
LOG_FILE_MAX_MB = 10
LOG_FILE_BACKUPS = 3
