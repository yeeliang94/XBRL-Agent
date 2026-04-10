from dataclasses import dataclass, field
import time

from pricing import estimate_cost as _estimate_cost


@dataclass
class TurnRecord:
    turn: int
    tool_name: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    thinking_tokens: int
    cumulative_tokens: int
    duration_ms: int
    timestamp: float


@dataclass
class TokenReport:
    turns: list[TurnRecord] = field(default_factory=list)
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_thinking_tokens: int = 0
    model: object = None  # str or PydanticAI model object for pricing lookup

    @property
    def grand_total(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens

    def add_turn(self, record: TurnRecord) -> None:
        self.turns.append(record)
        self.total_prompt_tokens += record.prompt_tokens
        self.total_completion_tokens += record.completion_tokens
        self.total_thinking_tokens += record.thinking_tokens

    def format_table(self) -> str:
        lines = []
        lines.append(
            f"{'Turn':<5} {'Tool':<25} {'Prompt':>8} {'Complete':>10} {'Think':>7} {'Cumul':>10} {'Time':>8}"
        )
        lines.append("─" * 80)
        for t in self.turns:
            lines.append(
                f"{t.turn:<5} {t.tool_name:<25} {t.prompt_tokens:>8} {t.completion_tokens:>10} {t.thinking_tokens:>7} {t.cumulative_tokens:>10} {t.duration_ms:>6}ms"
            )
        lines.append("─" * 80)
        lines.append(
            f"{'':<5} {'Total':<25} {self.total_prompt_tokens:>8} {self.total_completion_tokens:>10} {self.total_thinking_tokens:>7} {self.grand_total:>10}"
        )
        lines.append("")
        est_cost = self.estimate_cost()
        lines.append(f"Estimated cost: ${est_cost:.4f}")
        return "\n".join(lines)

    def estimate_cost(self) -> float:
        return _estimate_cost(
            self.total_prompt_tokens,
            self.total_completion_tokens,
            self.total_thinking_tokens,
            self.model,
        )
