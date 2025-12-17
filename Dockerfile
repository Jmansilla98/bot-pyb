FROM python:3.11-slim

WORKDIR /app

# Copiamos dependencias si existe requirements.txt
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt || true

# Si no tienes requirements.txt, instalamos lo mínimo aquí:
RUN pip install --no-cache-dir discord.py aiohttp

COPY . /app

ENV PYTHONUNBUFFERED=1
ENV PORT=8080

EXPOSE 8080

CMD ["python", "bot.py"]
