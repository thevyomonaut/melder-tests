#!/usr/bin/env python3
"""Rank enroll-response edges using score, SystemCodes, and WCIS IDs.

For each enrolled record, edges at/above the configured Melder score cutoff
rank first by score. Edges below it rank by best SystemCode hierarchy, distinct
in-hierarchy code count, numeric WCIS ID ascending, then score. The input
response is never modified; ranked JSON and CSV reports are written separately.
"""

import argparse
import csv
import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_RESPONSE = BASE_DIR / "enroll_response.json"
DEFAULT_RECORDS = BASE_DIR / "enroll_test_data.csv"
DEFAULT_HIERARCHY = BASE_DIR / "systemcode_hierarchy.json"
DEFAULT_CSV_OUTPUT = BASE_DIR / "ranked_enroll_matches.csv"
DEFAULT_JSON_OUTPUT = BASE_DIR / "ranked_enroll_response.json"


def normalize_systemcode(code):
    """Normalize codes for distinctness and exclusion matching."""
    return "".join(character for character in code.casefold() if character.isalnum())


def read_records(path, id_field, codes_field, wcis_id_field, hierarchy_codes):
    """Load records and count only distinct codes in the configured hierarchy."""
    records = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"Records CSV has no header: {path}")

        headers = {header.casefold(): header for header in reader.fieldnames}
        actual_id_field = headers.get(id_field.casefold())
        actual_codes_field = headers.get(codes_field.casefold())
        actual_wcis_id_field = headers.get(wcis_id_field.casefold())
        if actual_id_field is None:
            raise ValueError(f"ID column {id_field!r} not found in {path}")
        if actual_codes_field is None:
            raise ValueError(f"SystemCodes column {codes_field!r} not found in {path}")
        if actual_wcis_id_field is None:
            raise ValueError(f"WCIS ID column {wcis_id_field!r} not found in {path}")

        seen_wcis_ids = set()
        for row_number, row in enumerate(reader, start=2):
            record_id = (row.get(actual_id_field) or "").strip()
            if not record_id:
                raise ValueError(f"Empty {actual_id_field} at CSV row {row_number}")
            if record_id in records:
                raise ValueError(f"Duplicate record ID {record_id!r} in {path}")

            raw_wcis_id = (row.get(actual_wcis_id_field) or "").strip()
            try:
                wcis_id = int(raw_wcis_id)
            except ValueError as error:
                raise ValueError(
                    f"Invalid numeric {actual_wcis_id_field} at CSV row {row_number}: {raw_wcis_id!r}"
                ) from error
            if not 230_000_000 <= wcis_id <= 410_000_000:
                raise ValueError(
                    f"{actual_wcis_id_field} at CSV row {row_number} must be between "
                    "230000000 and 410000000"
                )
            if wcis_id in seen_wcis_ids:
                raise ValueError(f"Duplicate {actual_wcis_id_field}: {wcis_id}")
            seen_wcis_ids.add(wcis_id)

            codes = []
            seen_codes = set()
            countable_codes = set()
            for raw_code in (row.get(actual_codes_field) or "").split(","):
                code = raw_code.strip()
                normalized_code = normalize_systemcode(code)
                if code and normalized_code not in seen_codes:
                    seen_codes.add(normalized_code)
                    codes.append(code)
                if code and normalized_code in hierarchy_codes:
                    countable_codes.add(normalized_code)
            records[record_id] = {
                "count": len(countable_codes),
                "codes": codes,
                "wcis_id": wcis_id,
            }
    return records


def read_hierarchy(path):
    """Read the score cutoff and codes in highest-to-lowest priority order."""
    with path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    codes = data.get("priority_high_to_low") if isinstance(data, dict) else None
    if not isinstance(codes, list) or not codes:
        raise ValueError(
            "Hierarchy JSON must contain a non-empty 'priority_high_to_low' list"
        )

    normalized = [str(code).strip().upper() for code in codes if str(code).strip()]
    normalized_codes = [normalize_systemcode(code) for code in normalized]
    if len(normalized_codes) != len(set(normalized_codes)):
        raise ValueError("Hierarchy contains duplicate SystemCodes")
    try:
        score_threshold = float(data.get("melder_score_threshold", 0.98))
    except (TypeError, ValueError) as error:
        raise ValueError("melder_score_threshold must be a number from 0 to 1") from error
    if not 0.0 <= score_threshold <= 1.0:
        raise ValueError("melder_score_threshold must be between 0 and 1")
    return normalized, score_threshold, set(normalized_codes)


def rank_response(response, records, hierarchy, score_threshold):
    results = response.get("results") if isinstance(response, dict) else None
    if not isinstance(results, list):
        raise ValueError("Enroll response must be a JSON object with a 'results' array")

    ranked_results = []
    csv_rows = []
    missing_ids = set()

    for result in results:
        if not isinstance(result, dict):
            continue

        query_id = str(result.get("id", ""))
        edges = result.get("edges", [])
        if not isinstance(edges, list):
            edges = []

        decorated_edges = []
        for original_index, edge in enumerate(edges):
            if not isinstance(edge, dict):
                continue

            matched_id = str(edge.get("id", ""))
            linked = records.get(matched_id)
            if linked is None:
                missing_ids.add(matched_id)
                linked = {"count": 0, "codes": [], "wcis_id": 410_000_001}

            priority_by_code = {
                normalize_systemcode(code): index
                for index, code in enumerate(hierarchy, start=1)
            }
            code_priorities = [
                priority_by_code[normalize_systemcode(code)]
                for code in linked["codes"]
                if normalize_systemcode(code) in priority_by_code
            ]
            best_priority = min(code_priorities, default=len(hierarchy) + 1)
            best_code = hierarchy[best_priority - 1] if best_priority <= len(hierarchy) else ""
            score = float(edge.get("score", 0.0) or 0.0)
            score_tier = 0 if score >= score_threshold else 1

            decorated_edges.append(
                {
                    "edge": dict(edge),
                    "score": score,
                    "score_tier": score_tier,
                    "systemcodes_count": linked["count"],
                    "systemcodes": linked["codes"],
                    "wcis_id": linked["wcis_id"],
                    "best_systemcode_priority": best_priority,
                    "best_systemcode": best_code,
                    "original_index": original_index,
                }
            )

        # High-score edges rank by Melder score first; exact score ties fall
        # back to SystemCode hierarchy, distinct in-hierarchy code count, and
        # numeric WCIS ID. Lower-score edges use those SystemCode rules first,
        # then score. Unlisted codes do not affect priority or count.
        decorated_edges.sort(
            key=lambda item: (
                item["score_tier"],
                -item["score"] if item["score_tier"] == 0 else 0.0,
                item["best_systemcode_priority"] if item["score_tier"] == 1 else 0,
                -item["systemcodes_count"] if item["score_tier"] == 1 else 0,
                item["wcis_id"] if item["score_tier"] == 1 else 0,
                item["best_systemcode_priority"] if item["score_tier"] == 0 else 0,
                -item["systemcodes_count"] if item["score_tier"] == 0 else 0,
                item["wcis_id"] if item["score_tier"] == 0 else 0,
                -item["score"] if item["score_tier"] == 1 else 0.0,
                item["original_index"],
            )
        )

        high_score_groups = {}
        for item in decorated_edges:
            if item["score_tier"] == 0:
                high_score_groups.setdefault(item["score"], []).append(item)

        for item in decorated_edges:
            if item["score_tier"] != 0:
                item["ranking_basis"] = "systemcodes"
                continue

            tied_items = high_score_groups[item["score"]]
            if len(tied_items) == 1:
                item["ranking_basis"] = "melder_score"
            elif len({tie["best_systemcode_priority"] for tie in tied_items}) > 1:
                item["ranking_basis"] = "systemcode_priority"
            elif len({tie["systemcodes_count"] for tie in tied_items}) > 1:
                item["ranking_basis"] = "systemcodes_count"
            elif len({tie["wcis_id"] for tie in tied_items}) > 1:
                item["ranking_basis"] = "wcis_id"
            else:
                item["ranking_basis"] = "original_order"

        ranked_edges = []
        for rank, item in enumerate(decorated_edges, start=1):
            edge = item["edge"]
            edge["systemcodes_count"] = item["systemcodes_count"]
            edge["systemcodes"] = item["systemcodes"]
            edge["wcis_id"] = item["wcis_id"]
            edge["best_systemcode_priority"] = (
                item["best_systemcode_priority"]
                if item["best_systemcode"]
                else None
            )
            edge["best_systemcode"] = item["best_systemcode"]
            edge["linkage_rank"] = rank
            edge["ranking_basis"] = item["ranking_basis"]
            ranked_edges.append(edge)

            csv_rows.append(
                {
                    "query_id": query_id,
                    "linkage_rank": rank,
                    "ranking_basis": item["ranking_basis"],
                    "matched_id": str(edge.get("id", "")),
                    "wcis_id": item["wcis_id"],
                    "best_systemcode_priority": item["best_systemcode_priority"]
                    if item["best_systemcode"]
                    else "",
                    "best_systemcode": item["best_systemcode"],
                    "matched_systemcodes_count": item["systemcodes_count"],
                    "matched_systemcodes": ", ".join(item["systemcodes"]),
                    "melder_score": edge.get("score", ""),
                    "enrolled": result.get("enrolled", ""),
                }
            )

        ranked_result = dict(result)
        ranked_result["edges"] = ranked_edges
        ranked_results.append(ranked_result)

    ranked_response = dict(response)
    ranked_response["results"] = ranked_results
    return ranked_response, csv_rows, missing_ids


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--response", type=Path, default=DEFAULT_RESPONSE)
    parser.add_argument("--records", type=Path, default=DEFAULT_RECORDS)
    parser.add_argument("--hierarchy", type=Path, default=DEFAULT_HIERARCHY)
    parser.add_argument("--out-csv", type=Path, default=DEFAULT_CSV_OUTPUT)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--id-field", default="entity_id")
    parser.add_argument("--systemcodes-field", default="systemcodes")
    parser.add_argument("--wcis-id-field", default="wcis_id")
    args = parser.parse_args()

    with args.response.open("r", encoding="utf-8-sig") as handle:
        response = json.load(handle)

    hierarchy, score_threshold, hierarchy_codes = read_hierarchy(args.hierarchy)
    records = read_records(
        args.records,
        args.id_field,
        args.systemcodes_field,
        args.wcis_id_field,
        hierarchy_codes,
    )
    ranked_response, csv_rows, missing_ids = rank_response(
        response, records, hierarchy, score_threshold
    )

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.parent.mkdir(parents=True, exist_ok=True)

    with args.out_json.open("w", encoding="utf-8") as handle:
        json.dump(ranked_response, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    columns = [
        "query_id",
        "linkage_rank",
        "ranking_basis",
        "matched_id",
        "wcis_id",
        "best_systemcode_priority",
        "best_systemcode",
        "matched_systemcodes_count",
        "matched_systemcodes",
        "melder_score",
        "enrolled",
    ]
    with args.out_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(csv_rows)

    result_count = len(ranked_response.get("results", []))
    print(f"Ranked edges for {result_count} enrolled records ({len(csv_rows)} edges)")
    print(f"Melder score cutoff: {score_threshold:.2f} (high-score tier first)")
    print(f"SystemCode priority order: {', '.join(hierarchy)}")
    print(f"CSV report:  {args.out_csv}")
    print(f"Ranked JSON: {args.out_json}")
    if missing_ids:
        print(
            "Warning: no record CSV row found for matched ID(s); "
            "those edges were assigned zero SystemCodes: "
            + ", ".join(sorted(missing_ids))
        )


if __name__ == "__main__":
    main()
