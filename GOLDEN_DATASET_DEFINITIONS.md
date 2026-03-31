# Golden Dataset — Problem Definitions from Real Claims

Extracted from `Golden Dataset Categories - Examples.xlsx` — 21 imaging-only examples with original/checkout/claim image triplets across 7 categories.

## 1. Background Removal (3 examples)

**What it is:** BG removal tool fails — leaves background remnants, removes parts of the design, or creates halos.

**Real customer complaints:**
- "The L in Michael Jordan was removed from my print" — **over-removal** (design element deleted)
- "Transfers came in with a white background behind the tree on my logo that should have been transparent" — **under-removal** (background patches remain)
- "The number 1 in the area code is messed up on all of them" — **character corruption** during processing

**What to detect:** Compare original vs processed. Did BG removal delete design elements (over) or leave background patches (under)?

## 2. Semi-Transparent Effects (3 examples)

**What it is:** Images with smoke, glow, neon, shadow effects that look great on screen but print as white/gray/brown haze on dark garments because DTF deposits white ink at any alpha > 0.

**Real customer complaints:**
- "Shows white gray and brown after pressed when viewed before buying didn't look like it at all" — glow effects printed as white haze
- "The bottom image doesn't have enough backing causing it to not adhere" — semi-transparent areas have insufficient ink coverage
- "Just redo" — customer saw the defect and gave up explaining

**What to detect:** ANY area with gradual transparency (smoke, glow, neon, shadows, faded edges). This is a physics constraint — DTF cannot reproduce semi-transparency. Intent does not matter.

## 3. Thin Lines (3 examples)

**What it is:** Lines/strokes too thin for DTF adhesive powder to bond. Below 0.03" (0.76mm) at print size, the powder falls off during pressing. Result: missing lines, partial lines, white texture where ink should be.

**Real customer complaints:**
- "The yellow line didn't press to the shirt except for the 2 little spots that are white and textured" — thin line failed to transfer
- "When I go to press this item it will not come off correctly" — thin elements won't bond
- "No powder on the film in some areas" — powder can't adhere to thin strokes

**What to detect:** Isolated narrow strokes (not edges of filled shapes) that are < 2px wide at the image's resolution relative to print size. These are separate design elements like underlines, thin borders, fine text strokes.

## 4. RGB to CMYK Color Shift (3 examples)

**What it is:** Vivid RGB colors (electric blue, neon green, bright red) shift dramatically when converted to CMYK gamut for printing. Customer sees one color on screen, receives a different color on garment.

**Real customer complaints:**
- "Completely wrong color despite putting in the notes to make sure the color is accurate. When pressed it was actually gray rather than the electric blue we uploaded" — electric blue → gray
- "The green color isn't even remotely the same. All coloring off and the yellows aren't the same" — multiple colors shifted
- "Color on the background does not look like my uploaded photo" — overall color shift

**What to detect:** Highly saturated colors (neon, electric blue, vivid green, bright pink) that are outside CMYK gamut. These WILL shift. Currently informational-only (CS-1 gate).

## 5. Poor Quality / Low Resolution (3 examples)

**What it is:** Image resolution too low for the ordered print size. Pixels visible in the printed output. Text looks fuzzy, details are blurred.

**Real customer complaints:**
- "The letters look fuzzy not clean and sharp like my other two designs" — pixelation visible in text
- "DTF transfers quality of the DELCO lacrosse club could be a little sharper on the bottom" — resolution degradation in specific areas

**What to detect:** DPI below 150 at the ordered print dimensions. Below 72 DPI is a hard fail (LR-1). Blur detection via Laplacian variance (LR-2).

## 6. Upscaling / Vectorization Issues (3 examples)

**What it is:** AI upscaler (RealESRGAN etc.) or vectorization tool corrupts the image. Text gets garbled, faces distorted, details hallucinated, colors smudged.

**Real customer complaints:**
- "The number 1 in the area code is messed up on all of them" — text corruption from upscaling
- "The L in Michael Jordan was removed from my print" — character loss from processing
- "Black ink smudges on the red part of the flag on every single one (66 prints)" — processing artifact replicated across batch

**What to detect:** Compare original upload vs processed/enhanced version. Look for text corruption, face distortion, sharpening halos, color bleed, smudges not in original.

## 7. Trimming / Sizing (3 examples)

**What it is:** Image stretched, cropped, or resized incorrectly. Aspect ratio doesn't match what customer ordered. Elements cut off or distorted.

**Real customer complaints:**
- "The back prints I ordered in 10in and 12in did not show they would be stretched out of proportion! The logo is stretched" — aspect ratio distortion
- "Print previews are different sizes but I have the same size" — sizing inconsistency
- "Please make sure they don't make the shape oval... the logo is a circle" — shape distortion from sizing

**What to detect:** Compare original aspect ratio vs processed. Check if elements are stretched/compressed. Currently vendor-only check.

---

## Categories NOT Imaging Problems (excluded from AI analysis)

- **Printer Assignment** — system routing issue, not image defect
- **PRINTER > Banding/Color/Curing/etc** — physical printer hardware problems
- **COURIER > Damage/Lost** — shipping issues
- **CUSTOMER > Ordered Wrong** — customer error
- **BLANK SUPPLIER > Damaged/Wrong** — garment supply issues
- **REDRAW/VENDOR** — vendor execution issues (important for vendor QA, but not image pipeline detection)

## Image Triplet Structure

Each example has up to 3 images:
- **Original** — what the customer uploaded
- **Checkout** — what was shown to the customer on the product page (after enhancement/BG removal)
- **Claim** — photo the customer submitted with their complaint (shows the physical defect on fabric)

The AI system should compare **Original vs Checkout** to catch processing defects BEFORE printing.
The **Claim image** is verification only — proves the defect actually appeared on the physical product.
