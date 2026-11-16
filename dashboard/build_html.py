"""Build the redesigned index.html by combining new HTML/CSS with original JS."""
import pathlib

HERE = pathlib.Path(__file__).parent
BACKUP = HERE / "index.html.bak"
OUT    = HERE / "index.html"

# Load original JS section (from <script> to </html>)
with open(BACKUP, encoding="utf-8") as f:
    lines = f.readlines()

js_start = next(i for i, l in enumerate(lines) if l.strip() == "<script>")
js_section = "".join(lines[js_start:])  # includes <script> ... </html>

NEW_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Example Outreach</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet" />
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      /* Brand */
      --navy:         #0B1F3B;
      --navy-mid:     #142d52;
      --navy-light:   rgba(11,31,59,0.06);
      --gold:         #E6C87A;
      --gold-light:   rgba(230,200,122,0.14);

      /* Backgrounds */
      --bg:           #FFFFFF;
      --surface:      #F9FAFB;
      --surface2:     #F3F4F6;

      /* Borders */
      --border:       #E5E7EB;
      --border-mid:   #D1D5DB;

      /* Semantic */
      --green:        #059669;
      --green-bg:     rgba(5,150,105,0.08);
      --yellow:       #D97706;
      --yellow-bg:    rgba(217,119,6,0.08);
      --red:          #DC2626;
      --red-bg:       rgba(220,38,38,0.08);
      --blue:         #2563EB;
      --blue-bg:      rgba(37,99,235,0.08);

      /* Text */
      --text:         #111827;
      --text-dim:     #374151;
      --text-muted:   #6B7280;

      /* Backward-compat aliases used in inline JS-generated styles */
      --accent:       #0B1F3B;
      --accent-hover: #142d52;

      /* Type scale (also used in inline JS styles) */
      --text-xs:   0.75rem;
      --text-sm:   0.8125rem;
      --text-base: 0.9375rem;
      --text-lg:   1.0625rem;
      --text-xl:   1.25rem;

      /* Spacing (backward compat) */
      --space-1: 0.25rem; --space-2: 0.5rem; --space-3: 0.75rem;
      --space-4: 1rem;    --space-6: 1.5rem; --space-8: 2rem;

      /* Sidebar */
      --sidebar-w: 220px;

      /* Radii */
      --r-xs:   3px;
      --r-sm:   6px;
      --r:      10px;
      --r-lg:   14px;
      --r-pill: 9999px;

      /* Motion */
      --ease:     cubic-bezier(0.16,1,0.3,1);
      --dur-fast: 130ms;
      --dur:      210ms;

      /* ease-out alias */
      --ease-out:       cubic-bezier(0.16,1,0.3,1);
      --duration-fast:  130ms;
      --duration-base:  210ms;
    }

    html { font-size: 16px; }

    body {
      font-family: 'IBM Plex Sans', system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      height: 100vh;
      overflow: hidden;
      display: flex;
      -webkit-font-smoothing: antialiased;
      line-height: 1.5;
    }

    img, video { max-width: 100%; display: block; }
    a { color: inherit; }
    code {
      font-family: 'IBM Plex Mono', monospace;
      font-size: 0.84em;
      background: var(--surface2);
      padding: 1px 5px;
      border-radius: var(--r-xs);
    }

    /* ───────────── SIDEBAR ───────────── */
    .sidebar {
      width: var(--sidebar-w);
      background: var(--navy);
      display: flex;
      flex-direction: column;
      flex-shrink: 0;
      height: 100vh;
      overflow: hidden;
    }

    .sidebar-brand {
      padding: 20px 18px 18px;
      display: flex;
      align-items: center;
      gap: 9px;
      flex-shrink: 0;
      border-bottom: 1px solid rgba(255,255,255,0.07);
    }

    .sidebar-brand img {
      width: 90px;
      height: auto;
      filter: brightness(0) invert(1);
      opacity: 0.92;
    }

    .sidebar-badge {
      font-size: 9px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--gold);
      background: rgba(230,200,122,0.1);
      border: 1px solid rgba(230,200,122,0.2);
      padding: 2px 7px;
      border-radius: var(--r-pill);
      white-space: nowrap;
    }

    .sidebar-nav {
      flex: 1;
      padding: 12px 8px;
      display: flex;
      flex-direction: column;
      gap: 1px;
      overflow-y: auto;
    }

    /* .header-tab is used by JS for tab switching  -  keep class name */
    .header-tab {
      display: flex;
      align-items: center;
      gap: 9px;
      padding: 8px 10px;
      border-radius: var(--r-sm);
      font-size: 0.8125rem;
      font-weight: 500;
      color: rgba(255,255,255,0.55);
      cursor: pointer;
      border: none;
      background: transparent;
      transition: background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease);
      user-select: none;
      letter-spacing: 0.01em;
      position: relative;
    }

    .header-tab:hover {
      background: rgba(255,255,255,0.07);
      color: rgba(255,255,255,0.85);
    }

    /* .active is toggled by JS */
    .header-tab.active {
      background: rgba(255,255,255,0.1);
      color: #fff;
      font-weight: 600;
    }

    .header-tab.active::before {
      content: '';
      position: absolute;
      left: 0;
      top: 5px;
      bottom: 5px;
      width: 3px;
      background: var(--gold);
      border-radius: 0 2px 2px 0;
    }

    .nav-icon {
      width: 15px;
      height: 15px;
      opacity: 0.65;
      flex-shrink: 0;
    }

    .header-tab.active .nav-icon { opacity: 1; }

    .sidebar-divider {
      height: 1px;
      background: rgba(255,255,255,0.07);
      margin: 6px 8px;
    }

    .sidebar-footer {
      padding: 8px 8px 14px;
      flex-shrink: 0;
      border-top: 1px solid rgba(255,255,255,0.07);
    }

    /* ───────────── MAIN ───────────── */
    .main {
      flex: 1;
      overflow-y: auto;
      padding: 26px 30px 40px;
      background: var(--bg);
    }

    /* ───────────── ACCORDION (section cards) ───────────── */
    .accordion {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: var(--r);
      margin-bottom: 10px;
      overflow: hidden;
      box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }

    .accordion-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 13px 18px;
      cursor: pointer;
      user-select: none;
      transition: background var(--dur-fast) var(--ease);
    }

    .accordion-header:hover { background: var(--surface); }

    .accordion-header-left {
      display: flex;
      align-items: center;
      gap: 11px;
    }

    .acc-icon {
      width: 28px;
      height: 28px;
      background: var(--navy-light);
      border-radius: 6px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 13px;
      flex-shrink: 0;
      color: var(--navy);
    }

    .acc-icon svg {
      width: 14px;
      height: 14px;
    }

    /* Keep .accordion-icon for any legacy refs */
    .accordion-icon { font-size: 14px; }

    .accordion-title {
      font-size: 0.8125rem;
      font-weight: 600;
      color: var(--text);
      letter-spacing: 0.01em;
    }

    .accordion-meta {
      font-size: var(--text-xs);
      color: var(--text-muted);
      margin-left: 6px;
    }

    /* .accordion-chevron and .open are toggled by JS */
    .accordion-chevron {
      font-size: 10px;
      color: var(--text-muted);
      transition: transform var(--dur) var(--ease);
      width: 20px;
      height: 20px;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .accordion-chevron.open { transform: rotate(180deg); }

    /* .accordion-body and .open are toggled by JS */
    .accordion-body {
      border-top: 1px solid var(--border);
      padding: 20px;
      display: none;
    }

    .accordion-body.open { display: block; }

    /* ───────────── BUTTONS ───────────── */
    .btn {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 7px 14px;
      border-radius: var(--r-sm);
      font-size: 0.8125rem;
      font-weight: 500;
      cursor: pointer;
      border: 1px solid transparent;
      font-family: inherit;
      letter-spacing: 0.01em;
      transition: background var(--dur-fast) var(--ease),
                  color var(--dur-fast) var(--ease),
                  border-color var(--dur-fast) var(--ease),
                  opacity var(--dur-fast);
      white-space: nowrap;
    }

    .btn:disabled { opacity: 0.42; cursor: not-allowed; pointer-events: none; }

    .btn-primary { background: var(--navy); color: #fff; border-color: var(--navy); }
    .btn-primary:hover { background: var(--navy-mid); border-color: var(--navy-mid); }

    .btn-outline {
      background: transparent;
      color: var(--text-dim);
      border-color: var(--border-mid);
    }
    .btn-outline:hover { background: var(--surface2); color: var(--text); }

    .btn-green  { background: var(--green); color: #fff; border-color: var(--green); }
    .btn-green:hover { opacity: 0.88; }

    .btn-red    { background: var(--red); color: #fff; border-color: var(--red); }
    .btn-red:hover { opacity: 0.88; }

    .btn-secondary {
      background: var(--surface);
      color: var(--text-dim);
      border-color: var(--border);
    }
    .btn-secondary:hover { background: var(--surface2); }

    .btn-sm { padding: 4px 10px; font-size: 0.75rem; }
    .btn-lg { padding: 10px 20px; font-size: var(--text-base); }

    /* ───────────── TABLES ───────────── */
    .table-wrap {
      border: 1px solid var(--border);
      border-radius: var(--r-sm);
      overflow: hidden;
    }

    table { width: 100%; border-collapse: collapse; }

    th {
      padding: 8px 13px;
      text-align: left;
      font-size: 0.7rem;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      color: var(--text-muted);
      border-bottom: 1px solid var(--border);
      font-weight: 500;
      background: var(--surface);
    }

    td {
      padding: 9px 13px;
      font-size: 0.8125rem;
      border-bottom: 1px solid var(--border);
      color: var(--text);
    }

    tr:last-child td { border-bottom: none; }
    tr:hover td { background: var(--surface); }

    /* ───────────── FORMS ───────────── */
    .form-group { margin-bottom: 14px; }

    label {
      display: block;
      font-size: 0.7rem;
      color: var(--text-muted);
      margin-bottom: 5px;
      font-weight: 500;
      letter-spacing: 0.05em;
      text-transform: uppercase;
    }

    input, select, textarea {
      width: 100%;
      background: var(--bg);
      border: 1px solid var(--border-mid);
      border-radius: var(--r-sm);
      padding: 7px 10px;
      color: var(--text);
      font-size: 0.8125rem;
      outline: none;
      transition: border-color var(--dur-fast) var(--ease),
                  box-shadow var(--dur-fast) var(--ease);
      font-family: inherit;
    }

    input:focus, select:focus, textarea:focus {
      border-color: var(--navy);
      box-shadow: 0 0 0 3px rgba(11,31,59,0.08);
    }

    textarea { resize: vertical; min-height: 80px; }
    .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }

    /* .input alias (used in Events tab) */
    .input {
      background: var(--bg);
      border: 1px solid var(--border-mid);
      border-radius: var(--r-sm);
      padding: 7px 10px;
      color: var(--text);
      font-size: 0.8125rem;
      outline: none;
      transition: border-color var(--dur-fast) var(--ease),
                  box-shadow var(--dur-fast) var(--ease);
      font-family: inherit;
    }
    .input:focus {
      border-color: var(--navy);
      box-shadow: 0 0 0 3px rgba(11,31,59,0.08);
    }

    /* ───────────── BADGES ───────────── */
    .badge {
      display: inline-flex;
      align-items: center;
      padding: 2px 8px;
      border-radius: var(--r-pill);
      font-size: 11px;
      font-weight: 500;
      letter-spacing: 0.02em;
      white-space: nowrap;
    }

    .badge-green  { background: var(--green-bg);  color: var(--green); }
    .badge-yellow { background: var(--yellow-bg); color: var(--yellow); }
    .badge-red    { background: var(--red-bg);    color: var(--red); }
    .badge-blue   { background: var(--blue-bg);   color: var(--blue); }
    .badge-gray   { background: var(--surface2);  color: var(--text-muted); }

    /* ───────────── STAT CARDS ───────────── */
    .cards {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
      gap: 10px;
      margin-bottom: 16px;
    }

    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--r-sm);
      padding: 14px 16px;
    }

    .card-label {
      font-size: 0.7rem;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      color: var(--text-muted);
      margin-bottom: 6px;
      font-weight: 500;
    }

    .card-value { font-size: 1.55rem; font-weight: 600; letter-spacing: -0.02em; }
    .card-sub   { font-size: var(--text-xs); color: var(--text-muted); margin-top: 3px; }

    /* ───────────── PANEL ───────────── */
    .panel {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--r-sm);
      padding: 16px;
      margin-bottom: 12px;
    }

    .panel-title {
      font-size: 0.7rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      color: var(--text-muted);
      margin-bottom: 12px;
    }

    /* ───────────── TAG INPUT ───────────── */
    .tags-input-wrap {
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
      background: var(--bg);
      border: 1px solid var(--border-mid);
      border-radius: var(--r-sm);
      padding: 5px 8px;
      min-height: 36px;
      align-items: center;
      cursor: text;
      transition: border-color var(--dur-fast) var(--ease),
                  box-shadow var(--dur-fast) var(--ease);
    }

    .tags-input-wrap:focus-within {
      border-color: var(--navy);
      box-shadow: 0 0 0 3px rgba(11,31,59,0.08);
    }

    .tag {
      background: var(--navy-light);
      color: var(--navy);
      padding: 2px 8px;
      border-radius: var(--r-pill);
      font-size: 0.7rem;
      font-weight: 500;
      display: flex;
      align-items: center;
      gap: 4px;
    }

    .tag-remove { cursor: pointer; opacity: 0.4; }
    .tag-remove:hover { opacity: 1; }

    .tag-input {
      border: none;
      background: none;
      padding: 0;
      font-size: 0.8125rem;
      color: var(--text);
      outline: none;
      min-width: 80px;
      flex: 1;
    }

    /* ───────────── SUB-TOGGLE ───────────── */
    .sub-toggle {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-size: 0.75rem;
      color: var(--text-muted);
      cursor: pointer;
      padding: 4px 0;
      border: none;
      background: none;
      user-select: none;
      letter-spacing: 0.02em;
      font-weight: 500;
      transition: color var(--dur-fast);
    }

    .sub-toggle:hover { color: var(--navy); }

    /* ───────────── EMAIL PREVIEW ───────────── */
    .email-preview {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--r-sm);
      padding: 16px;
      margin-bottom: 10px;
    }

    .email-step-label {
      font-size: 0.7rem;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.07em;
      margin-bottom: 5px;
      font-weight: 500;
    }

    .email-subject { font-size: 0.8125rem; font-weight: 600; margin-bottom: 5px; }
    .email-body {
      font-size: 0.8125rem;
      color: var(--text-dim);
      line-height: 1.65;
      white-space: pre-wrap;
    }

    /* ───────────── CAMPAIGN BUILDER STEPS ───────────── */
    .steps {
      display: flex;
      gap: 3px;
      margin-bottom: 20px;
      padding: 4px;
      background: var(--surface2);
      border-radius: var(--r-sm);
    }

    .step {
      flex: 1;
      padding: 6px 10px;
      font-size: 0.75rem;
      color: var(--text-muted);
      cursor: pointer;
      border-radius: 4px;
      transition: all var(--dur-fast) var(--ease);
      text-align: center;
      font-weight: 500;
      letter-spacing: 0.01em;
    }

    .step:hover { background: var(--bg); color: var(--text); }
    .step.active { background: var(--navy); color: #fff; font-weight: 600; }

    /* ───────────── LOADING ───────────── */
    .loading {
      display: inline-block;
      width: 14px;
      height: 14px;
      border: 2px solid var(--border);
      border-top-color: var(--navy);
      border-radius: 50%;
      animation: spin 0.65s linear infinite;
    }

    @keyframes spin { to { transform: rotate(360deg); } }

    /* ───────────── UTILITIES ───────────── */
    .hidden { display: none !important; }

    input[type=checkbox] {
      width: auto;
      cursor: pointer;
      accent-color: var(--navy);
    }

    /* ───────────── TOAST ───────────── */
    .toast {
      position: fixed;
      bottom: 22px;
      left: calc(var(--sidebar-w) + 22px);
      background: var(--navy);
      color: #fff;
      border-radius: var(--r-sm);
      padding: 10px 16px;
      font-size: 0.8125rem;
      font-weight: 500;
      z-index: 2000;
      display: none;
      max-width: 380px;
      box-shadow: 0 4px 18px rgba(0,0,0,0.2);
    }

    .toast.show    { display: block; }
    .toast.success { background: var(--navy); border-left: 3px solid var(--gold); }
    .toast.error   { background: #1c0a0a; border-left: 3px solid var(--red); }
    .toast.warning { background: #1a1200; border-left: 3px solid var(--gold); }

    /* ───────────── VOICE BTN ───────────── */
    #voice-btn {
      position: fixed;
      bottom: 22px;
      right: 22px;
      width: 40px;
      height: 40px;
      border-radius: var(--r-pill);
      background: var(--navy);
      color: #fff;
      font-size: 15px;
      border: none;
      cursor: pointer;
      z-index: 1500;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background var(--dur-fast) var(--ease), box-shadow var(--dur-fast);
      box-shadow: 0 2px 12px rgba(11,31,59,0.28);
    }

    #voice-btn:hover { background: var(--navy-mid); }
    #voice-btn.listening { background: var(--red); }

    /* ───────────── VOICE OVERLAY ───────────── */
    #voice-overlay {
      position: fixed;
      bottom: 72px;
      right: 22px;
      background: var(--navy);
      color: #fff;
      border-radius: var(--r);
      padding: 13px 17px;
      font-size: 0.8125rem;
      z-index: 1500;
      display: none;
      width: 280px;
      box-shadow: 0 4px 20px rgba(0,0,0,0.22);
    }

    #voice-overlay.show { display: block; }

    #voice-transcript {
      font-style: italic;
      color: rgba(255,255,255,0.55);
      margin-bottom: 5px;
      font-size: 0.75rem;
      min-height: 16px;
    }

    #voice-action {
      font-weight: 500;
      color: var(--gold);
      font-size: 0.75rem;
    }

    /* ───────────── SECTION ROW ───────────── */
    .section-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 12px;
    }

    .section-row-title {
      font-size: 0.8125rem;
      font-weight: 600;
      color: var(--text-dim);
    }

    /* ───────────── MODAL ───────────── */
    .modal-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(11,31,59,0.42);
      z-index: 100;
      display: flex;
      align-items: center;
      justify-content: center;
      backdrop-filter: blur(2px);
    }

    .modal-box {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: var(--r);
      padding: 28px;
      box-shadow: 0 8px 32px rgba(11,31,59,0.16);
    }

    /* ───────────── TIMELINE ───────────── */
    .timeline-step {
      display: flex;
      align-items: flex-start;
      gap: 12px;
      padding: 10px 0;
      border-bottom: 1px solid var(--border);
    }

    .timeline-step:last-child { border-bottom: none; }

    .timeline-dot {
      width: 8px;
      height: 8px;
      background: var(--navy);
      border-radius: 50%;
      margin-top: 5px;
      flex-shrink: 0;
    }

    /* ───────────── WORKFLOW GRID ───────────── */
    .workflow-grid { display: grid; grid-template-columns: 260px 1fr; gap: 18px; }

    /* ───────────── INLINE FILTER (collapse) ───────────── */
    #leads-filter-body { display: none; }
    #leads-filter-body.open { display: block; }
  </style>
</head>
<body>

<!-- ── Sidebar ── -->
<aside class="sidebar">
  <div class="sidebar-brand">
    <img src="/assets/portfolio-logo.svg" alt="Example" />
    <span class="sidebar-badge">Outreach</span>
  </div>

  <nav class="sidebar-nav">
    <div class="header-tab active" data-tab="outreach">
      <svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6">
        <rect x="1" y="3" width="14" height="10" rx="2"/>
        <path d="M1 6l7 4 7-4"/>
      </svg>
      Campaigns
    </div>
    <div class="header-tab" data-tab="events-parent" tabindex="0" role="button">
      <svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6">
        <rect x="2" y="3" width="12" height="11" rx="1.5"/>
        <path d="M5 1v4M11 1v4M2 7h12"/>
      </svg>
      Events
      <span id="events-chevron" style="margin-left:auto;font-size:8px;opacity:0.45;transition:transform 0.15s;">&#9660;</span>
    </div>
    <div id="events-sub-nav" style="display:none;">
      <div class="header-tab sub-tab" data-tab="conference" tabindex="0" role="button" style="padding-left:36px;font-size:12px;">
        Post-Conference
      </div>
      <div class="header-tab sub-tab" data-tab="pre-conference" tabindex="0" role="button" style="padding-left:36px;font-size:12px;">
        Pre-Conference
      </div>
    </div>
    <div class="header-tab" data-tab="copywriting">
      <svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6">
        <path d="M2 12.5L10.5 4a2 2 0 012.8 2.8L4.8 15H2v-2.5z"/>
        <path d="M9 5.5l1.5 1.5"/>
      </svg>
      Voice &amp; Style
    </div>
    <div class="header-tab" data-tab="replies">
      <svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6">
        <path d="M14 8c0 3.3-2.7 6-6 6l-4-3h1a5 5 0 10-1-4"/><path d="M2 4l2 3 3-2"/>
      </svg>
      Replies
      <span id="reply-badge" style="display:none;background:var(--accent);color:#fff;font-size:10px;font-weight:700;padding:1px 6px;border-radius:10px;margin-left:6px;"></span>
    </div>
    <div class="header-tab" data-tab="inboxes">
      <svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6">
        <path d="M1 4a2 2 0 012-2h10a2 2 0 012 2v6a2 2 0 01-2 2H3a2 2 0 01-2-2V4z"/>
        <path d="M1 4l7 5 7-5"/>
      </svg>
      Senders
    </div>

    <div class="sidebar-divider"></div>

    <div class="header-tab" data-tab="settings">
      <svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6">
        <circle cx="8" cy="8" r="2.5"/>
        <path d="M8 1.5v1.8M8 12.7v1.8M1.5 8h1.8M12.7 8h1.8M3.4 3.4l1.3 1.3M11.3 11.3l1.3 1.3M3.4 12.6l1.3-1.3M11.3 4.7l1.3-1.3"/>
      </svg>
      Settings
    </div>
  </nav>
</aside>

<!-- ── Main ── -->
<div class="main">

  <!-- ══════ CAMPAIGNS TAB ══════ -->
  <div id="tab-outreach">

    <!-- Setup Checklist Banner -->
    <div id="setup-checklist" style="display:none;margin-bottom:16px;padding:13px 18px;border:1px solid var(--border);border-radius:var(--r);background:var(--surface);">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">
        <span style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.08em;color:var(--navy);">Getting Started</span>
        <button onclick="dismissChecklist()" style="border:none;background:none;cursor:pointer;color:var(--text-muted);font-size:18px;line-height:1;padding:0 3px;">&#215;</button>
      </div>
      <div id="setup-steps" style="display:flex;flex-wrap:wrap;gap:7px;"></div>
    </div>

    <!-- Leads Management Accordion -->
    <div class="accordion" id="acc-leads">
      <div class="accordion-header" onclick="toggleAccordion('leads')">
        <div class="accordion-header-left">
          <div class="acc-icon"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="7" cy="7" r="5"/><line x1="10.5" y1="10.5" x2="14" y2="14"/></svg></div>
          <div>
            <span class="accordion-title">Leads</span>
            <span class="accordion-meta" id="leads-meta"></span>
          </div>
        </div>
        <span class="accordion-chevron open" id="chevron-leads">&#9660;</span>
      </div>
      <div class="accordion-body open" id="body-leads">

        <div style="margin-bottom:14px;">
          <button class="sub-toggle" onclick="toggleLeadsFilter()">
            <span id="leads-filter-arrow">&#9658;</span>
            Apollo Search Filters
          </button>
          <div id="leads-filter-body">
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-top:12px;">
              <div class="form-group" style="margin-bottom:0;">
                <label>Job Titles</label>
                <div class="tags-input-wrap" id="titles-wrap">
                  <input class="tag-input" id="titles-input" placeholder="Add title, Enter" />
                </div>
              </div>
              <div class="form-group" style="margin-bottom:0;">
                <label>Industries</label>
                <div class="tags-input-wrap" id="industries-wrap">
                  <input class="tag-input" id="industries-input" placeholder="Add industry, Enter" />
                </div>
              </div>
              <div class="form-group" style="margin-bottom:0;">
                <label>Locations</label>
                <div class="tags-input-wrap" id="locations-wrap">
                  <input class="tag-input" id="locations-input" placeholder="e.g. Saudi Arabia" />
                </div>
              </div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:12px;margin-top:10px;">
              <div class="form-group" style="margin-bottom:0;">
                <label>Company Size</label>
                <select id="filter-size">
                  <option value="">Any size</option>
                  <option value="1,50">1&#8211;50</option>
                  <option value="51,200">51&#8211;200</option>
                  <option value="201,1000">201&#8211;1000</option>
                  <option value="1001,10000">1000+</option>
                </select>
              </div>
              <div class="form-group" style="margin-bottom:0;">
                <label>Limit</label>
                <input type="number" id="filter-limit" value="50" min="1" max="500" />
              </div>
              <div class="form-group" style="margin-bottom:0;">
                <label>Save as segment</label>
                <input type="text" id="filter-segment" placeholder="Segment name" />
              </div>
              <div style="display:flex;align-items:flex-end;gap:8px;">
                <button class="btn btn-primary" style="flex:1;" onclick="searchProspects()">Search Apollo</button>
              </div>
            </div>
            <div id="prospects-loading" class="hidden" style="text-align:center;padding:14px;color:var(--text-muted);margin-top:8px;">
              <div class="loading"></div> Searching Apollo.io&#8230;
            </div>
          </div>
        </div>

        <div id="segments-bar" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px;align-items:center;"></div>

        <!-- CSV Import Panel -->
        <div id="csv-import-panel" style="display:none;margin-bottom:14px;padding:14px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r-sm);">
          <div style="font-size:11px;font-weight:600;color:var(--text-dim);margin-bottom:10px;text-transform:uppercase;letter-spacing:0.06em;">Import from CSV</div>
          <div style="display:grid;grid-template-columns:1fr 1fr auto;gap:10px;align-items:end;">
            <div>
              <label>CSV File</label>
              <input type="file" id="csv-import-file" accept=".csv,.txt" style="font-size:12px;width:100%;" />
            </div>
            <div>
              <label>Save as segment (optional)</label>
              <input type="text" id="csv-import-segment" placeholder="e.g. LinkedIn Export" />
            </div>
            <button class="btn btn-primary btn-sm" onclick="importCsv()" style="height:36px;">Import</button>
          </div>
          <div style="font-size:11px;color:var(--text-muted);margin-top:8px;line-height:1.6;">
            Required column: <strong>email</strong>. Optional: first_name, last_name, title, company, industry, location, linkedin_url
          </div>
          <div id="csv-import-status" style="font-size:12px;margin-top:6px;"></div>
        </div>

        <div class="section-row">
          <div style="display:flex;align-items:center;gap:10px;">
            <span style="font-size:13px;font-weight:600;color:var(--text-dim);">Prospects</span>
            <span id="view-mode-label" style="font-size:12px;color:var(--blue);cursor:pointer;display:none;" onclick="showAllProspects()">Show all</span>
          </div>
          <div style="display:flex;gap:7px;align-items:center;">
            <input type="text" style="width:180px;" placeholder="Filter&#8230;" id="prospect-search-input" oninput="filterProspects()" />
            <button class="btn btn-outline btn-sm" id="export-csv-btn" onclick="exportProspectsCsv()">Export CSV</button>
            <button class="btn btn-outline btn-sm" onclick="toggleCsvImport()" title="Import leads from a CSV file">&#8593; Import CSV</button>
            <button class="btn btn-red btn-sm" onclick="clearAllProspects()">Clear All</button>
          </div>
        </div>

        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th><input type="checkbox" id="select-all" onchange="toggleSelectAll()" /></th>
                <th>Name</th><th>Email</th><th>Title / Company</th>
                <th>Location</th><th>LinkedIn</th><th>Source</th>
                <th>Status</th><th>Email Check</th><th></th>
              </tr>
            </thead>
            <tbody id="prospects-tbody">
              <tr><td colspan="10" style="text-align:center;color:var(--text-muted);padding:28px;">Loading prospects&#8230;</td></tr>
            </tbody>
          </table>
        </div>

        <div id="prospects-footer" style="margin-top:10px;display:flex;align-items:center;justify-content:space-between;">
          <span id="prospects-count" style="font-size:12px;color:var(--text-muted);"></span>
          <div style="display:flex;gap:7px;">
            <button class="btn btn-outline btn-sm" id="validate-smart-btn" onclick="validateSmart()">Validate All</button>
            <button class="btn btn-outline btn-sm" onclick="recoverEmails()" title="Find working emails for invalid/bounced prospects">Recover Bounced</button>
            <button class="btn btn-primary btn-sm" onclick="openWorkflowWithSelected()">Add to Campaign &#8594;</button>
          </div>
        </div>
        <div id="validate-status" class="hidden" style="margin-top:8px;font-size:12px;color:var(--text-muted);text-align:right;"></div>
      </div>
    </div>

    <!-- Email Workflow Accordion -->
    <div class="accordion" id="acc-workflow">
      <div class="accordion-header" onclick="toggleAccordion('workflow')">
        <div class="accordion-header-left">
          <div class="acc-icon"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="2" y="3" width="12" height="10" rx="1.5"/><path d="M2 5l6 4 6-4"/></svg></div>
          <div>
            <span class="accordion-title">Email Workflow</span>
            <span class="accordion-meta" id="workflow-meta"></span>
          </div>
        </div>
        <span class="accordion-chevron open" id="chevron-workflow">&#9660;</span>
      </div>
      <div class="accordion-body open" id="body-workflow">

        <div style="display:flex;gap:9px;align-items:flex-end;margin-bottom:18px;">
          <div style="flex:1;">
            <label>Select Campaign</label>
            <select id="wf-campaign-select" onchange="onWorkflowCampaignChange()">
              <option value=""> -  pick a campaign  - </option>
              <option value="__new__">+ Create new campaign</option>
            </select>
          </div>
          <button class="btn btn-outline btn-sm" onclick="loadWorkflowCampaigns()" style="white-space:nowrap;">&#8635; Refresh</button>
          <button class="btn btn-outline btn-sm" onclick="cloneCurrentCampaign()" style="white-space:nowrap;" title="Duplicate this campaign as a new draft">&#8680; Clone</button>
          <button class="btn btn-sm" onclick="deleteCurrentCampaign()" style="white-space:nowrap;background:var(--red);color:#fff;border:none;">&#10005; Delete</button>
        </div>

        <!-- Campaign builder -->
        <div id="wf-builder" class="hidden">
          <div class="steps">
            <div class="step active" id="wf-step-tab-1" onclick="wfGoStep(1)">1. Audience</div>
            <div class="step" id="wf-step-tab-2" onclick="wfGoStep(2)">2. Goal</div>
            <div class="step" id="wf-step-tab-3" onclick="wfGoStep(3)">3. Preview</div>
            <div class="step" id="wf-step-tab-4" onclick="wfGoStep(4)">4. Inboxes</div>
            <div class="step" id="wf-step-tab-5" onclick="wfGoStep(5)">5. Launch</div>
          </div>

          <div id="wf-step-1">
            <div class="form-row" style="margin-bottom:12px;">
              <div class="form-group" style="margin-bottom:0;">
                <label>Campaign Name</label>
                <input type="text" id="b-name" placeholder="e.g. Q1 GCC HSE Push" />
              </div>
              <div class="form-group" style="margin-bottom:0;">
                <label>Audience Segment</label>
                <select id="b-segment">
                  <option value="">All pending prospects</option>
                </select>
              </div>
            </div>
            <button class="btn btn-primary" onclick="wfGoStep(2)">Next &#8594;</button>
          </div>

          <div id="wf-step-2" class="hidden">
            <div class="form-group">
              <label>Goal</label>
              <input type="text" id="b-goal" placeholder="e.g. Book a 20-minute discovery call" />
            </div>
            <div class="form-group">
              <label>Value Proposition <span style="font-size:11px;color:var(--text-muted);font-weight:400;text-transform:none;letter-spacing:0;">(pre-filled from settings  -  edit to override)</span></label>
              <div id="b-value-prop-warning" class="hidden" style="font-size:12px;color:var(--yellow);background:var(--yellow-bg);border:1px solid rgba(217,119,6,0.22);padding:8px 12px;border-radius:var(--r-sm);margin-bottom:8px;">
                &#9888; No value proposition saved. <a href="#" onclick="event.preventDefault();switchTab('settings');" style="color:var(--yellow);text-decoration:underline;">Set one in Settings</a> or type it below.
              </div>
              <textarea id="b-value-prop" rows="3" placeholder="Describe what you offer and its key outcomes&#8230;" oninput="this.dataset.edited='1'"></textarea>
            </div>

            <div style="margin-bottom:14px;">
              <button class="sub-toggle" onclick="toggleStepInstructions()" id="step-instr-toggle">
                <span id="step-instr-arrow">&#9658;</span>
                Per-email focus instructions (optional)
              </button>
              <div id="step-instr-body" style="display:none;margin-top:10px;">
                <div style="font-size:11px;color:var(--text-muted);margin-bottom:8px;">Guide Claude on each email&#8217;s focus. Leave blank to use global instructions.</div>
                <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;">
                  <div class="form-group" style="margin-bottom:0;">
                    <label>Email 1  -  Initial</label>
                    <input type="text" id="b-email1-instr" placeholder="e.g. Lead with the safety stat" style="font-size:12px;" />
                  </div>
                  <div class="form-group" style="margin-bottom:0;">
                    <label>Email 2  -  Follow-up 1</label>
                    <input type="text" id="b-email2-instr" placeholder="e.g. Reference a case study" style="font-size:12px;" />
                  </div>
                  <div class="form-group" style="margin-bottom:0;">
                    <label>Email 3  -  Final</label>
                    <input type="text" id="b-email3-instr" placeholder="e.g. Soft close, offer a quick call" style="font-size:12px;" />
                  </div>
                </div>
              </div>
            </div>

            <div style="display:flex;gap:8px;">
              <button class="btn btn-outline" onclick="wfGoStep(1)">&#8592; Back</button>
              <button class="btn btn-primary" onclick="createAndGenerate()">Generate Sequences</button>
            </div>
          </div>

          <div id="wf-step-3" class="hidden">
            <div id="builder-generating" style="text-align:center;padding:36px;color:var(--text-muted);">
              <div class="loading"></div>
              <div style="margin-top:12px;" id="builder-gen-status">Starting generation job&#8230;</div>
              <div style="margin-top:12px;background:var(--surface2);height:4px;overflow:hidden;width:240px;border-radius:var(--r-pill);margin-left:auto;margin-right:auto;">
                <div id="builder-gen-bar" style="height:100%;background:var(--navy);width:0%;transition:width 0.4s;border-radius:var(--r-pill);"></div>
              </div>
            </div>
            <div id="builder-previews"></div>
            <div id="builder-preview-actions" class="hidden" style="display:flex;gap:8px;margin-top:14px;">
              <button class="btn btn-outline" onclick="wfGoStep(2)">&#8592; Back</button>
              <button class="btn btn-primary" onclick="approveAndContinue()">Approve All &amp; Continue</button>
            </div>
          </div>

          <div id="wf-step-4" class="hidden">
            <div id="builder-inboxes-list" style="color:var(--text-muted);font-size:13px;margin-bottom:14px;">Loading inboxes&#8230;</div>
            <div class="form-group">
              <label>Daily Sending Limit (per inbox)</label>
              <input type="number" id="b-daily-limit" value="30" min="1" max="200" style="width:120px;" />
            </div>
            <div style="display:flex;gap:8px;">
              <button class="btn btn-outline" onclick="wfGoStep(3)">&#8592; Back</button>
              <button class="btn btn-primary" onclick="wfGoStep(5)">Next &#8594;</button>
            </div>
          </div>

          <div id="wf-step-5" class="hidden">
            <div id="builder-launch-summary" style="font-size:13.5px;line-height:1.9;color:var(--text-dim);margin-bottom:16px;padding:14px;background:var(--surface);border-radius:var(--r-sm);border:1px solid var(--border);"></div>
            <div style="display:flex;gap:8px;">
              <button class="btn btn-outline" onclick="wfGoStep(4)">&#8592; Back</button>
              <button class="btn btn-green" onclick="launchCampaign()">Launch Campaign</button>
            </div>
          </div>
        </div>

        <div id="wf-main" style="color:var(--text-muted);font-size:13px;padding:12px 0;">Select a campaign above, or create a new one.</div>
      </div>
    </div>

    <!-- Analytics Accordion -->
    <div class="accordion" id="acc-analytics">
      <div class="accordion-header" onclick="toggleAccordion('analytics')">
        <div class="accordion-header-left">
          <div class="acc-icon"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><line x1="4" y1="12" x2="4" y2="7"/><line x1="8" y1="12" x2="8" y2="4"/><line x1="12" y1="12" x2="12" y2="9"/></svg></div>
          <div>
            <span class="accordion-title">Analytics</span>
            <span class="accordion-meta" id="analytics-meta"></span>
          </div>
        </div>
        <span class="accordion-chevron" id="chevron-analytics">&#9660;</span>
      </div>
      <div class="accordion-body" id="body-analytics">
        <div id="analytics-loading" style="text-align:center;padding:40px;color:var(--text-muted);">
          <div class="loading"></div>
          <div style="margin-top:10px;">Loading analytics&#8230;</div>
        </div>
        <div id="analytics-content" class="hidden">
          <div id="kpi-grid" class="cards" style="grid-template-columns:repeat(6,1fr);"></div>
          <div class="panel">
            <div class="panel-title" style="display:flex;align-items:center;justify-content:space-between;">
              <span>Campaign Breakdown</span>
              <div style="display:flex;align-items:center;gap:10px;">
                <span style="font-size:11px;color:var(--text-muted);font-weight:400;text-transform:none;letter-spacing:0;">Campaign engagement data</span>
                <button class="btn btn-secondary btn-sm" onclick="loadAnalytics()">&#8635; Refresh</button>
              </div>
            </div>
            <div style="overflow-x:auto;">
              <table id="analytics-table">
                <thead>
                  <tr>
                    <th>Campaign</th><th>Status</th>
                    <th style="text-align:right;">Sent</th><th style="text-align:right;">Delivery %</th>
                    <th style="text-align:right;">Open %</th><th style="text-align:right;">Reply %</th>
                    <th style="text-align:right;">Positive %</th><th style="text-align:right;">Meetings</th>
                    <th style="text-align:center;">Edit</th>
                  </tr>
                </thead>
                <tbody id="analytics-tbody"></tbody>
              </table>
            </div>
          </div>

          <div id="cost-panel" class="panel" style="margin-top:2px;">
            <div class="panel-title">Campaign Cost Overview</div>
            <div id="cost-loading" style="color:var(--text-muted);font-size:13px;">Loading cost data&#8230;</div>
            <div id="cost-content" class="hidden">
              <div id="cost-summary-cards" class="cards" style="grid-template-columns:repeat(3,1fr);margin-bottom:16px;"></div>
              <div style="font-size:12px;font-weight:600;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px;">Anthropic Token Usage by Campaign</div>
              <div style="overflow-x:auto;">
                <table id="cost-table">
                  <thead>
                    <tr>
                      <th>Campaign</th><th>Status</th>
                      <th style="text-align:right;">Input tokens</th>
                      <th style="text-align:right;">Output tokens</th>
                      <th style="text-align:right;">Anthropic cost</th>
                    </tr>
                  </thead>
                  <tbody id="cost-tbody"></tbody>
                </table>
              </div>
              <div style="font-size:11px;color:var(--text-muted);margin-top:8px;">
                Pricing: <span id="cost-pricing-note"></span> &#183; Configure in Settings &#8594; Cost Tracking.
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

  </div><!-- /tab-outreach -->

  <!-- ══════ EVENTS TAB ══════ -->
  <div id="tab-conference" class="hidden">

    <div style="margin-bottom:16px;padding:12px 16px;background:var(--navy-light);border:1px solid rgba(11,31,59,0.12);border-radius:var(--r-sm);font-size:12px;color:var(--text-dim);line-height:1.65;">
      <strong>Events outreach</strong> sends warm, personal one-off emails from your own Outlook inbox to people you&#8217;ve met at conferences.
      These go to Outlook <em>Drafts</em> so you review each one before hitting send.
    </div>

    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:18px;">
      <span style="font-size:16px;font-weight:600;color:var(--navy);">Events Outreach</span>
      <div style="display:flex;gap:8px;align-items:center;">
        <div id="outlook-status-badge" style="font-size:12px;padding:4px 10px;background:var(--red-bg);color:var(--red);border:1px solid rgba(220,38,38,0.2);border-radius:var(--r-pill);cursor:pointer;" onclick="connectOutlook()">
          &#9679; Outlook not connected
        </div>
        <select id="conf-filter" class="input" style="width:200px;" onchange="loadConferenceLeads()">
          <option value="">All conferences</option>
        </select>
        <button class="btn btn-outline" onclick="loadConferenceLeads()">&#8635; Refresh</button>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px;">
      <div class="panel" style="margin-bottom:0;">
        <div class="panel-title">Import Leads from Excel</div>
        <div class="form-group">
          <label>Conference Name</label>
          <input id="conf-name-input" class="input" type="text" placeholder="e.g. ADIPEC 2026" />
        </div>
        <div class="form-group" style="margin-bottom:12px;">
          <label>File (.xlsx or .csv)</label>
          <input id="conf-file-input" type="file" accept=".xlsx,.xls,.xlsm,.csv" style="display:block;padding:8px 0;font-size:13px;color:var(--text-muted);" />
        </div>
        <div style="display:flex;gap:8px;align-items:center;">
          <button class="btn btn-primary" onclick="importConferenceLeads()">Import</button>
          <span id="conf-import-status" style="font-size:12px;color:var(--text-muted);"></span>
        </div>
        <div style="margin-top:12px;font-size:11px;color:var(--text-muted);line-height:1.6;">
          Expected: <strong>email</strong>, first name, last name, company, title, notes, linkedin, website
        </div>
      </div>

      <div class="panel" style="margin-bottom:0;">
        <div class="panel-title">Outreach Context</div>
        <div class="form-group" style="margin-bottom:8px;">
          <label>Tell Claude what this outreach is about</label>
          <textarea id="conf-context" rows="6" style="font-size:13px;resize:vertical;"
            placeholder="e.g. We met at ADIPEC 2026. I'm Rishabh from Example  -  we build AI safety monitoring for O&amp;G sites. Goal: book a 20-min follow-up."></textarea>
        </div>
        <div style="margin-bottom:10px;">
          <label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:5px;text-transform:none;letter-spacing:0;">Draft inbox</label>
          <div style="display:flex;gap:8px;align-items:center;">
            <select id="conf-inbox-select" class="input" style="flex:1;font-size:13px;">
              <option value="outlook">Personal Outlook</option>
            </select>
            <button class="btn btn-secondary" style="white-space:nowrap;font-size:12px;" onclick="openInboxManager()">&#9881; Manage</button>
          </div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <button class="btn btn-primary" onclick="generateAllConference()">Generate All</button>
          <button class="btn btn-outline" onclick="enrichAllConference()">Enrich All</button>
          <button class="btn btn-secondary" onclick="draftAllConference()">Push All to Drafts</button>
        </div>
        <div id="conf-bulk-status" style="margin-top:8px;font-size:12px;color:var(--text-muted);"></div>
      </div>
    </div>

    <div class="panel">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;">
        <div class="panel-title" style="margin-bottom:0;">Leads</div>
        <div style="display:flex;align-items:center;gap:10px;">
          <input type="text" id="conf-search-input" placeholder="Filter leads&#8230;" style="width:180px;font-size:12px;" oninput="filterConfLeads()" />
          <span id="conf-leads-count" style="font-size:12px;color:var(--text-muted);"></span>
        </div>
      </div>
      <div id="conf-loading" style="text-align:center;padding:40px;color:var(--text-muted);">
        <div class="loading"></div>
        <div style="margin-top:10px;">Loading leads&#8230;</div>
      </div>
      <div id="conf-table-wrap" class="hidden" style="overflow-x:auto;">
        <table>
          <thead>
            <tr>
              <th style="width:36px;"><input type="checkbox" id="conf-check-all" onchange="confToggleAll(this)"></th>
              <th>Name</th><th>Email</th><th>Company / Title</th>
              <th>Enriched</th><th>Email</th><th>Draft</th><th style="text-align:center;">Actions</th>
            </tr>
          </thead>
          <tbody id="conf-tbody"></tbody>
        </table>
      </div>
      <div id="conf-empty" class="hidden" style="text-align:center;padding:40px;color:var(--text-muted);">
        No leads yet. Import an Excel file above.
      </div>
    </div>

    <!-- Inbox Manager Modal -->
    <div id="inbox-manager-modal" class="hidden" style="position:fixed;inset:0;background:rgba(11,31,59,0.42);z-index:100;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(2px);">
      <div style="background:var(--bg);border:1px solid var(--border);border-radius:var(--r);padding:28px;width:520px;max-height:80vh;overflow-y:auto;box-shadow:0 8px 32px rgba(11,31,59,0.16);">
        <div style="font-size:15px;font-weight:600;margin-bottom:18px;color:var(--navy);">Manage Draft Inboxes</div>
        <div id="inbox-list" style="margin-bottom:20px;"></div>
        <div style="border-top:1px solid var(--border);padding-top:16px;">
          <div style="font-size:13px;font-weight:600;margin-bottom:12px;color:var(--text-dim);">Add Inbox</div>
          <div class="form-row" style="margin-bottom:10px;">
            <div><label>Display Name</label><input id="new-inbox-name" class="input" type="text" placeholder="e.g. Rishabh Personal" /></div>
            <div><label>Email Address</label><input id="new-inbox-email" class="input" type="email" placeholder="you@example.com" /></div>
          </div>
          <div class="form-group">
            <label>Type</label>
            <select id="new-inbox-type" class="input" onchange="toggleImapFields()">
              <option value="imap">IMAP (Gmail, custom domain, etc.)</option>
              <option value="outlook">Personal Outlook (Microsoft Graph)</option>
            </select>
          </div>
          <div id="imap-fields">
            <div class="form-row" style="margin-bottom:10px;">
              <div><label>IMAP Host</label><input id="new-inbox-host" class="input" type="text" placeholder="imap.gmail.com" /></div>
              <div><label>Port</label><input id="new-inbox-port" class="input" type="number" value="993" /></div>
            </div>
            <div class="form-group">
              <label>Password / App Password</label>
              <input id="new-inbox-pass" class="input" type="password" placeholder="Gmail: use an App Password" />
            </div>
            <div style="font-size:11px;color:var(--text-muted);margin-bottom:10px;line-height:1.5;">
              Gmail: enable 2FA &#8594; <a href="https://myaccount.google.com/apppasswords" target="_blank" style="color:var(--blue);">create an App Password</a> &#8594; paste it here.
            </div>
          </div>
          <div style="display:flex;gap:8px;">
            <button class="btn btn-primary" onclick="addInbox()">Add Inbox</button>
            <button class="btn btn-secondary" onclick="closeInboxManager()">Cancel</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Outlook Auth Modal -->
    <div id="outlook-auth-modal" class="hidden" style="position:fixed;inset:0;background:rgba(11,31,59,0.42);z-index:200;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(2px);">
      <div style="background:var(--bg);border:1px solid var(--border);border-radius:var(--r);padding:32px;width:460px;text-align:center;box-shadow:0 8px 32px rgba(11,31,59,0.2);">
        <div id="outlook-auth-title" style="font-size:16px;font-weight:600;margin-bottom:8px;color:var(--navy);">Connect Outlook</div>
        <div id="outlook-auth-step1" class="">
          <p style="font-size:13px;color:var(--text-muted);margin-bottom:20px;">Sign in to Microsoft once so the system can create drafts in your Outlook.</p>
          <button class="btn btn-primary" style="width:100%;" onclick="startOutlookAuth()">Connect Outlook Account</button>
          <button class="btn btn-secondary" style="width:100%;margin-top:8px;" onclick="closeOutlookAuth()">Cancel</button>
        </div>
        <div id="outlook-auth-step2" class="hidden">
          <p style="font-size:13px;color:var(--text-muted);margin-bottom:16px;">Open the link below and enter the code to sign in:</p>
          <div style="margin-bottom:16px;">
            <div style="font-size:28px;font-weight:700;letter-spacing:4px;color:var(--navy);margin-bottom:8px;" id="outlook-user-code">------</div>
            <a id="outlook-verify-link" href="#" target="_blank" class="btn btn-outline" style="display:inline-block;margin-bottom:8px;">Open microsoft.com/devicelogin &#8599;</a>
          </div>
          <p style="font-size:12px;color:var(--text-muted);margin-bottom:16px;" id="outlook-auth-waiting">Waiting for you to sign in&#8230;</p>
          <button class="btn btn-secondary" style="width:100%;" onclick="closeOutlookAuth()">Cancel</button>
        </div>
        <div id="outlook-auth-step3" class="hidden">
          <div style="font-size:36px;margin-bottom:12px;">&#10003;</div>
          <div style="font-size:14px;font-weight:600;margin-bottom:6px;color:var(--green);">Connected!</div>
          <div style="font-size:13px;color:var(--text-muted);margin-bottom:20px;" id="outlook-auth-account"></div>
          <button class="btn btn-primary" style="width:100%;" onclick="closeOutlookAuth()">Done</button>
        </div>
      </div>
    </div>

    <!-- Email Preview Modal -->
    <div id="conf-email-modal" class="hidden" style="position:fixed;inset:0;background:rgba(11,31,59,0.42);z-index:100;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(2px);">
      <div style="background:var(--bg);border:1px solid var(--border);border-radius:var(--r);padding:28px;width:560px;max-height:80vh;overflow-y:auto;box-shadow:0 8px 32px rgba(11,31,59,0.16);">
        <div style="font-size:15px;font-weight:600;margin-bottom:16px;color:var(--navy);" id="conf-modal-name">Email Preview</div>
        <div class="form-group"><label>Subject</label><input id="conf-modal-subject" class="input" type="text" /></div>
        <div class="form-group"><label>Body</label><textarea id="conf-modal-body" rows="10" style="font-size:13px;font-family:inherit;"></textarea></div>
        <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:4px;">
          <button class="btn btn-secondary" onclick="closeConfModal()">Cancel</button>
          <button class="btn btn-outline" onclick="saveConfEmail()">Save Changes</button>
          <button class="btn btn-primary" onclick="sendConfDraft()">Push to Drafts</button>
        </div>
      </div>
    </div>

  </div><!-- /tab-conference -->

  <!-- ══════ PRE-CONFERENCE TAB ══════ -->
  <div id="tab-pre-conference" class="hidden">

    <!-- Top bar: event selector + outlook -->
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;">
      <div style="display:flex;align-items:center;gap:12px;">
        <span style="font-size:16px;font-weight:600;color:var(--navy);">Pre-Conference Outreach</span>
        <select id="preconf-event-select" class="input" style="width:220px;font-size:13px;" onchange="onPreconfEventChange()">
          <option value="">Select an event...</option>
        </select>
        <button class="btn btn-primary btn-sm" onclick="createNewPreconfEvent()">+ New Event</button>
      </div>
      <div id="preconf-outlook-status" style="font-size:12px;padding:4px 10px;background:var(--red-bg);color:var(--red);border:1px solid rgba(220,38,38,0.2);border-radius:var(--r-pill);cursor:pointer;" onclick="connectOutlook()">
        &#9679; Outlook not connected
      </div>
    </div>

    <!-- Stepper -->
    <div style="display:flex;align-items:center;justify-content:center;gap:0;margin-bottom:24px;padding:16px 20px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r);">
      <div class="preconf-step active" data-step="1" onclick="setPreconfStep(1)" style="display:flex;align-items:center;gap:8px;cursor:pointer;padding:6px 14px;border-radius:var(--r-sm);transition:background 0.15s;">
        <span class="preconf-step-dot" id="preconf-dot-1" style="width:22px;height:22px;border-radius:50%;background:var(--navy);color:#fff;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;flex-shrink:0;">1</span>
        <span class="preconf-step-label" style="font-size:12px;font-weight:600;color:var(--navy);white-space:nowrap;">Event</span>
      </div>
      <div style="flex:1;max-width:80px;height:2px;background:var(--border);"></div>
      <div class="preconf-step" data-step="2" onclick="setPreconfStep(2)" style="display:flex;align-items:center;gap:8px;cursor:pointer;padding:6px 14px;border-radius:var(--r-sm);transition:background 0.15s;">
        <span class="preconf-step-dot" id="preconf-dot-2" style="width:22px;height:22px;border-radius:50%;background:var(--border);color:var(--text-muted);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;flex-shrink:0;">2</span>
        <span class="preconf-step-label" style="font-size:12px;font-weight:500;color:var(--text-muted);white-space:nowrap;">Leads</span>
      </div>
      <div style="flex:1;max-width:80px;height:2px;background:var(--border);"></div>
      <div class="preconf-step" data-step="3" onclick="setPreconfStep(3)" style="display:flex;align-items:center;gap:8px;cursor:pointer;padding:6px 14px;border-radius:var(--r-sm);transition:background 0.15s;">
        <span class="preconf-step-dot" id="preconf-dot-3" style="width:22px;height:22px;border-radius:50%;background:var(--border);color:var(--text-muted);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;flex-shrink:0;">3</span>
        <span class="preconf-step-label" style="font-size:12px;font-weight:500;color:var(--text-muted);white-space:nowrap;">Sequence</span>
      </div>
      <div style="flex:1;max-width:80px;height:2px;background:var(--border);"></div>
      <div class="preconf-step" data-step="4" onclick="setPreconfStep(4)" style="display:flex;align-items:center;gap:8px;cursor:pointer;padding:6px 14px;border-radius:var(--r-sm);transition:background 0.15s;">
        <span class="preconf-step-dot" id="preconf-dot-4" style="width:22px;height:22px;border-radius:50%;background:var(--border);color:var(--text-muted);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;flex-shrink:0;">4</span>
        <span class="preconf-step-label" style="font-size:12px;font-weight:500;color:var(--text-muted);white-space:nowrap;">Send</span>
      </div>
    </div>

    <!-- Step 1: Event Setup -->
    <div id="preconf-step-1" class="panel" style="margin-bottom:20px;">
      <div class="panel-title">Event Setup</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:12px;">
        <div class="form-group" style="margin-bottom:0;">
          <label>Event Name</label>
          <input id="preconf-name-input" class="input" type="text" placeholder="e.g. ADIPEC 2026" />
        </div>
        <div class="form-group" style="margin-bottom:0;">
          <label>Event Date</label>
          <input id="preconf-date-input" class="input" type="date" />
        </div>
      </div>
      <div class="form-group" style="margin-bottom:12px;">
        <label>Outreach Context</label>
        <textarea id="preconf-context" rows="4" style="font-size:13px;resize:vertical;"
          placeholder="e.g. I'm attending ADIPEC 2026. I'm Rishabh from Example  -  we build AI safety monitoring for O&amp;G sites. Goal: schedule meetings with HSE leaders at the event."></textarea>
      </div>
      <button class="btn btn-primary btn-sm" onclick="savePreconfEvent()">Save Event</button>
      <span id="preconf-event-save-status" style="font-size:12px;color:var(--text-muted);margin-left:8px;"></span>
    </div>

    <!-- Step 2: Lead Sourcing -->
    <div id="preconf-step-2" style="display:none;margin-bottom:20px;">
      <!-- Source chooser -->
      <div style="display:flex;gap:12px;margin-bottom:16px;">
        <div id="preconf-src-apollo-btn" onclick="showPreconfSource('apollo')" style="flex:1;padding:14px 18px;border:2px solid var(--navy);border-radius:var(--r);cursor:pointer;background:var(--navy);color:#fff;text-align:center;transition:all 0.15s;">
          <div style="font-size:13px;font-weight:600;">Apollo</div>
          <div style="font-size:11px;opacity:0.7;margin-top:2px;">Search by title, industry, location</div>
        </div>
        <div id="preconf-src-salesnav-btn" onclick="showPreconfSource('salesnav')" style="flex:1;padding:14px 18px;border:2px solid var(--border);border-radius:var(--r);cursor:pointer;background:var(--surface);text-align:center;transition:all 0.15s;">
          <div style="font-size:13px;font-weight:600;color:var(--navy);">Sales Navigator</div>
          <div style="font-size:11px;color:var(--text-muted);margin-top:2px;">Paste a Sales Nav search URL</div>
        </div>
        <div id="preconf-src-file-btn" onclick="showPreconfSource('file')" style="flex:1;padding:14px 18px;border:2px solid var(--border);border-radius:var(--r);cursor:pointer;background:var(--surface);text-align:center;transition:all 0.15s;">
          <div style="font-size:13px;font-weight:600;color:var(--navy);">Upload File</div>
          <div style="font-size:11px;color:var(--text-muted);margin-top:2px;">Import from Excel or CSV</div>
        </div>
      </div>

      <!-- Apollo panel -->
      <div id="preconf-src-apollo" class="panel" style="margin-bottom:12px;">
        <div class="form-row" style="margin-bottom:8px;">
          <div><label>Job Titles</label><input id="preconf-apollo-titles" class="input" type="text" placeholder="HSE Manager, VP Safety" /></div>
          <div><label>Seniorities</label><input id="preconf-apollo-seniorities" class="input" type="text" placeholder="director, vp" /></div>
        </div>
        <div class="form-row" style="margin-bottom:8px;">
          <div><label>Industries</label><input id="preconf-apollo-industries" class="input" type="text" placeholder="oil and gas, energy" /></div>
          <div><label>Locations</label><input id="preconf-apollo-locations" class="input" type="text" placeholder="UAE, Saudi Arabia" /></div>
        </div>
        <div class="form-group" style="margin-bottom:10px;">
          <label>Employee Range</label>
          <input id="preconf-apollo-employees" class="input" type="text" placeholder="1001-5000, 5001-10000" />
        </div>
        <div style="display:flex;gap:8px;align-items:center;">
          <button class="btn btn-primary btn-sm" onclick="searchApolloPreConf()">Search</button>
          <span id="preconf-apollo-status" style="font-size:12px;color:var(--text-muted);"></span>
        </div>
        <div id="preconf-apollo-preview" class="hidden" style="margin-top:12px;max-height:260px;overflow-y:auto;">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">
            <span style="font-size:11px;font-weight:600;color:var(--text-dim);">Preview (no credits spent)</span>
            <button class="btn btn-primary btn-sm" onclick="confirmApolloImport()">Import Selected</button>
          </div>
          <table style="font-size:11px;">
            <thead><tr>
              <th style="width:26px;"><input type="checkbox" id="preconf-apollo-check-all" onchange="preconfApolloToggleAll(this)"></th>
              <th>Name</th><th>Title</th><th>Company</th><th>Location</th>
            </tr></thead>
            <tbody id="preconf-apollo-tbody"></tbody>
          </table>
        </div>
      </div>

      <!-- Sales Navigator panel -->
      <div id="preconf-src-salesnav" class="panel" style="display:none;margin-bottom:12px;">
        <!-- Sub-tabs: Quick Search vs Paste URL -->
        <div style="display:flex;gap:6px;margin-bottom:14px;">
          <button id="preconf-sn-tab-filters" class="btn btn-primary btn-sm" onclick="showSalesNavTab('filters')">Quick Search</button>
          <button id="preconf-sn-tab-url" class="btn btn-secondary btn-sm" onclick="showSalesNavTab('url')">Paste URL</button>
        </div>

        <!-- Quick Search (filter-based) -->
        <div id="preconf-sn-filters">
          <div class="form-group" style="margin-bottom:10px;">
            <label>Job Title Keywords</label>
            <input id="preconf-sn-titles" class="input" type="text" placeholder="HSE Manager, VP Safety, Director EHS" />
          </div>
          <div style="margin-bottom:10px;">
            <label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:6px;">Seniority</label>
            <div style="display:flex;gap:10px;flex-wrap:wrap;">
              <label style="font-size:12px;cursor:pointer;"><input type="checkbox" class="preconf-sn-seniority" value="Manager"> Manager</label>
              <label style="font-size:12px;cursor:pointer;"><input type="checkbox" class="preconf-sn-seniority" value="Director" checked> Director</label>
              <label style="font-size:12px;cursor:pointer;"><input type="checkbox" class="preconf-sn-seniority" value="VP" checked> VP</label>
              <label style="font-size:12px;cursor:pointer;"><input type="checkbox" class="preconf-sn-seniority" value="CxO"> C-Suite</label>
              <label style="font-size:12px;cursor:pointer;"><input type="checkbox" class="preconf-sn-seniority" value="Owner"> Owner</label>
            </div>
          </div>
          <div style="margin-bottom:10px;">
            <label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:6px;">Industry</label>
            <div style="display:flex;gap:10px;flex-wrap:wrap;">
              <label style="font-size:12px;cursor:pointer;"><input type="checkbox" class="preconf-sn-industry" value="Oil & Gas" checked> Oil &amp; Gas</label>
              <label style="font-size:12px;cursor:pointer;"><input type="checkbox" class="preconf-sn-industry" value="Energy"> Energy</label>
              <label style="font-size:12px;cursor:pointer;"><input type="checkbox" class="preconf-sn-industry" value="Mining & Metals"> Mining</label>
              <label style="font-size:12px;cursor:pointer;"><input type="checkbox" class="preconf-sn-industry" value="Construction"> Construction</label>
              <label style="font-size:12px;cursor:pointer;"><input type="checkbox" class="preconf-sn-industry" value="Chemicals"> Chemicals</label>
              <label style="font-size:12px;cursor:pointer;"><input type="checkbox" class="preconf-sn-industry" value="Environmental Services"> Environmental</label>
            </div>
          </div>
          <div style="margin-bottom:10px;">
            <label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:6px;">Region</label>
            <div id="preconf-sn-region-container"></div>
          </div>
          <div style="margin-bottom:12px;">
            <label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:6px;">Company Size</label>
            <div style="display:flex;gap:10px;flex-wrap:wrap;">
              <label style="font-size:12px;cursor:pointer;"><input type="checkbox" class="preconf-sn-size" value="201-500"> 201-500</label>
              <label style="font-size:12px;cursor:pointer;"><input type="checkbox" class="preconf-sn-size" value="501-1000"> 501-1000</label>
              <label style="font-size:12px;cursor:pointer;"><input type="checkbox" class="preconf-sn-size" value="1001-5000" checked> 1001-5000</label>
              <label style="font-size:12px;cursor:pointer;"><input type="checkbox" class="preconf-sn-size" value="5001-10000" checked> 5001-10000</label>
              <label style="font-size:12px;cursor:pointer;"><input type="checkbox" class="preconf-sn-size" value="10001+"> 10001+</label>
            </div>
          </div>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
            <label style="font-size:11px;color:var(--text-muted);">Max Results</label>
            <input id="preconf-salesnav-max" type="number" class="input" value="25" min="5" max="2500" step="5" style="width:70px;font-size:12px;" />
            <button class="btn btn-primary btn-sm" onclick="searchSalesNavPreConf()">Search LinkedIn</button>
            <span id="preconf-salesnav-status" style="font-size:12px;color:var(--text-muted);"></span>
          </div>
        </div>

        <!-- Paste URL fallback -->
        <div id="preconf-sn-url" style="display:none;">
          <div style="font-size:12px;color:var(--text-muted);margin-bottom:10px;line-height:1.5;">
            Paste a Sales Navigator search URL directly. Use this if the quick search filters don't cover your needs.
          </div>
          <div class="form-group" style="margin-bottom:10px;">
            <input id="preconf-salesnav-url" class="input" type="text" placeholder="https://www.linkedin.com/sales/search/people?query=..." />
          </div>
          <div style="display:flex;gap:8px;align-items:center;">
            <button class="btn btn-primary btn-sm" onclick="importSalesNavUrlPreConf()">Search &amp; Import</button>
            <span id="preconf-salesnav-url-status" style="font-size:12px;color:var(--text-muted);"></span>
          </div>
        </div>
      </div>

      <!-- File import panel -->
      <div id="preconf-src-file" class="panel" style="display:none;margin-bottom:12px;">
        <div class="form-group" style="margin-bottom:10px;">
          <label>Upload .xlsx or .csv</label>
          <input id="preconf-file-input" type="file" accept=".xlsx,.xls,.xlsm,.csv" style="display:block;padding:8px 0;font-size:12px;color:var(--text-muted);" />
        </div>
        <div style="display:flex;gap:8px;align-items:center;">
          <button class="btn btn-primary btn-sm" onclick="importPreConfLeads()">Import</button>
          <span id="preconf-import-status" style="font-size:12px;color:var(--text-muted);"></span>
        </div>
      </div>

      <!-- Consolidated lead actions -->
      <div class="panel" style="margin-bottom:0;background:var(--surface);">
        <div style="display:flex;align-items:center;justify-content:space-between;">
          <div style="font-size:13px;font-weight:600;color:var(--navy);">Consolidated Leads</div>
          <div style="display:flex;gap:8px;align-items:center;">
            <button class="btn btn-outline btn-sm" onclick="enrichAllPreConf()">Enrich All</button>
            <span id="preconf-enrich-status" style="font-size:12px;color:var(--text-muted);"></span>
          </div>
        </div>
        <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">All leads from every source appear in the table below. Use filters to narrow down before proceeding to Sequence design.</div>
      </div>
    </div>

    <!-- Step 3: Sequence Design -->
    <div id="preconf-step-3" class="hidden" style="margin-bottom:20px;">
      <div class="panel">
        <div class="panel-title">Sequence Steps</div>
        <div style="font-size:12px;color:var(--text-muted);margin-bottom:12px;">
          Configure your email sequence. Each step has a day offset and description. Changes apply to all future generations for this event.
        </div>
        <div id="preconf-seq-design-steps" style="margin-bottom:12px;"></div>
        <div style="display:flex;gap:8px;align-items:center;">
          <button class="btn btn-outline btn-sm" onclick="addPreConfSeqStep()">+ Add Step</button>
          <button class="btn btn-primary btn-sm" onclick="savePreConfSeqDesign(this)">Save Sequence</button>
          <button class="btn btn-secondary btn-sm" onclick="resetPreConfSeqDesign()">Reset</button>
          <span id="preconf-seq-design-status" style="font-size:12px;color:var(--text-muted);"></span>
        </div>
      </div>
    </div>

    <!-- Step 4: Generate & Send -->
    <div id="preconf-step-4" class="hidden" style="margin-bottom:20px;">
      <div class="panel">
        <div class="panel-title">Generate &amp; Send</div>
        <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;">
          <div style="display:flex;align-items:center;gap:6px;">
            <label style="font-size:12px;color:var(--text-muted);white-space:nowrap;">Inbox:</label>
            <select id="preconf-inbox-select" class="input" style="width:200px;font-size:13px;">
              <option value="outlook">Personal Outlook</option>
            </select>
          </div>
          <button class="btn btn-outline btn-sm" onclick="previewOnePreConf(this)">Preview One</button>
          <button class="btn btn-primary btn-sm" onclick="generateAllPreConf()">Generate All</button>
          <button class="btn btn-outline btn-sm" onclick="draftAllPreConf()">Push All to Drafts</button>
          <button class="btn btn-primary btn-sm" onclick="launchPreConfLinkedIn(this)">Launch LinkedIn</button>
          <button class="btn btn-secondary btn-sm" onclick="checkPreConfReplies()">Check Replies</button>
          <span id="preconf-bulk-status" style="font-size:12px;color:var(--text-muted);"></span>
        </div>
      </div>
    </div>

    <!-- Lead Table (always visible) -->
    <div class="panel" id="preconf-leads-panel">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;">
        <div style="display:flex;align-items:center;gap:10px;">
          <div class="panel-title" style="margin-bottom:0;">Leads</div>
          <span id="preconf-leads-count" style="font-size:12px;color:var(--text-muted);"></span>
        </div>
        <div style="display:flex;align-items:center;gap:8px;">
          <input type="text" id="preconf-search-input" placeholder="Filter&#8230;" style="width:160px;font-size:12px;" oninput="filterPreConfLeads()" />
          <button class="btn btn-secondary btn-sm" onclick="deleteSelectedPreConf()" id="preconf-bulk-delete-btn" style="display:none;color:var(--red);">Delete Selected</button>
        </div>
      </div>
      <div id="preconf-loading" style="text-align:center;padding:30px;color:var(--text-muted);display:none;">
        <div class="loading"></div>
      </div>
      <div id="preconf-table-wrap" style="overflow-x:auto;">
        <table>
          <thead id="preconf-thead"><tr>
            <th style="width:32px;"><input type="checkbox" id="preconf-check-all" onchange="preconfToggleAll(this)"></th>
            <th>Name</th><th>Email</th><th>Company / Title</th>
          </tr></thead>
          <tbody id="preconf-tbody"></tbody>
        </table>
      </div>
      <div id="preconf-empty" style="text-align:center;padding:36px;color:var(--text-muted);display:none;">
        No leads yet. Select or create an event, then add leads in Step 2.
      </div>
    </div>

  </div><!-- /tab-pre-conference -->

  <!-- ══════ VOICE & STYLE TAB ══════ -->
  <div id="tab-copywriting" class="hidden">
    <div style="max-width:860px;margin:0 auto;">

      <div class="accordion" id="acc-style">
        <div class="accordion-header" onclick="toggleAccordion('style')">
          <div class="accordion-header-left">
            <div class="acc-icon"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M11 2.5l2.5 2.5-7 7H4v-2.5l7-7z"/><line x1="9.5" y1="4" x2="12" y2="6.5"/></svg></div>
            <div>
              <span class="accordion-title">Writing Style</span>
              <span class="accordion-meta">Your voice and formatting rules</span>
            </div>
          </div>
          <span class="accordion-chevron open" id="chevron-style">&#9660;</span>
        </div>
        <div class="accordion-body open" id="body-style">
          <div style="font-size:12px;color:var(--text-muted);margin-bottom:12px;line-height:1.65;padding:10px 14px;background:var(--surface);border-radius:var(--r-sm);border:1px solid var(--border);">
            This style guide is injected into every email Claude writes  -  tone, greetings, sign-offs, phrases to avoid.
            <strong>Sync from Outlook</strong> scans your last 200 sent emails to extract your style automatically.
          </div>
          <textarea id="style-editor" rows="22" style="width:100%;font-size:13px;font-family:'IBM Plex Mono',monospace;line-height:1.65;border:1px solid var(--border-mid);border-radius:var(--r-sm);padding:14px;resize:vertical;background:var(--surface);"
            placeholder="Your writing style guide will appear here.

Click 'Sync from Outlook' to extract your style automatically, or write your own below.

Example:
- Greeting: Use first name only, no 'Dear'
- Tone: Direct, professional, not overly formal
- Length: Keep under 100 words for Email 1
- Sign-off: 'Best, [Name]' - never 'Kind regards'
- Never use: synergy, leverage, circle back, touch base"></textarea>
          <div style="display:flex;gap:8px;margin-top:10px;align-items:center;">
            <button class="btn btn-primary btn-sm" onclick="saveWritingStyle()">Save Style</button>
            <button class="btn btn-outline btn-sm" onclick="syncStyleWithFeedback()" id="sync-style-btn">&#8635; Sync from Outlook</button>
            <span id="style-save-status" style="font-size:12px;color:var(--green);"></span>
          </div>

          <div style="border-top:1px solid var(--border);margin-top:20px;padding-top:16px;">
            <div style="font-size:13px;font-weight:600;margin-bottom:6px;color:var(--navy);">Ask Claude to update your style</div>
            <div style="font-size:12px;color:var(--text-muted);margin-bottom:10px;">Describe the change. Claude will update the guide and save it automatically.</div>
            <div style="display:flex;gap:8px;">
              <input type="text" id="style-refine-input" placeholder="e.g. Make sign-offs less formal" style="flex:1;" onkeydown="if(event.key==='Enter')refineWritingStyle()" />
              <button class="btn btn-primary btn-sm" onclick="refineWritingStyle()" id="style-refine-btn">Update</button>
            </div>
            <div id="style-refine-status" style="font-size:12px;color:var(--text-muted);margin-top:6px;"></div>
          </div>
        </div>
      </div>

      <div class="accordion" id="acc-instructions">
        <div class="accordion-header" onclick="toggleAccordion('instructions')">
          <div class="accordion-header-left">
            <div class="acc-icon"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="3" y="2" width="10" height="13" rx="1"/><line x1="6" y1="6" x2="10" y2="6"/><line x1="6" y1="9" x2="10" y2="9"/></svg></div>
            <div>
              <span class="accordion-title">Email Instructions</span>
              <span class="accordion-meta">Campaign-level overrides applied to every generation</span>
            </div>
          </div>
          <span class="accordion-chevron" id="chevron-instructions">&#9660;</span>
        </div>
        <div class="accordion-body" id="body-instructions">
          <div style="font-size:12px;color:var(--text-muted);margin-bottom:10px;line-height:1.65;">
            Applied on top of the style guide. Use for campaign-specific rules  -  case study mentions, pain-point focus, word count limits.
          </div>
          <textarea id="email-instructions" rows="6" style="width:100%;font-size:13px;font-family:inherit;" placeholder="Examples:&#10;- Always include the O&amp;G case study for oil and gas contacts&#10;- Keep emails under 80 words&#10;- For HSE managers, focus on reducing incident rates not manhours"></textarea>
          <div style="display:flex;gap:8px;align-items:center;margin-top:10px;">
            <button class="btn btn-primary btn-sm" onclick="saveEmailInstructions()">Save Instructions</button>
            <button class="btn btn-outline btn-sm" onclick="clearEmailInstructions()">Clear</button>
            <span id="instructions-save-status" style="font-size:12px;color:var(--green);"></span>
          </div>
        </div>
      </div>

    </div>
  </div><!-- /tab-copywriting -->

  <!-- ══════ SETTINGS TAB ══════ -->
  <div id="tab-settings" class="hidden">
    <div style="max-width:860px;margin:0 auto;">

      <div class="accordion" id="acc-timezone">
        <div class="accordion-header" onclick="toggleAccordion('timezone')">
          <div class="accordion-header-left">
            <div class="acc-icon"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="8" cy="8" r="6"/><polyline points="8,4 8,8 11,10"/></svg></div>
            <div>
              <span class="accordion-title">Sending Schedule</span>
              <span class="accordion-meta">Time window and timezone for campaign delivery</span>
            </div>
          </div>
          <span class="accordion-chevron open" id="chevron-timezone">&#9660;</span>
        </div>
        <div class="accordion-body open" id="body-timezone">
          <div style="margin-bottom:18px;">
            <label>Timezone</label>
            <div style="font-size:12px;color:var(--text-muted);margin-bottom:8px;">
              IANA format  -  e.g. <code>Asia/Singapore</code>, <code>Etc/GMT-8</code>, <code>America/New_York</code>
            </div>
            <div style="display:flex;gap:8px;align-items:center;">
              <input type="text" id="timezone-input" placeholder="e.g. Asia/Singapore" style="width:260px;" />
              <button class="btn btn-primary btn-sm" onclick="saveTimezone()">Save</button>
              <button class="btn btn-outline btn-sm" onclick="detectTimezone()">Detect</button>
              <span id="timezone-save-status" style="font-size:12px;color:var(--green);"></span>
            </div>
            <div id="timezone-detected" style="font-size:11px;color:var(--text-muted);margin-top:5px;display:none;"></div>
          </div>

          <div style="border-top:1px solid var(--border);padding-top:16px;">
            <label>Sending Window</label>
            <div style="font-size:12px;color:var(--text-muted);margin-bottom:8px;">
              Emails only go out between these hours (24-hour, in the timezone above).
            </div>
            <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;">
              <div style="display:flex;align-items:center;gap:8px;">
                <span style="font-size:12px;color:var(--text-muted);">From</span>
                <input type="time" id="window-start-input" style="font-size:13px;padding:5px 8px;border:1px solid var(--border-mid);border-radius:var(--r-sm);background:var(--bg);color:var(--text);font-family:inherit;outline:none;" />
              </div>
              <div style="display:flex;align-items:center;gap:8px;">
                <span style="font-size:12px;color:var(--text-muted);">To</span>
                <input type="time" id="window-end-input" style="font-size:13px;padding:5px 8px;border:1px solid var(--border-mid);border-radius:var(--r-sm);background:var(--bg);color:var(--text);font-family:inherit;outline:none;" />
              </div>
              <button class="btn btn-primary btn-sm" onclick="saveSendingWindow()">Save</button>
              <span id="window-save-status" style="font-size:12px;color:var(--green);"></span>
            </div>
          </div>
        </div>
      </div>

      <div class="accordion" id="acc-suppression">
        <div class="accordion-header" onclick="toggleAccordion('suppression')">
          <div class="accordion-header-left">
            <div class="acc-icon"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="8" cy="8" r="6"/><line x1="5" y1="5" x2="11" y2="11"/><line x1="11" y1="5" x2="5" y2="11"/></svg></div>
            <div>
              <span class="accordion-title">Suppression List</span>
              <span class="accordion-meta">Emails that will never be enrolled in any campaign</span>
            </div>
          </div>
          <span class="accordion-chevron open" id="chevron-suppression">&#9660;</span>
        </div>
        <div class="accordion-body open" id="body-suppression">
          <div style="font-size:12px;color:var(--text-muted);margin-bottom:10px;">
            Add emails here to permanently suppress them. Skipped at launch even if they have approved sequences.
          </div>
          <div style="display:flex;gap:8px;margin-bottom:14px;">
            <input type="email" id="unsub-email-input" placeholder="email@example.com" style="flex:1;" onkeydown="if(event.key==='Enter')addUnsubscribe()" />
            <select id="unsub-reason-input" style="width:140px;">
              <option value="manual">Manual</option>
              <option value="unsubscribed">Unsubscribed</option>
              <option value="bounce">Bounce</option>
              <option value="competitor">Competitor</option>
              <option value="do-not-contact">Do Not Contact</option>
            </select>
            <button class="btn btn-primary btn-sm" onclick="addUnsubscribe()">Add</button>
          </div>
          <div id="suppression-status" style="font-size:12px;color:var(--green);margin-bottom:10px;"></div>
          <div class="table-wrap">
            <table id="suppression-table">
              <thead><tr><th>Email</th><th>Reason</th><th>Added</th><th></th></tr></thead>
              <tbody id="suppression-tbody"><tr><td colspan="4" style="color:var(--text-muted);text-align:center;">Loading&#8230;</td></tr></tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="accordion" id="acc-costs">
        <div class="accordion-header" onclick="toggleAccordion('costs')">
          <div class="accordion-header-left">
            <div class="acc-icon"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="8" cy="8" r="6"/><line x1="8" y1="5" x2="8" y2="11"/><path d="M6 6.5h3a1.5 1.5 0 0 1 0 3H6"/></svg></div>
            <div>
              <span class="accordion-title">Cost Tracking</span>
              <span class="accordion-meta">Subscription and API token costs</span>
            </div>
          </div>
          <span class="accordion-chevron" id="chevron-costs">&#9660;</span>
        </div>
        <div class="accordion-body" id="body-costs">
          <div style="font-size:12px;color:var(--text-muted);margin-bottom:14px;">
            Enter your monthly subscription costs. Anthropic token costs are tracked automatically.
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px;">
            <div>
              <div style="font-size:12px;font-weight:600;color:var(--text-dim);margin-bottom:10px;text-transform:uppercase;letter-spacing:0.05em;">Monthly Subscriptions</div>
              <div class="form-group">
                <label>Apollo.io (USD/month)</label>
                <input type="number" id="cost-apollo" min="0" step="0.01" placeholder="e.g. 99" />
              </div>
            </div>
            <div>
              <div style="font-size:12px;font-weight:600;color:var(--text-dim);margin-bottom:10px;text-transform:uppercase;letter-spacing:0.05em;">Anthropic Token Costs</div>
              <div style="font-size:13px;color:var(--text-dim);line-height:1.6;">
                Token usage is tracked automatically per campaign.<br>
                Pricing is derived from the configured model  -  no manual input needed.
              </div>
            </div>
          </div>
          <div style="display:flex;gap:8px;align-items:center;">
            <button class="btn btn-primary btn-sm" onclick="saveCostSettings()">Save</button>
            <span id="cost-save-status" style="font-size:12px;color:var(--green);"></span>
          </div>
        </div>
      </div>

    </div>
  </div><!-- /tab-settings -->

  <!-- ══════ SENDERS TAB ══════ -->
  <div id="tab-inboxes" class="hidden">
    <div style="max-width:860px;margin:0 auto;">

      <div id="inboxes-health-summary" style="display:none;font-size:12px;color:var(--text-dim);margin-bottom:14px;padding:10px 16px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r-sm);"></div>

      <div id="inboxes-status-banner" style="display:flex;align-items:center;gap:12px;padding:13px 18px;border-radius:var(--r-sm);background:var(--surface);border:1px solid var(--border);margin-bottom:20px;">
        <span id="inboxes-status-dot" style="width:10px;height:10px;border-radius:50%;background:var(--text-muted);flex-shrink:0;"></span>
        <span id="inboxes-status-label" style="font-size:13px;color:var(--text-muted);">Checking inbox connections&#8230;</span>
        <button class="btn btn-sm btn-outline" style="margin-left:auto;" onclick="loadInboxes()">Refresh</button>
      </div>

      <div class="accordion" id="acc-reply-to">
        <div class="accordion-header" onclick="toggleAccordion('reply-to')">
          <div class="accordion-header-left">
            <div class="acc-icon"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><polyline points="4,9 1,12 4,15"/><path d="M15 4a6 6 0 0 1-6 6H1"/></svg></div>
            <div>
              <span class="accordion-title">Reply-To Address</span>
              <span class="accordion-meta">Replies from leads will be forwarded here</span>
            </div>
          </div>
          <span class="accordion-chevron open" id="chevron-reply-to">&#9660;</span>
        </div>
        <div class="accordion-body open" id="body-reply-to">
          <div style="font-size:12px;color:var(--text-muted);margin-bottom:10px;">
            Set a reply-to address so lead replies land in your inbox, not the sending account.
          </div>
          <div style="display:flex;gap:8px;align-items:center;">
            <input type="email" id="reply-to-input" placeholder="you@company.com" style="width:300px;" />
            <button class="btn btn-primary btn-sm" onclick="saveReplyTo()">Save</button>
            <span id="reply-to-status" style="font-size:12px;color:var(--green);"></span>
          </div>
        </div>
      </div>

      <div class="accordion" id="acc-sending-inboxes">
        <div class="accordion-header" onclick="toggleAccordion('sending-inboxes')">
          <div class="accordion-header-left">
            <div class="acc-icon"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><polyline points="3,10 8,5 13,10"/><line x1="8" y1="5" x2="8" y2="15"/><line x1="1" y1="2" x2="15" y2="2"/></svg></div>
            <div>
              <span class="accordion-title">Sending Inboxes</span>
              <span class="accordion-meta">Select which accounts campaigns will rotate through</span>
            </div>
          </div>
          <span class="accordion-chevron open" id="chevron-sending-inboxes">&#9660;</span>
        </div>
        <div class="accordion-body open" id="body-sending-inboxes">
          <div style="font-size:12px;color:var(--text-muted);margin-bottom:14px;">
            Campaigns will send from the selected Outlook accounts. Connect new inboxes in the Senders tab.
          </div>
          <div id="inboxes-list" style="display:flex;flex-direction:column;gap:8px;">
            <div style="color:var(--text-muted);font-size:13px;">Loading&#8230;</div>
          </div>
          <div style="margin-top:16px;display:flex;gap:10px;align-items:center;">
            <button class="btn btn-primary btn-sm" onclick="saveActiveInboxes()">Save selection</button>
            <span id="inboxes-save-status" style="font-size:12px;color:var(--green);"></span>
          </div>
        </div>
      </div>

    </div>
  </div><!-- /tab-inboxes -->

</div><!-- /main -->

<!-- Analytics edit modal -->
<div id="analytics-edit-modal" class="hidden modal-backdrop">
  <div class="modal-box" style="width:360px;">
    <div style="font-size:15px;font-weight:600;margin-bottom:18px;color:var(--navy);">Update Manual Metrics</div>
    <div style="margin-bottom:14px;">
      <label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:6px;">Positive Replies</label>
      <input id="edit-positive-replies" type="number" min="0" style="width:100%;" />
    </div>
    <div style="margin-bottom:20px;">
      <label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:6px;">Meetings Booked</label>
      <input id="edit-meetings-booked" type="number" min="0" style="width:100%;" />
    </div>
    <div style="display:flex;gap:10px;justify-content:flex-end;">
      <button class="btn btn-secondary" onclick="closeAnalyticsEdit()">Cancel</button>
      <button class="btn btn-primary" onclick="saveAnalyticsEdit()">Save</button>
    </div>
  </div>
</div>

<!-- Custom dialog (replaces browser prompt/confirm) -->
<div id="app-dialog" onclick="if(event.target===this)_dialogResolve(null)" style="display:none;position:fixed;inset:0;background:rgba(11,31,59,0.42);z-index:9999;align-items:center;justify-content:center;backdrop-filter:blur(2px);">
  <div style="background:var(--bg);border:1px solid var(--border);border-radius:var(--r);padding:28px;width:420px;max-width:90vw;box-shadow:0 8px 32px rgba(11,31,59,0.16);">
    <div id="app-dialog-title" style="font-size:15px;font-weight:600;color:var(--navy);margin-bottom:12px;"></div>
    <div id="app-dialog-msg" style="font-size:13px;color:var(--text-dim);margin-bottom:16px;line-height:1.5;"></div>
    <div id="app-dialog-input-wrap" style="display:none;margin-bottom:16px;">
      <input id="app-dialog-input" class="input" type="text" style="width:100%;" />
    </div>
    <div style="display:flex;gap:8px;justify-content:flex-end;">
      <button id="app-dialog-cancel" class="btn btn-secondary" onclick="_dialogResolve(null)">Cancel</button>
      <button id="app-dialog-ok" class="btn btn-primary" onclick="_dialogResolve(true)">OK</button>
    </div>
  </div>
</div>

<!-- Toast -->
<div class="toast" id="toast"></div>

<!-- Voice button -->
<button id="voice-btn" title="Voice command (click to speak)">&#127908;</button>

<!-- Voice overlay -->
<div id="voice-overlay">
  <div style="font-size:11px;font-weight:600;color:rgba(255,255,255,0.45);text-transform:uppercase;letter-spacing:0.08em;margin-bottom:5px;" id="voice-status">Listening&#8230;</div>
  <div id="voice-transcript"></div>
  <div id="voice-action"></div>
</div>

"""

with open(OUT, "w", encoding="utf-8") as f:
    f.write(NEW_HTML)
    f.write(js_section)

lines_written = sum(1 for _ in open(OUT, encoding="utf-8"))
print(f"Written {lines_written} lines to {OUT}")
