# ForgeOS core. Runs every command with no network unless keys are provided.
#   docker build -t forgeos .
#   docker run --rm forgeos forge bench
#   docker run --rm --env-file .env -v "$PWD/data:/app/data" -v "$PWD/clients:/app/clients" forgeos forge run-all clients
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
COPY forge ./forge
RUN pip install --no-cache-dir . && mkdir -p /app/data /app/clients /app/fixtures
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["forge"]
CMD ["--help"]
