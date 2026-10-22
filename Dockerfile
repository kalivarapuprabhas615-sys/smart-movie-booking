FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000 DATABASE_PATH=/data/smartmovie.db COOKIE_SECURE=1
VOLUME /data
EXPOSE 8000
# ONE worker: the 15-minute pricing scheduler thread runs inside the process; SQLite serialises writers anyway.
CMD gunicorn wsgi:app --workers 1 --threads 8 --bind 0.0.0.0:${PORT}
