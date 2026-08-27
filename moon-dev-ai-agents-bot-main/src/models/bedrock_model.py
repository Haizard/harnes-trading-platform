"""
Moon Dev's Bedrock Model Adapter

Wraps bedrock_llm.py to match the BaseModel interface used by ModelFactory.
Supports multiple Bedrock models for the multi-model trading architecture:

  Model Roles:
    PRIMARY_ANALYST    -> deepseek.v3.2          (trading signals)
    SECONDARY_ANALYST  -> qwen.qwen3-next-80b-a3b (confirmation)
    INDEPENDENT        -> moonshotai.kimi-k2.5   (contrarian/analysis)
    DEEP_REASONING     -> deepseek.r1-v1:0       (deep investigation)
    CODING_ENGINEER    -> qwen.qwen3-coder-next  (strategy development)
    FAST_ANALYST       -> openai.gpt-oss-20b-1:0 (high-volume lightweight)
"""

import asyncio
from termcolor import cprint
from .base_model import BaseModel, ModelResponse
from src.bedrock_llm import (
    bedrock_chat,
    bedrock_chat_sync,
    ChatMessage,
    ChatOptions,
    is_bedrock_configured,
    get_bedrock_config,
)


# ── Model Roles ──────────────────────────────────────────────
MODEL_ROLES = {
    "primary_analyst":   "deepseek.v3.2",
    "secondary_analyst": "qwen.qwen3-next-80b-a3b",
    "independent":       "moonshotai.kimi-k2.5",
    "deep_reasoning":    "deepseek.r1-v1:0",
    "coding_engineer":   "qwen.qwen3-coder-next",
    "fast_analyst":      "openai.gpt-oss-20b-1:0",
    "heavy_analyst":     "openai.gpt-oss-120b-1:0",
    "reasoning_agent":   "minimax.minimax-m2.5",
    "general_agent":     "zai.glm-5",
}


class BedrockModel(BaseModel):
    """Implementation for AWS Bedrock models"""

    AVAILABLE_MODELS = {
        # Tier 1 — Primary trading models
        "deepseek.v3.2":              "DeepSeek V3.2 — Primary trading analyst, huge quota",
        "qwen.qwen3-next-80b-a3b":   "Qwen3 Next 80B A3B — MoE, efficient second analyst",
        "moonshotai.kimi-k2.5":       "Kimi K2.5 — Independent reasoning/analysis",
        # Tier 2 — Supporting models
        "deepseek.r1-v1:0":           "DeepSeek R1 — Deep reasoning (occasional use)",
        "qwen.qwen3-coder-next":      "Qwen3 Coder Next — Coding/strategy development",
        "openai.gpt-oss-20b-1:0":     "GPT-OSS 20B — Fast high-volume analyst",
        "openai.gpt-oss-120b-1:0":    "GPT-OSS 120B — Heavy analyst",
        "minimax.minimax-m2.5":       "MiniMax M2.5 — Reasoning/agent",
        "zai.glm-5":                  "GLM 5 — General reasoning/agent",
        "nvidia.nemotron-super-3-120b": "Nemotron Super 120B — Secondary analyst",
        # Tier 3 — Lightweight
        "qwen.qwen3-32b-v1:0":       "Qwen3 32B — Fast/cheap analyst",
        "qwen.qwen3-coder-30b-a3b-v1:0": "Qwen3 Coder 30B — Lightweight coding",
    }

    def __init__(self, api_key: str = "bedrock", model_name: str = "deepseek.v3.2", **kwargs):
        self.model_name = model_name
        super().__init__(api_key, **kwargs)

    def initialize_client(self, **kwargs) -> None:
        """Initialize by checking Bedrock configuration"""
        try:
            config = get_bedrock_config()
            if config["configured"]:
                self.client = "bedrock"
                cprint(f"  Initialized Bedrock model: {self.model_name}", "green")
                cprint(f"  Region: {config['region']}", "cyan")
            else:
                cprint("  AWS Bedrock not configured - check AWS_ACCESS_KEY_ID in .env", "red")
                self.client = None
        except Exception as e:
            cprint(f"  Failed to initialize Bedrock model: {str(e)}", "red")
            self.client = None

    def generate_response(
        self,
        system_prompt: str,
        user_content: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs
    ) -> ModelResponse:
        """Generate a response using Bedrock (sync wrapper)"""
        try:
            options = ChatOptions(
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                keep_json=False,
            )

            response = bedrock_chat_sync(
                [ChatMessage(role="user", content=user_content)],
                options,
            )

            return ModelResponse(
                content=response.text.strip(),
                raw_response=response,
                model_name=self.model_name,
                usage=None,
            )

        except Exception as e:
            cprint(f"  Bedrock generation error: {str(e)}", "red")
            raise e

    def is_available(self) -> bool:
        """Check if Bedrock is available"""
        return self.client is not None

    @property
    def model_type(self) -> str:
        return "bedrock"


def get_model_by_role(role: str) -> str:
    """Get the model ID for a given role"""
    return MODEL_ROLES.get(role, MODEL_ROLES["primary_analyst"])


def list_roles():
    """Print all model roles"""
    cprint("\nModel Roles:", "cyan")
    for role, model_id in MODEL_ROLES.items():
        cprint(f"  {role:25s} -> {model_id}", "green")
