from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os
import time
from pathlib import Path

# Initialize FastAPI app
app = FastAPI(
    title="Moon Dev's AI Agents 🌙",
    description="AI Agents for the Workplace",
    version="1.0.0"
)

# Bedrock health check cache
_bedrock_health_cache = None
_bedrock_health_cache_time = 0

# Get the project root directory
PROJECT_ROOT = Path(__file__).parent.parent.parent

# Mount the static directory
app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "src/frontend/static")), name="static")

# Set up templates
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "src/frontend/templates"))

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/request-agent")
async def request_agent_form(request: Request):
    return templates.TemplateResponse("request_agent.html", {"request": request})

@app.get("/submit-agent")
async def submit_agent_form(request: Request):
    return templates.TemplateResponse("submit_agent.html", {"request": request})

@app.get("/thank-you")
async def thank_you(request: Request):
    return templates.TemplateResponse("thank_you.html", {"request": request})

# ── AWS Bedrock Health Check Endpoint ─────────────────────────

@app.get("/bedrock-test")
async def bedrock_test():
    """Test AWS Bedrock connectivity and model response."""
    global _bedrock_health_cache, _bedrock_health_cache_time
    
    # Cache for 60 seconds to avoid hammering AWS
    if _bedrock_health_cache and (time.time() - _bedrock_health_cache_time) < 60:
        return _bedrock_health_cache
    
    from src.bedrock_llm import bedrock_chat, ChatMessage, ChatOptions, get_bedrock_config, is_bedrock_configured
    
    config = get_bedrock_config()
    
    if not config["configured"]:
        result = {
            "ok": False,
            "error": "AWS Bedrock not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in .env",
            "config": config,
        }
        return JSONResponse(content=result, status_code=503)
    
    try:
        start = time.time()
        response = await bedrock_chat(
            [ChatMessage(role="user", content="Reply with exactly: OK")],
            ChatOptions(max_tokens=10, temperature=0.0),
        )
        latency_ms = round((time.time() - start) * 1000)
        
        result = {
            "ok": True,
            "model": config["model_id"],
            "region": config["region"],
            "latency_ms": latency_ms,
            "response": response.text[:200],
            "message": "AWS Bedrock is working! 🚀",
        }
    except Exception as e:
        result = {
            "ok": False,
            "error": str(e),
            "config": config,
            "message": "AWS Bedrock connection failed ❌",
        }
    
    _bedrock_health_cache = result
    _bedrock_health_cache_time = time.time()
    
    status_code = 200 if result["ok"] else 503
    return JSONResponse(content=result, status_code=status_code)


@app.get("/bedrock-config")
async def bedrock_config():
    """Show current Bedrock configuration (non-sensitive)."""
    from src.bedrock_llm import get_bedrock_config
    config = get_bedrock_config()
    return {
        "configured": config["configured"],
        "model": config["model_id"],
        "region": config["region"],
        "status": "ready" if config["configured"] else "not_configured",
    }


@app.post("/bedrock-test-model")
async def bedrock_test_model(request: Request):
    """Test Bedrock with a custom prompt."""
    from src.bedrock_llm import bedrock_chat, ChatMessage, ChatOptions, get_bedrock_config
    
    body = await request.json()
    prompt = body.get("prompt", "Say hello in 5 words")
    temperature = body.get("temperature", 0.3)
    max_tokens = body.get("max_tokens", 100)
    
    config = get_bedrock_config()
    if not config["configured"]:
        return JSONResponse(
            content={"ok": False, "error": "AWS Bedrock not configured"},
            status_code=503
        )
    
    try:
        start = time.time()
        response = await bedrock_chat(
            [ChatMessage(role="user", content=prompt)],
            ChatOptions(max_tokens=max_tokens, temperature=temperature),
        )
        latency_ms = round((time.time() - start) * 1000)
        
        return {
            "ok": True,
            "model": config["model_id"],
            "latency_ms": latency_ms,
            "prompt": prompt[:100],
            "response": response.text[:1000],
        }
    except Exception as e:
        return JSONResponse(
            content={"ok": False, "error": str(e)},
            status_code=500
        )


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    print(f"🚀 Starting server on port {port}")
    print(f"🤖 Bedrock test: http://localhost:{port}/bedrock-test")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True) 