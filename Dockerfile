FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY converter.py responses_adapter.py responses_projection.py anthropic_adapter.py desensitize.py ./

EXPOSE 8787

CMD ["python3", "converter.py", "--host", "0.0.0.0", "--port", "8787", "--skip-check"]
