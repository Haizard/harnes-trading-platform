from google import genai
from termcolor import cprint
from .base_model import BaseModel, ModelResponse

class GeminiModel(BaseModel):
    """Implementation for Google's Gemini models"""
    
    AVAILABLE_MODELS = {
        "gemini-3-flash-preview": "Latest Gemini 3 Flash model",
        "gemini-2.0-flash": "Latest Gemini 2.0 Flash model",
        "gemini-2.0-flash-lite": "Gemini 2.0 Flash Lite model",
        "gemini-pro-latest": "Latest Gemini Pro model",
        "gemini-pro": "Standard Gemini Pro model"
    }
    
    def __init__(self, api_key: str, model_name: str = "gemini-3-flash-preview", **kwargs):
        self.model_name = model_name
        super().__init__(api_key, **kwargs)
    
    def initialize_client(self, **kwargs) -> None:
        """Initialize the Gemini client using new google-genai SDK"""
        try:
            self.client = genai.Client(api_key=self.api_key)
            cprint(f"✨ Initialized Gemini model with NEW SDK: {self.model_name}", "green")
        except Exception as e:
            cprint(f"❌ Failed to initialize Gemini model: {str(e)}", "red")
            self.client = None
    
    def generate_response(self, 
        system_prompt: str,
        user_content: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs
    ) -> ModelResponse:
        """Generate a response using Gemini with new SDK and retry logic"""
        import time
        import random
        from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
        
        # Define a helper for retries
        @retry(
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=10, min=10, max=120),
            retry=retry_if_exception(lambda e: "429" in str(e) or "quota" in str(e).lower() or "503" in str(e)),
            before_sleep=lambda retry_state: cprint(f"⚠️ Gemini quota hit (429/503). Retrying... (Attempt {retry_state.attempt_number}/5) - Wait: {retry_state.upcoming_sleep}s", "yellow")
        )
        def _generate():
            full_content = f"{system_prompt}\n\n{user_content}"
            
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=full_content,
                config={
                    'temperature': temperature,
                    'max_output_tokens': max_tokens
                }
            )
            return response

        try:
            response = _generate()
            
            return ModelResponse(
                content=response.text.strip(),
                raw_response=response,
                model_name=self.model_name,
                usage=None
            )
            
        except Exception as e:
            error_msg = str(e)
            # Try to unwrap tenacity RetryError
            if "RetryError" in error_msg:
                try:
                    import tenacity
                    if isinstance(e, tenacity.RetryError):
                        last_e = e.last_attempt.exception()
                        error_msg = f"RetryError wrapping: {str(last_e)}"
                        if hasattr(last_e, 'response'):
                             error_msg += f"\nResponse Body: {last_e.response.text if hasattr(last_e.response, 'text') else 'No body'}"
                except: pass
            
            cprint(f"❌ Gemini NEW SDK generation error: {error_msg}", "red")
            raise e

    def is_available(self) -> bool:
        """Check if Gemini is available"""
        return self.client is not None
    
    @property
    def model_type(self) -> str:
        return "gemini"