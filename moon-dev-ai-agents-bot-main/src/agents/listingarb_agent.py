"""
🌙 Moon Dev's Listing Arbitrage Agent 🔍

=================================
📚 OVERVIEW & DOCUMENTATION
=================================

This agent is designed to find potential "gem" tokens before they get listed on major exchanges.
It's specifically built to analyze Solana tokens that meet these criteria:
- Not yet listed on Binance or Coinbase
- Market cap under $10M
- 24h volume over $100k

Key Features:
------------
1. Dual AI Analysis System:
   - Technical Analysis Agent (Default: Claude Haiku)
     • Analyzes price charts and patterns
     • Studies volume trends
     • Identifies support/resistance levels
     • Evaluates OHLCV patterns
   
   - Fundamental Analysis Agent (Default: Claude Sonnet)
     • Evaluates project quality
     • Assesses team and development
     • Analyzes market positioning
     • Reviews technical analysis findings

2. Data Collection:
   - Fetches 14 days of historical data
   - Uses 4-hour candle intervals
   - Calculates key metrics:
     • Volatility
     • Price trends
     • Volume patterns
     • Support/resistance levels

3. AI Model Flexibility:
   - Supports multiple AI models:
     • Claude models (default)
     • DeepSeek Chat
     • DeepSeek Reasoner
   - Easy model switching via MODEL_OVERRIDE setting

4. Performance Optimization:
   - Parallel processing (50 processes)
   - 24-hour analysis cycles
   - Smart token skipping:
     • Recently analyzed tokens
     • Stablecoins
     • Wrapped tokens

5. Results & Storage:
   - Main results: src/data/ai_analysis.csv
   - Buy signals: src/data/ai_analysis_buys.csv
   - Agent memory: src/data/agent_memory/

Usage:
------
1. Ensure required API keys in .env:
   - ANTHROPIC_KEY (for Claude)
   - DEEPSEEK_KEY (for DeepSeek)
   - COINGECKO_API_KEY

2. Configure model preference:
   MODEL_OVERRIDE = "0"           # Use Claude (default)
   MODEL_OVERRIDE = "deepseek-chat"    # Use DeepSeek Chat
   MODEL_OVERRIDE = "deepseek-reasoner" # Use DeepSeek Reasoner

3. Run the agent:
   python src/agents/listingarb_agent.py

Output Files:
------------
1. ai_analysis.csv:
   - Full analysis of all tokens
   - Includes both agent recommendations
   - Price and volume data
   - Timestamps of analysis

2. ai_analysis_buys.csv:
   - Filtered list of "BUY" recommendations
   - Only tokens under $10M market cap
   - Sorted by timestamp (newest first)

Memory System:
-------------
- Stores last 100 analyses per agent
- Tracks promising tokens
- Maintains conversation history
- Auto-cleans old records

Performance Notes:
----------------
- Analyzes ~1000 tokens per run
- Takes 2-3 hours for full analysis
- Uses rate limiting for API calls
- Parallel processing reduces runtime

Created by Moon Dev 🌙
For updates: https://github.com/moon-dev-ai-agents-for-trading
"""

import os
import pandas as pd
import json
from typing import Dict, List
from datetime import datetime, timedelta
import time
from pathlib import Path
from termcolor import colored, cprint
from dotenv import load_dotenv
import requests
import numpy as np
import concurrent.futures
import src.config as config
from src.bedrock_llm import bedrock_chat_sync, ChatMessage, ChatOptions

# Load environment variables
load_dotenv()

# Model override settings
# Set to "0" to use config.py's AI_MODEL setting
# Available models:
# - "deepseek-chat" (DeepSeek's V3 model - fast & efficient)
# - "deepseek-reasoner" (DeepSeek's R1 reasoning model)
# - "0" (Use config.py's AI_MODEL setting)
MODEL_OVERRIDE = "deepseek-chat"  # Set to "0" to disable override

# DeepSeek API settings
DEEPSEEK_BASE_URL = "https://api.deepseek.com"  # Base URL for DeepSeek API

# 🤖 Agent Model Selection
AI_MODEL = MODEL_OVERRIDE if MODEL_OVERRIDE != "0" else config.AI_MODEL

# 📁 File Paths
DISCOVERED_TOKENS_FILE = Path("src/data/discovered_tokens.csv")  # Input from token discovery script
AI_ANALYSIS_FILE = Path("src/data/ai_analysis.csv")  # AI analysis results

# 🤖 CoinGecko API Settings
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")
COINGECKO_BASE_URL = "https://pro-api.coingecko.com/api/v3"
TEMP_DATA_DIR = Path("src/data/temp_data")

# ⚙️ Configuration
HOURS_BETWEEN_RUNS = 24        # Run AI analysis every 24 hours to manage API costs
PARALLEL_PROCESSES = 50        # Number of parallel processes to run
MIN_VOLUME_USD = 100_000      # Minimum 24h volume to analyze
MAX_MARKET_CAP = 10_000_000   # Maximum market cap to include in analysis (10M)

# 🤖 Tokens to Ignore
DO_NOT_ANALYZE = [
    'tether',           # USDT - Stablecoin
    'usdt',            # Alternative USDT id
    'usdtsolana',      # Solana USDT
    'usdc',            # USDC
    'usd-coin',        # Alternative USDC id
    'busd',            # Binance USD
    'dai',             # DAI
    'frax',            # FRAX
    'true-usd',        # TUSD
    'wrapped-bitcoin',  # WBTC
    'wrapped-solana',  # WSOL
]

# 🤖 Agent Prompts
AGENT_ONE_PROMPT = """
You are the Technical Analysis Agent 📊
Your role is to analyze token metrics, market data, and OHLCV patterns.

IMPORTANT: Start your response with one of these recommendations:
RECOMMENDATION: BUY
RECOMMENDATION: SELL
RECOMMENDATION: DO NOTHING

Then provide your detailed analysis.

Focus on:
- Volume trends and liquidity patterns
- Price action and momentum using OHLCV data
- Support and resistance levels from price history
- Market cap relative to competitors
- Technical indicators and patterns
- 4-hour chart analysis for the past 14 days

Help Moon Dev identify tokens with strong technical setups! 🎯
"""

AGENT_TWO_PROMPT = """
You are the Fundamental Analysis Agent 🔬
Your role is to analyze project fundamentals and potential.

IMPORTANT: Start your response with one of these recommendations:
RECOMMENDATION: BUY
RECOMMENDATION: SELL
RECOMMENDATION: DO NOTHING

Then provide your detailed analysis.

Focus on:
- Project technology and innovation
- Team background and development activity
- Community growth and engagement
- Competition and market positioning
- Growth potential and risks
- How the technical analysis aligns with fundamentals

Help Moon Dev evaluate which tokens have the best fundamentals! 🚀
"""

class AIAgent:
    """AI Agent for analyzing tokens"""
    
    def __init__(self, name: str, model: str):
        self.name = name
        self.model = model
        
        # Initialize appropriate client based on model
        if "deepseek" in self.model.lower():
            # Bedrock via bedrock_llm.py - no old API keys needed
            pass

        # Initialize Anthropic client for other models
        self.client = None

if __name__ == "__main__":
    main()