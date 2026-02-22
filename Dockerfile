FROM python:3.12-alpine AS catalog-builder

WORKDIR /app
COPY Clouds ./Clouds
COPY App_Frontend/generate_catalogs.py ./App_Frontend/generate_catalogs.py
RUN python ./App_Frontend/generate_catalogs.py

FROM nginx:1.27-alpine

COPY App_Frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY App_Frontend/index.html /usr/share/nginx/html/index.html
COPY App_Frontend/styles.css /usr/share/nginx/html/styles.css
COPY App_Frontend/app.js /usr/share/nginx/html/app.js
COPY --from=catalog-builder /app/App_Frontend/catalogs/ /usr/share/nginx/html/catalogs/
COPY Clouds/Azure/Icons/ /usr/share/nginx/html/icons/azure/

EXPOSE 3000
