# ---- UI build ----
FROM node:22-slim AS ui
WORKDIR /ui
COPY admin-ui/package*.json ./
RUN npm ci --no-audit --no-fund
COPY admin-ui/ ./
RUN npm run build

# ---- Python runtime ----
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY alphaforge ./alphaforge
RUN pip install -e .
COPY --from=ui /ui/dist ./admin-ui/dist
VOLUME ["/app/data"]
EXPOSE 8000
CMD ["alphaforge", "serve", "--host", "0.0.0.0", "--port", "8000"]
