FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/app/model-cache HF_HUB_DISABLE_XET=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY site ./site
COPY tests ./tests
COPY data/defpea.redif ./data/defpea.redif
EXPOSE 8789
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8789"]
