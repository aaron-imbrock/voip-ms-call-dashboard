FROM python:3.12-slim
WORKDIR /app
COPY dashboard.py .
EXPOSE 8000
CMD ["python3", "dashboard.py"]
