FROM python:3.12-slim
WORKDIR /srv
COPY webapp/requirements.txt webapp/requirements.txt
RUN pip install --no-cache-dir -r webapp/requirements.txt
COPY app ./app
COPY webapp ./webapp
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "gunicorn --chdir webapp server:app --bind 0.0.0.0:$PORT --timeout 180 --workers 1"]
