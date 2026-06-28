FROM python:3.11-slim

# Security hardening: run as non-root user
RUN useradd --create-home --shell /bin/bash agent

# Install patch utility for applying unified diffs
RUN apt-get update && apt-get install -y patch && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
RUN chown agent:agent /workspace

USER agent

# No COPY — repo is mounted at runtime as read-only volume.
# Working copy is created inside the container at /tmp/workdir.
CMD ["python", "-m", "pytest", "--tb=short", "-q"]
