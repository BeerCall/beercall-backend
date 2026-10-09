ARG PYTHON_IMAGE=python:3.13-slim
FROM ${PYTHON_IMAGE}

WORKDIR /app

# Mise à jour des paquets pour Debian Trixie/Sid
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    libxcb1 \
    libx11-6 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cpu --no-cache-dir
RUN pip install -r requirements.txt --no-cache-dir

COPY . .
RUN mkdir -p uploads/aperos

COPY start.sh .
RUN chmod +x start.sh

EXPOSE 8000

CMD ["./start.sh"]
