"""
Orchestrator — wires the full pipeline:
  Creative Director → Editorial Critic (Stage 1.5) → Researcher → Render
  → Quality Gate → Output-QA.

  python orchestrate.py "brief text"         # from scratch
  python orchestrate.py spec.json            # from existing spec
  python orchestrate.py "brief" --preview --no-voice --skip-research
  python orchestrate.py "brief" --dry-run    # preview cost without API calls
  python orchestrate.py "brief" --force-regen # ignore cached spec for this brief
"""
import argparse
import json
import logging
import shutil
import sys
import time
from pathlib import Path

import config
from cost_tracker import get_tracker, reset_tracker

log = logging.getLogger("orchestrate")

# Nominal thinking-token estimate for uncapped (None) budgets, used only for the
# dry-run cost projection. Uncapped budgets are model-decided so the real usage
# varies; this gives a ballpark instead of crashing or under-reporting as zero.
_UNCAPPED_THINKING_EST = 2048


def _est_thinking_tokens(budget) -> int:
    """Thinking tokens to assume for cost projection.

    None = uncapped (model decides) → nominal estimate.
    0    = disabled → no thinking tokens.
    int  = the configured cap.
    """
    if budget is None:
        return _UNCAPPED_THINKING_EST
    return budget


def _generate_timeline(spec: dict) -> str:
    """Build a human-readable timeline of the video spec."""
    screens = spec.get("screens", [])
    total = sum(s.get("duration", 0) for s in screens)
    ambient_count = sum(1 for s in screens if s.get("clip_mode") == "ambient")
    clip_count = sum(1 for s in screens if s.get("clip") and isinstance(s["clip"], dict) and s["clip"].get("url"))

    w = 72
    lines = [
        "=" * w,
        f"  {spec.get('id', '?')}  |  {spec.get('scheme', '?')}  |  {total:.1f}s  |  {clip_count} clips  |  {ambient_count} ambient",
        "=" * w,
        f"  {'#':>2}  {'DUR':>4}  {'MODE':<16}  {'SOURCE':<28}  TEXT",
        "-" * w,
    ]

    prev_gradient = False
    for i, s in enumerate(screens):
        clip = s.get("clip")
        has_clip = bool(clip and isinstance(clip, dict) and clip.get("url"))
        mode_str = s.get("clip_mode") or ""

        if has_clip:
            url = clip["url"]
            vid_id = url.split("v=")[-1][:11] if "v=" in url else url[-11:]
            start = clip.get("start", 0)
            end = clip.get("end")
            ts = f"{vid_id}@{start:.1f}s" + ("–ambient" if end is None else f"–{end:.1f}s")
            mode_label = "AMBIENT" if mode_str == "ambient" else "CLIP"
            warn = ""
        else:
            ts = ""
            mode_label = "GRADIENT"
            warn = " ⚠ consecutive" if prev_gradient else ""

        text = (s.get("text") or "")[:28]
        sub = (s.get("subtitle") or "")[:20]
        display = f'"{text}"' + (f' / "{sub}"' if sub else "")

        lines.append(f"  {i+1:>2}  {s.get('duration', 0):>4.1f}  {mode_label:<16}  {ts:<28}  {display}{warn}")
        prev_gradient = not has_clip

    lines.append("=" * w)
    return "\n".join(lines)


def _cleanup_intermediates(spec_id: str = ""):
    """
    Delete render artifacts and re-downloadable caches after each run.

    Permanently kept (costs money or time to rebuild):
      cache/briefs/          — creative director specs (saves 2 Gemini text calls)
      cache/timestamps/      — Gemini video analysis results (~$0.005 each)
      cache/clip_knowledge/  — cross-run clip library with thumbnails

    Wiped (cheap to regenerate):
      .clip_cache/           — downloaded videos (re-downloadable)
      .trim_cache/           — trimmed clips (fast from cached source)
      cache/searches/        — YouTube search results (free yt-dlp calls)
      cache/metadata/        — video metadata (free yt-dlp calls)
      .vidpipe_{spec_id}/    — THIS run's render working directory only
    """
    try:
        dirs_to_wipe = [
            config.CACHE_DIR,
            config.RESEARCHER_TRIM_DIR,
            config.SEARCH_CACHE_DIR,
            config.METADATA_CACHE_DIR,
        ]
        for d in dirs_to_wipe:
            try:
                if d.exists():
                    shutil.rmtree(d)
                    d.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                log.warning("Cleanup: could not wipe %s — %s", d.name, e)

        # Only delete THIS run's vidpipe dir to avoid clashing with parallel runs
        if spec_id:
            vidpipe_dir = config.ROOT / f".vidpipe_{spec_id}"
            if vidpipe_dir.is_dir():
                try:
                    shutil.rmtree(vidpipe_dir)
                except OSError as e:
                    log.warning("Cleanup: could not remove %s — %s", vidpipe_dir.name, e)

        log.info("Cleanup: removed .clip_cache, .trim_cache, searches, metadata, render workdirs "
                 "(timestamps + clip_knowledge + briefs preserved)")
    except Exception as e:
        log.warning("Cleanup encountered an error (non-fatal): %s", e)


def _validate_spec(spec: dict) -> list[str]:
    errors = []
    if not isinstance(spec, dict):
        return ["spec is not a dict"]
    for key in ("id", "label", "scheme", "screens"):
        if key not in spec:
            errors.append(f"missing required field: {key}")
    if errors:
        return errors
    screens = spec.get("screens", [])
    if not isinstance(screens, list) or len(screens) == 0:
        errors.append("no screens defined")
        return errors
    valid_schemes = {"dark_gold", "dark_red", "dark_blue", "dark_green", "dark_purple"}
    if spec.get("scheme") not in valid_schemes:
        errors.append(f"invalid scheme: {spec.get('scheme')}")
    total_dur = 0
    for i, s in enumerate(screens):
        if "text" not in s:
            errors.append(f"screen {i}: missing 'text'")
        if "duration" not in s:
            errors.append(f"screen {i}: missing 'duration'")
        else:
            total_dur += s["duration"]
    if total_dur > 0 and not 3 <= total_dur <= 120:
        errors.append(f"total duration {total_dur:.1f}s seems unreasonable")
    return errors


def _spec_review_loop(spec: dict, brief: str, max_retries: int = None):
    """Editorial review of the spec before the expensive research/render stages.

    The editorial critic (Step 5) judges the PLAN — hook, arc, rhythm, text, voice,
    segment reasons. On decision == REVISE its fixes are fed back to the creative
    director to regenerate, capped at `max_retries` regenerations (default 2, i.e.
    3 CD calls total). PUBLISH proceeds; REVISE regenerates; REJECT stops and flags.

    Facts are NOT a reject — the critic reports them in facts_to_check, which we
    aggregate per attempt and surface to a human (logged, never blocking). The old
    factual_accuracy hard-reject was removed in Step 5.

    Returns (best_spec, best_review, attempts, regenerations, blocking) where
    `regenerations` is the number of regenerations performed and `blocking` is None
    when it is safe to proceed, or a dict describing why the run must stop and be
    flagged (a still-failing spec must never be rendered or looped indefinitely).
    Each attempt dict carries its decision, biggest_weakness and facts_to_check.
    """
    from editorial_critic import review_spec
    from creative_director import revise_spec
    from run_record import get_cost_meter

    if max_retries is None:
        max_retries = config.SPEC_REVIEW_MAX_RETRIES

    meter = get_cost_meter()
    best_spec, best_review = spec, None
    cur = spec
    attempts = []
    regenerations = 0
    blocking = None
    for attempt in range(max_retries + 1):
        with meter.stage("editorial_critic"):
            review = review_spec(cur)
        if not review:
            log.warning("Editorial critic returned nothing (pass %d) — proceeding with best",
                        attempt + 1)
            break
        decision = review.get("decision", "UNKNOWN")
        weakness = review.get("biggest_weakness", "")
        facts = review.get("facts_to_check") or []
        attempts.append({"pass": attempt + 1, "decision": decision,
                         "biggest_weakness": weakness, "facts_to_check": facts})
        log.info("Editorial pass %d/%d: %s — %s",
                 attempt + 1, max_retries + 1, decision, weakness)
        # Keep the latest non-rejected spec as the best; fall back to the first seen.
        if best_review is None or decision in ("PUBLISH", "REVISE"):
            best_spec, best_review = cur, review

        if decision == "REJECT":
            log.warning("Editorial critic REJECT — the angle/story does not work; "
                        "FLAGGING FOR HUMAN, not rendering")
            blocking = {
                "reason": f"editorial REJECT: {weakness}",
                "decision": decision,
                "regenerations": regenerations,
            }
            break

        if decision != "REVISE":
            # PUBLISH (or an unknown non-REVISE verdict) — accept and proceed.
            break

        # decision == REVISE
        if attempt == max_retries:
            # Regeneration cap reached and the spec STILL wants a revise — flag, do not render.
            log.warning("Editorial REVISE persists after %d regeneration(s) — regen cap reached; "
                        "FLAGGING FOR HUMAN, not rendering", regenerations)
            blocking = {
                "reason": f"editorial REVISE after {regenerations} regeneration(s) (cap): {weakness}",
                "decision": decision,
                "regenerations": regenerations,
            }
            break
        log.info("Editorial REVISE → regeneration %d/%d via Creative Director",
                 regenerations + 1, max_retries)
        with meter.stage("creative_director"):
            revised = revise_spec(brief, cur, review)
        if not revised:
            # Could not regenerate while a REVISE stands — do not ship the draft.
            log.warning("Regeneration call failed while an editorial REVISE stands — "
                        "FLAGGING FOR HUMAN, not rendering")
            blocking = {
                "reason": "regeneration call failed while an editorial REVISE stands",
                "decision": decision,
                "regenerations": regenerations,
            }
            break
        cur = revised
        regenerations += 1
    return best_spec, best_review, attempts, regenerations, blocking


def dry_run(input_arg: str, skip_research: bool = False):
    input_path = Path(input_arg)
    spec = None

    print("=" * 55)
    print("DRY RUN - no API calls will be made")
    print("=" * 55)
    print()

    if input_path.suffix == ".json" and input_path.exists():
        spec = json.loads(input_path.read_text(encoding="utf-8"))
        print(f"Source: spec file ({input_path})")
        cd_calls = 0
    else:
        print(f"Source: text brief")
        print(f"  \"{input_arg[:100]}{'...' if len(input_arg) > 100 else ''}\"")
        from creative_director import _brief_hash
        cache_file = config.BRIEF_CACHE_DIR / f"{_brief_hash(input_arg)}.json"
        if cache_file.exists():
            spec = json.loads(cache_file.read_text(encoding="utf-8"))
            print(f"  Cached spec found: {spec.get('id', '?')} (0 API calls)")
            cd_calls = 0
        else:
            print("  No cached spec -- Creative Director will generate")
            cd_calls = 1   # v2 is single-pass (the v1 self-critique call is retired)

    print()
    print("-" * 55)
    print("STAGE BREAKDOWN")
    print("-" * 55)
    print(f"  Creative Director:  {cd_calls} text call(s)")

    researcher_min = 0
    researcher_max = 0
    if spec and not skip_research:
        clip_screens = [s for s in spec.get("screens", []) if s.get("clip_description")]
        needs_research = [s for s in clip_screens
                          if not (s.get("clip") and isinstance(s.get("clip"), dict)
                                  and s["clip"].get("url"))]
        print(f"  Researcher:         {len(needs_research)} screen(s) need clips")
        print(f"    Best case (metadata hits): 0 video calls")
        researcher_max = len(needs_research) * 2
        print(f"    Worst case:        {researcher_max} video call(s)")
    elif spec and skip_research:
        print("  Researcher:         skipped")
    else:
        print("  Researcher:         unknown (spec not generated yet)")
        print("    Typical:           2-6 video calls")
        researcher_max = 6
    print("  Editorial Critic:   1 text call (pre-render; +1 per regeneration)")
    print("  Render:             local only (ffmpeg)")
    print("  Quality Gate:       local only (ffprobe)")
    print("  Output-QA:          1 vision call (post-render, frames)")

    total_text = cd_calls + 2   # editorial critic + output-QA (both paid Gemini calls)
    total_video_min = researcher_min
    total_video_max = researcher_max

    text_cost = total_text * (5000 * 0.15 + 500 * 0.60) / 1_000_000
    cd_thinking = cd_calls * (_est_thinking_tokens(config.THINKING_BUDGET_CREATIVE) * 3.50) / 1_000_000
    # Two reviewers now: editorial critic (pre-render) + Output-QA (post-render).
    review_thinking = 2 * (_est_thinking_tokens(config.THINKING_BUDGET_REVIEWER) * 3.50) / 1_000_000
    thinking_cost = cd_thinking + review_thinking
    uncapped = config.THINKING_BUDGET_CREATIVE is None or config.THINKING_BUDGET_REVIEWER is None

    video_cost_min = total_video_min * (60 * 263 * 0.15 + 50 * 0.60) / 1_000_000
    video_cost_max = total_video_max * (60 * 263 * 0.15 + 50 * 0.60) / 1_000_000

    total_min = text_cost + thinking_cost + video_cost_min
    total_max = text_cost + thinking_cost + video_cost_max

    print()
    print("-" * 55)
    print("ESTIMATED COST")
    print("-" * 55)
    print(f"  Text calls:         ${text_cost:.4f}")
    print(f"  Thinking tokens:    ${thinking_cost:.4f}" +
          (f"  (uncapped stages estimated at {_UNCAPPED_THINKING_EST} tokens)" if uncapped else ""))
    print(f"  Video calls:        ${video_cost_min:.4f} - ${video_cost_max:.4f}")
    print(f"  ----------------------------")
    print(f"  Total:              ${total_min:.4f} - ${total_max:.4f}")
    print()
    print("Thinking budgets: creative={}, critique={}, timestamps={}, reviewer={}".format(
        config.THINKING_BUDGET_CREATIVE, config.THINKING_BUDGET_CRITIQUE,
        config.THINKING_BUDGET_TIMESTAMPS, config.THINKING_BUDGET_REVIEWER))
    print("(See cost_tuning_guide.txt to adjust these)")
    print()


def run(input_arg: str, *, preview=False, no_voice=False,
        skip_research=False, force_regen=False) -> dict:
    from run_record import RunRecord, reset_cost_meter, set_active_record

    tracker = reset_tracker()
    meter = reset_cost_meter()
    summary = {"stages": {}, "output": None, "quality": None, "output_qa": None, "success": False}
    t0 = time.time()

    input_path = Path(input_arg)
    # brief is set when generating from a text brief (enables the v2 gate + review loop).
    brief = None if (input_path.suffix == ".json" and input_path.exists()) else input_arg

    # The run record is created up front and ALWAYS written (completion or abort)
    # via the finally below. Thin hooks in each stage write into it.
    record = RunRecord(brief=brief, input_arg=input_arg, meter=meter)
    set_active_record(record)

    def _ceiling_abort(stage_label: str) -> bool:
        """After a paid stage: abort if cumulative cost crossed the ceiling."""
        if record.enforce_ceiling():
            log.error("COST CEILING EXCEEDED after %s: $%.4f > $%.4f — aborting before render",
                      stage_label, record.data["total_cost"], record.data["cost_ceiling"])
            summary["stages"]["cost_ceiling"] = (
                f"ABORTED after {stage_label}: ${record.data['total_cost']:.4f} "
                f"> ${record.data['cost_ceiling']:.4f}"
            )
            record.set_final_status("aborted_cost_ceiling")
            return True
        return False

    try:
        # Stage 1: Creative Director
        if brief is None:
            log.info("Loading spec: %s", input_path)
            spec = json.loads(input_path.read_text(encoding="utf-8"))
            summary["stages"]["creative_director"] = "skipped (spec provided)"
            record.stage("creative_director", "skipped", note="spec provided")
        else:
            log.info("=" * 60)
            log.info("STAGE 1: Creative Director")
            log.info("=" * 60)
            try:
                from creative_director import generate_spec
                with meter.stage("creative_director"):
                    spec = generate_spec(input_arg, force=force_regen)
            except Exception as e:
                log.error("Stage 1 failed: %s", e)
                summary["stages"]["creative_director"] = f"FAILED: {e}"
                record.stage("creative_director", "FAILED", error=str(e))
                record.set_final_status("aborted_creative_director")
                return summary
            if not spec:
                summary["stages"]["creative_director"] = "FAILED: no spec"
                record.stage("creative_director", "FAILED", error="no spec")
                record.set_final_status("aborted_creative_director")
                return summary
            summary["stages"]["creative_director"] = f"OK — {spec['id']}"
            record.stage("creative_director", "OK", spec_id=spec.get("id"))

        record.bind_spec(spec)

        # Fail-fast: legacy basic validation (runs for both paths)
        spec_errors = _validate_spec(spec)
        if spec_errors:
            log.error("Spec validation failed before pipeline:")
            for err in spec_errors:
                log.error("  -> %s", err)
            summary["stages"]["validation"] = f"FAILED: {'; '.join(spec_errors)}"
            record.stage("validation", "FAILED", basic_errors=spec_errors)
            record.set_final_status("aborted_validation")
            return summary

        # Stage 1 gate (v2 contract): hard-reject out-of-contract specs and auto-repair
        # the safe-to-fix ones before any expensive stage. Runs only on CD-generated
        # specs — a provided .json spec is taken as final (same convention as Stage 1.5).
        if brief is not None:
            from spec_validator import validate_spec, SpecValidationError
            log.info("=" * 60)
            log.info("STAGE 1 GATE: Spec Validation (v2 contract)")
            log.info("=" * 60)
            try:
                spec, _repairs = validate_spec(spec)
                summary["spec_validation"] = {"repairs": _repairs}
                summary["stages"]["spec_validation"] = (
                    "OK" if not _repairs else f"OK — {len(_repairs)} auto-repair(s)"
                )
                record.stage("validation", "OK", repairs=_repairs, violations=[])
            except SpecValidationError as e:
                log.error("Spec validation REJECTED — spec will NOT reach research/render:")
                for v in e.violations:
                    log.error("  [X] %s", v)
                summary["spec_validation"] = {"rejected": e.violations}
                summary["stages"]["spec_validation"] = f"REJECTED: {len(e.violations)} violation(s)"
                record.stage("validation", "REJECTED", violations=e.violations, repairs=[])
                record.add_flag(f"spec validation rejected: {len(e.violations)} hard violation(s)")
                record.set_final_status("rejected_validation")
                return summary
        else:
            record.stage("validation", "OK", note="provided spec — basic checks only")

        if _ceiling_abort("creative_director"):
            return summary

        # Stage 1.5: Spec review + revision loop (text-only, before expensive render).
        # Only when generated from a brief — a provided .json spec is taken as final.
        if brief is not None:
            log.info("=" * 60)
            log.info("STAGE 1.5: Spec Review & Revision Loop")
            log.info("=" * 60)
            try:
                spec, _loop_review, _attempts, _regens, _blocking = _spec_review_loop(spec, brief)
                record.data["regenerations"] = _regens

                # Aggregate facts_to_check across all passes — surfaced to a human,
                # never blocking (facts are the human's job, per the critic's brief).
                _facts = []
                for a in _attempts:
                    for f in a.get("facts_to_check") or []:
                        if f not in _facts:
                            _facts.append(f)

                summary["spec_review"] = {
                    "passes": _attempts,
                    "regenerations": _regens,
                    "final_decision": (_loop_review or {}).get("decision"),
                    "biggest_weakness": (_loop_review or {}).get("biggest_weakness"),
                    "facts_to_check": _facts,
                }

                # Populate the editorial_critic run-record block (a Step-2 slot).
                if _loop_review is not None:
                    record.stage(
                        "editorial_critic", _loop_review.get("decision", "UNKNOWN"),
                        biggest_weakness=_loop_review.get("biggest_weakness"),
                        scores=_loop_review.get("scores"),
                        fixes=_loop_review.get("fixes"),
                        facts_to_check=_facts,
                        passes=_attempts,
                        regenerations=_regens,
                    )
                    summary["stages"]["spec_review_loop"] = (
                        f"{_loop_review.get('decision')} after {len(_attempts)} pass(es), "
                        f"{_regens} regen(s)"
                    )
                else:
                    record.stage("editorial_critic", "no_verdict",
                                 passes=_attempts, regenerations=_regens)
                    summary["stages"]["spec_review_loop"] = "no verdict"

                # Facts to verify are logged for a human but never block publishing.
                if _facts:
                    record.add_flag("facts to verify (non-blocking): " + " | ".join(_facts))

                # REJECT, regen cap, or a failed regeneration with the spec still
                # wanting a REVISE: flag for a human and abort BEFORE render.
                if _blocking is not None:
                    log.error("Stage 1.5 BLOCKED after %d regeneration(s): %s",
                              _regens, _blocking["reason"])
                    record.add_flag(
                        f"editorial gate: {_blocking['reason']} — "
                        f"flagged for human, not rendered "
                        f"(decision={_blocking['decision']}, regenerations={_regens})"
                    )
                    _status = ("flagged_editorial_reject"
                               if _blocking["decision"] == "REJECT"
                               else "flagged_regen_cap")
                    record.set_final_status(_status)
                    summary["stages"]["spec_review_loop"] = (
                        f"FLAGGED ({_blocking['decision']}) after {_regens} regen(s), not rendered"
                    )
                    return summary
            except Exception as e:
                log.error("Stage 1.5 failed: %s (continuing with current spec)", e)
                summary["stages"]["spec_review_loop"] = f"FAILED: {e}"

            # Stage 1.5 is a paid stage — compose with the cost ceiling.
            if _ceiling_abort("spec_review_loop"):
                return summary

        # Stage 2: Researcher
        if skip_research:
            summary["stages"]["researcher"] = "skipped"
            record.stage("research", "skipped")
        else:
            log.info("=" * 60)
            log.info("STAGE 2: Researcher")
            log.info("=" * 60)
            try:
                from researcher import research_spec
                with meter.stage("research"):
                    spec = research_spec(spec, selective=True)
                clips = sum(1 for s in spec.get("screens", []) if s.get("clip"))
                clip_screens = sum(1 for s in spec.get("screens", []) if s.get("clip_description"))
                summary["stages"]["researcher"] = f"OK — {clips} clips"
                record.stage("research", "OK", clips_found=clips, clip_screens=clip_screens,
                             fallbacks=len(record.data["fallback_reasons"]))
            except Exception as e:
                log.error("Stage 2 failed: %s (continuing)", e)
                summary["stages"]["researcher"] = f"FAILED: {e}"
                record.stage("research", "FAILED", error=str(e))

        if _ceiling_abort("research"):
            return summary

        # Save spec and print/save timeline
        spec_path = config.SPECS_DIR / f"{spec.get('id', 'unknown')}.json"
        spec_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")

        timeline = _generate_timeline(spec)
        log.info("\n%s", timeline)
        timeline_path = config.SPECS_DIR / f"{spec.get('id', 'unknown')}_timeline.txt"
        timeline_path.write_text(timeline, encoding="utf-8")

        # Stage 3: Render
        log.info("=" * 60)
        log.info("STAGE 3: Video Pipeline")
        log.info("=" * 60)
        output_path = config.OUTPUT_DIR / f"{spec.get('id', 'output')}.mp4"
        cli_args = [str(spec_path), "-o", str(output_path)]
        if preview:
            cli_args.append("--preview")
        if no_voice:
            cli_args.append("--no-voice")

        try:
            from vidpipe.cli import main as vidpipe_main
            rc = vidpipe_main(cli_args)
            if rc != 0:
                summary["stages"]["render"] = f"FAILED: exit {rc}"
                record.stage("render", "FAILED", exit_code=rc)
                record.set_final_status("aborted_render")
                return summary
            summary["stages"]["render"] = f"OK — {output_path.name}"
            summary["output"] = str(output_path)
            record.stage("render", "OK", output=str(output_path))
        except Exception as e:
            log.error("Stage 3 failed: %s", e)
            summary["stages"]["render"] = f"FAILED: {e}"
            record.stage("render", "FAILED", error=str(e))
            record.set_final_status("aborted_render")
            return summary

        # Stage 4: Quality Gate
        log.info("=" * 60)
        log.info("STAGE 4: Quality Gate")
        log.info("=" * 60)
        try:
            from quality_gate import check_video
            qr = check_video(output_path, spec, preview=preview)
            summary["quality"] = qr
            passed = sum(1 for c in qr["checks"].values() if c["passed"])
            summary["stages"]["quality_gate"] = (
                f"{'PASSED' if qr['passed'] else 'FAILED'} — {passed}/{len(qr['checks'])}"
            )
            record.stage("render", "OK", quality_passed=qr["passed"],
                         quality_checks=f"{passed}/{len(qr['checks'])}")
        except Exception as e:
            log.error("Stage 4 failed: %s", e)
            summary["stages"]["quality_gate"] = f"FAILED: {e}"

        # Stage 5: Output-QA (post-render, vision). Confirms the FINISHED video
        # matches its plan from the per-segment frames the gate just extracted.
        # Flag-and-stop: a FAIL stops the publish and surfaces blocking_issues; it
        # never re-renders or auto-fixes.
        log.info("=" * 60)
        log.info("STAGE 5: Output-QA")
        log.info("=" * 60)
        try:
            from output_qa import qa_output
            frames_dir = (summary.get("quality") or {}).get("frames_dir")
            probe_report = summary.get("quality") or {}
            with meter.stage("output_qa"):
                qa = qa_output(spec, probe_report, frames_dir)
            summary["output_qa"] = qa
            if qa:
                qa_decision = qa.get("decision", "UNKNOWN")
                blocking_issues = qa.get("blocking_issues") or []
                summary["stages"]["output_qa"] = (
                    f"{qa_decision} — {len(blocking_issues)} blocking issue(s)"
                )
                record.stage("output_qa", qa_decision,
                             blocking_issues=blocking_issues,
                             warnings=qa.get("warnings"),
                             per_segment=qa.get("per_segment"),
                             summary=qa.get("summary"))
                if qa_decision == "FAIL":
                    for issue in blocking_issues:
                        record.add_flag(f"output-QA blocking: {issue}")
            else:
                summary["stages"]["output_qa"] = "FAILED: no response"
                record.stage("output_qa", "FAILED", error="no response / no frames")
        except Exception as e:
            log.error("Stage 5 failed: %s", e)
            summary["stages"]["output_qa"] = f"FAILED: {e}"
            record.stage("output_qa", "FAILED", error=str(e))

        elapsed = time.time() - t0
        # None-safe: a stage that failed leaves its summary value as None (key present),
        # so `.get(key, {})` returns None — guard with `or {}`.
        quality_ok = (summary.get("quality") or {}).get("passed", False)
        qa_ok = (summary.get("output_qa") or {}).get("decision") == "PASS"
        summary["success"] = quality_ok and qa_ok
        summary["elapsed"] = round(elapsed, 1)
        record.set_final_status("publish" if summary["success"] else "needs_human_review")
        if not summary["success"]:
            if not qa_ok:
                record.add_flag("Output-QA did not PASS — needs human review, not published")
            elif not quality_ok:
                record.add_flag("did not clear technical quality gate — needs human review")

        log.info("=" * 60)
        log.info("DONE — %.1fs", elapsed)
        for stage, result in summary["stages"].items():
            log.info("  %s: %s", stage, result)
        log.info("=" * 60)

        tracker.save()
        tracker.print_summary()
        summary["cost"] = tracker.summary()

        _cleanup_intermediates(spec_id=spec.get("id", ""))

        return summary
    finally:
        try:
            path = record.write()
            summary["run_record"] = str(path)
        finally:
            set_active_record(None)


def main():
    p = argparse.ArgumentParser(prog="orchestrate",
                                description="Short-form video content pipeline")
    p.add_argument("input", help="Text brief (quoted) or .json spec path")
    p.add_argument("--preview", action="store_true", help="Half-res fast render")
    p.add_argument("--no-voice", action="store_true", help="Skip voiceover")
    p.add_argument("--skip-research", action="store_true", help="Skip footage search")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview API calls and cost without running anything")
    p.add_argument("--force-regen", action="store_true",
                   help="Ignore cached spec and regenerate from brief")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.dry_run:
        dry_run(args.input, skip_research=args.skip_research)
        sys.exit(0)

    summary = run(args.input, preview=args.preview, no_voice=args.no_voice,
                  skip_research=args.skip_research, force_regen=args.force_regen)

    report = config.LOGS_DIR / "pipeline_report.json"
    report.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    sys.exit(0 if summary["success"] else 1)


if __name__ == "__main__":
    main()
