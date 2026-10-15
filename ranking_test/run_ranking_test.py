#!/usr/bin/env python3
"""Submit the ranking fixture to a running Melder enroll server, then rank it."""

import csv
import argparse
import json
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent
RECORDS_CSV = BASE_DIR / "ranking_test_data.csv"
RESPONSE_JSON = BASE_DIR / "ranking_test_enroll_response.json"
RANKED_JSON = BASE_DIR / "ranking_test_ranked_response.json"
RANKED_CSV = BASE_DIR / "ranking_test_ranked_matches.csv"
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8090")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    with RECORDS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        records = list(csv.DictReader(handle))

    if len(records) != 25:
        raise ValueError(f"Expected 25 fixture records, found {len(records)}")

    with urlopen(f"{base_url}/api/v1/health", timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"Melder health check failed: HTTP {response.status}")

    payload = json.dumps({"records": records}).encode("utf-8")
    request = Request(
        f"{base_url}/api/v1/enroll-batch",
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=300) as response:
        result = json.loads(response.read().decode("utf-8"))

    with RESPONSE_JSON.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    command = [
        sys.executable,
        str(BASE_DIR / "rank_enroll_edges.py"),
        "--response", str(RESPONSE_JSON),
        "--records", str(RECORDS_CSV),
        "--hierarchy", str(BASE_DIR / "systemcode_hierarchy.json"),
        "--out-json", str(RANKED_JSON),
        "--out-csv", str(RANKED_CSV),
    ]
    subprocess.run(command, check=True)
    print(f"Raw enroll response: {RESPONSE_JSON}")
    print(f"Ranked result JSON:  {RANKED_JSON}")
    print(f"Ranked result CSV:   {RANKED_CSV}")


if __name__ == "__main__":
    main()
