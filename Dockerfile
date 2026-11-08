# Imagen de la caja (ADR 0016). Se construye en la VM desde una etiqueta git (deploy/box.sh).
# Las imágenes externas se fijan por digest; para actualizar una, cambiar etiqueta y digest juntos.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30

# uv 0.13.7
FROM ghcr.io/astral-sh/uv@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

# restic: binario oficial con sha256 verificado (SHA256SUMS de la release 0.19.1).
FROM ${PYTHON_IMAGE} AS restic
ARG TARGETARCH
ARG RESTIC_VERSION=0.19.1
ARG RESTIC_SHA256_AMD64=f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c
ARG RESTIC_SHA256_ARM64=a5f64aaab53d51e311fa3829124c5b703f2d14cf187d8640b6be3b2b49376465
RUN python - <<'PY'
import bz2, hashlib, os, urllib.request

arch = os.environ["TARGETARCH"]
expected = {"amd64": os.environ["RESTIC_SHA256_AMD64"], "arm64": os.environ["RESTIC_SHA256_ARM64"]}[arch]
version = os.environ["RESTIC_VERSION"]
url = f"https://github.com/restic/restic/releases/download/v{version}/restic_{version}_linux_{arch}.bz2"
data = urllib.request.urlopen(url, timeout=60).read()
if hashlib.sha256(data).hexdigest() != expected:
    raise SystemExit("sha256 de restic no coincide")
with open("/restic", "wb") as out:
    out.write(bz2.decompress(data))
os.chmod("/restic", 0o755)
PY

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-editable --no-install-project --extra s3 --extra workspace --extra server
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable --extra s3 --extra workspace --extra server

FROM ${PYTHON_IMAGE}
ARG ENS_UID=10001
RUN groupadd --gid ${ENS_UID} ens \
    && useradd --uid ${ENS_UID} --gid ${ENS_UID} --no-create-home --home-dir /data --shell /usr/sbin/nologin ens \
    && install -d -o ens -g ens -m 0700 /data
COPY --from=restic /restic /usr/local/bin/restic
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    ENS_STATE_DIR=/data/state \
    ENS_SETTINGS=/data/settings.yaml
USER ens
WORKDIR /data
VOLUME /data
CMD ["ens-evidence", "scheduler", "run"]
