FROM python:3.12-slim

WORKDIR /app

# libgomp1: dependencia de runtime do faiss-cpu (OpenMP) em imagens slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/

# data/ (indice ja construido) e montado como volume pelo docker-compose —
# nao faz parte da imagem, para nao reconstruir o indice a cada build.
EXPOSE 8000
CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
