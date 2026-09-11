# Hugging Face Spaces (Docker SDK) runs this image and expects the app on $PORT.
# The same image works on Cloud Run / Render if we ever move.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Spaces run containers as uid 1000; a named user keeps $HOME writable.
RUN useradd -m -u 1000 user
WORKDIR /app

COPY --chown=user:user requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user:user app ./app
COPY --chown=user:user web ./web
COPY --chown=user:user data ./data
COPY --chown=user:user samples ./samples

USER user
ENV HOME=/home/user \
    PORT=7860
EXPOSE 7860

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
