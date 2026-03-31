"""
Golden Dataset Eval — runs all imaging-only examples through the pipeline
and scores whether the system catches each real claim defect.

Usage: python run_eval.py [--model MODEL] [--no-bg]
"""

import asyncio
import json
import time
import sys
import os
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
if not os.environ.get("FAL_KEY"):
    raise RuntimeError("FAL_KEY environment variable not set. Export it before running eval.")

from app import run_pipeline, app, lifespan
import httpx

CATEGORY_TO_EXPECTED_CHECK = {
    "Artwork issue: poor quality": {"sw_gates": ["LR-1", "LR-2"], "vlm_checks": ["UP1_upscaling_integrity"]},
    "Artwork issue: semi-transparent effects": {"sw_gates": ["BG-3"], "vlm_checks": ["ST1_semi_transparency"]},
    "Artwork issue: thin lines": {"sw_gates": ["TL-1"], "vlm_checks": ["TL_VLM_thin_lines"]},
    "Artwork issue: RGB to CMYK": {"sw_gates": ["CS-1"], "vlm_checks": []},
    "Background Removal": {"sw_gates": ["BG-1", "BG-2", "BG-3", "BG-4"], "vlm_checks": ["BR1_bg_removal"]},
    "Trimming/Sizing": {"sw_gates": [], "vlm_checks": ["UP1_upscaling_integrity"]},
    "Upscaling/Vectorization Issue": {"sw_gates": [], "vlm_checks": ["UP1_upscaling_integrity"]},
}


async def run_eval(model="google/gemini-2.5-flash", use_bg=False):
    with open("golden_dataset/manifest.json") as f:
        manifest = json.load(f)

    imaging = [d for d in manifest
               if d["category"] == "IMAGE" and d["subcategory"] != "Printer Assignment"]

    print(f"Running eval: {len(imaging)} images, model={model}, bg_removal={use_bg}")
    print("=" * 80)

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(120.0, connect=10.0),
        headers={"Authorization": f"Key {os.environ['FAL_KEY']}", "Content-Type": "application/json"},
    ) as client:

        results = []
        for i, entry in enumerate(imaging):
            row = entry["row"]
            subcat = entry["subcategory"]
            comment = entry.get("comment", "")

            orig_path = entry["images"].get("original")
            checkout_path = entry["images"].get("checkout")
            img_path = orig_path or checkout_path
            if not img_path or not Path(img_path).exists():
                print(f"  [{i+1}/{len(imaging)}] Row {row}: SKIP — no image file")
                results.append({"row": row, "status": "skip", "reason": "no image"})
                continue

            try:
                img = Image.open(img_path)
                img.load()
            except Exception as e:
                print(f"  [{i+1}/{len(imaging)}] Row {row}: SKIP — {e}")
                results.append({"row": row, "status": "skip", "reason": str(e)[:80]})
                continue

            # If checkout image exists, use it as the "processed" image for comparison
            checkout_img = None
            if checkout_path and Path(checkout_path).exists() and checkout_path != img_path:
                try:
                    checkout_img = Image.open(checkout_path)
                    checkout_img.load()
                except Exception:
                    checkout_img = None

            t0 = time.perf_counter()
            try:
                # Pass checkout as the analysis_img if available (simulates post-processing)
                if checkout_img:
                    from app import _run_all_gates, _img_to_data_uri, vlm_assessment_with_retry, compute_final_verdict
                    from functools import partial
                    loop = asyncio.get_event_loop()
                    sw_results = await loop.run_in_executor(
                        None, partial(_run_all_gates, checkout_img, 10.0, 10.0, False, False))
                    orig_uri = _img_to_data_uri(img.convert("RGB") if img.mode not in ("RGB","RGBA") else img, max_dim=768)
                    proc_uri = _img_to_data_uri(checkout_img.convert("RGB") if checkout_img.mode not in ("RGB","RGBA") else checkout_img, max_dim=768)
                    vlm_r = await vlm_assessment_with_retry([orig_uri, proc_uri], sw_results, model, client)
                    fv = compute_final_verdict(sw_results, vlm_r)
                    result = {
                        "stage1_sw_gates": {"flagged_gates": [g["gate_id"] for g in sw_results if g["flag"]]},
                        "model_results": {model: vlm_r},
                        "final_verdict": fv,
                    }
                else:
                    result = await run_pipeline(img, 10.0, 10.0, [model], use_bg, client)
            except Exception as e:
                print(f"  [{i+1}/{len(imaging)}] Row {row}: ERROR — {e}")
                results.append({"row": row, "status": "error", "reason": str(e)[:80]})
                continue
            finally:
                img.close()
                if checkout_img:
                    checkout_img.close()

            elapsed = round(time.perf_counter() - t0, 1)

            fv = result.get("final_verdict", {})
            verdict = fv.get("verdict", "UNKNOWN")
            reasons = fv.get("reasons", [])

            mr = result.get("model_results", {})
            primary = mr.get(model, {})
            assessment = primary.get("assessment", {})
            checks = assessment.get("checks", {})

            s1 = result.get("stage1_sw_gates", {})
            flagged_gates = s1.get("flagged_gates", [])

            expected = CATEGORY_TO_EXPECTED_CHECK.get(subcat, {})
            expected_sw = expected.get("sw_gates", [])
            expected_vlm = expected.get("vlm_checks", [])

            sw_caught = any(g in flagged_gates for g in expected_sw) if expected_sw else None
            vlm_caught = any(
                checks.get(ck, {}).get("status") == "fail"
                for ck in expected_vlm
            ) if expected_vlm else None

            caught = verdict == "FAIL"

            status = "CAUGHT" if caught else "MISSED"
            icon = "✓" if caught else "✗"

            print(f"  [{i+1}/{len(imaging)}] {icon} Row {row}: {subcat}")
            print(f"       Verdict: {verdict} | SW caught: {sw_caught} | VLM caught: {vlm_caught} | {elapsed}s")
            if not caught:
                print(f"       ⚠ MISSED — comment: \"{comment[:80]}\"")
            if reasons:
                print(f"       Reasons: {reasons[0][:80]}")

            results.append({
                "row": row,
                "subcategory": subcat,
                "comment": comment[:150],
                "verdict": verdict,
                "caught": caught,
                "sw_caught": sw_caught,
                "vlm_caught": vlm_caught,
                "sw_flagged": flagged_gates,
                "vlm_checks": {k: v.get("status") for k, v in checks.items() if isinstance(v, dict)},
                "reasons": reasons,
                "elapsed_s": elapsed,
                "vlm_score": assessment.get("print_readiness_score"),
            })

        # Summary
        print("\n" + "=" * 80)
        print("EVAL SUMMARY")
        print("=" * 80)

        total = len([r for r in results if r.get("status") not in ("skip", "error")])
        caught_count = len([r for r in results if r.get("caught")])
        missed = [r for r in results if r.get("caught") == False]

        print(f"Total evaluated: {total}")
        print(f"Caught (FAIL):   {caught_count}/{total} ({caught_count/total*100:.0f}%)" if total else "")
        print(f"Missed (PASS):   {len(missed)}/{total} ({len(missed)/total*100:.0f}%)" if total else "")

        from collections import Counter
        cat_stats = {}
        for r in results:
            if r.get("status") in ("skip", "error"):
                continue
            cat = r.get("subcategory", "?")
            if cat not in cat_stats:
                cat_stats[cat] = {"total": 0, "caught": 0}
            cat_stats[cat]["total"] += 1
            if r.get("caught"):
                cat_stats[cat]["caught"] += 1

        print(f"\nPer-category accuracy:")
        for cat, stats in sorted(cat_stats.items()):
            pct = stats["caught"] / stats["total"] * 100 if stats["total"] else 0
            bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
            print(f"  {cat:40s} {stats['caught']}/{stats['total']} {bar} {pct:.0f}%")

        if missed:
            print(f"\nMISSED CASES (need investigation):")
            for r in missed:
                print(f"  Row {r['row']}: {r['subcategory']}")
                print(f"    Comment: \"{r.get('comment', '')[:100]}\"")
                print(f"    SW flagged: {r.get('sw_flagged', [])}")
                print(f"    VLM checks: {r.get('vlm_checks', {})}")

        with open("golden_dataset/eval_results.json", "w") as f:
            json.dump({"model": model, "results": results, "summary": cat_stats}, f, indent=2)
        print(f"\nResults saved to golden_dataset/eval_results.json")


if __name__ == "__main__":
    model = "google/gemini-2.5-flash"
    use_bg = False
    for arg in sys.argv[1:]:
        if arg.startswith("--model="):
            model = arg.split("=", 1)[1]
        elif arg == "--bg":
            use_bg = True
    asyncio.run(run_eval(model, use_bg))
