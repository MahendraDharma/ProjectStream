FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY server ./server
RUN pip install --no-cache-dir .

RUN mkdir -p /data /cache /media

EXPOSE 8080

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
