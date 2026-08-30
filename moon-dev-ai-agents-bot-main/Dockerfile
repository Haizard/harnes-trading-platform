FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (git needed for pip install from git URLs)
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Ensure twikit is installed (sentiment analysis)
RUN pip install --no-cache-dir twikit==2.3.3

# Copy application code
COPY . .

# Create data directories
RUN mkdir -p src/data/scanner src/data/sniper src/data/paper_trading src/data/micro_engine src/data/orchestrator src/data/sentiment

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Expose port for frontend (if needed)
EXPOSE 8000

# Default command - run in paper mode
CMD ["python", "run_deploy.py"]
