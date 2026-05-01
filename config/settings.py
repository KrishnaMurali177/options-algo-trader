"""Options Agent — Configuration via Pydantic Settings."""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """All configurable parameters loaded from environment / .env file."""

    # ── Robinhood Credentials ──
    robinhood_username: str = ""
    robinhood_password: str = ""
    robinhood_totp_secret: str = ""

    # ── Robinhood Account Selection ──
    # If you have multiple individual accounts under one Robinhood login,
    # set the index to choose which one the agent operates on.
    #   0 = first (primary) account
    #   1 = second account
    #   2 = third account, etc.
    # You can also set the full account number directly (e.g., "123456789").
    robinhood_account_index: int = Field(
        default=1,
        description="Index of the Robinhood account to use (0=primary, 1=secondary)",
    )
    robinhood_account_number: str = Field(
        default="",
        description="Explicit account number to use (overrides index if set)",
    )

    # ── LLM Enhancement (optional) ──
    # When a Gemini API key is set, the strategy selector will ask the LLM
    # to confirm or override its algorithmic decision. Leave blank to run
    # in pure algorithmic mode with no external API calls.
    gemini_api_key: str = Field(default="", description="Google Gemini API key (optional)")
    gemini_model: str = Field(default="gemini-2.5-pro", description="Gemini model name")

    # ── Agent Behaviour ──
    dry_run: bool = Field(default=True, description="If True, log orders without executing")
    require_confirmation: bool = Field(default=True, description="Require explicit confirmation before live order execution")
    log_level: str = "INFO"

    # ── Risk Parameters ──
    max_position_size_pct: float = Field(default=0.05, description="Max 5% of portfolio per trade")
    max_loss_pct: float = Field(default=0.02, description="Max 2% portfolio loss per trade")
    max_options_allocation_pct: float = Field(default=0.15, description="Max 15% total options")
    min_dte: int = Field(default=0, description="Minimum days to expiration")
    max_dte: int = Field(default=365, description="Maximum days to expiration")
    earnings_blackout_days: int = Field(default=7, description="No trades within N days of earnings")
    max_daily_trades: int = Field(default=3, description="Max new option positions per day")
    circuit_breaker_daily_loss_pct: float = Field(default=0.03, description="Halt if daily P&L < -N%")

    # ── Scheduling ──
    market_scan_times: str = Field(default="09:35,12:00,15:30", description="ET scan times")

    # ── MCP Server ──
    mcp_server_command: str = Field(
        default="python -m mcp_server.robinhood_mcp_server",
        description="Command to start the MCP server process (stdio transport)",
    )

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


# Singleton
settings = Settings()

