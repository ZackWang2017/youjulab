FROM python:3.12-slim
RUN pip install --no-cache-dir pandas==2.2.3 numpy==2.2.6
COPY sandbox_worker.py /app/worker.py
ENV PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1
ENTRYPOINT ["python", "-I", "/app/worker.py"]
