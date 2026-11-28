# ESA E-Shop - production image (Gunicorn). See DEPLOY.md.
# Base image pinned to an exact Python patch release and Debian version; Dependabot proposes updates weekly.
FROM python:3.15.1-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Security updates for the Debian packages in the base image
RUN apt-get update --error-on=any \
 && apt-get upgrade -y --no-install-recommends \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# Run as an ordinary user, not root (no home folder or login shell needed)
RUN useradd --system --no-create-home --shell /usr/sbin/nologin --uid 1000 shop

WORKDIR /app

# Install the dependencies, then remove pip: nothing installs packages at run time
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install -r requirements.txt \
 && pip uninstall -y pip

# The code belongs to root and is read-only for the app user: a bug in the app can't rewrite it.
COPY . .
RUN python -m compileall -q /app/shop /app/app.py \
 && chmod -R a+rX,go-w /app \
 && chmod 0755 docker/entrypoint.sh \
 # Only the folders the app writes to belong to the app user (they are Docker volumes in docker-compose.yml):
 #   static/uploads           product/promotion photos (UPLOAD_FOLDER) and resized copies (static/uploads/_cache)
 #   private_uploads/proofs   packing-proof photos/videos (PROOF_FOLDER)
 && mkdir -p static/uploads/promotions static/uploads/_cache private_uploads/proofs \
 && chown -R shop:shop static/uploads private_uploads

USER shop
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)" || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
