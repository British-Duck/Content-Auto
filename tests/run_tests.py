"""
Post-build quality runner.

Usage:
    python tests/run_tests.py <video.mp4> [spec.json] [--preview] [--report-dir DIR] [--baseline-dir DIR]

Exit 0 = all checks passed.
Exit 1 = one or more checks failed (details + fix suggestions printed to stdout).

Intended to be called after every pipeline build:
    python -m vidpipe spec.json -o output.mp4
    python tests/run_tests.py output.mp4 spec.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Allow running as a script even when not installed as a package
sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.quality import VideoQualityValidator


def main():
    parser = argparse.ArgumentParser(
        description="Validate video pipeline output quality",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("video", help="Path to output MP4 file")
    parser.add_argument("spec", nargs="?", help="Path to spec JSON (enables frame-level checks)")
    parser.add_argument("--preview", action="store_true",
                        help="Video was built with --preview (uses 540x960 thresholds)")
    parser.add_argument("--crossfade", type=float, default=0.0,
                        help="Crossfade duration used during build (default: 0)")
    parser.add_argument("--no-voice", action="store_true",
                        help="Voice was disabled during build (--no-voice flag was used)")
    parser.add_argument("--report-dir", default="tests/reports",
                        help="Directory to write JSON report (default: tests/reports)")
    parser.add_argument("--baseline-dir", default="tests/baselines",
                        help="Directory for SSIM reference frames (default: tests/baselines)")
    parser.add_argument("--no-report", action="store_true",
                        help="Skip writing a JSON report file")
    args = parser.parse_args()

    spec = None
    if args.spec:
        try:
            spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"Error reading spec: {exc}", file=sys.stderr)
            sys.exit(1)

    voice_enabled = None if not args.no_voice else False
    validator = VideoQualityValidator(
        args.video,
        spec=spec,
        preview=args.preview,
        baseline_dir=args.baseline_dir,
        crossfade=args.crossfade,
        voice_enabled=voice_enabled,
    )
    report = validator.validate()
    report.print_summary()

    if not args.no_report:
        report_dir = Path(args.report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = report_dir / f"report_{ts}.json"
        report_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        print(f"Report written: {report_path}")

    if not report.passed:
        n = len(report.failed_checks)
        print(f"\n{n} check(s) failed. See fix suggestions above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
