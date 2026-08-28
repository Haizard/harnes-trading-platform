FROM python:3.12-slim

WORKDIR /app

# Install system dependencies including git
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy the trading platform code
COPY moon-dev-ai-agents-bot-main/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY moon-dev-ai-agents-bot-main/ .

# Create data directories
RUN mkdir -p src/data/scanner src/data/sniper src/data/paper_trading src/data/micro_engine src/data/orchestrator src/data/sentiment

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

EXPOSE 8000

# Run engine + dashboard with explicit error handling
CMD ["python", "-u", "run_full.py"]
