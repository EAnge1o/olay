"""Optional read-only adapter for the C2PA Python SDK."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from content_trust.schemas import ProvenanceAnalysis


class C2paPythonClient:
    provider_name = "c2pa-python"

    def inspect(self, image_path: str) -> ProvenanceAnalysis:
        path = Path(image_path)
        if not path.is_file():
            return ProvenanceAnalysis(
                status="unavailable",
                error="image_unavailable",
            )

        try:
            import c2pa
        except ImportError:
            return ProvenanceAnalysis(
                status="unavailable",
                error="c2pa_dependency_missing",
            )

        try:
            context = c2pa.Context.from_dict(
                {"verify": {"remote_manifest_fetch": False, "ocsp_fetch": False}}
            )
            with context:
                with c2pa.Reader(str(path), context=context) as reader:
                    store = json.loads(reader.json())
                    validation_state = _validation_state(reader)
            if not isinstance(store, dict):
                raise ValueError("invalid_manifest_store")
        except Exception as exc:
            if _is_manifest_not_found(c2pa, exc):
                return ProvenanceAnalysis(
                    status="absent",
                    manifest_present=False,
                )
            return ProvenanceAnalysis(
                status="unavailable",
                error="c2pa_read_failed",
            )

        active_id = store.get("active_manifest")
        manifests = store.get("manifests")
        if not isinstance(manifests, dict) or not manifests:
            return ProvenanceAnalysis(
                status="absent",
                manifest_present=False,
                validation_state=validation_state,
            )

        active = manifests.get(active_id) if isinstance(active_id, str) else None
        active = active if isinstance(active, dict) else {}
        signature = active.get("signature_info")
        signature = signature if isinstance(signature, dict) else {}
        ingredients = active.get("ingredients")
        ingredients = ingredients if isinstance(ingredients, list) else []
        status = _normalized_status(validation_state)
        return ProvenanceAnalysis(
            status=status,
            manifest_present=True,
            validation_state=validation_state,
            active_manifest=active_id if isinstance(active_id, str) else None,
            claim_generator=_claim_generator(active),
            signature_issuer=_optional_text(signature.get("issuer")),
            signature_time=_optional_text(signature.get("time")),
            actions=_actions(active),
            ingredients_count=len(ingredients),
            error=(
                "c2pa_validation_inconclusive"
                if status == "unavailable"
                else None
            ),
        )


def _validation_state(reader: Any) -> str | None:
    getter = getattr(reader, "get_validation_state", None)
    if not callable(getter):
        return None
    value = getter()
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("validation_status", "status", "state"):
            candidate = value.get(key)
            if candidate is not None:
                return str(candidate)
    return str(value) if value is not None else None


def _is_manifest_not_found(c2pa: Any, error: Exception) -> bool:
    error_type = getattr(c2pa, "C2paError", None)
    message = getattr(error, "message", None) or str(error)
    return (
        isinstance(error_type, type)
        and isinstance(error, error_type)
        and message.startswith("ManifestNotFound:")
    )


def _normalized_status(validation_state: str | None) -> str:
    if validation_state is None:
        return "unavailable"
    normalized = validation_state.strip().casefold()
    if "invalid" in normalized or "untrusted" in normalized:
        return "invalid"
    if "warning" in normalized:
        return "unavailable"
    if normalized in {
        "valid",
        "valid manifest",
        "valid_manifest",
        "validationstate.valid",
        "validationstate::valid",
    }:
        return "valid"
    return "unavailable"


def _claim_generator(manifest: dict[str, Any]) -> str | None:
    value = manifest.get("claim_generator")
    if isinstance(value, str):
        return value
    details = manifest.get("claim_generator_info")
    if isinstance(details, list):
        names = [
            str(item.get("name"))
            for item in details
            if isinstance(item, dict) and item.get("name")
        ]
        return ", ".join(names) or None
    if isinstance(details, dict) and details.get("name"):
        return str(details["name"])
    return None


def _actions(manifest: dict[str, Any]) -> list[str]:
    assertions = manifest.get("assertions")
    if not isinstance(assertions, list):
        return []
    values: list[str] = []
    for assertion in assertions:
        if not isinstance(assertion, dict) or assertion.get("label") != "c2pa.actions":
            continue
        data = assertion.get("data")
        actions = data.get("actions") if isinstance(data, dict) else None
        if not isinstance(actions, list):
            continue
        values.extend(
            str(item["action"])
            for item in actions
            if isinstance(item, dict) and item.get("action")
        )
    return list(dict.fromkeys(values))


def _optional_text(value: Any) -> str | None:
    return str(value).strip() if value is not None and str(value).strip() else None
