FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app.py ./
COPY gwconsole ./gwconsole
COPY .streamlit ./.streamlit

# Non-root, code is read-only to the running user.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin console \
    && chown -R root:root /app && chmod -R a-w /app
USER 10001

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3).read()==b'ok' else 1)"

# 0.0.0.0 only because the container needs it; publish the port on loopback
# (-p 127.0.0.1:8501:8501) and put Cloudflare Tunnel in front. The app refuses
# CONSOLE_AUTH=none on a non-loopback bind, so this image cannot run unauthenticated.
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
