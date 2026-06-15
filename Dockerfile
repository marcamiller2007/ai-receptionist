# Use an official lightweight Python runtime layer
FROM python:3.11-slim

# Prevent Python from writing pyc files to disk and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies needed for compiling extensions
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ && rm -rf /var/lib/apt/lists/*

# Copy and install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy your application source code
COPY . .

# Expose FastAPI's default runtime port
EXPOSE 8000

# Fire up Uvicorn with a single high-performance async worker loop
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
