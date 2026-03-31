# DTF Image Quality Gate — Design Document

**Version:** 2.0 | **Date:** March 29, 2026
**Produced by:** gstack role pipeline (/office-hours, /plan-ceo-review, /plan-eng-review, /plan-design-review, /design-consultation, /cso)

---

## 1. Problem Statement (from /office-hours)

There are two problems masquerading as one.

**Problem A: Defective prints reaching customers.** 829 pipeline-targetable claims/month, $841.7K/yr addressable. Breakdown: BG Removal 477, Poor Quality 259, CMYK 568, Upscaling 289 (+31.9% MoM), Semi-Transparent 93, Thin Lines 70 (+14% MoM), Trimming 96.

**Problem B: Vendor QA cost.** 100 people at $150K/mo today, scaling to 500 people at $650K/mo ($7.8M/yr). This is the 10x opportunity.

The system to prevent defects (precision) is not the same system that graduates images out of vendor review (recall on "good"). The $841K defect pool is bounded. The $7.8M vendor cost scales with volume. **Optimize for graduation rate, not accuracy rate.**

### Demand Evidence

**Measured:** 1,852 claims/month. BG removal eval: 89.4% accuracy but 3.5% genuine defect rate (96.5% noise). CS-1 CMYK gate was a mathematical no-op. Upscaling claims growing +31.9% MoM.

**Assumed (unverified):** Dollar cost per defective image uniform across categories. Customer LTV impact absent. Label quality sufficient across all categories. Vendor QA internally consistent. Claim categorization by CS reps is accurate.

### Key Premises

1. **P1:** Label data is trustworthy enough to train/evaluate on. The 3.5% BG removal TPR says it isn't. Must build clean ground truth dataset.
2. **P2:** Software gates can achieve 99%+ precision on at least some categories. CS-1 no-op proves this isn't guaranteed. Must validate per-gate.
3. **P3:** Category taxonomy is stable and correctly attributed. Must audit CS claim categorization.
4. **P4:** Graduation rate, not accuracy rate, is the business metric. An AI that confidently passes 60% of images with zero defect leakage beats 95% accuracy that can't clear anything.
5. **P5:** Vendor QA quality is consistent enough to serve as baseline. Inter-rater reliability unmeasured.

### Narrowest Wedge

**Upscaling detection.** 289 claims/month, +31.9% MoM growth (likely AI-generated images). Signal is deterministic (DPI at print size is math, not opinion). False positives are verifiable. No VLM needed. No label noise dependency. Ship this gate first.

---

## 2. Scope and Strategy (from /plan-ceo-review)

### Scope Mode: HOLD

Do NOT expand to full production system. Collect data first.

- **EXPANSION** (all 10 gates, multi-model, real-time routing): Wrong — building a cathedral on sand. BG removal is 3.5% TPR. CS-1 was a no-op. How many other gates are load-bearing?
- **HOLD** (shadow/eval, data collection, threshold calibration): Right — Chris's directive requires certainty. Shadow mode gives ground truth labels with zero risk.
- **REDUCTION** (3-4 gates, single model): Premature — you don't know which 3-4 gates are highest-value without data.

### The 60-Day Play

1. **Week 1:** Build ground truth pipeline. Match vendor pass/fail labels to AI predictions.
2. **Week 2:** Per-gate scorecards. Precision, recall, F1 against vendor labels. Kill gates with <80% precision.
3. **Week 3-4:** With 48K-72K labeled images, identify which gates are load-bearing.
4. **Week 5-8:** Calibrate thresholds. Tighten until false negative rate <0.1%.
5. **Week 9-12:** Start graduating. Pick highest-precision category. Track vendor override rate.

### Error and Rescue Registry

| Failure Mode | Rescue | User Impact |
|---|---|---|
| VLM API timeout/failure | Retry 2x with backoff, then route to vendor. NEVER block. | None (vendor reviews) |
| BG removal API failure | Skip BG gates for this image. Mark "SKIPPED". | None (other gates + vendor) |
| False positive (AI flags clean) | In HOLD: no impact. In GRADUATE: $1.30 waste/image. If FP rate >5%, disable gate. | Unnecessary delay |
| False negative (AI misses defect) | In HOLD: vendor catches. In GRADUATE: BAD IMAGE SHIPS. Root-cause every FN. | DEFECTIVE PRINT. ~$1,017/claim |
| Unparseable VLM JSON | Treat as API failure → vendor. Log raw response. If >5% unparseable, fix prompt/model. | None |
| Cost overrun | Hard circuit breaker at daily budget. Route remaining to vendor. Auto-resume next day. | All images go to vendor |

### Graduation Economics

- 25,000 images/day x $1.30/image = $32,500/day vendor cost
- Each graduated image saves $1.29 ($1.30 minus $0.01 API)
- 50% graduation = $487K/mo saved
- 80% graduation = $780K/mo saved

---

## 3. Technical Architecture (from /plan-eng-review)

### Module Structure

Split the monolith:

```
dtf-quality-gate/
├── app.py                    # FastAPI routes only
├── config.py                 # Constants, env vars, model lists
├── gates/
│   ├── __init__.py           # GateRegistry, GateResult dataclass
│   ├── background.py         # BG-1..BG-4
│   ├── resolution.py         # LR-1, LR-2
│   ├── structural.py         # TL-1, JE-1
│   ├── color.py              # CS-1
│   └── qr.py                 # QR-1
├── vlm/
│   ├── client.py             # HTTP client: retry, fallback, JSON repair
│   ├── prompt.py             # System + user prompt builder
│   └── consensus.py          # 2-model verdict reconciliation
├── pipeline/
│   ├── runner.py             # Orchestrates gates → VLM → decision
│   ├── bg_removal.py         # BRIA integration
│   └── decision.py           # Final pass/fail authority
├── models.py                 # Pydantic: GateResult, VLMAssessment, PipelineResult
├── templates/
└── tests/
    └── eval/                 # Labeled dataset + eval runner
```

### Decision Hierarchy

```
                 ┌─────────────┐
                 │  FINAL GATE │  ← single boolean: PASS or FAIL
                 └──────┬──────┘
                        │
          ┌─────────────┼─────────────┐
          │             │             │
    ┌─────┴─────┐ ┌─────┴─────┐ ┌─────┴─────┐
    │ SW HARD   │ │ VLM       │ │ CS-1      │
    │ BLOCKS    │ │ CONSENSUS │ │ ADVISORY  │
    └───────────┘ └───────────┘ └───────────┘
```

1. **Hard blocks (auto-fail, no override):** LR-1 below 72 DPI. QR-1 confirmed corrupted.
2. **VLM-adjudicated:** BG-1..BG-4, LR-2, TL-1, JE-1. SW gates are witnesses; VLM is judge.
3. **Advisory (info-only):** CS-1 CMYK. No fail until ICC profile support.

### VLM Consensus (2 models, not 8)

| Primary (Gemini Flash) | Confirmation (Claude Sonnet) | Final |
|---|---|---|
| PASS | (not called) | PASS |
| FAIL | FAIL | FAIL |
| FAIL | PASS | MANUAL REVIEW |
| Error | (fallback to confirmation) | Use confirmation |

Multi-model (8 models) is an eval tool, not production. $0.005/image in production vs $0.30-0.50/image with 8 models.

### False Positive Reduction

| Gate | Problem | Fix |
|---|---|---|
| BG-2 | Fires on white designs | Raise to 92% AND require >90% opaque pixels |
| BG-3 | Anti-aliased text triggers | Require >2% of edge pixels AND exclude adjacent-to-content |
| BG-4 | 1-pixel compression noise | Require clusters >5px AND >3px from content boundary |
| LR-2 | Noisy on soft art | Add VLM style classifier (watercolor → auto-exempt) |
| TL-1 | Skeletonize slow + noisy | Use distance_transform alone, threshold at 0.3mm |
| JE-1 | Pixel-grid quantization | Only analyze alpha contours, require >0.25 on >100 points |
| CS-1 | Heuristic only | Keep info-only until ICC profiles |

### VLM Prompt: Two-Phase Approach

Phase 1 — Blind: VLM sees image only, no gate data. Forms independent opinion.
Phase 2 — Gate-informed: VLM sees its Phase 1 assessment + gate flags. Reconciles.

Prevents anchoring bias. More expensive (2 calls) but eliminates "VLM rubber-stamps SW flags."

### Model Reliability

- **Retry:** 3 attempts, exponential backoff (1s, 4s, 15s), jitter. No retry on 4xx.
- **Fallback chain:** Gemini Flash → Claude Sonnet → Gemini Flash Lite.
- **Circuit breaker:** 5 failures in 10 min → mark unhealthy for 5 min.
- **JSON:** Use Gemini structured output mode (response_mime_type: application/json) or Anthropic tool_use. Eliminates parse failures.
- **Migrate off deprecated fal-ai/any-llm/vision.** Use provider APIs directly.

### BG Removal Flow

- **Auto-detect:** If RGBA with >10% transparent pixels → skip BRIA (already removed).
- **Run BRIA:** On RGB/no-alpha images with uniform corner color.
- **Send both images to VLM:** Original + BG-removed for BR-1 comparison.
- **BG gates only run when BG removal was applied.** Never flag on images that didn't use BG removal.

### Scalability (25K images/day)

Queue architecture: FastAPI (accept) → Redis Queue → Workers (N=4-8) → Results DB.

**Cost model per image:**

| Component | Cost | Monthly at 25K/day |
|---|---|---|
| BRIA BG removal | $0.01 | $7,500 |
| VLM primary (Gemini Flash) | $0.003 | $2,250 |
| VLM confirmation (20% x Claude) | $0.002 | $1,500 |
| Compute | $0.0004 | $300 |
| **Total** | **$0.015** | **~$11,600** |

### Test Plan

150 images minimum: 50 clean, 80 defective (10 per category), 20 edge cases (FP bait).

**Go/no-go criteria:**

| Metric | Target |
|---|---|
| Overall precision | ≥90% |
| Overall recall | ≥95% |
| Per-gate FP rate | <10% |
| VLM consensus rate | >90% |
| Manual review rate | <5% |
| Pipeline P95 latency | <20s |
| Cost per image | <$0.02 |

### NOT In Scope

- ICC profile CMYK conversion (revisit when CS-1 FP rate is proven problematic)
- Multi-tenant auth (single-operator for now)
- Vendor portal / redraw workflow (separate project after gate accuracy targets met)
- GPU-accelerated SW gates (CPU fast enough at current scale)
- QR code live URL verification (security risk)
- Batch upload (after queue architecture)
- Historical analytics dashboard (after 30 days of production data)

---

## 4. UI/UX Design (from /plan-design-review + /design-consultation)

### Design Principles

This is a **professional print QA lab tool**, not a consumer app. Design for:
- **Speed of judgment:** The operator needs pass/fail in <2 seconds of visual scan
- **Trust calibration:** Show why the AI decided, not just what it decided
- **Minimal cognitive load:** 500 operators x 25K images = no time for complexity

### Information Hierarchy

1. **PASS/FAIL verdict** — the biggest element on the page
2. **Flagged gates** — only the ones that triggered (not all 10)
3. **VLM rationale** — why it decided what it decided
4. **Image preview** — original + BG-removed side by side
5. **Details on demand** — expandable gate metrics, raw JSON

### Key Decisions

- **Multi-model comparison is debug/eval only.** Production UI shows single-model verdict. Comparison table available under "Advanced > Model Comparison" for calibration sessions.
- **Default to 1 model (Gemini Flash).** 8-model parallel run is opt-in.
- **Vendor dashboard is a separate project.** Current tool is for Jay's team to calibrate the AI. Vendor-facing UI is a future milestone after accuracy targets are met.

### Design System

- **Font:** Inter, system-ui fallback. Body 13px, headings 15-20px.
- **Colors:** Pass #16a34a on #dcfce7. Fail #dc2626 on #fef2f2. Warning #d97706 on #fef3c7. Info #2563eb on #dbeafe. Neutral #f8f8f6 / #fff.
- **Spacing:** 4px base unit. 8, 12, 16, 24, 32px scale.
- **Border radius:** 8px cards, 6px inputs, 4px chips.
- **Max width:** 960px single-column layout.

---

## 5. Security Requirements (from /cso)

### Priority Fixes

| Priority | Issue | Fix |
|---|---|---|
| P0 | Uploads publicly accessible | Replace StaticFiles with gated endpoint + path traversal check |
| P1 | No authentication | Shared API key via X-API-Key header, hmac.compare_digest |
| P2 | No rate limiting | slowapi, 10 req/min on /analyze |
| P2 | No daily spend cap | Track running total, reject when exceeded |
| P3 | No CORS | CORSMiddleware, allow own origin only |
| P4 | Python 3.9 EOL | Upgrade to 3.11+ |

### Cost Protection

- **Default to 1 model, not 8.** Multi-model is opt-in.
- **Per-request cost cap:** Reject if estimated cost >$0.50.
- **Daily spend tracking:** Hard ceiling (default $10/day), reject with clear error when hit.
- **Concurrent request cap:** asyncio.Semaphore, max 4 in-flight VLM calls.

### Data Retention

- Default: 24 hours. Override via DTF_RETENTION_HOURS env var.
- Cleanup: startup sweep + hourly background task.
- BG-removed variants follow same policy.

### Production Deployment

Reverse proxy (Caddy/nginx) → TLS → uvicorn on 127.0.0.1. Process management via systemd or Docker. Non-root execution. Secrets in .env with 600 permissions.

---

## 6. Implementation Plan

| Phase | Work | Days | Depends On |
|---|---|---|---|
| **P0** | Build labeled eval dataset (150 images) | 3 | Nothing |
| **P0** | Module split (gates/, vlm/, pipeline/) | 1 | Nothing |
| **P0** | Migrate off deprecated fal-ai/any-llm/vision | 1 | Module split |
| **P1** | VLM client: retry, fallback, JSON repair | 1 | Module split |
| **P1** | Decision hierarchy + 2-model consensus | 1 | VLM client |
| **P1** | BG auto-detection + before/after VLM | 1 | Module split |
| **P1** | Threshold calibration vs eval dataset | 2 | Eval dataset + split |
| **P1** | Security fixes (auth, rate limit, upload gating) | 1 | Module split |
| **P2** | Queue architecture (Redis + arq) | 2 | Module split |
| **P2** | Eval CI integration | 1 | Eval dataset |
| **P2** | Two-phase VLM prompt (blind + gate-informed) | 1 | Decision hierarchy |

**P0 blocks everything.** Build the eval dataset and split the monolith first.

---

## 7. Open Questions (Need Human Answers)

1. **What is the actual dollar cost per defective print, by category and customer segment?** The $841K aggregate hides the distribution.
2. **What is the inter-rater reliability of vendor QA staff?** If vendors disagree 15% of the time, the noise floor is 15%.
3. **What is customer LTV impact of receiving a defective print?** Retention data for claim-filers vs non-filers.
4. **Why is upscaling growing at +31.9% MoM?** AI-generated images? Product mix shift?
5. **What's the vendor contract structure?** Per-image, retainer, or per-seat? Determines graduation savings.
6. **Who owns claim categorization and how accurate is it?** CS reps or visual inspection?
7. **What's the vendor defect miss rate?** You know FP rate. What's the FN rate?

---

## Key Insight

> You have a perfect learning environment (vendor catches everything) and you're not using it. The vendor audit IS your training data pipeline. Capture the labels. The rest follows.
>
> — /office-hours, /plan-ceo-review (convergent finding)
