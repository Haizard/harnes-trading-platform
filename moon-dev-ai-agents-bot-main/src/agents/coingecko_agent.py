from pathlib import Path

"""
🌙 Moon Dev's CoinGecko Agent 🦎
Provides comprehensive access to CoinGecko API data and market intelligence

=================================
📚 FILE OVERVIEW & DOCUMENTATION
=================================

This file implements a multi-agent AI trading system that analyzes crypto markets using CoinGecko data.
The system consists of three specialized agents working together:

1. Agent One (Technical Analysis) 📊
   - Focuses on charts, patterns, and technical indicators
   - Uses shorter-term analysis for trading opportunities
   - Configured with AGENT_ONE_MODEL and AGENT_ONE_MAX_TOKENS

2. Agent Two (Fundamental Analysis) 🌍
   - Analyzes macro trends and fundamental data
   - Provides longer-term market perspective
   - Configured with AGENT_TWO_MODEL and AGENT_TWO_MAX_TOKENS

3. Token Extractor Agent 🔍
   - Monitors agent conversations
   - Extracts mentioned tokens/symbols
   - Maintains historical token discussion data
   - Uses minimal tokens/temperature for precise extraction

Key Components:
--------------
1. Configuration Section
   - Model selection for each agent
   - Response length control (max_tokens)
   - Creativity control (temperature)
   - Round timing configuration

2. Memory System
   - Stores agent conversations in JSON files
   - Maintains token discussion history in CSV
   - Keeps track of last 50 rounds
   - Auto-cleans old memory files

3. CoinGecko API Integration
   - Comprehensive market data access
   - Rate limiting and error handling
   - Multiple endpoints (prices, trends, history)

4. Game Loop Structure
   - Runs in continuous rounds
   - Each round:
     a. Fetch fresh market data
     b. Agent One analyzes
     c. Agent Two responds
     d. Extract mentioned tokens
     e. Generate round synopsis
     f. Wait for next round

5. Output Formatting
   - Colorful terminal output
   - Clear section headers
   - Structured agent responses
   - Easy-to-read summaries

File Structure:
--------------
1. Configuration & Constants
2. Helper Functions (print_banner, print_section)
3. Core Classes:
   - AIAgent: Base agent functionality
   - CoinGeckoAPI: API wrapper
   - TokenExtractorAgent: Symbol extraction
   - MultiAgentSystem: Orchestrates everything

Usage:
------
1. Ensure environment variables are set:
   - ANTHROPIC_KEY
   - COINGECKO_API_KEY

2. Run the file directly:
   python src/agents/coingecko_agent.py

3. Or import the classes:
   from agents.coingecko_agent import MultiAgentSystem

Configuration:
-------------
Adjust the constants at the top of the file to:
- Change agent models
- Modify response lengths
- Control creativity levels
- Adjust round timing

Memory Files:
------------
- src/data/agent_memory/agent_one.json
- src/data/agent_memory/agent_two.json
- src/data/agent_discussed_tokens.csv

Author: Moon Dev 🌙
"""

# Model override settings
# Set to "0" to use config.py's AI_MODEL setting
# Available models:
# - "deepseek-chat" (DeepSeek's V3 model - fast & efficient)
# - "deepseek-reasoner" (DeepSeek's R1 reasoning model)
# - "0" (Use config.py's AI_MODEL setting)
MODEL_OVERRIDE = "deepseek-chat"  # Set to "0" to disable override
DEEPSEEK_BASE_URL = "https://api.deepseek.com"  # Base URL for DeepSeek API

# 🤖 Agent Prompts & Personalities
AGENT_ONE_PROMPT = """
You are Agent One - The Technical Analysis Expert 📊
Your role is to analyze charts, patterns, and market indicators to identify trading opportunities.

Focus on:
- Price action and chart patterns
- Technical indicators (RSI, MACD, etc.)
- Volume analysis
- Support/resistance levels
- Short to medium-term opportunities

Remember to be specific about entry/exit points and always consider Moon Dev's risk management rules! 🎯
"""

AGENT_TWO_PROMPT = """
You are Agent Two - The Fundamental Analysis Expert 🌍
Your role is to analyze macro trends, project fundamentals, and long-term potential.

Focus on:
- Project fundamentals and technology
- Team and development activity
- Market trends and sentiment
- Competitor analysis
- Long-term growth potential

Always consider the bigger picture and help guide Moon Dev's long-term strategy! 🚀
"""

TOKEN_EXTRACTOR_PROMPT = """
You are the Token Extraction Agent 🔍
Your role is to identify and extract all cryptocurrency symbols and tokens mentioned in conversations.

Rules:
- Extract both well-known (BTC, ETH) and newer tokens
- Include tokens mentioned by name or symbol
- Format as a clean list of symbols
- Be thorough but avoid duplicates
- When only a name is given, provide the symbol

Keep Moon Dev's token tracking clean and organized! 📝
"""

SYNOPSIS_AGENT_PROMPT = """
You are the Round Synopsis Agent 📊
Your role is to create clear, concise summaries of trading discussions.

Guidelines:
- Summarize key points in 1-2 sentences
- Focus on actionable decisions
- Highlight agreement between agents
- Note significant market observations
- Track progress toward the $10M goal

Help Moon Dev keep track of the trading journey! 🎯
"""

# 🤖 Agent Model Selection
AGENT_ONE_MODEL = MODEL_OVERRIDE if MODEL_OVERRIDE != "0" else "claude-3-haiku-20240307"
AGENT_TWO_MODEL = MODEL_OVERRIDE if MODEL_OVERRIDE != "0" else "claude-3-sonnet-20240229"
TOKEN_EXTRACTOR_MODEL = MODEL_OVERRIDE if MODEL_OVERRIDE != "0" else "claude-3-haiku-20240307"

# 🎮 Game Configuration
MINUTES_BETWEEN_ROUNDS = 30  # Time to wait between trading rounds (in minutes)

# 🔧 Agent Response Configuration
# Max Tokens (Controls response length):
AGENT_ONE_MAX_TOKENS = 1000    # Technical analysis needs decent space (500-1000 words)
AGENT_TWO_MAX_TOKENS = 1000    # Fundamental analysis might need more detail (600-1200 words)
EXTRACTOR_MAX_TOKENS = 100     # Keep it brief, just token lists (50-100 words)
SYNOPSIS_MAX_TOKENS = 100      # Brief round summaries (50-100 words)

# Temperature (Controls response creativity/randomness):
AGENT_ONE_TEMP = 0.7    # Balanced creativity for technical analysis (0.5-0.8)
AGENT_TWO_TEMP = 0.7    # Balanced creativity for fundamental analysis (0.5-0.8)
EXTRACTOR_TEMP = 0      # Zero creativity, just extract tokens (always 0)
SYNOPSIS_TEMP = 0.3     # Low creativity for consistent summaries (0.2-0.4)

# Token Log File
TOKEN_LOG_FILE = Path("src/data/agent_discussed_tokens.csv")

# Available Models:
# - claude-3-opus-20240229    (Most powerful, longest responses)
# - claude-3-sonnet-20240229  (Balanced performance)
# - claude-3-haiku-20240307   (Fastest, shorter responses)
# - claude-2.1                (Previous generation)
# - claude-2.0                (Previous generation)

"""
Response Length Guide (max_tokens):
50-100:   Ultra concise, bullet points
100-200:  Short paragraphs
500-800:  Detailed explanation
1000+:    In-depth analysis

Temperature Guide:
0.0:  Deterministic, same response every time
0.3:  Very focused, minimal variation
0.7:  Creative but stays on topic
1.0:  Maximum creativity/variation
"""

"""
SYSTEM GOAL:
Two AI agents (Haiku & Sonnet) collaborate to grow a $10,000 portfolio to $10,000,000 using CoinGecko's 
comprehensive crypto data (since 2014). They analyze market trends, identify opportunities, and make 
strategic decisions together while maintaining a conversation log in the data folder.

Agent One: Technical Analysis Expert 📊
Agent Two: Fundamental/Macro Analysis Expert 🌍
"""


import os
import requests
import pandas as pd
import json
from typing import Dict, List, Optional, Union
from datetime import datetime, timedelta
import time
from dotenv import load_dotenv
from termcolor import colored, cprint
from pathlib import Path

# Local imports
from src.config import *
from src.bedrock_llm import bedrock_chat_sync, ChatMessage, ChatOptions

# Load environment variables
load_dotenv()

def print_banner():
    """Print a fun colorful banner"""
    cprint("\n" + "="*70, "white", "on_blue")
    cprint("🌙 🎮 Moon Dev's Crypto Trading Game! 🎮 🌙", "white", "on_magenta", attrs=["bold"])
    cprint("="*70 + "\n", "white", "on_blue")

def print_section(title: str, color: str = "on_blue"):
    """Print a section header"""
    cprint(f"\n{'='*35}", "white", color)
    cprint(f" {title} ", "white", color, attrs=["bold"])
    cprint(f"{'='*35}\n", "white", color)

# Create data directory for agent memory in the correct project structure
AGENT_MEMORY_DIR = Path("src/data/agent_memory")
AGENT_MEMORY_DIR.mkdir(parents=True, exist_ok=True)

def cleanup_old_memory_files():
    """Clean up old memory files from previous naming conventions"""
    old_files = ['haiku_memory.json', 'sonnet_memory.json']
    for file in old_files:
        try:
            old_file = AGENT_MEMORY_DIR / file
            if old_file.exists():
                old_file.unlink()
                cprint(f"🧹 Cleaned up old memory file: {file}", "white", "on_blue")
        except Exception as e:
            cprint(f"⚠️ Error cleaning up {file}: {e}", "white", "on_yellow")

print(f"📁 Agent memory directory: {AGENT_MEMORY_DIR}")
cleanup_old_memory_files()  # Clean up old files on startup

class AIAgent:
    """Individual AI Agent for collaborative decision making"""
    
    def __init__(self, name: str, model: str = None):
        self.name = name
        self.model = model or AI_MODEL
        
        # Initialize appropriate client based on model
        if "deepseek" in self.model.lower():
            # Bedrock via bedrock_llm.py - no old API keys needed
            pass

        # Initialize Anthropic client for other models
        self.client = None

if __name__ == "__main__":
    main()