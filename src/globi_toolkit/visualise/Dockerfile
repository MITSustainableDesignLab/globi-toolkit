ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim-bookworm AS main
COPY --from=ghcr.io/astral-sh/uv:0.9.30 /uv /uvx /bin/


# Install dependencies for running energy models
# etc
RUN apt-get update && \
    apt-get install --no-install-recommends -y \
        build-essential ca-certificates \
        libx11-6 libexpat1 libgomp1 libcurl4-openssl-dev libssl-dev libxml2-dev gfortran \
        curl tk python3-tk \
        git \
        gdal-bin libgdal-dev libproj-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*


WORKDIR /code
COPY uv.lock pyproject.toml README.md /code/
# the workspace member's metadata, so `uv sync --locked` can resolve the workspace
COPY apps/sbem-ui/pyproject.toml apps/sbem-ui/README.md /code/apps/sbem-ui/

RUN uv sync --locked --no-install-workspace --no-dev

# --no-sync: the project's src/ is not copied yet, so don't try to install it here
RUN uv run --no-sync epi prisma generate

COPY src /code/src/
COPY apps/sbem-ui/lib /code/apps/sbem-ui/lib/

COPY src/globi_toolkit/visualise/config.toml /code/.streamlit/config.toml

RUN uv sync --locked --no-dev


# --no-sync: the image is already synced (without dev tools); don't re-sync at start-up
CMD [ "uv", "run", "--no-sync", "streamlit", "run", "src/globi_toolkit/visualise/main.py" ]
