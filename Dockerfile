FROM python:3.12-alpine

WORKDIR /app

COPY App_Backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY App_Backend/settings_server.py ./settings_server.py
COPY Agents ./Agents
COPY App_Frontend ./App_Frontend
COPY Clouds ./Clouds
COPY Projects/Default /workspace/Projects/Default
COPY App_State /workspace/App_State

RUN python /app/App_Frontend/generate_catalogs.py \
	&& mkdir -p /app/App_Frontend/icons/azure \
	&& cp -R /app/Clouds/Azure/Icons/. /app/App_Frontend/icons/azure/ \
	&& cp /app/Clouds/Azure/Azure-Icon.png /app/App_Frontend/icons/azure-icon.png \
	&& cp /app/Clouds/AWS/AWS-Icon.png /app/App_Frontend/icons/aws-icon.png \
	&& cp /app/Clouds/GCP/GCP-Icon.png /app/App_Frontend/icons/gcp-icon.png

EXPOSE 3000

CMD ["uvicorn", "settings_server:app", "--host", "0.0.0.0", "--port", "3000"]
