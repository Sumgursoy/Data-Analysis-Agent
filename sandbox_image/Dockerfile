# Agent'ın yazdığı kodun koştuğu izole ortam.
# Build:  docker build -t analyst-sandbox:latest ./sandbox_image
#
# Bu imajda LLM istemcisi, web sunucusu veya ağ istemcisi YOKTUR.
# Container --network none ile çalışır; tek işi kod çalıştırmaktır.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/mpl \
    HOME=/tmp

# matplotlib'in Türkçe karakterleri düzgün çizmesi için font.
# fonts-dejavu: ş, ğ, İ, ı, ç, ö, ü hepsini kapsar.
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY kernel_server.py analyst.mplstyle ./

# Kök dosya sistemi salt-okunur çalıştırılacağı için (--read-only),
# yazılabilir tek yerler /tmp (tmpfs) ve /artifacts (mount).
RUN useradd --uid 1000 --create-home --home-dir /home/analyst analyst \
 && mkdir -p /data /artifacts \
 && chown -R analyst:analyst /app /data /artifacts

USER analyst

# Backend stdin/stdout üzerinden JSON konuşur — bkz. kernel_server.py
ENTRYPOINT ["python", "-u", "kernel_server.py"]
