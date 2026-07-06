#!/usr/bin/env python3
"""
Batch image processor.

Full pipeline:          adjust → OpenAI enhance → rembg background removal
Lite pipeline:          adjust → rembg background removal  (--skip-enhance)
Enhance-only pipeline:  adjust → OpenAI enhance  (--skip-bg-removal)

The two stages of the full pipeline run concurrently: OpenAI API calls
(I/O-bound) overlap with rembg inference (CPU-bound) via a shared queue.
"""

import argparse
import os
import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image
from rembg import new_session
from tqdm import tqdm

from pipeline.adjust import adjust
from pipeline.enhance import DEFAULT_PROMPT, enhance_image
from pipeline.remove_bg import remove_background

load_dotenv()

SUPPORTED = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif"}
_SENTINEL = object()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_images(directory: Path) -> list[Path]:
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED
    )


# ---------------------------------------------------------------------------
# Full pipeline: OpenAI enhance → rembg  (two stages, overlapped)
# ---------------------------------------------------------------------------

def run_full_pipeline(
    images: list[Path],
    output_dir: Path,
    openai_client,
    rembg_session,
    prompt: str,
    brightness: float,
    contrast: float,
    sharpness: float,
    openai_workers: int,
    rembg_workers: int,
) -> list[tuple[str, str]]:
    # Bounded queue so OpenAI workers naturally pause if rembg falls behind
    work_queue: queue.Queue = queue.Queue(maxsize=rembg_workers * 2)

    failed: list[tuple[str, str]] = []
    lock = threading.Lock()
    pbar = tqdm(total=len(images), unit="img", ncols=80)

    # --- Stage 1: OpenAI enhancement ---

    def enhance_one(img_path: Path) -> None:
        try:
            img = Image.open(img_path)
            img = adjust(img, brightness, contrast, sharpness)
            enhanced = enhance_image(openai_client, img, prompt)
            work_queue.put((img_path, enhanced))
        except Exception as e:
            with lock:
                failed.append((img_path.name, f"enhance: {e}"))
            pbar.update(1)
            pbar.set_postfix(failed=len(failed))

    # --- Stage 2: rembg background removal ---

    def remove_one() -> None:
        while True:
            item = work_queue.get()
            if item is _SENTINEL:
                work_queue.put(_SENTINEL)  # pass sentinel along to other workers
                break
            img_path, enhanced = item
            try:
                result = remove_background(enhanced, rembg_session)
                (output_dir / (img_path.stem + ".png")).write_bytes(
                    _to_png_bytes(result)
                )
            except Exception as e:
                with lock:
                    failed.append((img_path.name, f"remove_bg: {e}"))
            pbar.update(1)
            pbar.set_postfix(failed=len(failed))

    # Start rembg workers (they block on the queue until work arrives)
    rembg_threads = [
        threading.Thread(target=remove_one, daemon=True)
        for _ in range(rembg_workers)
    ]
    for t in rembg_threads:
        t.start()

    # Run OpenAI calls concurrently; this blocks until all images are enhanced
    with ThreadPoolExecutor(max_workers=openai_workers) as pool:
        list(as_completed([pool.submit(enhance_one, img) for img in images]))

    # Signal rembg workers that no more work is coming
    work_queue.put(_SENTINEL)
    for t in rembg_threads:
        t.join()

    pbar.close()
    return failed


# ---------------------------------------------------------------------------
# Lite pipeline: rembg only  (--skip-enhance)
# ---------------------------------------------------------------------------

def run_lite_pipeline(
    images: list[Path],
    output_dir: Path,
    rembg_session,
    brightness: float,
    contrast: float,
    sharpness: float,
    workers: int,
) -> list[tuple[str, str]]:
    failed: list[tuple[str, str]] = []

    def process_one(img_path: Path) -> tuple[bool, str | None]:
        try:
            img = Image.open(img_path)
            img = adjust(img, brightness, contrast, sharpness)
            result = remove_background(img, rembg_session)
            (output_dir / (img_path.stem + ".png")).write_bytes(_to_png_bytes(result))
            return True, None
        except Exception as e:
            return False, str(e)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process_one, img): img for img in images}
        with tqdm(total=len(images), unit="img", ncols=80) as bar:
            for future in as_completed(futures):
                ok, err = future.result()
                if not ok:
                    failed.append((futures[future].name, err or "unknown"))
                bar.update(1)
                if failed:
                    bar.set_postfix(failed=len(failed))

    return failed


# ---------------------------------------------------------------------------
# Enhance-only pipeline: OpenAI only  (--skip-bg-removal)
# ---------------------------------------------------------------------------

def run_enhance_only_pipeline(
    images: list[Path],
    output_dir: Path,
    openai_client,
    prompt: str,
    brightness: float,
    contrast: float,
    sharpness: float,
    workers: int,
) -> list[tuple[str, str]]:
    failed: list[tuple[str, str]] = []

    def process_one(img_path: Path) -> tuple[bool, str | None]:
        try:
            img = Image.open(img_path)
            img = adjust(img, brightness, contrast, sharpness)
            enhanced = enhance_image(openai_client, img, prompt)
            (output_dir / (img_path.stem + ".png")).write_bytes(_to_png_bytes(enhanced))
            return True, None
        except Exception as e:
            return False, str(e)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process_one, img): img for img in images}
        with tqdm(total=len(images), unit="img", ncols=80) as bar:
            for future in as_completed(futures):
                ok, err = future.result()
                if not ok:
                    failed.append((futures[future].name, err or "unknown"))
                bar.update(1)
                if failed:
                    bar.set_postfix(failed=len(failed))

    return failed


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _to_png_bytes(img: Image.Image) -> bytes:
    from io import BytesIO
    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input_dir", type=Path, help="Folder of input images")
    parser.add_argument("output_dir", type=Path, help="Folder for output PNGs")

    img_group = parser.add_argument_group("image adjustments")
    img_group.add_argument("--brightness", type=float, default=1.0, metavar="N",
                           help="Brightness multiplier (default: 1.0)")
    img_group.add_argument("--contrast",   type=float, default=1.0, metavar="N",
                           help="Contrast multiplier (default: 1.0)")
    img_group.add_argument("--sharpness",  type=float, default=1.0, metavar="N",
                           help="Sharpness multiplier (default: 1.0)")

    ai_group = parser.add_argument_group("OpenAI enhancement")
    ai_group.add_argument("--skip-enhance", action="store_true",
                          help="Skip OpenAI step and run background removal only")
    ai_group.add_argument("--prompt", default=DEFAULT_PROMPT,
                          help="Prompt sent to gpt-image-1 for each image")
    ai_group.add_argument("--openai-workers", type=int, default=5, metavar="N",
                          help="Concurrent OpenAI API calls (default: 5; lower if rate-limited)")

    bg_group = parser.add_argument_group("background removal")
    bg_group.add_argument("--skip-bg-removal", action="store_true",
                          help="Skip rembg and save the OpenAI-enhanced image directly")
    bg_group.add_argument("--model", default="isnet-general-use",
                          choices=["isnet-general-use", "u2net", "silueta"],
                          help="rembg model (default: isnet-general-use)")
    bg_group.add_argument("--rembg-workers", type=int, default=8, metavar="N",
                          help="Concurrent rembg workers (default: 8)")

    args = parser.parse_args()

    if args.skip_enhance and args.skip_bg_removal:
        sys.exit(
            "Error: --skip-enhance and --skip-bg-removal together would skip "
            "the entire pipeline (only brightness/contrast/sharpness adjustment "
            "would run). Pass at most one of them."
        )

    if not args.input_dir.is_dir():
        sys.exit(f"Error: '{args.input_dir}' is not a directory.")

    images = find_images(args.input_dir)
    if not images:
        sys.exit(f"No supported images found in '{args.input_dir}'.")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    client = None
    if not args.skip_enhance:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            sys.exit(
                "Error: OPENAI_API_KEY not set.\n"
                "Copy .env.example → .env and add your key, or pass --skip-enhance."
            )
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

    session = None
    if not args.skip_bg_removal:
        print(f"Loading rembg model '{args.model}'...", end=" ", flush=True)
        session = new_session(args.model)
        print("done.")

    if args.skip_bg_removal:
        print(f"Enhance-only pipeline — {len(images)} images, {args.openai_workers} OpenAI workers.\n")
        failed = run_enhance_only_pipeline(
            images, args.output_dir, client, args.prompt,
            args.brightness, args.contrast, args.sharpness,
            args.openai_workers,
        )
    elif args.skip_enhance:
        print(f"Lite pipeline — {len(images)} images, {args.rembg_workers} workers.\n")
        failed = run_lite_pipeline(
            images, args.output_dir, session,
            args.brightness, args.contrast, args.sharpness,
            args.rembg_workers,
        )
    else:
        print(
            f"Full pipeline — {len(images)} images, "
            f"{args.openai_workers} OpenAI + {args.rembg_workers} rembg workers.\n"
        )
        failed = run_full_pipeline(
            images, args.output_dir, client, session,
            args.prompt,
            args.brightness, args.contrast, args.sharpness,
            args.openai_workers, args.rembg_workers,
        )

    passed = len(images) - len(failed)
    print(f"\n{passed}/{len(images)} succeeded.")
    if failed:
        print("Failures:")
        for name, err in failed:
            print(f"  {name}: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
