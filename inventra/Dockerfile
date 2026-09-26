FROM python:3.12-slim
WORKDIR /app
ARG BUILD_VERSION=dev
LABEL io.inventra.build_version="${BUILD_VERSION}"
COPY requirements.txt pyproject.toml ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir -e .
CMD ["sh", "-c", "python -m alembic upgrade head && python -m inventra_backend.main"]
