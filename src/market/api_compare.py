from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin
from urllib.request import urlopen


DEFAULT_COMPARE_ENDPOINTS = [
    "/api/health",
    "/api/boards/HK_STOCK_FOCUS20",
    "/api/boards/A_SHARE_FOCUS20",
    "/api/boards/CRYPTO_TURNOVER_TOP50",
    "/api/instruments/HK/00700",
    "/api/bars/daily?market=HK&symbol=00700&limit=5",
    "/api/bars/intraday?market=CRYPTO&symbol=BTCUSDT&interval=1m&limit=5",
]


@dataclass(frozen=True)
class ApiPayloadDifference:
    endpoint: str
    path: str
    primary: object
    candidate: object


@dataclass(frozen=True)
class ApiPayloadError:
    endpoint: str
    message: str


@dataclass(frozen=True)
class ApiCompareResult:
    matched: list[str]
    differences: list[ApiPayloadDifference]
    errors: list[ApiPayloadError]

    @property
    def status(self) -> str:
        if self.errors:
            return "error"
        if self.differences:
            return "different"
        return "match"

    @property
    def exit_code(self) -> int:
        if self.errors:
            return 2
        if self.differences:
            return 1
        return 0


def fetch_json(url: str) -> object:
    with urlopen(url, timeout=20) as response:
        return json.load(response)


def compare_api_payloads(
    primary_base_url: str,
    candidate_base_url: str,
    *,
    endpoints: list[str] | None = None,
    fetch_json: Callable[[str], object] = fetch_json,
) -> ApiCompareResult:
    selected_endpoints = endpoints or DEFAULT_COMPARE_ENDPOINTS
    matched: list[str] = []
    differences: list[ApiPayloadDifference] = []
    errors: list[ApiPayloadError] = []
    for endpoint in selected_endpoints:
        try:
            primary_payload = fetch_json(_join_endpoint(primary_base_url, endpoint))
            candidate_payload = fetch_json(_join_endpoint(candidate_base_url, endpoint))
        except Exception as error:  # pragma: no cover - exact urllib failures vary
            errors.append(ApiPayloadError(endpoint=endpoint, message=str(error)))
            continue
        difference = _first_difference(primary_payload, candidate_payload)
        if difference is None:
            matched.append(endpoint)
            continue
        path, primary, candidate = difference
        differences.append(
            ApiPayloadDifference(
                endpoint=endpoint,
                path=path,
                primary=primary,
                candidate=candidate,
            )
        )
    return ApiCompareResult(
        matched=matched,
        differences=differences,
        errors=errors,
    )


def format_api_compare_report(result: ApiCompareResult) -> str:
    lines = [
        "api payload diff: "
        f"{len(result.matched)} matched, "
        f"{len(result.differences)} different, "
        f"{len(result.errors)} errors"
    ]
    for difference in result.differences:
        lines.append(
            f"DIFF {difference.endpoint} {difference.path}: "
            f"primary={_compact_json(difference.primary)} "
            f"candidate={_compact_json(difference.candidate)}"
        )
    for error in result.errors:
        lines.append(f"ERROR {error.endpoint}: {error.message}")
    return "\n".join(lines)


def _join_endpoint(base_url: str, endpoint: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))


def _first_difference(
    primary: object,
    candidate: object,
    path: str = "",
) -> tuple[str, object, object] | None:
    if type(primary) is not type(candidate):
        return path or "$", primary, candidate
    if isinstance(primary, dict) and isinstance(candidate, dict):
        primary_keys = set(primary)
        candidate_keys = set(candidate)
        if primary_keys != candidate_keys:
            missing = sorted(primary_keys ^ candidate_keys)[0]
            return _join_path(path, missing), primary.get(missing), candidate.get(missing)
        for key in sorted(primary_keys):
            difference = _first_difference(
                primary[key],
                candidate[key],
                _join_path(path, key),
            )
            if difference is not None:
                return difference
        return None
    if isinstance(primary, list) and isinstance(candidate, list):
        if len(primary) != len(candidate):
            return _join_path(path, "length"), len(primary), len(candidate)
        for index, (primary_item, candidate_item) in enumerate(
            zip(primary, candidate, strict=True)
        ):
            difference = _first_difference(
                primary_item,
                candidate_item,
                f"{path}[{index}]" if path else f"[{index}]",
            )
            if difference is not None:
                return difference
        return None
    if primary != candidate:
        return path or "$", primary, candidate
    return None


def _join_path(prefix: str, key: str) -> str:
    if not prefix:
        return key
    if key == "length":
        return f"{prefix}.length"
    return f"{prefix}.{key}"


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
