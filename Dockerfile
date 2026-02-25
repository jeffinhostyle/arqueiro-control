FROM python:3.9-slim

WORKDIR /app

# Instalar FFmpeg (necessário para áudio)
RUN apt-get update && apt-get install -y ffmpeg

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

CMD ["python", "bot.py"]
