# Build the virtualenv here, ship only the virtualenv. uv is 37MB and the sync
# layer carries build residue, none of which the running app needs: a
# single-stage build put both in the final image and came out larger than the pip
# build it replaced, despite installing fewer packages.
FROM python:3.12-slim AS builder

# Pinned rather than :latest so the image is reproducible.
COPY --from=ghcr.io/astral-sh/uv:0.8.9 /uv /usr/local/bin/uv

WORKDIR /app

# Copy mode because there is no cache mount to hardlink against. Bytecode is
# deliberately not precompiled: it costs about 90MB, and the instance these
# demos share has 16GiB for six of them and filled up once already.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=0

COPY pyproject.toml uv.lock ./
COPY src/ src/

# Installs from uv.lock, so the container holds exactly the tree the lock records
# and the dependency graph scans, rather than whatever pip resolved from
# pyproject on the day of the build.
#
# No extras. This used to install .[dev], putting pytest, respx and responses in
# the serving image. httpx looks like the reason for that and is not: it is in
# the main dependencies and merely duplicated into the dev extra.
#
# --locked and not --frozen. Both install from the lock; only --locked fails when
# the lock no longer matches pyproject. Measured, since the names suggest the
# reverse.
RUN uv sync --locked --no-editable

FROM python:3.12-slim

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"

COPY --from=builder /app/.venv /app/.venv

EXPOSE 8000

CMD ["uvicorn", "titletrace.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
