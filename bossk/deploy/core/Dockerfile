# BOSSK core + NAS checker. Runs on TrueNAS SCALE (Apps > Custom App, or docker compose).
FROM python:3.12-slim
RUN sed -i "s/ main/ main contrib/" /etc/apt/sources.list.d/debian.sources && apt-get update && apt-get install -y --no-install-recommends ffmpeg zfsutils-linux cron && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/bossk
# Claude Code CLI: BOSSK's brain, logged in with CLAUDE_CODE_OAUTH_TOKEN (claude setup-token) so it bills the Claude plan
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://claude.ai/install.sh | bash
ENV PATH="/root/.local/bin:${PATH}" CLAUDE_CONFIG_DIR=/data/claude
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV BOSSK_CONFIG=/data/bossk.yml PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8750
CMD ["python3", "-m", "core", "--config", "/data/bossk.yml"]
