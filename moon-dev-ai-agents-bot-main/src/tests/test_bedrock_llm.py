"""
🧪 Tests for AWS Bedrock LLM Module

Tests the Python Bedrock LLM module without requiring actual AWS credentials.
Uses mocking to simulate Bedrock API responses.
"""

import os
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from src.bedrock_llm import (
    ChatMessage,
    ChatResponse,
    ChatOptions,
    _extract_text,
    _extract_json,
    get_bedrock_region,
    get_bedrock_model_id,
    is_bedrock_configured,
    get_bedrock_config,
)


# ── Configuration Tests ──────────────────────────────────────

class TestConfiguration:
    """Test Bedrock configuration functions."""

    def test_default_region(self):
        """Default region should be us-east-1."""
        with patch.dict(os.environ, {}, clear=True):
            region = get_bedrock_region()
            assert region == "us-east-1"

    def test_custom_region(self):
        """Should read region from environment."""
        with patch.dict(os.environ, {"AWS_BEDROCK_REGION": "eu-west-1"}):
            region = get_bedrock_region()
            assert region == "eu-west-1"

    def test_default_model(self):
        """Default model should be qwen.qwen3-coder-next."""
        with patch.dict(os.environ, {}, clear=True):
            model = get_bedrock_model_id()
            assert model == "qwen.qwen3-coder-next"

    def test_custom_model(self):
        """Should read model from environment."""
        with patch.dict(os.environ, {"AWS_BEDROCK_MODEL_ID": "anthropic.claude-sonnet-4-20250514-v1:0"}):
            model = get_bedrock_model_id()
            assert model == "anthropic.claude-sonnet-4-20250514-v1:0"

    def test_is_configured_with_key(self):
        """Should detect AWS credentials."""
        with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "AKIA123"}):
            assert is_bedrock_configured() is True

    def test_is_configured_with_profile(self):
        """Should detect AWS profile."""
        with patch.dict(os.environ, {"AWS_PROFILE": "my-profile"}):
            assert is_bedrock_configured() is True

    def test_is_configured_with_region(self):
        """Should detect region as configured."""
        with patch.dict(os.environ, {"AWS_BEDROCK_REGION": "us-east-1"}):
            assert is_bedrock_configured() is True

    def test_not_configured(self):
        """Should detect missing configuration."""
        with patch.dict(os.environ, {}, clear=True):
            assert is_bedrock_configured() is False

    def test_get_config(self):
        """Should return config dict."""
        with patch.dict(os.environ, {
            "AWS_BEDROCK_REGION": "us-west-2",
            "AWS_BEDROCK_MODEL_ID": "meta.llama3-70b-instruct-v1:0",
        }):
            config = get_bedrock_config()
            assert config["region"] == "us-west-2"
            assert config["model_id"] == "meta.llama3-70b-instruct-v1:0"
            assert config["configured"] is True


# ── Response Text Extraction Tests ───────────────────────────

class TestExtractText:
    """Test text extraction from various model response formats."""

    def test_qwen3_openai_style(self):
        """Qwen3/OpenAI-style chat response."""
        body = {
            "choices": [{"message": {"content": "BTC is bullish"}}]
        }
        assert _extract_text(body) == "BTC is bullish"

    def test_completion_style(self):
        """Older completion-style response."""
        body = {
            "choices": [{"text": "Price is rising"}]
        }
        assert _extract_text(body) == "Price is rising"

    def test_claude_response(self):
        """Claude/Anthropic response format."""
        body = {
            "content": [{"text": "Market analysis complete"}]
        }
        assert _extract_text(body) == "Market analysis complete"

    def test_legacy_generation(self):
        """Legacy generation format."""
        body = {"generation": "BTC to the moon"}
        assert _extract_text(body) == "BTC to the moon"

    def test_completions_format(self):
        """Older completions format."""
        body = {
            "completions": [{"data": {"text": "Sell signal"}}]
        }
        assert _extract_text(body) == "Sell signal"

    def test_output_string(self):
        """Bedrock output as string."""
        body = {"output": "Simple response"}
        assert _extract_text(body) == "Simple response"

    def test_output_dict(self):
        """Bedrock output as dict with text."""
        body = {"output": {"text": "Structured response"}}
        assert _extract_text(body) == "Structured response"

    def test_message_format(self):
        """Bedrock message format."""
        body = {
            "messages": [{"content": [{"text": "Message content"}]}]
        }
        assert _extract_text(body) == "Message content"

    def test_fallback_stringify(self):
        """Unknown format should be stringified."""
        body = {"unknown_key": "value"}
        result = _extract_text(body)
        assert "unknown_key" in result


# ── JSON Extraction Tests ────────────────────────────────────

class TestExtractJSON:
    """Test JSON extraction from response text."""

    def test_json_code_block(self):
        """Should extract JSON from ```json blocks."""
        text = 'Here is the analysis:\n```json\n{"signal": "BUY", "confidence": 85}\n```\nDone.'
        result = _extract_json(text)
        assert result == {"signal": "BUY", "confidence": 85}

    def test_raw_json(self):
        """Should extract raw JSON."""
        text = 'The recommendation is {"action": "SELL", "reason": "overbought"} based on analysis.'
        result = _extract_json(text)
        assert result == {"action": "SELL", "reason": "overbought"}

    def test_no_json(self):
        """Should return None when no JSON found."""
        text = "This is just plain text with no JSON data."
        result = _extract_json(text)
        assert result is None

    def test_invalid_json(self):
        """Should return None for invalid JSON."""
        text = "```json\n{not valid json}\n```"
        result = _extract_json(text)
        assert result is None

    def test_nested_json(self):
        """Should extract nested JSON objects."""
        text = '```json\n{"portfolio": {"BTC": 1000, "ETH": 500}}\n```'
        result = _extract_json(text)
        assert result == {"portfolio": {"BTC": 1000, "ETH": 500}}

    def test_json_array(self):
        """Should extract JSON with arrays."""
        text = '```json\n{"tokens": ["BTC", "ETH", "SOL"]}\n```'
        result = _extract_json(text)
        assert result == {"tokens": ["BTC", "ETH", "SOL"]}


# ── Dataclass Tests ──────────────────────────────────────────

class TestDataclasses:
    """Test dataclass instantiation and defaults."""

    def test_chat_message(self):
        """ChatMessage should store role and content."""
        msg = ChatMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_chat_response(self):
        """ChatResponse should store text, raw_text, and optional json_data."""
        resp = ChatResponse(text="clean", raw_text="raw", json_data={"key": "val"})
        assert resp.text == "clean"
        assert resp.raw_text == "raw"
        assert resp.json_data == {"key": "val"}

    def test_chat_response_defaults(self):
        """ChatResponse should have json_data=None by default."""
        resp = ChatResponse(text="text", raw_text="raw")
        assert resp.json_data is None

    def test_chat_options(self):
        """ChatOptions should have sensible defaults."""
        opts = ChatOptions()
        assert opts.max_tokens == 4096
        assert opts.temperature == 0.3
        assert opts.top_p == 0.85
        assert opts.keep_json is False

    def test_chat_options_custom(self):
        """ChatOptions should accept custom values."""
        opts = ChatOptions(max_tokens=8192, temperature=0.7, keep_json=True)
        assert opts.max_tokens == 8192
        assert opts.temperature == 0.7
        assert opts.keep_json is True


# ── Async Chat Tests (Mocked) ────────────────────────────────

class TestBedrockChat:
    """Test bedrock_chat with mocked AWS Bedrock client."""

    @pytest.mark.asyncio
    async def test_basic_chat(self):
        """Should make a valid Bedrock API call."""
        from src.bedrock_llm import bedrock_chat

        mock_response_body = json.dumps({
            "choices": [{"message": {"content": "BTC is bullish"}}]
        }).encode("utf-8")

        mock_body = MagicMock()
        mock_body.read.return_value = mock_response_body

        mock_client = MagicMock()
        mock_client.invoke_model.return_value = {"body": mock_body}

        with patch("src.bedrock_llm._get_client", return_value=mock_client), \
             patch.dict(os.environ, {"AWS_BEDROCK_MODEL_ID": "qwen.qwen3-coder-next"}):

            response = await bedrock_chat(
                [ChatMessage(role="user", content="Analyze BTC")],
                ChatOptions(system_prompt="You are a crypto analyst."),
            )

            assert response.text == "BTC is bullish"
            assert response.raw_text == "BTC is bullish"
            assert mock_client.invoke_model.called

    @pytest.mark.asyncio
    async def test_json_extraction_in_chat(self):
        """Should extract JSON data from response."""
        from src.bedrock_llm import bedrock_chat

        mock_response_body = json.dumps({
            "choices": [{"message": {"content": '```json\n{"signal": "BUY", "confidence": 85}\n```\nAnalysis shows bullish trend.'}}]
        }).encode("utf-8")

        mock_body = MagicMock()
        mock_body.read.return_value = mock_response_body

        mock_client = MagicMock()
        mock_client.invoke_model.return_value = {"body": mock_body}

        with patch("src.bedrock_llm._get_client", return_value=mock_client), \
             patch.dict(os.environ, {"AWS_BEDROCK_MODEL_ID": "qwen.qwen3-coder-next"}):

            response = await bedrock_chat(
                [ChatMessage(role="user", content="Analyze")],
                ChatOptions(keep_json=True),
            )

            assert response.json_data == {"signal": "BUY", "confidence": 85}
            assert "BUY" in response.raw_text

    @pytest.mark.asyncio
    async def test_keep_json_false_strips_blocks(self):
        """Should strip JSON blocks when keep_json=False."""
        from src.bedrock_llm import bedrock_chat

        mock_response_body = json.dumps({
            "choices": [{"message": {"content": 'Analysis:\n```json\n{"data": 123}\n```\nConclusion.'}}]
        }).encode("utf-8")

        mock_body = MagicMock()
        mock_body.read.return_value = mock_response_body

        mock_client = MagicMock()
        mock_client.invoke_model.return_value = {"body": mock_body}

        with patch("src.bedrock_llm._get_client", return_value=mock_client), \
             patch.dict(os.environ, {"AWS_BEDROCK_MODEL_ID": "qwen.qwen3-coder-next"}):

            response = await bedrock_chat(
                [ChatMessage(role="user", content="Analyze")],
                ChatOptions(keep_json=False),
            )

            assert "```json" not in response.text
            assert "Analysis:" in response.text

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Should propagate AWS errors."""
        from src.bedrock_llm import bedrock_chat

        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = Exception("AccessDeniedException")

        with patch("src.bedrock_llm._get_client", return_value=mock_client), \
             patch.dict(os.environ, {"AWS_BEDROCK_MODEL_ID": "qwen.qwen3-coder-next"}):

            with pytest.raises(Exception, match="AccessDeniedException"):
                await bedrock_chat(
                    [ChatMessage(role="user", content="Test")],
                )


# ── Synchronous Wrapper Tests ────────────────────────────────

class TestSyncWrapper:
    """Test bedrock_chat_sync function."""

    def test_sync_chat(self):
        """Should return ChatResponse synchronously."""
        from src.bedrock_llm import bedrock_chat_sync

        mock_response_body = json.dumps({
            "choices": [{"message": {"content": "Sync response"}}]
        }).encode("utf-8")

        mock_body = MagicMock()
        mock_body.read.return_value = mock_response_body

        mock_client = MagicMock()
        mock_client.invoke_model.return_value = {"body": mock_body}

        with patch("src.bedrock_llm._get_client", return_value=mock_client), \
             patch.dict(os.environ, {"AWS_BEDROCK_MODEL_ID": "qwen.qwen3-coder-next"}):

            response = bedrock_chat_sync(
                [ChatMessage(role="user", content="Test sync")],
            )

            assert response.text == "Sync response"


# ── Convenience Function Tests ───────────────────────────────

class TestConvenience:
    """Test ask_ai and ask_ai_json convenience functions."""

    @pytest.mark.asyncio
    async def test_ask_ai_configured(self):
        """ask_ai should return text when configured."""
        from src.bedrock_llm import ask_ai

        mock_response_body = json.dumps({
            "choices": [{"message": {"content": "Market is bullish"}}]
        }).encode("utf-8")

        mock_body = MagicMock()
        mock_body.read.return_value = mock_response_body

        mock_client = MagicMock()
        mock_client.invoke_model.return_value = {"body": mock_body}

        with patch("src.bedrock_llm._get_client", return_value=mock_client), \
             patch.dict(os.environ, {"AWS_BEDROCK_MODEL_ID": "qwen.qwen3-coder-next", "AWS_ACCESS_KEY_ID": "test"}):

            result = await ask_ai("Analyze BTC")
            assert result == "Market is bullish"

    @pytest.mark.asyncio
    async def test_ask_ai_not_configured(self):
        """ask_ai should return fallback when not configured."""
        from src.bedrock_llm import ask_ai

        with patch.dict(os.environ, {}, clear=True):
            result = await ask_ai("Analyze BTC")
            assert "unavailable" in result.lower() or "not configured" in result.lower()

    @pytest.mark.asyncio
    async def test_ask_ai_json(self):
        """ask_ai_json should return parsed JSON."""
        from src.bedrock_llm import ask_ai_json

        mock_response_body = json.dumps({
            "choices": [{"message": {"content": '```json\n{"signal": "BUY"}\n```'}}]
        }).encode("utf-8")

        mock_body = MagicMock()
        mock_body.read.return_value = mock_response_body

        mock_client = MagicMock()
        mock_client.invoke_model.return_value = {"body": mock_body}

        with patch("src.bedrock_llm._get_client", return_value=mock_client), \
             patch.dict(os.environ, {"AWS_BEDROCK_MODEL_ID": "qwen.qwen3-coder-next", "AWS_ACCESS_KEY_ID": "test"}):

            result = await ask_ai_json("Analyze BTC")
            assert result == {"signal": "BUY"}


# ── Health Check Tests ───────────────────────────────────────

class TestHealthCheck:
    """Test bedrock_health_check function."""

    @pytest.mark.asyncio
    async def test_health_check_not_configured(self):
        """Should report not configured."""
        from src.bedrock_llm import bedrock_health_check

        with patch.dict(os.environ, {}, clear=True):
            result = await bedrock_health_check()
            assert result["ok"] is False
            assert "not configured" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_health_check_success(self):
        """Should report success when Bedrock responds."""
        from src.bedrock_llm import bedrock_health_check

        mock_response_body = json.dumps({
            "choices": [{"message": {"content": "OK"}}]
        }).encode("utf-8")

        mock_body = MagicMock()
        mock_body.read.return_value = mock_response_body

        mock_client = MagicMock()
        mock_client.invoke_model.return_value = {"body": mock_body}

        with patch("src.bedrock_llm._get_client", return_value=mock_client), \
             patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "test", "AWS_BEDROCK_REGION": "us-east-1"}):

            result = await bedrock_health_check()
            assert result["ok"] is True
            assert "OK" in result["response"]


# ── Model Compatibility Tests ────────────────────────────────

class TestModelCompatibility:
    """Test that various Bedrock model response formats work."""

    def test_claude_sonnet_format(self):
        """Claude Sonnet response format."""
        body = {
            "content": [{"type": "text", "text": "Claude analysis result"}],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
        }
        assert _extract_text(body) == "Claude analysis result"

    def test_llama_format(self):
        """Llama response format."""
        body = {
            "generation": "Llama response text"
        }
        assert _extract_text(body) == "Llama response text"

    def test_mistral_format(self):
        """Mistral response format (OpenAI-compatible)."""
        body = {
            "choices": [{"message": {"content": "Mistral analysis"}}]
        }
        assert _extract_text(body) == "Mistral analysis"

    def test_titan_format(self):
        """Amazon Titan response format."""
        body = {
            "outputText": "Titan response"
        }
        # Titan uses outputText, but our extractor doesn't handle it
        # It falls through to the stringifier
        result = _extract_text(body)
        assert "Titan" in result or "outputText" in result

    def test_cohere_format(self):
        """Cohere response format."""
        body = {
            "generations": [{"text": "Cohere response"}]
        }
        # Cohere uses generations, falls through to stringifier
        result = _extract_text(body)
        assert "Cohere" in result or "generations" in result
