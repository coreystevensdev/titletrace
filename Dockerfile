FROM python:3.12-slim

WORKDIR /app

# src/ has to land before the editable install. Installing first leaves the
# package pointing at an empty directory: the build still succeeds and the
# container dies on import.
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir -e ".[dev]"

EXPOSE 8000

CMD ["uvicorn", "titletrace.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
