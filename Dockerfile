FROM python:3.11-slim
RUN pip install hdbcli websocket-client
COPY src/ /app/
WORKDIR /app
CMD ["python", "hana_connect_aicore.py"]
