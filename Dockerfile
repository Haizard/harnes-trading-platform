FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y gcc g++ git && rm -rf /var/lib/apt/lists/*

COPY moon-dev-ai-agents-bot-main/requirements-deploy.txt .
RUN pip install --no-cache-dir -r requirements-deploy.txt

COPY moon-dev-ai-agents-bot-main/ .

RUN mkdir -p src/data/scanner src/data/sniper src/data/paper_trading src/data/micro_engine src/data/orchestrator src/data/sentiment

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["python", "-u", "run_deploy.py"]
