# Multi-stage build: dependencies compile in the builder, only the runtime
# artefacts reach the final image. Keeps the shipped image small and free of
# compilers, which are useful to an attacker and useless to the application.

FROM python:3.12-slim AS builder

WORKDIR /build

# Install into a virtualenv so the whole tree can be copied in one layer.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip "setuptools>=78.1.1" && \
    pip install --no-cache-dir -r requirements.txt


FROM python:3.12-slim AS runtime

# Unprivileged user, created before copying anything so ownership is right.
RUN groupadd --gid 10001 app && \
    useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY --chown=app:app app/ ./app/

USER app

EXPOSE 8000

# No shell form: exec form means the process is PID 1 and receives SIGTERM
# directly, so Kubernetes can shut it down gracefully instead of waiting for
# the grace period to expire and sending SIGKILL.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
