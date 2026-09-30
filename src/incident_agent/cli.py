import argparse
import json
from pathlib import Path

from .orchestrator import IncidentOrchestrator, WorkflowStopped


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the synthetic incident-response workflow.")
    parser.add_argument("--incident", default="inc-042")
    parser.add_argument("--service", default="checkout-api")
    parser.add_argument("--output", type=Path, default=Path("artifacts/incident-report.json"))
    parser.add_argument("--state", type=Path, default=Path("artifacts/runs.sqlite"))
    parser.add_argument("--step", action="store_true", help="Checkpoint one stage and exit")
    args = parser.parse_args()

    agent = IncidentOrchestrator(state_path=args.state)
    if args.step:
        state = agent.advance(args.incident, args.service)
        print(
            json.dumps(
                {"incident_id": state.incident_id, "stage": state.stage, "reason": state.reason}
            )
        )
        if state.stage == "escalated":
            parser.exit(2)
        return
    try:
        report = agent.investigate(args.incident, args.service)
    except WorkflowStopped as error:
        parser.exit(2, f"Investigation escalated: {error}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report.to_dict(), indent=2) + "\n")
    print(json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    main()
