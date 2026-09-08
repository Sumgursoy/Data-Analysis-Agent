"""Katalog — session'daki tüm veri setlerinin tek kaydı.

Kaynağı ne olursa olsun (Excel, SQL tablosu, kazınmış web tablosu,
tanınmayan dosya) her şey burada aynı yapıda durur. Agent'ın gördüğü
tek soyutlama budur; adaptör katmanı bu noktadan sonra görünmez.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from backend import config

log = logging.getLogger(__name__)

CATALOG_DOSYASI = "catalog.json"


@dataclass
class Dataset:
    name: str                       # "sales" — agent'ın kullandığı ad
    kind: str                       # file | sql_table | web | raw | derived
    location: str                   # sandbox içindeki yol: "/data/sales.parquet"
    origin: str = ""                # "satislar_2024.xlsx → Sheet1"
    row_count: int | None = None
    col_count: int | None = None
    schema_card: str = ""           # profile.py'nin ürettiği metin
    summary: str = ""               # katalog listesinde görünen tek satır
    sampled: bool = False           # örneklem mi, tam veri mi
    sample_location: str | None = None
    created_by: str = "ingest"      # ingest | agent
    notes: list[str] = field(default_factory=list)

    def as_line(self) -> str:
        """Sistem prompt'undaki katalog listesi için tek satır."""
        boyut = (
            f"{self.row_count:,} satır × {self.col_count} kolon".replace(",", ".")
            if self.row_count is not None and self.col_count is not None
            else self.kind
        )
        parca = f"  {self.name:<16} — {boyut}"
        if self.summary:
            parca += f", {self.summary}"
        if self.sampled:
            parca += "  [örneklem mevcut]"
        return parca


class Catalog:
    """Bir session'ın veri seti kayıtları. Diske JSON olarak yazılır."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._dir = config.session_dir(session_id)
        self._path: Path = self._dir / CATALOG_DOSYASI
        self._items: dict[str, Dataset] = {}
        self._load()

    # ── kalıcılık ──────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            ham = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log.exception("catalog.json okunamadı (session=%s)", self.session_id)
            return
        for kayit in ham.get("datasets", []):
            try:
                ds = Dataset(**kayit)
            except TypeError:  # şema değişmiş eski kayıt — atla
                log.warning("uyumsuz katalog kaydı atlandı: %s", kayit.get("name"))
                continue
            self._items[ds.name] = ds

    def _save(self) -> None:
        payload = {"session_id": self.session_id,
                   "datasets": [asdict(d) for d in self._items.values()]}
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Atomik değiştirme — yarım yazılmış katalog kalmasın.
        #
        # Windows'ta os.replace, dosyayı o an başka bir süreç açık tuttuğunda
        # PermissionError (WinError 5) verir. Proje bir OneDrive klasöründe
        # duruyor ve senkronizasyon tam bu anda dosyaya dokunabiliyor —
        # testte bir kez gerçekten oldu ve isteği 500'e düşürdü.
        # Antivirüs taraması da aynı etkiyi yapar. Geçici bir yarış olduğu
        # için kısa aralıklarla birkaç kez denemek yeterli.
        for deneme in range(5):
            try:
                tmp.replace(self._path)
                return
            except PermissionError:
                if deneme == 4:
                    raise
                time.sleep(0.1 * (deneme + 1))

    # ── erişim ─────────────────────────────────────────────────

    def unique_name(self, istenen: str) -> str:
        """Çakışmayan bir ad döner: satis → satis_2 → satis_3.

        Dosya YAZILMADAN ÖNCE çağır. Aksi halde aynı adlı ikinci veri seti
        kataloğa `satis_2` olarak girer ama parquet `satis.parquet` adıyla
        yazılıp birincinin dosyasını ezer.
        """
        return self._benzersiz_ad(istenen)

    def add(self, ds: Dataset) -> Dataset:
        ds.name = self._benzersiz_ad(ds.name)
        self._items[ds.name] = ds
        self._save()
        log.info("kataloğa eklendi: %s (%s)", ds.name, ds.kind)
        return ds

    def get(self, name: str) -> Dataset | None:
        return self._items.get(name)

    def all(self) -> list[Dataset]:
        return list(self._items.values())

    def names(self) -> list[str]:
        return list(self._items)

    def save(self) -> None:
        """Kayıtlar yerinde değiştirildiyse diske yaz (ör. şema kartı güncellemesi)."""
        self._save()

    def remove(self, name: str) -> bool:
        if self._items.pop(name, None) is None:
            return False
        self._save()
        return True

    def _benzersiz_ad(self, istenen: str) -> str:
        if istenen not in self._items:
            return istenen
        i = 2
        while f"{istenen}_{i}" in self._items:
            i += 1
        return f"{istenen}_{i}"

    # ── agent'a giden metin ────────────────────────────────────

    def as_prompt_block(self) -> str:
        """Sistem prompt'una giren özet.

        Sadece isim + tek satır. Detaylı şema kartı `get_schema(name)`
        ile istendiğinde gelir — 20 tablolu bir veritabanında tüm şemayı
        prompt'a basmak hem pahalı hem model dağıtıcıdır.
        """
        if not self._items:
            return "Henüz veri seti yüklenmedi."
        satirlar = "\n".join(d.as_line() for d in self._items.values())
        return f"Mevcut veri setleri:\n{satirlar}"
