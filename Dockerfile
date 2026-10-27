FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Build with --build-arg WITH_DEMUCS=1 for Demucs separation (much larger image).
ARG WITH_DEMUCS=0
COPY requirements*.txt ./
RUN if [ "$WITH_DEMUCS" = "1" ]; then pip install --no-cache-dir -r requirements-demucs.txt; \
    else pip install --no-cache-dir -r requirements.txt; fi

COPY taalloop ./taalloop
COPY frontend ./frontend
ENV TAALLOOP_DATA_DIR=/data
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "taalloop.main:app", "--host", "0.0.0.0", "--port", "8000"]
