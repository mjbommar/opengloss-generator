"""OpenGloss Generator: schema-validated, cost-aware lexical knowledge-graph generation."""

from opengloss_generator.config import AppConfig, load_config
from opengloss_generator.errors import (
    BudgetExceededError,
    GenerationError,
    OpenGlossError,
    StageFailedError,
    StoreError,
)
from opengloss_generator.schema import Lexeme, ReadingLevel, Register, Sense
from opengloss_generator.store import LexemeStore

__version__ = "0.1.0"

__all__ = [
    "AppConfig",
    "BudgetExceededError",
    "GenerationError",
    "Lexeme",
    "LexemeStore",
    "OpenGlossError",
    "ReadingLevel",
    "Register",
    "Sense",
    "StageFailedError",
    "StoreError",
    "__version__",
    "load_config",
]
