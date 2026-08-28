FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv/campustoday
RUN addgroup --system campustoday && adduser --system --ingroup campustoday campustoday
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY campus ./campus
RUN chown -R campustoday:campustoday /srv/campustoday
USER campustoday
EXPOSE 8000
CMD ["gunicorn", "--bind=0.0.0.0:8000", "--workers=1", "--threads=4", "--timeout=60", "--access-logfile=-", "--error-logfile=-", "app:create_app()"]

