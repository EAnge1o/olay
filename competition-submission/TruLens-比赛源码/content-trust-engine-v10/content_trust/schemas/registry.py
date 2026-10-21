"""Public, provenance-preserving matches from product registries."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ProductRegistryMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registry: Literal["nmpa"] = "nmpa"
    record_id: str
    product_name: str
    matched_query: str
    match_method: Literal["exact_name", "exact_alias"]
    filing_number: str | None = None
    filing_person: str | None = None
    product_category: str | None = None
    filing_status: str | None = None
    filing_date: str | None = None
    efficacy_claims: list[str]
    ingredients: list[str]
    source_provider: Literal["nmpa_official", "wanxiangdata"] = "nmpa_official"
    source_is_sample: bool = False
    source_url: str
    retrieved_at: str
