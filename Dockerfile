# 1) Сборочный слой
FROM python:3.12-slim AS build
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --upgrade pip && pip wheel --wheel-dir /wheels -r requirements.txt

# 2) Рантайм
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1

# системные пакеты при необходимости (sqlite уже есть)
# RUN apt-get update && apt-get install -y --no-install-recommends ... && rm -rf /var/lib/apt/lists/*

COPY --from=build /wheels /wheels
RUN pip install --no-cache /wheels/*

# Копируем код
COPY . .

# Cloud Run слушает порт в $PORT
ENV PORT=8080
EXPOSE 8080

# Uvicorn слушает на 0.0.0.0:$PORT
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
