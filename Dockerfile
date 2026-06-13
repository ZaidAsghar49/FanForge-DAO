# Build a slim Python container for Hugging Face Spaces
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=7860

# Install system dependencies (build-essential needed for compiling rapidfuzz/xgboost if wheel is missing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements_deploy.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code directories
COPY web_clean /app/web_clean
COPY clean_analysis /app/clean_analysis
COPY scripts /app/scripts

# Copy data resources
COPY bowlers.csv /app/bowlers.csv
COPY Dataset/Processed/cricket_clean_38.db.gz /app/Dataset/Processed/cricket_clean_38.db.gz

# Expose Hugging Face Spaces default port
EXPOSE 7860

# Run uvicorn server on startup
CMD ["uvicorn", "web_clean.app:app", "--host", "0.0.0.0", "--port", "7860"]
