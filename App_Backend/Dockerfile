FROM python:3.12-alpine

WORKDIR /app
COPY App_Backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY App_Backend/settings_server.py ./settings_server.py
RUN mkdir -p /workspace/Projects /workspace/App_State
COPY Projects/Default /workspace/Projects/Default
COPY App_State /workspace/App_State

EXPOSE 8000
CMD ["uvicorn", "settings_server:app", "--host", "0.0.0.0", "--port", "8000"]
