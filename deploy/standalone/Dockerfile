FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core ./core

EXPOSE 8787

CMD ["python3", "-m", "core.converter", "--host", "0.0.0.0", "--port", "8787", "--skip-check"]
