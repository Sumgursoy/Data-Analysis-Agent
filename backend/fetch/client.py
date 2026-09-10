"""Güvenli sayfa indirme.

Sandbox `--network none` ile koşuyor; ağ erişimi olan TEK yer burası.
O yüzden kapıyı sıkı tutuyoruz:

  - şema sadece http/https
  - DNS çözülür, özel/loopback/metadata adresleri reddedilir (SSRF)
  - yönlendirmeler ELLE takip edilir ve her adımda yeniden denetlenir
    (açık yönlendirmeyle iç ağa sıçramayı engeller)
  - boyut ve süre tavanı
  - robots.txt ve istekler arası gecikme (nezaket)

İndirilen sayfa /data'ya dosya olarak düşer; agent onu sandbox'ta okur.
SQL'deki desenin aynısı — ağ işi backend'de, sonuç dosyada.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from backend import config

log = logging.getLogger(__name__)

# Alan adı başına son istek zamanı — arka arkaya istek yağdırmayalım.
_SON_ISTEK: dict[str, float] = {}
ISTEKLER_ARASI_SN = 1.0


class FetchError(Exception):
    """İndirme başarısız — kullanıcıya gösterilebilir mesaj taşır."""


@dataclass
class FetchedPage:
    url: str            # yönlendirmelerden sonraki nihai adres
    status: int
    content_type: str
    text: str
    encoding: str
    elapsed_ms: int
    rendered: bool = False


# ── güvenlik ────────────────────────────────────────────────────


def _ozel_adres_mi(ip: str) -> bool:
    try:
        adres = ipaddress.ip_address(ip)
    except ValueError:
        return True  # çözemediysek güvenme
    return (
        adres.is_private or adres.is_loopback or adres.is_link_local
        or adres.is_reserved or adres.is_multicast or adres.is_unspecified
    )


def dogrula(url: str) -> str:
    """URL'i denetler; sorun varsa FetchError fırlatır."""
    ayristirilmis = urlparse(url)

    if ayristirilmis.scheme not in ("http", "https"):
        # Modele giden mesaj EYLEME DÖNÜK olmalı (aynı gerekçe: sandbox/base.py
        # boru tamponu hatası). GERÇEK TUR: agent yerel bir PDF'i `file://` ile
        # açmaya çalıştı, sadece kısıtı bildiren bu mesajı aldı ve dosya elinin
        # altındayken tekrar okumayı denemeden 3 adımda pes etti.
        ek = (
            " Yerel dosyayı fetch_url ile açamazsın — run_python içinde "
            "data_path('dosya_adi') ile oku."
            if ayristirilmis.scheme == "file"
            else ""
        )
        raise FetchError(
            f"fetch_url sadece http/https adreslerini açar "
            f"(gelen: {ayristirilmis.scheme or 'yok'}).{ek}"
        )
    if not ayristirilmis.hostname:
        raise FetchError("URL'de alan adı yok.")

    host = ayristirilmis.hostname.lower()

    if config.FETCH_ALLOWED_DOMAINS:
        izinli = any(
            host == d or host.endswith("." + d) for d in config.FETCH_ALLOWED_DOMAINS
        )
        if not izinli:
            raise FetchError(
                f"'{host}' izinli alan adları listesinde değil "
                f"({', '.join(config.FETCH_ALLOWED_DOMAINS)})."
            )

    if not config.FETCH_ALLOW_PRIVATE_HOSTS:
        try:
            kayitlar = socket.getaddrinfo(host, None)
        except socket.gaierror as e:
            raise FetchError(f"Alan adı çözülemedi: {host} ({e})") from e

        for kayit in kayitlar:
            ip = kayit[4][0]
            if _ozel_adres_mi(ip):
                raise FetchError(
                    f"'{host}' özel/iç ağ adresine ({ip}) çözülüyor — "
                    "güvenlik gereği engellendi."
                )

    return host


async def _robots_izin_veriyor_mu(client: httpx.AsyncClient, url: str) -> bool:
    if not config.RESPECT_ROBOTS_TXT:
        return True

    parca = urlparse(url)
    robots_url = f"{parca.scheme}://{parca.netloc}/robots.txt"
    try:
        r = await client.get(robots_url, timeout=5)
        if r.status_code >= 400:
            return True  # robots.txt yoksa serbest kabul edilir
        ayristirici = RobotFileParser()
        ayristirici.parse(r.text.splitlines())
        return ayristirici.can_fetch(config.FETCH_USER_AGENT, url)
    except (httpx.HTTPError, ValueError):
        return True  # okunamadıysa engelleme


async def _nezaket_bekle(host: str) -> None:
    son = _SON_ISTEK.get(host)
    if son is not None:
        gecen = time.monotonic() - son
        if gecen < ISTEKLER_ARASI_SN:
            await asyncio.sleep(ISTEKLER_ARASI_SN - gecen)
    _SON_ISTEK[host] = time.monotonic()


# ── indirme ─────────────────────────────────────────────────────


async def fetch(url: str, *, render: bool = False) -> FetchedPage:
    """Sayfayı indirir. render=True ise JS çalıştırılır (Playwright)."""
    dogrula(url)
    if render:
        return await _playwright_ile(url)
    return await _httpx_ile(url)


async def _httpx_ile(url: str) -> FetchedPage:
    basladi = time.monotonic()
    basliklar = {
        "User-Agent": config.FETCH_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
    }

    async with httpx.AsyncClient(
        timeout=config.FETCH_TIMEOUT_SEC,
        headers=basliklar,
        follow_redirects=False,  # her adımı kendimiz denetleyeceğiz
    ) as client:
        suanki = url

        for adim in range(config.FETCH_MAX_REDIRECTS + 1):
            host = dogrula(suanki)  # yönlendirme sonrası TEKRAR denetle

            if adim == 0 and not await _robots_izin_veriyor_mu(client, suanki):
                raise FetchError(
                    f"robots.txt bu adresi kazımaya izin vermiyor: {suanki}"
                )

            await _nezaket_bekle(host)

            try:
                yanit = await client.get(suanki)
            except httpx.TimeoutException as e:
                raise FetchError(
                    f"{config.FETCH_TIMEOUT_SEC} saniyede yanıt gelmedi: {suanki}"
                ) from e
            except httpx.HTTPError as e:
                raise FetchError(f"Bağlantı hatası: {e}") from e

            if yanit.is_redirect:
                hedef = yanit.headers.get("location")
                if not hedef:
                    raise FetchError("Yönlendirme var ama hedef adres yok.")
                suanki = urljoin(suanki, hedef)
                continue

            if yanit.status_code >= 400:
                raise FetchError(f"HTTP {yanit.status_code} — {suanki}")

            govde = yanit.content
            if len(govde) > config.FETCH_MAX_BYTES:
                raise FetchError(
                    f"Sayfa {config.FETCH_MAX_BYTES // 1024 // 1024} MB sınırını aştı."
                )

            return FetchedPage(
                url=str(yanit.url),
                status=yanit.status_code,
                content_type=yanit.headers.get("content-type", ""),
                text=yanit.text,
                encoding=yanit.encoding or "utf-8",
                elapsed_ms=int((time.monotonic() - basladi) * 1000),
            )

        raise FetchError(
            f"{config.FETCH_MAX_REDIRECTS} yönlendirmeden sonra hedefe ulaşılamadı."
        )


async def _playwright_ile(url: str) -> FetchedPage:
    """JS ile gelen içerik için. Pahalı — ancak httpx yetmezse."""
    basladi = time.monotonic()
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise FetchError(
            "Playwright kurulu değil. Kur:\n"
            "  pip install playwright && playwright install chromium\n"
            "Ya da render=False ile dene — çoğu sayfada yeterli olur."
        ) from e

    try:
        async with async_playwright() as p:
            tarayici = await p.chromium.launch(args=["--disable-dev-shm-usage"])
            try:
                sayfa = await tarayici.new_page(user_agent=config.FETCH_USER_AGENT)
                yanit = await sayfa.goto(
                    url,
                    wait_until="networkidle",
                    timeout=config.FETCH_RENDER_TIMEOUT_SEC * 1000,
                )
                html = await sayfa.content()
                durum = yanit.status if yanit else 200
                son_url = sayfa.url
            finally:
                await tarayici.close()
    except Exception as e:
        raise FetchError(f"Sayfa render edilemedi: {e}") from e

    return FetchedPage(
        url=son_url,
        status=durum,
        content_type="text/html",
        text=html,
        encoding="utf-8",
        elapsed_ms=int((time.monotonic() - basladi) * 1000),
        rendered=True,
    )
