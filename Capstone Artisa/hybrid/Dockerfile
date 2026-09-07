FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt
COPY lab/ lab/
COPY tests/ tests/
COPY fixtures/ fixtures/
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["python", "-m", "lab.service"]
