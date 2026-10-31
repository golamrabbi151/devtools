FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_ENV=production

RUN useradd --create-home --uid 10001 appuser
WORKDIR /srv/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser wsgi.py gunicorn.conf.py ./
# Knowledge base database lives here; mount a volume so it survives container replacement.
RUN mkdir -p /srv/app/instance && chown appuser:appuser /srv/app/instance
VOLUME /srv/app/instance

USER appuser
EXPOSE 8000

# SECRET_KEY must be supplied at runtime (docker run -e SECRET_KEY=...).
# Behind nginx / a load balancer also set PROXY_HOPS=1 so rate limits use the real client IP.
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
