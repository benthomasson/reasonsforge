"""Reason Maintenance System — data model based on Doyle (1979)."""

from dataclasses import dataclass, field


@dataclass
class Justification:
    """A reason for believing a node.

    Two types:
    - SL (Support List): node is IN iff ALL antecedents (inlist) are IN
      AND ALL outlist nodes are OUT
    - CP (Conditional Proof): node is IN iff assumptions are consistent

    The outlist enables non-monotonic reasoning: "believe X unless Y is
    believed." If Y comes IN, the justification becomes invalid and X
    may go OUT (if no other justification supports it).
    """

    type: str  # "SL" or "CP"
    antecedents: list[str] = field(default_factory=list)  # inlist: must be IN
    outlist: list[str] = field(default_factory=list)  # must be OUT
    label: str = ""
    content_hash: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.type, str) or self.type not in {"SL", "CP"}:
            raise ValueError(f"Invalid justification type: {self.type!r}; expected 'SL' or 'CP'")
        for name, references in (("antecedents", self.antecedents), ("outlist", self.outlist)):
            if not isinstance(references, list) or not all(
                isinstance(reference, str) and reference for reference in references
            ):
                raise ValueError(f"{name} must be a list of non-empty node IDs")


@dataclass
class Node:
    """A node in the dependency network.

    A node is IN if ANY of its justifications is valid.
    A premise has no justifications and is IN by default.
    """

    id: str
    text: str
    truth_value: str = "IN"  # IN or OUT
    justifications: list[Justification] = field(default_factory=list)
    supporting_justification: int | None = None  # index into justifications list
    dependents: set[str] = field(default_factory=set)  # reverse index
    source: str = ""
    source_url: str = ""
    source_hash: str = ""
    model: str = ""
    text_hash: str = ""
    date: str = ""
    metadata: dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    reviewed_at: str = ""
    verified_at: str = ""
    retracted_at: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.truth_value, str) or self.truth_value not in {"IN", "OUT"}:
            raise ValueError(
                f"Invalid truth value: {self.truth_value!r}; expected 'IN' or 'OUT'"
            )


@dataclass
class Nogood:
    """A recorded contradiction — these node IDs cannot all be IN simultaneously."""

    id: str
    nodes: list[str] = field(default_factory=list)
    discovered: str = ""
    resolution: str = ""
