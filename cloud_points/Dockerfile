FROM python:3.13-slim
WORKDIR /app
COPY cloud_points/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && useradd --uid 10001 --create-home points
COPY cloud_points /app/cloud_points
COPY core/android_ledger.py /app/core/android_ledger.py
RUN touch /app/core/__init__.py
USER points
EXPOSE 8080
CMD ["gunicorn", "--bind=0.0.0.0:8080", "--workers=2", "--threads=4", "--timeout=60", "cloud_points.service:create_app()"]
