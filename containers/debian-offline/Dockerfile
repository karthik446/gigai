FROM python:3.11-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.5.25 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 gigai \
    && useradd --uid 10001 --gid gigai --create-home --shell /usr/sbin/nologin gigai

WORKDIR /workspace

COPY --chown=gigai:gigai LICENSE MANIFEST.in README.md pyproject.toml uv.lock ./
COPY --chown=gigai:gigai docs ./docs
COPY --chown=gigai:gigai research ./research
COPY --chown=gigai:gigai src ./src
COPY --chown=gigai:gigai tests ./tests
COPY --chown=gigai:gigai tools ./tools

RUN chown gigai:gigai /workspace

USER gigai

ENV HOME=/home/gigai \
    PATH=/workspace/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy

RUN uv sync --locked --extra test --python python

CMD ["/bin/sh", "tools/run_debian_offline.sh"]
