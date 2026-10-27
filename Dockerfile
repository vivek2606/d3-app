FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces run the container as uid 1000.
RUN useradd -m -u 1000 app
WORKDIR /app

# WITH_DEMUCS=0 builds a small HPSS-only image.  TORCH_INDEX_URL picks the
# CPU-only PyTorch build (much smaller); set it empty to use PyPI's default.
ARG WITH_DEMUCS=1
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
COPY requirements*.txt ./
RUN if [ "$WITH_DEMUCS" = "1" ]; then \
        if [ -n "$TORCH_INDEX_URL" ]; then pip install --no-cache-dir torch --index-url "$TORCH_INDEX_URL"; fi \
        && pip install --no-cache-dir -r requirements-demucs.txt; \
    else pip install --no-cache-dir -r requirements.txt; fi

USER app
ENV HOME=/home/app \
    TAALLOOP_DATA_DIR=/home/app/data \
    PORT=7860
# Bake the htdemucs model into the image so the first song doesn't wait for a download.
RUN if [ "$WITH_DEMUCS" = "1" ]; then python -c "from demucs.pretrained import get_model; get_model('htdemucs')"; fi

COPY --chown=app taalloop ./taalloop
COPY --chown=app frontend ./frontend
EXPOSE 7860
# exec keeps uvicorn as PID 1 so it receives stop signals; sh expands ${PORT}.
CMD ["sh", "-c", "exec uvicorn taalloop.main:app --host 0.0.0.0 --port ${PORT}"]
