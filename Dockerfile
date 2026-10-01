FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py purchase.py clock.py payment_client.py access_client.py gunicorn.conf.py ./
COPY templates/ templates/
COPY static/ static/

EXPOSE 8000

CMD ["gunicorn", "app:create_app()"]
