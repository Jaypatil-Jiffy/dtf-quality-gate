# DTF Image Quality Gate — Design System

## 1. Product Context

### Who uses this

| Role | Context | Primary need |
|------|---------|-------------|
| **Image Analyst** | Receives customer artwork, runs QA before sending to print. Desktop workstation, dual monitors, processes 50-100 images/shift. | Speed and confidence. Needs a clear PASS/FAIL in under 20 seconds. Does not want to interpret raw data — wants a verdict with evidence. |
| **Vendor Auditor** | Reviews flagged images, decides if defects are fixable or require customer resubmission. May be on the production floor with a tablet. | Actionable defect reports. Needs to see exactly what's wrong, where it is in the image, and what fix to apply. Does not care about model comparison — wants the answer. |
| **Redraw Artist** | Fixes defects flagged by auditors. Working in Photoshop/Illustrator. | Precise technical specifications: "Increase stroke width to 0.5mm at 300 DPI", "Remove residual alpha in top-left corner region". Pixel-level detail. |
| **Operations Manager** | Monitors throughput, quality trends, model accuracy. Dashboard consumer. | Aggregate statistics: pass rates, common failure modes, processing times, model agreement rates. |

### What they need to feel

- **Trust**: The tool's judgment is reliable. When it says PASS, the print won't fail. When it says FAIL, the defect is real.
- **Efficiency**: This is a high-throughput industrial tool. Every unnecessary click costs time across 25K images/day.
- **Precision**: The aesthetic should communicate "calibrated instrument" — not "friendly app." Think: oscilloscope UI, lab equipment readout, print shop color checker.

---

## 2. Aesthetic Direction

### Industrial Precision

This is a **laboratory instrument for print quality assurance**, not a consumer product. The visual language borrows from:

- Color calibration tools (X-Rite i1Studio)
- Print shop RIP software (Wasatch, Kothari)
- Clinical lab result interfaces (structured, hierarchical, no decoration)

### Guiding principles

1. **Data density over whitespace.** Users process 50-100 images per shift. Wasted space = wasted time.
2. **Semantic color only.** Color communicates meaning (pass/fail/warning/info), never decoration.
3. **Monochrome chrome, chromatic signals.** The UI frame is neutral gray. Color appears only when data demands attention.
4. **Typography carries hierarchy.** Size, weight, and case do the structural work — not borders, shadows, or color fills.

---

## 3. Typography

### Font stack

```css
--font-sans: "Inter", "SF Pro Text", system-ui, -apple-system, sans-serif;
--font-mono: "JetBrains Mono", "SF Mono", "Cascadia Code", "Fira Code", monospace;
```

Inter is retained — it is the correct choice for a data-heavy UI with tabular numbers
(`font-feature-settings: "tnum"`) and clear small-size legibility. No display or
decorative fonts. This is not a branding exercise.

### Type scale (1.2 ratio, base 13px)

| Token | Size | Weight | Use |
|-------|------|--------|-----|
| `--text-xs` | 10px / 0.625rem | 500 | Gate IDs, latency values, metadata labels |
| `--text-sm` | 11px / 0.6875rem | 400 | Table cells, card body text, secondary info |
| `--text-base` | 13px / 0.8125rem | 400 | Body text, form labels, descriptions |
| `--text-md` | 14px / 0.875rem | 600 | Card titles, section labels, button text |
| `--text-lg` | 16px / 1rem | 700 | Section headings, panel titles |
| `--text-xl` | 20px / 1.25rem | 700 | Page title, verdict display |
| `--text-2xl` | 28px / 1.75rem | 800 | Hero verdict score (the big number) |

### Type rules

```css
body {
  font-family: var(--font-sans);
  font-size: var(--text-base);
  font-feature-settings: "tnum" 1, "ss01" 1;
  -webkit-font-smoothing: antialiased;
  line-height: 1.5;
  color: var(--neutral-900);
}

.mono {
  font-family: var(--font-mono);
  font-size: calc(var(--text-sm) - 1px); /* mono reads larger */
  letter-spacing: -0.01em;
}

/* All-caps labels for gate IDs and column headers */
.label-caps {
  font-size: var(--text-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--neutral-500);
}
```

---

## 4. Color Palette

### Design rationale

Print QA has a natural color vocabulary: green=pass, red=fail, amber=warning.
These are not arbitrary — they map to the physical world (traffic lights, lab
indicators, print calibration targets). The palette uses these semantics at
full strength on a neutral-warm gray foundation.

### Neutrals (warm gray, not blue-gray)

```css
--neutral-50:  #FAFAF8;   /* page background */
--neutral-100: #F5F5F0;   /* card backgrounds, table header bg */
--neutral-150: #EDEDEA;   /* hover state backgrounds */
--neutral-200: #E0E0DB;   /* borders, dividers */
--neutral-300: #C8C8C1;   /* disabled state borders */
--neutral-400: #A3A39C;   /* placeholder text */
--neutral-500: #787872;   /* secondary text, labels */
--neutral-600: #5C5C56;   /* body text (secondary) */
--neutral-700: #3D3D38;   /* body text (primary) */
--neutral-800: #282824;   /* headings */
--neutral-900: #141412;   /* high-emphasis text */
--neutral-950: #0A0A08;   /* maximum contrast */
```

### Semantic status colors

```css
/* Pass / Success — forest green, not neon */
--pass-50:  #F0FDF4;   /* background tint */
--pass-100: #DCFCE7;   /* light badge bg */
--pass-500: #16A34A;   /* text, icons */
--pass-600: #15803D;   /* darker text on light bg */
--pass-700: #166534;   /* high-contrast text */

/* Fail / Critical — true red, not orange-red */
--fail-50:  #FEF2F2;   /* background tint */
--fail-100: #FEE2E2;   /* light badge bg */
--fail-500: #DC2626;   /* text, icons */
--fail-600: #B91C1C;   /* darker text on light bg */
--fail-700: #991B1B;   /* high-contrast text */

/* Warning / Flagged — warm amber */
--warn-50:  #FFFBEB;   /* background tint */
--warn-100: #FEF3C7;   /* light badge bg */
--warn-500: #D97706;   /* text, icons */
--warn-600: #B45309;   /* darker text on light bg */
--warn-700: #92400E;   /* high-contrast text */

/* Info / Neutral signal — slate blue (not purple) */
--info-50:  #F0F4FF;   /* background tint */
--info-100: #DBEAFE;   /* light badge bg */
--info-500: #3B6FC2;   /* text, icons — desaturated, not electric */
--info-600: #2D5BA0;   /* darker text on light bg */
--info-700: #1E4278;   /* high-contrast text */
```

### Interactive / Focus

```css
/* Primary action — steel blue, not purple */
--action-500: #2563EB;  /* default button bg */
--action-600: #1D4ED8;  /* hover */
--action-700: #1E40AF;  /* active/pressed */
--action-100: #DBEAFE;  /* selected row bg, focus ring bg */

/* Focus ring */
--focus-ring: 0 0 0 2px var(--neutral-50), 0 0 0 4px var(--action-500);
```

### Surface & elevation

```css
--surface-primary:   #FFFFFF;   /* cards, panels, inputs */
--surface-secondary: #FAFAF8;   /* page bg, nested cards */
--surface-elevated:  #FFFFFF;   /* modals, floating panels */

--shadow-sm: 0 1px 2px rgba(20, 20, 18, 0.05);
--shadow-md: 0 2px 8px rgba(20, 20, 18, 0.08);
--shadow-lg: 0 4px 16px rgba(20, 20, 18, 0.12);

--border-default: 1px solid var(--neutral-200);
--border-subtle:  1px solid rgba(20, 20, 18, 0.06);
```

### Color usage rules

1. **Never use color alone to convey meaning.** Always pair with an icon, label, or pattern.
2. **Semantic colors only in data regions.** The chrome (header, sidebar, containers) is neutral.
3. **No gradients except the progress bar.** Flat color everywhere else.
4. **Status dot + text label together.** The dot is for scanability; the text is for accessibility.

---

## 5. Spacing System

### Base unit: 4px

```css
--space-0:  0;
--space-1:  4px;    /* 0.25rem — tight internal padding */
--space-2:  8px;    /* 0.5rem  — between related elements */
--space-3:  12px;   /* 0.75rem — card internal padding, input padding */
--space-4:  16px;   /* 1rem    — between cards, standard gap */
--space-5:  20px;   /* 1.25rem — section padding */
--space-6:  24px;   /* 1.5rem  — between sections */
--space-8:  32px;   /* 2rem    — major section breaks */
--space-10: 40px;   /* 2.5rem  — page-level padding */
--space-12: 48px;   /* 3rem    — hero/header vertical spacing */
```

### Usage rules

| Context | Spacing |
|---------|---------|
| Inside a card (padding) | `--space-3` (12px) on compact cards, `--space-4` (16px) on standard cards |
| Between cards in a grid | `--space-2` (8px) compact, `--space-3` (12px) standard |
| Between a heading and its content | `--space-2` (8px) |
| Between sections | `--space-6` (24px) |
| Page edge padding | `--space-5` (20px) on mobile, `--space-8` (32px) on desktop |
| Table cell padding | `--space-2` (8px) vertical, `--space-3` (12px) horizontal |

---

## 6. Layout

### Grid system

```css
/* Page container */
.page {
  max-width: 1440px;
  margin: 0 auto;
  padding: 0 var(--space-8);
}

/* Main content area (single-image view) */
.main-content {
  max-width: 1100px;
  margin: 0 auto;
}

/* Future: three-panel layout for batch mode */
.layout-batch {
  display: grid;
  grid-template-columns: 280px 1fr 360px;
  grid-template-rows: auto 1fr;
  gap: 0;
  height: 100vh;
}

.layout-batch .queue-panel {
  border-right: var(--border-default);
  overflow-y: auto;
}

.layout-batch .detail-panel {
  border-left: var(--border-default);
  overflow-y: auto;
}
```

### Breakpoints

```css
/* Mobile-first breakpoints */
--bp-sm:  640px;   /* small phones → larger phones */
--bp-md:  768px;   /* phones → tablets */
--bp-lg:  1024px;  /* tablets → small desktops */
--bp-xl:  1280px;  /* small desktops → standard desktops */
--bp-2xl: 1440px;  /* standard → wide desktops */
```

| Breakpoint | Layout behavior |
|------------|----------------|
| < 640px | Single column. Upload stacks. Table becomes card list. |
| 640-767px | Single column, wider cards. Table still cards. |
| 768-1023px | Upload side-by-side. Table scrolls horizontally. Detail panel below. |
| 1024-1279px | Full table visible. Detail panel below or slide-out right. |
| 1280+ | Three-panel layout available. Everything visible. |

### Content widths

```css
--width-prose: 65ch;     /* max-width for reading text (findings, reports) */
--width-table: 100%;     /* tables stretch to container */
--width-input: 240px;    /* form inputs in side panel */
```

---

## 7. Component Patterns

### 7.1 Verdict Card (the "answer")

The single most important component. Appears at the top of results. Synthesizes
all signals into one weighted verdict.

```css
.verdict-card {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-5);
  border-radius: 8px;
  border: 2px solid var(--neutral-200);
  background: var(--surface-primary);
}

.verdict-card[data-verdict="pass"] {
  border-color: var(--pass-500);
  background: var(--pass-50);
}

.verdict-card[data-verdict="fail"] {
  border-color: var(--fail-500);
  background: var(--fail-50);
}

.verdict-card[data-verdict="conditional"] {
  border-color: var(--warn-500);
  background: var(--warn-50);
}

.verdict-score {
  font-size: var(--text-2xl);
  font-weight: 800;
  font-family: var(--font-mono);
  line-height: 1;
  min-width: 72px;
  text-align: center;
}

.verdict-summary {
  font-size: var(--text-base);
  color: var(--neutral-700);
  line-height: 1.5;
}

.verdict-model-agreement {
  font-size: var(--text-xs);
  color: var(--neutral-500);
  font-family: var(--font-mono);
}
```

Structure:
```
┌─────────────────────────────────────────────────┐
│  ┌──────┐                                       │
│  │  87  │  CONDITIONAL PASS                     │
│  │      │  3/8 models flag thin line issues in   │
│  └──────┘  text region. SW gate TL-1 confirms.  │
│            6/8 models agree · consensus: 84%     │
└─────────────────────────────────────────────────┘
```

### 7.2 Status Badge

```css
.badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: var(--text-xs);
  font-weight: 600;
  line-height: 1.5;
}

.badge-pass { background: var(--pass-100); color: var(--pass-700); }
.badge-fail { background: var(--fail-100); color: var(--fail-700); }
.badge-warn { background: var(--warn-100); color: var(--warn-700); }
.badge-info { background: var(--info-100); color: var(--info-700); }
.badge-neutral { background: var(--neutral-150); color: var(--neutral-600); }
```

### 7.3 Gate Card (Software Gate Result)

Compact card showing one gate result. Used in a grid.

```css
.gate-card {
  padding: var(--space-2) var(--space-3);
  border-radius: 6px;
  background: var(--surface-primary);
  border: var(--border-default);
  border-left: 3px solid var(--neutral-300);
  font-size: var(--text-sm);
}

.gate-card[data-severity="none"]     { border-left-color: var(--pass-500); }
.gate-card[data-severity="low"]      { border-left-color: var(--warn-500); }
.gate-card[data-severity="medium"]   { border-left-color: var(--warn-500); }
.gate-card[data-severity="high"]     { border-left-color: var(--fail-500); }
.gate-card[data-severity="critical"] { border-left-color: var(--fail-500); }
.gate-card[data-severity="info"]     { border-left-color: var(--info-500); }

.gate-id {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-weight: 700;
  color: var(--neutral-500);
}

.gate-name {
  font-weight: 600;
  font-size: var(--text-sm);
  color: var(--neutral-800);
  margin: 2px 0;
}

.gate-detail {
  font-size: var(--text-xs);
  color: var(--neutral-500);
  line-height: 1.4;
}
```

### 7.4 Severity Bar (compact gate summary)

Replaces the full gate grid when space is limited or as a summary.

```css
.severity-bar {
  display: flex;
  gap: 2px;
  height: 24px;
  border-radius: 4px;
  overflow: hidden;
}

.severity-bar .segment {
  flex: 1;
  position: relative;
  cursor: pointer;
  transition: opacity 0.15s;
}

.severity-bar .segment:hover {
  opacity: 0.8;
}

.severity-bar .segment[data-status="pass"]    { background: var(--pass-500); }
.severity-bar .segment[data-status="fail"]    { background: var(--fail-500); }
.severity-bar .segment[data-status="warning"] { background: var(--warn-500); }
.severity-bar .segment[data-status="info"]    { background: var(--info-500); }
.severity-bar .segment[data-status="skip"]    { background: var(--neutral-200); }

/* Tooltip on hover shows gate ID + name */
.severity-bar .segment::after {
  content: attr(data-label);
  position: absolute;
  bottom: calc(100% + 4px);
  left: 50%;
  transform: translateX(-50%);
  font-size: 10px;
  white-space: nowrap;
  background: var(--neutral-900);
  color: white;
  padding: 2px 6px;
  border-radius: 3px;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.15s;
}

.severity-bar .segment:hover::after {
  opacity: 1;
}
```

### 7.5 Comparison Table

```css
.cmp-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-sm);
}

.cmp-table th {
  background: var(--neutral-100);
  font-size: var(--text-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--neutral-500);
  padding: var(--space-2) var(--space-3);
  text-align: center;
  border-bottom: 2px solid var(--neutral-200);
  position: sticky;
  top: 0;
  z-index: 1;
}

.cmp-table th:first-child {
  text-align: left;
  min-width: 160px;
}

.cmp-table td {
  padding: var(--space-2) var(--space-3);
  text-align: center;
  border-bottom: var(--border-subtle);
  white-space: nowrap;
}

.cmp-table td:first-child {
  text-align: left;
  font-weight: 600;
  font-size: var(--text-base);
}

.cmp-table tr {
  cursor: pointer;
  transition: background 0.1s;
}

.cmp-table tr:hover {
  background: var(--neutral-150);
}

.cmp-table tr[aria-selected="true"] {
  background: var(--action-100);
}

/* Score cell styling */
.score-hi { color: var(--pass-600); font-weight: 700; }
.score-md { color: var(--warn-600); font-weight: 700; }
.score-lo { color: var(--fail-600); font-weight: 700; }
```

### 7.6 Status Dot

```css
.dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}

.dot-pass { background: var(--pass-500); }
.dot-fail { background: var(--fail-500); }
.dot-warn { background: var(--warn-500); }
.dot-info { background: var(--info-500); }
.dot-skip { background: var(--neutral-300); }
.dot-err  { background: var(--neutral-300); border: 1px dashed var(--fail-500); }
```

### 7.7 Upload Zone

```css
.upload-zone {
  border: 2px dashed var(--neutral-300);
  border-radius: 8px;
  padding: var(--space-8) var(--space-5);
  text-align: center;
  cursor: pointer;
  background: var(--surface-primary);
  transition: border-color 0.15s, background 0.15s;
}

.upload-zone:hover,
.upload-zone:focus-within {
  border-color: var(--action-500);
  background: var(--action-100);
}

.upload-zone[data-state="dragover"] {
  border-color: var(--action-500);
  background: var(--action-100);
  border-style: solid;
}

.upload-zone[data-state="has-file"] {
  border-style: solid;
  border-color: var(--pass-500);
  background: var(--pass-50);
  padding: var(--space-3);
}

.upload-zone .upload-icon {
  color: var(--neutral-400);
  margin-bottom: var(--space-2);
}

.upload-zone .upload-title {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--neutral-700);
}

.upload-zone .upload-hint {
  font-size: var(--text-sm);
  color: var(--neutral-500);
  margin-top: var(--space-1);
}
```

### 7.8 Button

```css
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border: none;
  border-radius: 6px;
  font-family: var(--font-sans);
  font-size: var(--text-md);
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s, box-shadow 0.15s;
  line-height: 1;
}

.btn-primary {
  background: var(--action-500);
  color: white;
}

.btn-primary:hover:not(:disabled) {
  background: var(--action-600);
  box-shadow: var(--shadow-sm);
}

.btn-primary:active:not(:disabled) {
  background: var(--action-700);
}

.btn-primary:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.btn-secondary {
  background: var(--surface-primary);
  color: var(--neutral-700);
  border: var(--border-default);
}

.btn-secondary:hover:not(:disabled) {
  background: var(--neutral-100);
}

/* Full-width variant */
.btn-block {
  width: 100%;
}
```

### 7.9 Progress Indicator

```css
/* Determinate progress bar */
.progress-bar {
  height: 4px;
  background: var(--neutral-200);
  border-radius: 2px;
  overflow: hidden;
}

.progress-bar .fill {
  height: 100%;
  background: var(--action-500);
  transition: width 0.4s ease;
  border-radius: 2px;
}

/* Step progress (for streaming pipeline results) */
.pipeline-steps {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--text-xs);
}

.pipeline-step {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 4px;
  color: var(--neutral-500);
}

.pipeline-step[data-state="pending"]  { color: var(--neutral-400); }
.pipeline-step[data-state="running"]  { color: var(--action-500); font-weight: 600; }
.pipeline-step[data-state="done"]     { color: var(--pass-500); }
.pipeline-step[data-state="error"]    { color: var(--fail-500); }

/* Spinner for in-progress states */
@keyframes spin {
  to { transform: rotate(360deg); }
}

.spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--neutral-200);
  border-top-color: var(--action-500);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

/* Skeleton loading for cards */
@keyframes shimmer {
  0%   { background-position: -200px 0; }
  100% { background-position: 200px 0; }
}

.skeleton {
  background: linear-gradient(
    90deg,
    var(--neutral-100) 0%,
    var(--neutral-150) 50%,
    var(--neutral-100) 100%
  );
  background-size: 400px 100%;
  animation: shimmer 1.5s infinite;
  border-radius: 4px;
}

.skeleton-line {
  height: 12px;
  margin-bottom: 8px;
  width: 80%;
}

.skeleton-line:last-child {
  width: 60%;
  margin-bottom: 0;
}
```

### 7.10 Detail Panel

```css
.detail-panel {
  background: var(--surface-primary);
  border: var(--border-default);
  border-radius: 8px;
  overflow: hidden;
}

.detail-header {
  padding: var(--space-4);
  border-bottom: var(--border-default);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.detail-header h3 {
  font-size: var(--text-lg);
  font-weight: 700;
}

.detail-body {
  padding: var(--space-4);
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--space-3);
}

.finding-card {
  padding: var(--space-3);
  border-radius: 6px;
  border: var(--border-default);
  font-size: var(--text-sm);
}

.finding-card[data-status="pass"] {
  background: var(--pass-50);
  border-color: var(--pass-500);
}

.finding-card[data-status="fail"] {
  background: var(--fail-50);
  border-color: var(--fail-500);
}

.finding-card h4 {
  font-size: var(--text-xs);
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  margin-bottom: var(--space-1);
}

.finding-list {
  color: var(--neutral-600);
  line-height: 1.5;
  margin-top: var(--space-1);
}
```

### 7.11 Form Controls

```css
.input {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  border: var(--border-default);
  border-radius: 6px;
  font-family: var(--font-sans);
  font-size: var(--text-sm);
  color: var(--neutral-800);
  background: var(--surface-primary);
  transition: border-color 0.15s;
}

.input:focus {
  outline: none;
  border-color: var(--action-500);
  box-shadow: var(--focus-ring);
}

.form-label {
  display: block;
  font-size: var(--text-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--neutral-500);
  margin-bottom: var(--space-1);
}

/* Toggle switch */
.toggle {
  position: relative;
  width: 36px;
  height: 20px;
  flex-shrink: 0;
}

.toggle input {
  opacity: 0;
  width: 0;
  height: 0;
}

.toggle .track {
  position: absolute;
  inset: 0;
  background: var(--neutral-300);
  border-radius: 10px;
  cursor: pointer;
  transition: background 0.2s;
}

.toggle .track::before {
  content: '';
  position: absolute;
  width: 16px;
  height: 16px;
  left: 2px;
  bottom: 2px;
  background: white;
  border-radius: 50%;
  transition: transform 0.2s;
  box-shadow: var(--shadow-sm);
}

.toggle input:checked + .track {
  background: var(--pass-500);
}

.toggle input:checked + .track::before {
  transform: translateX(16px);
}

.toggle input:focus-visible + .track {
  box-shadow: var(--focus-ring);
}
```

---

## 8. Motion & Transitions

### Principles

1. **Functional, not decorative.** Motion communicates state changes, not personality.
2. **Fast.** Max 200ms for micro-interactions. 300ms for panel transitions.
3. **No bounce, no overshoot.** Linear or ease-out only. This is a precision tool.

### Timing tokens

```css
--duration-instant: 0ms;      /* state toggles with no visual transition */
--duration-fast:    100ms;     /* hover states, focus rings */
--duration-normal:  150ms;     /* color changes, opacity */
--duration-slow:    250ms;     /* panel slide, accordion expand */
--duration-enter:   300ms;     /* new elements appearing */

--ease-out:   cubic-bezier(0.0, 0.0, 0.2, 1);
--ease-in-out: cubic-bezier(0.4, 0.0, 0.2, 1);
```

### Defined transitions

```css
/* Result rows appearing (streaming from pipeline) */
.model-row-enter {
  animation: fadeSlideIn var(--duration-enter) var(--ease-out);
}

@keyframes fadeSlideIn {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* Detail panel expand */
.panel-expand {
  animation: expandDown var(--duration-slow) var(--ease-out);
  transform-origin: top;
}

@keyframes expandDown {
  from {
    opacity: 0;
    max-height: 0;
  }
  to {
    opacity: 1;
    max-height: 800px;
  }
}

/* BG removal reveal */
.bg-reveal {
  animation: fadeIn var(--duration-enter) var(--ease-out);
}

@keyframes fadeIn {
  from { opacity: 0; }
  to   { opacity: 1; }
}
```

### Loading states

| State | Treatment |
|-------|-----------|
| Upload processing | Progress bar (determinate if possible, indeterminate fallback) |
| BG removal in progress | Shimmer skeleton on the right side of the before/after comparison |
| SW gates computing | Gates cascade in one by one, 50ms stagger — they're fast enough for this |
| VLM model waiting | Table row shows spinner + "Analyzing..." in the verdict column |
| VLM model complete | Row content fades in with `fadeSlideIn`, status dots appear |
| Full pipeline complete | Verdict card slides in from above with slight scale-up |

---

## 9. Accessibility Requirements

```css
/* Minimum contrast: 4.5:1 for text, 3:1 for large text and UI components */

/* Focus visible for keyboard navigation */
:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

/* Reduced motion */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}

/* Status dots get aria-label and are never the sole indicator */
.dot[role="img"] {
  /* Always accompanied by text label */
}
```

---

## 10. Border Radius Scale

```css
--radius-sm:  4px;   /* badges, small pills */
--radius-md:  6px;   /* inputs, buttons, small cards */
--radius-lg:  8px;   /* cards, panels, containers */
--radius-full: 9999px; /* dots, toggles, avatars */
```

Not 10px everywhere. Smaller elements get tighter radii; larger containers
get 8px max. This prevents the "everything is a bubble" look.

---

## 11. Icon System

No icon library required at this stage. Use inline SVGs with these constraints:

```css
.icon {
  width: 16px;
  height: 16px;
  stroke: currentColor;
  stroke-width: 1.5;
  fill: none;
  flex-shrink: 0;
}

.icon-sm { width: 12px; height: 12px; }
.icon-lg { width: 20px; height: 20px; }
```

Keep icons to a minimum. This is a data tool. Icons for: upload, check, x, alert-triangle,
info, chevron-down, external-link, download. That's the full set needed.

---

## 12. Dark Mode (future consideration)

Not implemented yet, but the token system is ready for it. When implemented:

```css
@media (prefers-color-scheme: dark) {
  :root {
    --neutral-50:  #141412;
    --neutral-100: #1C1C1A;
    --neutral-900: #FAFAF8;
    /* ... invert the neutral scale ... */

    --surface-primary:   #1C1C1A;
    --surface-secondary: #141412;
  }
}
```

Semantic colors (pass/fail/warn/info) do NOT invert — they stay the same hues
but shift to lighter tints for text-on-dark-bg readability.
