#!/usr/bin/env python3
import json
import sys


def load_queries(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"[parse_data] ERROR: invalid JSON in {path}: line {e.lineno}, column {e.colno}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"[parse_data] ERROR: cannot read {path}: {e}", file=sys.stderr)
        return 1

    queries = data.get("queries")
    if not isinstance(queries, list):
        print(f"[parse_data] ERROR: queries must be an array in {path}", file=sys.stderr)
        return 1

    for index, query in enumerate(queries):
        if not isinstance(query, dict):
            print(f"[parse_data] ERROR: queries[{index}] must be an object in {path}", file=sys.stderr)
            return 1

        file_name = query.get("file", "")
        lines = query.get("lines", [])
        covering_tasks = query.get("covering_tasks", [])
        total_tasks = query.get("total_tasks", "")

        if not isinstance(lines, list):
            print(f"[parse_data] ERROR: queries[{index}].lines must be an array in {path}", file=sys.stderr)
            return 1
        if not isinstance(covering_tasks, list):
            print(f"[parse_data] ERROR: queries[{index}].covering_tasks must be an array in {path}", file=sys.stderr)
            return 1

        lines_csv = ",".join(str(line) for line in lines)
        covering_tasks_json = json.dumps(covering_tasks, ensure_ascii=False, separators=(",", ":"))
        print("\t".join([str(index), str(file_name), lines_csv, str(total_tasks), covering_tasks_json]))

    return 0


def main():
    if len(sys.argv) != 2:
        print("usage: parse_data.py DATA_JSON_PATH", file=sys.stderr)
        return 2

    return load_queries(sys.argv[1])


if __name__ == "__main__":
    raise SystemExit(main())
