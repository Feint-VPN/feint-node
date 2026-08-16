# Use Python 3.11 slim image as base
FROM python:3.11-slim

# Metadata labels for GitHub Container Registry
LABEL org.opencontainers.image.source="https://github.com/Feint-VPN/feint-node"
LABEL org.opencontainers.image.description="Feint VPN Node API - Automated VPN node management with sing-box"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.title="Feint VPN Node"
LABEL org.opencontainers.image.vendor="Feint VPN"

# Set working directory
WORKDIR /app

# Install system dependencies
# docker.io is needed for Docker socket communication
RUN apt-get update && apt-get install -y \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

# Copy requirements first for better layer caching
COPY --chown=appuser:appuser requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY --chown=appuser:appuser src/ ./src/

# Switch to non-root user
USER appuser

# Set Python path to include src directory
ENV PYTHONPATH=/app/src

# Expose API port (configurable via environment variable)
EXPOSE ${API_PORT:-8000}

# Run uvicorn; skip SSL when DEV_MODE=true
CMD ["sh", "-c", "if [ \"${DEV_MODE:-false}\" = \"true\" ]; then exec uvicorn main:app --host 0.0.0.0 --port ${API_PORT:-8000}; else exec uvicorn main:app --host 0.0.0.0 --port ${API_PORT:-8000} --ssl-keyfile ${TLS_KEY_PATH} --ssl-certfile ${TLS_CERT_PATH}; fi"]
