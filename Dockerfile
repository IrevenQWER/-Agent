FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY data ./data
COPY prompts ./prompts

RUN pip install --upgrade pip && pip install '.[rag,ui]'

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/runtime \
    && chown -R appuser:appuser /app/runtime

USER appuser

EXPOSE 8000 7860

CMD ["futureedu-api"]
