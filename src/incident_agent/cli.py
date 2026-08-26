import argparse
import json
from pathlib import Path

from .orchestrator import IncidentOrchestrator


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the synthetic incident-response workflow.")
    parser.add_argument("--incident", default="inc-042")
    parser.add_argument("--service", default="checkout-api")
    parser.add_argument("--output", type=Path, default=Path("artifacts/incident-report.json"))
    args = parser.parse_args()

    report = IncidentOrchestrator().investigate(args.incident, args.service)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report.to_dict(), indent=2) + "\n")
    print(json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    main()
