"""ZIP arşivi adaptörü.

Bir arşiv birden çok veri seti demektir; her üye dosya normal ingest
hattından yeniden geçirilir (özyineleme). Böylece zip içindeki Excel de,
CSV de, tanınmayan dosya da aynı muameleyi görür.

Güvenlik: zip slip (üye adında `../` ile klasör dışına yazma) engellenir,
sıkıştırma bombalarına karşı toplam açılmış boyut sınırlanır.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

from backend import config
from backend.ingest.adapters.base import UnsupportedSource

log = logging.getLogger(__name__)

MAX_UYE = 40
MAX_ACILMIS_BAYT = config.MAX_UPLOAD_MB * 1024 * 1024 * 4  # sıkıştırma bombası koruması

ATLANACAK = (".ds_store", "thumbs.db")
ATLANACAK_UZANTI = {".exe", ".dll", ".so", ".dylib", ".bat", ".sh", ".ps1"}


def cikar(path: Path, hedef_dizin: Path) -> list[Path]:
    """Arşivi güvenli biçimde açar ve işlenecek dosyaların yolunu döner."""
    hedef_dizin.mkdir(parents=True, exist_ok=True)
    cikarilan: list[Path] = []
    toplam = 0

    try:
        with zipfile.ZipFile(path) as z:
            for bilgi in z.infolist():
                if bilgi.is_dir() or len(cikarilan) >= MAX_UYE:
                    continue

                ad = Path(bilgi.filename).name  # yol bileşenlerini at → zip slip yok
                if not ad or ad.lower() in ATLANACAK or ad.startswith("."):
                    continue
                if Path(ad).suffix.lower() in ATLANACAK_UZANTI:
                    log.info("arşivde atlanan dosya: %s", ad)
                    continue

                toplam += bilgi.file_size
                if toplam > MAX_ACILMIS_BAYT:
                    raise UnsupportedSource(
                        "Arşivin açılmış boyutu sınırı aştı — sıkıştırma bombası olabilir."
                    )

                hedef = hedef_dizin / ad
                with z.open(bilgi) as kaynak, hedef.open("wb") as f:
                    f.write(kaynak.read())
                cikarilan.append(hedef)
    except zipfile.BadZipFile as e:
        raise UnsupportedSource(f"Arşiv açılamadı: {e}") from e

    if not cikarilan:
        raise UnsupportedSource("Arşivde işlenebilir dosya yok.")

    log.info("arşivden %d dosya çıkarıldı: %s", len(cikarilan), path.name)
    return cikarilan
