"""Schema + loader for golden_set.json (validated, so a typo in a case fails loudly)."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.core.schemas import AnalystOutput

EVALS_DIR = Path(__file__).parent
GOLDEN_SET_PATH = EVALS_DIR / "golden_set.json"
FIXTURES_DIR = EVALS_DIR / "fixtures"


class ExtractionCase(BaseModel):
    """Free-text request -> expected ticker. expected_ticker=None means extraction must FAIL."""

    model_config = ConfigDict(extra="forbid")
    id: str
    request: str
    expected_ticker: str | None
    notes: str


class JudgeCase(BaseModel):
    """A hand-written analysis checked against a frozen evidence fixture, with a manual label."""

    model_config = ConfigDict(extra="forbid")
    id: str
    fixture: str                       # file name in evals/fixtures/
    analysis: AnalystOutput
    expected_is_grounded: bool
    # Key phrases of the planted unsupported claims; each should appear in one of the
    # auditor's unsupported_claims (case-insensitive). Empty for grounded cases.
    planted_claims: list[str] = Field(default_factory=list)
    flaw: Literal["none", "wrong_number", "unit_misread", "period_misread",
                  "citation_unsupported", "outside_knowledge", "opinion_only"]
    notes: str


class PipelineCase(BaseModel):
    """Full live run (extraction + debate + auditor) with code-checked expectations."""

    model_config = ConfigDict(extra="forbid")
    id: str
    request: str
    expected_ticker: str
    expected_sector: str
    max_iterations: int = Field(ge=1)
    notes: str


class DataCheckCase(BaseModel):
    """One analyst data point and what the Data Checker (code, no LLM) should say about it."""

    model_config = ConfigDict(extra="forbid")
    id: str
    fixture: str
    data_point: str
    expected_status: Literal["match", "mismatch", "unknown_field", "not_checkable"]
    notes: str


class RiskCase(BaseModel):
    """Live Risk Officer run on a judge case's analysis + frozen evidence, with code checks."""

    model_config = ConfigDict(extra="forbid")
    id: str
    fixture: str
    analysis_from: str                 # id of a judge case whose analysis is reused
    forced_exit: bool
    critic_objections: list[str]
    # At least one of these (case-insensitive) should appear in the risks or conditions
    expect_any_keyword: list[str]
    notes: str


class GoldenSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    extraction_cases: list[ExtractionCase]
    judge_cases: list[JudgeCase]
    pipeline_cases: list[PipelineCase]
    data_check_cases: list[DataCheckCase] = Field(default_factory=list)   # Stage 9
    risk_cases: list[RiskCase] = Field(default_factory=list)              # Stage 9

    def judge_case(self, case_id: str) -> JudgeCase:
        return next(case for case in self.judge_cases if case.id == case_id)


def load_golden_set(path: Path = GOLDEN_SET_PATH) -> GoldenSet:
    return GoldenSet.model_validate_json(path.read_text(encoding="utf-8"))


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
