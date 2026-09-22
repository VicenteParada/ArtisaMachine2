FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
WORKDIR /app
COPY requirements.txt .
COPY modelo_machine/requirements.txt machine-requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt -r machine-requirements.txt
COPY common/ common/
COPY DB/schema.sql DB/schema.sql
COPY modelo_machine/ modelo_machine/
COPY processing/ processing/
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "modelo_machine.worker"]


