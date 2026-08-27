"""
Moon Dev's Model Factory

Multi-model trading architecture using AWS Bedrock:

  PRIMARY_ANALYST    -> deepseek.v3.2          (trading signals)
  SECONDARY_ANALYST  -> qwen.qwen3-next-80b-a3b (confirmation)
  INDEPENDENT        -> moonshotai.kimi-k2.5   (contrarian/analysis)
  DEEP_REASONING     -> deepseek.r1-v1:0       (deep investigation)
  CODING_ENGINEER    -> qwen.qwen3-coder-next  (strategy development)
  FAST_ANALYST       -> openai.gpt-oss-20b-1:0 (high-volume lightweight)
"""

import os
import sys
import io
import random
from typing import Dict, Optional
from termcolor import cprint
from dotenv import load_dotenv
from pathlib import Path
from .base_model import BaseModel
from .bedrock_model import BedrockModel, MODEL_ROLES

# Fix Windows encoding for emoji output
if sys.platform == 'win32' and hasattr(sys.stdout, 'buffer'):
    try:
        if sys.stdout.encoding != 'utf-8':
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
    except (ValueError, AttributeError):
        pass  # Already wrapped or not available


class ModelFactory:
    """Factory for creating and managing Bedrock AI models"""

    # Cache of initialized models by model_id
    _models: Dict[str, BedrockModel] = {}

    def __init__(self):
        cprint("\nMoon Dev's Model Factory Initialization", "cyan")
        cprint("=" * 50, "cyan")

        project_root = Path(__file__).parent.parent.parent
        env_path = project_root / '.env'
        load_dotenv(dotenv_path=env_path)

        self._models = {}
        self._initialize_models()

    def _initialize_models(self):
        """Initialize primary Bedrock model"""
        cprint("\nInitializing Bedrock (PRIMARY)...", "cyan")
        try:
            primary = BedrockModel(api_key="bedrock", model_name="deepseek.v3.2")
            if primary.is_available():
                self._models["deepseek.v3.2"] = primary
                cprint("  Bedrock initialized successfully", "green")
                cprint(f"  Primary model: deepseek.v3.2", "green")
            else:
                cprint("  Bedrock not configured - check AWS credentials in .env", "yellow")
        except Exception as e:
            cprint(f"  Bedrock init failed: {e}", "red")

        cprint("\n" + "=" * 50, "cyan")
        cprint(f"Models initialized: {len(self._models)}", "cyan")
        cprint(f"Available: {list(self._models.keys())}", "cyan")

    def get_model(self, model_type: str = None, model_name: str = None) -> Optional[BedrockModel]:
        """
        Get a Bedrock model instance.

        Usage:
          get_model()                                    -> deepseek.v3.2 (default)
          get_model("bedrock", "deepseek.v3.2")          -> deepseek.v3.2
          get_model("bedrock", "qwen.qwen3-coder-next")  -> qwen3-coder-next
          get_model(role="primary_analyst")               -> deepseek.v3.2
          get_model(role="coding_engineer")               -> qwen3-coder-next
        """
        # Resolve model_name from role if provided
        if model_name and model_name in MODEL_ROLES:
            model_name = MODEL_ROLES[model_name]

        # Default to primary analyst
        if not model_name:
            model_name = MODEL_ROLES.get("primary_analyst", "deepseek.v3.2")

        # Return cached model or create new one
        if model_name in self._models:
            return self._models[model_name]

        # Create new model instance
        cprint(f"  Creating Bedrock model: {model_name}", "cyan")
        try:
            model = BedrockModel(api_key="bedrock", model_name=model_name)
            if model.is_available():
                self._models[model_name] = model
                return model
        except Exception as e:
            cprint(f"  Failed to create model {model_name}: {e}", "red")

        return None

    def get_model_for_role(self, role: str) -> Optional[BedrockModel]:
        """Get model by trading role (primary_analyst, secondary_analyst, etc.)"""
        model_id = MODEL_ROLES.get(role)
        if not model_id:
            cprint(f"  Unknown role: {role}", "red")
            return None
        return self.get_model(model_name=model_id)

    def is_model_available(self, model_name: str) -> bool:
        """Check if a specific model is available"""
        return model_name in self._models and self._models[model_name].is_available()

    @property
    def available_models(self) -> Dict[str, list]:
        """Get all available models"""
        return {
            model_name: model.AVAILABLE_MODELS
            for model_name, model in self._models.items()
        }


# Create a singleton instance
model_factory = ModelFactory()
