FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
WORKDIR /app
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
COPY backend/requirements.lock.txt /app/requirements.txt
COPY backend/requirements.txt /app/direct-requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r direct-requirements.txt
COPY backend/app /app/app
COPY tests /app/tests
COPY tools/simulate.py /app/simulate.py
COPY tools/integration_check.py /app/integration_check.py
COPY tools /app/tools
COPY tools/device_simulator.py /app/device_simulator.py
COPY tools/control_integration_check.py /app/control_integration_check.py
COPY tools/ml_integration_check.py /app/ml_integration_check.py
COPY tools/fallback_integration_check.py /app/fallback_integration_check.py
COPY tools/automatic_integration_check.py /app/automatic_integration_check.py
COPY tools/three_units_integration_check.py /app/three_units_integration_check.py
COPY config /app/config
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
