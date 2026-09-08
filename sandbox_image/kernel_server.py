"""Container içinde koşan kalıcı Python kernel'ı.

stdin'den satır başına bir JSON komut okur, çalıştırır, stdout'a
satır başına bir JSON sonuç yazar. Değişkenler çağrılar arasında yaşar —
Jupyter'da hücreleri sırayla çalıştırmak gibi.

Protokol (backend/sandbox/protocol.py ile eşleşmeli):

  gelen:  {"id": "c7", "code": "df.head()"}
          {"id": "c8", "op": "reset"}          # namespace'i temizle
          {"id": "c9", "op": "ping"}

  giden:  {"id": "c7", "ok": true, "stdout": "...", "stderr": "",
           "result_repr": "...", "new_artifacts": ["chart_01.png"],
           "duration_ms": 412}
          {"id": "c8", "ok": false, "error_type": "KeyError",
           "traceback": "Traceback (most recent call last): ..."}

Zaman aşımı BURADA uygulanmaz — backend boruyu beklerken zaman aşımına
uğrar ve container'ı yeniden başlatır. Buradaki tek iş kodu çalıştırmak.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import sys
import time
import traceback
from pathlib import Path

# Bu dosya Docker imajına KOPYALANIYOR. Burada bir şey değiştirdiysen
# imajı yeniden build etmen gerekir; aksi halde container eski sürümü
# çalıştırır. Sürümü artır ki backend bayat imajı açılışta yakalasın.
#   docker build -t analyst-sandbox:latest ./sandbox_image
PROTOCOL_VERSION = 6

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
ARTIFACTS_DIR = Path(os.environ.get("ARTIFACTS_DIR", "/artifacts"))

MAX_STREAM_CHARS = 200_000  # bir çalıştırmanın stdout tavanı


def _bootstrap() -> dict:
    """Her kernel'ın başlangıç namespace'i. Agent bunları import etmeden kullanır.

    Docker imajında hepsi kurulu. LocalKernel yedeğinde bazıları eksik
    olabilir — o yüzden savunmacı: eksik olan atlanır, kernel yine açılır.
    """
    # Yol yardımcıları: Docker'da /data ve /artifacts, yerel yedekte
    # gerçek host klasörleri. Agent'ın yazdığı kod iki modda da aynı
    # çalışsın diye yolu ELLE yazdırmıyoruz.
    def data_path(name: str = "") -> str:
        return str(DATA_DIR / name) if name else str(DATA_DIR)

    def artifact_path(name: str = "") -> str:
        return str(ARTIFACTS_DIR / name) if name else str(ARTIFACTS_DIR)

    def tr_sayi(x: float, ondalik: int = 0) -> str:
        """1234567.5 → '1.234.567,5' (Türkçe biçim)."""
        s = f"{x:,.{ondalik}f}"
        return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")

    def tr_eksen(ax=None, eksen: str = "y", ondalik: int = 0, son_ek: str = "") -> None:
        """Eksen etiketlerini Türkçe biçime çevirir.

        Modelin her seferinde formatter yazmasını beklemek yerine hazır
        veriyoruz: `4,007,888` değil `4.007.888 TL`, `1e6` gösterimi yok.
        """
        import matplotlib.pyplot as _plt
        from matplotlib.ticker import FuncFormatter

        ax = ax or _plt.gca()
        bicim = FuncFormatter(lambda v, _p: tr_sayi(v, ondalik) + son_ek)
        for e in eksen:
            (ax.yaxis if e == "y" else ax.xaxis).set_major_formatter(bicim)

    _tr_eksenleri_kur(tr_sayi)

    ns: dict = {
        "__name__": "__sandbox__",
        "DATA_DIR": str(DATA_DIR),
        "ARTIFACTS_DIR": str(ARTIFACTS_DIR),
        "data_path": data_path,
        "artifact_path": artifact_path,
        "tr_sayi": tr_sayi,
        "tr_eksen": tr_eksen,
    }

    try:
        import matplotlib

        matplotlib.use("Agg")  # başsız ortam — ekran yok
        import matplotlib.pyplot as plt

        ns["plt"] = plt
    except ImportError:
        pass
    else:
        # Tek stil dosyası: agent sadece veriyi çizer, görünüm buradan gelir.
        # AYRI try: bozuk veya eksik bir stil dosyası kernel'ı ASLA düşürmemeli
        # — grafikler çirkin çıkar, ama analiz devam eder.
        try:
            import matplotlib.style  # `import matplotlib` bunu getirmiyor

            stil = Path(__file__).with_name("analyst.mplstyle")
            if stil.exists():
                matplotlib.style.use(str(stil))
        except Exception as e:  # noqa: BLE001 — kasıtlı geniş yakalama
            print(f"[kernel] stil yüklenemedi, varsayılana düşüldü: {e}",
                  file=sys.stderr, flush=True)

    try:
        import pandas as pd

        pd.set_option("display.width", 160)
        pd.set_option("display.max_columns", 60)
        ns["pd"] = pd
    except ImportError:
        pass

    for alias, module in (("np", "numpy"), ("duckdb", "duckdb")):
        try:
            ns[alias] = __import__(module)
        except ImportError:
            pass

    return ns


def _tr_eksenleri_kur(tr_sayi) -> None:
    """Kaydedilen her grafikte sayısal eksenleri Türkçe biçime çevirir.

    Neden otomatik: modele "tr_eksen kullan" demek yetmiyor — ölçüldü,
    kendi biçimlendiricisini yazıp ekseni ham bırakıyor (`1000000`).
    Görünüm altyapının işi, modelin değil (bkz. analyst.mplstyle).

    Modelin KENDİ koyduğu biçimlendiriciye dokunulmaz: sadece matplotlib'in
    varsayılan ScalarFormatter'ı değiştirilir. Kategori ve tarih eksenleri
    farklı sınıf kullandığı için doğal olarak korunur.
    """
    try:
        import matplotlib.figure
        from matplotlib.ticker import FuncFormatter, ScalarFormatter
    except ImportError:
        return

    def ondalik_bul(konumlar) -> int:
        adimlar = [
            abs(b - a) for a, b in zip(konumlar, konumlar[1:]) if abs(b - a) > 0
        ]
        if not adimlar:
            return 0
        adim = min(adimlar)
        if adim >= 1:
            return 0
        for basamak in range(1, 5):
            if adim >= 10 ** -basamak:
                return basamak
        return 4

    def kisa(v: float, ondalik: int) -> str:
        """Büyük sayıyı kısaltır: 4000000 → '4 mn'.

        Eksende tam basamak yazmak etiketleri uzatıp üst üste bindiriyor.
        Kesin rakam zaten çubuk etiketlerinde ve metinde veriliyor.
        """
        # (eşik, bölen, sonek) — eşik ile bölen AYRI: "bin" kısaltması
        # 10.000'den itibaren devreye girer ama 1.000'e bölünür.
        mutlak = abs(v)
        for esik, bolen, sonek in (
            (1e9, 1e9, " mr"), (1e6, 1e6, " mn"), (1e4, 1e3, " bin")
        ):
            if mutlak >= esik:
                bolunmus = v / bolen
                basamak = 0 if abs(bolunmus) >= 10 else 1
                return tr_sayi(bolunmus, basamak) + sonek
        return tr_sayi(v, ondalik)

    def uygula(fig) -> None:
        for ax in fig.get_axes():
            for eksen in (ax.xaxis, ax.yaxis):
                if type(eksen.get_major_formatter()) is not ScalarFormatter:
                    continue  # model kendi biçimini koymuş — karışma
                ondalik = ondalik_bul(list(eksen.get_majorticklocs()))
                eksen.set_major_formatter(
                    FuncFormatter(lambda v, _p, d=ondalik: kisa(v, d))
                )

    orijinal = matplotlib.figure.Figure.savefig

    def savefig(self, *a, **kw):
        try:
            uygula(self)
        except Exception:
            pass  # biçimlendirme asla kaydı engellemesin
        return orijinal(self, *a, **kw)

    matplotlib.figure.Figure.savefig = savefig


def _snapshot_artifacts() -> set[str]:
    if not ARTIFACTS_DIR.is_dir():
        return set()
    return {p.name for p in ARTIFACTS_DIR.iterdir() if p.is_file()}


def _clip(text: str) -> str:
    if len(text) <= MAX_STREAM_CHARS:
        return text
    half = MAX_STREAM_CHARS // 2
    kesilen = len(text) - MAX_STREAM_CHARS
    return f"{text[:half]}\n\n... [{kesilen:,} karakter kırpıldı] ...\n\n{text[-half:]}"


def _run(code: str, ns: dict) -> dict:
    """Kodu çalıştır. Son satır bir ifadeyse değerini de döndür (Jupyter gibi)."""
    before = _snapshot_artifacts()
    out, err = io.StringIO(), io.StringIO()
    started = time.perf_counter()
    result_repr = ""

    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError:
        return {
            "ok": False,
            "error_type": "SyntaxError",
            "traceback": traceback.format_exc(),
            "stdout": "",
            "stderr": "",
            "new_artifacts": [],
            "duration_ms": 0,
        }

    # Son ifade varsa ayır: gövdeyi exec, sonu eval et.
    tail = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        tail = ast.Expression(tree.body.pop().value)

    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            if tree.body:
                exec(compile(tree, "<agent>", "exec"), ns)
            if tail is not None:
                value = eval(compile(tail, "<agent>", "eval"), ns)
                if value is not None:
                    ns["_"] = value
                    result_repr = _clip(repr(value))
        ok, error_type, tb = True, None, None
    except BaseException:  # SystemExit / KeyboardInterrupt de yakalansın
        ok, error_type = False, sys.exc_info()[0].__name__
        tb = traceback.format_exc()

    new = sorted(_snapshot_artifacts() - before)

    return {
        "ok": ok,
        "error_type": error_type,
        "traceback": tb,
        "stdout": _clip(out.getvalue()),
        "stderr": _clip(err.getvalue()),
        "result_repr": result_repr,
        "new_artifacts": new,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }


def main() -> None:
    ns = _bootstrap()
    # Backend "hazırım" sinyalini bekliyor.
    print(
        json.dumps(
            {"op": "ready", "pid": os.getpid(), "protocol": PROTOCOL_VERSION}
        ),
        flush=True,
    )

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            print(
                json.dumps({"id": None, "ok": False, "error_type": "ProtocolError",
                            "traceback": "geçersiz JSON"}),
                flush=True,
            )
            continue

        msg_id = msg.get("id")
        op = msg.get("op", "exec")

        if op == "ping":
            reply = {"id": msg_id, "ok": True, "pong": True}
        elif op == "reset":
            ns = _bootstrap()
            reply = {"id": msg_id, "ok": True, "reset": True}
        elif op == "shutdown":
            print(json.dumps({"id": msg_id, "ok": True}), flush=True)
            return
        else:
            reply = {"id": msg_id, **_run(msg.get("code", ""), ns)}

        print(json.dumps(reply, ensure_ascii=False, default=str), flush=True)


if __name__ == "__main__":
    main()
