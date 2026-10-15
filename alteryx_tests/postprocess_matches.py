#!/usr/bin/env python3
"""Apply case-specific business rules to Melder relationship output."""

import argparse
import csv
import json
from pathlib import Path


def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"CSV has no data rows: {path}")
    return rows


def read_relationships(path):
    """Read Melder output, preserving duplicate ID headers positionally."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if not header or len(header) < 6:
            raise ValueError(f"Relationship CSV has an invalid header: {path}")
        rows = []
        for values in reader:
            if not values:
                continue
            padded = values + [""] * (len(header) - len(values))
            row = {name: padded[index] for index, name in enumerate(header[2:], start=2)}
            row["__b_id"] = padded[0]
            row["__a_id"] = padded[1]
            rows.append(row)
    if not rows:
        raise ValueError(f"CSV has no data rows: {path}")
    return rows


def value(record, field):
    return (record.get(field) or "").strip()


def normalized(record, field):
    return value(record, field).upper()


def code_set(record, field):
    return {
        part.strip().upper().replace(" ", "_")
        for part in value(record, field).split(",")
        if part.strip()
    }


def index_records(rows, id_field):
    result = {}
    for row in rows:
        record_id = value(row, id_field)
        if not record_id:
            raise ValueError(f"Missing {id_field} in input row")
        if record_id in result:
            raise ValueError(f"Duplicate {id_field}: {record_id}")
        result[record_id] = row
    return result


def validate_pair(a_record, b_record, rules):
    failures = []

    for field in rules.get("required_fields_both", []):
        if not value(a_record, field) or not value(b_record, field):
            failures.append(f"{field} must be populated on both records")

    legal = rules.get("legal_type", {})
    legal_field = legal.get("field", "LegalType")
    a_type = normalized(a_record, legal_field)
    b_type = normalized(b_record, legal_field)
    forbidden = {item.upper() for item in legal.get("forbidden", [])}
    allowed = {item.upper() for item in legal.get("allowed", [])}

    if a_type in forbidden or b_type in forbidden:
        failures.append(f"{legal_field} contains a forbidden value")
    if allowed and a_type not in allowed and b_type not in allowed:
        failures.append(
            f"at least one {legal_field} must be one of the configured allowed values"
        )

    if legal.get("require_one_nonblank_allowed"):
        if not ((a_type and a_type in allowed) or (b_type and b_type in allowed)):
            failures.append(f"at least one {legal_field} must be a nonblank allowed value")

    system = rules.get("system_codes", {})
    required_codes = {item.upper().replace(" ", "_") for item in system.get("required", [])}
    if required_codes:
        system_field = system.get("field", "SystemCodes")
        a_codes = code_set(a_record, system_field)
        b_codes = code_set(b_record, system_field)
        mode = system.get("mode", "either")
        if mode == "both":
            passed = required_codes <= a_codes and required_codes <= b_codes
        elif mode == "same":
            passed = bool(required_codes & a_codes & b_codes)
        elif mode == "either":
            passed = bool(required_codes & (a_codes | b_codes))
        else:
            raise ValueError(f"Unsupported system_codes.mode: {mode}")
        if not passed:
            failures.append(f"required SystemCodes missing ({mode})")

    return failures


def write_csv(path, rows, fieldnames):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def process(matches, a_records, b_records, rules, a_id_field, b_id_field):
    output = []

    for match in matches:
        # Melder writes B ID first and A ID second. The headers may be
        # identical, so these values are read positionally.
        b_id = match["__b_id"]
        a_id = match["__a_id"]
        a_record = a_records.get(a_id)
        b_record = b_records.get(b_id)
        failures = []

        if a_record is None:
            failures.append(f"A record not found: {a_id}")
        if b_record is None:
            failures.append(f"B record not found: {b_id}")

        if not failures:
            failures.extend(validate_pair(a_record, b_record, rules))

        decision = "rejected" if failures else "accepted"

        row = {key: item for key, item in match.items() if not key.startswith("__")}
        row.update(
            {
                "a_id": a_id,
                "b_id": b_id,
                "decision": decision,
                "rule_reasons": "; ".join(failures),
            }
        )
        output.append(row)

    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", required=True, type=Path)
    parser.add_argument("--matches", type=Path)
    parser.add_argument("--a", type=Path, dest="a_path")
    parser.add_argument("--b", type=Path, dest="b_path")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--a-id-field")
    parser.add_argument("--b-id-field")
    args = parser.parse_args()

    with args.rules.open("r", encoding="utf-8") as handle:
        rules = json.load(handle)

    paths = rules.get("paths", {})
    matches_path = args.matches or Path(paths.get("matches", ""))
    a_path = args.a_path or Path(paths.get("a", ""))
    b_path = args.b_path or Path(paths.get("b", ""))
    out_dir = args.out_dir or Path(paths.get("out_dir", ""))
    a_id_field = args.a_id_field or paths.get("a_id_field", "clientId")
    b_id_field = args.b_id_field or paths.get("b_id_field", "clientId")

    missing = [
        name
        for name, path in (
            ("matches", matches_path),
            ("a", a_path),
            ("b", b_path),
            ("out_dir", out_dir),
        )
        if not str(path)
    ]
    if missing:
        parser.error(
            "missing paths: " + ", ".join(missing) +
            "; provide them in the rules JSON or on the command line"
        )

    matches = read_relationships(matches_path)
    a_records = index_records(read_csv(a_path), a_id_field)
    b_records = index_records(read_csv(b_path), b_id_field)
    results = process(matches, a_records, b_records, rules, a_id_field, b_id_field)

    out_dir.mkdir(parents=True, exist_ok=True)
    fields = [key for key in matches[0] if not key.startswith("__")]
    fields += ["a_id", "b_id", "decision", "rule_reasons"]
    write_csv(out_dir / "postprocessed_matches.csv", results, fields)
    write_csv(out_dir / "accepted_matches.csv", [r for r in results if r["decision"] == "accepted"], fields)
    write_csv(out_dir / "review_matches.csv", [r for r in results if r["decision"] == "review"], fields)
    write_csv(out_dir / "rejected_matches.csv", [r for r in results if r["decision"] == "rejected"], fields)

    counts = {
        decision: sum(row["decision"] == decision for row in results)
        for decision in ("accepted", "rejected")
    }
    print(f"Processed {len(results)} Melder relationships")
    print(f"Accepted: {counts['accepted']}")
    print(f"Rejected: {counts['rejected']}")


if __name__ == "__main__":
    main()
