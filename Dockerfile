# ✅ Используем стабильный образ Python 3.11
FROM python:3.11-slim

# Устанавливаем системные зависимости (нужно для pydantic-core и прочих пакетов)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем requirements.txt отдельно — чтобы использовать кэш Docker
COPY requirements.txt .

# Обновляем pip и устанавливаем зависимости
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Копируем остальные файлы проекта
COPY . .

# Делаем start.sh исполняемым (если используешь его)
RUN chmod +x start.sh || true

# Команда запуска (если нет start.sh)
CMD ["python", "bot.py"]

