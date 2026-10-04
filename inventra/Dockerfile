FROM python:3.12-slim
WORKDIR /app
ARG BUILD_VERSION=dev
ENV INVENTRA_ADDON_VERSION=${BUILD_VERSION}
LABEL io.inventra.build_version="${BUILD_VERSION}"
COPY requirements.txt pyproject.toml ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir -e .
CMD ["sh", "-c", "python -m alembic upgrade head && python -m inventra_backend.main"]
