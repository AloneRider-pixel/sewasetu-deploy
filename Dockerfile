FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends libxml2 libssl3 && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/sewasetu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m -u 1000 user 2>/dev/null; chown -R 1000:1000 /srv/sewasetu
USER 1000
ENV PORT=7860
EXPOSE 7860
CMD ["python", "deploy_start.py"]
