FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /app/app/templates /app/app/static

COPY main.py /app/app/main.py
COPY index.html /app/app/templates/index.html

# Keep FastAPI StaticFiles mount valid even if styles.css has not been uploaded yet.
RUN touch /app/app/static/styles.css

EXPOSE 8000

CMD ["sh","-c","uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
