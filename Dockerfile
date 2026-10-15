FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/app/model-cache HF_HUB_DISABLE_XET=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM base AS topics
ENV HF_HOME=/app/model-cache/huggingface TOPIC_ARTIFACTS_DIR=/app/topic-artifacts \
    TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=4 NUMBA_NUM_THREADS=4
COPY requirements-topics.txt .
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && \
    pip install --no-cache-dir -r requirements-topics.txt && \
    apt-get purge -y build-essential && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY app ./app
COPY site ./site
COPY tests ./tests
COPY data/defpea.redif ./data/defpea.redif
COPY data/retrospectives.json ./data/retrospectives.json
COPY data/topic-labels.json ./data/topic-labels.json
ENTRYPOINT ["python", "-m", "app.topics"]

FROM base AS citations
ENV CITATION_ARTIFACTS_DIR=/app/citation-artifacts MPLCONFIGDIR=/tmp/matplotlib
COPY requirements-citations.txt .
RUN pip install --no-cache-dir -r requirements-citations.txt
COPY app ./app
COPY tests ./tests
COPY data/citation-decisions.json ./data/citation-decisions.json
COPY data/topic-labels.json ./data/topic-labels.json
ENTRYPOINT ["python", "-m", "app.citations"]

FROM base AS web
# Bundle Plotly locally, without installing modelling dependencies in the web image.
RUN pip install --no-cache-dir --target=/tmp/plotly plotly==6.3.1 && \
    mkdir -p /app/vendor && cp /tmp/plotly/plotly/package_data/plotly.min.js /app/vendor/plotly.min.js && \
    rm -rf /tmp/plotly
COPY app ./app
COPY site ./site
COPY tests ./tests
COPY data/defpea.redif ./data/defpea.redif
COPY data/retrospectives.json ./data/retrospectives.json
COPY data/topic-labels.json ./data/topic-labels.json
EXPOSE 8789
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8789"]
