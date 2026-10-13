FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
WORKDIR /app
# LightGBM (predicción de actividad) necesita OpenMP del sistema.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
COPY machine_learning/requirements.txt machine-requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt -r machine-requirements.txt
COPY common/ common/
COPY DB/schema.sql DB/vehiculos_data.sql DB/faenas_data.sql DB/
COPY machine_learning/__init__.py machine_learning/diario.py machine_learning/diario_worker.py machine_learning/viajes.py machine_learning/rutas.py machine_learning/rutas_job.py machine_learning/validar_rutas.py machine_learning/prediccion.py machine_learning/
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "machine_learning.diario_worker"]
