"""Custom exceptions for the options trading agent."""


class DataFetchError(Exception):
    """Raised when market data cannot be fetched from an external source (yfinance, MCP)."""


class InsufficientDataError(DataFetchError):
    """Raised when fetched data has too few bars/candles for analysis."""


class PortfolioDataError(Exception):
    """Raised when portfolio data is missing or invalid for risk checks."""
