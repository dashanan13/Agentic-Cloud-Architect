FROM python:3.12-slim

WORKDIR /app

COPY App_Backend/requirements.txt ./requirements.txt
COPY patch_agent_framework.py ./patch_agent_framework.py
RUN apt-get update \
	&& apt-get install -y --no-install-recommends nodejs npm \
	&& rm -rf /var/lib/apt/lists/* \
	&& pip install --no-cache-dir -r requirements.txt \
	&& python3 /app/patch_agent_framework.py

COPY App_Backend/settings_server.py ./settings_server.py
COPY Agents ./Agents
COPY App_Frontend ./App_Frontend
COPY Assets ./Assets
COPY Clouds ./Clouds
COPY Projects/Default /workspace/Projects/Default
RUN mkdir -p /workspace/App_State

RUN python /app/App_Frontend/scripts/generate_catalogs.py \
	&& mkdir -p /app/App_Frontend/icons/azure \
	&& cp -R /app/Clouds/Azure/Icons/. /app/App_Frontend/icons/azure/ \
	&& cp /app/Clouds/Azure/Azure-Icon.png /app/App_Frontend/icons/azure-icon.png \
	&& if [ -f /app/Clouds/AWS/AWS-Icon.png ]; then cp /app/Clouds/AWS/AWS-Icon.png /app/App_Frontend/icons/aws-icon.png; fi \
	&& if [ -f /app/Clouds/GCP/GCP-Icon.png ]; then cp /app/Clouds/GCP/GCP-Icon.png /app/App_Frontend/icons/gcp-icon.png; fi \
	&& cp /app/Clouds/azure-bicep-icon.png /app/App_Frontend/icons/azure-bicep-icon.png \
	&& cp /app/Clouds/terraform-icon.png /app/App_Frontend/icons/terraform-icon.png \
	&& cp -R /app/Assets /app/App_Frontend/Assets

EXPOSE 3000

CMD ["uvicorn", "settings_server:app", "--host", "0.0.0.0", "--port", "3000"]
