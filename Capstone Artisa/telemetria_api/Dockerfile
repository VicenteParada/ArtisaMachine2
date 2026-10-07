FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt
COPY common/ common/
COPY DB/schema.sql DB/vehiculos_data.sql DB/faenas_data.sql DB/
COPY telemetria_api/ telemetria_api/
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "telemetria_api.worker"]
