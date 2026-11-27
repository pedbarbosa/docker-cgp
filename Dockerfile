# Collectd Graph Panel in Python: interactive graphs of collectd's RRD files,
# with rrdtool drawing PNG/SVG images. See README.md.
FROM python:3.14-slim
LABEL org.opencontainers.image.authors="Pedro Barbosa, https://github.com/pedbarbosa"

# The docker-cgp commit it's built from, shown in the page footer
ARG REVISION=dev

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=80 \
    CGP_REVISION=${REVISION}

# For the build only, so apt doesn't try to prompt
ARG DEBIAN_FRONTEND=noninteractive

# rrdtool draws the graphs; curl is for health checks. Build rrdtool's font
# cache now, and let CGP's user (below) refresh it, so rrdtool never warns
# about fontconfig at runtime.
RUN apt-get update --error-on=any \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends rrdtool curl sudo \
    && fc-cache -f \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /usr/share/doc/* /usr/share/info/*

WORKDIR /app

# Dependencies first, so a code change doesn't reinstall them
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY LICENSE ./
COPY cgp ./cgp
COPY src/entrypoint.sh /entrypoint.sh

# Run as an unprivileged user, which can still bind port 80 under Docker.
# sudo lets package managers (e.g. patch_containers) run apt inside the
# container when it is running as non-root.
RUN useradd --system --uid 10001 --no-create-home cgp \
    && echo '%sudo ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/nopasswd \
    && chmod 440 /etc/sudoers.d/nopasswd \
    && usermod -aG sudo cgp \
    && mkdir -p /etc/cgp/plugin \
    && chown -R cgp:cgp /var/cache/fontconfig

USER cgp

EXPOSE 80

HEALTHCHECK --interval=1m --timeout=5s --start-period=30s \
    CMD curl -fsS -o /dev/null "http://localhost:${PORT}/" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
