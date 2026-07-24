FROM python:3.11-slim

# delta — a small maths e-learning host (WSGI). Base toto platform + the revived
# education apps (academy / quizzes / competence / palimpsest / library /
# subscriptions). No Neo4j, no AI, no realtime, no media/ffmpeg. A small Celery
# worker + beat pair (same image) recomputes the recommendation matrix daily.

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# The GIS toolchain (GDAL/GEOS/PROJ) is only needed for a BUILD_GEO=1 build. A
# light image sets BUILD_GEO=0 and this whole ~250 MB apt layer is skipped;
# toto.locations then loads geometry-less. deploy.py passes the build arg from the
# resolved features.
ARG BUILD_GEO=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    libpq-dev \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    libglib2.0-0 \
    curl \
    postgresql-client \
    && if [ "$BUILD_GEO" = "1" ]; then apt-get install -y --no-install-recommends \
        gdal-bin libgdal-dev libproj-dev proj-bin libgeos-dev; fi \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV DJANGO_SETTINGS_MODULE=delta.settings

WORKDIR /app/delta

# Single requirements file: WSGI base (Django + gunicorn + whitenoise + GIS/
# imaging + markdownx/trix for the education apps) + celery/numpy for the
# recommendation matrix. No channels layer.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt
RUN pip install build setuptools wheel

# toto is installed from the wheel deploy.py stages into dist/ (built from a local
# toto_libs checkout, or from the `toto_requirement:` git pin).
COPY dist/ /tmp/toto_dist/
COPY requirements.toto.txt /tmp/toto_dist/requirements.toto.txt
RUN pip install --no-cache-dir --no-index --find-links /tmp/toto_dist \
        -r /tmp/toto_dist/requirements.toto.txt \
 && rm -rf /tmp/toto_dist

# Vendored third-party static (fontawesome, ace, leaflet, …) is excluded from toto
# wheels and fetched from CDNs. Bake it into the installed package at build time so
# the strict whitenoise manifest can resolve every {% static 'vendor/...' %} ref.
COPY delta/scripts/download_vendor.py /tmp/download_vendor.py
RUN python /tmp/download_vendor.py --download-only

COPY data/ /app/data/
COPY delta/ /app/delta/

COPY delta/deploy/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "delta.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4", "--timeout", "120"]
