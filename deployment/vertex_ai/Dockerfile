FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt /app/deployment-requirements.txt
RUN pip install --upgrade pip && pip install -r /app/deployment-requirements.txt

COPY . /app
RUN pip install --no-deps -e /app

EXPOSE 8080

# Vertex AI custom prediction routines call /health and /predict.
CMD ["uvicorn", "deployment.vertex_ai.serve:app", "--host", "0.0.0.0", "--port", "8080"]
