# 1. Pakai mesin Linux Python asli
FROM python:3.10-slim

# 2. Bekerja di folder /app
WORKDIR /app

# 3. Copy daftar requirements dan install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy semua file Python kamu (kecuali yang dilarang di .dockerignore)
COPY . .

# 5. PERINTAH MUTLAK UNTUK MENYALA OTOMATIS
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
