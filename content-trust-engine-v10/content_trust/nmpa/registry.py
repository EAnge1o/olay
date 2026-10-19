"""Small SQLite registry for normalized, source-linked NMPA filing records.

The importer deliberately accepts a documented normalized JSONL format. It does
not guess the shape of NMPA's protected query response or bypass its access page.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from content_trust.schemas import EvidenceItem, ProductRegistryMatch


class NMPAProductRecord(BaseModel):
    """Normalized filing record with explicit upstream-data provenance."""

    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1)
    product_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    filing_number: str | None = None
    filing_person: str | None = None
    product_category: str | None = None
    filing_status: str | None = None
    filing_date: str | None = None
    efficacy_claims: list[str] = Field(default_factory=list)
    ingredients: list[str] = Field(default_factory=list)
    source_provider: Literal["nmpa_official", "wanxiangdata"] = "nmpa_official"
    source_is_sample: bool = False
    source_url: str = Field(min_length=1)
    retrieved_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    raw_record: dict[str, Any] = Field(default_factory=dict)

    @field_validator("product_name", "record_id")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("aliases", "efficacy_claims", "ingredients")
    @classmethod
    def clean_text_list(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @field_validator("source_url")
    @classmethod
    def known_source_url(cls, value: str, info: ValidationInfo) -> str:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        provider = info.data.get("source_provider", "nmpa_official")
        if parsed.scheme != "https":
            raise ValueError("source_url must use HTTPS")
        if provider == "nmpa_official" and not (
            host == "nmpa.gov.cn" or host.endswith(".nmpa.gov.cn")
        ):
            raise ValueError("official NMPA records require an NMPA government URL")
        if provider == "wanxiangdata" and not (
            host == "wanxiangdata.com" or host.endswith(".wanxiangdata.com")
        ):
            raise ValueError("Wanxiang Data records require a wanxiangdata.com URL")
        return value


class NMPAMatch(BaseModel):
    record: NMPAProductRecord
    matched_query: str
    match_method: Literal["exact_name", "exact_alias"]

    def to_public_match(self) -> ProductRegistryMatch:
        return ProductRegistryMatch(
            record_id=self.record.record_id,
            product_name=self.record.product_name,
            matched_query=self.matched_query,
            match_method=self.match_method,
            filing_number=self.record.filing_number,
            filing_person=self.record.filing_person,
            product_category=self.record.product_category,
            filing_status=self.record.filing_status,
            filing_date=self.record.filing_date,
            efficacy_claims=self.record.efficacy_claims,
            ingredients=self.record.ingredients,
            source_provider=self.record.source_provider,
            source_is_sample=self.record.source_is_sample,
            source_url=self.record.source_url,
            retrieved_at=self.record.retrieved_at,
        )

    def to_evidence_item(self) -> EvidenceItem:
        record = self.record
        is_vendor_sample = record.source_provider == "wanxiangdata"
        name_label = "供应商样例中的产品名称" if is_vendor_sample else "备案公示产品名称"
        facts = [f"{name_label}：{record.product_name}"]
        for label, value in (
            ("备案编号", record.filing_number),
            ("备案人", record.filing_person),
            ("产品类别", record.product_category),
            ("备案状态", record.filing_status),
            ("备案日期", record.filing_date),
        ):
            if value:
                facts.append(f"{label}：{value}")
        if record.ingredients:
            facts.append("备案记录所列成分：" + "、".join(record.ingredients))
        if record.efficacy_claims:
            facts.append("备案记录所列功效宣称：" + "；".join(record.efficacy_claims))
        facts.append(
            "证据范围：该记录只反映数据源所列备案字段及功效相关信息；不能单独证明实际功效、"
            "安全性、临床效果或手中商品真伪。"
        )
        if is_vendor_sample:
            facts.insert(
                0,
                "来源说明：此记录来自万象数据提供的 NMPA 备案数据样例，"
                "尚未对该条记录逐项进行 NMPA 官方页面复核。",
            )
        return EvidenceItem(
            id=f"nmpa:{record.record_id}",
            source=(
                "Wanxiang Data sample of NMPA cosmetics filing data"
                if is_vendor_sample
                else "NMPA cosmetics filing record"
            ),
            text="\n".join(facts),
            url=record.source_url,
            metadata={
                "registry": "nmpa",
                "source_provider": record.source_provider,
                "source_is_sample": record.source_is_sample,
                "record_id": record.record_id,
                "matched_query": self.matched_query,
                "match_method": self.match_method,
                "evidence_scope": [
                    "product_identity",
                    "filing_metadata",
                    "listed_ingredients",
                    "recorded_efficacy_claims",
                ],
                "not_proof_of": ["actual_efficacy", "safety", "clinical_outcome", "physical_item_authenticity"],
                "retrieved_at": record.retrieved_at,
            },
        )


class NMPARegistry:
    """SQLite-backed exact-name lookup; no fuzzy match is promoted to evidence."""

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS cosmetics (
                    record_id TEXT PRIMARY KEY,
                    product_name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    filing_number TEXT,
                    filing_person TEXT,
                    product_category TEXT,
                    filing_status TEXT,
                    filing_date TEXT,
                    efficacy_claims_json TEXT NOT NULL,
                    ingredients_json TEXT NOT NULL,
                    source_provider TEXT NOT NULL DEFAULT 'nmpa_official',
                    source_is_sample INTEGER NOT NULL DEFAULT 0,
                    source_url TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    raw_record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cosmetics_name
                    ON cosmetics(normalized_name);
                CREATE TABLE IF NOT EXISTS cosmetics_aliases (
                    alias_normalized TEXT NOT NULL,
                    alias_text TEXT NOT NULL,
                    record_id TEXT NOT NULL REFERENCES cosmetics(record_id)
                        ON DELETE CASCADE,
                    PRIMARY KEY(alias_normalized, record_id)
                );
                CREATE INDEX IF NOT EXISTS idx_cosmetics_alias
                    ON cosmetics_aliases(alias_normalized);
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(cosmetics)")
            }
            if "source_provider" not in columns:
                connection.execute(
                    "ALTER TABLE cosmetics ADD COLUMN source_provider "
                    "TEXT NOT NULL DEFAULT 'nmpa_official'"
                )
            if "source_is_sample" not in columns:
                connection.execute(
                    "ALTER TABLE cosmetics ADD COLUMN source_is_sample "
                    "INTEGER NOT NULL DEFAULT 0"
                )

    def import_jsonl(self, input_path: str | Path) -> int:
        """Import normalized records; each input line must validate independently."""
        self.initialize()
        records: list[NMPAProductRecord] = []
        with Path(input_path).open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                try:
                    records.append(NMPAProductRecord.model_validate_json(line))
                except Exception as exc:
                    raise ValueError(f"Invalid normalized NMPA record at line {line_number}: {exc}") from exc
        self.upsert(records)
        return len(records)

    def upsert(self, records: Iterable[NMPAProductRecord]) -> int:
        self.initialize()
        count = 0
        with self._connect() as connection:
            for record in records:
                connection.execute(
                    """
                    INSERT INTO cosmetics (
                        record_id, product_name, normalized_name, filing_number,
                        filing_person, product_category, filing_status, filing_date,
                        efficacy_claims_json, ingredients_json, source_provider,
                        source_is_sample, source_url, retrieved_at, raw_record_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(record_id) DO UPDATE SET
                        product_name=excluded.product_name,
                        normalized_name=excluded.normalized_name,
                        filing_number=excluded.filing_number,
                        filing_person=excluded.filing_person,
                        product_category=excluded.product_category,
                        filing_status=excluded.filing_status,
                        filing_date=excluded.filing_date,
                        efficacy_claims_json=excluded.efficacy_claims_json,
                        ingredients_json=excluded.ingredients_json,
                        source_provider=excluded.source_provider,
                        source_is_sample=excluded.source_is_sample,
                        source_url=excluded.source_url,
                        retrieved_at=excluded.retrieved_at,
                        raw_record_json=excluded.raw_record_json
                    """,
                    (
                        record.record_id,
                        record.product_name,
                        normalize_product_name(record.product_name),
                        record.filing_number,
                        record.filing_person,
                        record.product_category,
                        record.filing_status,
                        record.filing_date,
                        json.dumps(record.efficacy_claims, ensure_ascii=False),
                        json.dumps(record.ingredients, ensure_ascii=False),
                        record.source_provider,
                        int(record.source_is_sample),
                        record.source_url,
                        record.retrieved_at,
                        json.dumps(record.raw_record, ensure_ascii=False),
                    ),
                )
                connection.execute(
                    "DELETE FROM cosmetics_aliases WHERE record_id = ?",
                    (record.record_id,),
                )
                for alias in record.aliases:
                    normalized = normalize_product_name(alias)
                    if normalized:
                        connection.execute(
                            "INSERT OR IGNORE INTO cosmetics_aliases "
                            "(alias_normalized, alias_text, record_id) VALUES (?, ?, ?)",
                            (normalized, alias, record.record_id),
                        )
                count += 1
        return count

    def lookup(self, product_name: str, *, limit: int = 10) -> list[NMPAMatch]:
        """Return exact normalized product-name or explicitly supplied alias matches."""
        normalized = normalize_product_name(product_name)
        if not normalized or not self.path.is_file():
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT c.*, 'exact_name' AS match_method, c.product_name AS matched_query
                FROM cosmetics AS c
                WHERE c.normalized_name = ?
                UNION ALL
                SELECT c.*, 'exact_alias' AS match_method, a.alias_text AS matched_query
                FROM cosmetics_aliases AS a
                JOIN cosmetics AS c ON c.record_id = a.record_id
                WHERE a.alias_normalized = ?
                ORDER BY product_name, record_id
                LIMIT ?
                """,
                (normalized, normalized, limit),
            ).fetchall()
        return [self._row_to_match(row, product_name) for row in rows]

    def find_mentions(self, text: str, *, limit: int = 10) -> list[NMPAMatch]:
        """Find complete registered names/aliases explicitly present in supplied text.

        This is a local dictionary scan, not fuzzy entity resolution. Very short
        normalized strings are skipped to avoid matching generic fragments such
        as product categories or common words.
        """
        normalized_text = normalize_product_name(text)
        if len(normalized_text) < 5 or not self.path.is_file():
            return []

        with self._connect() as connection:
            name_rows = connection.execute(
                """
                SELECT c.*, 'exact_name' AS match_method,
                       c.product_name AS matched_query
                FROM cosmetics AS c
                WHERE length(c.normalized_name) >= 5
                  AND instr(?, c.normalized_name) > 0
                ORDER BY length(c.normalized_name) DESC, c.product_name
                LIMIT ?
                """,
                (normalized_text, limit),
            ).fetchall()
            alias_rows = connection.execute(
                """
                SELECT c.*, 'exact_alias' AS match_method,
                       a.alias_text AS matched_query
                FROM cosmetics_aliases AS a
                JOIN cosmetics AS c ON c.record_id = a.record_id
                WHERE length(a.alias_normalized) >= 5
                  AND instr(?, a.alias_normalized) > 0
                ORDER BY length(a.alias_normalized) DESC, a.alias_text
                LIMIT ?
                """,
                (normalized_text, limit),
            ).fetchall()

        matches: list[NMPAMatch] = []
        seen_record_ids: set[str] = set()
        for row in [*name_rows, *alias_rows]:
            if row["record_id"] in seen_record_ids:
                continue
            seen_record_ids.add(row["record_id"])
            matches.append(self._row_to_match(row, row["matched_query"]))
            if len(matches) >= limit:
                break
        return matches

    def count(self) -> int:
        if not self.path.is_file():
            return 0
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM cosmetics").fetchone()
        return int(row[0])

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _row_to_match(row: sqlite3.Row, query: str) -> NMPAMatch:
        record = NMPAProductRecord(
            record_id=row["record_id"],
            product_name=row["product_name"],
            filing_number=row["filing_number"],
            filing_person=row["filing_person"],
            product_category=row["product_category"],
            filing_status=row["filing_status"],
            filing_date=row["filing_date"],
            efficacy_claims=json.loads(row["efficacy_claims_json"]),
            ingredients=json.loads(row["ingredients_json"]),
            source_provider=row["source_provider"],
            source_is_sample=bool(row["source_is_sample"]),
            source_url=row["source_url"],
            retrieved_at=row["retrieved_at"],
            raw_record=json.loads(row["raw_record_json"]),
        )
        return NMPAMatch(
            record=record,
            matched_query=query,
            match_method=row["match_method"],
        )


def normalize_product_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE)
