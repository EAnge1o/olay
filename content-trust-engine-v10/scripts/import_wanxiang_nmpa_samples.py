"""Import the two downloaded Wanxiang Data cosmetics filing sample exports."""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any

from content_trust.nmpa import NMPAProductRecord, NMPARegistry


SAMPLE_FILES = {
    "imported": "pharma.imported-ordinary-cosmetic-filings-sample.json",
    "domestic": "pharma.domestic-ordinary-cosmetic-filings-sample.json",
}
SOURCE_URL = "https://wanxiangdata.com/"


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def parse_ingredients(value: Any) -> list[str]:
    text = clean_text(value)
    if not text:
        return []
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        return []
    candidate = text[start : end + 1].replace("，", ",")
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in parsed if str(item).strip()))


def export_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        raise ValueError("sample export is missing its metadata object")
    return {
        key: meta.get(key)
        for key in (
            "id",
            "data_version",
            "schema_version",
            "row_count",
            "sha256",
            "approved_at",
        )
    }


def convert_record(
    row: dict[str, Any], *, dataset: str, source_file: str, source_meta: dict[str, Any]
) -> NMPAProductRecord:
    product_name = clean_text(row.get("prod_name"))
    if not product_name:
        raise ValueError(f"record {row.get('id')!r} has no product name")

    dataset_label = (
        "进口普通化妆品备案" if dataset == "imported" else "国产普通化妆品备案"
    )
    status_parts = []
    if row.get("sfyx") not in (None, ""):
        status_parts.append(
            f"供应商原始有效性代码 sfyx={row['sfyx']}（枚举含义未映射）"
        )
    if offdate := clean_text(row.get("offdate")):
        status_parts.append(f"下架日期字段：{offdate}")

    efficacy_claims = []
    for key, label in (
        ("gxxc_cpgx", "产品功效字段"),
        ("gxxc_gxpjjl", "功效评价结论字段"),
    ):
        if value := clean_text(row.get(key)):
            efficacy_claims.append(f"{label}：{value}")

    aliases = []
    if alias := clean_text(row.get("prod_name_en")):
        if alias.casefold() != product_name.casefold():
            aliases.append(alias)

    return NMPAProductRecord(
        record_id=f"wanxiang:{dataset}:{row.get('id')}",
        product_name=product_name,
        aliases=aliases,
        filing_number=clean_text(row.get("record_no")),
        filing_person=clean_text(row.get("ent_name")),
        product_category=dataset_label,
        filing_status="；".join(status_parts) or None,
        filing_date=clean_text(row.get("record_date")),
        efficacy_claims=efficacy_claims,
        ingredients=parse_ingredients(row.get("component_original") or row.get("component")),
        source_provider="wanxiangdata",
        source_is_sample=True,
        source_url=SOURCE_URL,
        raw_record={
            "source_file": source_file,
            "source_dataset": dataset_label,
            "source_export": source_meta,
            "provider_record": row,
        },
    )


def load_sample(path: Path, dataset: str) -> list[NMPAProductRecord]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError(f"{path} must contain an object with a data array")
    if payload.get("status") != "published":
        raise ValueError(f"{path} is not marked as a published sample export")

    rows = payload["data"]
    meta = export_metadata(payload)
    expected_count = meta.get("row_count")
    if expected_count is not None and int(expected_count) != len(rows):
        raise ValueError(
            f"{path} metadata says {expected_count} rows but contains {len(rows)}"
        )
    return [
        convert_record(
            row,
            dataset=dataset,
            source_file=path.name,
            source_meta=meta,
        )
        for row in rows
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples-dir", type=Path, default=Path("data/nmpa/source_samples"))
    parser.add_argument("--db", type=Path, default=Path("data/nmpa/cosmetics.sqlite3"))
    args = parser.parse_args()

    records = []
    counts = {}
    for dataset, filename in SAMPLE_FILES.items():
        path = args.samples_dir / filename
        loaded = load_sample(path, dataset)
        records.extend(loaded)
        counts[dataset] = len(loaded)

    registry = NMPARegistry(args.db)
    imported = registry.upsert(records)
    print(
        json.dumps(
            {
                "source_provider": "wanxiangdata",
                "sample_records_loaded": counts,
                "records_upserted": imported,
                "database_records_total": registry.count(),
                "database_path": str(args.db),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
