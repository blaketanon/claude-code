FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
# Linux torch wheels from PyPI include CUDA; the same image runs on CPU when no GPU is present.
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py engines.py ./
ENV VOICE_DATA_DIR=/data HF_HOME=/models
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
