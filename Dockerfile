# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

# Use a multi-arch compatible Python image as the base
FROM python:3.11-slim-bullseye

# Set the working directory in the container
WORKDIR /app

# Install system dependencies
# - build-essential: For compiling Python packages (e.g., from source)
# - portaudio19-dev: Required for PyAudio, a dependency of RealtimeSTT
# - ffmpeg: Required by aiortc/av for audio/video processing
# - espeak-ng: A text-to-speech engine for pyttsx3 on Linux
# - curl: Needed to download the Node.js setup script
# - chromium & dependencies: Required for puppeteer (used by whatsapp-web.js)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    portaudio19-dev \
    ffmpeg \
    espeak-ng \
    curl \
    ca-certificates \
    git \
    gnupg \
    # Below are dependencies for running Chromium/Puppeteer
    chromium \
    libgconf-2-4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libgdk-pixbuf2.0-0 \
    libgtk-3-0 \
    libnss3 \
    libx11-xcb1 \
    libxss1 \
    fonts-liberation \
    libappindicator3-1 \
    libasound2 \
    libdrm2 \
    libxkbcommon0 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js v22
RUN apt-get update && apt-get install -y ca-certificates curl gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && NODE_MAJOR=22 \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_$NODE_MAJOR.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y nodejs

# Copy dependency manifests
COPY requirements.txt ./
COPY requirements ./requirements
COPY scripts/gen_locked_constraints.py ./scripts/gen_locked_constraints.py
COPY scripts/install_realtimestt_runtime.py ./scripts/install_realtimestt_runtime.py
COPY node/whatsapp/package.json ./node/whatsapp/
COPY node/whatsapp/package-lock.json ./node/whatsapp/

# Install dependencies
# This layer is cached and only re-runs if the dependency files change.
ARG AUTOYOU_INCLUDE_COGNEE=0
ARG AUTOYOU_INCLUDE_TUNING=0
RUN python -m pip install --upgrade 'pip>=26.1.2,<27' \
    && locked_constraints="$(python scripts/gen_locked_constraints.py)" \
    && python -m pip install --no-cache-dir -r requirements.txt -c "$locked_constraints" \
    && python scripts/install_realtimestt_runtime.py \
    && if [ "$AUTOYOU_INCLUDE_COGNEE" = "1" ]; then python -m pip install --no-cache-dir -r requirements/cognee.txt -c "$locked_constraints"; fi \
    && if [ "$AUTOYOU_INCLUDE_TUNING" = "1" ]; then python -m pip install --no-cache-dir -r requirements/tuning.txt -c "$locked_constraints"; fi \
    && python -m pip check
RUN npm ci --omit=dev --prefix ./node/whatsapp

# Create non-root user and set ownership of /app
RUN groupadd --gid 1000 autoyou \
    && useradd --uid 1000 --gid autoyou --shell /bin/bash --create-home autoyou \
    && chown -R autoyou:autoyou /app

# Copy the rest of the application source code with correct ownership
COPY --chown=autoyou:autoyou . .
RUN test -f vendor/emotivoice/LICENSE \
    && test -f vendor/emotivoice/frontend.py \
    && test -f requirements/voice.txt \
    && python scripts/prepare_intent_router.py

# Pre-cache the standalone tunnelmole binary used by the /pair auth tunnel.
# Baking it into the image layer means /pair works immediately at runtime
# without any network call from inside the container. Private/local builds can
# skip this network fetch when they only need Local Pair or already mount/provide
# AUTOYOU_TUNNELMOLE_BIN.
# To disable auto-download at runtime, pass -e AUTOYOU_NO_DOWNLOAD_TUNNELMOLE=1.
ARG AUTOYOU_SKIP_TUNNELMOLE_DOWNLOAD=0
RUN if [ "$AUTOYOU_SKIP_TUNNELMOLE_DOWNLOAD" = "1" ]; then \
        echo "Skipping tunnelmole pre-cache for this Docker build."; \
    else \
        python scripts/download_tunnelmole.py --quiet || echo "Skipping optional tunnelmole pre-cache due to network restriction."; \
    fi

USER autoyou

ENV AUTOYOU_PACKAGED_RUNTIME=1 \
    AUTOYOU_BIND_HOST=0.0.0.0 \
    AUTOYOU_AI_AGENT_LAN_ACCESS=1

# Expose the ports the server and its services will run on
EXPOSE 8001
EXPOSE 8002
EXPOSE 8067
EXPOSE 8081
EXPOSE 8083
EXPOSE 8481

# Command to run the application, as specified in the project rules
CMD ["python", "server.py", "--admin", "8001", "--ai-agent", "8081"]
