"""
🌙 Moon Dev's New & Top Coins Agent 🔍

This agent goes through and analyzes all of the new tokens that have been listed in the coin gecko and then also analyzes the top movers of the last 24 hours on coin gecko and then makes recommendations based off that data. 

=================================
📚 QUICK START GUIDE
=================================
1. Set up environment variables in .env:
   - COINGECKO_API_KEY
   - ANTHROPIC_KEY (for Claude)
   - DEEPSEEK_KEY (for DeepSeek)

2. Choose AI model by setting MODEL_OVERRIDE at top of file:
   ```python
   # Use config.py's AI_MODEL (Default)
   MODEL_OVERRIDE = "0"  
   
   # For DeepSeek Chat (Faster, more concise)
   MODEL_OVERRIDE = "deepseek-chat"  
   
   # For DeepSeek Reasoner (Better reasoning, more detailed)
   MODEL_OVERRIDE = "deepseek-reasoner"
   ```

   🔍 Model Comparison:
   - Claude (from config.py): Balanced analysis, good for general use
   - DeepSeek Chat: Faster responses, more concise analysis
   - DeepSeek Reasoner: Better for complex market analysis, 
     provides more detailed reasoning

   To switch models:
   1. Get your DeepSeek API key from: https://platform.deepseek.com
   2. Add it to .env as DEEPSEEK_KEY="your_key_here"
   3. Set MODEL_OVERRIDE to your preferred model
   4. Restart the agent

3. Run the agent:
   python src/agents/new_or_top_agent.py

4. Check results in src/data/coingecko_results:
   - top_gainers_losers.csv (Raw data of top performers)
   - new_coins.csv (Latest 200 added coins)
   - ai_picks.csv (AI analysis and recommendations)
   - ai_buys.csv (Only BUY recommendations)

The agent runs every hour and:
- Fetches top 30 gainers and losers
- Gets latest 200 new coins
- Analyzes each coin with AI
- Saves BUY/SELL/DO NOTHING recommendations

=================================
🤖 AI ANALYSIS PROMPT
=================================
You can modify this prompt to customize the AI analysis:
"""

AI_PROMPT = """
Please analyze this cryptocurrency and provide a clear BUY, SELL, or DO NOTHING recommendation.

Coin Information:
• Name: {name}
• Symbol: {symbol}
• Source: {source_type}

Market Data (USD):
• Current Price: ${price:,.8f}
• 24h Open: ${open:,.8f}
• 24h High: ${high:,.8f}
• 24h Low: ${low:,.8f}
• 24h Volume: ${volume:,.2f}
• Market Cap Rank: #{market_cap_rank}
• 24h Change: {change:,.2f}%
• 7d Change: {change_7d:,.2f}%
• 30d Change: {change_30d:,.2f}%

Community Data:
{community_data}

IMPORTANT: Start your response with one of these recommendations:
RECOMMENDATION: BUY
RECOMMENDATION: SELL
RECOMMENDATION: DO NOTHING

Then provide your detailed analysis.
"""

"""
Main Agent Code Below
=================================
"""

import os
import requests
import pandas as pd
import json
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from typing import Dict, List
import time
from termcolor import colored, cprint
import random
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

# Configuration
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")
BASE_URL = "https://pro-api.coingecko.com/api/v3"
RESULTS_DIR = Path("src/data/coingecko_results")
DELAY_BETWEEN_REQUESTS = 1  # Seconds between API calls

# Output files
TOP_GAINERS_LOSERS_FILE = RESULTS_DIR / "top_gainers_losers.csv"
NEW_COINS_FILE = RESULTS_DIR / "new_coins.csv"
AI_PICKS_FILE = RESULTS_DIR / "ai_picks.csv"
AI_BUYS_FILE = RESULTS_DIR / "ai_buys.csv"  # New file for buy signals

# Create results directory
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Fun emoji sets for different actions
SPINNER_EMOJIS = ['🌍', '🌎', '🌏']  # Earth spinning
MOON_PHASES = ['🌑', '🌒', '🌓', '🌔', '🌕', '🌖', '🌗', '🌘']  # Moon phases
ROCKET_SEQUENCE = ['🚀', '💫', '✨', '💫', '🌟']  # Rocket launch
ERROR_EMOJIS = ['💥', '🚨', '⚠️', '❌', '🔥']  # Error indicators
SUCCESS_EMOJIS = ['✨', '🎯', '🎨', '🎪', '🎭', '🎪']  # Success indicators

def print_spinner(message: str, emoji_set: List[str], color: str = 'white', bg_color: str = 'on_blue'):
    """Print a spinning emoji animation with message"""
    for emoji in emoji_set:
        print(f"\r{emoji} {colored(message, color, bg_color)}", end='', flush=True)
        time.sleep(0.2)
    print()  # New line after animation

def print_fancy(message: str, color: str = 'white', bg_color: str = 'on_blue', emojis: List[str] = None):
    """Print a message with random emojis from set"""
    if emojis:
        emoji = random.choice(emojis)
        cprint(f"{emoji} {message} {emoji}", color, bg_color)
    else:
        cprint(message, color, bg_color)

class NewOrTopAgent:
    """Agent for analyzing new and top performing coins"""
    
    def __init__(self):
        self.headers = {
            "x-cg-pro-api-key": COINGECKO_API_KEY,
            "Content-Type": "application/json"
        }
        
        # Initialize AI client based on model
        if "deepseek" in AI_MODEL.lower():
            # Bedrock via bedrock_llm.py - no old API keys needed
            pass
        # Print final summary
        if total_analyzed > 0:
            print_fancy("\n🎮 ANALYSIS COMPLETE 🎮", 'white', 'on_green')
            print_fancy("=" * 50, 'blue', 'on_white')
            
            # Read the full file to get summary
            results_df = pd.read_csv(AI_PICKS_FILE)
            summary = results_df['recommendation'].value_counts()
            
            print_fancy(f"BUY: {summary.get('BUY', 0)} 💰", 'green', 'on_grey')
            print_fancy(f"SELL: {summary.get('SELL', 0)} 📉", 'red', 'on_grey')
            print_fancy(f"DO NOTHING: {summary.get('DO NOTHING', 0)} 🎯", 'yellow', 'on_grey')
            print_fancy("=" * 50, 'blue', 'on_white')

def main():
    """Main function to run the agent"""
    print_fancy("\n🌙 Moon Dev's Cosmic Token Analysis Starting! 🌟", 'white', 'on_magenta', SUCCESS_EMOJIS)
    agent = NewOrTopAgent()
    
    try:
        while True:
            agent.run_analysis()
            for emoji in MOON_PHASES:
                print_fancy(f"{emoji} Waiting for next analysis cycle...", 'cyan', 'on_blue')
                time.sleep(450)  # 450 * 8 = 3600 (1 hour)
            
    except KeyboardInterrupt:
        print_fancy("\n👋 Agent stopped by user - Moon Dev out! 🌙", 'white', 'on_magenta')
    except Exception as e:
        print_fancy(f"\nError: {str(e)}", 'white', 'on_red', ERROR_EMOJIS)

if __name__ == "__main__":
    main()
