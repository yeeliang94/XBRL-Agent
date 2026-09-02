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

    @classmethod
    def from_turn_metrics(
        cls, turns: list[dict], model: object = None,
    ) -> "TokenReport":
        """Build the legacy text report from live agent-loop telemetry.

        ``save_result`` runs inside a tool node, before the coordinator has
        observed that node's final cumulative usage. The agent loop's turn
        records are therefore the authoritative source for the completed
        report; rebuilding it after the loop prevents a successful live run
        from leaving a misleading zero-token cost file.
        """
        report = cls(model=model)
        for metric in turns:
            report.add_turn(TurnRecord(
                turn=int(metric.get("turn_index") or 0),
                tool_name=str(
                    metric.get("tool_names")
                    or metric.get("node_kind")
                    or ""
                ),
                prompt_tokens=int(metric.get("prompt_tokens") or 0),
                completion_tokens=int(metric.get("completion_tokens") or 0),
                total_tokens=int(metric.get("total_tokens") or 0),
                thinking_tokens=int(metric.get("thinking_tokens") or 0),
                cumulative_tokens=int(metric.get("cumulative_tokens") or 0),
                duration_ms=int(metric.get("duration_ms") or 0),
                timestamp=0.0,
            ))
        return report

    @property
    def grand_total(self) -> int:
        # Thinking tokens are part of total spend — excluding them here made
        # the "Total" column inconsistent with `estimate_cost()`, which does
        # include them (peer-review I15).
        return (
            self.total_prompt_tokens
            + self.total_completion_tokens
            + self.total_thinking_tokens
        )

    def add_turn(self, record: TurnRecord) -> None:
        self.turns.append(record)
        self.total_prompt_tokens += record.prompt_tokens
        self.total_completion_tokens += record.completion_tokens
        self.total_thinking_tokens += record.thinking_tokens
        # Populate cumulative_tokens from the running running totals so the
        # display column shows a real monotonically-increasing number
        # regardless of what the caller passed in.
        record.cumulative_tokens = self.grand_total

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
