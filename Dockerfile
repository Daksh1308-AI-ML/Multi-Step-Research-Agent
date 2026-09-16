# syntax=docker/dockerfile:1

FROM python:3.14-slim AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.14-slim
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels
RUN useradd --create-home --uid 1000 app
USER app
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8000"]