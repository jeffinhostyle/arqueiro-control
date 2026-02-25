# Usar uma imagem base oficial do Python
FROM python:3.11-slim

# Instalar dependências do sistema, especialmente o ffmpeg para áudio
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Definir o diretório de trabalho
WORKDIR /app

# Copiar o arquivo de requisitos e instalar as dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar o resto do código do aplicativo
COPY . .

# Comando para executar o aplicativo
CMD ["python", "bot.py"]
