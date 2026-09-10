FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5843
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:5843", "--timeout", "60", "wsgi:app"]
