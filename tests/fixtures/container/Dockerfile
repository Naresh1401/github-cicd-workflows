FROM python:3.13-alpine
WORKDIR /app
COPY server.py .
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}
USER 65534
EXPOSE 8000
CMD ["python", "server.py"]
