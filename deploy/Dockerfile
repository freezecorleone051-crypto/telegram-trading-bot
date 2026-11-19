# Image Docker du bot de trading
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dépendances d'abord (cache Docker efficace)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code du projet
COPY . .

# data_store et logs sont montés en volumes pour survivre aux redémarrages
VOLUME ["/app/data_store", "/app/logs"]

CMD ["python", "bot.py"]
