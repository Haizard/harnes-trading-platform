"""
🤖 Moon Dev's AWS Bedrock LLM Engine
Port of NetMaster's TypeScript Bedrock module to Python.

Works with Qwen3-Coder-Next, Claude, Llama, DeepSeek, or any Bedrock model.

Usage:
    from src.bedrock_llm import bedrock_chat, ChatMessage

    response = await bedrock_chat([
        ChatMessage(role="user", content="Analyze BTC market data...")
    ], system_prompt="You are a crypto trading expert.")

    print(response.text)       # Cleaned text
    print(response.json_data)  # Parsed JSON block (if any)
"""

import os
import re
import json
import asyncio
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Union

# Lazy-loaded AWS Bedrock client
_client = None
_cached_region = None


# ── Configuration ────────────────────────────────────────────

def get_bedrock_region() -> str:
    """Get Bedrock region from environment."""
    return os.environ.get("AWS_BEDROCK_REGION", "us-east-1")


def get_bedrock_model_id() -> str:
    """Get Bedrock model ID from environment."""
    return os.environ.get("AWS_BEDROCK_MODEL_ID", "qwen.qwen3-coder-next")


def is_bedrock_configured() -> bool:
    """Check if AWS Bedrock credentials are configured."""
    return bool(
        os.environ.get("AWS_ACCESS_KEY_ID")
        or os.environ.get("AWS_PROFILE")
        or os.environ.get("AWS_BEDROCK_REGION")
    )


def get_bedrock_config() -> dict:
    """Get current Bedrock config (non-sensitive)."""
    return {
        "region": get_bedrock_region(),
        "model_id": get_bedrock_model_id(),
        "configured": is_bedrock_configured(),
    }


# ── Types ────────────────────────────────────────────────────

@dataclass
class ChatMessage:
    """A chat message with role and content."""
    role: str  # "user", "assistant", or "system"
    content: str


@dataclass
class ChatResponse:
    """Response from Bedrock LLM."""
    text: str           # Cleaned text (JSON blocks stripped by default)
    raw_text: str       # Raw response before any stripping
    json_data: Optional[Dict[str, Any]] = None  # Parsed JSON block (if any)


@dataclass
class ChatOptions:
    """Options for chat completion."""
    max_tokens: int = 4096
    temperature: float = 0.3
    top_p: float = 0.85
    system_prompt: str = "You are a helpful assistant."
    keep_json: bool = False  # If True, keep JSON blocks in returned text


# ── Client Initialization ────────────────────────────────────

def _get_client():
    """Lazy-load the Bedrock Runtime client."""
    global _client, _cached_region

    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise ImportError(
            "boto3 is required for AWS Bedrock. Install with: pip install boto3"
        )

    region = get_bedrock_region()

    # Recreate client if region changed
    if _client is None or _cached_region != region:
        _client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(
                retries={"max_attempts": 3, "mode": "adaptive"},
                connect_timeout=10,
                read_timeout=30,
            ),
        )
        _cached_region = region

    return _client


# ── Response Parsing ─────────────────────────────────────────

def _extract_text(response_body: dict) -> str:
    """
    Extract text from various model response formats.

    Handles: Qwen3, DeepSeek, Claude, Llama, Mistral, legacy formats.
    """
    # Qwen3 / OpenAI-style chat response
    if isinstance(response_body.get("choices"), list) and response_body["choices"]:
        choice = response_body["choices"][0]
        msg = choice.get("message", {})
        if "content" in msg and msg["content"]:
            return msg["content"]
        if "text" in choice:
            return choice["text"]

    # Claude / Anthropic response
    if isinstance(response_body.get("content"), list) and response_body["content"]:
        block = response_body["content"][0]
        if "text" in block:
            return block["text"]

    # Legacy generation format
    if "generation" in response_body:
        return response_body["generation"]

    # Older completions format
    if isinstance(response_body.get("completions"), list) and response_body["completions"]:
        data = response_body["completions"][0].get("data", {})
        if "text" in data:
            return data["text"]

    # Bedrock output format
    output = response_body.get("output")
    if isinstance(output, str):
        return output
    if isinstance(output, dict) and "text" in output:
        return output["text"]

    # Bedrock message format
    if isinstance(response_body.get("messages"), list) and response_body["messages"]:
        msg = response_body["messages"][0]
        if isinstance(msg.get("content"), list):
            return msg["content"][0].get("text", "")
        if "content" in msg:
            return msg["content"]

    # Fallback: stringify whatever we got
    return json.dumps(response_body)


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Try to extract a JSON block from the response text."""
    # Try ```json ... ``` blocks first
    json_match = re.search(r"```json\s*(.*?)```", text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try raw JSON
    raw_match = re.search(r"\{[\s\S]*\}", text)
    if raw_match:
        try:
            return json.loads(raw_match.group(0))
        except json.JSONDecodeError:
            pass

    return None


# ── Main Chat Function ───────────────────────────────────────

async def bedrock_chat(
    messages: List[ChatMessage],
    options: Optional[ChatOptions] = None,
) -> ChatResponse:
    """
    Send a chat request to AWS Bedrock.

    Args:
        messages: List of chat messages
        options: Chat options (temperature, max_tokens, etc.)

    Returns:
        ChatResponse with cleaned text, raw text, and optional JSON data
    """
    if options is None:
        options = ChatOptions()

    client = _get_client()
    model_id = get_bedrock_model_id()

    # Build messages array — Qwen3 supports 'system' role natively
    chat_messages = []
    if options.system_prompt:
        chat_messages.append({
            "role": "system",
            "content": options.system_prompt,
        })

    for msg in messages:
        chat_messages.append({
            "role": msg.role,
            "content": msg.content,
        })

    # Build request body
    input_body = json.dumps({
        "messages": chat_messages,
        "max_tokens": options.max_tokens,
        "temperature": options.temperature,
        "top_p": options.top_p,
    })

    # Invoke the model
    response = client.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=input_body,
    )

    # Parse response
    response_body = json.loads(response["body"].read().decode("utf-8"))

    # Extract text from various model formats
    raw_text = _extract_text(response_body)

    # Try to extract JSON data
    json_data = _extract_json(raw_text)

    # Clean text (strip JSON blocks unless keep_json)
    visible_text = raw_text
    if not options.keep_json:
        visible_text = re.sub(r"```json[\s\S]*?```", "", raw_text).strip()
        visible_text = re.sub(r"\n{3,}", "\n\n", visible_text).strip()

    return ChatResponse(
        text=visible_text,
        raw_text=raw_text,
        json_data=json_data,
    )


# ── Synchronous Wrapper ──────────────────────────────────────

def bedrock_chat_sync(
    messages: List[ChatMessage],
    options: Optional[ChatOptions] = None,
) -> ChatResponse:
    """
    Synchronous wrapper for bedrock_chat().

    Use this when you can't use async/await.
    """
    loop = None
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're in an async context, create a new thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    bedrock_chat(messages, options)
                )
                return future.result(timeout=60)
    except RuntimeError:
        pass

    return asyncio.run(bedrock_chat(messages, options))


# ── Convenience Functions ────────────────────────────────────

async def ask_ai(
    prompt: str,
    system_prompt: str = "You are a helpful assistant.",
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> str:
    """
    Simple AI query — returns just the text response.

    Args:
        prompt: User prompt
        system_prompt: System prompt
        temperature: Creativity (0-1)
        max_tokens: Max response tokens

    Returns:
        Cleaned text response
    """
    if not is_bedrock_configured():
        return f"[AI unavailable — not configured] {prompt[:200]}"

    try:
        response = await bedrock_chat(
            [ChatMessage(role="user", content=prompt)],
            ChatOptions(
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            ),
        )
        return response.text
    except Exception as e:
        return f"[AI error: {str(e)}] {prompt[:200]}"


async def ask_ai_json(
    prompt: str,
    system_prompt: str = "You are a helpful assistant. Respond in JSON.",
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> Optional[Dict[str, Any]]:
    """
    AI query that returns parsed JSON data.

    Args:
        prompt: User prompt
        system_prompt: System prompt
        temperature: Creativity (0-1, lower for structured output)
        max_tokens: Max response tokens

    Returns:
        Parsed JSON dict, or None if parsing failed
    """
    if not is_bedrock_configured():
        return None

    try:
        response = await bedrock_chat(
            [ChatMessage(role="user", content=prompt)],
            ChatOptions(
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                keep_json=True,
            ),
        )
        return response.json_data
    except Exception:
        return None


# ── Model Fallback ──────────────────────────────────────────

# Cheaper/faster models for simple tasks
FALLBACK_MODELS = {
    "classification": "anthropic.claude-3-haiku-20241022-v1:0",  # Fast, cheap
    "analysis": "qwen.qwen3-coder-next",  # Default
    "creative": "anthropic.claude-sonnet-4-20250514-v1:0",  # Better quality
}


async def bedrock_chat_with_fallback(
    messages: List[ChatMessage],
    options: Optional[ChatOptions] = None,
    task_type: str = "analysis",
) -> ChatResponse:
    """
    Chat with automatic model fallback.

    If primary model fails (rate limit, unavailable), tries a cheaper fallback.

    Args:
        messages: Chat messages
        options: Chat options
        task_type: 'classification' (cheap/fast), 'analysis' (default), 'creative' (best quality)
    """
    primary_model = get_bedrock_model_id()
    fallback_model = FALLBACK_MODELS.get(task_type, FALLBACK_MODELS["analysis"])

    try:
        return await bedrock_chat(messages, options)
    except Exception as primary_error:
        error_str = str(primary_error).lower()

        # Only fallback on rate limits or model unavailable
        if "throttl" in error_str or "notready" in error_str or "unavailable" in error_str:
            if fallback_model != primary_model:
                # Temporarily switch model
                old_model = os.environ.get("AWS_BEDROCK_MODEL_ID")
                os.environ["AWS_BEDROCK_MODEL_ID"] = fallback_model
                try:
                    result = await bedrock_chat(messages, options)
                    return result
                finally:
                    # Restore original model
                    if old_model:
                        os.environ["AWS_BEDROCK_MODEL_ID"] = old_model
                    else:
                        del os.environ["AWS_BEDROCK_MODEL_ID"]

        # Re-raise if fallback didn't help
        raise primary_error


# ── Retry with Backoff ──────────────────────────────────────

async def bedrock_chat_with_retry(
    messages: List[ChatMessage],
    options: Optional[ChatOptions] = None,
    max_retries: int = 3,
) -> ChatResponse:
    """
    Chat with automatic retry and exponential backoff.

    Handles rate limits (ThrottlingException) gracefully.
    """
    import time

    last_error = None
    for attempt in range(max_retries):
        try:
            return await bedrock_chat(messages, options)
        except Exception as e:
            last_error = e
            error_str = str(e).lower()

            # Only retry on throttling/rate limit errors
            if "throttl" in error_str or "rate" in error_str:
                if attempt < max_retries - 1:
                    delay = (2 ** attempt) * 1.0  # 1s, 2s, 4s
                    await asyncio.sleep(delay)
                    continue

            # Other errors — don't retry
            raise

    raise last_error


# ── Health Check ─────────────────────────────────────────────

async def bedrock_health_check() -> dict:
    """
    Test Bedrock connectivity.

    Returns:
        Dict with status, config, and test response
    """
    config = get_bedrock_config()

    if not config["configured"]:
        return {
            "ok": False,
            "error": "Bedrock not configured",
            "config": config,
        }

    try:
        response = await bedrock_chat(
            [ChatMessage(role="user", content="Reply with exactly: OK")],
            ChatOptions(max_tokens=10, temperature=0.0),
        )
        return {
            "ok": True,
            "config": config,
            "response": response.text[:200],
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "config": config,
        }


# ── Export All ───────────────────────────────────────────────

__all__ = [
    "ChatMessage",
    "ChatResponse",
    "ChatOptions",
    "bedrock_chat",
    "bedrock_chat_sync",
    "bedrock_chat_with_retry",
    "bedrock_chat_with_fallback",
    "ask_ai",
    "ask_ai_json",
    "is_bedrock_configured",
    "get_bedrock_config",
    "bedrock_health_check",
    "FALLBACK_MODELS",
]
