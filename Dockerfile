FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
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

# Expose port for frontend (if needed)
EXPOSE 8000

# Default command - run in paper mode
CMD ["python", "run_micro.py"]
