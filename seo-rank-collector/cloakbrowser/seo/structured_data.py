"""SEO structured data extractor: Schema.org JSON-LD and Microdata."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class StructuredDataAuditResult:
    json_ld_schemas: List[Dict[str, Any]] = field(default_factory=list)
    schema_types: List[str] = field(default_factory=list)
    has_valid_schema: bool = False
    errors: List[str] = field(default_factory=list)


class StructuredDataExtractor:
    """Extracts and validates Schema.org JSON-LD structured data scripts."""

    @staticmethod
    def extract(page: Any) -> StructuredDataAuditResult:
        js = """
        () => {
            const schemas = [];
            document.querySelectorAll('script[type="application/ld+json"]').forEach(el => {
                try {
                    schemas.push(JSON.parse(el.innerText));
                } catch (e) {
                    schemas.push({ __error: e.message, __raw: el.innerText });
                }
            });
            return schemas;
        }
        """
        raw_schemas = page.evaluate(js) if hasattr(page, "evaluate") else []
        valid_schemas: List[Dict[str, Any]] = []
        schema_types: List[str] = []
        errors: List[str] = []

        for s in raw_schemas:
            if isinstance(s, dict) and "__error" in s:
                errors.append(f"JSON-LD syntax error: {s['__error']}")
            elif isinstance(s, dict):
                valid_schemas.append(s)
                stype = s.get("@type")
                if stype:
                    if isinstance(stype, list):
                        schema_types.extend(stype)
                    else:
                        schema_types.append(stype)

        return StructuredDataAuditResult(
            json_ld_schemas=valid_schemas,
            schema_types=schema_types,
            has_valid_schema=len(valid_schemas) > 0,
            errors=errors,
        )
