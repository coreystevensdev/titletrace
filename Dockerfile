FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

COPY src/ src/

EXPOSE 8000

CMD ["uvicorn", "titletrace.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
