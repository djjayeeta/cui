from __future__ import annotations

import argparse
import json
from pathlib import Path

from demo2agent.compiler_llm import Compiler
from demo2agent.compiler_preprocess import preprocess_video_segments_for_compiler
from demo2agent.llm_segmenter import segment_video, SegmenterConfig
from demo2agent.models import DemoTrace, WorkflowSpec
from demo2agent.util import ensure_dir, read_json
from demo2agent.executors.web_browser_use import BrowserUseWebExecutor
from demo2agent.executors.macos_ax_desktop_executor import MacOSAXDesktopExecutor
from demo2agent.orchestrator import Orchestrator
from demo2agent.util import write_json
from demo2agent.recorder import DemoRecorder, RecorderConfig,AudioRecordConfig


def cmd_segment(args) -> None:
    run_dir = Path(args.run)
    video_path = run_dir / "screen.mp4"
    if not video_path.exists():
        raise FileNotFoundError(f"Missing {video_path}")

    segments_path = run_dir / "segments.json"

    cfg = SegmenterConfig(
        model=args.segment_model,
        sample_fps=float(args.sample_fps),
        max_frames=int(args.max_frames),
        image_max_w=int(args.image_max_w),
        image_detail=args.image_detail,
    )
    data = segment_video(video_path, user_text=args.text, cfg=cfg)
    print(f"Wrote segments: {segments_path} ({len(data.get('segments', []))} segments)")


def cmd_compile(args) -> None:
    run_dir = Path(args.run)
    ensure_dir(run_dir)

    video_path = run_dir / "screen.mp4"
    if not video_path.exists():
        raise FileNotFoundError(f"Missing {video_path}")

    segments_path = run_dir / "segments.json"
    if not args.skip_segment:
        cfg = SegmenterConfig(
            model=args.segment_model,
            sample_fps=float(args.sample_fps),
            max_frames=int(args.max_frames),
            max_w=int(args.image_max_w),
        )
        segment_video(video_path, user_text=args.text, cfg=cfg)

    if not segments_path.exists():
        raise FileNotFoundError(
            f"Missing {segments_path}. Run 'segment' or compile without --skip-segment."
        )

    compiled_dir = run_dir / "compiled"
    ensure_dir(compiled_dir)

    # Best-effort: load trace.json if present (for metadata only)
    started_at_iso = ""
    screen_size = []
    transcript = []
    trace_path = run_dir / "trace.json"
    transcript_file_path = None
    if trace_path.exists():
        tr = DemoTrace.model_validate(read_json(trace_path))
        started_at_iso = tr.started_at_iso
        screen_size = tr.screen_size
        transcript = tr.transcript or []
        transcript_file_path = tr.transcript_file_path

    compile_input = preprocess_video_segments_for_compiler(
        video_path=video_path,
        segments_path=segments_path,
        out_dir=compiled_dir,
        demo_name=run_dir.name,
        started_at_iso=started_at_iso,
        screen_size=screen_size,
        transcript_file_path=transcript_file_path,
    )

    compiler = Compiler(model=args.compile_model)
    wf = compiler.compile_from_preprocessed(
        compile_input=compile_input,
        workflow_name=args.workflow_name or run_dir.name,
        debug_dir=str(compiled_dir),
    )

    workflow_path = compiled_dir / "workflow.json"
    workflow_path.write_text(wf.model_dump_json(indent=2), encoding="utf-8")
    print(f"Wrote workflow: {workflow_path}")


def cmd_run(args) -> None:
    run_dir = Path(args.run)
    workflow_path = run_dir / "compiled" / "workflow.json"
    if not workflow_path.exists():
        raise FileNotFoundError(f"Missing {workflow_path}. Run compile first.")

    wf = WorkflowSpec.model_validate(json.loads(workflow_path.read_text(encoding="utf-8")))

    # You want: --text "..." for user_text
    if args.text is not None and args.text.strip():
        inputs = {"user_text": args.text.strip()}
    elif args.inputs is not None and args.inputs.strip():
        inputs = json.loads(args.inputs)
    else:
        raise SystemExit(
            "run requires either:\n"
            '  --text "best pizza restaurants in San Jose"\n'
            'or\n'
            '  --inputs \'{"user_text":"best pizza restaurants in San Jose"}\'\n'
        )

    if "user_text" not in inputs or not str(inputs["user_text"]).strip():
        raise ValueError(
            'RUN requires user_text. Example: --text "..." or --inputs \'{"user_text":"..."}\''
        )

    # Instantiate your executors + orchestrator (adjust import paths if your repo differs)
    

    web_exec = BrowserUseWebExecutor()
    desktop_exec = MacOSAXDesktopExecutor()

    orch = Orchestrator(web_exec=web_exec, desktop_exec=desktop_exec)
    ctx = orch.run(wf, inputs)

    out_path = run_dir / "compiled" / "run_outputs.json"
    out_path.write_text(json.dumps(ctx, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote run outputs: {out_path}")

def cmd_record(args):
    out = Path(args.out)
    ensure_dir(out)

    cfg = RecorderConfig(
        out_dir=out,
        name=args.name,
        max_seconds=args.seconds,

        # new: audio + transcript
        record_audio=bool(args.audio),
        audio_cfg=AudioRecordConfig(enabled=bool(args.audio)),
        transcribe_audio=bool(args.transcribe),
        transcription_model=getattr(args, "transcribe_model", "whisper-1"),
    )

    trace = DemoRecorder(cfg).run_blocking()

    write_json(out / "trace.json", trace.model_dump())
    print(f"Saved: {out/'trace.json'}")

def main() -> None:
    p = argparse.ArgumentParser("demo2agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--out", default="runs/demo1")
    r.add_argument("--name", default="demo1")
    r.add_argument("--seconds", type=int, default=180)
    r.add_argument("--audio", action="store_true")
    r.add_argument("--transcribe", action="store_true")
    r.add_argument("--transcribe-model", default="whisper-1")
    r.set_defaults(func=cmd_record)

    sp = sub.add_parser("segment", help="LLM-segment the screen recording video")
    sp.add_argument("--run", required=True, help="run directory, e.g., runs/demo1")
    sp.add_argument("--model", default="gpt-5.2")
    sp.add_argument("--sample-fps", default="0.5")
    sp.add_argument("--max-frames", default="60")
    sp.add_argument("--image-max-w", default="640")
    sp.add_argument("--image-detail", default="low")
    sp.add_argument("--text", default=None, help="optional narration/intent text")
    sp.set_defaults(func=cmd_segment)

    cp = sub.add_parser("compile", help="segment video, build compile_input, compile workflow.json")
    cp.add_argument("--run", required=True)
    cp.add_argument("--workflow-name", default=None)
    cp.add_argument("--skip-segment", action="store_true")
    cp.add_argument("--segment-model", default="gpt-5.2")
    cp.add_argument("--compile-model", default="gpt-5.2")
    cp.add_argument("--sample-fps", default="0.5")
    cp.add_argument("--max-frames", default="60")
    cp.add_argument("--image-max-w", default="640")
    cp.add_argument("--image-detail", default="low")
    cp.add_argument("--text", default=None, help="optional narration/intent text")
    cp.set_defaults(func=cmd_compile)

    rp = sub.add_parser("run", help="run workflow using --text as user_text")
    rp.add_argument("--run", required=True)
    rp.add_argument(
        "--text",
        default=None,
        help='the runtime user_text (preferred). Example: --text "best pizza restaurants in San Jose"',
    )
    rp.add_argument(
        "--inputs",
        required=False,
        default=None,
        help='optional JSON string. Example: \'{"user_text":"..."}\'',
    )
    rp.set_defaults(func=cmd_run)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
