FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

# база лежит в /app/data - этот каталог монтируется как volume
VOLUME /app/data
CMD ["python", "main.py"]
