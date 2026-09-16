from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # OpenCode Zen key (your opencode API key). DeepSeek V4 Flash is served at cost.
    openai_api_key: str = ""
    llm_base_url: str = "https://opencode.ai/zen/v1"
    llm_model: str = "deepseek-v4-flash"

    tavily_api_key: str = ""

    # PostgreSQL connection for LangGraph checkpointing. Empty -> in-memory.
    database_url: str = ""

    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # loop guard / budget caps
    max_iterations: int = 3
    per_request_budget: float = 0.05  # USD, hard cap per task

    # total deadline (seconds) over a whole streaming run
    request_timeout: int = 180

    search_depth: str = "basic"
    max_search_results: int = 5

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
