
// ── Custom dialog system (replaces browser prompt/confirm) ──────────────────
let _dialogResolveFn = null;
function _dialogResolve(value) {
  const dlg = document.getElementById('app-dialog');
  if (dlg) dlg.style.display = 'none';
  if (_dialogResolveFn) { const fn = _dialogResolveFn; _dialogResolveFn = null; fn(value); }
}
function _showDialog(title, msg, opts = {}) {
  return new Promise(resolve => {
    // Resolve any existing dialog with null before opening a new one
    if (_dialogResolveFn) { const prev = _dialogResolveFn; _dialogResolveFn = null; prev(null); }
    _dialogResolveFn = resolve;
    const dlg = document.getElementById('app-dialog');
    document.getElementById('app-dialog-title').textContent = title || '';
    document.getElementById('app-dialog-msg').innerHTML = msg || '';
    const inputWrap = document.getElementById('app-dialog-input-wrap');
    const input = document.getElementById('app-dialog-input');
    const okBtn = document.getElementById('app-dialog-ok');
    const cancelBtn = document.getElementById('app-dialog-cancel');
    if (opts.input) {
      inputWrap.style.display = '';
      input.value = opts.defaultValue || '';
      input.placeholder = opts.placeholder || '';
    } else {
      inputWrap.style.display = 'none';
    }
    okBtn.textContent = opts.okText || 'OK';
    okBtn.className = opts.destructive ? 'btn btn-primary' : 'btn btn-primary';
    if (opts.destructive) { okBtn.style.background = 'var(--red)'; okBtn.style.borderColor = 'var(--red)'; }
    else { okBtn.style.background = ''; okBtn.style.borderColor = ''; }
    cancelBtn.style.display = opts.hideCancel ? 'none' : '';
    okBtn.onclick = () => {
      if (opts.input) _dialogResolve(input.value);
      else _dialogResolve(true);
    };
    cancelBtn.onclick = () => _dialogResolve(null);
    dlg.style.display = 'flex';
    if (opts.input) setTimeout(() => input.focus(), 50);
    else setTimeout(() => okBtn.focus(), 50);
    // Keyboard: Enter=OK, Escape=Cancel
    const handler = (e) => {
      if (e.key === 'Escape') { dlg.removeEventListener('keydown', handler); _dialogResolve(null); }
      if (e.key === 'Enter' && !e.shiftKey) { dlg.removeEventListener('keydown', handler); okBtn.click(); }
    };
    dlg.addEventListener('keydown', handler);
  });
}
function customConfirm(msg, opts = {}) {
  return _showDialog(opts.title || 'Confirm', msg, { destructive: opts.destructive, okText: opts.okText });
}
function customPrompt(msg, defaultValue, opts = {}) {
  return _showDialog(opts.title || '', msg, { input: true, defaultValue, placeholder: opts.placeholder });
}

// ── LinkedIn Sales Navigator filter ID mapping ─────────────────────────────
const SALESNAV_IDS = {
  seniority: { Manager: 5, Director: 6, VP: 7, CxO: 8, Partner: 9, Owner: 10 },
  industry: { 'Oil & Gas': 57, 'Mining & Metals': 56, Utilities: 59, Construction: 48, Chemicals: 46, Energy: 58, 'Environmental Services': 86, 'Maritime': 52, 'Renewables & Environment': 60, 'Logistics & Supply Chain': 55 },
  region: {
    // Middle East
    'United Arab Emirates': 104305776, 'Saudi Arabia': 100459316, 'Qatar': 104350532,
    'Kuwait': 101728296, 'Bahrain': 100903498, 'Oman': 105538498, 'Iraq': 103883259,
    'Jordan': 106413498, 'Lebanon': 101834488, 'Israel': 101620260,
    // Africa
    'Egypt': 106155005, 'Nigeria': 105365761, 'South Africa': 104035573,
    'Kenya': 100710714, 'Ghana': 105765362, 'Tanzania': 100961908,
    'Algeria': 104909954, 'Libya': 104579040, 'Morocco': 102787409, 'Tunisia': 102134353,
    'Angola': 102994132, 'Mozambique': 104803956, 'Senegal': 103252560,
    // Asia
    'India': 102713980, 'Singapore': 102454443, 'Malaysia': 106808692,
    'Indonesia': 102478259, 'Thailand': 105146118, 'Vietnam': 104195383,
    'Philippines': 103121230, 'Japan': 101355337, 'South Korea': 105149562,
    'China': 102890883, 'Hong Kong': 103291313, 'Taiwan': 104187078,
    'Pakistan': 101022442, 'Bangladesh': 106535694, 'Sri Lanka': 100446943,
    'Myanmar': 106354748, 'Cambodia': 101230593,
    // Europe
    'United Kingdom': 101165590, 'Germany': 101282230, 'France': 105015875,
    'Netherlands': 102890719, 'Norway': 103819153, 'Sweden': 105117694,
    'Denmark': 104514075, 'Italy': 103350119, 'Spain': 105646813,
    'Switzerland': 106693272, 'Belgium': 100565514, 'Poland': 105072130,
    'Ireland': 104738515, 'Finland': 100456013,
    // TODO: Austria geo ID needs verification (was colliding with Iraq/Egypt). Look up via Sales Nav URL.
    'Portugal': 100364837, 'Czech Republic': 104508036, 'Romania': 106670623,
    'Greece': 104677530, 'Turkey': 102105699,
    // Oceania
    'Australia': 101452733, 'New Zealand': 105490917, 'Papua New Guinea': 106409747,
    // North America
    'United States': 103644278, 'Canada': 101174742, 'Mexico': 103323778,
    // South America
    'Brazil': 106057199, 'Colombia': 100876405,
    // TODO: Argentina geo ID needs verification (was colliding with Colombia/Sri Lanka). Look up via Sales Nav URL.
    'Chile': 104621616, 'Peru': 102927786, 'Ecuador': 106373673,
    'Venezuela': 101490751, 'Trinidad and Tobago': 104780949, 'Guyana': 100881972,
  },
  regionsByContinent: {
    'Middle East': ['United Arab Emirates', 'Saudi Arabia', 'Qatar', 'Kuwait', 'Bahrain', 'Oman', 'Iraq', 'Jordan', 'Lebanon', 'Israel'],
    'Africa': ['Egypt', 'Nigeria', 'South Africa', 'Kenya', 'Ghana', 'Tanzania', 'Algeria', 'Libya', 'Morocco', 'Tunisia', 'Angola', 'Mozambique', 'Senegal'],
    'Asia': ['India', 'Singapore', 'Malaysia', 'Indonesia', 'Thailand', 'Vietnam', 'Philippines', 'Japan', 'South Korea', 'China', 'Hong Kong', 'Taiwan', 'Pakistan', 'Bangladesh', 'Sri Lanka', 'Myanmar', 'Cambodia'],
    'Europe': ['United Kingdom', 'Germany', 'France', 'Netherlands', 'Norway', 'Sweden', 'Denmark', 'Italy', 'Spain', 'Switzerland', 'Belgium', 'Poland', 'Ireland', 'Austria', 'Finland', 'Portugal', 'Czech Republic', 'Romania', 'Greece', 'Turkey'],
    'Oceania': ['Australia', 'New Zealand', 'Papua New Guinea'],
    'North America': ['United States', 'Canada', 'Mexico'],
    'South America': ['Brazil', 'Argentina', 'Colombia', 'Chile', 'Peru', 'Ecuador', 'Venezuela', 'Trinidad and Tobago', 'Guyana'],
  },
  companySize: { '1-10': 'B', '11-50': 'C', '51-200': 'D', '201-500': 'E', '501-1000': 'F', '1001-5000': 'G', '5001-10000': 'H', '10001+': 'I' },
};

function buildSalesNavUrl(filters) {
  const parts = [];
  const keywords = (filters.titles || []).join(' OR ');
  if (filters.seniorities && filters.seniorities.length) {
    const ids = filters.seniorities.map(s => SALESNAV_IDS.seniority[s]).filter(Boolean);
    if (ids.length) parts.push('(type:SENIORITY_LEVEL,values:List(' + ids.map(id => '(id:' + id + ')').join(',') + '))');
  }
  if (filters.industries && filters.industries.length) {
    const ids = filters.industries.map(i => SALESNAV_IDS.industry[i]).filter(Boolean);
    if (ids.length) parts.push('(type:INDUSTRY,values:List(' + ids.map(id => '(id:' + id + ')').join(',') + '))');
  }
  if (filters.regions && filters.regions.length) {
    const ids = filters.regions.map(r => SALESNAV_IDS.region[r]).filter(Boolean);
    if (ids.length) parts.push('(type:REGION,values:List(' + ids.map(id => '(id:' + id + ')').join(',') + '))');
  }
  if (filters.companySizes && filters.companySizes.length) {
    const codes = filters.companySizes.map(s => SALESNAV_IDS.companySize[s]).filter(Boolean);
    if (codes.length) parts.push('(type:COMPANY_HEADCOUNT,values:List(' + codes.map(c => '(id:' + c + ')').join(',') + '))');
  }
  const filterStr = parts.length ? 'filters:List(' + parts.join(',') + ')' : '';
  // Build query=(keywords:...,filters:List(...)) — all inside one parenthesized block
  const queryParts = [];
  if (keywords) queryParts.push('keywords:' + encodeURIComponent(keywords));
  if (filterStr) queryParts.push(filterStr);
  const url = 'https://www.linkedin.com/sales/search/people?query=(' + queryParts.join(',') + ')';
  return url;
}

// Render continent → country region picker for Sales Nav filters
function renderSalesNavRegions() {
  const container = document.getElementById('preconf-sn-region-container');
  if (!container) return;
  const continents = SALESNAV_IDS.regionsByContinent;
  container.innerHTML = Object.entries(continents).map(([continent, countries]) => {
    const cid = 'sn-continent-' + continent.replace(/\s+/g, '-').toLowerCase();
    const countryCheckboxes = countries.map(c =>
      `<label style="font-size:11px;cursor:pointer;white-space:nowrap;"><input type="checkbox" class="preconf-sn-region" value="${c}"> ${c}</label>`
    ).join('');
    return `<div style="margin-bottom:6px;">
      <div style="display:flex;align-items:center;gap:6px;cursor:pointer;" onclick="document.getElementById('${cid}').classList.toggle('hidden');this.querySelector('.sn-arrow').textContent=document.getElementById('${cid}').classList.contains('hidden')?'\\u25B6':'\\u25BC'">
        <span class="sn-arrow" style="font-size:9px;color:var(--text-muted);">&#9654;</span>
        <label style="font-size:12px;font-weight:600;color:var(--navy);cursor:pointer;">
          <input type="checkbox" onchange="toggleSalesNavContinent(this,'${cid}')" style="margin-right:4px;">${continent}
        </label>
      </div>
      <div id="${cid}" class="hidden" style="display:flex;gap:8px;flex-wrap:wrap;padding:4px 0 2px 18px;">${countryCheckboxes}</div>
    </div>`;
  }).join('');
}

function toggleSalesNavContinent(masterCb, containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;
  // Expand if checking
  if (masterCb.checked) container.classList.remove('hidden');
  container.querySelectorAll('.preconf-sn-region').forEach(cb => { cb.checked = masterCb.checked; });
  // Update arrow
  const arrow = masterCb.closest('div')?.parentElement?.querySelector('.sn-arrow');
  if (arrow) arrow.textContent = container.classList.contains('hidden') ? '\u25B6' : '\u25BC';
}

const API = '/api';
let currentTab = 'outreach';
let selectedProspects = new Set();
let activeCampaignId = null;
let builderStep = 1;
let builderInboxSelection = [];

// ── Campaign Wizard state ──────────────────────────────────────────────────
let cwStep = 0;        // current wizard step (0 = not in wizard)
let cwCampaignId = null;
let cwData = {};       // collects form data across steps
let analyticsEditCampaignId = null;
let confLeads = [];
let confLeadsPage = 0;
let confLeadsTotal = 0;
const CONF_LEADS_PAGE_SIZE = 100;
let confModalLeadId = null;
let confInboxes = [];
let _outlookAuthInboxId = 'outlook';
let confActiveStep = 1;
let confCurrentEvent = '';
let _apolloConfPreviewResults = [];

const tagState = { titles: [], industries: [], locations: [] };

// ── Sequence shape config (single source of truth, 2026-04-11) ──────────────
// Populated once at boot from GET /config/sequence-defaults. All code that
// previously hardcoded the 6-email / day-offset / limit arrays now reads
// from this object so raising MAX_SEQUENCE_EMAILS on the backend is a
// single one-line change. Falls back to the historical defaults if the
// endpoint fails so the UI still renders on first-run installs.
let seqDefaults = {
  max_sequence_emails: 6,
  default_email_day_offsets: [1, 7, 12, 18, 24, 30],
  default_linkedin_connect_day: 4,
  default_linkedin_message_day: 16,
  default_email_word_limit: 50,
  email_word_limits: { email1: 100, email2: 60, email3: 60, email4: 60, email5: 50, email6: 50 },
  linkedin_word_limits: { linkedin_connect_note: 30, linkedin_message: 50 },
};
async function loadSeqDefaults() {
  try {
    const d = await api('/config/sequence-defaults');
    if (d && typeof d === 'object') seqDefaults = { ...seqDefaults, ...d };
  } catch (e) {
    // Keep the hardcoded fallback  -  first-run installs hit this path.
    console.warn('loadSeqDefaults failed, using fallback:', e.message);
  }
}

// ── Project-search provider (single source of truth for UI wording) ─────────
// Backend is src/services/project_search.py (DuckDuckGo via ddgs). Previously
// Tavily; migrated 2026-04-09. If this ever changes again, update ONLY this
// constant  -  HTML labels with [data-ps-label] and all JS sites read from here.
const PROJECT_SEARCH_PROVIDER = {
  key: 'project_search',
  name: 'DuckDuckGo',
  displayName: 'DuckDuckGo Scraper',
  color: '#636e72',
  desc: 'Free web-scraping search. Finds recent company projects and press releases to inject into your emails for relevance. No API key required.',
  // Backend cost-settings field is still named `tavily_monthly` for data continuity.
  costField: 'tavily_monthly',
};

function applyProjectSearchLabels() {
  const name = PROJECT_SEARCH_PROVIDER.displayName;
  document.querySelectorAll('[data-ps-label]').forEach(el => {
    const mode = el.getAttribute('data-ps-label') || 'text';
    if (mode === 'placeholder') el.setAttribute('placeholder', name);
    else el.textContent = name;
  });
}

// ── Monochrome line-icon system ─────────────────────────────────────────────
// Single source of truth. Feather-style 24x24 viewBox, stroke=currentColor,
// no fill. All previously-colored/emoji icons across the app route through
// icon(name, size) and inherit the parent element's CSS color. To add an icon
// just put its inner SVG body in ICONS and reference it via icon('name') in JS
// templates or <span data-icon="name" data-icon-size="24"></span> in HTML.
const ICONS = {
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  zap: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
  'bar-chart': '<line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/>',
  send: '<line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>',
  users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  clipboard: '<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/>',
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
  'map-pin': '<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>',
  award: '<circle cx="12" cy="8" r="7"/><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"/>',
  'message-circle': '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>',
  mail: '<path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1 .9-2 2-2z"/><polyline points="22,6 12,13 2,6"/>',
  'check-circle': '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',
  eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
  'thumbs-up': '<path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/>',
  'alert-triangle': '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  search: '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
  cpu: '<rect x="4" y="4" width="16" height="16" rx="2" ry="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/>',
  'file-text': '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/>',
  paperclip: '<path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
  x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  check: '<polyline points="20 6 9 17 4 12"/>',
  inbox: '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
  edit: '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>',
  clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  phone: '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>',
  square: '<rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>',
};
function icon(name, size) {
  const s = size || 14;
  const body = ICONS[name] || '';
  return '<svg width="' + s + '" height="' + s + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:inline-block;vertical-align:-0.15em;flex-shrink:0;">' + body + '</svg>';
}
function replaceHtmlIcons(root) {
  const r = root || document;
  r.querySelectorAll('[data-icon]').forEach(el => {
    if (el.getAttribute('data-icon-ready') === '1') return;
    const n = el.getAttribute('data-icon');
    const size = parseInt(el.getAttribute('data-icon-size') || '16', 10);
    el.innerHTML = icon(n, size);
    el.setAttribute('data-icon-ready', '1');
  });
}

// ── Auth state ──────────────────────────────────────────────────────────────
let _authToken = localStorage.getItem('auth_token') || null;
let _currentUser = null;
let _allUsers = [];
let _authMode = 'login'; // 'login' or 'register'

function toggleAuthMode() {
  _authMode = _authMode === 'login' ? 'register' : 'login';
  const nameField = document.getElementById('auth-name-field');
  const submitBtn = document.getElementById('auth-submit-btn');
  const toggleBtn = document.getElementById('auth-toggle');
  const subtitle = document.querySelector('#auth-screen p');
  if (_authMode === 'register') {
    nameField.style.display = '';
    submitBtn.textContent = 'Create Account';
    toggleBtn.textContent = 'Already have an account? Sign in';
    if (subtitle) subtitle.textContent = 'Create your account';
  } else {
    nameField.style.display = 'none';
    submitBtn.textContent = 'Sign In';
    toggleBtn.textContent = "Don't have an account? Register";
    if (subtitle) subtitle.textContent = 'Sign in to your account';
  }
  document.getElementById('auth-error').style.display = 'none';
}

async function handleAuth(e) {
  e.preventDefault();
  const errEl = document.getElementById('auth-error');
  errEl.style.display = 'none';
  const btn = document.getElementById('auth-submit-btn');
  const origText = btn.textContent;
  btn.disabled = true; btn.textContent = _authMode === 'register' ? 'Creating Account…' : 'Signing In…';
  const email = document.getElementById('auth-email').value.trim();
  const password = document.getElementById('auth-password').value;
  const name = document.getElementById('auth-name').value.trim();

  const endpoint = _authMode === 'register' ? '/auth/register' : '/auth/login';
  const body = _authMode === 'register'
    ? { name, email, password }
    : { email, password };

  try {
    const res = await fetch(API + endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `${res.status} ${res.statusText}`);
    }
    const data = await res.json();
    _authToken = data.token;
    _currentUser = data.user;
    localStorage.setItem('auth_token', _authToken);
    // Clear ALL cached data from previous user session to prevent
    // stale data flashing before fresh API calls return.
    wfSequences = [];
    wfOrchMap = {};
    seqAllStates = [];
    seqAllTasks = [];
    _cachedCampaigns = [];
    allProspects = [];
    confLeads = [];
    confActiveStep = 1;
    confCurrentEvent = '';
    _apolloConfPreviewResults = [];
    nlList = [];
    nlCurrent = null;
    activeCampaignId = null;
    wfCampaignId = null;
    editingCampaignId = null;
    _confAnalyticsData = null;
    showApp();
  } catch (err) {
    errEl.textContent = err.message;
    errEl.style.display = '';
  } finally {
    btn.disabled = false; btn.textContent = origText;
  }
}

// ── Unsaved-work snapshot (FIX-22) ───────────────────────────────────────────
const UNSAVED_SNAPSHOT_KEY = 'unsaved_form_snapshot_v1';
function snapshotUnsavedWork() {
  try {
    const snap = {};
    const nodes = document.querySelectorAll('textarea, input[type="text"]');
    nodes.forEach(el => {
      const id = el.id || el.name;
      if (!id) return;
      const val = el.value;
      if (val && val.trim()) snap[id] = val;
    });
    if (Object.keys(snap).length) {
      localStorage.setItem(UNSAVED_SNAPSHOT_KEY, JSON.stringify({ ts: Date.now(), fields: snap }));
    }
  } catch (_) {}
}
function restoreUnsavedWork() {
  try {
    const raw = localStorage.getItem(UNSAVED_SNAPSHOT_KEY);
    if (!raw) return;
    const snap = JSON.parse(raw);
    if (!snap || !snap.fields) return;
    let restored = 0;
    Object.keys(snap.fields).forEach(id => {
      const el = document.getElementById(id) || document.querySelector(`[name="${id}"]`);
      if (el && !el.value) { el.value = snap.fields[id]; restored++; }
    });
    if (restored > 0) toast(`Restored ${restored} unsaved field${restored === 1 ? '' : 's'} from your previous session.`, 'info');
    localStorage.removeItem(UNSAVED_SNAPSHOT_KEY);
  } catch (_) {}
}

let _sessionExpiryWarningTimer = null;
function scheduleSessionExpiryWarning() {
  if (_sessionExpiryWarningTimer) { clearTimeout(_sessionExpiryWarningTimer); _sessionExpiryWarningTimer = null; }
  if (!_authToken) return;
  try {
    const parts = _authToken.split('.');
    if (parts.length < 2) return;
    const payload = JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')));
    if (!payload.exp) return;
    const msUntilExp = payload.exp * 1000 - Date.now();
    const msUntilWarn = msUntilExp - 5 * 60 * 1000;
    if (msUntilWarn > 0) {
      _sessionExpiryWarningTimer = setTimeout(() => {
        toast('Your session expires in 5 minutes. Save your work.', 'warning');
      }, msUntilWarn);
    }
  } catch (_) {}
}

function logoutUser() {
  snapshotUnsavedWork();
  if (_sessionExpiryWarningTimer) { clearTimeout(_sessionExpiryWarningTimer); _sessionExpiryWarningTimer = null; }
  _authToken = null;
  _currentUser = null;
  localStorage.removeItem('auth_token');
  showAuthScreen();
}

function showResetPasswordForm() {
  document.getElementById('auth-screen').style.display = 'none';
  document.getElementById('reset-screen').style.display = 'flex';
  document.getElementById('reset-error').style.display = 'none';
  document.getElementById('reset-success').style.display = 'none';
  document.getElementById('reset-form').style.display = '';
  document.getElementById('reset-form').reset();
}

function hideResetPasswordForm() {
  document.getElementById('reset-screen').style.display = 'none';
  document.getElementById('auth-screen').style.display = 'flex';
}

async function handleForgotPassword(e) {
  e.preventDefault();
  const errEl = document.getElementById('reset-error');
  const successEl = document.getElementById('reset-success');
  errEl.style.display = 'none';
  successEl.style.display = 'none';

  const email = document.getElementById('reset-email').value.trim();
  if (!email) {
    errEl.textContent = 'Please enter your email.';
    errEl.style.display = '';
    return;
  }

  const btn = document.getElementById('reset-submit-btn');
  btn.disabled = true; btn.textContent = 'Sending…';

  try {
    const res = await fetch(API + '/auth/forgot-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    const data = await res.json();
    successEl.textContent = data.message || 'If an account with that email exists, a reset link has been sent. Check your inbox.';
    successEl.style.display = '';
    document.getElementById('reset-form').style.display = 'none';
  } catch (err) {
    errEl.textContent = 'Network error. Please try again.';
    errEl.style.display = '';
  } finally {
    btn.disabled = false; btn.textContent = 'Send Reset Link';
  }
}

function showAuthScreen() {
  document.getElementById('auth-screen').style.display = 'flex';
  document.getElementById('reset-screen').style.display = 'none';
  document.querySelector('.sidebar').style.display = 'none';
  document.querySelector('.main-wrap').style.display = 'none';
}

async function showApp() {
  // Clear stale DOM from any previous user session BEFORE making the
  // app visible. This prevents the brief flash of another user's data.
  // Clear wizard state
  const cwWizard = document.getElementById('campaign-wizard');
  if (cwWizard) cwWizard.classList.add('hidden');
  const cwDash = document.getElementById('cw-dashboard-content');
  if (cwDash) cwDash.innerHTML = '';
  const wfEmpty = document.getElementById('wf-empty-state');
  if (wfEmpty) wfEmpty.style.display = '';
  const pTbody = document.getElementById('prospects-tbody');
  if (pTbody) pTbody.innerHTML = '';
  const confTbody = document.getElementById('conf-tbody');
  if (confTbody) confTbody.innerHTML = '';
  const confAnalytics = document.getElementById('conf-analytics');
  if (confAnalytics) confAnalytics.innerHTML = '';
  // Reset campaign dropdown to empty
  const wfSel = document.getElementById('wf-campaign-select');
  if (wfSel) wfSel.innerHTML = '<option value="">Loading campaigns…</option>';

  document.getElementById('auth-screen').style.display = 'none';
  document.querySelector('.sidebar').style.display = '';
  document.querySelector('.main-wrap').style.display = '';
  // Update sidebar user info
  if (_currentUser) {
    const userEl = document.getElementById('sidebar-user');
    userEl.style.display = '';
    document.getElementById('sidebar-user-name').textContent = _currentUser.name;
    document.getElementById('sidebar-user-email').textContent = _currentUser.email;
    const initials = (_currentUser.name || '?').split(' ').map(w => w[0] || '').join('').toUpperCase().slice(0, 2) || '?';
    document.getElementById('sidebar-user-avatar').textContent = initials;
  }
  // Load users list for filters
  try { _allUsers = await api('/auth/users'); } catch (_) { _allUsers = []; }
  // Boot the app - must await so campaign list loads before UI renders
  _booted = false;  // Reset so bootApp runs fresh for the new user
  await bootApp();
  // Restore unsaved work from a prior session (FIX-22)
  setTimeout(restoreUnsavedWork, 500);
  // Schedule 5-minute-before-expiry warning (FIX-22)
  scheduleSessionExpiryWarning();
}

async function checkAuth() {
  // Try existing token
  if (_authToken) {
    try {
      const res = await fetch(API + '/auth/me', {
        headers: { 'Authorization': 'Bearer ' + _authToken },
      });
      if (res.ok) {
        const data = await res.json();
        if (data.user) {
          _currentUser = data.user;
          showApp();
          return;
        }
      }
    } catch (_) {}
    // Token invalid
    _authToken = null;
    localStorage.removeItem('auth_token');
  }
  // Try cookie auth (legacy)  -  only to check if users exist
  try {
    const res = await fetch(API + '/auth/me', { credentials: 'same-origin' });
    if (res.ok) {
      const data = await res.json();
      if (data.user) {
        _currentUser = data.user;
        showApp();
        return;
      }
      // No users yet  -  show register screen so first user can sign up
      if (data.auth_required === false) {
        _authMode = 'login';
        toggleAuthMode();
        showAuthScreen();
        return;
      }
    }
  } catch (_) {}
  showAuthScreen();
}

const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
const escBody = s => {
  // Escape all HTML, then restore only safe <a href="https://...">text</a> links
  let t = esc(s);
  return t.replace(/&lt;a href=&quot;(https?:\/\/[^&"]+)&quot;&gt;(.+?)&lt;\/a&gt;/gi,
    (_, url, text) => `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(text)}</a>`);
};

// ── Tab switching ──────────────────────────────────────────────────────────────
document.querySelectorAll('.header-tab').forEach(tab => {
  tab.addEventListener('click', () => switchTab(tab.dataset.tab));
  tab.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); switchTab(tab.dataset.tab); } });
});

function switchTab(name) {
  document.querySelectorAll('.header-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));

  // Outreach tab uses display:contents for two-panel layout
  const outreachEl = document.getElementById('tab-outreach');
  if (name === 'outreach') {
    outreachEl.style.display = 'contents';
    outreachEl.classList.remove('hidden');
  } else {
    outreachEl.style.display = 'none';
    outreachEl.classList.add('hidden');
  }

  // Other tabs show as full-width panels
  ['conference','pre-conference','copywriting','replies','inboxes','inbox-mgmt','sequences','newsletters','gtm-analytics','maxine','workflow','settings','analytics','leads'].forEach(tab => {
    const el = document.getElementById('tab-' + tab);
    if (!el) return;
    if (tab === name) {
      el.classList.remove('hidden');
      el.style.display = 'block';
      el.style.flex = '1';
      el.style.overflowY = 'auto';
      el.style.padding = '26px 30px 40px';
    } else {
      el.classList.add('hidden');
      el.style.display = 'none';
    }
  });

  // Toggle top bars
  const outreachBar = document.getElementById('outreach-top-bar');
  const otherBar = document.getElementById('other-top-bar');
  if (outreachBar) outreachBar.style.display = name === 'outreach' ? 'flex' : 'none';
  if (otherBar) {
    otherBar.style.display = name === 'outreach' ? 'none' : 'flex';
    const titleMap = { conference:'Post-Conference Outreach', 'pre-conference':'Pre-Conference Outreach', copywriting:'Writing Style', sequences:'Sequences', newsletters:'Newsletters', 'gtm-analytics':'GTM Analytics', maxine:'Follow-ups', workflow:'How It Works', settings:'Settings', analytics:'Analytics', leads:'Contacts' };
    const titleEl = document.getElementById('other-tab-title');
    if (titleEl) titleEl.textContent = titleMap[name] || '';
  }

  currentTab = name;

  // Show/hide campaign sub-nav
  const campaignTabs = ['outreach','leads','analytics','sequences','copywriting'];
  const subNav = document.getElementById('campaign-sub-nav');
  if (subNav) {
    const isCampaignTab = campaignTabs.includes(name);
    if (name === 'outreach') {
      // Toggle: clicking Campaigns toggles sub-nav open/closed
      const isOpen = subNav.style.display !== 'none';
      subNav.style.display = isOpen ? 'none' : 'block';
    } else if (isCampaignTab) {
      // Clicking a sub-tab keeps it open
      subNav.style.display = 'block';
    }
    // Update chevron rotation
    const chevron = document.getElementById('campaign-chevron');
    if (chevron) {
      chevron.style.transform = subNav.style.display !== 'none' ? 'rotate(0deg)' : 'rotate(-90deg)';
    }
    // Keep Campaigns parent highlighted when a sub-tab is active
    const campaignParent = document.querySelector('[data-tab="outreach"]');
    if (campaignParent && isCampaignTab && name !== 'outreach') {
      campaignParent.classList.add('active');
    }
  }

  // Show/hide events sub-nav
  const eventsTabs = ['conference', 'pre-conference'];
  const eventsSubNav = document.getElementById('events-sub-nav');
  if (eventsSubNav) {
    const isEventsTab = eventsTabs.includes(name);
    if (name === 'events-parent') {
      // Clicking the Events parent toggles sub-nav and defaults to post-conference
      const isOpen = eventsSubNav.style.display !== 'none';
      if (isOpen) {
        eventsSubNav.style.display = 'none';
      } else {
        eventsSubNav.style.display = 'block';
        // Auto-navigate to post-conference when expanding
        switchTab('conference');
        return;
      }
    } else if (isEventsTab) {
      eventsSubNav.style.display = 'block';
    }
    const evChevron = document.getElementById('events-chevron');
    if (evChevron) {
      evChevron.style.transform = eventsSubNav.style.display !== 'none' ? 'rotate(0deg)' : 'rotate(-90deg)';
    }
    // Keep Events parent highlighted when a sub-tab is active
    const eventsParent = document.querySelector('[data-tab="events-parent"]');
    if (eventsParent && isEventsTab) {
      eventsParent.classList.add('active');
    }
  }

  if (name === 'outreach') loadSequences();
  if (name === 'conference') loadConference().catch(() => {});
  if (name === 'pre-conference') loadPreConference().catch(() => {});
  if (name === 'copywriting') { loadSalesCopy(); loadConfCopy(); loadNlCopy(); loadWritingStyle(); loadEmailInstructions(); loadOutlookSignature(); }
  if (name === 'sequences') { initDesignSteps(); }
  if (name === 'settings') { loadCostSettings(); loadProjectSearchSetting(); initWebhookUrl(); loadPbCredentials(); loadLinkedInAccounts(); }
  if (name === 'leads') { _showLeadsTab(); }
  if (name === 'analytics') { loadAnalytics(); }
  if (name === 'newsletters') { loadNewsletters(); }
  if (name === 'gtm-analytics') { loadGtmAnalytics(); checkGtmInboundStatus(); }
  if (name === 'maxine') { loadFuConfig(); loadFuDeals(); }
}

// ── Accordion ─────────────────────────────────────────────────────────────────
function toggleAccordion(name) {
  // In the new layout, leads/workflow/analytics are always visible panels, not accordions
  if (name === 'leads' || name === 'workflow' || name === 'analytics') {
    if (name === 'analytics') loadAnalytics();
    if (name === 'workflow') loadWorkflowCampaigns();
    return;
  }
  const body = document.getElementById('body-' + name);
  const chevron = document.getElementById('chevron-' + name);
  if (!body || !chevron) return;
  const isOpen = body.classList.contains('open');
  body.classList.toggle('open', !isOpen);
  chevron.classList.toggle('open', !isOpen);
  // Lazy-load data when accordion opens for the first time
  if (!isOpen) {
    const loaders = { 'api-quotas': loadApiQuotas, 'timezone': loadTimezone, 'calendly': loadCalendlyLink, 'suppression': loadSuppressionList };
    if (loaders[name] && !body.dataset.loaded) { loaders[name](); body.dataset.loaded = '1'; }
  }
}

function openAccordion(name) {
  if (name === 'leads' || name === 'workflow' || name === 'analytics') {
    if (name === 'analytics') loadAnalytics();
    if (name === 'workflow') loadWorkflowCampaigns();
    return;
  }
  const body = document.getElementById('body-' + name);
  const chevron = document.getElementById('chevron-' + name);
  if (!body || !chevron) return;
  if (!body.classList.contains('open')) {
    body.classList.add('open');
    chevron.classList.add('open');
  }
}

// ── Navigate (called by voice and legacy code) ────────────────────────────────
function navigate(section) {
  if (section === 'conference') { switchTab('conference'); return; }
  if (section === 'pre-conference') { switchTab('pre-conference'); return; }
  if (section === 'inboxes') { switchTab('inboxes'); return; }
  if (section === 'style') { switchTab('copywriting'); return; }
  // Everything else goes to outreach tab
  switchTab('outreach');
  if (section === 'analytics') loadAnalytics();
  if (section === 'workflow' || section === 'builder' || section === 'campaigns' || section === 'email') loadWorkflowCampaigns();
}

// ── Reply Management (removed — Instantly-era feature) ───────────────────────
// Reply classification labels retained for analytics renderTimeToReply.
function loadReplyStats() {} // stub — removed
function loadReplies() {} // stub — removed
function openReplyModal() {} // stub — removed
function closeReplyModal() {} // stub — removed
function replyViaEmail() {} // stub — removed
function replyAction() {} // stub — removed
function replyActionFromModal() {} // stub — removed

// Stubs above avoid runtime errors from any residual callers.
// Original implementation removed.

// ── (end of reply stubs) ──────────────────────────────────────────────────────

// (original reply function bodies removed below)

// ── (dead reply code removed) ──

// ── Apollo filter toggle ───────────────────────────────────────────────────────
function toggleLeadsFilter() {
  const body = document.getElementById('leads-filter-body');
  const arrow = document.getElementById('leads-filter-arrow');
  const isOpen = body.classList.contains('open');
  body.classList.toggle('open', !isOpen);
  arrow.textContent = isOpen ? '▶' : '▼';
  if (!isOpen) {
    setupTagInput('titles-input', 'titles-wrap', 'titles');
    setupTagInput('industries-input', 'industries-wrap', 'industries');
    setupTagInput('locations-input', 'locations-wrap', 'locations');
  }
}

// ── Save indicator helper ──────────────────────────────────────────────────────
function showSaveIndicator(elementId) {
  let el = document.getElementById(elementId);
  if (!el) return;
  el.textContent = 'Saved';
  el.style.color = 'var(--green)';
  setTimeout(() => { el.textContent = ''; }, 2000);
}

// ── API helpers ────────────────────────────────────────────────────────────────
async function api(path, opts = {}) {
  // Auth via JWT bearer token (primary) or __session cookie (fallback).
  const headers = {
    'Content-Type': 'application/json',
    ...(opts.headers || {}),
  };
  if (_authToken) {
    headers['Authorization'] = 'Bearer ' + _authToken;
  }
  const res = await fetch(API + path, { ...opts, headers, credentials: 'same-origin' });
  if (res.status === 401) {
    // Token expired  -  force re-login
    logoutUser();
    throw new Error('Session expired. Please log in again.');
  }
  if (!res.ok) {
    // Gracefully handle non-JSON error bodies (e.g. 502 nginx HTML pages)
    let detail = res.statusText;
    try {
      const err = await res.json();
      const raw = err.detail || err.message || detail;
      detail = (typeof raw === 'object' && raw !== null) ? (raw.message || JSON.stringify(raw)) : raw;
    } catch (_) { /* body was not JSON  -  keep statusText */ }
    // Actionable error messages for common status codes
    const friendlyPrefix = {
      402: 'Insufficient credits. Check your API billing.',
      403: "You don't have access to this resource.",
      404: 'Not found  -  it may have been deleted.',
      429: 'Rate limit hit. Wait a moment and try again.',
      500: 'Server error. Try again or check the logs.',
    }[res.status] || '';
    const msg = friendlyPrefix ? `${friendlyPrefix} (${detail})` : `${res.status} ${detail}`;
    throw new Error(msg);
  }
  if (res.status === 204) return {};
  return res.json();
}

// ── Panel collapse (full left/right half) ─────────────────────────────────────
// Left button (◀/▶) toggles leads panel. Right button (◀/▶) toggles campaign panel.
// Only one panel can be collapsed at a time.
function toggleSidebar() {
  const sb = document.querySelector('.sidebar');
  const btn = document.getElementById('sidebar-expand-btn');
  sb.classList.toggle('collapsed');
  const isCollapsed = sb.classList.contains('collapsed');
  try { localStorage.setItem('sidebar_collapsed', isCollapsed ? '1' : '0'); } catch(_) {}
}

// Restore sidebar state on load
try {
  if (localStorage.getItem('sidebar_collapsed') === '1') {
    document.querySelector('.sidebar').classList.add('collapsed');
  }
} catch(_) {}

function togglePanel(which) {
  const lp = document.getElementById('leads-panel');
  const cp = document.getElementById('campaign-panel');
  const lc = lp.classList.contains('panel-collapsed');
  const rc = cp.classList.contains('panel-collapsed');

  if (which === 'leads') {
    if (lc) {
      lp.classList.remove('panel-collapsed');
      try { localStorage.setItem('panel_leads_collapsed', '0'); } catch(_) {}
    } else {
      if (rc) cp.classList.remove('panel-collapsed');
      lp.classList.add('panel-collapsed');
      try { localStorage.setItem('panel_leads_collapsed', '1'); localStorage.setItem('panel_campaign_collapsed', '0'); } catch(_) {}
    }
  } else {
    if (rc) {
      cp.classList.remove('panel-collapsed');
      try { localStorage.setItem('panel_campaign_collapsed', '0'); } catch(_) {}
    } else {
      if (lc) lp.classList.remove('panel-collapsed');
      cp.classList.add('panel-collapsed');
      try { localStorage.setItem('panel_campaign_collapsed', '1'); localStorage.setItem('panel_leads_collapsed', '0'); } catch(_) {}
    }
  }
  // Apply expanded class when the other panel is collapsed
  const leadsCollapsed = lp.classList.contains('panel-collapsed');
  const campCollapsed = cp.classList.contains('panel-collapsed');
  lp.classList.toggle('panel-expanded', campCollapsed && !leadsCollapsed);
  cp.classList.toggle('panel-expanded', leadsCollapsed && !campCollapsed);
  updateDividerButtons();
}

function updateDividerButtons() {
  const lc = document.getElementById('leads-panel').classList.contains('panel-collapsed');
  const rc = document.getElementById('campaign-panel').classList.contains('panel-collapsed');
  const btnL = document.getElementById('toggle-leads');
  const btnR = document.getElementById('toggle-campaign');
  // Left button: ◀ = collapse leads, ▶ = expand leads
  btnL.innerHTML = lc ? '&#9654;' : '&#9664;';
  btnL.title = lc ? 'Expand leads' : 'Collapse leads';
  // Right button: ▶ = collapse campaigns, ◀ = expand campaigns
  btnR.innerHTML = rc ? '&#9664;' : '&#9654;';
  btnR.title = rc ? 'Expand campaigns' : 'Collapse campaigns';
}

function restorePanelStates() {
  try {
    const lp = document.getElementById('leads-panel');
    const cp = document.getElementById('campaign-panel');
    const lc = localStorage.getItem('panel_leads_collapsed') === '1';
    const rc = localStorage.getItem('panel_campaign_collapsed') === '1';
    if (lc && rc) localStorage.removeItem('panel_campaign_collapsed');
    if (lc) {
      lp.classList.add('panel-collapsed');
      cp.classList.add('panel-expanded');
    } else if (rc) {
      cp.classList.add('panel-collapsed');
      lp.classList.add('panel-expanded');
    }
    updateDividerButtons();
  } catch(_) {}
}

// ── Collapsible sections ──────────────────────────────────────────────────────
function toggleSection(headerId) {
  const header = document.getElementById(headerId);
  if (!header) return;
  const bodyId = header.dataset.body;
  const body = document.getElementById(bodyId);
  const chevron = header.querySelector('.collapse-chevron');
  if (!body || !chevron) return;
  const isCollapsed = body.classList.toggle('collapsed');
  chevron.classList.toggle('collapsed', isCollapsed);
  // Remember state
  try { localStorage.setItem('collapse_' + headerId, isCollapsed ? '1' : '0'); } catch(_) {}
}
function restoreCollapseStates() {
  document.querySelectorAll('.section-header').forEach(h => {
    try {
      const val = localStorage.getItem('collapse_' + h.id);
      if (val === '1') {
        const body = document.getElementById(h.dataset.body);
        const chevron = h.querySelector('.collapse-chevron');
        if (body) body.classList.add('collapsed');
        if (chevron) chevron.classList.add('collapsed');
      }
    } catch(_) {}
  });
}

let _toastTimer = null;
function toast(msg, type = 'success') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `toast show ${type}`;
  if (_toastTimer) clearTimeout(_toastTimer);
  const duration = type === 'warning' ? 7000 : 4000;
  _toastTimer = setTimeout(() => { el.className = 'toast'; _toastTimer = null; }, duration);
}

function validationBadge(status) {
  if (!status) return '<span style="font-size:11px;color:var(--text-muted);"> - </span>';
  const map = { valid:'badge-green', invalid:'badge-red', 'catch-all':'badge-yellow', unknown:'badge-gray', spamtrap:'badge-red', abuse:'badge-red', 'do_not_mail':'badge-red' };
  return `<span class="badge ${map[status]||'badge-gray'}">${esc(status)}</span>`;
}

function statusBadge(status) {
  const map = { active:'badge-green', draft:'badge-gray', paused:'badge-yellow', completed:'badge-blue', archived:'badge-gray', pending:'badge-gray', enrolled:'badge-blue', opened:'badge-yellow', replied:'badge-green', bounced:'badge-red', unsubscribed:'badge-red', connected:'badge-yellow', messaged:'badge-green' };
  return `<span class="badge ${map[status]||'badge-gray'}">${esc(status)}</span>`;
}

// ── Tag input ──────────────────────────────────────────────────────────────────
function setupTagInput(inputId, wrapId, key) {
  const input = document.getElementById(inputId);
  const wrap = document.getElementById(wrapId);
  if (!input || !wrap) return;
  if (input.dataset.tagBound) return; // prevent duplicate listeners
  input.dataset.tagBound = '1';
  wrap.addEventListener('click', () => input.focus());
  input.addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' || e.key === ',') && input.value.trim()) {
      e.preventDefault();
      addTag(input.value.trim(), wrapId, inputId, key);
    } else if (e.key === 'Backspace' && !input.value && tagState[key].length > 0) {
      removeTag(tagState[key].length - 1, wrapId, inputId, key);
    }
  });
}

function addTag(val, wrapId, inputId, key) {
  if (tagState[key].includes(val)) return;
  tagState[key].push(val);
  renderTags(wrapId, inputId, key);
  document.getElementById(inputId).value = '';
}

function removeTag(idx, wrapId, inputId, key) {
  tagState[key].splice(idx, 1);
  renderTags(wrapId, inputId, key);
}

function renderTags(wrapId, inputId, key) {
  const wrap = document.getElementById(wrapId);
  const input = document.getElementById(inputId);
  wrap.querySelectorAll('.tag').forEach(el => el.remove());
  tagState[key].forEach((t, i) => {
    const tag = document.createElement('span');
    tag.className = 'tag';
    const textNode = document.createTextNode(t + ' ');
    const removeBtn = document.createElement('span');
    removeBtn.className = 'tag-remove';
    removeBtn.textContent = '×';
    removeBtn.addEventListener('click', () => removeTag(i, wrapId, inputId, key));
    tag.appendChild(textNode);
    tag.appendChild(removeBtn);
    wrap.insertBefore(tag, input);
  });
}

// ── Prospects ─────────────────────────────────────────────────────────────────
let allProspects = [];
let prospectsPage = 0;
const PROSPECTS_PAGE_SIZE = 100;
let prospectsTotal = 0;

async function loadProspects() {
  prospectsPage = 0;
  // Segment-first flow: load segments bar, only fetch prospects if a
  // segment is already selected. Otherwise show the empty state.
  await loadSegmentsBar();
  if (activeSegmentId) {
    await fetchProspects();
  } else {
    allProspects = [];
    prospectsTotal = 0;
    renderProspectsTable();
  }
}

async function fetchProspects() {
  try {
    const search = document.getElementById('prospect-search-input')?.value.trim() || '';
    const segParam = activeSegmentId ? `&segment_id=${activeSegmentId}` : '';
    const searchParam = search ? `&search=${encodeURIComponent(search)}` : '';
    const data = await api(`/prospects/?limit=${PROSPECTS_PAGE_SIZE}&offset=${prospectsPage * PROSPECTS_PAGE_SIZE}${segParam}${searchParam}`);
    allProspects = data.prospects;
    prospectsTotal = data.total;
    // Clear selections when table data changes (pagination, segment switch, etc.)
    selectedProspects.clear();
    const selAll = document.getElementById('select-all');
    if (selAll) selAll.checked = false;
    renderProspectsTable(allProspects);
    _updateCampaignBtn();
    const exportBtn = document.getElementById('export-csv-btn');
    if (exportBtn) exportBtn.textContent = `Export (${data.total})`;
    const start = prospectsPage * PROSPECTS_PAGE_SIZE + 1;
    const end = Math.min((prospectsPage + 1) * PROSPECTS_PAGE_SIZE, data.total);
    const pageInfo = data.total > PROSPECTS_PAGE_SIZE ? ` (${start}\u2013${end})` : '';
    document.getElementById('prospects-count').textContent = `${data.total} total${pageInfo}`;
    const metaEl = document.getElementById('leads-meta');
    if (metaEl) metaEl.textContent = `${data.total} prospects`;
    // Update top bar stats
    const topLeads = document.getElementById('top-stat-leads');
    if (topLeads) topLeads.innerHTML = `<strong>${esc(String(data.total))}</strong> leads`;
    renderPaginationControls(data.total);
    updateLeadsHealthBar(data.prospects, data.total);
  } catch (e) {
    toast('Error loading prospects: ' + e.message, 'error');
  }
}

function updateLeadsHealthBar(prospects, total) {
  const bar = document.getElementById('leads-health-bar');
  if (!bar || !total) { if (bar) bar.style.display = 'none'; return; }
  bar.style.display = '';

  // We only have the current page  -  use it for ratios, show total count
  const invalid = ['invalid', 'do_not_mail', 'spamtrap', 'abuse'];
  let validCount = 0, invalidCount = 0, unvalidatedCount = 0, recoveredCount = 0;
  for (const p of prospects) {
    const vs = p.email_validation_status;
    const sub = p.email_validation_sub_status || '';
    if (!vs) { unvalidatedCount++; }
    else if (invalid.includes(vs)) { invalidCount++; }
    else { validCount++; }
    if (sub === 'recovered') recoveredCount++;
  }
  // Scale to total if paginated
  const ratio = total / (prospects.length || 1);
  const estValid = Math.round(validCount * ratio);
  const estInvalid = Math.round(invalidCount * ratio);
  const estUnval = Math.round(unvalidatedCount * ratio);
  const qualified = estValid + estUnval; // unvalidated are still usable

  document.getElementById('lh-total').textContent = `${total} leads`;
  document.getElementById('lh-qualified').textContent = `${qualified} qualified`;
  document.getElementById('lh-invalid').textContent = estInvalid ? `${estInvalid} bounced/invalid` : '';
  document.getElementById('lh-unvalidated').textContent = estUnval ? `${estUnval} unvalidated` : '';
  document.getElementById('lh-recovered').textContent = recoveredCount ? `${recoveredCount} recovered` : '';

  const pctValid = (validCount / (prospects.length || 1)) * 100;
  const pctUnval = (unvalidatedCount / (prospects.length || 1)) * 100;
  const pctInvalid = (invalidCount / (prospects.length || 1)) * 100;
  document.getElementById('lh-bar-valid').style.width = pctValid + '%';
  document.getElementById('lh-bar-unval').style.width = pctUnval + '%';
  document.getElementById('lh-bar-invalid').style.width = pctInvalid + '%';
}

function renderPaginationControls(total) {
  const totalPages = Math.ceil(total / PROSPECTS_PAGE_SIZE);
  let el = document.getElementById('prospects-pagination');
  if (!el) return;
  if (totalPages <= 1) { el.innerHTML = ''; return; }
  el.innerHTML = `
    <button class="btn btn-outline btn-sm" onclick="prospectsPageNav(-1)" ${prospectsPage === 0 ? 'disabled' : ''} style="font-size:11px;" title="Prev.">&#8592; Prev</button>
    <span style="font-size:11px;color:var(--text-muted);">Page ${prospectsPage + 1} of ${totalPages}</span>
    <button class="btn btn-outline btn-sm" onclick="prospectsPageNav(1)" ${prospectsPage >= totalPages - 1 ? 'disabled' : ''} style="font-size:11px;" title="Go to the next page of prospects.">Next &#8594;</button>
  `;
}

async function prospectsPageNav(dir) {
  prospectsPage = Math.max(0, prospectsPage + dir);
  await fetchProspects();
}

let activeSegmentId = null;

async function loadSegmentsBar() {
  try {
    const segs = await api('/prospects/segments');
    const bar = document.getElementById('segments-bar');
    bar.innerHTML = '';

    // "Show all" pill
    const allWrap = document.createElement('div');
    const isAllActive = !activeSegmentId;
    allWrap.style.cssText = `display:flex;align-items:center;gap:0;border:1px solid ${isAllActive ? 'var(--accent)' : 'var(--border)'};overflow:hidden;background:${isAllActive ? 'rgba(30,45,79,0.08)' : 'var(--surface)'};cursor:pointer;`;
    const allBtn = document.createElement('button');
    allBtn.className = 'btn btn-sm';
    allBtn.style.cssText = `border:none;border-radius:0;background:transparent;color:${isAllActive ? 'var(--accent)' : 'var(--text-dim)'};font-weight:${isAllActive ? '600' : '400'};`;
    allBtn.textContent = 'Show all';
    allBtn.addEventListener('click', () => { showAllProspects(); loadSegmentsBar(); });
    allWrap.appendChild(allBtn);
    bar.appendChild(allWrap);

    // Segment pills
    segs.forEach((s) => {
      const isActive = s.id === activeSegmentId;

      const wrap = document.createElement('div');
      wrap.style.cssText = `display:flex;align-items:center;gap:0;border:1px solid ${isActive ? 'var(--accent)' : 'var(--border)'};overflow:hidden;background:${isActive ? 'rgba(30,45,79,0.08)' : 'var(--surface)'};cursor:pointer;`;
      wrap.dataset.segId = s.id;

      const btn = document.createElement('button');
      btn.className = 'btn btn-sm';
      btn.style.cssText = `border:none;border-radius:0;background:transparent;color:${isActive ? 'var(--accent)' : 'var(--text-dim)'};font-weight:${isActive ? '600' : '400'};`;
      btn.textContent = `${s.name} (${s.prospect_count})`;
      btn.addEventListener('click', () => {
        activeSegmentId = s.id;
        applySegment(s.id, s.name);
        loadSegmentsBar();
      });

      const del = document.createElement('button');
      del.className = 'btn btn-sm';
      del.style.cssText = 'border:none;border-left:1px solid var(--border);border-radius:0;background:transparent;color:var(--red);padding:5px 10px;font-size:12px;cursor:pointer;';
      del.textContent = '✕';
      del.title = 'Delete segment and its prospects';
      del.addEventListener('click', (e) => { e.stopPropagation(); deleteSegmentWithProspects(s.id, s.name); });

      wrap.appendChild(btn);
      wrap.appendChild(del);
      bar.appendChild(wrap);
    });

    // "+ Create Segment" button
    const createBtn = document.createElement('button');
    createBtn.className = 'btn btn-primary btn-sm';
    createBtn.style.cssText = 'font-size:11px;white-space:nowrap;';
    createBtn.textContent = '+ Create List';
    createBtn.addEventListener('click', () => {
      // Use the same toggle as the Filters button
      const panel = document.getElementById('leads-filter-body');
      if (!panel) return;
      const wasOpen = panel.classList.contains('open');
      toggleLeadsFilter();
      if (!wasOpen) {
        // Panel just opened — focus the segment name field
        setTimeout(() => {
          const segInput = document.getElementById('filter-segment');
          if (segInput) { segInput.focus(); segInput.value = ''; }
        }, 50);
      }
    });
    bar.appendChild(createBtn);

    // Also populate wizard segment select
    const sel = document.getElementById('cw-segment');
    if (sel) {
      sel.innerHTML = '<option value="">All pending prospects</option>' +
        segs.map(s => `<option value="${esc(s.id)}">${esc(s.name)} (${esc(String(s.prospect_count))})</option>`).join('');
    }

    // Populate datalist for "Save to Segment" autocomplete
    const datalist = document.getElementById('segment-options');
    if (datalist) {
      datalist.innerHTML = segs.map(s => `<option value="${esc(s.name)}">`).join('');
    }
  } catch (e) { console.error('loadSegmentsBar:', e); }
}

async function applySegment(segId, segName) {
  prospectsPage = 0;
  await fetchProspects();
  const lbl = document.getElementById('view-mode-label');
  lbl.style.display = 'inline';
  lbl.textContent = 'Show all';
}

async function showAllProspects() {
  activeSegmentId = null;
  document.getElementById('view-mode-label').style.display = 'none';
  prospectsPage = 0;
  await fetchProspects();
}

async function deleteSegmentWithProspects(segId, segName) {
  if (!await customConfirm(`Delete segment "${segName}" and all its prospects?`, {destructive: true})) return;
  try {
    await api(`/prospects/clear?segment_id=${segId}&confirm=true`, { method: 'DELETE' });
    await api(`/prospects/segments/${segId}`, { method: 'DELETE' });
    if (activeSegmentId === segId) { activeSegmentId = null; }
    toast(`Deleted segment "${segName}"`);
    await fetchProspects();
    await loadSegmentsBar();
  } catch(e) { toast('Delete failed: ' + e.message, 'error'); }
}

async function clearAllProspects() {
  try {
    const res = await api('/prospects/clear?confirm=true', { method: 'DELETE' });
    toast(`Deleted ${res.deleted} prospects.`);
    allProspects = [];
    renderProspectsTable([]);
    document.getElementById('prospects-count').textContent = '0 prospects';
    document.getElementById('leads-meta').textContent = '0 prospects';
    document.getElementById('view-mode-label').style.display = 'none';
    await loadSegmentsBar();
  } catch(e) { toast('Clear failed: ' + e.message, 'error'); }
}

function renderProspectsTable(prospects) {
  const tbody = document.getElementById('prospects-tbody');
  if (!prospects || !prospects.length) {
    const msg = activeSegmentId
      ? 'No contacts in this segment yet.'
      : 'Select a segment above to view its contacts, or click <b>+ Create Segment</b> to search Apollo for new leads.';
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--text-muted);padding:36px;">${msg}</td></tr>`;
    return;
  }
  tbody.innerHTML = prospects.map(p =>
    `<tr>
      <td><input type="checkbox" onchange="toggleSelect('${esc(p.id)}', this)" ${selectedProspects.has(p.id) ? 'checked' : ''} /></td>
      <td style="font-weight:500;">${esc(p.first_name)} ${esc(p.last_name||'')}</td>
      <td style="font-size:12.5px;color:var(--text-muted);">${esc(p.email||'')}</td>
      <td style="font-size:12.5px;">
        <div style="font-weight:500;">${esc(p.company||'')}</div>
        <div style="color:var(--text-muted);font-size:11.5px;">${esc(p.title||'')}</div>
      </td>
      <td>${p.linkedin_url ? `<a href="${esc(p.linkedin_url)}" target="_blank" style="color:var(--blue);font-size:12px;">LinkedIn ↗</a>` : '<span style="color:var(--text-muted);font-size:12px;"> - </span>'}</td>
      <td>${statusBadge(p.status||'pending')}</td>
      <td>${validationBadge(p.email_validation_status)}</td>
      <td><button class="btn btn-secondary btn-sm" style="color:var(--red);" onclick="removeProspect('${esc(p.id)}')" title="Remove this prospect from the database.">✕</button></td>
    </tr>`
  ).join('');
}

async function removeProspect(id) {
  const p = allProspects.find(x => x.id === id);
  const name = p ? `${p.first_name || ''} ${p.last_name || ''}`.trim() || p.email : 'this prospect';
  if (!await customConfirm(`Delete ${name}?`, {destructive: true})) return;
  try {
    await api(`/prospects/${id}`, { method: 'DELETE' });
    allProspects = allProspects.filter(p => p.id !== id);
    selectedProspects.delete(id);
    prospectsTotal = Math.max(0, prospectsTotal - 1);
    renderProspectsTable(allProspects);
    document.getElementById('prospects-count').textContent = `${prospectsTotal} prospects`;
    document.getElementById('leads-meta').textContent = `${prospectsTotal} prospects`;
    const exportBtn = document.getElementById('export-csv-btn');
    if (exportBtn) exportBtn.textContent = `Export (${prospectsTotal})`;
  } catch(e) { toast('Delete failed: ' + e.message, 'error'); }
}

let _filterTimer = null;
function filterProspects() {
  if (_filterTimer) clearTimeout(_filterTimer);
  _filterTimer = setTimeout(() => { prospectsPage = 0; fetchProspects(); }, 300);
}

function _updateCampaignBtn() {
  const btn = document.getElementById('add-to-campaign-btn');
  if (!btn) return;
  const n = selectedProspects.size;
  const sel = document.getElementById('wf-campaign-select');
  const isExisting = sel && sel.value && sel.value !== '__new__' && sel.value !== '';
  const campName = isExisting ? (sel.options[sel.selectedIndex]?.text?.split(' (')[0] || 'Campaign') : '';
  if (n > 0 && isExisting) {
    btn.textContent = `Add ${n} to "${campName}" \u2192`;
  } else if (n > 0) {
    btn.textContent = `Add ${n} to New Campaign \u2192`;
  } else {
    btn.textContent = 'Add to Campaign \u2192';
  }
  btn.disabled = n === 0;
  // Also update validate button label
  const vBtn = document.getElementById('validate-smart-btn');
  if (vBtn) vBtn.textContent = n > 0 ? `Validate ${n} Selected` : 'Validate All';
}

function toggleSelect(id, checkbox) {
  if (checkbox.checked) selectedProspects.add(id);
  else selectedProspects.delete(id);
  _updateCampaignBtn();
}

function toggleSelectAll() {
  const all = document.getElementById('select-all').checked;
  document.querySelectorAll('#prospects-tbody input[type=checkbox]').forEach((cb) => {
    cb.checked = all;
    // Extract prospect ID from the onchange attribute
    const match = cb.getAttribute('onchange')?.match(/toggleSelect\('([^']+)'/);
    const pid = match ? match[1] : null;
    if (!pid) return;
    if (all) selectedProspects.add(pid);
    else selectedProspects.delete(pid);
  });
  _updateCampaignBtn();
}

async function searchProspects() {
  const sizeEl = document.getElementById('filter-size');
  const size = Array.from(sizeEl.querySelectorAll('input[type=checkbox]:checked')).map(cb => cb.value).filter(Boolean).join('|') || '';
  const limit = parseInt(document.getElementById('filter-limit').value) || 50;
  const segment = document.getElementById('filter-segment').value.trim();

  // Segment name is required — contacts without a segment are invisible
  // in the segment-first view.
  if (!segment) {
    toast('Enter a segment name. Contacts must belong to a segment.', 'error');
    document.getElementById('filter-segment').focus();
    return;
  }

  const searchBtn = document.querySelector('button[onclick="searchProspects()"]');
  if (searchBtn) { searchBtn.disabled = true; searchBtn.textContent = 'Searching…'; }
  document.getElementById('prospects-loading').classList.remove('hidden');

  try {
    const payload = {
      titles: tagState.titles.length ? tagState.titles : null,
      industries: tagState.industries.length ? tagState.industries : null,
      locations: tagState.locations.length ? tagState.locations : null,
      employee_ranges: size ? size.split('|') : null,
      limit,
      save_as_segment: segment,
    };
    const data = await api('/prospects/search', { method: 'POST', body: JSON.stringify(payload) });
    toast(`Found ${data.found} prospects, saved ${data.saved} new.`);
    // Clear stale selections
    selectedProspects.clear();
    const selAll = document.getElementById('select-all');
    if (selAll) selAll.checked = false;
    _updateCampaignBtn();
    // Auto-select the segment we just created/added to
    if (data.segment_id) {
      activeSegmentId = data.segment_id;
    }
    // Collapse search panel so results are visible
    const filterBody = document.getElementById('leads-filter-body');
    if (filterBody && filterBody.classList.contains('open')) {
      toggleLeadsFilter();
    }

    // Reload segments bar and fetch the segment's prospects
    await loadSegmentsBar();
    await fetchProspects();
    document.getElementById('prospects-count').textContent = `${data.saved} new from this search (${data.found} found, ${data.skipped_duplicates} already existed)`;
    document.getElementById('leads-meta').textContent = `${data.found} found`;
  } catch (e) {
    toast('Search failed: ' + e.message, 'error');
  } finally {
    document.getElementById('prospects-loading').classList.add('hidden');
    const searchBtn = document.querySelector('button[onclick="searchProspects()"]');
    if (searchBtn) { searchBtn.disabled = false; searchBtn.textContent = 'Search Apollo'; }
  }
}

async function recoverEmails() {
  if (!await customConfirm('This will attempt to find working email addresses for all invalid/bounced prospects using Hunter.io and pattern matching, then validate each with Reacher. Continue?')) return;
  const btn = document.querySelector('button[onclick="recoverEmails()"]');
  const el = document.getElementById('validate-status');
  el.classList.remove('hidden');
  el.textContent = 'Searching for replacement emails (Hunter + DB pattern matching)…';
  if (btn) btn.disabled = true;
  try {
    const result = await api('/prospects/recover-emails', { method: 'POST', body: JSON.stringify({}) });
    if (result.recovered === 0 && result.total === 0) {
      el.textContent = 'No invalid/bounced prospects to recover.';
      return;
    }
    el.textContent = `Checked ${result.total} bounced. Recovered ${result.recovered}, not found ${result.not_found}.`;
    toast(`Recovered ${result.recovered} email${result.recovered !== 1 ? 's' : ''}.`);
    if (result.recovered > 0) await fetchProspects();
  } catch(e) {
    el.textContent = '';
    toast('Recovery failed: ' + e.message, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function exportProspectsCsv() {
  const params = new URLSearchParams();
  const search = document.getElementById('prospect-search-input')?.value.trim();
  if (search) params.set('search', search);
  if (activeSegmentId) params.set('segment_id', activeSegmentId);
  const qs = params.toString();
  toast('Preparing CSV export…', 'info');
  try {
    const headers = {};
    if (_authToken) headers['Authorization'] = 'Bearer ' + _authToken;
    const res = await fetch(`/api/prospects/export${qs ? '?' + qs : ''}`, { headers });
    if (!res.ok) throw new Error(`Export failed: ${res.status}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'prospects.csv';
    a.click();
    URL.revokeObjectURL(url);
    toast('CSV export downloaded.');
  } catch(e) { toast('Export failed: ' + e.message, 'error'); }
}

async function _runValidation(ids) {
  const el = document.getElementById('validate-status');
  const valBtn = document.getElementById('validate-smart-btn');
  const origText = valBtn ? valBtn.textContent : 'Validate All';
  if (valBtn) { valBtn.disabled = true; valBtn.textContent = 'Validating…'; }
  el.classList.remove('hidden');
  el.textContent = 'Validating with Reacher…';
  try {
    const result = await api('/prospects/validate', {
      method: 'POST',
      body: JSON.stringify({ only_unvalidated: ids === null, prospect_ids: ids || null }),
    });
    const summary = Object.entries(result.stats || {}).map(([k, v]) => `${k}: ${v}`).join(', ');
    el.textContent = `Checked ${result.validated} emails. ${summary}. Credits left: ${result.credits_remaining}.`;
    toast(`Validated ${result.validated} emails.`);
    await fetchProspects();
  } catch (e) {
    el.textContent = '';
    toast('Validation failed: ' + e.message, 'error');
  } finally {
    if (valBtn) { valBtn.disabled = false; valBtn.textContent = origText; }
  }
}

async function openWorkflowWithSelected() {
  const count = selectedProspects.size;
  if (!count) {
    toast('Select at least one prospect first (use the checkboxes).', 'error');
    return;
  }
  const sel = document.getElementById('wf-campaign-select');
  const currentCampaign = sel?.value;

  // If a campaign is already selected, add leads to it directly
  if (currentCampaign && currentCampaign !== '__new__' && currentCampaign !== '') {
    try {
      const prospectIds = [...selectedProspects];
      await api(`/campaigns/${currentCampaign}`, {
        method: 'PATCH',
        body: JSON.stringify({ prospect_ids: prospectIds }),
      });
      toast(`${count} lead${count !== 1 ? 's' : ''} added to campaign. Click Generate to create emails.`, 'success');
      selectedProspects.clear();
      document.querySelectorAll('#prospects-tbody input[type=checkbox]').forEach(cb => cb.checked = false);
      const selAll = document.getElementById('select-all');
      if (selAll) selAll.checked = false;
      _updateCampaignBtn();
      loadWfSequences(currentCampaign);
    } catch(e) { toast('Failed to add leads: ' + e.message, 'error'); }
    return;
  }

  // No campaign selected  -  open the builder for a new one.
  // If the builder is ALREADY open (user filled in name/sequence, then went
  // to pick leads), do NOT re-init - that would wipe the form fields.
  const builderAlreadyOpen = sel && sel.value === '__new__'
    && !document.getElementById('campaign-wizard').classList.contains('hidden');
  if (builderAlreadyOpen) {
    // Builder is already showing - just update the audience badge
    updateAudienceUI();
    toast(`${count} lead${count !== 1 ? 's' : ''} selected. Fill in the campaign details and generate.`, 'success');
  } else {
    await loadWorkflowCampaigns();
    setTimeout(() => {
      if (sel) {
        sel.value = '__new__';
        onWorkflowCampaignChange();
      }
      updateAudienceUI();
      toast(`${count} lead${count !== 1 ? 's' : ''} selected. Fill in the campaign details and generate.`, 'success');
    }, 300);
  }
}

function updateAudienceUI() {
  // Update wizard audience UI
  const selectedEl = document.getElementById('cw-audience-selected');
  const countEl = document.getElementById('cw-audience-count');
  const segEl = document.getElementById('cw-segment');
  if (selectedProspects.size > 0) {
    if (selectedEl) selectedEl.style.display = '';
    if (countEl) countEl.textContent = selectedProspects.size;
    if (segEl) segEl.style.display = 'none';
  } else {
    if (selectedEl) selectedEl.style.display = 'none';
    if (segEl) segEl.style.display = '';
  }
}

function clearAudienceSelection() {
  selectedProspects.clear();
  document.querySelectorAll('#prospects-tbody input[type=checkbox]').forEach(cb => cb.checked = false);
  const selAll = document.getElementById('select-all');
  if (selAll) selAll.checked = false;
  updateAudienceUI();
  toast('Selection cleared. Campaign will use the segment dropdown.');
}

// ── Email Workflow ─────────────────────────────────────────────────────────────
let _cachedCampaigns = [];
function _showLeadsTab() {
  // Move the leads panel content into the standalone tab-leads container
  // so it renders as a full page instead of a split panel.
  const leadsPanel = document.getElementById('leads-panel');
  const tabLeads = document.getElementById('tab-leads');
  if (leadsPanel && tabLeads && !tabLeads.dataset.moved) {
    leadsPanel.style.display = '';
    leadsPanel.classList.remove('panel-left');
    leadsPanel.style.maxWidth = '100%';
    leadsPanel.style.width = '100%';
    leadsPanel.style.height = 'auto';
    leadsPanel.style.overflow = 'visible';
    tabLeads.appendChild(leadsPanel);
    tabLeads.dataset.moved = '1';
  } else if (leadsPanel) {
    leadsPanel.style.display = '';
  }
  // Trigger prospect load
  if (typeof loadProspects === 'function') loadProspects();
}

function _renderCampaignCards(campaigns) {
  const container = document.getElementById('campaign-cards-container');
  if (!container) return;

  const active = campaigns.filter(c => c.status !== 'archived');
  if (!active.length) {
    container.innerHTML = `
      <div style="text-align:center;padding:60px 20px;">
        <div style="font-size:48px;margin-bottom:16px;opacity:0.3;">📋</div>
        <div style="font-size:16px;font-weight:600;color:var(--navy);margin-bottom:8px;">No campaigns yet</div>
        <div style="font-size:13px;color:var(--text-muted);margin-bottom:24px;">Create your first campaign to start LinkedIn outreach.</div>
        <button class="btn btn-primary" onclick="openCampaignWizard(null)" style="font-size:14px;padding:10px 24px;">+ New Campaign</button>
      </div>`;
    return;
  }

  const statusColors = {
    draft: 'var(--text-muted)', active: '#16a34a', paused: '#ca8a04',
    completed: '#2563eb', cancelled: '#dc2626', launching: '#ca8a04',
  };

  container.innerHTML = active.map(c => {
    const color = statusColors[c.status] || 'var(--text-muted)';
    const dot = `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:6px;"></span>`;
    const date = c.created_at ? new Date(c.created_at).toLocaleDateString() : '';
    const acctName = c.outreach_account_name || 'Default';
    return `
      <div style="padding:16px 20px;border:1px solid var(--border);border-radius:var(--r);background:var(--surface);transition:all 0.15s;display:flex;align-items:center;justify-content:space-between;"
           onmouseenter="this.style.borderColor='var(--navy)';this.style.boxShadow='0 2px 8px rgba(0,0,0,0.06)'"
           onmouseleave="this.style.borderColor='var(--border)';this.style.boxShadow='none'">
        <div onclick="openCampaignWizard('${c.id}')" style="cursor:pointer;flex:1;">
          <div style="font-size:15px;font-weight:600;color:var(--navy);">${esc(c.name)}</div>
          <div style="font-size:12px;color:var(--text-muted);margin-top:4px;">
            ${dot}${c.status} · ${date} · Outreach: ${esc(acctName)}
          </div>
        </div>
        <div style="display:flex;gap:6px;align-items:center;" onclick="event.stopPropagation();">
          <button class="btn btn-outline btn-sm" style="font-size:11px;" data-id="${c.id}" onclick="cloneCampaignFromList(this.dataset.id)" title="Duplicate this campaign">Clone</button>
          <button class="btn btn-outline btn-sm" style="font-size:11px;color:var(--red);border-color:var(--red);" data-id="${c.id}" data-name="${esc(c.name)}" onclick="deleteCampaignFromList(this.dataset.id,this.dataset.name)" title="Delete this campaign">Delete</button>
          <span style="font-size:12px;color:var(--text-muted);cursor:pointer;" onclick="openCampaignWizard('${c.id}')">→</span>
        </div>
      </div>`;
  }).join('');
}

async function deleteCampaignFromList(id, name) {
  if (!await customConfirm('Delete campaign "' + name + '"? This cannot be undone.', { destructive: true })) return;
  try {
    await api('/campaigns/' + id + '?confirm=true', { method: 'DELETE' });
    toast('Campaign deleted', 'success');
    loadWorkflowCampaigns();
  } catch (e) { toast('Delete failed: ' + e.message, 'error'); }
}

async function cloneCampaignFromList(id) {
  try {
    const cloned = await api('/campaigns/' + id + '/clone', { method: 'POST' });
    toast('Campaign cloned: ' + (cloned.name || 'copy'), 'success');
    loadWorkflowCampaigns();
  } catch (e) { toast('Clone failed: ' + e.message, 'error'); }
}

function showCampaignList() {
  const list = document.getElementById('campaign-list-view');
  const wizard = document.getElementById('campaign-wizard');
  const topBar = document.getElementById('outreach-top-bar');
  if (list) list.style.display = '';
  if (wizard) wizard.classList.add('hidden');
  if (topBar) topBar.style.display = 'flex';
}

async function loadWorkflowCampaigns() {
  // Pre-fill value prop with saved default (only if field is empty or still on placeholder)
  const vpEl = document.getElementById('cw-value-prop');
  if (vpEl && !vpEl.dataset.edited) {
    api('/settings/value-prop').then(r => {
      const warn = document.getElementById('cw-value-prop-warning');
      if (r.value_prop) {
        vpEl.value = r.value_prop;
        vpEl.placeholder = '';
        if (warn) warn.classList.add('hidden');
      } else {
        if (warn) warn.classList.remove('hidden');
      }
    }).catch(() => {});
  }
  try {
    const campaigns = await api('/campaigns/');
    _cachedCampaigns = campaigns;
    const sel = document.getElementById('wf-campaign-select');
    const cur = sel.value;
    const activeCamps = campaigns.filter(c => c.status !== 'archived');
    const archivedCamps = campaigns.filter(c => c.status === 'archived');
    sel.innerHTML = '<option value="">Select a campaign</option>' +
      '<option value="__new__">+ Create new campaign</option>' +
      activeCamps.filter(c => c.name && c.name.trim()).map(c => `<option value="${c.id}" ${c.id===cur?'selected':''}>${esc(c.name.length > 50 ? c.name.slice(0,47)+'...' : c.name)} (${c.status})</option>`).join('') +
      (archivedCamps.filter(c => c.name && c.name.trim()).length ? '<option disabled>── Archived ──</option>' +
        archivedCamps.filter(c => c.name && c.name.trim()).map(c => `<option value="${c.id}" ${c.id===cur?'selected':''} style="color:gray;">${esc(c.name.length > 50 ? c.name.slice(0,47)+'...' : c.name)} (archived)</option>`).join('') : '');
    const wfMeta = document.getElementById('workflow-meta');
    if (wfMeta) wfMeta.textContent = `${campaigns.length} campaign${campaigns.length!==1?'s':''}`;
    // Don't auto-select — show the campaign list view instead.
    // User clicks a campaign card or "+ New Campaign" to enter the wizard.
    const builderVisible = !document.getElementById('campaign-wizard').classList.contains('hidden');
    if (false && !cur && activeCamps.length && !builderVisible && !_wfGenerating) {
      sel.value = activeCamps[activeCamps.length - 1].id;
      onWorkflowCampaignChange();
    } else if (cur && cur !== '__new__') {
      sel.value = cur;
    } else if (editingCampaignId && builderVisible) {
      // Wizard is open for an existing campaign  -  keep it selected in dropdown
      sel.value = editingCampaignId;
    } else if (cur === '__new__' && builderVisible) {
      // Wizard is open for a NEW campaign  -  restore __new__ selection
      sel.value = '__new__';
    }
    // Render campaign cards in the list view
    _renderCampaignCards(campaigns);
  } catch(e) { console.error('loadWorkflowCampaigns:', e); }
}

function onWorkflowCampaignChange() {
  // Close lead detail drawer when switching campaigns
  closeSeqDrawer();
  // Reset sequence state immediately so stale data from the previous campaign
  // can never be acted on while the new campaign is loading.
  wfSequences = [];
  wfCampaignId = null;
  editingCampaignId = null;
  const val = document.getElementById('wf-campaign-select').value;
  const isNew = val === '__new__';

  _updateCampaignBtn();

  if (isNew) {
    openCampaignWizard(null);
    return;
  }

  if (val) {
    activeCampaignId = val;
    // Load the campaign to determine which wizard step to show
    openCampaignWizard(val);
  } else {
    // Nothing selected — show empty state
    _hideWizard();
    const emptyEl = document.getElementById('wf-empty-state');
    if (emptyEl) emptyEl.style.display = '';
  }

  _updateCampaignBtn();
  // Disable top-bar Generate/Launch when no campaign selected
  const genBtn = document.getElementById('top-generate-btn');
  const launchBtn = document.getElementById('top-launch-btn');
  if (genBtn) genBtn.disabled = !val || val === '__new__';
  if (launchBtn && (!val || val === '__new__')) launchBtn.style.display = 'none';
}

// ── Campaign Wizard Controller ─────────────────────────────────────────────

function _hideWizard() {
  const wiz = document.getElementById('campaign-wizard');
  if (wiz) wiz.classList.add('hidden');
  cwStep = 0;
  // Show the campaign list again
  const list = document.getElementById('campaign-list-view');
  if (list) list.style.display = '';
}

function _showWizard() {
  // Hide the campaign list, top bar, and panel divider — show the wizard
  const list = document.getElementById('campaign-list-view');
  if (list) list.style.display = 'none';
  const topBar = document.getElementById('outreach-top-bar');
  if (topBar) topBar.style.display = 'none';
  const divider = document.getElementById('panel-divider');
  if (divider) divider.style.display = 'none';
  const wiz = document.getElementById('campaign-wizard');
  if (wiz) wiz.classList.remove('hidden');
  const emptyEl = document.getElementById('wf-empty-state');
  if (emptyEl) emptyEl.style.display = 'none';
}

async function openCampaignWizard(existingCampaignId) {
  _showWizard();
  cwData = {};
  _sampleSequenceId = null;
  _sampleCampaignId = null;
  window._cwReviewShowAll = false;

  if (!existingCampaignId) {
    // New campaign: start at step 1
    cwCampaignId = null;
    activeCampaignId = null;
    cwData = {};
    cwGoStep(1);
    // Pre-fill value prop from saved default
    api('/settings/value-prop').then(r => {
      const vpEl = document.getElementById('cw-value-prop');
      const warn = document.getElementById('cw-value-prop-warning');
      if (r.value_prop) {
        if (vpEl && !vpEl.value) vpEl.value = r.value_prop;
        if (warn) warn.classList.add('hidden');
      } else {
        if (warn) warn.classList.remove('hidden');
      }
    }).catch(() => {});
    // Reset form fields
    const nameEl = document.getElementById('cw-name');
    if (nameEl) nameEl.value = '';
    const goalEl = document.getElementById('cw-goal');
    if (goalEl) goalEl.value = '';
    const vpEl = document.getElementById('cw-value-prop');
    if (vpEl) vpEl.value = '';
    // Load dropdowns for step 2 (leads) and step 3 (setup)
    _loadCwSegments();
    _loadCwSequenceDropdown();
    _loadCwOutreachAccounts();
    _loadCwSalesNavAccounts();
    _cwResetLeadSource();
    return;
  }

  // Existing campaign: load it and route to the right step
  cwCampaignId = existingCampaignId;
  activeCampaignId = existingCampaignId;
  try {
    const camp = await api(`/campaigns/${existingCampaignId}`);
    const sequences = await api(`/campaigns/${existingCampaignId}/preview`).catch(() => []);
    const hasSequences = sequences && sequences.length > 0;
    const status = camp.status;

    // Store data for chips
    cwData.name = camp.name || '';
    cwData.segment = camp.segment_id || '';
    cwData.segmentName = camp.segment_name || (camp.segment_id ? 'Segment' : 'All pending');
    cwData.goal = camp.goal || '';
    cwData.valueProp = camp.value_prop || '';

    if (status === 'active' || status === 'paused' || status === 'cancelled' || status === 'completed') {
      // Go straight to step 7 (live dashboard)
      cwGoStep(7);
      await _cwLoadDashboard(existingCampaignId);
    } else if (hasSequences) {
      // Draft with sequences — go to step 6 (review)
      cwGoStep(6);
      _cwLoadReviewTable(sequences);
    } else {
      // Draft with no sequences — go to step 3 (goal/value prop to create & generate)
      // Pre-fill form fields
      const nameEl = document.getElementById('cw-name');
      if (nameEl) nameEl.value = camp.name || '';
      const goalEl = document.getElementById('cw-goal');
      if (goalEl) goalEl.value = camp.goal || '';
      const vpEl = document.getElementById('cw-value-prop');
      if (vpEl) vpEl.value = camp.value_prop || '';
      _loadCwSegments(camp.segment_id || '');
      _loadCwSequenceDropdown(camp.sequence_config_id || '');
      _loadCwOutreachAccounts(camp.outreach_account_id || '');
      _loadCwSalesNavAccounts();
      _cwResetLeadSource();
      // Restore lead count from existing campaign's prospect_ids
      if (camp.prospect_ids && camp.prospect_ids.length) {
        _cwImportedProspectIds = camp.prospect_ids;
      }
      cwGoStep(3);
    }
  } catch(e) {
    console.error('openCampaignWizard:', e);
    toast('Failed to load campaign: ' + e.message, 'error');
    _hideWizard();
  }
}

function cwGoStep(n) {
  // Validate current step before advancing (only when going forward)
  if (n > cwStep && cwStep > 0 && cwStep < n) {
    if (!cwValidateStep(cwStep)) return;
    // Save current step data
    _cwSaveStepData(cwStep);
  }

  // Hide all steps — belt-and-suspenders: remove class AND force display:none
  for (let i = 1; i <= 7; i++) {
    const stepEl = document.getElementById(`cw-step-${i}`);
    if (stepEl) {
      stepEl.classList.remove('active');
      stepEl.style.display = 'none';
    }
  }

  // Show target step
  const targetEl = document.getElementById(`cw-step-${n}`);
  if (targetEl) {
    targetEl.classList.add('active');
    targetEl.style.display = 'block';
  }
  cwStep = n;

  // Scroll wizard into view so content isn't lost below the fold
  const wizEl = document.getElementById('campaign-wizard');
  if (wizEl) wizEl.scrollIntoView({ behavior: 'smooth', block: 'start' });

  // Update progress dots, top nav, and summary
  cwRenderProgress(n);
  _cwRenderTopNav(n);
  cwRenderSummary(n);

  // Show lead count on step 3
  if (n === 3) {
    const lcEl = document.getElementById('cw-step3-lead-count');
    if (lcEl) {
      const count = _cwImportedProspectIds.length;
      if (count > 0) {
        lcEl.textContent = `${count} lead${count !== 1 ? 's' : ''} selected for this campaign`;
        lcEl.style.display = '';
      } else {
        lcEl.style.display = 'none';
      }
    }
  }

  // Auto-trigger actions for certain steps
  if (n === 4 && !_sampleSequenceId && cwCampaignId) {
    cwGenerateSample();
  }
}

function cwRenderProgress(step) {
  const container = document.getElementById('cw-progress');
  if (!container) return;
  // Hide progress dots on the live dashboard — it's not a "step" anymore
  if (step >= 7) { container.innerHTML = ''; container.style.display = 'none'; return; }
  container.style.display = '';
  const labels = ['Name', 'Leads', 'Setup', 'Preview', 'Applying', 'Review', 'Live'];
  let html = '';
  for (let i = 1; i <= 7; i++) {
    let cls = 'cw-dot';
    if (i < step) cls += ' done';
    else if (i === step) cls += ' current';
    html += `<div class="${cls}" title="Step ${i}: ${labels[i-1]}"></div>`;
  }
  container.innerHTML = html;
}

function _cwRenderTopNav(step) {
  const el = document.getElementById('cw-top-nav');
  if (!el) return;
  // Mirror the bottom nav buttons at the top for easy access
  const navMap = {
    1: '<button class="btn btn-primary btn-sm" onclick="cwGoStep(2)">Continue →</button>',
    2: '<button class="btn btn-outline btn-sm" onclick="cwGoStep(1)">← Back</button><button class="btn btn-primary btn-sm" onclick="cwGoStep(3)">Continue →</button>',
    3: '<button class="btn btn-outline btn-sm" onclick="cwGoStep(2)">← Back</button><button class="btn btn-primary btn-sm" onclick="cwCreateAndGo()">Generate Sample →</button>',
    4: '<button class="btn btn-outline btn-sm" onclick="cwGoStep(3)">← Setup</button><button class="btn btn-primary btn-sm" onclick="cwApproveAndApply()">Apply to All →</button>',
    5: '',
    6: '<button class="btn btn-outline btn-sm" onclick="cwGoStep(4)">← Messages</button><button class="btn btn-sm" style="background:#22c55e;color:#fff;" onclick="cwLaunchCampaign()">Launch →</button>',
    7: '<span style="font-size:12px;color:var(--text-muted);font-style:italic;">Campaign is live</span>',
  };
  el.innerHTML = navMap[step] || '';
}

function cwRenderSummary(step) {
  // Always keep the header name in sync
  const headerName = document.getElementById('cw-header-name');
  if (headerName) headerName.textContent = cwData.name || '';

  const container = document.getElementById('cw-summary');
  if (!container) return;
  // Hide chips on review/dashboard steps — the campaign info is already visible there
  if (step >= 6) { container.innerHTML = ''; container.style.display = 'none'; return; }
  let chips = '';
  if (step > 1 && cwData.name) {
    chips += `<span class="cw-chip">${esc(cwData.name)}</span>`;
  }
  if (step > 2) {
    const leadLabel = _cwImportedProspectIds.length > 0
      ? `${_cwImportedProspectIds.length} leads`
      : (cwData.segmentName || cwData.segment ? (cwData.segmentName || 'All pending') : 'All pending');
    chips += `<span class="cw-chip">${esc(leadLabel)}</span>`;
  }
  if (step > 3 && cwData.goal) {
    const short = cwData.goal.length > 50 ? cwData.goal.slice(0, 47) + '...' : cwData.goal;
    chips += `<span class="cw-chip">${esc(short)}</span>`;
  }
  container.innerHTML = chips;
  container.style.display = chips ? '' : 'none';
}

function cwValidateStep(n) {
  if (n === 1) {
    const name = document.getElementById('cw-name')?.value.trim();
    if (!name) { toast('Enter a campaign name.', 'error'); return false; }
    // Check for duplicate
    const existingNames = (_cachedCampaigns || []).map(c => (c.name || '').trim().toLowerCase());
    if (!cwCampaignId && existingNames.includes(name.toLowerCase())) {
      toast('A campaign with this name already exists.', 'error');
      return false;
    }
    return true;
  }
  if (n === 2) {
    // Must have imported prospects OR a segment selected OR "all pending" (existing tab with empty segment)
    if (_cwImportedProspectIds.length > 0) return true;
    if (_cwLeadSource === 'existing') return true; // "all pending" is valid
    toast('Import or select leads before continuing.', 'error');
    return false;
  }
  if (n === 3) {
    const goal = document.getElementById('cw-goal')?.value.trim();
    if (!goal) { toast('Enter a campaign goal.', 'error'); return false; }
    return true;
  }
  return true;
}

function _cwSaveStepData(n) {
  if (n === 1) {
    cwData.name = document.getElementById('cw-name')?.value.trim() || '';
  }
  if (n === 2) {
    // Lead source data
    cwData.leadSource = _cwLeadSource;
    if (_cwLeadSource === 'existing') {
      const segEl = document.getElementById('cw-segment');
      cwData.segment = segEl?.value || '';
      cwData.segmentName = segEl?.selectedOptions?.[0]?.textContent || 'All pending';
    }
    // Save list name if checkbox checked
    const saveList = document.getElementById('cw-save-as-list');
    if (saveList?.checked) {
      cwData.saveAsListName = document.getElementById('cw-list-name')?.value.trim() || '';
    }
  }
  if (n === 3) {
    cwData.goal = document.getElementById('cw-goal')?.value.trim() || '';
    cwData.valueProp = document.getElementById('cw-value-prop')?.value.trim() || '';
    // Save selected outreach account for campaign creation
    const acctEl = document.getElementById('cw-outreach-account');
    cwData.outreachAccountId = acctEl?.value || '';
  }
}

function _loadCwSegments(selectedId) {
  api('/prospects/segments').then(segs => {
    const sel = document.getElementById('cw-segment');
    if (!sel) return;
    sel.innerHTML = '<option value="">All pending prospects</option>' +
      segs.map(s => `<option value="${s.id}" ${s.id === selectedId ? 'selected' : ''}>${esc(s.name)} (${s.prospect_count})</option>`).join('');
  }).catch(() => {});
}

function _loadCwSequenceDropdown(selectedId) {
  loadCampaignSequenceDropdown(selectedId, 'cw-sequence-config');
}

function _loadCwOutreachAccounts(selectedId) {
  // Load LinkedIn accounts that have outreach capability (connect_agent_id set).
  // Populates the #cw-outreach-account dropdown in wizard Step 3.
  api('/linkedin-accounts').then(resp => {
    const accounts = resp.items || resp || [];
    const sel = document.getElementById('cw-outreach-account');
    if (!sel) return;
    // Filter to accounts with outreach capability (connect_agent_id present)
    const outreachAccounts = accounts.filter(a => a.connect_agent_id);
    const defaultOpt = '<option value="">Select outreach account...</option>';
    sel.innerHTML = defaultOpt +
      outreachAccounts.map(a => {
        const firstName = (a.name || '').split(' ')[0];
        const caps = [];
        if (a.connect_agent_id) caps.push('Connect');
        if (a.message_agent_id) caps.push('Message');
        const label = firstName + (caps.length ? ' (' + caps.join(' + ') + ')' : '');
        return `<option value="${a.id}" ${a.id === selectedId ? 'selected' : ''}>${esc(label)}</option>`;
      }).join('');
  }).catch(() => {
    const sel = document.getElementById('cw-outreach-account');
    if (sel) sel.innerHTML = '<option value="">Default (env config)</option>';
  });
}

// ── Wizard Step 2: Lead Source Functions ──────────────────────────────────

let _cwLeadSource = 'salesnav';  // default source tab
let _cwImportedProspectIds = [];  // prospect IDs from any source

function cwSelectLeadSource(source, tabEl) {
  _cwLeadSource = source;
  // Hide all panels, show selected one
  document.querySelectorAll('.cw-source-panel').forEach(p => {
    p.style.display = 'none';
    p.classList.remove('active');
  });
  const panel = document.getElementById('cw-source-' + source);
  if (panel) { panel.style.display = ''; panel.classList.add('active'); }
  // Update tab styles
  document.querySelectorAll('.cw-source-tab').forEach(t => t.classList.remove('active'));
  if (tabEl) tabEl.classList.add('active');
}

function _cwUpdateLeadCount() {
  const el = document.getElementById('cw-lead-count-summary');
  if (!el) return;
  if (_cwImportedProspectIds.length > 0) {
    el.textContent = `${_cwImportedProspectIds.length} leads ready for this campaign`;
    el.style.color = 'var(--green)';
  } else {
    el.textContent = '';
  }
}

function _loadCwSalesNavAccounts() {
  api('/linkedin-accounts').then(resp => {
    const accounts = (resp.items || resp || []).filter(a => a.sales_nav_agent_id);
    const sel = document.getElementById('cw-sn-account');
    if (!sel) return;
    sel.innerHTML = '<option value="">Select Sales Nav account...</option>' +
      accounts.map(a => {
        const firstName = (a.name || '').split(' ')[0];
        return `<option value="${a.id}">${esc(firstName)} (Sales Navigator)</option>`;
      }).join('');
  }).catch(() => {
    const sel = document.getElementById('cw-sn-account');
    if (sel) sel.innerHTML = '<option value="">No accounts available</option>';
  });
}

let _cwSnMode = 'search';  // 'search' or 'url'

function cwSnMode(mode) {
  _cwSnMode = mode;
  const searchPanel = document.getElementById('cw-sn-search-panel');
  const urlPanel = document.getElementById('cw-sn-url-panel');
  const btnSearch = document.getElementById('cw-sn-mode-search');
  const btnUrl = document.getElementById('cw-sn-mode-url');
  if (searchPanel) searchPanel.style.display = mode === 'search' ? '' : 'none';
  if (urlPanel) urlPanel.style.display = mode === 'url' ? '' : 'none';
  if (btnSearch) { btnSearch.style.background = mode === 'search' ? 'var(--navy)' : ''; btnSearch.style.color = mode === 'search' ? '#fff' : ''; btnSearch.className = mode === 'search' ? 'btn btn-sm' : 'btn btn-sm btn-outline'; }
  if (btnUrl) { btnUrl.style.background = mode === 'url' ? 'var(--navy)' : ''; btnUrl.style.color = mode === 'url' ? '#fff' : ''; btnUrl.className = mode === 'url' ? 'btn btn-sm' : 'btn btn-sm btn-outline'; }
}

async function cwStartSalesNavImport() {
  const accountSel = document.getElementById('cw-sn-account');
  const status = document.getElementById('cw-sn-status');
  const btn = document.getElementById('cw-sn-import-btn');

  let searchUrl;
  let maxLeads;

  if (_cwSnMode === 'search') {
    // Build URL from filters using existing buildSalesNavUrl()
    const titles = (document.getElementById('cw-sn-titles')?.value || '').split(',').map(s => s.trim()).filter(Boolean);
    const industries = [...document.querySelectorAll('#cw-sn-industry input:checked')].map(cb => cb.value);
    const regions = [...document.querySelectorAll('#cw-sn-region input:checked')].map(cb => cb.value);
    const companySizes = [...document.querySelectorAll('#cw-sn-companysize input:checked')].map(cb => cb.value);
    maxLeads = parseInt(document.getElementById('cw-sn-max')?.value) || 100;

    if (!titles.length && !industries.length && !regions.length) {
      toast('Set at least one filter (titles, industry, or region).', 'error');
      return;
    }

    searchUrl = buildSalesNavUrl({ titles, seniorities: [], industries, regions, companySizes });
  } else {
    // Paste URL mode
    const urlInput = document.getElementById('cw-sn-url');
    searchUrl = (urlInput?.value || '').trim();
    maxLeads = parseInt(document.getElementById('cw-sn-max-url')?.value) || 100;

    if (!searchUrl || !searchUrl.includes('linkedin.com')) {
      toast('Enter a valid LinkedIn Sales Navigator search URL.', 'error');
      urlInput?.focus();
      return;
    }
  }

  const accountId = accountSel?.value;
  if (!accountId) {
    toast('Select a LinkedIn account with Sales Nav configured.', 'error');
    return;
  }

  if (status) { status.style.color = 'var(--text-muted)'; status.textContent = 'Launching Sales Nav phantom...'; }
  if (btn) { btn.disabled = true; btn.textContent = 'Importing...'; }

  try {
    const body = { search_url: searchUrl, account_id: accountId, max_leads: maxLeads };
    // If save-as-list is checked, create a segment
    const saveList = document.getElementById('cw-save-as-list');
    const listName = document.getElementById('cw-list-name')?.value.trim();
    if (saveList?.checked && listName) body.segment_name = listName;

    const res = await api('/prospects/import-salesnav', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    if (!res.job_id) throw new Error('No job_id returned from server.');
    if (status) status.textContent = 'Phantom launched. Polling for results...';
    _cwPollSalesNavStatus(res.job_id, accountId);
  } catch(e) {
    if (status) { status.style.color = 'var(--red)'; status.textContent = 'Launch failed: ' + (e.message || e); }
    toast('Sales Nav import failed: ' + (e.message || e), 'error');
    if (btn) { btn.disabled = false; btn.textContent = 'Import from Sales Nav'; }
  }
}

async function _cwPollSalesNavStatus(jobId, accountId) {
  const status = document.getElementById('cw-sn-status');
  const btn = document.getElementById('cw-sn-import-btn');
  const pollInterval = 5000;
  const maxPolls = 120;
  let pollCount = 0;
  let errorCount = 0;

  const poll = async () => {
    pollCount++;
    if (pollCount > maxPolls) {
      if (status) { status.style.color = 'var(--red)'; status.textContent = 'Timed out waiting for phantom to finish.'; }
      if (btn) { btn.disabled = false; btn.textContent = 'Import from Sales Nav'; }
      return;
    }
    try {
      const url = `/prospects/import-salesnav/status?job_id=${encodeURIComponent(jobId)}&account_id=${encodeURIComponent(accountId)}`;
      const res = await api(url);
      errorCount = 0;

      if (res.status === 'running') {
        if (status) status.textContent = 'Phantom still running... checking again in 5s.';
        setTimeout(poll, pollInterval);
      } else if (res.status === 'complete') {
        if (status) { status.style.color = 'var(--green)'; status.textContent = `Done: ${res.imported} imported, ${res.duplicates} duplicates skipped.`; }
        // Store imported prospect IDs
        if (res.prospect_ids && res.prospect_ids.length > 0) {
          _cwImportedProspectIds = res.prospect_ids;
        } else if (res.imported > 0) {
          // If the API doesn't return IDs, fetch them from the segment
          const fetchRes = await api('/prospects/?limit=2500').catch(() => ({ items: [] }));
          _cwImportedProspectIds = (fetchRes.prospects || []).slice(0, res.imported).map(p => p.id);
        }
        _cwUpdateLeadCount();
        toast(`${res.imported} prospects imported from Sales Navigator`);
        if (btn) { btn.disabled = false; btn.textContent = 'Import from Sales Nav'; }
      } else if (res.status === 'failed') {
        if (status) { status.style.color = 'var(--red)'; status.textContent = 'Import failed: ' + (res.detail || 'Unknown error'); }
        if (btn) { btn.disabled = false; btn.textContent = 'Import from Sales Nav'; }
      } else {
        if (status) { status.style.color = 'var(--red)'; status.textContent = 'Unexpected status: ' + res.status; }
        if (btn) { btn.disabled = false; btn.textContent = 'Import from Sales Nav'; }
      }
    } catch(e) {
      errorCount++;
      if (status) { status.style.color = 'var(--red)'; status.textContent = 'Poll error: ' + (e.message || e); }
      if (errorCount < 3) {
        setTimeout(poll, pollInterval * 2);
      } else {
        if (status) status.textContent += ' (giving up after 3 consecutive errors)';
        if (btn) { btn.disabled = false; btn.textContent = 'Import from Sales Nav'; }
      }
    }
  };

  setTimeout(poll, pollInterval);
}

async function cwStartApolloSearch() {
  const titlesRaw = document.getElementById('cw-apollo-titles')?.value.trim() || '';
  const industriesRaw = document.getElementById('cw-apollo-industries')?.value.trim() || '';
  const locationsRaw = document.getElementById('cw-apollo-locations')?.value.trim() || '';
  const sizeVal = document.getElementById('cw-apollo-size')?.value || '';
  const limit = parseInt(document.getElementById('cw-apollo-limit')?.value) || 50;
  const status = document.getElementById('cw-apollo-status');
  const btn = document.getElementById('cw-apollo-search-btn');

  const titles = titlesRaw ? titlesRaw.split(',').map(s => s.trim()).filter(Boolean) : [];
  const industries = industriesRaw ? industriesRaw.split(',').map(s => s.trim()).filter(Boolean) : [];
  const locations = locationsRaw ? locationsRaw.split(',').map(s => s.trim()).filter(Boolean) : [];
  const employeeRanges = sizeVal ? [sizeVal] : null;

  if (!titles.length && !industries.length && !locations.length) {
    toast('Enter at least one search filter (title, industry, or location).', 'error');
    return;
  }

  if (status) { status.style.color = 'var(--text-muted)'; status.textContent = 'Searching Apollo...'; }
  if (btn) { btn.disabled = true; btn.textContent = 'Searching...'; }

  try {
    // Build segment name from save-as-list or auto-generate
    const saveList = document.getElementById('cw-save-as-list');
    const listName = document.getElementById('cw-list-name')?.value.trim();
    const segmentName = (saveList?.checked && listName) ? listName : `Campaign ${cwData.name || 'search'} ${new Date().toISOString().slice(0,10)}`;

    const payload = {
      titles: titles.length ? titles : null,
      industries: industries.length ? industries : null,
      locations: locations.length ? locations : null,
      employee_ranges: employeeRanges,
      limit,
      save_as_segment: segmentName,
    };

    const data = await api('/prospects/search', { method: 'POST', body: JSON.stringify(payload) });

    if (status) { status.style.color = 'var(--green)'; status.textContent = `Found ${data.found} prospects, saved ${data.saved} new.`; }

    // Store segment and prospect IDs
    if (data.segment_id) {
      cwData.segment = data.segment_id;
      cwData.segmentName = segmentName;
      // Fetch the actual prospect IDs from this segment
      const fetchRes = await api(`/prospects/?segment_id=${data.segment_id}&limit=2500`).catch(() => ({ items: [] }));
      _cwImportedProspectIds = (fetchRes.prospects || []).map(p => p.id);
    }
    _cwUpdateLeadCount();
    toast(`Found ${data.found} prospects, saved ${data.saved} new.`);
  } catch(e) {
    if (status) { status.style.color = 'var(--red)'; status.textContent = 'Search failed: ' + (e.message || e); }
    toast('Apollo search failed: ' + (e.message || e), 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Search Apollo'; }
  }
}

async function cwHandleCsvUpload() {
  const fileInput = document.getElementById('cw-csv-file');
  const status = document.getElementById('cw-csv-status');
  const btn = document.getElementById('cw-csv-import-btn');
  const file = fileInput?.files?.[0];

  if (!file) { toast('Select a CSV file first.', 'error'); return; }
  if (!file.name.endsWith('.csv') && !file.name.endsWith('.xlsx')) {
    toast('Please select a CSV or XLSX file.', 'error');
    return;
  }

  if (status) { status.style.color = 'var(--text-muted)'; status.textContent = 'Importing...'; }
  if (btn) { btn.disabled = true; btn.textContent = 'Importing...'; }

  try {
    // Build segment name from save-as-list or auto-generate
    const saveList = document.getElementById('cw-save-as-list');
    const listName = document.getElementById('cw-list-name')?.value.trim();
    const segmentName = (saveList?.checked && listName) ? listName : `CSV Import ${new Date().toISOString().slice(0,10)}`;

    const form = new FormData();
    form.append('file', file);
    form.append('segment_name', segmentName);

    const res = await fetch('/api/prospects/import', {
      method: 'POST',
      headers: _authToken ? { 'Authorization': `Bearer ${_authToken}` } : {},
      body: form,
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }

    const data = await res.json();
    if (status) { status.style.color = 'var(--green)'; status.textContent = `Imported ${data.imported} prospects, ${data.skipped_duplicates} duplicates skipped.`; }

    // Store the segment for campaign creation
    if (data.segment_id) {
      cwData.segment = data.segment_id;
      cwData.segmentName = segmentName;
      // Fetch imported prospect IDs
      const fetchRes = await api(`/prospects/?segment_id=${data.segment_id}&limit=2500`).catch(() => ({ items: [] }));
      _cwImportedProspectIds = (fetchRes.prospects || []).map(p => p.id);
    }
    _cwUpdateLeadCount();
    toast(`${data.imported} prospects imported from CSV`);
  } catch(e) {
    if (status) { status.style.color = 'var(--red)'; status.textContent = 'Import failed: ' + (e.message || e); }
    toast('CSV import failed: ' + (e.message || e), 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Upload & Import'; }
  }
}

async function cwSelectExistingSegment() {
  const segEl = document.getElementById('cw-segment');
  const countEl = document.getElementById('cw-existing-count');
  const segmentId = segEl?.value;

  cwData.segment = segmentId || '';
  cwData.segmentName = segEl?.selectedOptions?.[0]?.textContent || 'All pending';

  // Clear any previously imported IDs — we're using segment-based selection
  _cwImportedProspectIds = [];

  if (!segmentId) {
    if (countEl) countEl.textContent = 'All pending prospects will be included.';
    const summaryEl = document.getElementById('cw-lead-count-summary');
    if (summaryEl) { summaryEl.textContent = 'All pending prospects'; summaryEl.style.color = 'var(--navy)'; }
    return;
  }

  if (countEl) countEl.textContent = 'Loading count...';
  try {
    const res = await api(`/prospects/?segment_id=${segmentId}&limit=1`);
    const total = res.total || res.count || (res.items || []).length;
    if (countEl) countEl.textContent = `${total} leads in this segment`;
    const summaryEl = document.getElementById('cw-lead-count-summary');
    if (summaryEl) { summaryEl.textContent = `${total} leads ready for this campaign`; summaryEl.style.color = 'var(--green)'; }
  } catch(e) {
    if (countEl) countEl.textContent = 'Could not load count.';
  }
}

// Save-as-list checkbox toggle
document.addEventListener('DOMContentLoaded', () => {
  const cb = document.getElementById('cw-save-as-list');
  if (cb) {
    cb.addEventListener('change', function() {
      const wrap = document.getElementById('cw-list-name-wrap');
      if (wrap) wrap.style.display = this.checked ? '' : 'none';
    });
  }
});

function _cwResetLeadSource() {
  _cwLeadSource = 'salesnav';
  _cwImportedProspectIds = [];
  // Reset panels
  document.querySelectorAll('.cw-source-panel').forEach(p => { p.style.display = 'none'; p.classList.remove('active'); });
  const snPanel = document.getElementById('cw-source-salesnav');
  if (snPanel) { snPanel.style.display = ''; snPanel.classList.add('active'); }
  // Reset tabs
  document.querySelectorAll('.cw-source-tab').forEach((t, i) => { t.classList.toggle('active', i === 0); });
  // Reset inputs
  const fields = ['cw-sn-url', 'cw-sn-max', 'cw-apollo-titles', 'cw-apollo-industries', 'cw-apollo-locations', 'cw-apollo-limit', 'cw-list-name'];
  fields.forEach(id => { const el = document.getElementById(id); if (el) el.value = el.defaultValue || ''; });
  const csvFile = document.getElementById('cw-csv-file');
  if (csvFile) csvFile.value = '';
  const saveList = document.getElementById('cw-save-as-list');
  if (saveList) saveList.checked = false;
  const listWrap = document.getElementById('cw-list-name-wrap');
  if (listWrap) listWrap.style.display = 'none';
  // Reset status and count displays
  ['cw-sn-status', 'cw-apollo-status', 'cw-csv-status', 'cw-existing-count', 'cw-lead-count-summary'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = '';
  });
}

// ── Wizard step actions ────────────────────────────────────────────────────

async function cwCreateAndGo() {
  // Validate step 3
  if (!cwValidateStep(3)) return;
  _cwSaveStepData(3);

  const name = cwData.name;
  const goal = cwData.goal;
  const segmentId = cwData.segment || '';
  const valueProp = cwData.valueProp;
  // Use wizard-imported prospect IDs first, then fall back to manually selected prospects
  const prospectIds = _cwImportedProspectIds.length > 0 ? [..._cwImportedProspectIds]
    : selectedProspects.size > 0 ? [...selectedProspects] : null;

  if (!name || !goal) { toast('Fill in campaign name and goal.', 'error'); return; }
  // "All pending prospects" (empty segmentId) is valid — the backend resolves all prospects.
  // Only block if no segment AND no manually selected prospects.

  const btn = document.getElementById('cw-create-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Working...'; }

  try {
    // Save value prop
    let finalValueProp = valueProp;
    if (!finalValueProp) {
      const vp = await api('/settings/value-prop').catch(() => ({}));
      finalValueProp = vp.value_prop || '';
    } else {
      api('/settings/value-prop', { method: 'POST', body: JSON.stringify({ instructions: finalValueProp }) })
        .catch(e => console.error('Value prop auto-save failed:', e));
    }

    const sequenceConfigId = document.getElementById('cw-sequence-config')?.value || '';
    const outreachAccountId = document.getElementById('cw-outreach-account')?.value || '';

    let camp;
    if (cwCampaignId) {
      // Editing existing draft
      const patchBody = { name, goal, value_prop: finalValueProp, segment_id: segmentId || null, prospect_ids: prospectIds };
      if (sequenceConfigId) patchBody.sequence_config_id = sequenceConfigId;
      if (outreachAccountId) patchBody.outreach_account_id = outreachAccountId;
      else patchBody.outreach_account_id = null;
      camp = await api(`/campaigns/${cwCampaignId}`, {
        method: 'PATCH',
        body: JSON.stringify(patchBody),
      });
    } else {
      camp = await api('/campaigns/', {
        method: 'POST',
        body: JSON.stringify({ name, goal, value_prop: finalValueProp, segment_id: segmentId || null, prospect_ids: prospectIds, sequence_config_id: sequenceConfigId || null, outreach_account_id: outreachAccountId || null }),
      });
    }

    cwCampaignId = camp.id;
    activeCampaignId = camp.id;
    wfCampaignId = camp.id;

    // Prepare sequences (creates EmailSequence rows from segment/prospect IDs)
    await api(`/campaigns/${camp.id}/prepare`, {
      method: 'POST',
      body: JSON.stringify({}),
    });

    // Update dropdown
    const sel = document.getElementById('wf-campaign-select');
    if (sel) {
      let opt = [...sel.options].find(o => o.value === camp.id);
      if (!opt) {
        opt = document.createElement('option');
        opt.value = camp.id;
        sel.add(opt, sel.options[2] || null);
      }
      opt.textContent = `${esc(camp.name)} (draft)`;
      sel.value = camp.id;
    }

    // Move to step 4 (AI preview)
    cwGoStep(4);
    loadWorkflowCampaigns();
    toast('Campaign created. Generating sample...');
  } catch(e) {
    toast('Failed: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Create & Generate Sample'; }
  }
}

async function cwGenerateSample() {
  const spinner = document.getElementById('cw-generate-spinner');
  const preview = document.getElementById('cw-sample-preview');
  document.getElementById('cw-generate-retry')?.remove();
  if (spinner) spinner.style.display = '';
  if (preview) preview.style.display = 'none';

  try {
    const resp = await api(`/campaigns/${cwCampaignId}/generate-sample`, { method: 'POST', body: JSON.stringify({}) });
    _sampleSequenceId = resp.sequence_id;
    _sampleCampaignId = cwCampaignId;

    document.getElementById('cw-sample-lead-name').textContent = resp.prospect.name;
    document.getElementById('cw-sample-lead-detail').textContent = `${resp.prospect.title || ''} at ${resp.prospect.company || ''}`;
    const noteEl = document.getElementById('cw-connect-note-text');
    const msgEl = document.getElementById('cw-message-text');
    if (noteEl) { noteEl.textContent = resp.linkedin_connect_note || ''; }
    if (msgEl) { msgEl.textContent = resp.linkedin_message || ''; }
    // Update character counters
    const noteCounter = document.getElementById('cw-note-counter');
    const msgCounter = document.getElementById('cw-msg-counter');
    if (noteCounter && noteEl) noteCounter.textContent = noteEl.textContent.length + '/300';
    if (msgCounter && msgEl) msgCounter.textContent = msgEl.textContent.length + '/500';

    if (spinner) spinner.style.display = 'none';
    if (preview) preview.style.display = '';
  } catch(e) {
    toast('Failed to generate sample: ' + e.message, 'error');
    if (spinner) {
      spinner.style.display = 'none';
      spinner.insertAdjacentHTML('afterend',
        `<div id="cw-generate-retry" style="text-align:center;padding:20px;">
          <p style="color:var(--text-muted);margin-bottom:12px;">Could not generate a sample. This usually means no leads are prepared yet.</p>
          <button class="btn btn-primary btn-sm" onclick="document.getElementById('cw-generate-retry')?.remove();cwGenerateSample()">Retry</button>
          <button class="btn btn-outline btn-sm" onclick="cwGoStep(3)" style="margin-left:8px;">Back to Setup</button>
        </div>`);
    }
  }
}

async function cwRegenerateSample() {
  _sampleSequenceId = null;
  await cwGenerateSample();
}

async function cwRefineMessage(step) {
  const feedbackInput = document.getElementById(`cw-refine-feedback-${step}`);
  const feedback = feedbackInput?.value.trim();
  if (!feedback) { toast('Enter your feedback first', 'warning'); return; }

  const textEl = document.getElementById(step === 'linkedin_connect_note' ? 'cw-connect-note-text' : 'cw-message-text');
  const currentText = textEl.textContent;

  const btn = feedbackInput.nextElementSibling;
  const origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Refining...';

  try {
    const data = await api(`/campaigns/${cwCampaignId}/refine-message`, {
      method: 'PATCH',
      body: JSON.stringify({
        step: step,
        current_text: currentText,
        feedback: feedback,
        sequence_id: _sampleSequenceId,
      }),
    });
    textEl.textContent = data.text;
    feedbackInput.value = '';
    toast('Message refined');
  } catch(e) {
    toast('Refinement failed: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
  }
}

async function cwApproveAndApply() {
  const noteText = document.getElementById('cw-connect-note-text')?.textContent.trim();
  const msgText = document.getElementById('cw-message-text')?.textContent.trim();

  if (!noteText && !msgText) {
    toast('Generate messages before applying', 'warning');
    return;
  }

  // Move to step 5 (applying)
  cwGoStep(5);

  try {
    const data = await api(`/campaigns/${cwCampaignId}/apply-to-all`, {
      method: 'POST',
      body: JSON.stringify({ reference_sequence_id: _sampleSequenceId }),
    });

    const countEl = document.getElementById('cw-apply-count');
    if (countEl) countEl.textContent = `${data.prepared} leads prepared`;
    const barEl = document.getElementById('cw-apply-bar');
    if (barEl) barEl.style.width = '100%';

    // Auto-advance to step 6 after a short pause
    setTimeout(async () => {
      const sequences = await api(`/campaigns/${cwCampaignId}/preview`).catch(() => []);
      _cwLoadReviewTable(sequences || []);
      cwGoStep(6);
    }, 1200);
  } catch(e) {
    toast('Apply failed: ' + e.message, 'error');
    cwGoStep(4);
  }
}

function _cwLoadReviewTable(sequences) {
  const tbody = document.getElementById('cw-review-tbody');
  const countEl = document.getElementById('cw-review-count');
  const summaryEl = document.getElementById('cw-review-summary');
  if (!tbody) return;

  window._cwReviewSequences = sequences;
  wfSequences = sequences;
  const total = sequences.length;
  const approved = sequences.filter(s => s.approved).length;

  if (summaryEl) summaryEl.textContent = `${total} personalized message${total !== 1 ? 's' : ''} ready (${approved} approved).`;
  if (countEl) countEl.textContent = `${total} lead${total !== 1 ? 's' : ''}`;

  const PAGE_SIZE = 50;
  const showing = window._cwReviewShowAll ? sequences : sequences.slice(0, PAGE_SIZE);
  const rows = showing.map(seq => {
    const name = seq.prospect_name || 'Unknown';
    const rawEmail = seq.prospect_email || '';
    const isPlaceholder = rawEmail.includes('@placeholder.local');
    const emailDisplay = isPlaceholder
      ? '<span style="font-style:italic;">via Sales Nav</span>'
      : esc(rawEmail);
    const company = seq.prospect_company || '--';
    const status = seq.approved ? '<span style="color:#059669;font-weight:600;font-size:12px;">Approved</span>' : '<span style="color:#3b82f6;font-size:12px;">Draft</span>';
    const note = seq.linkedin_connect_note || '';
    const truncNote = note.length > 55 ? esc(note.slice(0, 52)) + '&hellip;' : esc(note);
    return `<tr data-seq-id="${seq.id}" style="border-bottom:1px solid var(--border);"
        onclick="openUnifiedDrawer('${esc(seq.id)}','${esc(seq.prospect_id)}')">
      <td style="padding:10px 12px;">
        <div style="font-weight:600;font-size:13px;">${esc(name)}</div>
        <div style="font-size:11px;color:var(--text-muted);">${emailDisplay}</div>
      </td>
      <td style="padding:10px 12px;color:var(--text-dim);font-size:13px;">${esc(company)}</td>
      <td style="padding:10px 12px;">${status}</td>
      <td style="padding:10px 12px;font-size:12px;color:var(--text-dim);max-width:200px;" class="cw-hide-narrow" title="${esc(note)}">${truncNote || '<span style="color:var(--text-muted);">\u2014</span>'}</td>
      <td style="padding:10px 12px;text-align:right;white-space:nowrap;" class="cw-row-actions">
        <button class="btn btn-outline btn-sm" onclick="event.stopPropagation();openUnifiedDrawer('${esc(seq.id)}','${esc(seq.prospect_id)}')" title="Edit messages">Edit</button>
        <button class="btn btn-outline btn-sm" style="color:var(--red);border-color:var(--red);margin-left:4px;" onclick="event.stopPropagation();cwRemoveFromReview('${esc(seq.id)}')" title="Remove from campaign">Remove</button>
      </td>
    </tr>`;
  }).join('');
  let showMoreRow = '';
  if (!window._cwReviewShowAll && total > PAGE_SIZE) {
    showMoreRow = `<tr><td colspan="5" style="text-align:center;padding:12px;">
      <button class="btn btn-outline btn-sm" onclick="window._cwReviewShowAll=true;_cwLoadReviewTable(window._cwReviewSequences)">
        Show all ${total} leads (${total - PAGE_SIZE} more)
      </button></td></tr>`;
  }
  tbody.innerHTML = (rows + showMoreRow) || '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:28px;">No sequences found.</td></tr>';
}

function cwFilterReviewLeads() {
  const q = (document.getElementById('cw-review-search')?.value || '').toLowerCase().trim();
  const tbody = document.getElementById('cw-review-tbody');
  if (!tbody) return;
  tbody.querySelectorAll('tr[data-seq-id]').forEach(row => {
    const text = row.textContent.toLowerCase();
    row.style.display = !q || text.includes(q) ? '' : 'none';
  });
}

async function cwRemoveFromReview(seqId) {
  // Block removal from launched campaigns — only drafts can be edited
  if (cwStep === 7) { toast('Cannot remove leads from a live campaign.', 'error'); return; }
  const seq = (window._cwReviewSequences || []).find(s => s.id === seqId);
  const name = seq ? (seq.prospect_name || 'this lead') : 'this lead';
  if (!await customConfirm(
    `Remove <strong>${esc(name)}</strong> from this campaign? They won\u2019t receive any outreach.`,
    { destructive: true, okText: 'Remove' }
  )) return;
  try {
    await api(`/campaigns/${cwCampaignId}/sequences/${seqId}`, { method: 'DELETE' });
    window._cwReviewSequences = (window._cwReviewSequences || []).filter(s => s.id !== seqId);
    _cwLoadReviewTable(window._cwReviewSequences);
    toast('Lead removed from campaign.');
  } catch(e) {
    toast('Failed to remove: ' + e.message, 'error');
  }
}

async function cwBackToCampaigns() {
  if (cwStep > 1 && !cwCampaignId) {
    const ok = await customConfirm(
      'You have unsaved changes. Leaving will discard your progress.',
      { title: 'Discard changes?', destructive: true, okText: 'Discard' }
    );
    if (!ok) return;
  }
  showCampaignList();
  loadWorkflowCampaigns();
}

async function cwLaunchCampaign() {
  if (_builderLaunching) return;
  if (!cwCampaignId) return;
  if (_activeJobId) {
    toast('Cannot launch while sequences are still generating.', 'error');
    return;
  }

  // Confirmation dialog — launching sends real LinkedIn requests
  const leadCount = (window._cwReviewSequences || []).length;
  const confirmed = await customConfirm(
    `This will send LinkedIn connection requests to <strong>${leadCount} lead${leadCount !== 1 ? 's' : ''}</strong>. This action cannot be undone.`,
    { title: 'Launch campaign?', okText: 'Launch' }
  );
  if (!confirmed) return;

  const btns = document.querySelectorAll('button[onclick="cwLaunchCampaign()"]');
  const origLabels = [...btns].map(b => b.innerHTML);
  _builderLaunching = true;
  btns.forEach(b => { b.disabled = true; b.innerHTML = '<span class="loading" style="width:14px;height:14px;border-width:2px;display:inline-block;vertical-align:middle;margin-right:6px;"></span>Launching...'; });

  try {
    await api(`/campaigns/${cwCampaignId}`, {
      method: 'PATCH',
      body: JSON.stringify({ sending_limit_per_day: 20 }),
    });
    const result = await api(`/campaigns/${cwCampaignId}/launch`, { method: 'POST' });
    toast(`Campaign launched! ${result.leads_enrolled} leads enrolled.`);
    await loadWorkflowCampaigns();
    cwGoStep(7);
    await _cwLoadDashboard(cwCampaignId);
  } catch(e) {
    toast('Launch failed: ' + e.message, 'error');
  } finally {
    _builderLaunching = false;
    btns.forEach((b, i) => { b.disabled = false; b.innerHTML = origLabels[i] || 'Launch Campaign &rarr;'; });
  }
}

async function _cwLoadDashboard(campaignId) {
  const contentEl = document.getElementById('cw-dashboard-content');
  if (!contentEl) return;
  contentEl.innerHTML = '<div style="padding:28px;text-align:center;color:var(--text-muted);"><div class="loading"></div></div>';

  try {
    let [camp, sequences, stats, inboxAccounts, orchStates, tasks, liHealth] = await Promise.all([
      api(`/campaigns/${campaignId}`).catch(() => null),
      api(`/campaigns/${campaignId}/preview`).catch(() => null),
      api(`/campaigns/${campaignId}/stats`).catch(() => null),
      Promise.resolve([]),  // inboxes — not used in LinkedIn-only flow
      api(`/orchestration/states?include_terminal=true&campaign_id=${encodeURIComponent(campaignId)}`).catch(() => []),
      api('/orchestration/tasks').catch(() => []),
      api('/orchestration/linkedin/health').catch(() => null),
    ]);
    if (!camp) { toast('Could not load campaign details.', 'error'); contentEl.innerHTML = ''; return; }
    if (!sequences) sequences = [];
    if (!stats) stats = {};
    wfSequences = sequences;
    wfCampaignId = campaignId;
    seqAllTasks = tasks;
    wfSeqShape = Array.isArray(stats?.sequence_shape) && stats.sequence_shape.length
      ? stats.sequence_shape.map(s => ({ day: s.day, type: s.step_type, label: s.label, _sequence_total_days: s._sequence_total_days }))
      : null;
    wfOrchMap = {};
    // Only include orch states for prospects in THIS campaign
    const campProspectIds = new Set((camp.prospect_ids || []).concat(sequences.map(s => s.prospect_id)));
    orchStates.forEach(s => {
      if (campProspectIds.size === 0 || campProspectIds.has(s.lead_id)) {
        wfOrchMap[s.lead_id] = s;
      }
    });
    // Synthesize orch-like entries for leads without orch state
    sequences.forEach(seq => {
      const sentCount = Math.max(0, (seq.current_step || 1) - 1);
      if (!wfOrchMap[seq.prospect_id] && sentCount > 0) {
        const isBounced = seq.status === 'bounced';
        wfOrchMap[seq.prospect_id] = {
          lead_id: seq.prospect_id,
          lead_name: seq.prospect_name || 'Unknown',
          lead_email: seq.prospect_email || '',
          lead_company: seq.prospect_company || '',
          status: isBounced ? 'stopped_bounced' : 'waiting_for_next_step',
          current_step_index: 0,
          engagement_score: 0,
          email_sent_count: sentCount,
          email_sent_steps: Array.from({length: sentCount}, (_, i) => i + 1),
          enrolled_at: camp.launched_at || camp.created_at || null,
          scheduled_next_at: null,
          linkedin_connected: false,
          linkedin_connect_sent: false,
          linkedin_connect_queued: false,
          linkedin_message_sent: false,
          linkedin_message_queued: false,
          _synthetic: true,
        };
      }
    });
    contentEl.innerHTML = buildWfHtml(camp, sequences, stats, inboxAccounts, liHealth);
    updateTopBarStats(camp, sequences, stats);
    updateCampaignStatusPill(camp.status);
    if (stats && stats.active_job) {
      _showGenerationOverlay(stats.active_job, camp, campaignId);
    }
    _fetchLinkedInQueueHealth();
  } catch(e) {
    contentEl.innerHTML = `<div style="color:var(--red);padding:20px;font-size:13px;">Error loading campaign: ${esc(String(e.message || e))}</div>`;
  }
}

let wfMaxStep = 1;
let editingCampaignId = null;  // non-null when editing an existing campaign

function initWfBuilder() {
  // Redirect to campaign wizard
  openCampaignWizard(null);
}

async function editCampaign(campaignId) {
  // Fetch the campaign details and open wizard at step 1 for editing
  const camp = await api(`/campaigns/${campaignId}`);
  editingCampaignId = campaignId;
  activeCampaignId = campaignId;
  cwCampaignId = campaignId;

  // Pre-fill audience: prospect_ids take priority over segment
  selectedProspects.clear();
  if (camp.prospect_ids && camp.prospect_ids.length > 0) {
    camp.prospect_ids.forEach(id => selectedProspects.add(id));
  }

  // Open wizard and pre-fill form
  _showWizard();
  cwData = {
    name: camp.name || '',
    segment: camp.segment_id || '',
    segmentName: camp.segment_name || '',
    goal: camp.goal || '',
    valueProp: camp.value_prop || '',
  };

  const nameEl = document.getElementById('cw-name');
  if (nameEl) nameEl.value = camp.name || '';
  const goalEl = document.getElementById('cw-goal');
  if (goalEl) goalEl.value = camp.goal || '';
  const vpEl = document.getElementById('cw-value-prop');
  if (vpEl) vpEl.value = camp.value_prop || '';

  _loadCwSegments(camp.segment_id || '');
  _loadCwSequenceDropdown(camp.sequence_config_id || '');
  _loadCwOutreachAccounts(camp.outreach_account_id || '');

  // Keep the campaign selected in dropdown
  const sel = document.getElementById('wf-campaign-select');
  if (sel) sel.value = campaignId;

  cwGoStep(1);
  toast(`Editing campaign: ${camp.name}`);
}

function cancelEdit() {
  editingCampaignId = null;
  // Reload the campaign view through the wizard
  if (activeCampaignId) {
    openCampaignWizard(activeCampaignId);
  } else {
    _hideWizard();
  }
}

async function saveEditOnly() {
  if (!editingCampaignId) return;
  const name = (document.getElementById('cw-name')?.value || '').trim();
  const goal = (document.getElementById('cw-goal')?.value || '').trim();
  const segmentId = document.getElementById('cw-segment')?.value || '';
  const valueProp = (document.getElementById('cw-value-prop')?.value || '').trim();
  const prospectIds = selectedProspects.size > 0 ? [...selectedProspects] : null;

  if (!name || !goal) { toast('Fill in campaign name and goal.', 'error'); return; }

  try {
    await api(`/campaigns/${editingCampaignId}`, {
      method: 'PATCH',
      body: JSON.stringify({ name, goal, value_prop: valueProp || null, segment_id: segmentId || null, prospect_ids: prospectIds }),
    });
    toast('Campaign updated.');
    editingCampaignId = null;
    await loadWorkflowCampaigns();
    // Reload campaign in the wizard
    openCampaignWizard(activeCampaignId);
  } catch(e) {
    toast('Save failed: ' + e.message, 'error');
  }
}

function wfGoStep(n) {
  if (n > wfMaxStep) return;
  builderStep = n;
  wfUpdateSteps();
  // Auto-generate sample LinkedIn messages when entering step 2 for the first time
  if (n === 2 && !_sampleSequenceId && activeCampaignId) {
    wfGenerateSample();
  }
}

function wfUpdateSteps() {
  for (let i = 1; i <= 4; i++) {
    const stepEl = document.getElementById(`wf-step-${i}`);
    if (stepEl) stepEl.classList.toggle('hidden', i !== builderStep);
    const tab = document.getElementById(`wf-step-tab-${i}`);
    if (tab) {
      tab.classList.toggle('active', i === builderStep);
      const locked = i > wfMaxStep;
      tab.style.opacity = locked ? '0.35' : '1';
      tab.style.cursor = locked ? 'not-allowed' : 'pointer';
      tab.title = locked ? 'Complete the previous step first' : '';
    }
  }
  // Hide old step 5 if it exists
  const step5 = document.getElementById('wf-step-5');
  if (step5) step5.classList.add('hidden');
  const tab5 = document.getElementById('wf-step-tab-5');
  if (tab5) tab5.style.display = 'none';

  if (builderStep === 3) {
    // Review step — createAndPrepare handles the content
  }
  if (builderStep === 4) renderLaunchSummary();
}

let wfSequences = [];
let wfCampaignId = null;
let wfOrchMap = {};   // prospect_id → orchestration state (populated by loadWfSequences)
let wfSeqShape = null; // per-campaign sequence shape from stats.sequence_shape  -  drives step dots in the monitor view so a newly saved workspace sequence doesn't retroactively reshape already-launched campaigns.

// Maps SEQ_STEPS index → email number (1-3). Initialized after SEQ_STEPS loads.
let EMAIL_SEQ_MAP = {};

// ── Unified campaign + sequence view ───────────────────────────────────────────
async function loadWfSequences(campaignId) {
  // Route through the wizard step 7 (live dashboard)
  wfCampaignId = campaignId;
  cwCampaignId = campaignId;
  _showWizard();
  cwGoStep(7);
  await _cwLoadDashboard(campaignId);
}

function buildWfHtml(camp, sequences, stats, inboxAccounts, liHealth) {
  const status = camp.status;
  const total = sequences.length;
  const approved = sequences.filter(s => s.approved).length;
  const inboxes = camp.inbox_ids || [];
  const isLive = status === 'active';
  const isPaused = status === 'paused';
  const liConnected = Object.values(wfOrchMap).filter(s => s.linkedin_connected).length;

  // Status banner
  // LinkedIn stats from health endpoint — must be computed before the status block uses them
  const liCampHealth = liHealth?.campaigns?.find(c => c.campaign_id === camp.id) || null;
  // Connect Sent = only successful sends (done), not failed attempts
  const liConnectSent = liCampHealth?.connect?.done || 0;
  const liMsgSent = liCampHealth?.message?.done || 0;
  const liMsgFailed = liCampHealth?.message?.failed || 0;
  const liNoResult = liCampHealth?.message_no_result_failures || 0;
  const liHealthStatus = liCampHealth?.status || 'ok';

  let statusColor, statusText, statusDesc, actionBtn;
  if (!total) {
    statusColor = '#ca8a04'; statusText = 'No sequences yet';
    statusDesc = 'Prepare LinkedIn outreach for this campaign.';
    actionBtn = `<button class="btn btn-primary" style="font-size:14px;padding:10px 24px;" onclick="wfPrepareLinkedIn()" title="Prepare LinkedIn connect and message sequences for every prospect in this campaign.">Prepare LinkedIn Outreach</button>`;
  } else if (isLive) {
    statusColor = '#16a34a'; statusText = 'Live';
    statusDesc = `${approved}/${total} leads enrolled. Connected: ${liConnected}. Messaged: ${liMsgSent}.`;
    actionBtn = `<button class="btn btn-outline btn-sm" onclick="wfPause()" title="Reversible. Pauses LinkedIn outreach. Leads stay enrolled  -  Resume to continue.">Pause Sending</button>
                 <button class="btn btn-outline btn-sm" style="color:var(--red);border-color:var(--red);" onclick="wfCancel()" title="Destructive. Cancels LinkedIn outreach for every enrolled lead in this campaign. Cannot be resumed.">Cancel Campaign</button>`;
  } else if (isPaused) {
    statusColor = '#ca8a04'; statusText = 'Paused';
    statusDesc = 'LinkedIn outreach is paused. No actions going out.';
    actionBtn = `<button class="btn btn-primary btn-sm" onclick="wfResume()" title="Resume LinkedIn outreach for this campaign.">Resume Sending</button>
                 <button class="btn btn-outline btn-sm" style="color:var(--red);border-color:var(--red);" onclick="wfCancel()" title="Destructive. Cancels LinkedIn outreach for all enrolled leads. Cannot be resumed.">Cancel Campaign</button>`;
  } else if (status === 'cancelled') {
    statusColor = '#dc2626'; statusText = 'Cancelled';
    statusDesc = 'This campaign has been cancelled and cannot be resumed  -  create a new one if you need to re-engage these contacts.';
    actionBtn = '';
  } else {
    statusColor = '#2563eb'; statusText = 'Review and approve';
    statusDesc = `${total} draft${total!==1?'s':''}, ${approved} approved. Nothing sent yet.`;
    actionBtn = `<button class="btn btn-primary" style="font-size:14px;padding:10px 24px;" onclick="wfPrepareLinkedIn()" title="Prepare LinkedIn connect and message sequences for every prospect in this campaign.">Prepare LinkedIn Outreach</button>
                 <button class="btn btn-outline btn-sm" onclick="wfApproveAllAndLaunch()" title="Approve all sequences and enroll leads into LinkedIn outreach.">Approve All and Send</button>
                 <button class="btn btn-outline btn-sm" style="color:var(--red);" onclick="clearWfSequences()" title="Delete all generated sequences and start over">Clear Sequences</button>`;
  }

  // PhantomBuster status line (replaces legacy inbox display)
  const inboxLine = `Channel: <strong>LinkedIn via PhantomBuster</strong>`;
  // Show which sequence this campaign was pinned to, if any. Pre-0009
  // campaigns (including Nasser v1.1) have sequence_config_id=null and
  // use the legacy global fallback at launch time  -  we label them as
  // "default" so operators can tell at a glance.
  const seqLine = camp.sequence_config_name
    ? `Sequence: <strong>${esc(camp.sequence_config_name)}</strong>`
    : `Sequence: <span style="color:var(--text-muted);" title="This campaign was created before per-campaign sequence selection existed. It will use the workspace's default sequence at launch time.">default (legacy)</span>`;
  // Stat bar using live orchestration data - dynamic step count from backend
  const _seqLen = stats.sequence_length || 6;
  const emailsSent = Array.from({length: _seqLen}, (_, i) => i + 1).reduce((sum, n) => sum + (stats[`email${n}_sent`]||0), 0);

  // (liCampHealth, liConnectSent, liMsgSent, liMsgFailed, liNoResult, liHealthStatus
  //  are defined above the status block — they must be available for statusDesc)

  // Warning banner: shown when message failures exist
  const liWarningHtml = liMsgFailed > 0 ? `
  <div style="margin-bottom:12px;padding:10px 14px;background:#fef3c7;border:1px solid #f59e0b;border-radius:var(--r-sm);display:flex;align-items:center;gap:10px;font-size:13px;">
    <span style="font-size:16px;">⚠️</span>
    <div>
      <strong style="color:#92400e;">LinkedIn Message Failures Detected</strong>
      <span style="color:#78350f;margin-left:8px;">${liMsgFailed} message${liMsgFailed !== 1 ? 's' : ''} failed${liNoResult > 0 ? ` (${liNoResult} phantom never confirmed)` : ''}.</span>
      <span style="color:#78350f;">Check PhantomBuster phantom logs and the CSV feed at <code>/api/pb/message-leads.csv</code>.</span>
    </div>
  </div>` : '';

  const liFailed = (liCampHealth?.connect?.failed || 0) + (liCampHealth?.message?.failed || 0);
  // Acceptance rate: connected / sent (avoid division by zero)
  const acceptRate = liConnectSent > 0 ? Math.round(liConnected / liConnectSent * 100) : 0;
  const liReplied = sequences.filter(s => s.status === 'replied').length;
  const statsHtml = liWarningHtml + `<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-bottom:16px;">
    ${[
      ['Leads', total, 'var(--text)'],
      ['Connect Sent', liConnectSent, '#0a66c2'],
      ['Connected', liConnected + (liConnectSent > 0 ? ` (${acceptRate}%)` : ''), '#0a66c2'],
      ['Messaged', liMsgSent, liMsgSent > 0 ? '#0a66c2' : 'var(--text-muted)'],
      ['Replied', liReplied, liReplied > 0 ? '#16a34a' : 'var(--text-muted)'],
      ['Failed', liFailed, liFailed > 0 ? 'var(--red)' : 'var(--text-muted)'],
    ].map(([l,v,c]) => `<div style="background:var(--surface);border:1px solid ${l === 'Failed' && v > 0 ? 'rgba(220,38,38,0.3)' : 'var(--border)'};padding:10px;text-align:center;">
      <div style="font-size:1.35rem;font-weight:600;color:${c};letter-spacing:-0.02em;">${v}</div>
      <div style="font-size:10px;color:var(--text-muted);margin-top:3px;text-transform:uppercase;letter-spacing:0.07em;">${l}</div>
    </div>`).join('')}
  </div>
  <!-- LinkedIn Queue Health Banner (populated async) -->
  <div id="li-queue-health-banner" class="hidden" style="margin-bottom:12px;padding:10px 14px;border-radius:var(--r-sm);font-size:13px;"></div>`;

  // Per-step progress strip  -  shows where leads are sitting in the sequence.
  // Backed by EmailSequence.current_step (1..N email sends) which is bumped
  // on every email_sent webhook (see orchestration/ingestion._confirm_send_log).
  //
  // The strip is driven off the SAME source the dots use  -  wfSeqShape  -  so the
  // number of buckets, their labels, and their order always match what the
  // per-lead dot row shows. For sequences with no shape yet (draft) we fall
  // back to the backend-reported sequence_length.
  let progressStripHtml = '';
  const stepDist = stats.step_distribution || {};
  const emailShape = (Array.isArray(wfSeqShape) ? wfSeqShape : [])
    .filter(s => s.type === 'email');
  const seqLen = emailShape.length || stats.sequence_length || 6;
  const distTotal = Object.values(stepDist).reduce((a,b) => a + (b||0), 0);
  if (distTotal > 0 && (isLive || isPaused || status === 'cancelled' || status === 'completed')) {
    // Monochrome accent-ramp: darker accent = later in the sequence. Keeps the
    // progress strip in the single-color family now that we no longer use
    // the rainbow emoji/step-color palette.
    const rampFor = (n, total) => {
      const ratio = total > 1 ? (n - 1) / (total - 1) : 0;
      // HSL ramp from light-accent to dark-accent, single hue.
      const light = 72 - ratio * 38; // 72% → 34%
      return `hsl(160, 38%, ${light}%)`;
    };
    const segments = [];
    let renderedAny = false;
    for (let n = 1; n <= seqLen; n++) {
      const cnt = stepDist[n] || 0;
      const pct = distTotal > 0 ? (cnt / distTotal) * 100 : 0;
      const shapeEntry = emailShape[n - 1];
      const stepLabel = shapeEntry
        ? `Day ${shapeEntry.day} · ${shapeEntry.label || 'Email ' + n}`
        : `Email ${n}`;
      const bg = cnt > 0 ? rampFor(n, seqLen) : 'var(--border)';
      const flex = cnt > 0 ? cnt : 0.25; // keep empty buckets visible as a thin gutter
      const title = `${stepLabel}  -  ${cnt} lead${cnt!==1?'s':''} (${pct.toFixed(0)}%)`;
      const showLabel = cnt > 0 && pct >= 9;
      segments.push(`<div title="${esc(title)}" style="flex:${flex};background:${bg};display:flex;align-items:center;justify-content:center;font-size:10px;color:#fff;font-weight:600;min-width:0;padding:0 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${showLabel ? esc('E' + n) : ''}</div>`);
      if (cnt > 0) renderedAny = true;
    }
    const avg = stats.avg_step_reached || 0;
    const avgStr = (typeof avg === 'number' ? avg.toFixed(2) : avg);
    // Legend row: mirrors the sequence dots  -  one marker per step, in order.
    const legend = emailShape.length
      ? emailShape.map((s, i) => `<span style="display:inline-flex;align-items:center;gap:4px;font-size:10px;color:var(--text-muted);"><span style="width:8px;height:8px;border-radius:50%;background:${rampFor(i+1, seqLen)};"></span>${esc((s.label || 'Email ' + (i+1)))}</span>`).join('')
      : '';
    progressStripHtml = `<div style="margin-bottom:16px;padding:12px 14px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r-sm);">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;gap:12px;flex-wrap:wrap;">
        <span style="font-size:11px;text-transform:uppercase;letter-spacing:0.07em;color:var(--text-muted);font-weight:600;">Sequence progress</span>
        <span style="font-size:11px;color:var(--text-dim);">Average step reached: <strong>${avgStr}</strong> of ${seqLen}</span>
      </div>
      <div style="display:flex;height:18px;overflow:hidden;border-radius:3px;background:var(--bg);" title="Distribution of leads across sequence steps">
        ${renderedAny ? segments.join('') : '<div style="flex:1;color:var(--text-muted);font-size:11px;display:flex;align-items:center;justify-content:center;">No progress yet</div>'}
      </div>
      ${legend ? `<div style="display:flex;flex-wrap:wrap;gap:10px 14px;margin-top:8px;">${legend}</div>` : ''}
    </div>`;
  }

  // Generation progress (hidden by default, shown by JS)
  const progressHtml = `<div id="wf-gen-progress" style="display:none;margin-bottom:14px;padding:14px 18px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r-sm);">
    <div style="display:flex;align-items:center;gap:16px;margin-bottom:12px;">
      <img id="wf-gen-img" src="/assets/demo-avatar.svg" alt="" style="width:80px;height:80px;border-radius:var(--r);object-fit:contain;flex-shrink:0;" />
      <div style="flex:1;">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">
          <div style="display:flex;align-items:center;gap:10px;">
            <div class="loading"></div>
            <span id="wf-gen-status" style="font-size:13px;color:var(--text-dim);font-weight:500;">Preparing LinkedIn outreach…</span>
          </div>
          <div style="display:flex;align-items:center;gap:14px;font-size:12px;font-family:'JetBrains Mono',monospace;">
            <span id="wf-gen-timer" style="color:var(--navy);font-weight:600;" title="Elapsed time">0:00</span>
            <span id="wf-gen-eta" style="color:var(--text-muted);" title="Estimated time remaining"></span>
          </div>
        </div>
        <div style="background:var(--border);height:4px;border-radius:2px;overflow:hidden;">
          <div id="wf-gen-bar" style="height:100%;background:var(--accent);width:0%;transition:width 0.4s;border-radius:2px;"></div>
        </div>
        <div id="wf-gen-speed" style="margin-top:6px;font-size:11px;color:var(--text-muted);display:none;"></div>
        <button id="wf-cancel-btn" class="btn btn-outline btn-sm" style="margin-top:10px;color:var(--red);border-color:var(--red);" onclick="cancelGeneration()" title="Stop the generation job that is currently running.">Cancel</button>
      </div>
    </div>
  </div>`;

  // Unified lead table with pagination
  const WF_SEQ_PAGE_SIZE = 50;
  let tableHtml = '';
  if (sequences.length) {
    // Store full list for pagination
    window._wfAllSequences = sequences;
    window._wfSeqPage = 0;
    const pageRows = sequences.slice(0, WF_SEQ_PAGE_SIZE);
    const rows = pageRows.map(seq => buildUnifiedLeadRow(seq, wfOrchMap[seq.prospect_id])).join('');
    const totalPages = Math.ceil(sequences.length / WF_SEQ_PAGE_SIZE);
    tableHtml = `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;flex-wrap:wrap;">
        <input type="text" id="wf-seq-search" placeholder="Search leads…" style="width:200px;font-size:13px;" oninput="filterWfSequences()" />
        <span id="wf-seq-count" style="font-size:12px;color:var(--text-muted);">${total} lead${total!==1?'s':''} · Click any row to view full sequence</span>
      </div>
      <div style="overflow-x:auto;">
      <table style="width:100%;border-collapse:collapse;font-size:13px;" id="wf-seq-list">
        <thead>
          <tr style="border-bottom:2px solid var(--border);text-align:left;background:var(--surface);">
            <th style="padding:8px 12px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:var(--text-muted);">Lead</th>
            <th style="padding:8px 12px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:var(--text-muted);">Company</th>
            <th style="padding:8px 12px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:var(--text-muted);">30-Day Journey</th>
            <th style="padding:8px 12px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:var(--text-muted);">Emails</th>
            <th style="padding:8px 12px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:var(--text-muted);">LinkedIn</th>
            <th style="padding:8px 12px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:var(--text-muted);">Score</th>
            <th style="padding:8px 12px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:var(--text-muted);">Status</th>
            <th style="padding:8px 12px;"></th>
          </tr>
        </thead>
        <tbody id="wf-seq-tbody">
          ${rows}
        </tbody>
      </table>
      </div>
      <div id="wf-seq-pagination" style="display:flex;align-items:center;justify-content:center;gap:12px;padding:12px 0;border-top:1px solid var(--border);margin-top:8px;position:sticky;bottom:0;background:var(--bg);">
        <button class="btn btn-outline btn-sm" id="wf-seq-prev" onclick="_wfSeqPrev()" style="font-size:12px;padding:6px 16px;" ${sequences.length <= WF_SEQ_PAGE_SIZE ? 'disabled' : ''}>← Previous</button>
        <span id="wf-seq-page-info" style="font-size:12px;color:var(--text-muted);font-weight:500;">Page 1 of ${totalPages}</span>
        <button class="btn btn-outline btn-sm" id="wf-seq-next" onclick="_wfSeqNext()" style="font-size:12px;padding:6px 16px;" ${totalPages <= 1 ? 'disabled' : ''}>Next →</button>
      </div>`;
  }

  return `
    <div style="padding:14px 18px;margin-bottom:16px;border:2px solid ${statusColor};background:var(--surface);">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:10px;">
        <div>
          <div style="font-size:15px;font-weight:700;">${esc(camp.name)}</div>
          <div style="margin-top:4px;"><span style="color:${statusColor};font-weight:600;font-size:13px;">${statusText}</span>
            <span style="color:var(--text-muted);font-size:12px;margin-left:8px;">${statusDesc}</span></div>
          <div style="margin-top:6px;font-size:12px;color:var(--text-muted);">${inboxLine}</div>
          <div style="margin-top:2px;font-size:12px;color:var(--text-muted);">${seqLine}</div>
          <div style="margin-top:2px;font-size:12px;color:var(--text-muted);">Outreach: <strong>${camp.outreach_account_name ? esc(camp.outreach_account_name) : '<span style="color:var(--text-muted);">default (env config)</span>'}</strong></div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
          <button class="btn btn-outline btn-sm" onclick="editCampaign('${esc(camp.id)}')" title="Edit campaign settings">&#9998; Edit</button>
          ${actionBtn}
        </div>
      </div>
    </div>
    ${progressHtml}
    ${total ? statsHtml : ''}
    ${progressStripHtml}
    ${tableHtml}`;
}

function buildUnifiedLeadRow(seq, orch) {
  const name    = seq.prospect_name    || orch?.lead_name    || 'Unknown';
  const email   = seq.prospect_email   || orch?.lead_email   || '';
  const company = seq.prospect_company || orch?.lead_company || ' - ';

  // Journey: orchestration view if enrolled, else simple status.
  // Use the per-campaign sequence shape (wfSeqShape) so the dot count
  // reflects what THIS campaign was launched with  -  not whatever the
  // workspace's global sequence config happens to be right now.
  const journeyHtml = orch
    ? renderStepDots(orch, wfSeqShape || SEQ_STEPS)
    : `<span style="font-size:11px;color:var(--text-muted);">Not enrolled in 30-day sequence</span>`;

  // Email pills  -  dynamic based on how many emails exist
  const emailPills = [1,2,3,4,5,6].map(n => {
    const e = seq[`email${n}`];
    const hasContent = e?.subject || e?.body;
    if (!hasContent) return '';
    let bg, title;
    if (seq.approved) { bg = 'var(--green)'; title = `Email ${n}: approved`; }
    else { bg = '#3b82f6'; title = `Email ${n}: draft`; }
    return `<span title="${title}" style="font-size:10px;font-weight:600;padding:2px 6px;background:${bg};color:#fff;letter-spacing:0.04em;">E${n}</span>`;
  }).filter(Boolean).join('') || '<span style="font-size:10px;color:var(--text-muted);"> - </span>';

  // LinkedIn
  const liHtml = orch?.linkedin_connected
    ? `<span style="font-size:11px;color:#0a66c2;font-weight:600;display:inline-flex;align-items:center;gap:4px;">${icon('link',11)} Connected</span>`
    : `<span style="font-size:11px;color:var(--text-muted);"> - </span>`;

  // Score  -  prefer orch score, fallback to prospect engagement_score
  const score = orch?.engagement_score ?? seq.engagement_score ?? null;
  const scoreHtml = score !== null && score > 0
    ? (score >= 5
        ? `<span class="badge badge-green" style="font-size:11px;display:inline-flex;align-items:center;gap:3px;">${icon('zap',11)} ${score}</span>`
        : `<span style="color:var(--text-muted);font-size:12px;">${score}</span>`)
    : `<span style="color:var(--text-muted);font-size:12px;"> - </span>`;

  // Status: prefer orch status, fallback to email seq status
  const rowStatus = orch ? seqStatusBadge(orch.status) : statusBadge(seq.status);

  return `<tr data-seq-id="${seq.id}" data-prospect-id="${seq.prospect_id}"
      style="border-bottom:1px solid var(--border);cursor:pointer;transition:background 0.1s;"
      onclick="openUnifiedDrawer('${esc(seq.id)}','${esc(seq.prospect_id)}')"
      onmouseenter="this.style.background='var(--surface)'" onmouseleave="this.style.background=''">
    <td style="padding:10px 12px;">
      <div style="font-weight:600;font-size:13px;">${esc(name)}</div>
      <div style="font-size:11px;color:var(--text-muted);">${esc(email)}</div>
    </td>
    <td style="padding:10px 12px;color:var(--text-dim);font-size:13px;">${esc(company)}</td>
    <td style="padding:10px 12px;">${journeyHtml}</td>
    <td style="padding:10px 12px;"><div style="display:flex;gap:3px;">${emailPills}</div></td>
    <td style="padding:10px 12px;">${liHtml}</td>
    <td style="padding:10px 12px;text-align:center;">${scoreHtml}</td>
    <td style="padding:10px 12px;">${rowStatus}</td>
    <td style="padding:10px 12px;text-align:right;white-space:nowrap;" onclick="event.stopPropagation();">
      <button class="btn btn-outline btn-sm" onclick="openUnifiedDrawer('${esc(seq.id)}','${esc(seq.prospect_id)}')" title="View →.">View →</button>
    </td>
  </tr>`;
}

function filterWfSequences() {
  const q = (document.getElementById('wf-seq-search')?.value || '').toLowerCase().trim();
  const tbody = document.getElementById('wf-seq-tbody');
  if (!tbody) return;
  tbody.querySelectorAll('tr[data-seq-id]').forEach(row => {
    const text = row.textContent.toLowerCase();
    row.style.display = !q || text.includes(q) ? '' : 'none';
  });
}

async function saveWfSequence(seqId) {
  const statusEl = document.getElementById(`save-status-${seqId}`);
  const payload = {};
  for (let n = 1; n <= 6; n++) {
    const subj = document.getElementById(`subj-${seqId}-${n}`);
    const body = document.getElementById(`body-${seqId}-${n}`);
    if (subj && subj.value !== undefined) payload[`email${n}_subject`] = subj.value;
    if (body && body.value !== undefined) payload[`email${n}_body`] = body.value;
  }
  if (!Object.keys(payload).length) { toast('No email fields to save.', 'error'); return; }
  // Save A/B variant subject if present
  const subjB = document.getElementById(`subj-b-${seqId}`);
  if (subjB) payload.email1_subject_b = subjB.value;
  try {
    const updated = await api(`/campaigns/${wfCampaignId}/sequences/${seqId}`, { method: 'PATCH', body: JSON.stringify(payload) });
    if (statusEl) { statusEl.textContent = '✓ Saved'; setTimeout(() => statusEl.textContent = '', 2500); }
    const idx = wfSequences.findIndex(s => s.id === seqId);
    if (idx !== -1) {
      for (let n = 1; n <= 6; n++) wfSequences[idx][`email${n}`] = { subject: payload[`email${n}_subject`], body: payload[`email${n}_body`] };
      // Backend resets approved when content changes  -  mirror that in the UI
      if (!updated.approved && wfSequences[idx].approved) {
        wfSequences[idx].approved = false;
        const row = document.querySelector(`[data-seq-id="${seqId}"]`);
        if (row) row.querySelectorAll('.badge-green').forEach(b => b.remove());
      }
    }
  } catch(e) {
    if (statusEl) { statusEl.style.color = 'var(--red)'; statusEl.textContent = 'Save failed'; }
  }
}

async function approveWfSequence(seqId) {
  try {
    await api(`/campaigns/${wfCampaignId}/sequences/${seqId}`, { method: 'PATCH', body: JSON.stringify({ approved: true }) });
    toast('Approved.');
    const idx = wfSequences.findIndex(s => s.id === seqId);
    if (idx !== -1) wfSequences[idx].approved = true;
    loadWfSequences(wfCampaignId);
  } catch(e) { toast('Failed: ' + e.message, 'error'); }
}

let _wfGenerating = false;  // guard against double-click starting two pollers
let _genTimerInterval = null;

function _fmtTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function _startGenTimer() {
  const startTime = Date.now();
  if (_genTimerInterval) clearInterval(_genTimerInterval);
  _genTimerInterval = setInterval(() => {
    const el = document.getElementById('wf-gen-timer');
    if (el) el.textContent = _fmtTime((Date.now() - startTime) / 1000);
  }, 1000);
  return startTime;
}

function _stopGenTimer() {
  if (_genTimerInterval) { clearInterval(_genTimerInterval); _genTimerInterval = null; }
}

// wfGenerateAll removed — replaced by wfPrepareLinkedIn (LinkedIn-only flow)
async function wfPrepareLinkedIn() {
  if (!wfCampaignId) return;
  const btn = document.querySelector('button[onclick="wfPrepareLinkedIn()"]');
  if (btn) { btn.disabled = true; btn.textContent = 'Preparing…'; }
  try {
    const result = await api(`/campaigns/${wfCampaignId}/prepare`, {
      method: 'POST',
      body: JSON.stringify({}),
    });
    toast(`LinkedIn outreach prepared for ${result.prospects_prepared || 0} prospects.`);
    await loadWfSequences(wfCampaignId);
  } catch(e) {
    toast('Prepare failed: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Prepare LinkedIn Outreach'; }
  }
}

function _resetGenUi() {
  const progress = document.getElementById('wf-gen-progress');
  if (progress) progress.style.display = 'none';
  const st = document.getElementById('wf-gen-status');
  if (st) st.textContent = '';
  const bar = document.getElementById('wf-gen-bar');
  if (bar) bar.style.width = '0%';
  const etaEl = document.getElementById('wf-gen-eta');
  if (etaEl) etaEl.textContent = '';
  const speedEl = document.getElementById('wf-gen-speed');
  if (speedEl) { speedEl.style.display = 'none'; speedEl.textContent = ''; }
  const timerEl = document.getElementById('wf-gen-timer');
  if (timerEl) timerEl.textContent = '0:00';
}

let _wfLaunching = false;  // guard against double-submit
async function wfApproveAllAndLaunch() {
  if (!wfCampaignId) return;
  if (_wfLaunching) return;
  if (!await customConfirm('Approve all sequences and launch LinkedIn outreach?')) return;
  const launchBtns = document.querySelectorAll('button[onclick="wfApproveAllAndLaunch()"]');
  const origBtnTexts = [];
  launchBtns.forEach(b => { origBtnTexts.push(b.textContent); b.disabled = true; b.textContent = 'Working…'; });
  _wfLaunching = true;
  try {
    // LinkedIn-only: no inbox validation needed
    await api(`/campaigns/${wfCampaignId}/approve-all`, { method: 'POST' });
    const result = await api(`/campaigns/${wfCampaignId}/launch`, { method: 'POST' });
    toast(`Launched. ${result.leads_enrolled || 0} leads enrolled.`);
    loadWfSequences(wfCampaignId);
  } catch(e) { toast('Launch failed: ' + e.message, 'error'); }
  finally { _wfLaunching = false; launchBtns.forEach((b, i) => { b.disabled = false; b.textContent = origBtnTexts[i] || 'Approve All and Send'; }); }
}

async function wfPause() {
  if (!wfCampaignId) return;
  const btn = document.querySelector('button[onclick="wfPause()"]');
  if (btn) btn.disabled = true;
  try { await pauseCampaign(wfCampaignId); await loadWfSequences(wfCampaignId); await loadWorkflowCampaigns(); }
  finally { if (btn) btn.disabled = false; }
}

async function wfResume() {
  if (!wfCampaignId) return;
  const btn = document.querySelector('button[onclick="wfResume()"]');
  if (btn) btn.disabled = true;
  try { await resumeCampaign(wfCampaignId); await loadWfSequences(wfCampaignId); await loadWorkflowCampaigns(); }
  finally { if (btn) btn.disabled = false; }
}

async function clearWfSequences() {
  if (!wfCampaignId) return;
  if (!await customConfirm('Delete all generated sequences for this campaign? You can re-generate them afterwards. This cannot be undone.', {destructive: true})) return;
  try {
    await api(`/campaigns/${wfCampaignId}/sequences?confirm=true`, { method: 'DELETE' });
    toast('Sequences cleared.');
    loadWfSequences(wfCampaignId);
  } catch(e) { toast('Failed: ' + e.message, 'error'); }
}

async function viewCampaignContacts() {
  const sel = document.getElementById('wf-campaign-select');
  const id = sel?.value;
  if (!id || id === '__new__') { toast('Select a campaign first.', 'error'); return; }
  const modal = document.getElementById('contacts-modal');
  const body = document.getElementById('contacts-modal-body');
  body.innerHTML = '<div style="text-align:center;padding:24px;color:var(--text-muted);"><div class="loading"></div> Loading contacts…</div>';
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  try {
    const data = await api(`/campaigns/${id}/contacts`);
    if (!data.prospects.length) {
      body.innerHTML = '<div style="text-align:center;padding:24px;color:var(--text-muted);">No contacts assigned to this campaign.</div>';
      return;
    }
    const validColor = s => {
      if (!s) return 'var(--text-muted)';
      if (s === 'valid') return 'var(--green)';
      if (['invalid','do_not_mail','spamtrap','abuse'].includes(s)) return 'var(--red)';
      return 'var(--yellow)';
    };
    // Count statuses for summary
    const validCount = data.prospects.filter(p => !p.email_validation_status || !['invalid','do_not_mail','spamtrap','abuse'].includes(p.email_validation_status)).length;
    const invalidCount = data.total - validCount;
    const summaryParts = [`${data.total} total`];
    if (validCount) summaryParts.push(`${validCount} eligible`);
    if (invalidCount) summaryParts.push(`<span style="color:var(--red);">${invalidCount} invalid</span>`);
    body.innerHTML = `
      <div style="font-size:12px;color:var(--text-muted);margin-bottom:10px;">${summaryParts.join(' · ')}</div>
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead><tr style="border-bottom:2px solid var(--border);text-align:left;">
          <th style="padding:6px 10px;font-size:10px;font-weight:700;text-transform:uppercase;color:var(--text-muted);">Name</th>
          <th style="padding:6px 10px;font-size:10px;font-weight:700;text-transform:uppercase;color:var(--text-muted);">Email</th>
          <th style="padding:6px 10px;font-size:10px;font-weight:700;text-transform:uppercase;color:var(--text-muted);">Company</th>
          <th style="padding:6px 10px;font-size:10px;font-weight:700;text-transform:uppercase;color:var(--text-muted);">Title</th>
          <th style="padding:6px 10px;font-size:10px;font-weight:700;text-transform:uppercase;color:var(--text-muted);">Valid</th>
        </tr></thead>
        <tbody>${data.prospects.map(p => `<tr style="border-bottom:1px solid var(--border);">
          <td style="padding:6px 10px;font-weight:500;">${esc(p.first_name || '')} ${esc(p.last_name || '')}</td>
          <td style="padding:6px 10px;font-size:12px;">${esc(p.email || '')}</td>
          <td style="padding:6px 10px;">${esc(p.company || '')}</td>
          <td style="padding:6px 10px;font-size:12px;color:var(--text-muted);">${esc(p.title || '')}</td>
          <td style="padding:6px 10px;"><span style="font-size:11px;font-weight:600;color:${validColor(p.email_validation_status)};">${esc(p.email_validation_status || ' - ')}</span></td>
        </tr>`).join('')}</tbody>
      </table>`;
  } catch (e) {
    body.innerHTML = `<div style="color:var(--red);padding:14px;">Failed: ${esc(e.message)}</div>`;
  }
}

function closeContactsModal() {
  const m = document.getElementById('contacts-modal');
  m.classList.add('hidden');
  m.style.display = '';
}

async function cloneCurrentCampaign() {
  const sel = document.getElementById('wf-campaign-select');
  const id = sel?.value;
  if (!id || id === '__new__') { toast('Select a campaign to clone.', 'error'); return; }
  const btn = document.querySelector('button[onclick="cloneCurrentCampaign()"]');
  if (btn) btn.disabled = true;
  try {
    const cloned = await api(`/campaigns/${id}/clone`, { method: 'POST' });
    toast(`Cloned as "${cloned.name}"`);
    await loadWorkflowCampaigns();
    // Select the new clone
    const newSel = document.getElementById('wf-campaign-select');
    if (newSel) newSel.value = cloned.id;
    loadWfSequences(cloned.id);
  } catch(e) { toast('Clone failed: ' + e.message, 'error'); }
  finally { if (btn) btn.disabled = false; }
}

async function deleteCurrentCampaign() {
  const sel = document.getElementById('wf-campaign-select');
  const id = sel?.value;
  if (!id || id === '__new__') { toast('Select a campaign first.', 'error'); return; }
  const name = sel.options[sel.selectedIndex]?.text || id;
  if (!await customConfirm(`Delete campaign "${name}" and all its emails? This cannot be undone.`, {destructive: true})) return;
  const btn = document.querySelector('button[onclick="deleteCurrentCampaign()"]');
  if (btn) { btn.disabled = true; btn.textContent = 'Deleting…'; }
  try {
    await api(`/campaigns/${id}?confirm=true`, { method: 'DELETE' });
    toast(`Campaign deleted.`);
    wfCampaignId = null;
    _hideWizard();
    const emptyEl = document.getElementById('wf-empty-state');
    if (emptyEl) emptyEl.style.display = '';
    loadWorkflowCampaigns();
  } catch(e) { toast('Delete failed: ' + e.message, 'error'); }
  finally { if (btn) { btn.disabled = false; btn.textContent = 'Delete'; } }
}

// ── AI-powered LinkedIn message generation & refinement ─────────────────────
let _sampleSequenceId = null;
let _sampleCampaignId = null;

async function wfGenerateSample() {
  // Redirects to wizard version
  return cwGenerateSample();
}

async function wfRegenerateSample() {
  return cwRegenerateSample();
}

async function wfRefineMessage(step) {
  // Redirects to wizard version
  return cwRefineMessage(step);
}

async function wfApproveAndApply() {
  // Redirects to wizard version
  return cwApproveAndApply();
}

// Campaign builder (used in workflow) — LinkedIn-only flow
async function createAndPrepare() {
  const name = (document.getElementById('cw-name')?.value || '').trim();
  const goal = (document.getElementById('cw-goal')?.value || '').trim();
  const segmentId = document.getElementById('cw-segment')?.value || '';
  const valuePropEl = document.getElementById('cw-value-prop');
  const valueProp = valuePropEl?.value.trim() || '';

  if (!name || !goal) { toast('Fill in campaign name and goal.', 'error'); return; }

  // Check for duplicate campaign name
  const existingNames = (_cachedCampaigns || []).map(c => (c.name || '').trim().toLowerCase());
  if (existingNames.includes(name.toLowerCase())) {
    toast('A campaign with this name already exists. Choose a different name.', 'error');
    return;
  }

  // Validate audience: must have either selected prospects or a segment
  const prospectIds = selectedProspects.size > 0 ? [...selectedProspects] : null;
  if (!prospectIds && !segmentId) {
    toast('Select leads first (use checkboxes in the left panel) or choose a segment.', 'error');
    return;
  }

  const prepBtn = document.getElementById('cw-create-btn');
  if (prepBtn) { prepBtn.disabled = true; prepBtn.textContent = 'Working...'; }

  try {
    // Resolve value prop: use field value, fall back to saved default
    let finalValueProp = valueProp;
    if (!finalValueProp) {
      const vp = await api('/settings/value-prop').catch(() => ({}));
      finalValueProp = vp.value_prop || '';
    } else {
      api('/settings/value-prop', { method: 'POST', body: JSON.stringify({ instructions: finalValueProp }) })
        .then(() => { if (valuePropEl) valuePropEl.dataset.edited = ''; showSaveIndicator('cw-value-prop-save-indicator'); })
        .catch(e => console.error('Value prop auto-save failed:', e));
    }

    const sequenceConfigId = document.getElementById('cw-sequence-config')?.value || '';
    let camp;
    if (editingCampaignId) {
      const patchBody = { name, goal, value_prop: finalValueProp, segment_id: segmentId || null, prospect_ids: prospectIds };
      if (sequenceConfigId) patchBody.sequence_config_id = sequenceConfigId;
      camp = await api(`/campaigns/${editingCampaignId}`, {
        method: 'PATCH',
        body: JSON.stringify(patchBody),
      });
    } else {
      camp = await api('/campaigns/', {
        method: 'POST',
        body: JSON.stringify({ name, goal, value_prop: finalValueProp, segment_id: segmentId || null, prospect_ids: prospectIds, sequence_config_id: sequenceConfigId || null }),
      });
    }
    activeCampaignId = camp.id;
    wfCampaignId = camp.id;

    // Update dropdown to show the campaign name
    const sel = document.getElementById('wf-campaign-select');
    if (sel) {
      let opt = [...sel.options].find(o => o.value === camp.id);
      if (!opt) {
        opt = document.createElement('option');
        opt.value = camp.id;
        sel.add(opt, sel.options[2] || null);
      }
      opt.textContent = `${esc(camp.name)} (draft)`;
      sel.value = camp.id;
    }

    // Prepare LinkedIn sequences (instant, no polling needed)
    const result = await api(`/campaigns/${camp.id}/prepare`, {
      method: 'POST',
      body: JSON.stringify({}),
    });

    // Route to wizard step 6 (review) after prepare
    editingCampaignId = null;
    cwCampaignId = camp.id;
    loadWorkflowCampaigns();
    const sequences = await api(`/campaigns/${camp.id}/preview`).catch(() => []);
    _cwLoadReviewTable(sequences || []);
    cwGoStep(6);
    toast(`LinkedIn outreach prepared for ${result.prospects_prepared || 0} prospects.`);
  } catch(e) {
    cwGoStep(3);
    toast('Prepare failed: ' + e.message, 'error');
  } finally {
    if (prepBtn) { prepBtn.disabled = false; prepBtn.textContent = 'Create & Generate Sample'; }
    // Wizard handles visibility now; no manual toggle needed
  }
}
// Keep backward compat alias
const createAndGenerate = createAndPrepare;

let _activeJobId = null;

function pollJob(jobId, onProgress) {
  _activeJobId = jobId;
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + 10 * 60 * 1000;
    let consecutiveFails = 0;
    const interval = setInterval(async () => {
      if (Date.now() > deadline) { clearInterval(interval); _activeJobId = null; reject(new Error('Job timed out.')); return; }
      try {
        const job = await api(`/jobs/${jobId}`);
        consecutiveFails = 0;
        if (onProgress) onProgress(job);
        if (job.status === 'done' || job.status === 'completed_with_errors') { clearInterval(interval); _activeJobId = null; resolve(job); }
        else if (job.status === 'failed') { clearInterval(interval); _activeJobId = null; reject(new Error(job.error || 'Job failed')); }
        else if (job.status === 'cancelled') { clearInterval(interval); _activeJobId = null; reject(new Error('Generation cancelled.')); }
      } catch(e) {
        if (++consecutiveFails > 5) { clearInterval(interval); _activeJobId = null; reject(new Error('Lost connection while polling job.')); return; }
      }
    }, 3000);
  });
}

async function cancelGeneration() {
  if (!_activeJobId) { toast('No generation in progress.', 'error'); return; }
  try {
    await api(`/jobs/${_activeJobId}/cancel`, { method: 'POST' });
    toast('Cancelling generation…');
    // Optimistically reset UI now.
    _stopGenTimer();
    _resetGenUi();
    document.querySelectorAll('#cw-dashboard-content button').forEach(b => b.disabled = false);
    _wfGenerating = false;
  } catch(e) {
    toast('Cancel failed: ' + e.message, 'error');
  }
}

async function approveAndContinue() {
  if (!activeCampaignId) return;
  wfCampaignId = activeCampaignId; // keep in sync
  try {
    await api(`/campaigns/${activeCampaignId}/approve-all`, { method: 'POST' });
    toast('All sequences approved.');
    // Reload the review table in the wizard
    const sequences = await api(`/campaigns/${activeCampaignId}/preview`).catch(() => []);
    _cwLoadReviewTable(sequences || []);
    cwGoStep(6);
  } catch(e) { toast('Error: ' + e.message, 'error'); }
}

function _showGenerationOverlay(activeJob, camp, campaignId) {
  const el = document.getElementById('cw-dashboard-content') || document.getElementById('wf-empty-state');
  const pct = activeJob.progress || 0;
  const detail = activeJob.progress_detail || 'Claude is generating email sequences...';
  el.innerHTML = `
    <div style="padding:40px 20px;text-align:center;">
      <div style="font-size:18px;font-weight:600;margin-bottom:8px;">Generating Sequences</div>
      <div style="font-size:13px;color:var(--text-muted);margin-bottom:20px;">${esc(camp.name)}</div>
      <div style="background:var(--border);border-radius:8px;height:24px;overflow:hidden;max-width:500px;margin:0 auto 12px;">
        <div id="gen-overlay-bar" style="background:var(--accent);height:100%;width:${pct}%;transition:width 0.5s ease;border-radius:8px;"></div>
      </div>
      <div id="gen-overlay-pct" style="font-size:24px;font-weight:700;color:var(--accent);margin-bottom:4px;">${pct}%</div>
      <div id="gen-overlay-detail" style="font-size:12px;color:var(--text-muted);margin-bottom:20px;">${esc(detail)}</div>
      <div style="font-size:11px;color:var(--text-muted);margin-bottom:16px;">
        This takes ~20 seconds per contact. Do not close this page.<br>
        Generation continues in the background even if you navigate away.
      </div>
      <button class="btn btn-outline btn-sm" style="color:var(--red);" onclick="cancelGeneration()">Cancel Generation</button>
    </div>
  `;
  // Disable launch button
  const launchBtn = document.getElementById('top-launch-btn');
  if (launchBtn) { launchBtn.disabled = true; launchBtn.title = 'Wait for generation to finish'; }
  // Poll for progress updates
  const pollInterval = setInterval(async () => {
    try {
      const job = await api('/jobs/' + activeJob.id);
      const bar = document.getElementById('gen-overlay-bar');
      const pctEl = document.getElementById('gen-overlay-pct');
      const detailEl = document.getElementById('gen-overlay-detail');
      if (bar) bar.style.width = (job.progress || 0) + '%';
      if (pctEl) pctEl.textContent = (job.progress || 0) + '%';
      if (detailEl) detailEl.textContent = job.progress_detail || 'Generating...';
      if (job.status === 'done' || job.status === 'completed_with_errors' || job.status === 'failed' || job.status === 'cancelled') {
        clearInterval(pollInterval);
        const msg = job.status === 'done' ? 'Generation complete!' : 'Generation ' + job.status;
        toast(msg);
        loadWfSequences(campaignId);
      }
    } catch(e) {
      // Lost connection — keep polling
    }
  }, 3000);
  _activeJobId = activeJob.id;
}

function renderLaunchSummary() {
  // Legacy function — no longer used by the wizard but kept for backward compat
  const el = document.getElementById('builder-launch-summary');
  if (!el) return;
  el.innerHTML =
    `Campaign: <strong>${esc(cwData.name || '')}</strong><br>
     Goal: ${esc(cwData.goal || '')}<br>
     Channel: LinkedIn (connect + message)`;
}

let _builderLaunching = false;
async function launchCampaign() {
  if (_builderLaunching) return;
  if (!activeCampaignId) return;
  // Block launch while generation is running
  if (_activeJobId) {
    toast('Cannot launch while sequences are still generating. Wait for generation to complete.', 'error');
    return;
  }
  const btn = document.querySelector('button[onclick="launchCampaign()"]');
  _builderLaunching = true;
  if (btn) { btn.disabled = true; btn.textContent = 'Launching…'; }
  try {
    await api(`/campaigns/${activeCampaignId}`, {
      method: 'PATCH',
      body: JSON.stringify({ sending_limit_per_day: 20 }),
    });
    const result = await api(`/campaigns/${activeCampaignId}/launch`, { method: 'POST' });
    toast(`Campaign launched! ${result.leads_enrolled} leads enrolled.`);
    await loadWorkflowCampaigns();
    // Move to wizard step 7 (live dashboard)
    cwCampaignId = activeCampaignId;
    cwGoStep(7);
    await _cwLoadDashboard(activeCampaignId);
  } catch(e) { toast('Launch failed: ' + e.message, 'error'); }
  finally { _builderLaunching = false; if (btn) { btn.disabled = false; btn.textContent = 'Launch Campaign'; } }
}

async function pauseCampaign(id) {
  try {
    const r = await api(`/campaigns/${id}/pause`, { method: 'POST' });
    if (r && r.warning) toast('Paused. ' + r.warning, 'warning');
    else toast('Campaign paused.');
  } catch(e) { toast('Error: ' + e.message, 'error'); }
}

async function resumeCampaign(id) {
  try { await api(`/campaigns/${id}/resume`, { method: 'POST' }); toast('Campaign resumed.'); } catch(e) { toast('Error: ' + e.message, 'error'); }
}

async function cancelCampaign(id) {
  if (!await customConfirm(
    'Hard-cancel this campaign?\n\n' +
    '• LinkedIn outreach will be stopped for all enrolled leads.\n' +
    '• The campaign cannot be resumed  -  you would need to create a new one.\n\n' +
    'Use Pause instead if you only want to stop sending temporarily.',
    {destructive: true}
  )) return;
  try {
    const r = await api(`/campaigns/${id}/cancel`, { method: 'POST' });
    toast('Campaign cancelled.', 'info');
    if (r.warning) toast(r.warning, 'warning');
  } catch(e) { toast('Cancel failed: ' + e.message, 'error'); }
}

function wfCancel() {
  if (wfCampaignId) cancelCampaign(wfCampaignId).then(() => { loadWfSequences(wfCampaignId); loadWorkflowCampaigns(); });
}

// ── Sequence templates ───────────────────────────────────────────────────────
async function wfSaveTemplate() {
  if (!wfCampaignId) return;
  const name = await customPrompt('Template name (e.g. "GCC HSE 6-step v2"):', '');
  if (!name || !name.trim()) return;
  const share = await customConfirm('Share this template with the rest of the team?\n\nOK = shared (others can see and apply it).\nCancel = private (only you can use it).');
  try {
    const r = await api('/sequence-templates', {
      method: 'POST',
      body: JSON.stringify({
        name: name.trim(),
        source_campaign_id: wfCampaignId,
        is_shared: share,
      }),
    });
    toast(`Template saved (${r.step_count} step${r.step_count!==1?'s':''}).`, 'success');
  } catch(e) {
    toast('Save failed: ' + e.message, 'error');
  }
}

async function wfUseTemplate() {
  if (!wfCampaignId) return;
  let templates;
  try {
    templates = await api('/sequence-templates');
  } catch(e) {
    toast('Could not load templates: ' + e.message, 'error');
    return;
  }
  if (!templates || !templates.length) {
    toast('No templates yet. Save a sequence as a template first.', 'info');
    return;
  }
  const lines = templates.map((t, i) =>
    `${i + 1}. ${t.name}  -  ${t.step_count} step${t.step_count!==1?'s':''}${t.is_shared && !t.is_owner ? ' (shared)' : ''}${t.is_owner ? '' : ''}`
  ).join('\n');
  const pick = await customPrompt(`Pick a template by number to apply to this campaign:\n\n${lines}\n\nThis replaces every existing draft for every prospect. Continue?`, '1');
  if (!pick) return;
  const idx = parseInt(pick, 10) - 1;
  if (isNaN(idx) || idx < 0 || idx >= templates.length) {
    toast('Invalid selection.', 'error');
    return;
  }
  const t = templates[idx];
  try {
    const r = await api(`/sequence-templates/${t.id}/apply/${wfCampaignId}`, { method: 'POST' });
    toast(`Applied "${r.template_name}" to ${r.applied} prospect${r.applied!==1?'s':''}.`, 'success');
    loadWfSequences(wfCampaignId);
  } catch(e) {
    toast('Apply failed: ' + e.message, 'error');
  }
}

// ── Analytics ──────────────────────────────────────────────────────────────────
let _analyticsCampaigns = [];
let _analyticsSelectedCampaignId = '';

function renderAnalyticsCampaignOptions(campaigns) {
  const sel = document.getElementById('analytics-campaign-select');
  if (!sel) return;
  const prev = _analyticsSelectedCampaignId || sel.value || '';
  sel.innerHTML = '<option value=""> -  Select a campaign  - </option>' + campaigns.map(c =>
    `<option value="${esc(c.id)}" ${c.id===prev?'selected':''}>${esc(c.name)}</option>`
  ).join('');
  // If previous value no longer valid, clear it
  if (prev && !campaigns.some(c => c.id === prev)) {
    sel.value = '';
    _analyticsSelectedCampaignId = '';
  } else {
    _analyticsSelectedCampaignId = sel.value;
  }
}

function onAnalyticsCampaignChange() {
  const sel = document.getElementById('analytics-campaign-select');
  _analyticsSelectedCampaignId = sel ? sel.value : '';
  renderAnalyticsForSelection();
}

function renderAnalyticsForSelection() {
  const loading = document.getElementById('analytics-loading');
  const empty = document.getElementById('analytics-empty');
  const content = document.getElementById('analytics-content');
  if (loading) loading.classList.add('hidden');
  if (!_analyticsSelectedCampaignId) {
    if (content) content.classList.add('hidden');
    if (empty) empty.classList.remove('hidden');
    return;
  }
  if (empty) empty.classList.add('hidden');
  if (content) content.classList.remove('hidden');

  const campaign = _analyticsCampaigns.find(c => c.id === _analyticsSelectedCampaignId);
  if (campaign) renderKPICards(campaign);

  // Hide email-specific panels for LinkedIn-only campaigns
  const isLiOnly = campaign && !campaign.emails_sent && (campaign.li_connect_sent > 0 || campaign.li_message_sent > 0 || campaign.total > 0);
  ['ab-test-panel', 'insights-panel'].forEach(id => {
    const p = document.getElementById(id);
    if (p) p.style.display = isLiOnly ? 'none' : '';
  });
  // Hide subject line + reply time panels (email-only)
  document.querySelectorAll('#sh-subjects, #sh-reply-time, #sh-seq-dropoff').forEach(el => {
    const panel = el.closest('.panel');
    if (panel) panel.style.display = isLiOnly ? 'none' : '';
  });

  // Reset cost panel for re-load
  const cl = document.getElementById('cost-loading');
  if (cl) { cl.textContent = 'Loading cost data…'; cl.style.color = ''; cl.classList.remove('hidden'); }
  const cc = document.getElementById('cost-content');
  if (cc) cc.classList.add('hidden');

  loadCostBreakdown();
  loadAbTestResults(campaign ? [campaign] : []);
  loadSequenceDropoff();
  loadCostPerMeeting();
  loadChannelAttribution();
  // Insights are fetched per campaign via query param
  api(`/analytics/insights?campaign_id=${encodeURIComponent(_analyticsSelectedCampaignId)}`)
    .catch(() => api('/analytics/insights').catch(() => null))
    .then(insights => {
      if (!insights) return;
      renderFunnelViz(insights.funnel);
      renderSubjectLeaderboard(insights.subject_line_performance);
      renderTimeToReply(insights.time_to_first_reply);
    });
}

async function loadAnalytics() {
  const loading = document.getElementById('analytics-loading');
  const empty = document.getElementById('analytics-empty');
  const content = document.getElementById('analytics-content');
  if (loading) loading.classList.remove('hidden');
  if (empty) empty.classList.add('hidden');
  if (content) content.classList.add('hidden');
  try {
    const data = await api('/analytics/overview');
    _analyticsCampaigns = data.campaigns || [];
    // Default to campaign currently selected in the Campaigns tab, if any
    if (!_analyticsSelectedCampaignId) {
      const wfSel = document.getElementById('wf-campaign-select');
      const wfVal = wfSel && wfSel.value && wfSel.value !== '__new__' ? wfSel.value : '';
      if (wfVal && _analyticsCampaigns.some(c => c.id === wfVal)) {
        _analyticsSelectedCampaignId = wfVal;
      }
    }
    renderAnalyticsCampaignOptions(_analyticsCampaigns);
    const anMeta = document.getElementById('analytics-meta');
    if (anMeta) anMeta.textContent = `${_analyticsCampaigns.length} campaign${_analyticsCampaigns.length===1?'':'s'}`;
    renderAnalyticsForSelection();
  } catch(e) {
    if (loading) loading.innerHTML = `<div style="color:var(--red)">Failed: ${esc(e.message)}</div>`;
  }
}

function renderKPICards(stats) {
  const fmt = v => v == null ? ' - ' : Number(v).toLocaleString();
  const pct = v => v == null ? ' - ' : v + '%';
  const s = stats || {};

  // Detect if this is a LinkedIn-only campaign (no emails sent, has LinkedIn data)
  const isLinkedInOnly = !s.emails_sent && (s.li_connect_sent > 0 || s.li_message_sent > 0 || s.total > 0);

  let cards;
  if (isLinkedInOnly) {
    const acceptRate = s.li_connect_sent > 0 ? Math.round((s.li_connected || 0) / s.li_connect_sent * 100) : null;
    cards = [
      { label:'Leads',            value:fmt(s.total),              color:'var(--navy)',   icon:'users' },
      { label:'Connects Sent',    value:fmt(s.li_connect_sent),    color:'var(--blue)',   icon:'user-plus' },
      { label:'Connected',        value:fmt(s.li_connected) + (acceptRate != null ? ` (${acceptRate}%)` : ''), color:'#0a66c2', icon:'check-circle' },
      { label:'Messages Sent',    value:fmt(s.li_message_sent),    color:'var(--accent)', icon:'message-circle' },
      { label:'Replied',          value:fmt(s.replied),            color:'var(--green)',  icon:'thumbs-up' },
      { label:'Meetings Booked',  value:fmt(s.meetings_booked),    color:'var(--accent)', icon:'calendar' },
    ];
  } else {
    cards = [
      { label:'Emails Sent',     value:fmt(s.emails_sent),          color:'var(--blue)',   icon:'mail' },
      { label:'Delivery Rate',   value:pct(s.delivery_rate),        color:'var(--green)',  icon:'check-circle' },
      { label:'Open Rate',       value:pct(s.open_rate),            color:'var(--accent)', icon:'eye' },
      { label:'Reply Rate',      value:pct(s.reply_rate),           color:'var(--yellow)', icon:'message-circle' },
      { label:'Positive Replies',value:pct(s.positive_reply_rate),  color:'var(--green)',  icon:'thumbs-up' },
      { label:'Meetings Booked', value:fmt(s.meetings_booked),      color:'var(--accent)', icon:'calendar' },
    ];
  }
  const el = document.getElementById('kpi-grid');
  if (!el) return;
  el.innerHTML = cards.map(c =>
    `<div class="card" style="text-align:center;">
      <div style="margin-bottom:6px;color:${c.color};line-height:1;">${icon(c.icon, 22)}</div>
      <div style="font-size:22px;font-weight:700;color:${c.color};">${c.value}</div>
      <div class="card-label" style="margin-top:4px;">${c.label}</div>
    </div>`
  ).join('');
}

async function loadAbTestResults(campaigns) {
  const el = document.getElementById('ab-test-content');
  if (!el) return;
  // Find campaigns with A/B testing enabled
  const abCampaigns = campaigns.filter(c => c.ab_test_enabled);
  if (!abCampaigns.length) {
    el.innerHTML = '<div style="text-align:center;padding:16px;color:var(--text-muted);font-size:13px;">No campaigns with A/B testing enabled. Enable it in the campaign builder (Step 2) to compare subject line variants.</div>';
    return;
  }
  try {
    const results = await Promise.all(
      abCampaigns.map(c => api(`/analytics/ab-test/${c.id}`).catch(() => null))
    );
    const validResults = results.filter(r => r && r.total_sequences > 0);
    if (!validResults.length) {
      el.innerHTML = '<div style="text-align:center;padding:16px;color:var(--text-muted);font-size:13px;">A/B tests are set up but no sequences generated yet. Generate sequences to see results here.</div>';
      return;
    }
    el.innerHTML = validResults.map(r => {
      const aRate = r.variant_a.open_rate != null ? r.variant_a.open_rate + '%' : '--';
      const bRate = r.variant_b.open_rate != null ? r.variant_b.open_rate + '%' : '--';
      const winnerLabel = r.winner === 'A' ? '<span style="color:var(--green);font-weight:600;">A wins</span>'
        : r.winner === 'B' ? '<span style="color:var(--accent);font-weight:600;">B wins</span>'
        : r.winner === 'tie' ? '<span style="color:var(--text-muted);">Tie</span>'
        : '<span style="color:var(--text-muted);">Not enough data</span>';
      const aSample = (r.variant_a.sample_subjects || []).slice(0, 2).map(s => esc(s)).join(', ') || '--';
      const bSample = (r.variant_b.sample_subjects || []).slice(0, 2).map(s => esc(s)).join(', ') || '--';
      return `<div style="border:1px solid var(--border);border-radius:var(--r-sm);padding:14px;margin-bottom:10px;">
        <div style="font-size:13px;font-weight:600;color:var(--navy);margin-bottom:10px;">${esc(r.campaign_name)} <span style="font-size:11px;font-weight:400;color:var(--text-muted);">(${r.total_sequences} sequences)</span></div>
        <div style="display:grid;grid-template-columns:1fr 1fr 120px;gap:12px;align-items:start;">
          <div style="padding:10px;background:var(--surface);border-radius:var(--r-sm);">
            <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.05em;color:var(--text-muted);margin-bottom:6px;">Variant A (Original)</div>
            <div style="font-size:20px;font-weight:700;color:var(--navy);">${aRate}</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:2px;">open rate (${r.variant_a.sent} sent, ${r.variant_a.opened} opened)</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:6px;font-style:italic;" title="${aSample}">${aSample.length > 60 ? aSample.substring(0,60) + '...' : aSample}</div>
          </div>
          <div style="padding:10px;background:var(--surface);border-radius:var(--r-sm);">
            <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.05em;color:var(--text-muted);margin-bottom:6px;">Variant B (Alternative)</div>
            <div style="font-size:20px;font-weight:700;color:var(--accent);">${bRate}</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:2px;">open rate (${r.variant_b.sent} sent, ${r.variant_b.opened} opened)</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:6px;font-style:italic;" title="${bSample}">${bSample.length > 60 ? bSample.substring(0,60) + '...' : bSample}</div>
          </div>
          <div style="text-align:center;padding:10px;">
            <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.05em;color:var(--text-muted);margin-bottom:6px;">Winner</div>
            <div style="font-size:14px;">${winnerLabel}</div>
          </div>
        </div>
      </div>`;
    }).join('');
  } catch(e) {
    el.innerHTML = `<div style="color:var(--red);padding:12px;">Failed to load A/B test results: ${esc(e.message)}</div>`;
  }
}

function openAnalyticsEdit(campaignId, pos, mtg) {
  analyticsEditCampaignId = campaignId;
  document.getElementById('edit-positive-replies').value = pos;
  document.getElementById('edit-meetings-booked').value = mtg;
  document.getElementById('analytics-edit-modal').classList.remove('hidden');
  document.getElementById('analytics-edit-modal').style.display = 'flex';
}

function closeAnalyticsEdit() {
  const m = document.getElementById('analytics-edit-modal');
  m.classList.add('hidden');
  m.style.display = '';
  analyticsEditCampaignId = null;
}

async function saveAnalyticsEdit() {
  if (!analyticsEditCampaignId) return;
  try {
    await api(`/analytics/campaigns/${analyticsEditCampaignId}`, {
      method: 'PATCH',
      body: JSON.stringify({
        positive_replies: parseInt(document.getElementById('edit-positive-replies').value)||0,
        meetings_booked: parseInt(document.getElementById('edit-meetings-booked').value)||0,
      }),
    });
    closeAnalyticsEdit();
    loadAnalytics();
    toast('Metrics updated');
  } catch(e) { toast('Save failed: ' + e.message, 'error'); }
}

// ── Enhanced Analytics Renderers ────────────────────────────────────────────

function renderFunnelViz(funnelData) {
  const el = document.getElementById('funnel-content');
  const loading = document.getElementById('funnel-loading');
  if (!el) return;
  if (!funnelData || funnelData.length === 0) {
    if (loading) loading.textContent = 'No funnel data yet.';
    el.classList.add('hidden');
    return;
  }
  if (loading) loading.style.display = 'none';
  el.classList.remove('hidden');

  const STAGE_LABELS = {
    total_prospects: 'Prospects',
    generated: 'Generated',
    approved: 'Approved',
    enrolled: 'Enrolled',
    sent: 'Sent',
    opened: 'Opened',
    replied: 'Replied',
    positive_reply: 'Positive',
    meeting_booked: 'Meetings',
  };
  const STAGE_COLORS = [
    'var(--blue)', '#5b9bd5', '#70ad47', '#4472c4', 'var(--accent)',
    '#ed7d31', '#ffc000', 'var(--green)', '#7030a0'
  ];

  let html = '';
  for (const campaign of funnelData) {
    const stages = campaign.stages || [];
    const maxCount = Math.max(...stages.map(s => s.count), 1);
    html += `<div style="margin-bottom:20px;">
      <div style="font-size:13px;font-weight:600;margin-bottom:10px;">${esc(campaign.campaign_name)}</div>
      <div style="display:flex;align-items:flex-end;gap:4px;height:120px;">`;
    for (let i = 0; i < stages.length; i++) {
      const s = stages[i];
      const pct = Math.max((s.count / maxCount) * 100, 4);
      const label = STAGE_LABELS[s.stage] || s.stage;
      const conv = s.conversion_from_prev != null ? s.conversion_from_prev + '%' : '';
      html += `<div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;">
        <div style="font-size:11px;font-weight:700;margin-bottom:2px;">${s.count}</div>
        <div style="width:100%;background:${STAGE_COLORS[i % STAGE_COLORS.length]};border-radius:4px 4px 0 0;height:${pct}%;min-height:4px;transition:height 0.3s;"></div>
        <div style="font-size:9px;color:var(--text-muted);margin-top:4px;text-align:center;line-height:1.2;">${label}</div>
        ${conv ? `<div style="font-size:9px;color:var(--text-dim);">${conv}</div>` : ''}
      </div>`;
    }
    html += `</div></div>`;
  }
  el.innerHTML = html;
}

function renderSubjectLeaderboard(subjectData) {
  const el = document.getElementById('subjects-content');
  if (!el) return;
  const top = (subjectData && subjectData.top) || [];
  const bottom = (subjectData && subjectData.bottom) || [];

  if (top.length === 0 && bottom.length === 0) {
    el.innerHTML = '<div style="color:var(--text-muted);font-size:13px;padding:8px 0;">No subject line data yet.</div>';
    return;
  }

  function buildTable(rows, title) {
    if (!rows.length) return '';
    let h = `<div style="font-size:12px;font-weight:600;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.05em;margin:12px 0 6px;">${title}</div>`;
    h += `<div style="overflow-x:auto;"><table class="analytics-table" style="width:100%;">
      <thead><tr><th>Subject Line</th><th>Campaign</th><th style="text-align:right;">Sent</th><th style="text-align:right;">Opens</th><th style="text-align:right;">Open Rate</th></tr></thead><tbody>`;
    for (const r of rows) {
      h += `<tr>
        <td style="font-weight:500;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(r.subject || '')}</td>
        <td style="font-size:11px;color:var(--text-muted);">${esc(r.campaign_name || '')}</td>
        <td style="text-align:right;">${r.sent}</td>
        <td style="text-align:right;">${r.open_count}</td>
        <td style="text-align:right;font-weight:600;color:${r.open_rate >= 30 ? 'var(--green)' : r.open_rate >= 15 ? 'var(--accent)' : 'var(--red)'};">${r.open_rate}%</td>
      </tr>`;
    }
    h += '</tbody></table></div>';
    return h;
  }

  el.innerHTML = buildTable(top, 'Top Performing') + buildTable(bottom, 'Lowest Performing');
}

function renderTimeToReply() {} // stub — removed (Instantly-era)

// ── API Quotas ─────────────────────────────────────────────────────────────────

async function loadApiQuotas(forceRefresh = false) {
  const loading = document.getElementById('api-quotas-loading');
  const grid    = document.getElementById('api-quotas-grid');
  const note    = document.getElementById('quotas-cache-note');
  const btn     = document.getElementById('quotas-refresh-btn');

  if (loading) { loading.style.display = 'block'; loading.textContent = 'Loading quotas\u2026'; }
  if (grid)    { grid.classList.add('hidden'); grid.style.display = 'none'; }
  if (btn)     btn.disabled = true;

  try {
    const data = await api('/limits/' + (forceRefresh ? '?refresh=1' : ''));

    if (loading) loading.style.display = 'none';
    if (grid)    { grid.classList.remove('hidden'); grid.style.display = 'grid'; }

    if (note) {
      const age = data.cache_age_s || 0;
      note.textContent = data.cached && age > 5
        ? `Cached ${age}s ago. Click Refresh for latest.`
        : 'Live data.';
    }

    grid.innerHTML = (data.services || []).map(svc => _renderServiceCard(svc)).join('');
  } catch(e) {
    if (loading) {
      loading.style.display = 'block';
      loading.innerHTML = `<span style="color:var(--red)">Failed to load quotas: ${esc(e.message)}</span>`;
    }
    if (grid) { grid.classList.add('hidden'); grid.style.display = 'none'; }
  } finally {
    if (btn) btn.disabled = false;
  }
}

function _renderServiceCard(svc) {
  const dotColor = svc.connected ? 'var(--green)' : 'var(--red)';
  const dotTitle = svc.connected ? 'Connected' : (svc.error || 'Not connected');

  let body = '';
  if (!svc.connected) {
    body = `<div style="font-size:12px;color:var(--text-muted);padding:4px 0;">${esc(svc.error || 'Not connected')}</div>`;
  } else if (!svc.quotas || !svc.quotas.length) {
    body = `<div style="font-size:12px;color:var(--text-muted);padding:4px 0;">No quota data available.</div>`;
  } else {
    body = svc.quotas.map(q => _renderQuotaRow(q)).join('');
  }

  return `<div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--r-sm);padding:14px 16px;">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:11px;border-bottom:1px solid var(--border);padding-bottom:8px;">
      <span style="width:8px;height:8px;border-radius:50%;background:${dotColor};flex-shrink:0;" title="${esc(dotTitle)}"></span>
      <span style="font-size:13px;font-weight:600;color:var(--navy);">${esc(svc.name)}</span>
    </div>
    ${body}
  </div>`;
}

function _renderQuotaRow(q) {
  const hasBar = q.total != null && q.total > 0;
  const pct = hasBar ? Math.max(0, Math.min(100, Math.round((q.remaining / q.total) * 100))) : null;

  // Bar colour: green >50%, amber 20-50%, red <20%
  let barColor = 'var(--green)';
  if (pct !== null && pct < 20) barColor = 'var(--red)';
  else if (pct !== null && pct < 50) barColor = 'var(--yellow)';

  const remText   = q.remaining != null ? Number(q.remaining).toLocaleString() : '\u2013';
  const totalText = q.total     != null ? Number(q.total).toLocaleString()     : '';
  const countText = totalText ? `${remText}&thinsp;/&thinsp;${totalText}` : remText;
  const tooltip   = q.tooltip ? ` title="${esc(q.tooltip)}"` : '';

  return `<div style="margin-bottom:9px;cursor:default;"${tooltip}>
    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:3px;">
      <span style="font-size:11px;color:var(--text-muted);">${esc(q.metric)}</span>
      <span style="font-size:11px;font-weight:600;color:var(--text);">${countText}
        <span style="font-weight:400;color:var(--text-muted);font-size:10px;"> ${esc(q.period)}</span>
      </span>
    </div>
    ${hasBar
      ? `<div style="height:3px;background:var(--surface2);border-radius:var(--r-pill);overflow:hidden;">
           <div style="height:100%;width:${pct}%;background:${barColor};border-radius:var(--r-pill);transition:width 0.5s;"></div>
         </div>`
      : ''}
  </div>`;
}

// ── Cost tracking ─────────────────────────────────────────────────────────────

async function loadCostSettings() {
  try {
    const d = await api('/settings/costs');
    document.getElementById('cost-apollo').value = d.apollo_monthly || '';
    document.getElementById('cost-phantombuster').value = d.phantombuster_monthly || '';
    document.getElementById('cost-reacher').value = d.reacher_monthly || '';
    document.getElementById('cost-hunter').value = d.hunter_monthly || '';
    // Proxycurl removed (shut down 2025)
    document.getElementById('cost-tavily').value = d.tavily_monthly || '';
  } catch(e) { console.error('loadCostSettings:', e); }
}

async function saveCostSettings() {
  const status = document.getElementById('cost-save-status');
  try {
    await api('/settings/costs', {
      method: 'POST',
      body: JSON.stringify({
        apollo_monthly: parseFloat(document.getElementById('cost-apollo').value) || 0,
        phantombuster_monthly: parseFloat(document.getElementById('cost-phantombuster').value) || 0,
        reacher_monthly: parseFloat(document.getElementById('cost-reacher').value) || 0,
        hunter_monthly: parseFloat(document.getElementById('cost-hunter').value) || 0,
        // proxycurl_monthly removed
        tavily_monthly: parseFloat(document.getElementById('cost-tavily').value) || 0,
      }),
    });
    status.style.color = 'var(--green)';
    status.textContent = '✓ Saved';
    setTimeout(() => status.textContent = '', 3000);
  } catch(e) {
    status.style.color = 'var(--red)';
    status.textContent = 'Save failed: ' + e.message;
  }
}

// ── Project search toggle ────────────────────────────────────────────────────
async function loadProjectSearchSetting() {
  try {
    const data = await api('/settings/project-search');
    const cb = document.getElementById('project-search-toggle');
    const st = document.getElementById('project-search-status');
    if (cb) cb.checked = data.enabled;
    if (st) { st.textContent = data.enabled ? 'ON' : 'OFF'; st.style.color = data.enabled ? 'var(--green)' : 'var(--text-muted)'; }
  } catch (_) {}
}

async function toggleProjectSearch() {
  const cb = document.getElementById('project-search-toggle');
  const st = document.getElementById('project-search-status');
  const enabled = cb?.checked || false;
  try {
    await api('/settings/project-search', { method: 'POST', body: JSON.stringify({ instructions: enabled ? 'true' : 'false' }) });
    if (st) { st.textContent = enabled ? 'ON' : 'OFF'; st.style.color = enabled ? 'var(--green)' : 'var(--text-muted)'; }
    toast(`Project search ${enabled ? 'enabled' : 'disabled'}.`);
  } catch (e) {
    if (cb) cb.checked = !enabled;
    toast('Failed: ' + e.message, 'error');
  }
}

function initWebhookUrl() {
  // Instantly webhooks removed  -  no longer used.
}

function registerWebhook() {
  // Instantly webhooks removed  -  no longer used.
}

// ══════════════════════════════════════════════════════════════════════════════
// SETUP WIZARD  -  Service Connections + Sequence Designer
// ══════════════════════════════════════════════════════════════════════════════

const SVC_META = {
  anthropic:  { name: 'Claude AI',     color: '#fdcb6e', desc: 'AI engine that writes personalised email sequences for each prospect using your style and case studies.', required: true },
  reacher:    { name: 'Reacher',       color: '#00b894', desc: 'Self-hosted email validation. Checks every address before sending to protect your domain reputation and reduce bounces. Unlimited  -  no per-email cost.', required: false },
  apollo:     { name: 'Apollo.io',     color: '#6c5ce7', desc: 'Lead database. Search 275M+ contacts by title, industry, company size and location. Reveals verified emails.', required: false },
  [PROJECT_SEARCH_PROVIDER.key]: { name: PROJECT_SEARCH_PROVIDER.displayName, color: PROJECT_SEARCH_PROVIDER.color, desc: PROJECT_SEARCH_PROVIDER.desc, required: false, keyless: true },
  // proxycurl removed (shut down 2025) - enrichment via PhantomBuster scraper
  hunter:     { name: 'Hunter.io',     color: '#e17055', desc: 'Email finder and domain pattern detection. Recovers invalid emails by inferring the correct format.', required: false },
  outlook:    { name: 'Outlook',       color: '#00cec9', desc: 'Microsoft Graph integration. Syncs your sent emails to extract writing style for AI-generated content.', required: false },
};

async function testAllConnections() {
  const grid = document.getElementById('svc-grid');
  const btn = document.getElementById('test-all-btn');
  btn.disabled = true; btn.textContent = 'Testing...';

  // Show loading state
  grid.innerHTML = Object.entries(SVC_META).map(([key, m]) => `
    <div id="svc-card-${key}" style="padding:14px 16px;border:1px solid var(--border);background:var(--bg);display:flex;gap:12px;align-items:flex-start;">
      <div style="width:10px;height:10px;border-radius:50%;background:var(--border-mid);flex-shrink:0;margin-top:4px;" id="svc-dot-${key}"></div>
      <div style="flex:1;min-width:0;">
        <div style="display:flex;align-items:center;gap:6px;">
          <span style="font-size:13px;font-weight:700;color:${m.color};">${m.name}</span>
          ${m.required ? '<span style="font-size:9px;font-weight:700;color:var(--red);text-transform:uppercase;">Required</span>' : '<span style="font-size:9px;color:var(--text-muted);text-transform:uppercase;">Optional</span>'}
        </div>
        <div style="font-size:11px;color:var(--text-muted);margin-top:3px;line-height:1.45;">${m.desc}</div>
        <div style="font-size:11px;margin-top:6px;color:var(--text-muted);" id="svc-status-${key}">Testing...</div>
      </div>
    </div>
  `).join('');

  try {
    const data = await api('/settings/connections');
    // Keyless services (e.g. DuckDuckGo scraper) are not in the connections
    // response  -  render them as "No API key required" up front.
    for (const [key, meta] of Object.entries(SVC_META)) {
      if (!meta.keyless) continue;
      const dot = document.getElementById('svc-dot-' + key);
      const status = document.getElementById('svc-status-' + key);
      const card = document.getElementById('svc-card-' + key);
      if (!dot || !status) continue;
      dot.style.background = 'var(--green)';
      status.innerHTML = '<span style="color:var(--green);font-weight:600;">Ready</span> <span style="color:var(--text-muted);"> -  no API key required</span>';
      if (card) card.style.borderColor = 'var(--green)';
    }
    for (const [key, info] of Object.entries(data)) {
      const dot = document.getElementById('svc-dot-' + key);
      const status = document.getElementById('svc-status-' + key);
      const card = document.getElementById('svc-card-' + key);
      if (!dot || !status) continue;

      if (info.connected) {
        dot.style.background = 'var(--green)';
        let extra = '';
        if (info.credits !== null && info.credits !== undefined) extra = ` | ${info.credits} credits`;
        status.innerHTML = '<span style="color:var(--green);font-weight:600;">Connected</span>' + extra;
        card.style.borderColor = 'var(--green)';
      } else if (info.configured) {
        dot.style.background = 'var(--gold)';
        status.innerHTML = '<span style="color:var(--gold);font-weight:600;">Key set but connection failed</span>' +
          (info.error ? ` <span style="color:var(--text-muted);">(${esc(info.error)})</span>` : '');
        card.style.borderColor = 'var(--gold)';
      } else {
        dot.style.background = 'var(--border-mid)';
        status.innerHTML = '<span style="color:var(--text-muted);">Not configured</span>  -  add API key to <code>.env</code>';
      }
    }
  } catch(e) {
    grid.innerHTML = '<div style="color:var(--red);padding:20px;">Failed to test connections: ' + esc(e.message) + '</div>';
  }

  btn.disabled = false; btn.textContent = 'Test All Connections';
}

// ── Sequence Designer ──────────────────────────────────────────────────────

let designSteps = [];
let _dragIdx = null; // drag-and-drop state for sequence designer

// EMAIL_STYLE_OPTIONS removed — LinkedIn-only flow

// updateEmailStyle removed — LinkedIn-only flow

// All icons are monochrome line-icons rendered via icon(name)  -  they inherit
// the surrounding element's color (set via CSS), so no more colored glyphs.
const STEP_TYPE_META = {
  send_linkedin_connect:  { icon: 'users',           color: '#0a66c2', label: 'LI Connect' },
  send_linkedin_message:  { icon: 'message-circle',  color: '#0a66c2', label: 'LI Message' },
  create_manual_task:     { icon: 'edit',            color: '#ca8a04', label: 'Manual Task' },
  wait:                   { icon: 'clock',           color: '#6c757d', label: 'Wait' },
  send_call_task:         { icon: 'phone',           color: '#059669', label: 'Call Task' },
  send_whatsapp:          { icon: 'message-circle',  color: '#25D366', label: 'WhatsApp' },
  end_sequence:           { icon: 'square',          color: '#636e72', label: 'End' },
};

function addDesignStep(stepType, dayOffset, dynamicOpts) {
  // Auto-calculate day offset
  const lastDay = designSteps.length > 0 ? Math.max(...designSteps.map(s => s.day_offset)) : 0;
  const defaultGaps = { send_linkedin_connect: 3, send_linkedin_message: 4 };
  const gap = defaultGaps[stepType] || 3;
  const day = dayOffset || (lastDay + gap) || 1;

  const step = {
    step_type: stepType,
    day_offset: day,
    template_id: null,
    label: STEP_TYPE_META[stepType]?.label || stepType,
  };

  // Dynamic timing fields
  if (dynamicOpts) {
    step.trigger_event = dynamicOpts.trigger_event || null;
    step.delay_after_trigger_hours = dynamicOpts.delay_after_trigger_hours || null;
    step.followup_index = dynamicOpts.followup_index != null ? dynamicOpts.followup_index : null;
    step.followup_delay_days = dynamicOpts.followup_delay_days || null;
    if (step.trigger_event && step.followup_index == null) {
      step.followup_index = 0; // first message = index 0
      step.label = 'LI Msg (after connect, ' + (step.delay_after_trigger_hours || 48) + 'h)';
    } else if (step.followup_index >= 1) {
      step.label = 'Follow-up #' + step.followup_index + ' (+' + (step.followup_delay_days || 7) + 'd)';
    }
  }

  designSteps.push(step);
  designSteps.sort((a, b) => a.day_offset - b.day_offset);
  renderDesignSteps();
}

function addDynamicFollowup() {
  // Count existing dynamic steps to determine followup_index
  const dynamicSteps = designSteps.filter(s => s.trigger_event === 'linkedin_connected');
  if (!dynamicSteps.length) {
    toast('Add a "LI Message (after connect)" step first before adding follow-ups.', 'error');
    return;
  }
  if (dynamicSteps.length >= 4) {
    toast('Maximum 3 follow-ups allowed.', 'error');
    return;
  }
  const nextIndex = dynamicSteps.length; // 0=first msg, 1=first followup, etc.
  const lastDay = designSteps.length > 0 ? Math.max(...designSteps.map(s => s.day_offset)) : 0;
  addDesignStep('send_linkedin_message', lastDay + 7, {
    trigger_event: 'linkedin_connected',
    followup_index: nextIndex,
    followup_delay_days: 7,
  });
}

function updateStepDynamicDelay(idx, hours) {
  const h = parseInt(hours, 10);
  if (!Number.isFinite(h) || h < 1) { toast('Minimum 1 hour.', 'error'); renderDesignSteps(); return; }
  designSteps[idx].delay_after_trigger_hours = Math.min(h, 720);
  designSteps[idx].label = 'LI Msg (after connect, ' + designSteps[idx].delay_after_trigger_hours + 'h)';
  renderDesignSteps();
}

function updateFollowupDelay(idx, days) {
  const d = parseInt(days, 10);
  if (!Number.isFinite(d) || d < 1) { toast('Minimum 1 day.', 'error'); renderDesignSteps(); return; }
  designSteps[idx].followup_delay_days = Math.min(d, 30);
  designSteps[idx].label = 'Follow-up #' + designSteps[idx].followup_index + ' (+' + designSteps[idx].followup_delay_days + 'd)';
  renderDesignSteps();
}

function removeDesignStep(idx) {
  designSteps.splice(idx, 1);
  renderDesignSteps();
}

function updateStepDay(idx, newDay) {
  // FIX-24: clamp day to [0, 90]; reject NaN
  const parsed = parseInt(newDay, 10);
  if (!Number.isFinite(parsed) || Number.isNaN(parsed)) {
    toast('Day must be a number between 0 and 90.', 'error');
    renderDesignSteps();
    return;
  }
  const clamped = Math.max(0, Math.min(90, parsed));
  if (clamped !== parsed) toast(`Day clamped to ${clamped} (range 0-90).`, 'warning');
  designSteps[idx].day_offset = clamped;
  renderDesignSteps();
}

function _onDesignDragStart(e) {
  _dragIdx = +e.currentTarget.dataset.idx;
  e.currentTarget.style.opacity = '0.4';
  e.dataTransfer.effectAllowed = 'move';
}
function _onDesignDragEnd(e) {
  e.currentTarget.style.opacity = '1';
  // Clear all drag-over highlights
  document.querySelectorAll('#seq-design-steps > [data-idx]').forEach(el => {
    el.style.borderTop = ''; el.style.borderBottom = '';
  });
}
function _onDesignDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  const row = e.currentTarget;
  const rect = row.getBoundingClientRect();
  const midY = rect.top + rect.height / 2;
  // Clear previous highlights on all rows
  document.querySelectorAll('#seq-design-steps > [data-idx]').forEach(el => {
    el.style.borderTop = ''; el.style.borderBottom = '';
  });
  if (e.clientY < midY) {
    row.style.borderTop = '2px solid var(--accent)';
  } else {
    row.style.borderBottom = '2px solid var(--accent)';
  }
}
function _onDesignDrop(e) {
  e.preventDefault();
  const toIdx = +e.currentTarget.dataset.idx;
  const rect = e.currentTarget.getBoundingClientRect();
  const midY = rect.top + rect.height / 2;
  let insertIdx = e.clientY < midY ? toIdx : toIdx + 1;
  if (_dragIdx === null || _dragIdx === insertIdx || _dragIdx + 1 === insertIdx) {
    _dragIdx = null;
    document.querySelectorAll('#seq-design-steps > [data-idx]').forEach(el => {
      el.style.borderTop = ''; el.style.borderBottom = '';
    });
    return;
  }
  const [moved] = designSteps.splice(_dragIdx, 1);
  if (insertIdx > _dragIdx) insertIdx--;
  designSteps.splice(insertIdx, 0, moved);
  _dragIdx = null;
  // Reassign day offsets to preserve order: first step keeps its day,
  // subsequent steps get at least +1 from the previous step
  for (let i = 1; i < designSteps.length; i++) {
    if (designSteps[i].day_offset <= designSteps[i - 1].day_offset) {
      designSteps[i].day_offset = designSteps[i - 1].day_offset + 1;
    }
  }
  renderDesignSteps();
}

function renderDesignSteps() {
  const container = document.getElementById('seq-design-steps');
  if (!designSteps.length) {
    container.innerHTML = '<div style="padding:16px;text-align:center;color:var(--text-muted);font-size:12px;border:1px dashed var(--border);">No steps yet. Click the buttons below to add steps.</div>';
    renderDesignPreview();
    return;
  }

  container.innerHTML = designSteps.map((s, i) => {
    const m = STEP_TYPE_META[s.step_type] || { icon: '?', color: '#999', label: s.step_type };
    return `<div data-idx="${i}" draggable="true" style="display:flex;align-items:center;gap:10px;padding:8px 12px;margin-bottom:4px;border:1px solid var(--border);background:var(--bg);cursor:grab;user-select:none;transition:opacity 0.15s;">
      <div style="color:var(--text-muted);font-size:14px;cursor:grab;flex-shrink:0;padding:0 2px;" title="Drag to reorder">&#9776;</div>
      <div style="width:28px;height:28px;background:${m.color || 'var(--navy)'};color:#fff;display:flex;align-items:center;justify-content:center;font-size:13px;border-radius:50%;flex-shrink:0;">${icon(m.icon, 14)}</div>
      <div style="flex:1;min-width:0;">
        <span style="font-size:13px;font-weight:600;">${esc(s.label || m.label)}</span>
        <span style="font-size:10px;color:var(--text-muted);margin-left:6px;">${esc(s.step_type)}</span>
        ${s.step_type === 'send_linkedin_connect' ? `<label style="font-size:10px;color:var(--text-muted);margin-left:10px;cursor:pointer;display:inline-flex;align-items:center;gap:3px;" title="Send connection request without a personalized note (blank invite)"><input type="checkbox" ${s.skip_connect_note ? 'checked' : ''} onchange="designSteps[${i}].skip_connect_note=this.checked;renderDesignSteps()" onclick="event.stopPropagation()" style="margin:0;" /> No note</label>` : ''}
      </div>
      <div style="display:flex;align-items:center;gap:6px;flex-shrink:0;">
        ${s.trigger_event && s.followup_index >= 1
          ? `<span style="font-size:10px;color:var(--accent);font-weight:600;">Follow-up #${s.followup_index}</span>
             <input type="number" value="${s.followup_delay_days || 7}" min="1" max="30" style="width:45px;font-size:12px;padding:3px 6px;text-align:center;" onchange="updateFollowupDelay(${i}, this.value)" onclick="event.stopPropagation()" title="Days after previous message" />
             <span style="font-size:10px;color:var(--text-muted);">days</span>`
          : s.trigger_event
          ? `<span style="font-size:10px;color:#0a66c2;font-weight:600;">After connect</span>
             <input type="number" value="${s.delay_after_trigger_hours || 48}" min="1" max="720" style="width:55px;font-size:12px;padding:3px 6px;text-align:center;" onchange="updateStepDynamicDelay(${i}, this.value)" onclick="event.stopPropagation()" title="Hours after connection accepted" />
             <span style="font-size:10px;color:var(--text-muted);">hrs</span>`
          : `<label style="font-size:10px;color:var(--text-muted);">Day</label>
             <input type="number" value="${s.day_offset}" min="1" max="90" style="width:55px;font-size:12px;padding:3px 6px;text-align:center;" onchange="updateStepDay(${i}, this.value)" onclick="event.stopPropagation()" />`
        }
        <button class="btn btn-secondary btn-sm" style="color:var(--red);padding:2px 8px;font-size:12px;" onclick="removeDesignStep(${i})" title="Remove this step from the sequence design.">&#10005;</button>
      </div>
    </div>`;
  }).join('');

  // Attach drag-and-drop listeners
  container.querySelectorAll('[data-idx]').forEach(el => {
    el.addEventListener('dragstart', _onDesignDragStart);
    el.addEventListener('dragend', _onDesignDragEnd);
    el.addEventListener('dragover', _onDesignDragOver);
    el.addEventListener('drop', _onDesignDrop);
  });

  renderDesignPreview();
}

function renderDesignPreview() {
  const el = document.getElementById('seq-design-preview');
  if (!designSteps.length) {
    el.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:20px;">Add steps above to see the timeline.</div>';
    return;
  }

  // Add virtual end_sequence step
  const steps = [...designSteps];
  const lastDay = Math.max(...steps.map(s => s.day_offset));
  const customEndDay = parseInt(document.getElementById('seq-design-end-day')?.value) || 0;
  const endDay = customEndDay > lastDay ? customEndDay : lastDay + 1;
  steps.push({ step_type: 'end_sequence', day_offset: endDay, label: 'End' });

  el.innerHTML = steps.map((s, i) => {
    const m = STEP_TYPE_META[s.step_type] || { icon: '?', color: '#636e72', label: s.step_type };
    const connector = i < steps.length - 1 ? '<div style="flex:0.5;display:flex;align-items:center;padding-top:10px;"><div style="flex:1;height:2px;background:var(--border);"></div></div>' : '';
    return `<div style="flex:1;text-align:center;min-width:70px;">
      <div style="width:32px;height:32px;margin:0 auto 6px;background:${m.color || 'var(--navy)'};color:#fff;display:flex;align-items:center;justify-content:center;border-radius:50%;">${icon(m.icon, 16)}</div>
      <div style="font-size:11px;font-weight:700;">${
        s.trigger_event
          ? (s.followup_index >= 1
              ? '+' + (s.followup_delay_days || 7) + 'd after msg'
              : '+' + (s.delay_after_trigger_hours || 48) + 'h after connect')
          : 'Day ' + s.day_offset
      }</div>
      <div style="font-size:9px;color:var(--text-muted);margin-top:1px;">${esc(s.label || m.label)}</div>
    </div>${connector}`;
  }).join('');
}

// ── Sequence library (multi-sequence list + editor, 2026-04-10) ──────────────
//
// Replaces the old single-global `/settings/sequence-config` POST with CRUD
// against /sequence-configs. Tracks which sequence (if any) is currently
// being edited via `_editingSequenceId`; "New" clears it, "Save" posts on
// null or patches on set. Delete and share-toggle happen inline in the list.

let _editingSequenceId = null;
let _sequenceConfigsCache = [];

async function loadSequencesList() {
  const el = document.getElementById('seq-list');
  if (!el) return;
  try {
    const rows = await api('/sequence-configs');
    _sequenceConfigsCache = rows || [];
    if (!_sequenceConfigsCache.length) {
      el.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:13px;">No saved sequences yet. Click "+ New Sequence" to create your first one.</div>';
      return;
    }
    el.innerHTML = _sequenceConfigsCache.map(r => {
      const owner = r.is_owner ? '' : '<span style="font-size:10px;color:var(--text-muted);margin-left:6px;padding:1px 6px;background:var(--bg);border-radius:10px;">shared</span>';
      const shared = r.is_shared ? '<span style="font-size:10px;color:var(--green);margin-left:6px;" title="Shared with the team">● shared</span>' : '';
      const actions = r.is_owner
        ? `<button class="btn btn-outline btn-sm" onclick="editSequenceConfig('${r.id}')" title="Edit.">Edit</button>
           <button class="btn btn-outline btn-sm" style="color:var(--red);border-color:var(--red);" onclick="deleteSequenceConfig('${r.id}','${esc(r.name).replace(/'/g,"\\'")}')" title="Delete this saved sequence config.">Delete</button>`
        : `<button class="btn btn-outline btn-sm" onclick="editSequenceConfig('${r.id}')" title="View this shared sequence (read-only)">View</button>`;
      const updated = r.updated_at ? new Date(r.updated_at).toLocaleDateString() : ' - ';
      return `<div style="display:flex;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid var(--border);">
        <div style="flex:1;min-width:0;">
          <div style="font-size:14px;font-weight:600;">${esc(r.name)}${owner}${shared}</div>
          <div style="font-size:11px;color:var(--text-muted);margin-top:2px;">${r.step_count} step${r.step_count === 1 ? '' : 's'} · updated ${esc(updated)}</div>
        </div>
        <div style="display:flex;gap:6px;flex-shrink:0;">${actions}</div>
      </div>`;
    }).join('');
    // Remove the bottom border from the last row for a cleaner look.
    const last = el.querySelector('div[style*="border-bottom"]:last-child');
    if (last) last.style.borderBottom = 'none';
  } catch (e) {
    el.innerHTML = '<div style="padding:16px;color:var(--red);font-size:13px;">Failed to load sequences: ' + esc(e.message) + '</div>';
  }
}

function _clearSequenceEditor() {
  _editingSequenceId = null;
  designSteps = [];
  const nameEl = document.getElementById('seq-design-name');
  if (nameEl) nameEl.value = '';
  const tz = document.getElementById('seq-design-tz');
  if (tz) tz.value = 'Asia/Riyadh';
  const th = document.getElementById('seq-design-threshold');
  if (th) th.value = 5;
  const bd = document.getElementById('seq-design-bizdays');
  if (bd) bd.checked = false;
  const sh = document.getElementById('seq-design-shared');
  if (sh) sh.checked = false;
  const status = document.getElementById('seq-design-status');
  if (status) status.textContent = '';
  renderDesignSteps();
}

function newSequenceConfig() {
  _clearSequenceEditor();
  document.getElementById('seq-editor-title').textContent = 'New sequence';
  document.getElementById('seq-editor').classList.remove('hidden');
  // Scroll the editor into view.
  document.getElementById('seq-editor').scrollIntoView({ behavior: 'smooth', block: 'start' });
  document.getElementById('seq-design-name').focus();
}

function cancelSequenceEdit() {
  _clearSequenceEditor();
  document.getElementById('seq-editor').classList.add('hidden');
}

async function editSequenceConfig(id) {
  try {
    const row = await api('/sequence-configs/' + encodeURIComponent(id));
    _editingSequenceId = row.id;
    const cfg = row.config || {};
    document.getElementById('seq-design-name').value = row.name || '';
    document.getElementById('seq-design-tz').value = cfg.timezone || 'Asia/Riyadh';
    document.getElementById('seq-design-threshold').value = cfg.scoring_threshold || 5;
    document.getElementById('seq-design-bizdays').checked = !!cfg.business_days_only;
    // Always shared with team (hidden input)

    // Load steps into the designer, skipping the terminal end_sequence.
    designSteps = [];
    let endStepDay = null;
    (cfg.steps || []).forEach(s => {
      if (s.step_type === 'end_sequence') { endStepDay = s.day_offset; return; }
      const step = {
        step_type: s.step_type,
        day_offset: s.day_offset,
        template_id: s.template_id,
        label: s.label || STEP_TYPE_META[s.step_type]?.label || s.step_type,
        skip_connect_note: !!s.skip_connect_note,
        trigger_event: s.trigger_event || null,
        delay_after_trigger_hours: s.delay_after_trigger_hours || null,
        followup_index: s.followup_index != null ? s.followup_index : null,
        followup_delay_days: s.followup_delay_days || null,
      };
      designSteps.push(step);
    });
    const endDayEl = document.getElementById('seq-design-end-day');
    if (endDayEl) endDayEl.value = endStepDay || '';
    renderDesignSteps();

    document.getElementById('seq-editor-title').textContent = row.is_owner ? ('Editing: ' + row.name) : ('Viewing: ' + row.name + ' (shared, read-only)');
    document.getElementById('seq-editor').classList.remove('hidden');
    document.getElementById('seq-editor').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) {
    toast('Failed to load sequence: ' + e.message, 'error');
  }
}

async function deleteSequenceConfig(id, name) {
  if (!await customConfirm(`Delete sequence "${name}"? This cannot be undone. (Campaigns already using it will keep running with their pinned copy.)`, {destructive: true})) return;
  try {
    await api('/sequence-configs/' + encodeURIComponent(id), { method: 'DELETE' });
    toast('Sequence deleted.');
    if (_editingSequenceId === id) cancelSequenceEdit();
    await loadSequencesList();
  } catch (e) {
    toast('Delete failed: ' + e.message, 'error');
  }
}

async function saveSequenceConfig(btn) {
  if (!designSteps.length) { toast('Add at least one step first.', 'error'); return; }

  const name = (document.getElementById('seq-design-name').value || '').trim();
  if (!name) { toast('Sequence name is required.', 'error'); return; }

  // Validate day_offset per step.
  for (let i = 0; i < designSteps.length; i++) {
    const d = Number(designSteps[i].day_offset);
    if (!Number.isFinite(d) || d < 0 || d > 90) {
      toast(`Step ${i + 1}: day must be between 0 and 90.`, 'error');
      return;
    }
  }

  const saveBtn = btn || (event && event.target) || null;
  const origBtnText = saveBtn ? saveBtn.textContent : null;
  if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Saving…'; }

  const payload = {
    name,
    timezone: document.getElementById('seq-design-tz').value || 'Asia/Riyadh',
    scoring_threshold: parseInt(document.getElementById('seq-design-threshold').value) || 5,
    business_days_only: document.getElementById('seq-design-bizdays').checked,
    is_shared: true,
    end_day_offset: parseInt(document.getElementById('seq-design-end-day')?.value) || null,
    steps: designSteps.map(s => ({
      step_type: s.step_type,
      day_offset: s.day_offset,
      template_id: s.template_id,
      label: s.label || null,
      skip_connect_note: !!s.skip_connect_note,
      trigger_event: s.trigger_event || null,
      delay_after_trigger_hours: s.delay_after_trigger_hours || null,
      followup_index: s.followup_index != null ? s.followup_index : null,
      followup_delay_days: s.followup_delay_days || null,
    })),
  };

  const statusEl = document.getElementById('seq-design-status');
  try {
    let result;
    if (_editingSequenceId) {
      result = await api('/sequence-configs/' + encodeURIComponent(_editingSequenceId), {
        method: 'PATCH',
        body: JSON.stringify(payload),
      });
    } else {
      result = await api('/sequence-configs', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      _editingSequenceId = result.id;  // subsequent clicks act as edit
    }
    if (statusEl) {
      statusEl.style.color = 'var(--green)';
      statusEl.textContent = '✓ Saved.';
      setTimeout(() => { if (statusEl) statusEl.textContent = ''; }, 4000);
    }
    _seqStepsCache = null;
    toast('Sequence saved.');
    await loadSequencesList();
    // Refresh the campaign builder dropdown if it's already populated.
    if (typeof loadCampaignSequenceDropdown === 'function') loadCampaignSequenceDropdown();
  } catch (e) {
    if (statusEl) {
      statusEl.style.color = 'var(--red)';
      statusEl.textContent = 'Save failed: ' + e.message;
    }
    toast('Save failed: ' + e.message, 'error');
  } finally {
    if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = origBtnText || 'Save Sequence'; }
  }
}

// Initialize the sequences tab on first open.
async function initDesignSteps() {
  await loadSequencesList();
  // Seed the editor with sensible default steps if the user clicks "New",
  // without auto-opening it  -  the list view is the default surface now.
  if (!designSteps || !designSteps.length) {
    designSteps = [
      { step_type: 'send_linkedin_connect', day_offset: 1, template_id: null, label: 'LinkedIn Connect' },
      { step_type: 'send_linkedin_message', day_offset: 7, template_id: null, label: 'LinkedIn Message' },
    ];
  }
}

// Populate the campaign builder's sequence-picker dropdown.
async function loadCampaignSequenceDropdown(selectedId, targetElId) {
  const sel = document.getElementById(targetElId || 'cw-sequence-config');
  if (!sel) return;
  try {
    const rows = await api('/sequence-configs');
    _sequenceConfigsCache = rows || [];
    if (!_sequenceConfigsCache.length) {
      sel.innerHTML = '<option value="">No sequences yet  -  create one in Sequences</option>';
      sel.disabled = true;
      return;
    }
    sel.disabled = false;
    const opts = ['<option value=""> -  Select a sequence  - </option>'];
    for (const r of _sequenceConfigsCache) {
      const label = r.is_owner ? r.name : r.name + ' (shared)';
      // Build friendly step description from config steps if available
      let sub;
      const steps = r.config?.steps || [];
      const stepLabels = steps
        .filter(s => s.step_type !== 'end_sequence')
        .map(s => {
          const t = (s.step_type || '').replace('send_', '').replace('linkedin_', 'LI ').replace('_', ' ');
          return t.charAt(0).toUpperCase() + t.slice(1);
        });
      if (stepLabels.length) {
        sub = ' · ' + stepLabels.join(' → ');
      } else {
        sub = ` · ${r.step_count} step${r.step_count === 1 ? '' : 's'}`;
      }
      opts.push(`<option value="${esc(r.id)}">${esc(label)}${esc(sub)}</option>`);
    }
    sel.innerHTML = opts.join('');
    if (selectedId) sel.value = selectedId;
  } catch (e) {
    sel.innerHTML = '<option value="">Failed to load sequences</option>';
  }
}

async function loadCostBreakdown() {
  const loading = document.getElementById('cost-loading');
  const content = document.getElementById('cost-content');
  try {
    const d = await api('/analytics/costs');
    loading.classList.add('hidden');
    content.classList.remove('hidden');

    const fmt = v => '$' + v.toFixed(2);
    const fmtTokens = n => n >= 1_000_000 ? (n/1_000_000).toFixed(2)+'M' : n >= 1000 ? (n/1000).toFixed(1)+'K' : n.toString();

    const subs = d.subscriptions;
    const subLines = [
      ['Apollo', subs.apollo_monthly],
      ['PhantomBuster', subs.phantombuster_monthly],
      ['Reacher', subs.reacher_monthly],
      ['Hunter', subs.hunter_monthly],
      // Proxycurl removed
      [PROJECT_SEARCH_PROVIDER.displayName, subs[PROJECT_SEARCH_PROVIDER.costField]],
    ].filter(([, v]) => v > 0);
    const subBreakdown = subLines.length
      ? subLines.map(([n, v]) => `${n}: ${fmt(v)}`).join(' · ')
      : 'No subscriptions set';
    document.getElementById('cost-summary-cards').innerHTML = [
      { label: 'Total Subscriptions', value: fmt(subs.total_monthly), sub: subBreakdown, color: 'var(--blue)' },
      { label: 'Total Monthly', value: fmt(subs.total_monthly + d.anthropic.total_cost_usd), sub: 'subscriptions + AI', color: 'var(--navy)' },
    ].map(c => `<div class="card" style="text-align:center;">
      <div style="font-size:1.6rem;font-weight:700;color:${c.color};letter-spacing:-0.02em;">${c.value}</div>
      <div class="card-label" style="margin-top:4px;">${c.label}</div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:2px;">${c.sub}</div>
    </div>`).join('');

    document.getElementById('cost-pricing-note').textContent =
      `${d.anthropic.model} · $${d.anthropic.pricing.input_per_mtok}/MTok input · $${d.anthropic.pricing.output_per_mtok}/MTok output`;

    const fmtCost = v => v > 0 ? `$${v.toFixed(4)}` : '<span style="color:var(--text-muted)"> - </span>';
    document.getElementById('cost-tbody').innerHTML = d.campaigns.map(c =>
      `<tr>
        <td style="font-weight:500;">${esc(c.name)}</td>
        <td>${statusBadge(c.status)}</td>
        <td style="text-align:right;font-weight:500;">${fmtCost(c.anthropic_cost_usd)}</td>
      </tr>`
    ).join('') || '<tr><td colspan="3" style="text-align:center;color:var(--text-muted);padding:16px;">No campaigns yet.</td></tr>';

  } catch(e) {
    loading.textContent = 'Failed to load costs: ' + e.message;
    loading.style.color = 'var(--red)';
  }
}

// ── Conference (post) stepper navigation ─────────────────────────────────────
function setConfStep(n) {
  confActiveStep = n;
  for (let i = 1; i <= 4; i++) {
    const panel = document.getElementById('conf-step-' + i);
    if (panel) { panel.classList.toggle('hidden', i !== n); panel.style.display = i === n ? '' : 'none'; }
    const dot = document.getElementById('conf-dot-' + i);
    if (dot) {
      const isActive = i === n;
      dot.style.background = isActive ? 'var(--navy)' : 'var(--border)';
      dot.style.color = isActive ? '#fff' : 'var(--text-muted)';
    }
    const step = document.querySelector(`.conf-step[data-step="${i}"]`);
    if (step) {
      const label = step.querySelector('.conf-step-label');
      if (label) {
        label.style.fontWeight = i === n ? '600' : '500';
        label.style.color = i === n ? 'var(--navy)' : 'var(--text-muted)';
      }
    }
  }
  // Hide lead table on Step 1 (Event Setup) — not relevant there
  const leadsPanel = document.getElementById('conf-leads-panel');
  if (leadsPanel) leadsPanel.style.display = n === 1 ? 'none' : '';
  if (n !== 1) renderConfLeads();
}

function updateConfStepperBadges() {
  // Numbered dots — active step is highlighted in setConfStep()
}

async function confNext(toStep) {
  // Auto-save current step before advancing
  if (confActiveStep === 1) {
    const name = document.getElementById('conf-name-input').value.trim();
    if (!name) { toast('Enter an event name before continuing.', 'error'); return; }
    await saveConfEvent();
  }
  if (toStep === 4) {
    try {
      const s = await api('/outlook/auth/status');
      if (!s.authenticated) {
        toast('Outlook is not connected. Connect it in the Inbox settings to push drafts.', 'warning');
      }
    } catch(_) {}
  }
  setConfStep(toStep);
}

// ── Post-conf source chooser ────────────────────────────────────────────────
function showConfSource(src) {
  const sources = ['apollo', 'salesnav', 'file'];
  for (const s of sources) {
    const panel = document.getElementById('conf-src-' + s);
    const btn = document.getElementById('conf-src-' + s + '-btn');
    if (panel) panel.style.display = s === src ? '' : 'none';
    if (btn) {
      btn.style.background = s === src ? 'var(--navy)' : 'var(--surface)';
      btn.style.color = s === src ? '#fff' : '';
      btn.style.borderColor = s === src ? 'var(--navy)' : 'var(--border)';
    }
  }
}

// ── Post-conf event management ──────────────────────────────────────────────
async function loadConfEventList() {
  try {
    const events = await api('/conference/conferences?outreach_type=post');
    const sel = document.getElementById('conf-event-select');
    if (!sel) return;
    const prev = sel.value || confCurrentEvent;
    sel.innerHTML = '<option value="">Select an event...</option>' + events.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
    if (prev && events.includes(prev)) { sel.value = prev; confCurrentEvent = prev; }
  } catch(_) {}
}

async function onConfEventChange() {
  const sel = document.getElementById('conf-event-select');
  if (!sel) return;
  confCurrentEvent = sel.value;
  // Also sync conf-filter dropdown used by analytics/costs
  const filterSel = document.getElementById('conf-filter');
  if (filterSel) filterSel.value = confCurrentEvent;
  if (!confCurrentEvent) {
    confLeads = [];
    renderConfLeads();
    return;
  }
  document.getElementById('conf-name-input').value = confCurrentEvent;
  const dateInput = document.getElementById('conf-date-input');
  const contextInput = document.getElementById('conf-context');
  if (dateInput) dateInput.value = '';
  if (contextInput) contextInput.value = '';
  try {
    const ctx = await api('/settings/conference-context?event_name=' + encodeURIComponent(confCurrentEvent));
    if (ctx.event_date && dateInput) dateInput.value = ctx.event_date;
    if (ctx.context && contextInput) contextInput.value = ctx.context;
  } catch(_) {}
  await loadConferenceLeads(true);
  updateConfStepperBadges();
}

async function createNewConfEvent() {
  const name = await customPrompt('Event name (e.g. ADIPEC 2026):', '');
  if (!name || !name.trim()) return;
  confCurrentEvent = name.trim();
  document.getElementById('conf-name-input').value = confCurrentEvent;
  const sel = document.getElementById('conf-event-select');
  if (sel) {
    let found = false;
    for (const opt of sel.options) { if (opt.value === confCurrentEvent) { found = true; break; } }
    if (!found) {
      const opt = document.createElement('option');
      opt.value = confCurrentEvent;
      opt.textContent = confCurrentEvent;
      sel.appendChild(opt);
    }
    sel.value = confCurrentEvent;
  }
  confLeads = [];
  renderConfLeads();
  setConfStep(1);
  await saveConfEvent();
  toast('Event created.', 'info');
}

async function saveConfEvent() {
  const name = document.getElementById('conf-name-input').value.trim();
  const date = document.getElementById('conf-date-input')?.value || '';
  const context = document.getElementById('conf-context')?.value?.trim() || '';
  if (!name) { toast('Enter an event name.', 'error'); return; }
  const isNew = confCurrentEvent !== name;
  confCurrentEvent = name;
  try {
    await api('/settings/conference-context', {
      method: 'POST',
      body: JSON.stringify({ event_name: name, event_date: date, context }),
    });
    const sel = document.getElementById('conf-event-select');
    if (sel) {
      let found = false;
      for (const opt of sel.options) { if (opt.value === name) { found = true; break; } }
      if (!found) {
        const opt = document.createElement('option');
        opt.value = name; opt.textContent = name;
        sel.appendChild(opt);
      }
      sel.value = name;
    }
    // Also sync conf-filter
    const filterSel = document.getElementById('conf-filter');
    if (filterSel) {
      let found = false;
      for (const opt of filterSel.options) { if (opt.value === name) { found = true; break; } }
      if (!found) {
        const opt = document.createElement('option');
        opt.value = name; opt.textContent = name;
        filterSel.appendChild(opt);
      }
      filterSel.value = name;
    }
    const status = document.getElementById('conf-event-save-status');
    if (status) {
      status.textContent = isNew ? 'Event created' : 'Saved';
      setTimeout(() => { status.textContent = ''; }, 3000);
    }
    toast(isNew ? `Event "${name}" created` : 'Event updated');
    updateConfStepperBadges();
  } catch(e) { toast('Save failed: ' + e.message, 'error'); }
}

// ── Post-conf Apollo search ─────────────────────────────────────────────────
let _busySearchApolloConf = false;
async function searchApolloConf() {
  if (_busySearchApolloConf) return;
  const status = document.getElementById('conf-apollo-status');
  if (status) status.textContent = 'Searching...';
  const confName = confCurrentEvent || document.getElementById('conf-name-input').value.trim();
  if (!confName) { if (status) status.textContent = ''; toast('Create or select an event first (Step 1).', 'error'); return; }

  const splitTrim = v => v.split(',').map(s => s.trim()).filter(Boolean);
  const titlesEl = document.getElementById('conf-apollo-titles');
  const seniorEl = document.getElementById('conf-apollo-seniorities');
  const indEl = document.getElementById('conf-apollo-industries');
  const locEl = document.getElementById('conf-apollo-locations');
  const empEl = document.getElementById('conf-apollo-employees');
  const req = {
    conference_name: confName,
    event_date: document.getElementById('conf-date-input')?.value || undefined,
    outreach_type: 'post',
    titles: titlesEl ? splitTrim(titlesEl.value) : [],
    seniorities: seniorEl ? splitTrim(seniorEl.value) : [],
    industries: indEl ? splitTrim(indEl.value) : [],
    locations: locEl ? splitTrim(locEl.value) : [],
    employee_ranges: empEl ? splitTrim(empEl.value) : [],
  };
  for (const k of Object.keys(req)) { if (Array.isArray(req[k]) && !req[k].length) delete req[k]; }

  _busySearchApolloConf = true;
  try {
    const data = await api('/conference/import-apollo', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(req) });
    _apolloConfPreviewResults = data.results || [];
    if (status) status.textContent = `${_apolloConfPreviewResults.length} results found`;
    renderApolloConfPreview();
  } catch(e) { if (status) status.textContent = ''; toast('Apollo search failed: ' + e.message, 'error'); }
  finally { _busySearchApolloConf = false; }
}

function renderApolloConfPreview() {
  const wrap = document.getElementById('conf-apollo-preview');
  const tbody = document.getElementById('conf-apollo-tbody');
  if (!wrap || !tbody) return;
  if (!_apolloConfPreviewResults.length) { wrap.classList.add('hidden'); return; }
  wrap.classList.remove('hidden');
  tbody.innerHTML = _apolloConfPreviewResults.map(p => `<tr>
    <td><input type="checkbox" class="conf-apollo-check" value="${esc(p.apollo_id)}" checked></td>
    <td>${esc(p.first_name||'')} ${esc(p.last_name||'')}</td>
    <td style="font-size:11px;">${esc(p.title||'')}</td>
    <td style="font-size:11px;">${esc(p.company||'')}</td>
    <td style="font-size:11px;">${esc(p.city||'')}${p.country ? ', ' + esc(p.country) : ''}</td>
  </tr>`).join('');
}

function confApolloToggleAll(el) {
  document.querySelectorAll('.conf-apollo-check').forEach(cb => { cb.checked = el.checked; });
}

let _busyConfirmApolloConfImport = false;
async function confirmApolloConfImport() {
  if (_busyConfirmApolloConfImport) return;
  const selected = [...document.querySelectorAll('.conf-apollo-check:checked')].map(cb => cb.value);
  if (!selected.length) { toast('Select at least one contact.', 'error'); return; }
  const confName = confCurrentEvent || document.getElementById('conf-name-input').value.trim();
  const status = document.getElementById('conf-apollo-status');
  if (status) status.textContent = `Importing ${selected.length} contacts...`;
  _busyConfirmApolloConfImport = true;
  try {
    const data = await api('/conference/import-apollo/confirm', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({
      conference_name: confName, event_date: document.getElementById('conf-date-input')?.value || undefined, apollo_ids: selected, outreach_type: 'post',
    })});
    if (status) status.textContent = `\u2713 Imported ${data.imported}, skipped ${data.skipped}`;
    const previewWrap = document.getElementById('conf-apollo-preview');
    if (previewWrap) previewWrap.classList.add('hidden');
    _apolloConfPreviewResults = [];
    confCurrentEvent = confName;
    await loadConfEventList();
    const sel = document.getElementById('conf-event-select');
    if (sel) sel.value = confName;
    await loadConferenceLeads(true);
    toast(`${data.imported} leads imported`);
  } catch(e) { if (status) status.textContent = ''; toast('Import failed: ' + e.message, 'error'); }
  finally { _busyConfirmApolloConfImport = false; }
}

// ── Post-conf Sales Navigator search ────────────────────────────────────────
let _busySearchSalesNavConf = false;
async function searchSalesNavConf() {
  if (_busySearchSalesNavConf) return;
  const status = document.getElementById('conf-salesnav-status');
  const confName = confCurrentEvent || document.getElementById('conf-name-input').value.trim();
  if (!confName) { toast('Create or select an event first (Step 1).', 'error'); return; }

  const url = document.getElementById('conf-salesnav-url')?.value?.trim();
  if (!url || !url.includes('linkedin.com')) { toast('Paste a valid Sales Navigator search URL.', 'error'); return; }

  _busySearchSalesNavConf = true;
  if (status) status.textContent = 'Launching LinkedIn search...';
  try {
    const launchData = await api('/conference/import-salesnav', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        conference_name: confName,
        event_date: document.getElementById('conf-date-input')?.value || undefined,
        search_url: url,
        max_results: parseInt(document.getElementById('conf-salesnav-max')?.value, 10) || 100,
        outreach_type: 'post',
      }),
    });
    const containerId = launchData.container_id;
    if (!containerId) { if (status) status.textContent = ''; toast('Sales Nav launch returned no container ID.', 'error'); return; }
    if (status) status.textContent = 'Searching LinkedIn (polling for results)...';
    const eventDate = document.getElementById('conf-date-input')?.value || '';
    const maxResults = launchData.max_results || 100;
    const MAX_POLLS = 60;
    let polls = 0;
    await new Promise((resolve) => {
      const pollTimer = setInterval(async () => {
        polls++;
        try {
          const qs = new URLSearchParams({ container_id: containerId, conference_name: confName, max_results: String(maxResults) });
          if (eventDate) qs.set('event_date', eventDate);
          const result = await api('/conference/import-salesnav/status?' + qs.toString());
          if (result.status === 'running') {
            if (status) status.textContent = `Searching LinkedIn (polling${'.'.repeat((polls % 3) + 1)})`;
            if (polls >= MAX_POLLS) { clearInterval(pollTimer); if (status) status.textContent = 'Timed out.'; toast('Sales Nav search timed out.', 'error'); resolve(); }
            return;
          }
          clearInterval(pollTimer);
          if (result.status === 'done') {
            if (status) status.textContent = `\u2713 Imported ${result.imported}, skipped ${result.skipped}, failed ${result.failed}`;
            confCurrentEvent = confName;
            await loadConfEventList();
            const sel = document.getElementById('conf-event-select');
            if (sel) sel.value = confName;
            await loadConferenceLeads(true);
            toast(`${result.imported} leads imported from Sales Navigator`);
          } else {
            if (status) status.textContent = '';
            toast('Sales Nav search failed: ' + (result.detail || 'Unknown error'), 'error');
          }
          resolve();
        } catch (e) { clearInterval(pollTimer); if (status) status.textContent = ''; toast('Sales Nav status check failed: ' + e.message, 'error'); resolve(); }
      }, 10000);
    });
  } catch(e) { if (status) status.textContent = ''; toast('Sales Nav search failed: ' + e.message, 'error'); }
  finally { _busySearchSalesNavConf = false; }
}

// ── Post-conf file import (uses existing importConferenceLeads for Step 2) ──
async function importConfLeadsFile() {
  const confName = confCurrentEvent || document.getElementById('conf-name-input').value.trim();
  const eventDate = document.getElementById('conf-date-input')?.value || '';
  const fileInput = document.getElementById('conf-file-input');
  const status = document.getElementById('conf-import-status');
  if (!confName) { toast('Create or select an event first (Step 1).', 'error'); return; }
  if (!fileInput || !fileInput.files.length) { toast('Select a file.', 'error'); return; }

  const fd = new FormData();
  fd.append('file', fileInput.files[0]);
  let url = `/conference/import?conference_name=${encodeURIComponent(confName)}&outreach_type=post`;
  if (eventDate) url += `&event_date=${encodeURIComponent(eventDate)}`;

  if (status) status.textContent = 'Importing...';
  try {
    const res = await fetch(API + url, {
      method: 'POST',
      headers: _authToken ? { 'Authorization': `Bearer ${_authToken}` } : {},
      body: fd,
    });
    if (!res.ok) { const e = await res.json(); throw new Error(e.detail || res.statusText); }
    const data = await res.json();
    if (status) status.textContent = `\u2713 Imported ${data.imported}, skipped ${data.skipped}`;
    fileInput.value = '';
    confFileChanged();
    confCurrentEvent = confName;
    await loadConfEventList();
    const sel = document.getElementById('conf-event-select');
    if (sel) sel.value = confName;
    await loadConferenceLeads(true);
    toast(`${data.imported} leads imported`);
  } catch(e) { if (status) status.textContent = ''; toast('Import failed: ' + e.message, 'error'); }
}

// ── Post-conf preview one ───────────────────────────────────────────────────
async function previewOneConf(btn) {
  const context = document.getElementById('conf-context')?.value?.trim();
  if (!context) { toast('Set outreach context in Step 1 first.', 'error'); setConfStep(1); return; }
  const lead = confLeads.find(l => !l.generated_sequence && !l.replied_at) || confLeads[0];
  if (!lead) { toast('No leads to preview.', 'error'); return; }
  const orig = btn?.textContent;
  if (btn) { btn.disabled = true; btn.textContent = 'Generating...'; }
  try {
    const data = await api('/conference/generate-sequence', {
      method: 'POST',
      body: JSON.stringify({ lead_id: lead.id, context }),
    });
    const idx = confLeads.findIndex(l => l.id === lead.id);
    if (idx !== -1) confLeads[idx] = data;
    renderConfLeads();
    openSeqModal(lead.id);
    toast(`Preview generated for ${data.first_name || 'lead'}. Review and click Generate All when ready.`);
  } catch (e) { toast('Preview failed: ' + e.message, 'error'); }
  finally { if (btn) { btn.disabled = false; btn.textContent = orig; } }
}

// ── Post-conf launch LinkedIn ───────────────────────────────────────────────
async function launchConfLinkedIn(btn) {
  const confName = confCurrentEvent || (confCurrentEvent || '');
  if (!confName) { toast('Select an event first.', 'error'); return; }
  const orig = btn?.textContent;
  if (btn) { btn.disabled = true; btn.textContent = 'Enrolling...'; }
  try {
    const r = await api('/conference/launch-linkedin', {
      method: 'POST',
      body: JSON.stringify({ conference_name: confName }),
    });
    toast(`LinkedIn outreach launched: ${r.enrolled} enrolled, ${r.skipped} already enrolled, ${r.no_linkedin} skipped (no LinkedIn URL)`);
    await loadConferenceLeads();
  } catch (e) {
    toast('Launch failed: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = orig; }
  }
}

// ── Conference ─────────────────────────────────────────────────────────────────
async function loadConference() {
  try {
    await Promise.all([refreshConfFilter(), loadConfEventList(), loadInboxes_conf(), checkOutlookStatus(), loadConfSeqDesign()]);
    // Outlook / inbox status for accounts bar
    try {
      const s = await api('/outlook/auth/status');
      const inboxBar = document.getElementById('conf-inbox-status');
      if (inboxBar) {
        if (s.authenticated) {
          inboxBar.innerHTML = `<span style="color:var(--green);">\u25CF</span> ${esc(s.account || 'Connected')}`;
        } else {
          inboxBar.innerHTML = '<span style="color:var(--red);">\u25CF</span> Not connected';
        }
      }
    } catch(_) {
      const inboxBar = document.getElementById('conf-inbox-status');
      if (inboxBar) inboxBar.innerHTML = '<span style="color:var(--text-muted);">\u25CF</span> Unknown';
    }
    // LinkedIn status
    try {
      const h = await api('/health/full');
      const liBar = document.getElementById('conf-linkedin-status');
      if (liBar) {
        const liOk = h.checks?.linkedin_worker?.status === 'ok';
        if (liOk) {
          liBar.innerHTML = '<span style="color:var(--green);">\u25CF</span> PhantomBuster connected';
        } else {
          liBar.innerHTML = '<span style="color:var(--red);">\u25CF</span> Worker not running';
        }
      }
    } catch(_) {
      const liBar = document.getElementById('conf-linkedin-status');
      if (liBar) liBar.innerHTML = '<span style="color:var(--text-muted);">\u25CF</span> Unknown';
    }
    if (confCurrentEvent) await loadConferenceLeads(true);
    setConfStep(confActiveStep);
  } catch(e) { toast('Failed to load conference data: ' + e.message, 'error'); }
}

async function checkOutlookStatus() {
  try {
    const s = await api('/outlook/auth/status');
    // Update all Outlook badges (conference + pre-conference tabs)
    for (const id of ['conf-outlook-status', 'preconf-outlook-status']) {
      const badge = document.getElementById(id);
      if (!badge) continue;
      if (s.authenticated) {
        badge.style.background = '#dcfce7';
        badge.style.color = '#15803d';
        badge.style.borderColor = 'rgba(21,128,61,0.2)';
        badge.textContent = '● Outlook connected' + (s.account ? ' (' + s.account + ')' : '');
        badge.onclick = async () => {
          if (!await customConfirm('Disconnect Outlook?')) return;
          await api('/outlook/auth/disconnect', { method: 'POST' });
          checkOutlookStatus();
        };
      } else {
        badge.style.background = '#fee2e2';
        badge.style.color = '#b91c1c';
        badge.style.borderColor = 'rgba(220,38,38,0.2)';
        badge.textContent = '● Outlook not connected';
        badge.onclick = () => connectOutlook();
      }
    }
  } catch(e) { console.error('checkOutlookStatus:', e); }
}

function connectOutlook() { connectInboxOutlook('outlook', 'Personal Outlook'); }

function connectInboxOutlook(inboxId, inboxName) {
  _outlookAuthInboxId = inboxId;
  document.getElementById('outlook-auth-title').textContent =
    inboxId === 'outlook' ? 'Connect Outlook' : `Connect Outlook: ${inboxName}`;
  document.getElementById('outlook-auth-step1').classList.remove('hidden');
  document.getElementById('outlook-auth-step2').classList.add('hidden');
  document.getElementById('outlook-auth-step3').classList.add('hidden');
  document.getElementById('outlook-auth-modal').classList.remove('hidden');
}

function closeOutlookAuth() {
  if (window._outlookAuthPoll) { clearInterval(window._outlookAuthPoll); window._outlookAuthPoll = null; }
  document.getElementById('outlook-auth-modal').classList.add('hidden');
  const connectBtn = document.getElementById('outlook-connect-btn');
  if (connectBtn) { connectBtn.disabled = false; connectBtn.textContent = connectBtn.dataset._origText || 'Connect Outlook Account'; }
  checkOutlookStatus();
  renderInboxList();
}

async function disconnectInboxOutlook(inboxId) {
  if (!await customConfirm('Disconnect this Outlook account?')) return;
  const url = inboxId === 'outlook' ? '/outlook/auth/disconnect' : `/outlook/inbox/${inboxId}/auth/disconnect`;
  try {
    await api(url, { method: 'POST' });
    toast('Disconnected');
    if (inboxId === 'outlook') checkOutlookStatus();
    renderInboxList();
  } catch(e) { toast('Disconnect failed: ' + e.message, 'error'); }
}

async function startOutlookAuth(btn) {
  const inboxId = _outlookAuthInboxId;
  const initiateUrl = inboxId === 'outlook' ? '/outlook/auth/initiate' : `/outlook/inbox/${inboxId}/auth/initiate`;
  const completeUrl = inboxId === 'outlook' ? '/outlook/auth/complete' : `/outlook/inbox/${inboxId}/auth/complete`;
  const connectBtn = btn || document.getElementById('outlook-connect-btn');
  if (connectBtn) { connectBtn.disabled = true; connectBtn.dataset._origText = connectBtn.textContent; connectBtn.textContent = 'Connecting…'; }
  try {
    const data = await api(initiateUrl, { method: 'POST' });
    if (data.already_authenticated) { showOutlookSuccess(data.account); return; }
    document.getElementById('outlook-auth-step1').classList.add('hidden');
    document.getElementById('outlook-auth-step2').classList.remove('hidden');
    document.getElementById('outlook-user-code').textContent = data.user_code;
    const link = document.getElementById('outlook-verify-link');
    link.href = data.verification_uri;
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = 'Open ' + data.verification_uri.replace('https://', '') + ' ↗';
    document.getElementById('outlook-auth-waiting').textContent = 'Waiting for sign-in…';
    // FIX-42: render the link only  -  do NOT call window.open (popup blockers eat it)
    try { await api(completeUrl, { method: 'POST' }); } catch(e) {}
    const statusUrl = inboxId === 'outlook' ? '/outlook/auth/status' : `/outlook/inbox/${inboxId}/auth/status`;
    const deadline = Date.now() + (data.expires_in || 900) * 1000;
    if (window._outlookAuthPoll) clearInterval(window._outlookAuthPoll);
    const poll = window._outlookAuthPoll = setInterval(async () => {
      if (Date.now() > deadline) { clearInterval(poll); document.getElementById('outlook-auth-waiting').textContent = 'Code expired. Try again.'; return; }
      try {
        const s = await api(statusUrl);
        if (s.authenticated) { clearInterval(poll); showOutlookSuccess(s.account); }
        else if (s.failed) { clearInterval(poll); document.getElementById('outlook-auth-waiting').textContent = s.error || 'Sign-in failed.'; }
      } catch(e) {}
    }, 3000);
  } catch(e) {
    toast('Outlook auth error: ' + e.message, 'error');
    if (connectBtn) { connectBtn.disabled = false; connectBtn.textContent = connectBtn.dataset._origText || 'Connect Outlook Account'; }
  }
}

function showOutlookSuccess(account) {
  document.getElementById('outlook-auth-step2').classList.add('hidden');
  document.getElementById('outlook-auth-step3').classList.remove('hidden');
  document.getElementById('outlook-auth-account').textContent = account || '';
}

async function refreshConfFilter() {
  try {
    const names = await api('/conference/conferences?outreach_type=post');
    const sel = document.getElementById('conf-filter') || document.getElementById('conf-event-select');
    if (sel) {
      const current = sel.value;
      sel.innerHTML = '<option value="">All conferences</option>' +
        names.map(n => `<option value="${esc(n)}" ${n===current?'selected':''}>${esc(n)}</option>`).join('');
    }
  } catch(e) { console.error('refreshConfFilter:', e); }
  toggleConfDeleteBtn();
}

function onConfFilterChange() {
  toggleConfDeleteBtn();
  loadConferenceLeads(true);
  loadConfAnalytics();
}

function toggleConfDeleteBtn() {
  const sel = document.getElementById('conf-filter') || document.getElementById('conf-event-select');
  const btn = document.getElementById('conf-delete-btn');
  if (btn) btn.style.display = (sel && sel.value) ? '' : 'none';
}

async function deleteConference() {
  const name = (confCurrentEvent || '');
  if (!name) return;
  if (!await customConfirm(`Delete conference "${name}" and ALL its leads? This cannot be undone.`, {destructive: true})) return;
  const btn = document.getElementById('conf-delete-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Deleting…'; }
  try {
    const res = await api(`/conference/conferences/${encodeURIComponent(name)}?outreach_type=post`, { method: 'DELETE' });
    toast(`Deleted "${name}" (${res.deleted} leads)`);
    confCurrentEvent = '';
    toggleConfDeleteBtn();
    await refreshConfFilter();
    await loadConferenceLeads(true);
  } catch(e) { toast('Delete failed: ' + e.message, 'error'); }
  finally { if (btn) { btn.disabled = false; btn.textContent = 'Delete Conference'; } }
}

// Cached sequence step count from /conference/costs. Used by the bulk-gen-seq
// tooltip and the progress tracker. Defaults to 6 so first paint never shows
// "undefined-email" before the first /costs call finishes.
let confSeqStepCount = 6;

function applyConfSeqCountToUi(n) {
  if (!Number.isFinite(n) || n < 1) n = 6;
  confSeqStepCount = n;
  const seqBtn = document.getElementById('conf-bulk-gen-seq');
  if (seqBtn) {
    seqBtn.setAttribute(
      'title',
      `Generate a full ${n}-email follow-up sequence per lead using Claude AI. Includes LinkedIn messages. Uses more API credits.`
    );
  }
  const intro = document.getElementById('conf-intro-copy');
  if (intro) {
    intro.innerHTML =
      `<strong>Events outreach</strong> sends warm, personal emails from your own Outlook inbox to people you&#8217;ve met at conferences. ` +
      `Generate a single email or a full ${n}-email follow-up sequence. All emails go to Outlook <em>Drafts</em> so you review each one before hitting send.`;
  }
}

async function loadConfCosts() {
  try {
    const data = await api('/conference/costs');
    document.getElementById('conf-cost-total').textContent = '$' + data.total_cost_usd.toFixed(4);
    if (typeof data.sequence_step_count === 'number') {
      applyConfSeqCountToUi(data.sequence_step_count);
    }
    const el = document.getElementById('conf-cost-detail');
    if (!data.events.length) { el.innerHTML = '<span style="color:var(--text-muted);">No events yet.</span>'; return; }
    const th = 'padding:5px 10px;font-weight:600;color:var(--text-muted);font-size:11px;text-transform:uppercase;letter-spacing:0.3px;text-align:left;';
    const td = 'padding:5px 10px;font-size:12px;';
    let rows = '';
    data.events.forEach(ev => {
      if (ev.users.length) {
        ev.users.forEach((u, i) => {
          rows += `<tr style="border-bottom:1px solid var(--border);">` +
            (i === 0 ? `<td rowspan="${ev.users.length}" style="${td}font-weight:500;color:var(--navy);vertical-align:top;">${esc(ev.event)}<div style="font-size:10px;color:var(--text-muted);">${ev.leads} leads</div></td>` : '') +
            `<td style="${td}">${esc(u.user_name)}</td>` +
            `<td style="${td}text-align:right;">${u.leads}</td>` +
            `<td style="${td}text-align:right;font-weight:600;color:var(--accent);">$${u.cost_usd.toFixed(4)}</td></tr>`;
        });
      } else {
        rows += `<tr style="border-bottom:1px solid var(--border);">` +
          `<td style="${td}font-weight:500;color:var(--navy);">${esc(ev.event)}<div style="font-size:10px;color:var(--text-muted);">${ev.leads} leads</div></td>` +
          `<td style="${td}color:var(--text-muted);"> - </td>` +
          `<td style="${td}text-align:right;">${ev.leads}</td>` +
          `<td style="${td}text-align:right;font-weight:600;color:var(--accent);">$${ev.cost_usd.toFixed(4)}</td></tr>`;
      }
    });
    el.innerHTML = `<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;">` +
      `<thead><tr style="border-bottom:1px solid var(--border);">` +
      `<th style="${th}">Event</th><th style="${th}">User</th><th style="${th}text-align:right;">Leads</th><th style="${th}text-align:right;">Cost</th>` +
      `</tr></thead><tbody>${rows}</tbody></table></div>`;
  } catch(_) {}
}

async function loadConferenceLeads(resetPage) {
  if (resetPage) confLeadsPage = 0;
  document.getElementById('conf-loading').classList.remove('hidden');
  document.getElementById('conf-table-wrap').classList.add('hidden');
  document.getElementById('conf-empty').classList.add('hidden');
  try {
    const conf = confCurrentEvent || (confCurrentEvent || '');
    let url = '/conference/leads?outreach_type=post&limit=' + CONF_LEADS_PAGE_SIZE + '&offset=' + (confLeadsPage * CONF_LEADS_PAGE_SIZE);
    if (conf) url += '&conference_name=' + encodeURIComponent(conf);
    const data = await api(url);
    confLeads = Array.isArray(data) ? data : (data.items || []);
    confLeadsTotal = (data && data.total != null) ? data.total : confLeads.length;
    // Enrich with orch_state for Step 4 (Send) — same pattern as pre-conf
    if (confActiveStep === 4 && confLeads.length) {
      try {
        const orchIds = confLeads.filter(l => l.id && !l.orch_state).map(l => l.id);
        if (orchIds.length) {
          const orchData = await api('/conference/orch-states', {
            method: 'POST',
            body: JSON.stringify({ lead_ids: orchIds, outreach_type: 'post' }),
          });
          if (orchData && orchData.states) {
            for (const l of confLeads) {
              if (orchData.states[l.id]) l.orch_state = orchData.states[l.id];
            }
          }
        }
      } catch(_) { /* orch enrichment is best-effort */ }
    }
    renderConfLeads();
    loadConfCosts();
    loadConfAnalytics();
    updateConfStepperBadges();
  } catch(e) {
    document.getElementById('conf-loading').innerHTML = `<div style="color:var(--red)">Failed: ${esc(e.message)}</div>`;
  }
}
async function confLeadsPageNav(dir) {
  confLeadsPage = Math.max(0, confLeadsPage + dir);
  await loadConferenceLeads();
}

function renderConfLeads() {
  document.getElementById('conf-loading').classList.add('hidden');
  if (!confLeads.length) {
    const confFilter = confCurrentEvent || (confCurrentEvent || '');
    const emptyEl = document.getElementById('conf-empty');
    emptyEl.textContent = confFilter ? `No leads for "${confFilter}". Import leads above.` : 'No leads yet. Import leads above.';
    emptyEl.classList.remove('hidden');
    document.getElementById('conf-leads-count').textContent = '';
    return;
  }
  const start = confLeadsPage * CONF_LEADS_PAGE_SIZE + 1;
  const end = Math.min(start + confLeads.length - 1, confLeadsTotal);
  const pageInfo = confLeadsTotal > CONF_LEADS_PAGE_SIZE ? ` (${start}\u2013${end} of ${confLeadsTotal})` : '';
  document.getElementById('conf-leads-count').textContent = `${confLeadsTotal} lead${confLeadsTotal!==1?'s':''}${pageInfo}`;
  document.getElementById('conf-table-wrap').classList.remove('hidden');

  // Contextual columns based on active step
  const step = confActiveStep;
  const thead = document.getElementById('conf-thead');
  if (thead) {
    let headerHtml = '<th style="width:32px;"><input type="checkbox" id="conf-check-all" onchange="confToggleAll(this)"></th><th>Name</th><th>Email</th><th>Company / Title</th>';
    if (step === 2) headerHtml += '<th style="text-align:center;">Category</th><th style="text-align:center;">Enriched</th>';
    if (step === 3) headerHtml += '<th>Generated Subject</th><th style="text-align:center;">View</th>';
    if (step === 4) headerHtml += '<th>Sequence</th><th style="text-align:center;">Status</th><th style="text-align:center;">Next</th><th style="text-align:center;">Category</th><th style="text-align:center;">Mtg</th><th>Notes</th><th style="text-align:center;">Draft</th><th style="text-align:center;">Reply</th>';
    headerHtml += '<th style="text-align:center;">Actions</th>';
    thead.innerHTML = '<tr>' + headerHtml + '</tr>';
  }

  document.getElementById('conf-tbody').innerHTML = confLeads.map(l => {
    const replied = !!l.replied_at;
    const repliedBadge = replied
      ? `<span class="conf-replied-badge" title="${esc(l.reply_subject || 'Reply detected')}${l.reply_snippet ? '\n\n' + esc(l.reply_snippet) : ''}" style="display:inline-flex;align-items:center;gap:3px;font-size:10px;padding:1px 6px;border-radius:8px;margin-left:4px;background:#fee2e2;color:#b91c1c;font-weight:600;cursor:help;">${icon('mail',10)} Replied</span>`
      : '';
    const notePreview = l.user_note ? esc((l.user_note||'').slice(0,120)) + ((l.user_note||'').length > 120 ? '...' : '') : '';
    const noteIcon = l.user_note
      ? `<span onclick="event.stopPropagation();openNoteEditor('${esc(l.id)}',this)" title="${notePreview}" style="display:inline-flex;align-items:center;margin-left:4px;color:#d97706;cursor:pointer;font-size:12px;">${icon('file-text',12)}</span>`
      : '';
    const rowBg = replied ? 'background:rgba(254, 226, 226, 0.18);' : '';
    const noteBorder = l.user_note ? 'border-left:3px solid #fbbf24;' : '';
    const disabledMark = '<span title="Lead has replied  -  further drafting disabled" style="color:var(--text-muted);font-size:11px;"> - </span>';
    const categoryBadge = l.category ? `<span style="font-size:10px;padding:1px 5px;border-radius:8px;background:${l.category==='partner'?'var(--blue-bg)':l.category==='referral'?'#fef3c7':'var(--green-bg)'};color:${l.category==='partner'?'var(--primary)':l.category==='referral'?'#92400e':'var(--green)'};">${esc(l.category)}</span>` : '<span style="color:var(--text-muted);">-</span>';

    let contextCols = '';

    if (step === 2) {
      // Step 2 (Leads): Category, Enriched
      contextCols = `<td style="text-align:center;">${categoryBadge}</td>`;
      contextCols += `<td style="text-align:center;">${l.enriched_at ? '<span style="color:var(--green);">\u2713</span>' : `<button class="btn btn-secondary" style="padding:3px 8px;font-size:11px;" onclick="enrichOneLead('${esc(l.id)}')" title="Enrich.">Enrich</button>`}</td>`;
    }
    if (step === 3) {
      // Step 3 (Sequence): Generated Subject, View
      const subj = l.generated_subject || '';
      contextCols = `<td style="font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(subj)}">${subj ? esc(subj) : '<span style="color:var(--text-muted);">Not generated</span>'}</td>`;
      contextCols += `<td style="text-align:center;">${l.generated_sequence
        ? `<button class="btn btn-secondary" style="padding:2px 6px;font-size:11px;" onclick="openSeqModal('${esc(l.id)}')">View</button>`
        : (replied ? disabledMark : `<button class="btn btn-outline" style="padding:3px 8px;font-size:11px;" onclick="generateOneSequence('${esc(l.id)}')" title="Generate.">Generate</button>`)}</td>`;
    }
    if (step === 4) {
      // Step 4 (Send): Sequence dots, Status, Next, Category, Meeting, Notes, Draft, Reply
      const seqSteps = confSeqSteps.map(s => ({
        day: s.day,
        type: s.type || 'email',
        label: s.preset !== 'custom' ? (_CONF_STEP_PRESETS[s.preset]?.label || s.desc) : s.desc
      }));
      const orchState = l.orch_state || null;

      if (orchState) {
        let emailSentCount = 0;
        const seqData = l.generated_sequence || {};
        const draftedSteps = seqData._drafted_steps || [];
        for (const sk of draftedSteps) { if (sk.startsWith('email')) emailSentCount++; }
        if (l.draft_created_at && !draftedSteps.includes('email1')) emailSentCount++;

        const dotsState = {
          enrolled_at: orchState.enrolled_at,
          current_step_index: orchState.current_step_index,
          status: orchState.status,
          email_sent_count: emailSentCount,
          linkedin_connect_sent: !!l.linkedin_connect_sent,
          linkedin_connect_queued: !!l.linkedin_connect_queued,
          linkedin_message_sent: !!l.linkedin_message_sent,
          linkedin_message_queued: !!l.linkedin_message_queued,
          linkedin_connected: false,
          engagement_score: orchState.engagement_score || 0,
          scheduled_next_at: orchState.scheduled_next_at,
        };
        const dots = renderStepDots(dotsState, seqSteps);
        const statusBdg = seqStatusBadge(orchState.status);
        const nextAt = seqFormatNext(orchState.scheduled_next_at);
        contextCols = `<td>${dots}</td><td style="text-align:center;">${statusBdg}</td><td style="text-align:center;">${nextAt}</td>`;
      } else {
        const hasSeq = !!l.generated_sequence;
        const label = !hasSeq ? 'Not generated' : 'Not launched';
        contextCols = `<td colspan="3" style="color:var(--text-muted);font-size:12px;">${label}</td>`;
      }
      // Category
      contextCols += `<td style="text-align:center;">${categoryBadge}</td>`;
      // Meeting
      contextCols += `<td style="text-align:center;"><label style="font-size:10px;color:var(--text-muted);cursor:pointer;" title="Meeting booked"><input type="checkbox" ${l.meeting_booked ? 'checked' : ''} onchange="toggleMeetingBooked('${esc(l.id)}')" style="vertical-align:middle;" onclick="event.stopPropagation()"> Mtg</label></td>`;
      // Notes
      contextCols += `<td style="font-size:11px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${l.user_note ? `<span onclick="event.stopPropagation();openNoteEditor('${esc(l.id)}',this)" style="cursor:pointer;color:#d97706;" title="${notePreview}">${esc((l.user_note||'').slice(0,50))}${(l.user_note||'').length > 50 ? '...' : ''}</span>` : `<button class="btn btn-secondary" style="padding:2px 6px;font-size:10px;" onclick="event.stopPropagation();openNoteEditor('${esc(l.id)}',this)">+</button>`}</td>`;
      // Draft
      contextCols += `<td style="text-align:center;">${replied ? disabledMark : (l.draft_created_at
        ? `<span style="color:var(--green);font-size:11px;">\u2713</span><button class="btn btn-secondary" style="padding:2px 4px;font-size:10px;margin-left:2px;" onclick="event.stopPropagation();clearOneDraft('${esc(l.id)}')" title="Clear draft">x</button>`
        : (l.generated_subject ? `<button class="btn btn-primary" style="padding:2px 6px;font-size:10px;" onclick="event.stopPropagation();draftOne('${esc(l.id)}')" title="Draft">Draft</button>` : '<span style="color:var(--text-muted);">-</span>'))}</td>`;
      // Reply
      contextCols += `<td style="text-align:center;">${replied
        ? `${repliedBadge}<button class="btn btn-outline" style="padding:1px 4px;font-size:9px;margin-left:2px;" onclick="event.stopPropagation();clearLeadReply('${esc(l.id)}')" title="Undo reply">Undo</button>`
        : '<span style="color:var(--text-muted);font-size:11px;">-</span>'}</td>`;
    }

    // For steps 2 and 3, keep legacy columns for actions
    const rowClickable = step === 4 && l.generated_sequence;
    const rowClick = rowClickable ? `onclick="openSeqModal('${esc(l.id)}')" style="cursor:pointer;${rowBg}${noteBorder}"` : `style="${rowBg}${noteBorder}"`;

    // Name column: simpler for step 4 (details are in dedicated columns), fuller for 2/3
    let nameCell;
    if (step === 4) {
      nameCell = `<td style="font-weight:500;">${esc(l.first_name||'')} ${esc(l.last_name||'')}${noteIcon}<br><span style="font-size:11px;color:var(--text-muted);">${esc(l.conference_name||'')}</span></td>`;
    } else {
      nameCell = `<td style="font-weight:500;">${esc(l.first_name||'')} ${esc(l.last_name||'')}${repliedBadge}${noteIcon}<br>
        <span style="font-size:11px;color:var(--text-muted);">${esc(l.conference_name||'')}</span>
        ${l.category && step !== 2 ? `<span style="font-size:10px;padding:1px 5px;border-radius:8px;margin-left:4px;background:${l.category==='partner'?'var(--blue-bg)':l.category==='referral'?'#fef3c7':'var(--green-bg)'};color:${l.category==='partner'?'var(--primary)':l.category==='referral'?'#92400e':'var(--green)'};">${esc(l.category)}</span>` : ''}
        ${replied && step !== 4 ? `<br><button class="btn btn-outline" style="padding:1px 6px;font-size:10px;margin-top:2px;" onclick="clearLeadReply('${esc(l.id)}')" title="Undo reply">Undo reply</button>` : ''}</td>`;
    }

    // Actions column
    let actionsCell;
    if (step === 4) {
      actionsCell = `<td style="text-align:center;"><button class="btn btn-secondary" style="padding:2px 6px;font-size:10px;color:var(--red);" onclick="event.stopPropagation();deleteConfLead('${esc(l.id)}')" title="Delete">\u2715</button></td>`;
    } else {
      actionsCell = `<td style="text-align:center;white-space:nowrap;">
        <button class="btn btn-secondary" style="padding:3px 6px;font-size:11px;color:${l.user_note ? '#d97706' : 'var(--text-muted)'};" onclick="openNoteEditor('${esc(l.id)}',this)" title="${l.user_note ? 'Edit note' : 'Add note'}">${icon('edit',11)}</button>
        <button class="btn btn-secondary" style="padding:3px 8px;font-size:11px;color:var(--red);" onclick="deleteConfLead('${esc(l.id)}')" title="Delete this conference lead.">✕</button>
      </td>`;
    }

    return `<tr data-id="${esc(l.id)}" ${rowClick}>
      <td><input type="checkbox" class="conf-check" value="${esc(l.id)}" onclick="event.stopPropagation()"></td>
      ${nameCell}
      <td style="font-size:12px;">${esc(l.email||'')}${l.cc ? `<br><span style="font-size:10px;color:var(--text-muted);">CC: ${esc(l.cc)}</span>` : ''}</td>
      <td style="font-size:12px;">${l.company ? esc(l.company) : '<span style="color:var(--text-muted)"> - </span>'}<br><span style="color:var(--text-muted);">${esc(l.title||'')}</span></td>
      ${contextCols}
      ${actionsCell}
    </tr>`;
  }).join('');
  // Pagination controls
  renderConfPagination();
}

function renderConfPagination() {
  let el = document.getElementById('conf-pagination');
  if (!el) {
    // Create pagination container after the table wrap
    el = document.createElement('div');
    el.id = 'conf-pagination';
    el.style.cssText = 'display:flex;align-items:center;justify-content:center;gap:10px;padding:8px 0;';
    const wrap = document.getElementById('conf-table-wrap');
    if (wrap && wrap.parentNode) wrap.parentNode.insertBefore(el, wrap.nextSibling);
  }
  const totalPages = Math.ceil(confLeadsTotal / CONF_LEADS_PAGE_SIZE);
  if (totalPages <= 1) { el.innerHTML = ''; return; }
  const start = confLeadsPage * CONF_LEADS_PAGE_SIZE + 1;
  const end = Math.min(start + confLeads.length - 1, confLeadsTotal);
  el.innerHTML = `
    <button class="btn btn-outline btn-sm" onclick="confLeadsPageNav(-1)" ${confLeadsPage === 0 ? 'disabled' : ''} style="font-size:11px;">&#8592; Prev</button>
    <span style="font-size:11px;color:var(--text-muted);">Showing ${start}\u2013${end} of ${confLeadsTotal}</span>
    <button class="btn btn-outline btn-sm" onclick="confLeadsPageNav(1)" ${confLeadsPage >= totalPages - 1 ? 'disabled' : ''} style="font-size:11px;">Next &#8594;</button>
  `;
}

// ── Note editor popover ────────────────────────────────────────────────────
let _noteEditorLeadId = null;

function openNoteEditor(leadId, anchorEl) {
  _noteEditorLeadId = leadId;
  const ed = document.getElementById('conf-note-editor');
  const ta = document.getElementById('conf-note-text');
  const lead = confLeads.find(l => l.id === leadId);
  ta.value = (lead && lead.user_note) || '';
  ed.classList.remove('hidden');
  // Position near the anchor element
  const rect = anchorEl.getBoundingClientRect();
  const scrollY = window.scrollY || document.documentElement.scrollTop;
  const scrollX = window.scrollX || document.documentElement.scrollLeft;
  ed.style.top = (rect.bottom + scrollY + 4) + 'px';
  ed.style.left = Math.min(rect.left + scrollX, window.innerWidth - 340) + 'px';
  ta.focus();
}

function closeNoteEditor() {
  _noteEditorLeadId = null;
  document.getElementById('conf-note-editor').classList.add('hidden');
}

async function saveLeadNote() {
  if (!_noteEditorLeadId) return;
  const leadId = _noteEditorLeadId;
  const notes = document.getElementById('conf-note-text').value.trim();
  const saveBtn = document.querySelector('[onclick^="saveLeadNote"]');
  if (saveBtn) { saveBtn.disabled = true; saveBtn.style.opacity = '0.6'; }
  try {
    const updated = await api(`/conference/leads/${leadId}/notes`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ notes: notes || null }),
    });
    // Update local array in-place
    const idx = confLeads.findIndex(l => l.id === leadId);
    if (idx !== -1) confLeads[idx] = { ...confLeads[idx], ...updated };
    closeNoteEditor();
    renderConfLeads();
  } catch (e) {
    console.error('saveLeadNote:', e);
    toast('Failed to save note.', 'error');
  } finally {
    if (saveBtn) { saveBtn.disabled = false; saveBtn.style.opacity = ''; }
  }
}

// Close note editor on Escape or click outside
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape' && _noteEditorLeadId) closeNoteEditor();
});
document.addEventListener('mousedown', function(e) {
  if (!_noteEditorLeadId) return;
  const ed = document.getElementById('conf-note-editor');
  if (ed && !ed.contains(e.target) && !e.target.closest('[onclick*="openNoteEditor"]')) {
    closeNoteEditor();
  }
});

let _confAnalyticsData = null;

async function loadConfAnalytics() {
    try {
        const conf = (confCurrentEvent || '');
        const params = conf ? `?conference_name=${encodeURIComponent(conf)}` : '';
        const data = await api(`/conference/analytics${params}`);
        _confAnalyticsData = data;
        const el = document.getElementById('conf-analytics');
        if (!el) return;

        const replyPct = data.total_replied > 0 ? ` (${data.reply_rate?.toFixed(0) || 0}%)` : '';
        // Build cards dynamically: Total Leads + per-email-step cards + Replied + Meetings
        const stepsDrafted = data.steps_drafted || [];
        const cards = [['Total Leads', data.total_leads, 'var(--text)', '']];
        stepsDrafted.forEach(s => {
            cards.push([s.label + ' Drafted', s.drafted, 'var(--blue)', '']);
        });
        cards.push(['Replied' + replyPct, data.total_replied, 'var(--green)', 'onclick="toggleConfReplyBreakdown()"']);
        cards.push(['Meetings', data.meetings_booked, 'var(--accent)', '']);

        const colCount = cards.length;
        el.innerHTML = `
            <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;align-items:stretch;">
                ${cards.map(([l,v,c,click]) => `<div ${click} style="${click ? 'cursor:pointer;' : ''}background:var(--surface);border:1px solid var(--border);padding:10px;text-align:center;border-radius:var(--r-sm);display:flex;flex-direction:column;justify-content:center;">
                    <div style="font-size:1.2rem;font-weight:600;color:${c};">${v}</div>
                    <div style="font-size:10px;color:var(--text-muted);margin-top:3px;text-transform:uppercase;letter-spacing:0.07em;">${l}</div>
                </div>`).join('')}
            </div>
            <div id="conf-reply-breakdown" class="hidden" style="margin-top:10px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r-sm);padding:12px;"></div>
        `;
    } catch(e) { console.error('loadConfAnalytics:', e); }
}

function toggleConfReplyBreakdown() {
    const el = document.getElementById('conf-reply-breakdown');
    if (!el) return;
    if (!el.classList.contains('hidden')) { el.classList.add('hidden'); return; }
    const data = _confAnalyticsData;
    if (!data || !data.replied_leads || !data.replied_leads.length) {
        el.innerHTML = '<span style="color:var(--text-muted);font-size:12px;">No replies yet.</span>';
        el.classList.remove('hidden');
        return;
    }

    const replied = data.replied_leads;
    const total = replied.length;
    // Build stages dynamically from the data
    const byStage = {};
    replied.forEach(r => {
        const stage = r.reply_stage || 1;
        if (!byStage[stage]) byStage[stage] = [];
        byStage[stage].push(r);
    });
    const stages = Object.keys(byStage).map(Number).sort((a, b) => a - b);

    // Stage percentage summary bar
    let html = '<div style="font-size:12px;">';
    html += '<div style="display:flex;gap:12px;margin-bottom:12px;">';
    stages.forEach(stage => {
        const leads = byStage[stage];
        const pct = total > 0 ? ((leads.length / total) * 100).toFixed(0) : 0;
        html += `<div style="flex:1;text-align:center;padding:8px;background:var(--surface2,var(--surface));border-radius:var(--r-sm);">
            <div style="font-size:1rem;font-weight:600;color:var(--green);">${pct}%</div>
            <div style="font-size:10px;color:var(--text-muted);margin-top:2px;">Email ${stage} (${leads.length})</div>
        </div>`;
    });
    html += '</div>';

    // Lead details per stage
    stages.forEach(stage => {
        const leads = byStage[stage];
        if (!leads.length) return;
        html += `<div style="margin-bottom:10px;">
            <div style="font-weight:600;color:var(--green);margin-bottom:4px;">Email ${stage} replies (${leads.length})</div>`;
        leads.forEach(l => {
            const date = l.replied_at ? new Date(l.replied_at).toLocaleDateString('en-GB', {day:'2-digit', month:'short'}) : '';
            html += `<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid var(--border);">
                <span>${esc(l.name)} <span style="color:var(--text-muted);">${esc(l.company)}</span></span>
                <span style="color:var(--text-muted);font-size:11px;">${date}</span>
            </div>`;
        });
        html += '</div>';
    });
    html += '</div>';
    el.innerHTML = html;
    el.classList.remove('hidden');
}

async function toggleMeetingBooked(leadId) {
    try {
        await api(`/conference/leads/${leadId}/meeting-booked`, { method: 'PATCH' });
        const idx = confLeads.findIndex(l => l.id === leadId);
        if (idx !== -1) confLeads[idx].meeting_booked = !confLeads[idx].meeting_booked;
        loadConfAnalytics();
    } catch (e) {
        toast('Failed to toggle meeting status: ' + e.message, 'error');
        renderConfLeads();
    }
}

function filterConfLeads() {
  const q = (document.getElementById('conf-search-input')?.value || '').toLowerCase().trim();
  document.querySelectorAll('#conf-tbody tr[data-id]').forEach(row => {
    row.style.display = !q || row.textContent.toLowerCase().includes(q) ? '' : 'none';
  });
}

function _fmtTokenCost(tokens) {
  if (!tokens) return '';
  const inp = tokens.input || 0;
  const out = tokens.output || 0;
  // Claude Sonnet pricing: $3/M input, $15/M output
  const cost = (inp * 3 + out * 15) / 1_000_000;
  return ` · ${(inp+out).toLocaleString()} tokens (~$${cost.toFixed(3)})`;
}

function confToggleAll(cb) {
  document.querySelectorAll('.conf-check').forEach(c => c.checked = cb.checked);
}

function getSelectedConfIds() {
  return [...document.querySelectorAll('.conf-check:checked')].map(c => c.value);
}

async function deleteSelectedConf() {
  const ids = getSelectedConfIds();
  if (!ids.length) { toast('Select leads to delete first.', 'warning'); return; }
  if (!await customConfirm(`Delete ${ids.length} selected lead(s)?`, {destructive: true})) return;
  try {
    await api('/conference/leads/bulk-delete', { method: 'POST', body: JSON.stringify({ ids }) });
    toast(`${ids.length} lead(s) deleted.`, 'success');
    await loadConferenceLeads();
  } catch (e) { toast('Delete failed: ' + e.message, 'error'); }
}

function confFileChanged() {
  const file = document.getElementById('conf-file-input').files[0];
  const content = document.getElementById('conf-dropzone-content');
  if (file) {
    content.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;gap:8px;">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--green)" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
      <span style="color:var(--text);font-weight:500;">${file.name}</span>
      <span style="color:var(--text-muted);font-size:11px;">(${(file.size/1024).toFixed(1)} KB)</span>
    </div>`;
  } else {
    content.innerHTML = `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="display:block;margin:0 auto 6px;opacity:0.5;"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
      Drop file here or <span style="color:var(--primary);font-weight:500;">browse</span>`;
  }
}

function confDropFile(event) {
  const dt = event.dataTransfer;
  if (dt.files.length) {
    document.getElementById('conf-file-input').files = dt.files;
    confFileChanged();
  }
}

async function importConferenceLeads() {
  const name = document.getElementById('conf-name-input').value.trim();
  const file = document.getElementById('conf-file-input').files[0];
  const status = document.getElementById('conf-import-status');
  if (!name) { toast('Enter a conference name', 'error'); return; }
  if (!file) { toast('Select a file', 'error'); return; }
  status.textContent = 'Importing…';
  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch(`/api/conference/import?conference_name=${encodeURIComponent(name)}`, {
      method: 'POST',
      headers: _authToken ? { 'Authorization': `Bearer ${_authToken}` } : {},
      body: form,
    });
    if (!res.ok) { const e = await res.json(); throw new Error(e.detail || res.statusText); }
    const data = await res.json();
    status.textContent = `✓ ${data.imported} imported, ${data.skipped} skipped`;
    toast(`${data.imported} leads imported`);
    document.getElementById('conf-file-input').value = '';
    confFileChanged();
    await refreshConfFilter();
    confCurrentEvent = name;
    await loadConferenceLeads(true);
  } catch(e) {
    status.textContent = '';
    toast('Import failed: ' + e.message, 'error');
  }
}

async function enrichOneLead(leadId) {
  const btn = document.querySelector(`button[onclick="enrichOneLead('${leadId}')"]`);
  if (btn) { btn.disabled = true; btn.textContent = 'Enriching…'; }
  try {
    const updated = await api(`/conference/enrich/${leadId}`, { method: 'POST' });
    const idx = confLeads.findIndex(l => l.id === leadId);
    if (idx !== -1) confLeads[idx] = updated;
    renderConfLeads();
    toast('Lead enriched');
  } catch(e) {
    toast('Enrich failed: ' + e.message, 'error');
    if (btn) { btn.disabled = false; btn.textContent = 'Enrich'; }
  }
}

function setConfBulkBtns(disabled) {
  document.querySelectorAll('.conf-bulk-btn').forEach(b => b.disabled = disabled);
}

async function enrichAllConference() {
  const status = document.getElementById('conf-bulk-status');
  status.textContent = 'Enriching…';
  setConfBulkBtns(true);
  try {
    const conf = (confCurrentEvent || '');
    const url = conf ? `/conference/enrich-all?conference_name=${encodeURIComponent(conf)}` : '/conference/enrich-all';
    const data = await api(url, { method: 'POST' });
    status.textContent = `✓ Enriched ${data.enriched}, failed ${data.failed}`;
    await loadConferenceLeads();
    toast(`Enriched ${data.enriched} leads`);
  } catch(e) { status.textContent = ''; toast('Enrichment failed: ' + e.message, 'error'); } finally { setConfBulkBtns(false); }
}

async function generateOne(leadId) {
  const context = document.getElementById('conf-context').value.trim();
  if (!context) { toast('Add outreach context above first', 'error'); return; }
  const btn = document.querySelector(`button[onclick="generateOne('${leadId}')"]`);
  if (btn) { btn.disabled = true; btn.textContent = 'Generating…'; }
  try {
    const updated = await api('/conference/generate', { method: 'POST', body: JSON.stringify({ lead_id: leadId, context }) });
    const idx = confLeads.findIndex(l => l.id === leadId);
    if (idx !== -1) confLeads[idx] = updated;
    renderConfLeads();
    toast('Email generated');
  } catch(e) {
    toast('Generate failed: ' + e.message, 'error');
    if (btn) { btn.disabled = false; btn.textContent = 'Generate'; }
  }
}

async function generateAllConference() {
  const context = document.getElementById('conf-context').value.trim();
  if (!context) { toast('Add outreach context above first', 'error'); return; }
  const selected = getSelectedConfIds();
  let ids;
  if (selected.length) {
    ids = selected;
  } else {
    ids = confLeads.map(l => l.id);
  }
  if (!ids.length) { toast('No leads to generate for. Import leads first.', 'info'); return; }
  const withContent = ids.filter(id => { const l = confLeads.find(x => x.id === id); return l && l.generated_subject; });
  if (withContent.length && !await customConfirm(`${withContent.length} of ${ids.length} lead(s) already have generated emails. Overwrite them?`)) return;
  if (!withContent.length && ids.length > 5 && !await customConfirm(`Generate AI emails for ${ids.length} leads? This uses API credits.`)) return;
  const status = document.getElementById('conf-bulk-status');
  status.textContent = `Generating ${ids.length} emails…`;
  setConfBulkBtns(true);
  try {
    const confBooking = document.getElementById('conf-booking-link')?.checked ?? true;
    const data = await api('/conference/generate-all', { method: 'POST', body: JSON.stringify({ lead_ids: ids, context, include_booking_link: confBooking }) });
    const cost = data.tokens ? _fmtTokenCost(data.tokens) : '';
    status.textContent = `✓ Generated ${data.generated}, failed ${data.failed}${cost}`;
    await loadConferenceLeads();
    toast(`Generated ${data.generated} emails`);
  } catch(e) { status.textContent = ''; toast('Generation failed: ' + e.message, 'error'); } finally { setConfBulkBtns(false); }
}

async function deleteConfLead(leadId) {
  const lead = confLeads.find(l => l.id === leadId);
  const name = lead ? `${lead.first_name || ''} ${lead.last_name || ''}`.trim() || lead.email : 'this lead';
  if (!await customConfirm(`Delete ${name}?`, {destructive: true})) return;
  try {
    await api(`/conference/leads/${leadId}`, { method: 'DELETE' });
    confLeads = confLeads.filter(l => l.id !== leadId);
    renderConfLeads();
    loadConfAnalytics();
  } catch(e) { toast('Delete failed: ' + e.message, 'error'); }
}

function openConfModal(leadId) {
  const lead = confLeads.find(l => l.id === leadId);
  if (!lead) return;
  confModalLeadId = leadId;
  document.getElementById('conf-modal-name').textContent = `${lead.first_name||''} ${lead.last_name||''}, ${lead.company||lead.email}`;
  document.getElementById('conf-modal-subject').value = lead.generated_subject || '';
  document.getElementById('conf-modal-body').value = lead.generated_body || '';
  document.getElementById('conf-email-modal').classList.remove('hidden');
  document.getElementById('conf-email-modal').style.display = 'flex';
}

function closeConfModal() {
  const m = document.getElementById('conf-email-modal');
  m.classList.add('hidden');
  m.style.display = '';
  confModalLeadId = null;
}

async function saveConfEmail() {
  if (!confModalLeadId) return;
  const subject = document.getElementById('conf-modal-subject').value;
  const body = document.getElementById('conf-modal-body').value;
  try {
    const updated = await api(`/conference/leads/${confModalLeadId}/email`, {
      method: 'PATCH',
      body: JSON.stringify({ subject, body }),
    });
    const idx = confLeads.findIndex(l => l.id === confModalLeadId);
    if (idx !== -1) confLeads[idx] = updated;
    closeConfModal();
    renderConfLeads();
    toast('Email saved');
  } catch(e) { toast('Save failed: ' + e.message, 'error'); }
}

async function sendConfDraft() {
  if (!confModalLeadId) return;
  const leadId = confModalLeadId;
  await saveConfEmail();
  await draftOne(leadId);
}

async function draftOne(leadId) {
  const inboxId = document.getElementById('conf-inbox-select').value || 'outlook';
  const globalCc = (document.getElementById('conf-global-cc')?.value || '').trim();
  // Events-sig-fix 2026-04-09: the opening greeting ("Hi John,") is always
  // preserved  -  there is no toggle to strip it. The only configurable is
  // the trailing closer, via "Include AI closer". When unchecked we pass
  // strip_signoff=true so the backend removes "Talk soon,\nName" and lets
  // Outlook's auto-signature handle sign-off alone.
  const includeCloser = document.getElementById('conf-include-ai-closer')?.checked !== false;
  const btn = document.querySelector(`button[onclick="draftOne('${leadId}')"]`);
  if (btn) { btn.disabled = true; btn.textContent = 'Drafting…'; }
  try {
    let url = `/conference/draft/${leadId}?inbox_id=${encodeURIComponent(inboxId)}`;
    if (globalCc) url += `&cc=${encodeURIComponent(globalCc)}`;
    if (!includeCloser) url += '&strip_signoff=true';
    const updated = await api(url, { method: 'POST' });
    const idx = confLeads.findIndex(l => l.id === leadId);
    if (idx !== -1) confLeads[idx] = updated;
    renderConfLeads();
    toast('Draft created');
  } catch(e) {
    toast('Draft failed: ' + e.message, 'error');
    if (btn) { btn.disabled = false; btn.textContent = 'Draft'; }
  }
}

async function draftAllConference() {
  const inboxId = document.getElementById('conf-inbox-select').value || 'outlook';
  const globalCc = (document.getElementById('conf-global-cc')?.value || '').trim();
  const includeCloser = document.getElementById('conf-include-ai-closer')?.checked !== false;
  const conf = (confCurrentEvent || '');
  const status = document.getElementById('conf-bulk-status');
  status.textContent = 'Creating drafts…';
  setConfBulkBtns(true);
  try {
    let url = `/conference/draft-all?inbox_id=${encodeURIComponent(inboxId)}`;
    if (conf) url += `&conference_name=${encodeURIComponent(conf)}`;
    if (globalCc) url += `&cc=${encodeURIComponent(globalCc)}`;
    if (!includeCloser) url += '&strip_signoff=true';
    const data = await api(url, { method: 'POST' });
    status.textContent = `✓ Drafted ${data.drafted}, failed ${data.failed}`;
    await loadConferenceLeads();
    toast(`${data.drafted} drafts created`);
  } catch(e) { status.textContent = ''; toast('Draft failed: ' + e.message, 'error'); } finally { setConfBulkBtns(false); }
}

// ── Pre-Conference Outreach (Stepper UI) ────────────────────────────────────
let preconfLeads = [];
let preconfLeadsPage = 0;
let preconfLeadsTotal = 0;
const PRECONF_LEADS_PAGE_SIZE = 100;
let _apolloPreviewResults = [];
let preconfActiveStep = 1;
let preconfCurrentEvent = '';

// ── Stepper navigation ─────────────────────────────────────────────────────
function setPreconfStep(n) {
  preconfActiveStep = n;
  for (let i = 1; i <= 4; i++) {
    const panel = document.getElementById('preconf-step-' + i);
    if (panel) { panel.classList.toggle('hidden', i !== n); panel.style.display = i === n ? '' : 'none'; }
    const dot = document.getElementById('preconf-dot-' + i);
    if (dot) {
      const isActive = i === n;
      dot.style.background = isActive ? 'var(--navy)' : 'var(--border)';
      dot.style.color = isActive ? '#fff' : 'var(--text-muted)';
    }
    // Update label weight
    const step = document.querySelector(`.preconf-step[data-step="${i}"]`);
    if (step) {
      const label = step.querySelector('.preconf-step-label');
      if (label) {
        label.style.fontWeight = i === n ? '600' : '500';
        label.style.color = i === n ? 'var(--navy)' : 'var(--text-muted)';
      }
    }
  }
  // Hide lead table on Step 1 (Event Setup) — it's not relevant there
  const leadsPanel = document.getElementById('preconf-leads-panel');
  if (leadsPanel) leadsPanel.style.display = n === 1 ? 'none' : '';
  if (n !== 1) renderPreConfLeads();
  updatePreconfStepperBadges();
}

function updatePreconfStepperBadges() {
  // Just keep numbered dots — active step is highlighted in setPreconfStep()
}

// ── Event management ────────────────────────────────────────────────────────
async function loadPreConference() {
  try {
    await Promise.all([loadPreconfEventList(), loadInboxes_conf(), loadPreConfSeqDesign()]);
    // Outlook / inbox status
    try {
      const s = await api('/outlook/auth/status');
      const badge = document.getElementById('preconf-outlook-status');
      const inboxBar = document.getElementById('preconf-inbox-status');
      if (badge) {
        if (s.authenticated) {
          badge.style.background = '#dcfce7'; badge.style.color = '#15803d';
          badge.textContent = '\u25CF Outlook connected' + (s.account ? ' (' + s.account + ')' : '');
        } else {
          badge.style.background = '#fee2e2'; badge.style.color = '#b91c1c';
          badge.textContent = '\u25CF Outlook not connected';
        }
      }
      if (inboxBar) {
        if (s.authenticated) {
          inboxBar.innerHTML = `<span style="color:var(--green);">\u25CF</span> ${esc(s.account || 'Connected')}`;
        } else {
          inboxBar.innerHTML = '<span style="color:var(--red);">\u25CF</span> Not connected';
        }
      }
    } catch(_) {
      const inboxBar = document.getElementById('preconf-inbox-status');
      if (inboxBar) inboxBar.innerHTML = '<span style="color:var(--text-muted);">\u25CF</span> Unknown';
    }
    // LinkedIn status — check PB connect phantom health
    try {
      const h = await api('/health/full');
      const liBar = document.getElementById('preconf-linkedin-status');
      if (liBar) {
        const liOk = h.checks?.linkedin_worker?.status === 'ok';
        if (liOk) {
          liBar.innerHTML = '<span style="color:var(--green);">\u25CF</span> PhantomBuster connected';
        } else {
          liBar.innerHTML = '<span style="color:var(--red);">\u25CF</span> Worker not running';
        }
      }
    } catch(_) {
      const liBar = document.getElementById('preconf-linkedin-status');
      if (liBar) liBar.innerHTML = '<span style="color:var(--text-muted);">\u25CF</span> Unknown';
    }
    if (preconfCurrentEvent) await loadPreConfLeads(true);
    setPreconfStep(preconfActiveStep);
  } catch(e) { toast('Failed to load pre-conference data: ' + e.message, 'error'); }
}

async function loadPreconfEventList() {
  try {
    const events = await api('/conference/conferences?outreach_type=pre');
    const sel = document.getElementById('preconf-event-select');
    const prev = sel.value || preconfCurrentEvent;
    sel.innerHTML = '<option value="">Select an event...</option>' + events.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
    if (prev && events.includes(prev)) { sel.value = prev; preconfCurrentEvent = prev; }
  } catch(_) {}
}

async function onPreconfEventChange() {
  const sel = document.getElementById('preconf-event-select');
  preconfCurrentEvent = sel.value;
  if (!preconfCurrentEvent) {
    preconfLeads = [];
    renderPreConfLeads();
    return;
  }
  // Clear stale fields before loading new event data
  document.getElementById('preconf-name-input').value = preconfCurrentEvent;
  document.getElementById('preconf-date-input').value = '';
  document.getElementById('preconf-context').value = '';
  try {
    const ctx = await api('/settings/pre-conference-context?event_name=' + encodeURIComponent(preconfCurrentEvent));
    if (ctx.event_date) document.getElementById('preconf-date-input').value = ctx.event_date;
    if (ctx.context) document.getElementById('preconf-context').value = ctx.context;
  } catch(_) {}
  await loadPreConfLeads(true);
  updatePreconfStepperBadges();
}

async function createNewPreconfEvent() {
  const name = await customPrompt('Event name (e.g. ADIPEC 2026):', '');
  if (!name || !name.trim()) return;
  preconfCurrentEvent = name.trim();
  document.getElementById('preconf-name-input').value = preconfCurrentEvent;
  // Add to dropdown if not present
  const sel = document.getElementById('preconf-event-select');
  let found = false;
  for (const opt of sel.options) { if (opt.value === preconfCurrentEvent) { found = true; break; } }
  if (!found) {
    const opt = document.createElement('option');
    opt.value = preconfCurrentEvent;
    opt.textContent = preconfCurrentEvent;
    sel.appendChild(opt);
  }
  sel.value = preconfCurrentEvent;
  preconfLeads = [];
  renderPreConfLeads();
  setPreconfStep(1);
  // Auto-save to backend so the event persists across refreshes
  await savePreconfEvent();
  toast('Event created.', 'info');
}

async function savePreconfEvent() {
  const name = document.getElementById('preconf-name-input').value.trim();
  const date = document.getElementById('preconf-date-input').value;
  const context = document.getElementById('preconf-context').value.trim();
  if (!name) { toast('Enter an event name.', 'error'); return; }
  const isNew = preconfCurrentEvent !== name;
  preconfCurrentEvent = name;
  try {
    await api('/settings/pre-conference-context', {
      method: 'POST',
      body: JSON.stringify({ event_name: name, event_date: date, context }),
    });
    // Add to dropdown if new, then select it
    const sel = document.getElementById('preconf-event-select');
    let found = false;
    for (const opt of sel.options) { if (opt.value === name) { found = true; break; } }
    if (!found) {
      const opt = document.createElement('option');
      opt.value = name; opt.textContent = name;
      sel.appendChild(opt);
    }
    sel.value = name;
    const status = document.getElementById('preconf-event-save-status');
    status.textContent = isNew ? 'Event created' : 'Saved';
    setTimeout(() => { status.textContent = ''; }, 3000);
    toast(isNew ? `Event "${name}" created` : 'Event updated');
    updatePreconfStepperBadges();
  } catch(e) { toast('Save failed: ' + e.message, 'error'); }
}

// ── Lead loading & contextual rendering ─────────────────────────────────────
async function loadPreConfLeads(resetPage) {
  if (resetPage) preconfLeadsPage = 0;
  const loading = document.getElementById('preconf-loading');
  loading.style.display = 'block';
  document.getElementById('preconf-empty').style.display = 'none';
  try {
    let url = '/conference/leads?outreach_type=pre&limit=' + PRECONF_LEADS_PAGE_SIZE + '&offset=' + (preconfLeadsPage * PRECONF_LEADS_PAGE_SIZE);
    if (preconfCurrentEvent) url += '&conference_name=' + encodeURIComponent(preconfCurrentEvent);
    const data = await api(url);
    preconfLeads = Array.isArray(data) ? data : (data.items || []);
    preconfLeadsTotal = (data && data.total != null) ? data.total : preconfLeads.length;
    renderPreConfLeads();
    updatePreconfStepperBadges();
  } catch(e) {
    loading.innerHTML = `<div style="color:var(--red)">Failed: ${esc(e.message)}</div>`;
  }
}
async function preconfLeadsPageNav(dir) {
  preconfLeadsPage = Math.max(0, preconfLeadsPage + dir);
  await loadPreConfLeads();
}

function renderPreConfLeads() {
  document.getElementById('preconf-loading').style.display = 'none';
  const empty = document.getElementById('preconf-empty');
  const wrap = document.getElementById('preconf-table-wrap');
  const count = document.getElementById('preconf-leads-count');

  if (!preconfLeads.length) {
    empty.style.display = 'block';
    wrap.style.display = 'none';
    count.textContent = '';
    return;
  }
  empty.style.display = 'none';
  wrap.style.display = '';
  const pcStart = preconfLeadsPage * PRECONF_LEADS_PAGE_SIZE + 1;
  const pcEnd = Math.min(pcStart + preconfLeads.length - 1, preconfLeadsTotal);
  const pcPageInfo = preconfLeadsTotal > PRECONF_LEADS_PAGE_SIZE ? ` (${pcStart}\u2013${pcEnd} of ${preconfLeadsTotal})` : '';
  count.textContent = `${preconfLeadsTotal} lead${preconfLeadsTotal !== 1 ? 's' : ''}${pcPageInfo}`;

  // Contextual columns based on active step
  const thead = document.getElementById('preconf-thead');
  const step = preconfActiveStep;
  let headerHtml = '<th style="width:32px;"><input type="checkbox" id="preconf-check-all" onchange="preconfToggleAll(this)"></th><th>Name</th><th>Email</th><th>Company / Title</th>';
  if (step === 2) headerHtml += '<th style="text-align:center;">Enriched</th>';
  if (step === 3) headerHtml += '<th>Generated Subject</th><th style="text-align:center;">View</th>';
  if (step === 4) headerHtml += '<th>Sequence</th><th style="text-align:center;">Status</th><th style="text-align:center;">Next</th>';
  headerHtml += '<th style="text-align:center;">Actions</th>';
  thead.innerHTML = '<tr>' + headerHtml + '</tr>';

  document.getElementById('preconf-tbody').innerHTML = preconfLeads.map(l => {
    const replied = !!l.replied_at;
    const rowBg = replied ? 'background:rgba(254, 226, 226, 0.18);' : '';
    let contextCols = '';

    if (step === 2) {
      contextCols = `<td style="text-align:center;">${l.enriched_at ? '<span style="color:var(--green);">\u2713</span>' : '<span style="color:var(--text-muted);">-</span>'}</td>`;
    }
    if (step === 3) {
      const subj3 = l.generated_subject || '';
      contextCols = `<td style="font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(subj3)}">${subj3 ? esc(subj3) : '<span style="color:var(--text-muted);">Not generated</span>'}</td>`;
      contextCols += `<td style="text-align:center;">${l.generated_sequence
        ? `<button class="btn btn-secondary" style="padding:2px 6px;font-size:11px;" onclick="openSeqModal('${esc(l.id)}')">View</button>`
        : '<span style="color:var(--text-muted);">-</span>'}</td>`;
    }
    if (step === 4) {
      // Build steps array from pre-conf sequence config for renderStepDots
      const seqSteps = preconfSeqSteps.map(s => ({
        day: s.day,
        type: s.type || 'email',
        label: s.preset !== 'custom' ? (_PRECONF_STEP_PRESETS[s.preset]?.label || s.desc) : s.desc
      }));
      const orchState = l.orch_state || null;

      if (orchState) {
        // Derive email sent count from drafted steps in generated_sequence
        let emailSentCount = 0;
        const seqData = l.generated_sequence || {};
        const draftedSteps = seqData._drafted_steps || [];
        // Count email steps that have been drafted (conference uses drafts, not Instantly)
        for (const sk of draftedSteps) {
          if (sk.startsWith('email')) emailSentCount++;
        }
        // Also count email1 if draft_created_at is set but not in _drafted_steps
        if (l.draft_created_at && !draftedSteps.includes('email1')) emailSentCount++;

        const dotsState = {
          enrolled_at: orchState.enrolled_at,
          current_step_index: orchState.current_step_index,
          status: orchState.status,
          email_sent_count: emailSentCount,
          linkedin_connect_sent: !!l.linkedin_connect_sent,
          linkedin_connect_queued: !!l.linkedin_connect_queued,
          linkedin_message_sent: !!l.linkedin_message_sent,
          linkedin_message_queued: !!l.linkedin_message_queued,
          linkedin_connected: false,
          engagement_score: orchState.engagement_score || 0,
          scheduled_next_at: orchState.scheduled_next_at,
        };
        const dots = renderStepDots(dotsState, seqSteps);
        const statusBdg = seqStatusBadge(orchState.status);
        const nextAt = seqFormatNext(orchState.scheduled_next_at);
        contextCols = `<td>${dots}</td><td style="text-align:center;">${statusBdg}</td><td style="text-align:center;">${nextAt}</td>`;
      } else {
        // No orch state yet — show contextual label
        const hasSeq = !!l.generated_sequence;
        const hasLi = !!l.linkedin_url;
        const label = !hasSeq ? 'Not generated' : !hasLi ? 'No LinkedIn URL' : 'Not launched';
        contextCols = `<td colspan="3" style="color:var(--text-muted);font-size:12px;">${label}</td>`;
      }
    }

    const rowClickable = step === 4 && l.generated_sequence;
    const rowClick = rowClickable ? `onclick="openSeqModal('${esc(l.id)}')" style="cursor:pointer;${rowBg}"` : `style="${rowBg}"`;
    return `<tr data-id="${esc(l.id)}" ${rowClick}>
      <td><input type="checkbox" class="preconf-check" value="${esc(l.id)}" onclick="event.stopPropagation()"></td>
      <td style="font-weight:500;">${l.linkedin_url ? `<a href="${esc(l.linkedin_url)}" target="_blank" rel="noopener" style="color:var(--navy);text-decoration:none;" title="Open LinkedIn profile" onclick="event.stopPropagation()">${esc(l.first_name||'')} ${esc(l.last_name||'')}</a>` : `${esc(l.first_name||'')} ${esc(l.last_name||'')}`}</td>
      <td style="font-size:12px;">${(!l.email || l.email.includes('@placeholder.local')) ? '<span style="color:var(--text-muted);">N/A</span>' : esc(l.email)}</td>
      <td style="font-size:12px;">${l.company ? esc(l.company) : '-'}<br><span style="color:var(--text-muted);">${esc(l.title||'')}</span></td>
      ${contextCols}
      <td style="text-align:center;"><button class="btn btn-secondary" style="padding:2px 6px;font-size:10px;color:var(--red);" onclick="event.stopPropagation();deletePreConfLead('${esc(l.id)}')">\u2715</button></td>
    </tr>`;
  }).join('');
  // Pagination controls
  renderPreConfPagination();
}

function renderPreConfPagination() {
  let el = document.getElementById('preconf-pagination');
  if (!el) {
    el = document.createElement('div');
    el.id = 'preconf-pagination';
    el.style.cssText = 'display:flex;align-items:center;justify-content:center;gap:10px;padding:8px 0;';
    const wrap = document.getElementById('preconf-table-wrap');
    if (wrap && wrap.parentNode) wrap.parentNode.insertBefore(el, wrap.nextSibling);
  }
  const totalPages = Math.ceil(preconfLeadsTotal / PRECONF_LEADS_PAGE_SIZE);
  if (totalPages <= 1) { el.innerHTML = ''; return; }
  const start = preconfLeadsPage * PRECONF_LEADS_PAGE_SIZE + 1;
  const end = Math.min(start + preconfLeads.length - 1, preconfLeadsTotal);
  el.innerHTML = `
    <button class="btn btn-outline btn-sm" onclick="preconfLeadsPageNav(-1)" ${preconfLeadsPage === 0 ? 'disabled' : ''} style="font-size:11px;">&#8592; Prev</button>
    <span style="font-size:11px;color:var(--text-muted);">Showing ${start}\u2013${end} of ${preconfLeadsTotal}</span>
    <button class="btn btn-outline btn-sm" onclick="preconfLeadsPageNav(1)" ${preconfLeadsPage >= totalPages - 1 ? 'disabled' : ''} style="font-size:11px;">Next &#8594;</button>
  `;
}

function filterPreConfLeads() {
  const q = (document.getElementById('preconf-search-input').value || '').toLowerCase();
  document.querySelectorAll('#preconf-tbody tr').forEach(tr => {
    tr.style.display = !q || tr.textContent.toLowerCase().includes(q) ? '' : 'none';
  });
}

function preconfToggleAll(el) {
  document.querySelectorAll('.preconf-check').forEach(cb => { cb.checked = el.checked; });
  const delBtn = document.getElementById('preconf-bulk-delete-btn');
  if (delBtn) delBtn.style.display = el.checked ? '' : 'none';
}

async function deleteSelectedPreConf() {
  const ids = [...document.querySelectorAll('.preconf-check:checked')].map(cb => cb.value);
  if (!ids.length) return;
  if (!await customConfirm(`Delete ${ids.length} lead(s)?`, {destructive: true})) return;
  try {
    await api('/conference/leads/bulk-delete', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ ids }) });
    await loadPreConfLeads();
    toast(`${ids.length} leads deleted`);
  } catch(e) { toast('Delete failed: ' + e.message, 'error'); }
}

// ── Step navigation ─────────────────────────────────────────────────────────
async function preconfNext(toStep) {
  // Auto-save current step before advancing
  if (preconfActiveStep === 1) {
    const name = document.getElementById('preconf-name-input').value.trim();
    if (!name) { toast('Enter an event name before continuing.', 'error'); return; }
    await savePreconfEvent();
  }
  if (toStep === 4) {
    try {
      const s = await api('/outlook/auth/status');
      if (!s.authenticated) {
        toast('Outlook is not connected. Connect it in the Inbox settings to push drafts.', 'warning');
      }
    } catch(_) {}
  }
  setPreconfStep(toStep);
}

// ── Lead source chooser ─────────────────────────────────────────────────────
function showPreconfSource(src) {
  const sources = ['apollo', 'salesnav', 'file'];
  for (const s of sources) {
    const panel = document.getElementById('preconf-src-' + s);
    const btn = document.getElementById('preconf-src-' + s + '-btn');
    if (panel) panel.style.display = s === src ? '' : 'none';
    if (btn) {
      btn.style.background = s === src ? 'var(--navy)' : 'var(--surface)';
      btn.style.color = s === src ? '#fff' : '';
      btn.style.borderColor = s === src ? 'var(--navy)' : 'var(--border)';
    }
  // Render region picker on first show
  if (src === 'salesnav' && !document.querySelector('.preconf-sn-region')) renderSalesNavRegions();
  }
}

// ── Sales Navigator import ──────────────────────────────────────────────────
// Filter-based Sales Nav search — builds URL from checkboxes, calls same backend
let _busySearchSalesNavPreConf = false;
async function searchSalesNavPreConf() {
  if (_busySearchSalesNavPreConf) return;
  const status = document.getElementById('preconf-salesnav-status');
  const confName = preconfCurrentEvent || document.getElementById('preconf-name-input').value.trim();
  if (!confName) { toast('Create or select an event first (Step 1).', 'error'); return; }

  // Collect filters from checkboxes
  const titles = document.getElementById('preconf-sn-titles').value.trim().split(',').map(s => s.trim()).filter(Boolean);
  const seniorities = [...document.querySelectorAll('.preconf-sn-seniority:checked')].map(cb => cb.value);
  const industries = [...document.querySelectorAll('.preconf-sn-industry:checked')].map(cb => cb.value);
  const regions = [...document.querySelectorAll('.preconf-sn-region:checked')].map(cb => cb.value);
  const companySizes = [...document.querySelectorAll('.preconf-sn-size:checked')].map(cb => cb.value);

  if (!titles.length && !seniorities.length && !industries.length && !regions.length) {
    toast('Select at least one filter.', 'error'); return;
  }

  _busySearchSalesNavPreConf = true;
  try {
    const url = buildSalesNavUrl({ titles, seniorities, industries, regions, companySizes });
    await _launchSalesNavImport(confName, url, status);
  } finally { _busySearchSalesNavPreConf = false; }
}

// URL paste fallback — same backend, user provides the URL directly
let _busyImportSalesNavUrlPreConf = false;
async function importSalesNavUrlPreConf() {
  if (_busyImportSalesNavUrlPreConf) return;
  const url = document.getElementById('preconf-salesnav-url').value.trim();
  const status = document.getElementById('preconf-salesnav-url-status');
  const confName = preconfCurrentEvent || document.getElementById('preconf-name-input').value.trim();
  if (!confName) { toast('Create or select an event first (Step 1).', 'error'); return; }
  if (!url || !url.includes('linkedin.com')) { toast('Paste a valid Sales Navigator search URL.', 'error'); return; }
  _busyImportSalesNavUrlPreConf = true;
  try { await _launchSalesNavImport(confName, url, status); }
  finally { _busyImportSalesNavUrlPreConf = false; }
}

// Shared: launch PB phantom with a Sales Nav URL, then poll for results
async function _launchSalesNavImport(confName, searchUrl, statusEl) {
  statusEl.textContent = 'Launching LinkedIn search...';
  let launchData;
  try {
    launchData = await api('/conference/import-salesnav', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        conference_name: confName,
        event_date: document.getElementById('preconf-date-input').value || undefined,
        search_url: searchUrl,
        max_results: parseInt(document.getElementById('preconf-salesnav-max')?.value, 10) || 100,
      }),
    });
  } catch (e) {
    statusEl.textContent = '';
    toast('Sales Nav search failed: ' + e.message, 'error');
    return;
  }

  const containerId = launchData.container_id;
  if (!containerId) {
    statusEl.textContent = '';
    toast('Sales Nav launch returned no container ID.', 'error');
    return;
  }

  statusEl.textContent = 'Searching LinkedIn (polling for results)...';
  const eventDate = document.getElementById('preconf-date-input').value || '';
  const maxResults = launchData.max_results || 100;

  // Poll every 10 seconds for up to ~10 minutes.
  // Wrapped in a Promise so the caller's `await` (and its `finally` block
  // that clears the busy flag) doesn't resolve until polling is done.
  const MAX_POLLS = 60;
  let polls = 0;

  await new Promise((resolve) => {
    const pollTimer = setInterval(async () => {
      polls++;
      try {
        const qs = new URLSearchParams({
          container_id: containerId,
          conference_name: confName,
          max_results: String(maxResults),
        });
        if (eventDate) qs.set('event_date', eventDate);

        const result = await api('/conference/import-salesnav/status?' + qs.toString());

        if (result.status === 'running') {
          statusEl.textContent = `Searching LinkedIn (polling${'.'.repeat((polls % 3) + 1)})`;
          if (polls >= MAX_POLLS) {
            clearInterval(pollTimer);
            statusEl.textContent = 'Timed out waiting for results. The phantom may still be running — try again shortly.';
            toast('Sales Nav search timed out.', 'error');
            resolve();
          }
          return; // keep polling
        }

        clearInterval(pollTimer);

        if (result.status === 'done') {
          statusEl.textContent = `\u2713 Imported ${result.imported}, skipped ${result.skipped}, failed ${result.failed}`;
          preconfCurrentEvent = confName;
          await loadPreconfEventList();
          document.getElementById('preconf-event-select').value = confName;
          await loadPreConfLeads(true);
          toast(`${result.imported} leads imported from Sales Navigator`);
        } else {
          // error or unknown status
          statusEl.textContent = '';
          toast('Sales Nav search failed: ' + (result.detail || 'Unknown error'), 'error');
        }
        resolve();
      } catch (e) {
        clearInterval(pollTimer);
        statusEl.textContent = '';
        toast('Sales Nav status check failed: ' + e.message, 'error');
        resolve();
      }
    }, 10000);
  });
}

function showSalesNavTab(tab) {
  document.getElementById('preconf-sn-filters').style.display = tab === 'filters' ? '' : 'none';
  document.getElementById('preconf-sn-url').style.display = tab === 'url' ? '' : 'none';
  document.getElementById('preconf-sn-tab-filters').className = tab === 'filters' ? 'btn btn-primary btn-sm' : 'btn btn-secondary btn-sm';
  document.getElementById('preconf-sn-tab-url').className = tab === 'url' ? 'btn btn-primary btn-sm' : 'btn btn-secondary btn-sm';
}

// ── Apollo search ───────────────────────────────────────────────────────────
let _busySearchApolloPreConf = false;
async function searchApolloPreConf() {
  if (_busySearchApolloPreConf) return;
  const status = document.getElementById('preconf-apollo-status');
  status.textContent = 'Searching...';
  const confName = preconfCurrentEvent || document.getElementById('preconf-name-input').value.trim();
  if (!confName) { status.textContent = ''; toast('Create or select an event first (Step 1).', 'error'); return; }

  const splitTrim = v => v.split(',').map(s => s.trim()).filter(Boolean);
  const req = {
    conference_name: confName,
    event_date: document.getElementById('preconf-date-input').value || undefined,
    titles: splitTrim(document.getElementById('preconf-apollo-titles').value),
    seniorities: splitTrim(document.getElementById('preconf-apollo-seniorities').value),
    industries: splitTrim(document.getElementById('preconf-apollo-industries').value),
    locations: splitTrim(document.getElementById('preconf-apollo-locations').value),
    employee_ranges: splitTrim(document.getElementById('preconf-apollo-employees').value),
  };
  for (const k of Object.keys(req)) { if (Array.isArray(req[k]) && !req[k].length) delete req[k]; }

  _busySearchApolloPreConf = true;
  try {
    const data = await api('/conference/import-apollo', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(req) });
    _apolloPreviewResults = data.results || [];
    status.textContent = `${_apolloPreviewResults.length} results found`;
    renderApolloPreview();
  } catch(e) { status.textContent = ''; toast('Apollo search failed: ' + e.message, 'error'); }
  finally { _busySearchApolloPreConf = false; }
}

function renderApolloPreview() {
  const wrap = document.getElementById('preconf-apollo-preview');
  const tbody = document.getElementById('preconf-apollo-tbody');
  if (!_apolloPreviewResults.length) { wrap.classList.add('hidden'); return; }
  wrap.classList.remove('hidden');
  tbody.innerHTML = _apolloPreviewResults.map(p => `<tr>
    <td><input type="checkbox" class="preconf-apollo-check" value="${esc(p.apollo_id)}" checked></td>
    <td>${esc(p.first_name||'')} ${esc(p.last_name||'')}</td>
    <td style="font-size:11px;">${esc(p.title||'')}</td>
    <td style="font-size:11px;">${esc(p.company||'')}</td>
    <td style="font-size:11px;">${esc(p.city||'')}${p.country ? ', ' + esc(p.country) : ''}</td>
  </tr>`).join('');
}

function preconfApolloToggleAll(el) {
  document.querySelectorAll('.preconf-apollo-check').forEach(cb => { cb.checked = el.checked; });
}

let _busyConfirmApolloImport = false;
async function confirmApolloImport() {
  if (_busyConfirmApolloImport) return;
  const selected = [...document.querySelectorAll('.preconf-apollo-check:checked')].map(cb => cb.value);
  if (!selected.length) { toast('Select at least one contact.', 'error'); return; }
  const confName = preconfCurrentEvent || document.getElementById('preconf-name-input').value.trim();
  const status = document.getElementById('preconf-apollo-status');
  status.textContent = `Importing ${selected.length} contacts...`;
  _busyConfirmApolloImport = true;
  try {
    const data = await api('/conference/import-apollo/confirm', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({
      conference_name: confName, event_date: document.getElementById('preconf-date-input').value || undefined, apollo_ids: selected,
    })});
    status.textContent = `\u2713 Imported ${data.imported}, skipped ${data.skipped}`;
    document.getElementById('preconf-apollo-preview').classList.add('hidden');
    _apolloPreviewResults = [];
    preconfCurrentEvent = confName;
    await loadPreconfEventList();
    document.getElementById('preconf-event-select').value = confName;
    await loadPreConfLeads(true);
    toast(`${data.imported} leads imported`);
  } catch(e) { status.textContent = ''; toast('Import failed: ' + e.message, 'error'); }
  finally { _busyConfirmApolloImport = false; }
}

// ── File import ─────────────────────────────────────────────────────────────
let _busyImportPreConfLeads = false;
async function importPreConfLeads() {
  if (_busyImportPreConfLeads) return;
  const confName = preconfCurrentEvent || document.getElementById('preconf-name-input').value.trim();
  const eventDate = document.getElementById('preconf-date-input').value || '';
  const fileInput = document.getElementById('preconf-file-input');
  const status = document.getElementById('preconf-import-status');
  if (!confName) { toast('Create or select an event first (Step 1).', 'error'); return; }
  if (!fileInput.files.length) { toast('Select a file.', 'error'); return; }

  const fd = new FormData();
  fd.append('file', fileInput.files[0]);
  let url = `/conference/import?conference_name=${encodeURIComponent(confName)}&outreach_type=pre`;
  if (eventDate) url += `&event_date=${encodeURIComponent(eventDate)}`;

  status.textContent = 'Importing...';
  _busyImportPreConfLeads = true;
  try {
    const res = await fetch(API + url, {
      method: 'POST',
      headers: _authToken ? { 'Authorization': `Bearer ${_authToken}` } : {},
      body: fd,
    });
    if (!res.ok) { const e = await res.json(); throw new Error(e.detail || res.statusText); }
    const data = await res.json();
    status.textContent = `\u2713 Imported ${data.imported}, skipped ${data.skipped}`;
    fileInput.value = '';
    preconfCurrentEvent = confName;
    await loadPreconfEventList();
    document.getElementById('preconf-event-select').value = confName;
    await loadPreConfLeads(true);
    toast(`${data.imported} leads imported`);
  } catch(e) { status.textContent = ''; toast('Import failed: ' + e.message, 'error'); }
  finally { _busyImportPreConfLeads = false; }
}

// ── Sequence designer ───────────────────────────────────────────────────────
const _PRECONF_STEP_PRESETS = {
  'cold_intro':        { label: 'Cold intro (event reference)', type: 'email', desc: 'Cold-warm introduction referencing the upcoming conference/event. Mention you will be attending and suggest meeting there. One clear CTA.', limit: 100, is_followup: false },
  'value_add':         { label: 'Value-add follow-up',          type: 'email', desc: 'Follow-up with a value add relevant to their work and the event. Share a brief insight or talking point. Light nudge toward scheduling a meeting at the event.', limit: 70, is_followup: true },
  'final_nudge':       { label: 'Final nudge',                  type: 'email', desc: 'Final nudge. Keep it brief and conversational. Reiterate interest in connecting at the event. Light social proof if available.', limit: 60, is_followup: true },
  'linkedin_connect':  { label: 'LinkedIn Connect Request',     type: 'linkedin_connect', desc: 'Send a LinkedIn connection request with a personalized note referencing the event.', limit: 30, is_followup: false },
  'linkedin_message':  { label: 'LinkedIn Message',             type: 'linkedin_message', desc: 'Send a LinkedIn message to connected leads. Reference the event and suggest meeting.', limit: 50, is_followup: false },
  'custom':            { label: 'Custom (email)',                type: 'email', desc: '', limit: 60, is_followup: false },
};
const _DEFAULT_PRECONF_SEQ_STEPS = [];
let preconfSeqSteps = JSON.parse(JSON.stringify(_DEFAULT_PRECONF_SEQ_STEPS));
let _preconfSeqSaved = false;

function _detectPreconfPreset(desc) { for (const [k, p] of Object.entries(_PRECONF_STEP_PRESETS)) { if (k !== 'custom' && p.desc === desc) return k; } return 'custom'; }
function _buildPreconfPresetSelect(idx, cur) {
  return `<select style="font-size:12px;min-width:180px;" onchange="onPreconfSeqPresetChange(${idx}, this.value)">${Object.entries(_PRECONF_STEP_PRESETS).map(([k, p]) => `<option value="${k}" ${k===cur?'selected':''}>${esc(p.label)}</option>`).join('')}</select>`;
}
function onPreconfSeqPresetChange(idx, key) { const p = _PRECONF_STEP_PRESETS[key]; if (!p) return; preconfSeqSteps[idx].preset = key; if (key !== 'custom') { preconfSeqSteps[idx].desc = p.desc; preconfSeqSteps[idx].limit = p.limit; preconfSeqSteps[idx].is_followup = p.is_followup; preconfSeqSteps[idx].type = p.type || 'email'; } renderPreConfSeqDesign(); }

function renderPreConfSeqDesign() {
  const c = document.getElementById('preconf-seq-design-steps'); if (!c) return;
  if (!preconfSeqSteps.length) {
    c.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-muted);font-size:13px;">No steps yet. Click "Add Step" below to design your sequence.</div>';
    return;
  }
  let emailNum = 0;
  c.innerHTML = preconfSeqSteps.map((s, i) => {
    const pr = s.preset || _detectPreconfPreset(s.desc); s.preset = pr;
    const isCust = pr === 'custom';
    const stepType = s.type || (_PRECONF_STEP_PRESETS[pr]?.type) || 'email';
    s.type = stepType;
    const isLI = stepType.startsWith('linkedin');
    const stepLabel = isLI ? (stepType === 'linkedin_connect' ? 'LI Connect' : 'LI Message') : `Email ${++emailNum}`;
    const labelColor = isLI ? '#0a66c2' : 'var(--navy)';
    const labelBg = isLI ? '#e8f4ff' : 'transparent';
    return `<div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;padding:8px 12px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r-sm);flex-wrap:wrap;">
      <span style="font-size:11px;font-weight:600;color:${labelColor};background:${labelBg};padding:1px 6px;border-radius:4px;min-width:65px;text-align:center;">${stepLabel}</span>
      <label style="font-size:11px;color:var(--text-muted);">Day</label><input type="number" value="${s.day}" min="0" max="90" style="width:50px;font-size:12px;" onchange="setPreconfSeqStepField(${i},'day',this)" />
      ${!isLI ? `<label style="font-size:11px;color:var(--text-muted);">Words</label><input type="number" value="${s.limit}" min="20" max="200" style="width:55px;font-size:12px;" onchange="setPreconfSeqStepField(${i},'limit',this)" />` : ''}
      ${_buildPreconfPresetSelect(i, pr)}
      ${isCust ? `<input type="text" value="${esc(s.desc)}" placeholder="Describe..." style="flex:1;font-size:12px;min-width:180px;" onchange="preconfSeqSteps[${i}].desc=this.value" />` : ''}
      ${preconfSeqSteps.length > 1 ? `<button class="btn btn-secondary" style="padding:2px 6px;font-size:11px;color:var(--red);" onclick="preconfSeqSteps.splice(${i},1);renderPreConfSeqDesign()">x</button>` : ''}
    </div>`; }).join('');
}
function addPreConfSeqStep() { preconfSeqSteps.push({ day: (preconfSeqSteps.length ? preconfSeqSteps[preconfSeqSteps.length-1].day+7 : 0), limit: 60, desc: '', preset: 'custom', type: 'email', is_followup: false }); renderPreConfSeqDesign(); }
function setPreconfSeqStepField(idx, field, el) { const v = parseInt(el.value,10); const b = CONF_SEQ_LIMITS[field]; if (!b) return; const c = Math.max(b.min, Math.min(b.max, Number.isFinite(v)?v:preconfSeqSteps[idx][field])); if (c!==v) el.value=c; preconfSeqSteps[idx][field]=c; }

async function savePreConfSeqDesign(btn) {
  for (let i = 0; i < preconfSeqSteps.length; i++) { if (!preconfSeqSteps[i].desc?.trim()) { toast(`Step ${i+1}: select a type or enter a description.`, 'error'); return; } }
  const orig = btn?.textContent; if (btn) { btn.disabled = true; btn.textContent = 'Saving...'; }
  try {
    await api('/settings/pre-conference-sequence', { method: 'POST', body: JSON.stringify({ steps: preconfSeqSteps }) });
    _preconfSeqSaved = true;
    const s = document.getElementById('preconf-seq-design-status'); if (s) { s.textContent = 'Saved'; setTimeout(() => s.textContent = '', 3000); }
    toast('Sequence saved'); updatePreconfStepperBadges();
  } catch(e) { toast('Save failed: ' + e.message, 'error'); }
  finally { if (btn) { btn.disabled = false; btn.textContent = orig || 'Save Sequence'; } }
}

async function loadPreConfSeqDesign() {
  try { const d = await api('/settings/pre-conference-sequence'); if (d.steps?.length) { preconfSeqSteps = d.steps.map(s => ({...s, preset: s.preset || _detectPreconfPreset(s.desc)})); _preconfSeqSaved = true; } else { preconfSeqSteps = JSON.parse(JSON.stringify(_DEFAULT_PRECONF_SEQ_STEPS)); _preconfSeqSaved = false; } }
  catch(_) { preconfSeqSteps = JSON.parse(JSON.stringify(_DEFAULT_PRECONF_SEQ_STEPS)); _preconfSeqSaved = false; }
  renderPreConfSeqDesign();
}
function resetPreConfSeqDesign() { preconfSeqSteps = JSON.parse(JSON.stringify(_DEFAULT_PRECONF_SEQ_STEPS)); renderPreConfSeqDesign(); toast('Reset to defaults'); }

// ── Step 4: Generate & Send ─────────────────────────────────────────────────
async function generateOnePreConf(leadId) {
  const context = document.getElementById('preconf-context').value.trim();
  if (!context) { toast('Set outreach context in Step 1 first.', 'error'); setPreconfStep(1); return; }
  try { await api('/conference/generate-sequence', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ lead_id: leadId, context }) }); await loadPreConfLeads(); toast('Sequence generated'); }
  catch(e) { toast('Generate failed: ' + e.message, 'error'); }
}

let _busyGenerateAllPreConf = false;
// Track which mode opened the modal — "generate_preview" means the Approve button should show
let _seqModalMode = null; // null | "generate_preview"
let _seqModalOutreachType = null; // "pre" | "post"

async function generateWithPreview(outreachType, btn) {
  // Step 1: Generate for one lead, open modal for review
  const isPre = outreachType === 'pre';
  const leads = isPre ? preconfLeads : confLeads;
  const contextEl = document.getElementById(isPre ? 'preconf-context' : 'conf-context');
  const context = contextEl?.value?.trim() || '';

  if (isPre && !context) { toast('Set outreach context in Step 1 first.', 'error'); setPreconfStep(1); return; }
  if (!leads.length) { toast('No leads to generate.', 'error'); return; }

  const lead = leads.find(l => !l.generated_sequence && !l.replied_at) || leads[0];
  if (!lead) { toast('No leads available.', 'error'); return; }

  const orig = btn?.textContent;
  if (btn) { btn.disabled = true; btn.textContent = 'Generating preview...'; }
  try {
    const data = await api('/conference/generate-sequence', {
      method: 'POST',
      body: JSON.stringify({ lead_id: lead.id, context }),
    });
    // Update local array
    const idx = leads.findIndex(l => l.id === lead.id);
    if (idx !== -1) leads[idx] = data;
    if (isPre) renderPreConfLeads(); else renderConfLeads();

    // Open modal in preview mode — show Approve button
    _seqModalMode = 'generate_preview';
    _seqModalOutreachType = outreachType;
    openSeqModal(lead.id);

    // Show the Approve button
    const approveBtn = document.getElementById('conf-seq-approve-btn');
    if (approveBtn) approveBtn.style.display = '';

    toast(`Preview generated for ${data.first_name || 'lead'}. Review, refine, then click "Approve & Generate All".`);
  } catch (e) { toast('Generate failed: ' + e.message, 'error'); }
  finally { if (btn) { btn.disabled = false; btn.textContent = orig; } }
}

async function approveAndGenerateAll(btn) {
  if (!seqModalLeadId) return;
  const isPre = _seqModalOutreachType === 'pre';
  const leads = isPre ? preconfLeads : confLeads;
  const context = document.getElementById(isPre ? 'preconf-context' : 'conf-context')?.value?.trim() || '';
  const confName = isPre ? preconfCurrentEvent : confCurrentEvent;

  // First save any edits the user made
  await saveSeqEmail();

  const remaining = leads.filter(l => l.id !== seqModalLeadId && !l.generated_sequence && !l.replied_at);
  if (!remaining.length) {
    toast('All leads already have sequences. Nothing to generate.');
    closeSeqModal();
    return;
  }

  const ok = await customConfirm(`Generate sequences for ${remaining.length} remaining leads using this lead's style as reference?`);
  if (!ok) return;

  const orig = btn?.textContent;
  if (btn) { btn.disabled = true; btn.textContent = 'Generating...'; }
  try {
    // Apply style to all remaining leads for each step that has content
    const refLead = leads.find(l => l.id === seqModalLeadId);
    const refSeq = refLead?.generated_sequence || {};
    const seq = typeof refSeq === 'string' ? JSON.parse(refSeq) : refSeq;
    const steps = Object.keys(seq).filter(k => k.startsWith('email') || k.startsWith('linkedin'));

    // Generate sequences for remaining leads
    const ids = remaining.map(l => l.id);
    const genResult = await api('/conference/generate-sequence-all', {
      method: 'POST',
      body: JSON.stringify({ lead_ids: ids, context }),
    });

    // Now apply style from reference lead to all steps
    for (const step of steps) {
      try {
        await api('/conference/apply-style', {
          method: 'POST',
          body: JSON.stringify({
            reference_lead_id: seqModalLeadId,
            step,
            conference_name: confName,
            context,
          }),
        });
      } catch (e) {
        // Style apply is best-effort per step
        console.warn(`apply-style failed for ${step}:`, e);
      }
    }

    toast(`Generated ${genResult.generated || ids.length} sequences with your approved style.`);
    if (isPre) await loadPreConfLeads(); else await loadConferenceLeads(false);
    closeSeqModal();
  } catch (e) { toast('Generate failed: ' + e.message, 'error'); }
  finally { if (btn) { btn.disabled = false; btn.textContent = orig; } }
}

async function previewOnePreConf(btn) {
  const context = document.getElementById('preconf-context').value.trim();
  if (!context) { toast('Set outreach context in Step 1 first.', 'error'); setPreconfStep(1); return; }
  // Pick the first lead without a generated sequence, or the first lead
  const lead = preconfLeads.find(l => !l.generated_sequence && !l.replied_at) || preconfLeads[0];
  if (!lead) { toast('No leads to preview.', 'error'); return; }
  const orig = btn?.textContent;
  if (btn) { btn.disabled = true; btn.textContent = 'Generating...'; }
  try {
    const data = await api('/conference/generate-sequence', {
      method: 'POST',
      body: JSON.stringify({ lead_id: lead.id, context }),
    });
    // Update local array
    const idx = preconfLeads.findIndex(l => l.id === lead.id);
    if (idx !== -1) preconfLeads[idx] = data;
    renderPreConfLeads();
    // Open the modal to review
    openSeqModal(lead.id);
    toast(`Preview generated for ${data.first_name || 'lead'}. Review and click Generate All when ready.`);
  } catch (e) { toast('Preview failed: ' + e.message, 'error'); }
  finally { if (btn) { btn.disabled = false; btn.textContent = orig; } }
}

async function regenerateLinkedInText(btn) {
  if (!seqModalLeadId) return;
  const lead = confLeads.find(l => l.id === seqModalLeadId) || preconfLeads.find(l => l.id === seqModalLeadId);
  if (!lead) return;
  const context = document.getElementById('preconf-context')?.value?.trim()
    || document.getElementById('conf-context')?.value?.trim() || '';
  const status = document.getElementById('conf-seq-lk-regen-status');
  const orig = btn?.textContent;
  if (btn) { btn.disabled = true; btn.textContent = 'Regenerating...'; }
  if (status) status.textContent = 'Generating...';
  try {
    const data = await api('/conference/generate-sequence', {
      method: 'POST',
      body: JSON.stringify({ lead_id: lead.id, context }),
    });
    // Update local array
    let idx = confLeads.findIndex(l => l.id === lead.id);
    if (idx !== -1) confLeads[idx] = data;
    else { idx = preconfLeads.findIndex(l => l.id === lead.id); if (idx !== -1) preconfLeads[idx] = data; }
    // Refresh the modal with new content
    renderSeqTabs(data);
    showSeqStep(data, seqModalStep);
    if (status) { status.textContent = 'Regenerated'; setTimeout(() => { status.textContent = ''; }, 3000); }
    toast('LinkedIn text regenerated');
  } catch (e) {
    toast('Regenerate failed: ' + e.message, 'error');
    if (status) status.textContent = '';
  } finally { if (btn) { btn.disabled = false; btn.textContent = orig; } }
}

async function applyStyleToAll(btn) {
  if (!seqModalLeadId || !seqModalStep) return;
  const confName = preconfCurrentEvent;
  if (!confName) { toast('No event selected.', 'error'); return; }
  const context = document.getElementById('preconf-context')?.value?.trim() || '';
  const ok = await customConfirm(`Apply this ${seqModalStep.startsWith('linkedin_') ? 'LinkedIn' : 'email'} style to all other leads in "${confName}"?`);
  if (!ok) return;
  const orig = btn?.textContent;
  if (btn) { btn.disabled = true; btn.textContent = 'Applying...'; }
  try {
    const r = await api('/conference/apply-style', {
      method: 'POST',
      body: JSON.stringify({
        reference_lead_id: seqModalLeadId,
        step: seqModalStep,
        conference_name: confName,
        context: context,
      }),
    });
    toast(`Style applied: ${r.updated} updated, ${r.failed} failed`);
    await loadPreConfLeads();
  } catch (e) { toast('Apply failed: ' + e.message, 'error'); }
  finally { if (btn) { btn.disabled = false; btn.textContent = orig; } }
}

async function refineSeqText() {
  if (!seqModalLeadId || !seqModalStep) return;
  const input = document.getElementById('conf-seq-refine-input');
  const feedback = input?.value?.trim();
  if (!feedback) { toast('Type your feedback first.', 'error'); input?.focus(); return; }

  const isLinkedin = seqModalStep.startsWith('linkedin_');
  let currentText = '', subject = '';
  if (isLinkedin) {
    currentText = document.getElementById('conf-seq-lk-text').value;
  } else {
    subject = document.getElementById('conf-seq-subject').value;
    currentText = document.getElementById('conf-seq-body').value;
  }

  const btn = document.getElementById('conf-seq-refine-btn');
  const status = document.getElementById('conf-seq-refine-status');
  const origBtn = btn?.textContent;
  if (btn) { btn.disabled = true; btn.textContent = 'Refining...'; }
  if (status) status.textContent = 'AI is thinking...';

  try {
    const r = await api('/conference/refine', {
      method: 'POST',
      body: JSON.stringify({
        lead_id: seqModalLeadId,
        step: seqModalStep,
        current_text: currentText,
        feedback: feedback,
        subject: subject,
      }),
    });

    // Update the textarea with refined text
    if (isLinkedin) {
      document.getElementById('conf-seq-lk-text').value = r.result?.text || currentText;
    } else {
      document.getElementById('conf-seq-subject').value = r.result?.subject || subject;
      document.getElementById('conf-seq-body').value = r.result?.body || currentText;
      updateSeqWordCount();
    }

    // Update local lead data
    if (r.lead) {
      let idx = confLeads.findIndex(l => l.id === seqModalLeadId);
      if (idx !== -1) confLeads[idx] = r.lead;
      else { idx = preconfLeads.findIndex(l => l.id === seqModalLeadId); if (idx !== -1) preconfLeads[idx] = r.lead; }
    }

    input.value = '';
    if (status) { status.textContent = 'Refined! Type more feedback or click Save.'; setTimeout(() => { status.textContent = ''; }, 5000); }
    // Show "Apply style to all" button after a successful refine
    const applyBtn = document.getElementById('conf-seq-apply-all-btn');
    if (applyBtn && preconfLeads.length > 1) applyBtn.style.display = '';
  } catch (e) {
    toast('Refine failed: ' + e.message, 'error');
    if (status) status.textContent = '';
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = origBtn; }
  }
}

async function generateAllPreConf() {
  if (_busyGenerateAllPreConf) return;
  const context = document.getElementById('preconf-context').value.trim();
  if (!context) { toast('Set outreach context in Step 1 first.', 'error'); setPreconfStep(1); return; }
  const status = document.getElementById('preconf-bulk-status');
  status.textContent = 'Generating sequences...';
  _busyGenerateAllPreConf = true;
  try {
    const ids = preconfLeads.filter(l => !l.generated_sequence && !l.replied_at).map(l => l.id);
    if (!ids.length) { status.textContent = ''; toast('No leads to generate.'); return; }
    const data = await api('/conference/generate-sequence-all', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ lead_ids: ids, context }) });
    status.textContent = `\u2713 Generated ${data.generated || ids.length}`;
    await loadPreConfLeads(); toast('Sequences generated');
  } catch(e) { status.textContent = ''; toast('Generate failed: ' + e.message, 'error'); }
  finally { _busyGenerateAllPreConf = false; }
}

let _busyEnrichAllPreConf = false;
async function enrichAllPreConf() {
  if (_busyEnrichAllPreConf) return;
  const confName = preconfCurrentEvent;
  const status = document.getElementById('preconf-enrich-status') || document.getElementById('preconf-bulk-status');
  status.textContent = 'Enriching...';
  _busyEnrichAllPreConf = true;
  try {
    let url = '/conference/enrich-all'; if (confName) url += '?conference_name=' + encodeURIComponent(confName);
    const data = await api(url, { method: 'POST' });
    status.textContent = `\u2713 Enriched ${data.enriched}, failed ${data.failed}`;
    await loadPreConfLeads();
  } catch(e) { status.textContent = ''; toast('Enrich failed: ' + e.message, 'error'); }
  finally { _busyEnrichAllPreConf = false; }
}

let _busyDraftAllPreConf = false;
async function draftAllPreConf() {
  if (_busyDraftAllPreConf) return;
  const confName = preconfCurrentEvent;
  const status = document.getElementById('preconf-bulk-status');
  status.textContent = 'Creating drafts...';
  _busyDraftAllPreConf = true;
  try {
    let url = '/conference/draft-all'; if (confName) url += '?conference_name=' + encodeURIComponent(confName);
    const inboxId = document.getElementById('preconf-inbox-select')?.value || 'outlook';
    if (inboxId && inboxId !== 'outlook') url += (url.includes('?') ? '&' : '?') + 'inbox_id=' + encodeURIComponent(inboxId);
    const data = await api(url, { method: 'POST' });
    status.textContent = `\u2713 Drafted ${data.drafted}, failed ${data.failed}`;
    await loadPreConfLeads(); toast(`${data.drafted} drafts created`);
  } catch(e) { status.textContent = ''; toast('Draft failed: ' + e.message, 'error'); }
  finally { _busyDraftAllPreConf = false; }
}

async function checkPreConfReplies() {
  const status = document.getElementById('preconf-bulk-status');
  status.textContent = 'Checking replies...';
  try {
    const data = await api('/conference/check-replies', { method: 'POST' });
    status.textContent = data.marked ? `Found ${data.marked} new replies` : 'No new replies';
    if (data.marked) await loadPreConfLeads();
    setTimeout(() => { status.textContent = ''; }, 5000);
  } catch(e) { status.textContent = ''; toast('Check failed: ' + e.message, 'error'); }
}

async function launchPreConfLinkedIn(btn) {
  const confName = preconfCurrentEvent;
  if (!confName) { toast('Select an event first.', 'error'); return; }
  const orig = btn?.textContent;
  if (btn) { btn.disabled = true; btn.textContent = 'Enrolling...'; }
  try {
    const r = await api('/conference/launch-linkedin', {
      method: 'POST',
      body: JSON.stringify({ conference_name: confName }),
    });
    toast(`LinkedIn outreach launched: ${r.enrolled} enrolled, ${r.skipped} already enrolled, ${r.no_linkedin} skipped (no LinkedIn URL)`);
    await loadPreConfLeads();
  } catch (e) {
    toast('Launch failed: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = orig; }
  }
}

async function deletePreConfLead(id) {
  if (!await customConfirm('Delete this lead?', {destructive: true})) return;
  try { await api(`/conference/leads/${id}`, { method: 'DELETE' }); await loadPreConfLeads(); toast('Lead deleted'); }
  catch(e) { toast('Delete failed: ' + e.message, 'error'); }
}

// ── Events-reply-cutoff 2026-04-09: reply checks & undo ─────────────────────
// Trigger an immediate Outlook inbox poll for the current user. Used by
// the "Check replies now" button above the bulk actions row. The backend
// reuses the same match logic as the 10-min background poller.
async function checkRepliesNow() {
  const btn = document.getElementById('conf-check-replies-btn');
  const origText = btn ? btn.textContent : null;
  if (btn) { btn.disabled = true; btn.textContent = 'Checking…'; }
  try {
    const result = await api('/conference/check-replies', { method: 'POST' });
    const n = result?.marked ?? 0;
    if (n > 0) {
      toast(`Marked ${n} lead${n !== 1 ? 's' : ''} as replied`);
      await loadConferenceLeads();  // refresh to pick up the new replied_at
    } else {
      toast('No new replies detected');
    }
  } catch (e) {
    toast('Check replies failed: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = origText || 'Check replies now'; }
  }
}

// False-positive undo: clear the replied_at flag on a single lead so the
// rep can resume drafting (used when the "reply" was actually an
// out-of-office, an auto-response, or an unrelated message from the
// same sender that landed in the polling window).
async function clearLeadReply(leadId) {
  if (!await customConfirm('Undo the reply flag for this lead? Drafting will resume. If the original reply is still in your inbox, the next poller tick will re-flag it.')) return;
  try {
    const updated = await api(`/conference/leads/${leadId}/clear-reply`, { method: 'POST' });
    const idx = confLeads.findIndex(l => l.id === leadId);
    if (idx !== -1) confLeads[idx] = updated;
    renderConfLeads();
    toast('Reply flag cleared');
  } catch (e) {
    toast('Clear reply failed: ' + e.message, 'error');
  }
}

// ── Sequence generation & modal ──────────────────────────────────────────────

let seqModalLeadId = null;
let seqModalStep = 'email1';

// ── Clear drafts ────────────────────────────────────────────────────────────

async function clearOneDraft(leadId) {
  try {
    await api(`/conference/leads/${leadId}/clear-draft`, { method: 'POST' });
    const idx = confLeads.findIndex(l => l.id === leadId);
    if (idx !== -1) { confLeads[idx].draft_created_at = null; confLeads[idx].draft_message_id = null; }
    renderConfLeads();
    toast('Draft cleared');
  } catch(e) { toast('Clear failed: ' + e.message, 'error'); }
}

async function clearAllDrafts() {
  const drafted = confLeads.filter(l => l.draft_created_at);
  if (!drafted.length) { toast('No drafts to clear'); return; }
  if (!await customConfirm(`Clear draft status for ${drafted.length} lead(s)? This resets them so you can re-draft.`)) return;
  const status = document.getElementById('conf-bulk-status');
  status.textContent = 'Clearing drafts...';
  setConfBulkBtns(true);
  let cleared = 0;
  try {
    for (const l of drafted) {
      await api(`/conference/leads/${l.id}/clear-draft`, { method: 'POST' });
      cleared++;
    }
    status.textContent = `Cleared ${cleared} drafts`;
    toast(`${cleared} drafts cleared`);
    await loadConferenceLeads();
  } catch(e) {
    status.textContent = `Cleared ${cleared}/${drafted.length}`;
    toast('Clear failed: ' + e.message, 'error');
  } finally { setConfBulkBtns(false); }
}

// ── Clear all conference leads ──────────────────────────────────────────────

async function clearAllConfLeads() {
  const conf = (confCurrentEvent || '');
  const scope = conf ? `all leads for "${conf}"` : 'ALL conference leads';
  if (!await customConfirm(`Delete ${scope}? This cannot be undone.`, {destructive: true})) return;
  const ids = confLeads.map(l => l.id);
  if (!ids.length) { toast('No leads to clear'); return; }
  const status = document.getElementById('conf-bulk-status');
  status.textContent = 'Clearing leads...';
  // FIX-25: single bulk call instead of N sequential DELETEs; button disabled via setConfBulkBtns
  setConfBulkBtns(true);
  try {
    const res = await api('/conference/leads/bulk-delete', {
      method: 'POST',
      body: JSON.stringify({ ids }),
    });
    const deleted = (res && typeof res.deleted === 'number') ? res.deleted : ids.length;
    status.textContent = `Cleared ${deleted} leads`;
    toast(`${deleted} leads deleted`);
    await loadConferenceLeads();
  } catch(e) {
    status.textContent = 'Clear failed';
    toast('Clear failed: ' + e.message, 'error');
  } finally { setConfBulkBtns(false); }
}

// ── Conference sequence designer ────────────────────────────────────────────

const _CONF_STEP_PRESETS = {
  'first_reachout':  { label: 'First reach-out post conference', desc: 'Warm follow-up referencing the conference. Specific detail about what you discussed or their work. One clear CTA.', limit: 100, is_followup: false },
  'first_followup':  { label: 'First follow-up',                 desc: 'Follow-up continuing the same thread. Add new value - a relevant insight, article, or resource. Light nudge toward a call.', limit: 70,  is_followup: true },
  'second_followup': { label: 'Second follow-up',                desc: 'Follow-up continuing the thread. Share a different angle or piece of value. Keep it brief and easy to reply to.', limit: 60,  is_followup: true },
  'case_study':      { label: 'Case study',                      desc: 'Social proof. Mention a specific result or customer story relevant to their industry. Keep it conversational.', limit: 60,  is_followup: false },
  'custom':          { label: 'Custom',                           desc: '',  limit: 60,  is_followup: false },
};

const _DEFAULT_CONF_SEQ_STEPS = [];

let confSeqSteps = JSON.parse(JSON.stringify(_DEFAULT_CONF_SEQ_STEPS));

function _detectPreset(desc) {
  for (const [key, p] of Object.entries(_CONF_STEP_PRESETS)) {
    if (key !== 'custom' && p.desc === desc) return key;
  }
  return 'custom';
}

function _buildPresetSelect(idx, currentPreset) {
  const opts = Object.entries(_CONF_STEP_PRESETS).map(([key, p]) =>
    `<option value="${key}" ${key === currentPreset ? 'selected' : ''}>${esc(p.label)}</option>`
  ).join('');
  return `<select style="font-size:12px;min-width:180px;" onchange="onConfSeqPresetChange(${idx}, this.value)">${opts}</select>`;
}

function onConfSeqPresetChange(idx, presetKey) {
  const p = _CONF_STEP_PRESETS[presetKey];
  if (!p) return;
  confSeqSteps[idx].preset = presetKey;
  if (presetKey !== 'custom') {
    confSeqSteps[idx].desc = p.desc;
    confSeqSteps[idx].limit = p.limit;
    confSeqSteps[idx].is_followup = p.is_followup;
  }
  renderConfSeqDesign();
}

function renderConfSeqDesign() {
  const container = document.getElementById('conf-seq-design-steps');
  if (!container) return;
  if (!confSeqSteps.length) {
    container.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-muted);font-size:13px;">No steps yet. Click "Add Step" below to design your sequence.</div>';
    return;
  }
  container.innerHTML = confSeqSteps.map((s, i) => {
    const preset = s.preset || _detectPreset(s.desc);
    s.preset = preset;
    const isCustom = preset === 'custom';
    return `
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;padding:8px 12px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r-sm);flex-wrap:wrap;">
      <span style="font-size:11px;font-weight:600;color:var(--navy);min-width:55px;">Email ${i+1}</span>
      <label style="font-size:11px;color:var(--text-muted);white-space:nowrap;">Day</label>
      <input type="number" value="${s.day}" min="0" max="90" style="width:50px;font-size:12px;" onchange="setConfSeqStepField(${i}, 'day', this)" />
      <label style="font-size:11px;color:var(--text-muted);white-space:nowrap;">Max words</label>
      <input type="number" value="${s.limit}" min="20" max="200" style="width:55px;font-size:12px;" onchange="setConfSeqStepField(${i}, 'limit', this)" />
      ${_buildPresetSelect(i, preset)}
      ${isCustom ? `<input type="text" value="${esc(s.desc)}" placeholder="Describe this step..." style="flex:1;font-size:12px;min-width:200px;" onchange="confSeqSteps[${i}].desc=this.value" />` : ''}
      ${confSeqSteps.length > 1 ? `<button class="btn btn-secondary" style="padding:2px 6px;font-size:11px;color:var(--red);" onclick="confSeqSteps.splice(${i},1);renderConfSeqDesign()" title="Remove step">x</button>` : ''}
    </div>`;
  }).join('');
}

function addConfSeqStep() {
  const lastDay = confSeqSteps.length ? confSeqSteps[confSeqSteps.length-1].day + 7 : 0;
  confSeqSteps.push({ day: lastDay, limit: 60, desc: '', preset: 'custom', is_followup: false });
  renderConfSeqDesign();
}

// FIX-24: clamp + validate numeric inputs before writing to the model
const CONF_SEQ_LIMITS = { day: { min: 0, max: 90 }, limit: { min: 20, max: 200 } };
function setConfSeqStepField(idx, field, inputEl) {
  const raw = parseInt(inputEl.value, 10);
  const bounds = CONF_SEQ_LIMITS[field];
  if (!bounds) return;
  if (!Number.isFinite(raw) || Number.isNaN(raw)) {
    toast(`Invalid ${field === 'day' ? 'day' : 'word limit'}  -  must be a number between ${bounds.min} and ${bounds.max}.`, 'error');
    inputEl.value = confSeqSteps[idx][field];
    return;
  }
  const clamped = Math.max(bounds.min, Math.min(bounds.max, raw));
  if (clamped !== raw) {
    toast(`${field === 'day' ? 'Day' : 'Word limit'} clamped to ${clamped} (range ${bounds.min}-${bounds.max}).`, 'warning');
    inputEl.value = clamped;
  }
  confSeqSteps[idx][field] = clamped;
}

function validateConfSeqSteps() {
  for (let i = 0; i < confSeqSteps.length; i++) {
    const s = confSeqSteps[i];
    const d = Number(s.day), l = Number(s.limit);
    if (!Number.isFinite(d) || d < CONF_SEQ_LIMITS.day.min || d > CONF_SEQ_LIMITS.day.max) {
      return `Step ${i + 1}: day must be between ${CONF_SEQ_LIMITS.day.min} and ${CONF_SEQ_LIMITS.day.max}.`;
    }
    if (!Number.isFinite(l) || l < CONF_SEQ_LIMITS.limit.min || l > CONF_SEQ_LIMITS.limit.max) {
      return `Step ${i + 1}: word limit must be between ${CONF_SEQ_LIMITS.limit.min} and ${CONF_SEQ_LIMITS.limit.max}.`;
    }
    if (!s.desc || !s.desc.trim()) {
      return `Step ${i + 1}: please select a type or enter a custom description.`;
    }
  }
  return null;
}

async function saveConfSeqDesign(btn) {
  // FIX-24: refuse invalid values
  const err = validateConfSeqSteps();
  if (err) { toast(err, 'error'); return; }
  // FIX-38: double-submit guard
  const saveBtn = btn || (event && event.target) || null;
  const origText = saveBtn ? saveBtn.textContent : null;
  if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Saving…'; }
  try {
    const ccVal = (document.getElementById('conf-global-cc')?.value || '').trim();
    await api('/settings/conference-sequence', {
      method: 'POST',
      body: JSON.stringify({ steps: confSeqSteps, cc: ccVal }),
    });
    document.getElementById('conf-seq-design-status').textContent = 'Saved';
    toast('Sequence config saved');
    setTimeout(() => { const el = document.getElementById('conf-seq-design-status'); if (el) el.textContent = ''; }, 3000);
  } catch(e) {
    toast('Save failed: ' + e.message, 'error');
  } finally {
    if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = origText || 'Save Sequence'; }
  }
}

async function loadConfSeqDesign() {
  try {
    const data = await api('/settings/conference-sequence');
    if (data.steps && data.steps.length) {
      confSeqSteps = data.steps.map(s => ({
        ...s,
        preset: s.preset || _detectPreset(s.desc),
      }));
    } else {
      confSeqSteps = JSON.parse(JSON.stringify(_DEFAULT_CONF_SEQ_STEPS));
    }
    // Restore saved CC into the global CC field
    const ccEl = document.getElementById('conf-global-cc');
    if (ccEl && data.cc) ccEl.value = data.cc;
  } catch(e) {
    confSeqSteps = JSON.parse(JSON.stringify(_DEFAULT_CONF_SEQ_STEPS));
  }
  renderConfSeqDesign();
}

function resetConfSeqDesign() {
  confSeqSteps = JSON.parse(JSON.stringify(_DEFAULT_CONF_SEQ_STEPS));
  renderConfSeqDesign();
  toast('Reset to defaults');
}

async function generateOneSequence(leadId) {
  const context = document.getElementById('conf-context').value.trim();
  if (!context) { toast('Add outreach context above first', 'error'); return; }
  const btn = document.querySelector(`button[onclick="generateOneSequence('${leadId}')"]`);
  if (btn) { btn.disabled = true; btn.textContent = 'Generating…'; }
  try {
    const updated = await api('/conference/generate-sequence', { method: 'POST', body: JSON.stringify({ lead_id: leadId, context }) });
    const idx = confLeads.findIndex(l => l.id === leadId);
    if (idx !== -1) confLeads[idx] = updated;
    renderConfLeads();
    toast('Sequence generated');
  } catch(e) {
    toast('Sequence generation failed: ' + e.message, 'error');
    if (btn) { btn.disabled = false; btn.textContent = 'Generate'; }
  }
}

async function generateAllSequences() {
  const context = document.getElementById('conf-context').value.trim();
  if (!context) { toast('Add outreach context above first', 'error'); return; }
  const selected = getSelectedConfIds();
  let ids;
  if (selected.length) {
    ids = selected;
  } else {
    ids = confLeads.map(l => l.id);
  }
  if (!ids.length) { toast('No leads to generate for. Import leads first.', 'info'); return; }
  const withContent = ids.filter(id => { const l = confLeads.find(x => x.id === id); return l && l.generated_sequence; });
  if (withContent.length && !await customConfirm(`${withContent.length} of ${ids.length} lead(s) already have generated sequences. Overwrite them?`)) return;
  if (!withContent.length && ids.length > 5 && !await customConfirm(`Generate sequences for ${ids.length} leads? This uses API credits.`)) return;
  const status = document.getElementById('conf-bulk-status');
  status.innerHTML = `<div style="display:flex;align-items:center;gap:10px;"><div style="flex:1;height:6px;border-radius:3px;background:var(--border-mid);overflow:hidden;"><div id="conf-gen-bar" style="height:100%;width:0%;background:var(--gold);border-radius:3px;transition:width 0.3s;"></div></div><span id="conf-gen-text" style="white-space:nowrap;min-width:120px;">0 / ${ids.length}</span></div>`;
  setConfBulkBtns(true);
  try {
    const confBookingSeq = document.getElementById('conf-booking-link')?.checked ?? true;
    const payload = JSON.stringify({ lead_ids: ids, context, include_booking_link: confBookingSeq });
    const strmHeaders = { 'Content-Type': 'application/json' };
    if (_authToken) strmHeaders['Authorization'] = 'Bearer ' + _authToken;
    const resp = await fetch(API + '/conference/generate-sequence-all-stream', {
      method: 'POST',
      headers: strmHeaders,
      body: payload,
      credentials: 'same-origin',
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let lastEvt = null;
    while (true) {
      const { done: rDone, value } = await reader.read();
      if (rDone) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        try {
          const evt = JSON.parse(line.slice(6));
          lastEvt = evt;
          const pct = Math.round((evt.i / evt.total) * 100);
          const bar = document.getElementById('conf-gen-bar');
          const txt = document.getElementById('conf-gen-text');
          if (bar) bar.style.width = pct + '%';
          if (txt) {
            if (evt.status === 'complete') {
              const cost = evt.tokens ? _fmtTokenCost(evt.tokens) : '';
              txt.textContent = `✓ ${evt.done} done, ${evt.failed} failed${cost}`;
              if (bar) bar.style.background = 'var(--green, #22c55e)';
            } else {
              txt.textContent = `${evt.i} / ${evt.total} — ${evt.name}`;
            }
          }
        } catch(_) {}
      }
    }
    await loadConferenceLeads();
    const d = lastEvt || { done: 0, failed: 0 };
    toast(`Generated ${d.done} sequences`);
  } catch(e) { status.textContent = ''; toast('Sequence generation failed: ' + e.message, 'error'); } finally { setConfBulkBtns(false); }
}

function openSeqModal(leadId) {
  const lead = confLeads.find(l => l.id === leadId) || preconfLeads.find(l => l.id === leadId);
  if (!lead || !lead.generated_sequence) return;
  seqModalLeadId = leadId;
  seqModalStep = 'email1';
  // Hide Approve button unless opened via generateWithPreview
  const approveBtn = document.getElementById('conf-seq-approve-btn');
  if (approveBtn && _seqModalMode !== 'generate_preview') approveBtn.style.display = 'none';
  // Events-reply-cutoff 2026-04-09: if the lead has replied, show a
  // prominent banner at the top of the modal and disable the push button.
  const nameEl = document.getElementById('conf-seq-modal-name');
  if (lead.replied_at) {
    const subject = lead.reply_subject ? `  -  "${lead.reply_subject}"` : '';
    nameEl.innerHTML = `${esc(lead.first_name||'')} ${esc(lead.last_name||'')}, ${esc(lead.company||lead.email)} <span style="display:inline-flex;align-items:center;gap:4px;margin-left:8px;font-size:11px;padding:2px 8px;border-radius:10px;background:#fee2e2;color:#b91c1c;font-weight:600;">${icon('mail',11)} Replied${esc(subject)}</span>`;
  } else {
    nameEl.textContent = `${lead.first_name||''} ${lead.last_name||''}, ${lead.company||lead.email}`;
  }
  renderSeqTabs(lead);
  showSeqStep(lead, 'email1');
  document.getElementById('conf-seq-modal').classList.remove('hidden');
  document.getElementById('conf-seq-modal').style.display = 'flex';
}

function renderSeqTabs(lead) {
  const seq = lead.generated_sequence;
  const drafted = seq._drafted_steps || [];
  const replied = !!lead.replied_at;

  // ── Sequence progress tracker ─────────────────────────────────────────
  // Visual dot tracker: filled green = drafted, outline = pending, red
  // when the lead has replied (the whole sequence is effectively closed).
  // Count emails actually present in this lead's sequence  -  a 3-step
  // override looks different from the 6-step default.
  const emailKeys = Object.keys(seq).filter(k => /^email\d+$/.test(k)).sort((a, b) => {
    return parseInt(a.replace('email', ''), 10) - parseInt(b.replace('email', ''), 10);
  });
  const totalEmails = emailKeys.length;
  const draftedEmails = emailKeys.filter(k => drafted.includes(k)).length;
  const progressEl = document.getElementById('conf-seq-progress');
  if (progressEl && totalEmails > 0) {
    const dotBase = 'display:inline-block;width:12px;height:12px;border-radius:50%;vertical-align:middle;';
    const connectorBase = 'display:inline-block;width:18px;height:2px;vertical-align:middle;';
    let dots = '';
    emailKeys.forEach((k, idx) => {
      const isDrafted = drafted.includes(k);
      let dotStyle, connectorBg;
      if (replied) {
        // Whole sequence closed  -  everything goes muted red.
        dotStyle = `${dotBase}background:#fecaca;border:1.5px solid #dc2626;opacity:0.7;`;
        connectorBg = '#fecaca';
      } else if (isDrafted) {
        dotStyle = `${dotBase}background:var(--green,#16a34a);border:1.5px solid var(--green,#16a34a);`;
        connectorBg = 'var(--green,#16a34a)';
      } else {
        dotStyle = `${dotBase}background:transparent;border:1.5px solid var(--border);`;
        connectorBg = 'var(--border)';
      }
      dots += `<span title="Email ${idx + 1}${isDrafted ? '  -  drafted' : ''}" style="${dotStyle}"></span>`;
      if (idx < emailKeys.length - 1) {
        dots += `<span style="${connectorBase}background:${connectorBg};"></span>`;
      }
    });
    const labelColor = replied ? '#b91c1c' : (draftedEmails === totalEmails ? 'var(--green,#16a34a)' : 'var(--text-dim)');
    const labelText = replied
      ? `Replied  -  sequence closed (${draftedEmails}/${totalEmails} had been drafted)`
      : `Step ${draftedEmails} of ${totalEmails} drafted`;
    progressEl.innerHTML =
      `<div style="display:flex;align-items:center;gap:10px;">` +
        `<div style="line-height:1;">${dots}</div>` +
        `<span style="font-size:11px;color:${labelColor};font-weight:500;">${labelText}</span>` +
      `</div>`;
  } else if (progressEl) {
    progressEl.innerHTML = '';
  }

  const tabsEl = document.getElementById('conf-seq-tabs');
  let html = '';
  // Compute real dates from draft_created_at + step day offsets
  const anchor = lead.draft_created_at ? new Date(lead.draft_created_at) : null;
  for (let i = 0; i < emailKeys.length; i++) {
    const key = emailKeys[i];
    const emailNum = parseInt(key.replace('email', ''), 10);
    const isDrafted = drafted.includes(key);
    // Look up day offset from the loaded config (confSeqSteps)
    const stepConf = confSeqSteps && confSeqSteps[i];
    const dayOffset = stepConf ? stepConf.day : i * 3;
    // Build date label
    let dateLabel = `Day ${dayOffset}`;
    if (anchor) {
      const targetDate = new Date(anchor);
      targetDate.setDate(targetDate.getDate() + dayOffset);
      const dd = String(targetDate.getDate()).padStart(2, '0');
      const mm = String(targetDate.getMonth() + 1).padStart(2, '0');
      dateLabel += ` | ${dd}/${mm}`;
    }
    const tabStyle = `padding:4px 10px;font-size:11px;${isDrafted?'border-color:var(--green);color:var(--green);':''}${replied?'opacity:0.55;':''}`;
    html += `<button class="btn btn-sm conf-seq-tab" data-step="${key}" onclick="showSeqStep(null,'${key}')" style="${tabStyle}" title="${dateLabel}">
      Email ${emailNum} <span style="font-size:10px;opacity:0.7;">${dateLabel}</span>${isDrafted?' ✓':''}
    </button>`;
  }
  if (seq.linkedin_connect_note) {
    html += `<button class="btn btn-sm btn-outline conf-seq-tab" data-step="linkedin_connect_note" onclick="showSeqStep(null,'linkedin_connect_note')" style="padding:4px 10px;font-size:11px;${replied?'opacity:0.55;':''}" title="LI Connect.">LI Connect</button>`;
  }
  if (seq.linkedin_message) {
    html += `<button class="btn btn-sm btn-outline conf-seq-tab" data-step="linkedin_message" onclick="showSeqStep(null,'linkedin_message')" style="padding:4px 10px;font-size:11px;${replied?'opacity:0.55;':''}" title="LI Message.">LI Message</button>`;
  }
  tabsEl.innerHTML = html;

  // Events-reply-cutoff: hard-disable the "Push to Drafts" button for
  // replied leads. The backend would reject the request with 409 anyway,
  // but disabling at the UI prevents a pointless round-trip + toast.
  const draftBtn = document.getElementById('conf-seq-draft-btn');
  if (draftBtn) {
    if (replied) {
      draftBtn.disabled = true;
      draftBtn.title = 'This lead has replied  -  further sequence steps are cancelled';
      draftBtn.style.opacity = '0.5';
      draftBtn.style.cursor = 'not-allowed';
    } else {
      draftBtn.disabled = false;
      draftBtn.title = '';
      draftBtn.style.opacity = '';
      draftBtn.style.cursor = '';
    }
  }
}

function showSeqStep(lead, step) {
  if (!lead) lead = confLeads.find(l => l.id === seqModalLeadId) || preconfLeads.find(l => l.id === seqModalLeadId);
  if (!lead || !lead.generated_sequence) return;
  seqModalStep = step;
  const seq = lead.generated_sequence;

  // Highlight active tab
  document.querySelectorAll('.conf-seq-tab').forEach(t => {
    t.style.background = t.dataset.step === step ? 'var(--navy-light)' : '';
    t.style.fontWeight = t.dataset.step === step ? '600' : '';
  });

  const isLinkedin = step.startsWith('linkedin_');
  document.getElementById('conf-seq-editor').classList.toggle('hidden', isLinkedin);
  document.getElementById('conf-seq-linkedin').classList.toggle('hidden', !isLinkedin);
  const draftBtn = document.getElementById('conf-seq-draft-btn');
  if (draftBtn) draftBtn.style.display = isLinkedin ? 'none' : '';

  if (isLinkedin) {
    document.getElementById('conf-seq-lk-label').textContent = step === 'linkedin_connect_note' ? 'Connection Request Note' : 'LinkedIn Message';
    document.getElementById('conf-seq-lk-text').value = seq[step] || '';
  } else {
    const email = seq[step] || {};
    document.getElementById('conf-seq-subject').value = email.subject || '';
    document.getElementById('conf-seq-body').value = email.body || '';
    updateSeqWordCount();
  }
}

// FIX-40: compare live word count against the configured per-step limit
function updateSeqWordCount() {
  const wcEl = document.getElementById('conf-seq-wc');
  if (!wcEl) return;
  const bodyEl = document.getElementById('conf-seq-body');
  const body = bodyEl ? bodyEl.value : '';
  const wc = body.split(/\s+/).filter(Boolean).length;
  // Extract email step index from "email1", "email2", ...
  const m = /^email(\d+)$/i.exec(seqModalStep || '');
  const stepIdx = m ? parseInt(m[1], 10) - 1 : -1;
  const limit = (stepIdx >= 0 && confSeqSteps[stepIdx] && Number.isFinite(+confSeqSteps[stepIdx].limit))
    ? +confSeqSteps[stepIdx].limit
    : null;
  if (limit && wc > limit) {
    wcEl.textContent = `${wc} words  -  ${wc - limit} over limit (${limit})`;
    wcEl.style.color = 'var(--red)';
    wcEl.style.fontWeight = '600';
  } else if (limit) {
    wcEl.textContent = `${wc} / ${limit} words`;
    wcEl.style.color = 'var(--text-muted)';
    wcEl.style.fontWeight = '';
  } else {
    wcEl.textContent = `${wc} words`;
    wcEl.style.color = 'var(--text-muted)';
    wcEl.style.fontWeight = '';
  }
}

function closeSeqModal() {
  document.getElementById('conf-seq-modal').classList.add('hidden');
  document.getElementById('conf-seq-modal').style.display = '';
  seqModalLeadId = null;
  _seqModalMode = null;
  _seqModalOutreachType = null;
  const approveBtn = document.getElementById('conf-seq-approve-btn');
  if (approveBtn) approveBtn.style.display = 'none';
}

async function saveSeqEmail(btn) {
  if (!seqModalLeadId) return;
  const step = seqModalStep;
  let subject, body;
  if (step.startsWith('linkedin_')) {
    // LinkedIn fields are plain text, save via the same endpoint with a dummy subject
    const text = document.getElementById('conf-seq-lk-text').value;
    subject = '';
    body = text;
  } else {
    subject = document.getElementById('conf-seq-subject').value;
    body = document.getElementById('conf-seq-body').value;
  }
  // FIX-37: act on the captured element instead of re-selecting by class
  const origText = btn ? btn.textContent : null;
  if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
  try {
    const updated = await api(`/conference/leads/${seqModalLeadId}/sequence-email`, {
      method: 'PATCH',
      body: JSON.stringify({ step, subject, body }),
    });
    // Update in whichever array holds this lead
    let idx = confLeads.findIndex(l => l.id === seqModalLeadId);
    if (idx !== -1) { confLeads[idx] = updated; }
    else { idx = preconfLeads.findIndex(l => l.id === seqModalLeadId); if (idx !== -1) preconfLeads[idx] = updated; }
    renderSeqTabs(updated);
    showSeqStep(updated, step);
    toast('Saved');
  } catch(e) { toast('Save failed: ' + e.message, 'error'); }
  finally { if (btn) { btn.disabled = false; btn.textContent = origText || 'Save Changes'; } }
}

async function draftSeqStep(btn) {
  if (!seqModalLeadId) return;
  const step = seqModalStep;
  if (step.startsWith('linkedin_')) { toast('LinkedIn messages cannot be pushed to email drafts', 'error'); return; }
  // FIX-37: act on the captured element directly
  const origText = btn ? btn.textContent : null;
  if (btn) { btn.disabled = true; btn.textContent = 'Drafting…'; }
  await saveSeqEmail();
  const inboxId = document.getElementById('conf-inbox-select')?.value || document.getElementById('preconf-inbox-select')?.value || 'outlook';
  const globalCc = (document.getElementById('conf-global-cc')?.value || '').trim();
  const includeCloser = document.getElementById('conf-include-ai-closer')?.checked !== false;
  try {
    let draftUrl = `/conference/draft-sequence/${seqModalLeadId}?step=${encodeURIComponent(step)}&inbox_id=${encodeURIComponent(inboxId)}`;
    if (globalCc) draftUrl += `&cc=${encodeURIComponent(globalCc)}`;
    if (!includeCloser) draftUrl += '&strip_signoff=true';
    const updated = await api(draftUrl, { method: 'POST' });
    // Update in whichever array holds this lead
    let idx = confLeads.findIndex(l => l.id === seqModalLeadId);
    if (idx !== -1) { confLeads[idx] = updated; renderConfLeads(); }
    else { idx = preconfLeads.findIndex(l => l.id === seqModalLeadId); if (idx !== -1) { preconfLeads[idx] = updated; renderPreConfLeads(); } }
    renderSeqTabs(updated);
    toast(`Draft created for ${step}`);
  } catch(e) { toast('Draft failed: ' + e.message, 'error'); }
  finally { if (btn) { btn.disabled = false; btn.textContent = origText || 'Push to Drafts'; } }
}

// ── Inbox manager ──────────────────────────────────────────────────────────────
async function loadInboxes_conf() {
  try {
    confInboxes = await api('/conference/inboxes');
  } catch(e) { console.error('loadInboxes_conf:', e); }
  if (!confInboxes || !confInboxes.length) {
    confInboxes = [{ id: 'outlook', name: 'Personal Outlook', email: 'default', type: 'outlook' }];
  }
  renderInboxSelect();
}

function renderInboxSelect() {
  const sel = document.getElementById('conf-inbox-select');
  if (!sel) return;
  const current = sel.value;
  sel.innerHTML = confInboxes.map(i =>
    `<option value="${esc(i.id)}" ${i.id===current?'selected':''}>${esc(i.name)} (${esc(i.email)})</option>`
  ).join('');
}

function openInboxManager() {
  renderInboxList();
  document.getElementById('inbox-manager-modal').classList.remove('hidden');
  document.getElementById('inbox-manager-modal').style.display = 'flex';
}

function closeInboxManager() {
  const m = document.getElementById('inbox-manager-modal');
  m.classList.add('hidden');
  m.style.display = '';
}

async function renderInboxList() {
  const el = document.getElementById('inbox-list');
  if (!confInboxes.length) { el.innerHTML = '<div style="color:var(--text-muted);font-size:13px;">No inboxes configured yet.</div>'; return; }
  const statusMap = {};
  await Promise.all(confInboxes.filter(i => i.type === 'outlook').map(async i => {
    try {
      const url = i.id === 'outlook' ? '/outlook/auth/status' : `/outlook/inbox/${i.id}/auth/status`;
      statusMap[i.id] = await api(url);
    } catch(e) { statusMap[i.id] = { authenticated: false }; }
  }));

  el.innerHTML = confInboxes.map((i, idx) => {
    const isOutlook = i.type === 'outlook';
    const status = statusMap[i.id];
    let authHtml = '';
    if (isOutlook) {
      if (status && status.authenticated) {
        authHtml = `<span style="font-size:11px;color:#15803d;margin-right:4px;">● Connected</span>
          <button class="btn btn-secondary" style="padding:3px 10px;font-size:11px;color:var(--red);" data-action="disconnect-outlook" data-idx="${idx}" title="Disconnect.">Disconnect</button>`;
      } else {
        authHtml = `<span style="font-size:11px;color:#b91c1c;margin-right:4px;">● Not connected</span>
          <button class="btn btn-primary" style="padding:3px 10px;font-size:11px;" data-action="connect-outlook" data-idx="${idx}" title="Connect.">Connect</button>`;
      }
    }
    return `
    <div style="display:flex;align-items:center;justify-content:space-between;padding:10px 12px;background:var(--surface);border:1px solid var(--border);margin-bottom:4px;">
      <div>
        <div style="font-size:13px;font-weight:500;">${esc(i.name)}</div>
        <div style="font-size:12px;color:var(--text-muted);">${esc(i.email)} · ${esc(i.type.toUpperCase())}${status&&status.account?' · '+esc(status.account):''}</div>
      </div>
      <div style="display:flex;align-items:center;gap:6px;">
        ${authHtml}
        ${!isOutlook?`<button class="btn btn-secondary" style="padding:3px 10px;font-size:11px;" data-action="test-inbox" data-idx="${idx}" title="Test.">Test</button>`:''}
        ${i.id!=='outlook'?`<button class="btn btn-secondary" style="padding:3px 10px;font-size:11px;color:var(--red);" data-action="delete-inbox" data-idx="${idx}" title="Perform this action.">✕</button>`:''}
      </div>
    </div>`;
  }).join('');

  el.querySelectorAll('[data-action]').forEach(btn => {
    const action = btn.dataset.action;
    const inbox = confInboxes[parseInt(btn.dataset.idx)];
    btn.addEventListener('click', () => {
      if (action === 'disconnect-outlook') disconnectInboxOutlook(inbox.id);
      else if (action === 'connect-outlook') connectInboxOutlook(inbox.id, inbox.name);
      else if (action === 'test-inbox') testInbox(inbox.id);
      else if (action === 'delete-inbox') deleteInbox(inbox.id);
    });
  });
}

function toggleImapFields() {
  const type = document.getElementById('new-inbox-type').value;
  document.getElementById('imap-fields').style.display = type === 'imap' ? 'block' : 'none';
}

async function addInbox() {
  const name = document.getElementById('new-inbox-name').value.trim();
  const email = document.getElementById('new-inbox-email').value.trim();
  const type = document.getElementById('new-inbox-type').value;
  if (!name || !email) { toast('Name and email required', 'error'); return; }
  const body = { name, email, type };
  if (type === 'imap') {
    body.imap_host = document.getElementById('new-inbox-host').value.trim();
    body.imap_port = parseInt(document.getElementById('new-inbox-port').value) || 993;
    body.imap_password = document.getElementById('new-inbox-pass').value;
    if (!body.imap_host || !body.imap_password) { toast('IMAP host and password required', 'error'); return; }
  }
  try {
    const created = await api('/conference/inboxes', { method: 'POST', body: JSON.stringify(body) });
    toast(`Inbox "${created.name}" added`);
    await loadInboxes_conf();
    renderInboxList();
    ['new-inbox-name','new-inbox-email','new-inbox-host','new-inbox-pass'].forEach(id => {
      const el = document.getElementById(id); if (el) el.value = '';
    });
  } catch(e) { toast('Failed: ' + e.message, 'error'); }
}

async function testInbox(inboxId) {
  try {
    const res = await api(`/conference/inboxes/${inboxId}/test`, { method: 'POST' });
    toast(res.message);
  } catch(e) { toast('Test failed: ' + e.message, 'error'); }
}

async function deleteInbox(inboxId) {
  try {
    await api(`/conference/inboxes/${inboxId}`, { method: 'DELETE' });
    await loadInboxes_conf();
    renderInboxList();
    toast('Inbox removed');
  } catch(e) { toast('Delete failed: ' + e.message, 'error'); }
}

// ── Voice control ──────────────────────────────────────────────────────────────
let voiceRecognition = null;
let voiceActive = false;

const voiceBtn = document.getElementById('voice-btn');
const voiceOverlay = document.getElementById('voice-overlay');

if (voiceBtn) {
  const _SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!_SR) {
    voiceBtn.style.display = 'none';
  } else {
    voiceBtn.addEventListener('click', () => {
      if (voiceActive) stopVoice();
      else startVoice();
    });
  }
}

function startVoice() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    toast('Voice control not supported in this browser. Use Chrome.', 'error');
    return;
  }
  voiceRecognition = new SR();
  voiceRecognition.lang = 'en-US';
  voiceRecognition.interimResults = false;
  voiceRecognition.maxAlternatives = 1;

  voiceRecognition.onstart = () => {
    voiceActive = true;
    voiceBtn.classList.add('listening');
    voiceBtn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" stroke="none"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
    document.getElementById('voice-status').textContent = 'Listening…';
    document.getElementById('voice-transcript').textContent = '';
    document.getElementById('voice-action').textContent = '';
    voiceOverlay.classList.add('show');
  };

  voiceRecognition.onresult = async (event) => {
    const transcript = event.results[0][0].transcript;
    document.getElementById('voice-transcript').textContent = `"${transcript}"`;
    document.getElementById('voice-status').textContent = 'Processing…';
    await processVoiceCommand(transcript);
  };

  voiceRecognition.onerror = (e) => {
    document.getElementById('voice-status').textContent = `Error: ${e.error}`;
    setTimeout(() => stopVoice(), 2000);
  };

  voiceRecognition.onend = () => { if (voiceActive) stopVoice(); };

  voiceRecognition.start();
}

function stopVoice() {
  voiceActive = false;
  voiceBtn.classList.remove('listening');
  voiceBtn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0014 0"/><path d="M12 17v4"/><path d="M8 21h8"/></svg>';
  if (voiceRecognition) { try { voiceRecognition.stop(); } catch(e) {} voiceRecognition = null; }
  setTimeout(() => voiceOverlay.classList.remove('show'), 3000);
}

async function processVoiceCommand(transcript) {
  const context = buildVoiceContext();
  try {
    const action = await api('/voice/command', {
      method: 'POST',
      body: JSON.stringify({ transcript, context }),
    });
    executeVoiceAction(action);
  } catch(e) {
    document.getElementById('voice-status').textContent = 'Failed to process command.';
    document.getElementById('voice-action').textContent = e.message;
    setTimeout(() => stopVoice(), 3000);
  }
}

function buildVoiceContext() {
  const ctx = { tab: currentTab };
  if (currentTab === 'outreach') {
    const sel = document.getElementById('wf-campaign-select');
    if (sel && sel.value && sel.value !== '__new__') {
      ctx.campaign_id = sel.value;
      ctx.campaign_name = sel.options[sel.selectedIndex]?.text;
    }
  }
  return ctx;
}

function executeVoiceAction(action) {
  const actionEl = document.getElementById('voice-action');
  switch (action.action) {
    case 'navigate':
      actionEl.textContent = `Navigating to ${action.section}…`;
      navigate(action.section);
      break;

    case 'search_leads':
      actionEl.textContent = 'Searching Apollo…';
      navigate('leads');
      // Open filter panel and populate
      if (!document.getElementById('leads-filter-body').classList.contains('open')) toggleLeadsFilter();
      if (action.titles) {
        tagState.titles = action.titles;
        renderTags('titles-wrap', 'titles-input', 'titles');
      }
      if (action.locations) {
        tagState.locations = action.locations;
        renderTags('locations-wrap', 'locations-input', 'locations');
      }
      if (action.industries) {
        tagState.industries = action.industries;
        renderTags('industries-wrap', 'industries-input', 'industries');
      }
      if (action.limit) document.getElementById('filter-limit').value = action.limit;
      setTimeout(() => searchProspects(), 300);
      break;

    case 'filter_leads':
      actionEl.textContent = `Filtering leads: "${action.query}"`;
      navigate('leads');
      document.getElementById('prospect-search-input').value = action.query;
      filterProspects();
      break;

    case 'select_campaign': {
      actionEl.textContent = `Selecting campaign: ${action.name}`;
      navigate('workflow');
      // Find matching campaign option
      const sel = document.getElementById('wf-campaign-select');
      const opt = [...sel.options].find(o => o.text.toLowerCase().includes((action.name||'').toLowerCase()));
      if (opt) { sel.value = opt.value; onWorkflowCampaignChange(); }
      break;
    }

    case 'generate_emails':
      actionEl.textContent = 'Starting email generation…';
      navigate('workflow');
      setTimeout(() => createAndGenerate(), 500);
      break;

    case 'speak':
      actionEl.textContent = action.text;
      document.getElementById('voice-status').textContent = 'Response';
      toast(action.text);
      break;

    default:
      actionEl.textContent = 'Command executed.';
  }
  if (action.action !== 'speak') setTimeout(() => stopVoice(), 2000);
  else setTimeout(() => stopVoice(), 4000);
}

// ── Email Writing Instructions ─────────────────────────────────────────────────
async function loadEmailInstructions() {
  try {
    const data = await api('/settings/email-instructions');
    const ta = document.getElementById('email-instructions');
    if (ta) ta.value = data.instructions || '';
  } catch(e) { console.error('loadEmailInstructions:', e); }
}

async function saveEmailInstructions() {
  const instructions = document.getElementById('email-instructions').value;
  const status = document.getElementById('instructions-save-status');
  try {
    await api('/settings/email-instructions', {
      method: 'POST',
      body: JSON.stringify({ instructions }),
    });
    status.style.color = 'var(--green)';
    status.textContent = '✓ Saved';
    setTimeout(() => status.textContent = '', 2500);
  } catch(e) {
    status.style.color = 'var(--red)';
    status.textContent = 'Save failed';
  }
}

async function clearEmailInstructions() {
  if (!await customConfirm('Clear all email instructions?')) return;
  document.getElementById('email-instructions').value = '';
  await saveEmailInstructions();
}

// ── Copywriting tab ────────────────────────────────────────────────────────────
// ── Sales Copy + Conference Copy ──────────────────────────────────────────────

function _setCopyBadge(badgeId, isCustom) {
  const el = document.getElementById(badgeId);
  if (!el) return;
  const isAdmin = _currentUser && _currentUser.role === 'admin';
  if (isCustom && !isAdmin) {
    el.textContent = 'Custom';
    el.style.background = '#fef3c7';
    el.style.color = '#92400e';
  } else if (isAdmin) {
    el.textContent = 'Admin default (you)';
    el.style.background = 'var(--blue-bg)';
    el.style.color = 'var(--primary)';
  } else {
    el.textContent = 'Admin default';
    el.style.background = 'var(--surface2,var(--surface))';
    el.style.color = 'var(--text-muted)';
  }
}

async function loadSalesCopy() {
  try {
    const data = await api('/settings/sales-copy');
    document.getElementById('sales-copy-editor').value = data.value || '';
    _setCopyBadge('sales-copy-badge', data.is_custom);
  } catch (e) { console.error('loadSalesCopy:', e); }
}

function _setSalesCopyBtnsDisabled(disabled) {
  document.querySelectorAll('[onclick^="saveSalesCopy"],[onclick^="resetSalesCopy"]').forEach(b => { b.disabled = disabled; b.style.opacity = disabled ? '0.6' : ''; });
}
function _setConfCopyBtnsDisabled(disabled) {
  document.querySelectorAll('[onclick^="saveConfCopy"],[onclick^="resetConfCopy"]').forEach(b => { b.disabled = disabled; b.style.opacity = disabled ? '0.6' : ''; });
}

async function saveSalesCopy() {
  const value = document.getElementById('sales-copy-editor').value.trim();
  if (!value) { toast('Sales copy cannot be empty.', 'error'); return; }
  const statusEl = document.getElementById('sales-copy-status');
  _setSalesCopyBtnsDisabled(true);
  try {
    await api('/settings/sales-copy', { method: 'POST', body: JSON.stringify({ value }) });
    statusEl.textContent = 'Saved!'; statusEl.style.color = 'var(--green)';
    loadSalesCopy();
    setTimeout(() => { statusEl.textContent = ''; }, 3000);
  } catch (e) { statusEl.textContent = 'Error: ' + e.message; statusEl.style.color = 'var(--red)'; }
  finally { _setSalesCopyBtnsDisabled(false); }
}

async function resetSalesCopy() {
  if (!await customConfirm('Discard your custom sales copy and use the admin default?')) return;
  _setSalesCopyBtnsDisabled(true);
  try {
    await api('/settings/sales-copy', { method: 'DELETE' });
    toast('Reverted to admin default.');
    loadSalesCopy();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  finally { _setSalesCopyBtnsDisabled(false); }
}

async function loadConfCopy() {
  try {
    const data = await api('/settings/conference-copy');
    document.getElementById('conf-copy-editor').value = data.value || '';
    _setCopyBadge('conf-copy-badge', data.is_custom);
  } catch (e) { console.error('loadConfCopy:', e); }
}

async function saveConfCopy() {
  const value = document.getElementById('conf-copy-editor').value.trim();
  if (!value) { toast('Conference copy cannot be empty.', 'error'); return; }
  const statusEl = document.getElementById('conf-copy-status');
  _setConfCopyBtnsDisabled(true);
  try {
    await api('/settings/conference-copy', { method: 'POST', body: JSON.stringify({ value }) });
    statusEl.textContent = 'Saved!'; statusEl.style.color = 'var(--green)';
    loadConfCopy();
    setTimeout(() => { statusEl.textContent = ''; }, 3000);
  } catch (e) { statusEl.textContent = 'Error: ' + e.message; statusEl.style.color = 'var(--red)'; }
  finally { _setConfCopyBtnsDisabled(false); }
}

async function resetConfCopy() {
  if (!await customConfirm('Discard your custom conference copy and use the admin default?')) return;
  _setConfCopyBtnsDisabled(true);
  try {
    await api('/settings/conference-copy', { method: 'DELETE' });
    toast('Reverted to admin default.');
    loadConfCopy();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  finally { _setConfCopyBtnsDisabled(false); }
}

// ── Newsletter Copy Rules ────────────────────────────────────────────────────

async function loadNlCopy() {
  try {
    const data = await api('/settings/newsletter-copy');
    document.getElementById('nl-copy-editor').value = data.value || '';
    _setCopyBadge('nl-copy-badge', data.is_custom);
  } catch (e) { console.error('loadNlCopy:', e); }
}

async function saveNlCopy() {
  const value = document.getElementById('nl-copy-editor').value.trim();
  if (!value) { toast('Newsletter copy cannot be empty.', 'error'); return; }
  const statusEl = document.getElementById('nl-copy-status');
  try {
    await api('/settings/newsletter-copy', { method: 'POST', body: JSON.stringify({ value }) });
    statusEl.textContent = 'Saved!'; statusEl.style.color = 'var(--green)';
    loadNlCopy();
    setTimeout(() => { statusEl.textContent = ''; }, 3000);
  } catch (e) { statusEl.textContent = 'Error: ' + e.message; statusEl.style.color = 'var(--red)'; }
}

async function resetNlCopy() {
  if (!await customConfirm('Discard your custom newsletter copy and use the admin default?')) return;
  try {
    await api('/settings/newsletter-copy', { method: 'DELETE' });
    toast('Reverted to admin default.');
    loadNlCopy();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}

async function loadWritingStyle() {
  try {
    const data = await api('/settings/writing-style');
    const ta = document.getElementById('style-editor');
    if (ta) ta.value = data.content || '';
  } catch(e) { console.error('loadWritingStyle:', e); }
}

async function saveWritingStyle() {
  const content = document.getElementById('style-editor').value;
  const status = document.getElementById('style-save-status');
  try {
    await api('/settings/writing-style', { method: 'POST', body: JSON.stringify({ content }) });
    status.style.color = 'var(--green)';
    status.textContent = '✓ Saved';
    setTimeout(() => status.textContent = '', 2500);
  } catch(e) {
    status.style.color = 'var(--red)';
    status.textContent = 'Save failed';
  }
}

// ── Outlook signature ────────────────────────────────────────────────────────

async function loadOutlookSignature() {
  try {
    const data = await api('/settings/outlook-signature');
    const area = document.getElementById('sig-paste-area');
    if (area && data.signature_html) area.textContent = data.signature_html;
  } catch(e) { /* first load, no sig yet */ }
}

async function saveOutlookSignature() {
  const area = document.getElementById('sig-paste-area');
  const html = area ? area.innerHTML : '';
  const status = document.getElementById('sig-save-status');
  try {
    await api('/settings/outlook-signature', {
      method: 'POST',
      body: JSON.stringify({ signature_html: html }),
    });
    status.style.color = 'var(--green)';
    status.textContent = 'Saved';
    setTimeout(() => status.textContent = '', 2500);
  } catch(e) {
    status.style.color = 'var(--red)';
    status.textContent = 'Save failed';
  }
}

function clearOutlookSignature() {
  const area = document.getElementById('sig-paste-area');
  if (area) area.innerHTML = '';
}

async function refineWritingStyle() {
  const prompt = document.getElementById('style-refine-input').value.trim();
  if (!prompt) return;
  const current_style = document.getElementById('style-editor').value;
  const statusEl = document.getElementById('style-refine-status');
  const btn = document.getElementById('style-refine-btn');
  btn.disabled = true;
  btn.textContent = 'Updating…';
  statusEl.textContent = 'Claude is updating your style guide…';
  try {
    const data = await api('/settings/refine-style', {
      method: 'POST',
      body: JSON.stringify({ prompt, current_style }),
    });
    document.getElementById('style-editor').value = data.content;
    document.getElementById('style-refine-input').value = '';
    statusEl.textContent = '✓ Style guide updated and saved.';
    setTimeout(() => statusEl.textContent = '', 4000);
  } catch(e) {
    statusEl.style.color = 'var(--red)';
    statusEl.textContent = 'Failed: ' + e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Update';
  }
}

// ── Settings: Timezone ────────────────────────────────────────────────────────
async function loadTimezone() {
  try {
    const d = await api('/settings/sending-timezone');
    const el = document.getElementById('timezone-input');
    if (d.timezone) {
      el.value = d.timezone;
    } else {
      // Auto-detect from browser as default
      const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (detected) {
        el.value = detected;
        const note = document.getElementById('timezone-detected');
        if (note) { note.textContent = `Auto-detected from browser. Save to confirm.`; note.style.display = 'block'; }
      }
    }
  } catch(e) { console.error('loadTimezone:', e); }
  loadSendingWindow();
}

function detectTimezone() {
  const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (!detected) { toast('Could not detect timezone from browser.', 'error'); return; }
  document.getElementById('timezone-input').value = detected;
  const note = document.getElementById('timezone-detected');
  if (note) { note.textContent = `Detected: ${detected}`; note.style.display = 'block'; }
  toast('Timezone detected. Click Save to apply.');
}

async function saveTimezone() {
  const val = document.getElementById('timezone-input').value.trim();
  const status = document.getElementById('timezone-save-status');
  if (!val) return;
  try {
    await api('/settings/sending-timezone', { method: 'POST', body: JSON.stringify({ timezone: val }) });
    status.style.color = 'var(--green)';
    status.textContent = '✓ Saved';
    setTimeout(() => status.textContent = '', 3000);
  } catch(e) {
    status.style.color = 'var(--red)';
    status.textContent = 'Error: ' + e.message;
  }
}

// ── Settings: Calendly Integration ────────────────────────────────────────────
async function loadCalendlyLink() {
  try {
    const d = await api('/settings/calendly-link');
    const input = document.getElementById('calendly-link-input');
    const freqSel = document.getElementById('calendly-frequency');
    const status = document.getElementById('calendly-status');
    if (d.calendly_link) {
      input.value = d.calendly_link;
      freqSel.value = d.calendly_frequency || 'every';
      const freqLabel = freqSel.options[freqSel.selectedIndex]?.text || d.calendly_frequency;
      status.innerHTML = `&#9989; Link saved. Frequency: ${esc(freqLabel)}.`;
      status.style.color = 'var(--green)';
    } else {
      status.textContent = 'No link set. Emails will be generated without a booking link.';
      status.style.color = 'var(--text-muted)';
    }
  } catch(e) { console.error('loadCalendlyLink:', e); }
}

async function saveCalendlyLink() {
  const input = document.getElementById('calendly-link-input');
  const freqSel = document.getElementById('calendly-frequency');
  const status = document.getElementById('calendly-status');
  const link = input.value.trim();
  const freq = freqSel.value;
  if (!link && freq !== 'off') { status.textContent = 'Enter a Calendly URL first.'; status.style.color = 'var(--red)'; return; }
  // FIX-39: validate URL format before saving
  if (link) {
    let parsed;
    try { parsed = new URL(link); }
    catch (_) { status.textContent = 'Not a valid URL.'; status.style.color = 'var(--red)'; toast('Invalid URL.', 'error'); return; }
    if (!/^https?:$/.test(parsed.protocol) || !/^(calendly\.com|cal\.com)$/i.test(parsed.hostname)) {
      status.textContent = 'URL must be a https://calendly.com/... or https://cal.com/... link.';
      status.style.color = 'var(--red)';
      toast('Only Calendly or cal.com URLs are accepted.', 'error');
      return;
    }
  }
  try {
    await api('/settings/calendly-link', { method: 'POST', body: JSON.stringify({ calendly_link: link, calendly_frequency: freq }) });
    const freqLabel = freqSel.options[freqSel.selectedIndex]?.text || freq;
    status.innerHTML = `&#9989; Saved! Frequency: ${esc(freqLabel)}.`;
    status.style.color = 'var(--green)';
    toast('Calendly settings saved.', 'success');
  } catch(e) {
    status.textContent = 'Error: ' + e.message;
    status.style.color = 'var(--red)';
  }
}

async function clearCalendlyLink() {
  const input = document.getElementById('calendly-link-input');
  const freqSel = document.getElementById('calendly-frequency');
  const status = document.getElementById('calendly-status');
  try {
    await api('/settings/calendly-link', { method: 'POST', body: JSON.stringify({ calendly_link: '', calendly_frequency: 'off' }) });
    input.value = '';
    freqSel.value = 'off';
    status.textContent = 'Link removed. Emails will be generated without a booking link.';
    status.style.color = 'var(--text-muted)';
    toast('Calendly link removed.', 'info');
  } catch(e) {
    status.textContent = 'Error: ' + e.message;
    status.style.color = 'var(--red)';
  }
}

// ── Settings: Sending window ───────────────────────────────────────────────────
async function loadSendingWindow() {
  try {
    const d = await api('/settings/sending-window');
    document.getElementById('window-start-input').value = d.start || '08:00';
    document.getElementById('window-end-input').value   = d.end   || '17:00';
  } catch(e) { console.error('loadSendingWindow:', e); }
}

async function saveSendingWindow() {
  const start  = document.getElementById('window-start-input').value;
  const end    = document.getElementById('window-end-input').value;
  const status = document.getElementById('window-save-status');
  if (!start || !end) return;
  try {
    await api('/settings/sending-window', { method: 'POST', body: JSON.stringify({ start, end }) });
    status.style.color = 'var(--green)';
    status.textContent = '✓ Saved';
    setTimeout(() => status.textContent = '', 3000);
  } catch(e) {
    status.style.color = 'var(--red)';
    status.textContent = 'Error: ' + e.message;
  }
}

// ── Settings: LinkedIn Accounts ────────────────────────────────────────────────

async function loadLinkedInAccounts() {
  const container = document.getElementById('li-accounts-list');
  if (!container) return;
  container.innerHTML = '<span style="color:var(--text-muted);">Loading...</span>';
  try {
    const data = await api('/linkedin-accounts');
    const items = data.items || [];
    if (!items.length) {
      container.innerHTML = '<span style="color:var(--text-muted);">No LinkedIn accounts configured yet. Click "+ Add Account" to get started.</span>';
      return;
    }
    container.innerHTML = items.map(a => {
      const badges = [];
      if (a.sales_nav_agent_id) badges.push('<span style="display:inline-block;padding:2px 7px;border-radius:var(--r-pill);font-size:10px;background:var(--green-bg);color:var(--green);">Sales Nav</span>');
      else badges.push('<span style="display:inline-block;padding:2px 7px;border-radius:var(--r-pill);font-size:10px;background:var(--surface2);color:var(--text-muted);">Sales Nav</span>');
      if (a.connect_agent_id) badges.push('<span style="display:inline-block;padding:2px 7px;border-radius:var(--r-pill);font-size:10px;background:var(--green-bg);color:var(--green);">Connect</span>');
      else badges.push('<span style="display:inline-block;padding:2px 7px;border-radius:var(--r-pill);font-size:10px;background:var(--surface2);color:var(--text-muted);">Connect</span>');
      if (a.message_agent_id) badges.push('<span style="display:inline-block;padding:2px 7px;border-radius:var(--r-pill);font-size:10px;background:var(--green-bg);color:var(--green);">Message</span>');
      else badges.push('<span style="display:inline-block;padding:2px 7px;border-radius:var(--r-pill);font-size:10px;background:var(--surface2);color:var(--text-muted);">Message</span>');
      if (a.inbox_scraper_agent_id) badges.push('<span style="display:inline-block;padding:2px 7px;border-radius:var(--r-pill);font-size:10px;background:var(--green-bg);color:var(--green);">Inbox Scraper</span>');
      else badges.push('<span style="display:inline-block;padding:2px 7px;border-radius:var(--r-pill);font-size:10px;background:var(--surface2);color:var(--text-muted);">Inbox Scraper</span>');
      const pbBadge = a.has_pb_api_key
        ? '<span style="display:inline-block;padding:2px 7px;border-radius:var(--r-pill);font-size:10px;background:var(--green-bg);color:var(--green);">PB Key</span>'
        : '<span style="display:inline-block;padding:2px 7px;border-radius:var(--r-pill);font-size:10px;background:var(--surface2);color:var(--text-muted);">PB Key</span>';
      const urlLink = a.linkedin_profile_url
        ? ` <a href="${a.linkedin_profile_url}" target="_blank" rel="noopener" style="font-size:11px;color:var(--text-muted);">profile</a>`
        : '';
      return `<div style="display:flex;align-items:center;justify-content:space-between;padding:10px 12px;border:1px solid var(--border);border-radius:var(--r-sm);margin-bottom:6px;background:var(--surface2);">
        <div>
          <div style="font-weight:600;font-size:13px;display:flex;align-items:center;gap:6px;">
            <span onclick="event.stopPropagation();_renameLinkedInAccount('${a.id}',this)" style="cursor:pointer;" title="Click to rename">${_esc(a.name)}</span>
            ${urlLink}
          </div>
          <div style="display:flex;gap:4px;margin-top:5px;flex-wrap:wrap;">${pbBadge} ${badges.join(' ')}</div>
        </div>
        <div style="display:flex;gap:6px;">
          <button class="btn btn-outline btn-sm" onclick="validateLinkedInAccount('${a.id}')" title="Check all phantom IDs are valid in PB">Validate</button>
          <button class="btn btn-outline btn-sm" onclick="showEditLinkedInAccount('${a.id}')">Edit</button>
          <button class="btn btn-outline btn-sm" style="color:var(--red);" onclick="deleteLinkedInAccount('${a.id}','${_esc(a.name)}')">Delete</button>
        </div>
        <div id="li-acct-health-${a.id}" style="margin-top:6px;font-size:11px;"></div>
      </div>`;
    }).join('');
    // Auto-validate all accounts so user immediately sees phantom health
    for (const a of items) {
      validateLinkedInAccount(a.id);
    }
  } catch (e) {
    container.innerHTML = '<span style="color:var(--red);">Error loading accounts: ' + _esc(e.message) + '</span>';
  }
}

// In-memory cache of fetched accounts for the edit form
let _liAccountsCache = [];

async function _fetchLiAccounts() {
  try {
    const data = await api('/linkedin-accounts');
    _liAccountsCache = data.items || [];
  } catch (e) { _liAccountsCache = []; }
  return _liAccountsCache;
}

function _liAccountFormHtml(acct) {
  // acct is null for create, or an account object for edit
  const n = acct ? _esc(acct.name) : '';
  const url = acct ? _esc(acct.linkedin_profile_url || '') : '';
  const sn = acct ? _esc(acct.sales_nav_agent_id || '') : '';
  const cn = acct ? _esc(acct.connect_agent_id || '') : '';
  const mn = acct ? _esc(acct.message_agent_id || '') : '';
  const ib = acct ? _esc(acct.inbox_scraper_agent_id || '') : '';
  const pbPlaceholder = acct && acct.has_pb_api_key ? '(encrypted - leave blank to keep)' : 'e.g. abc123def456...';
  return `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
      <div class="form-group" style="margin:0;">
        <label style="font-size:11px;">Name *</label>
        <input type="text" id="li-acct-name" class="input" value="${n}" placeholder="e.g. Vishnu Saran" style="font-size:12px;" />
      </div>
      <div class="form-group" style="margin:0;">
        <label style="font-size:11px;">LinkedIn Profile URL</label>
        <input type="text" id="li-acct-url" class="input" value="${url}" placeholder="https://linkedin.com/in/..." style="font-size:12px;" />
      </div>
      <div class="form-group" style="margin:0;">
        <label style="font-size:11px;">PB API Key</label>
        <input type="password" id="li-acct-pb-key" class="input" placeholder="${pbPlaceholder}" style="font-size:12px;" />
        <div style="font-size:10px;color:var(--text-muted);margin-top:2px;">Leave blank to use global PHANTOMBUSTER_API_KEY.</div>
      </div>
      <div class="form-group" style="margin:0;">
        <label style="font-size:11px;">Sales Nav Agent ID</label>
        <input type="text" id="li-acct-salesnav" class="input" value="${sn}" placeholder="e.g. 5974719660736073" style="font-size:12px;" />
      </div>
      <div class="form-group" style="margin:0;">
        <label style="font-size:11px;">Connect Agent ID</label>
        <input type="text" id="li-acct-connect" class="input" value="${cn}" placeholder="e.g. 5974719660736073" style="font-size:12px;" />
      </div>
      <div class="form-group" style="margin:0;">
        <label style="font-size:11px;">Message Agent ID</label>
        <input type="text" id="li-acct-message" class="input" value="${mn}" placeholder="e.g. 7012345678901234" style="font-size:12px;" />
      </div>
      <div class="form-group" style="margin:0;">
        <label style="font-size:11px;">Inbox Scraper Agent ID</label>
        <input type="text" id="li-acct-inbox" class="input" value="${ib}" placeholder="e.g. 8123456789012345" style="font-size:12px;" />
      </div>
    </div>
    <div style="display:flex;gap:8px;margin-top:14px;">
      <button class="btn btn-primary btn-sm" id="li-acct-save-btn" onclick="saveLinkedInAccount('${acct ? acct.id : ''}')">Save</button>
      <button class="btn btn-outline btn-sm" onclick="document.getElementById('li-acct-form-overlay').remove();">Cancel</button>
      <span id="li-acct-form-msg" style="font-size:11px;margin-left:8px;"></span>
    </div>`;
}

function _showLiAccountForm(title, acct) {
  // Remove any existing overlay
  const existing = document.getElementById('li-acct-form-overlay');
  if (existing) existing.remove();
  const overlay = document.createElement('div');
  overlay.id = 'li-acct-form-overlay';
  overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.4);display:flex;align-items:center;justify-content:center;z-index:9999;';
  overlay.innerHTML = `<div style="background:var(--surface);border-radius:var(--r);padding:20px 24px;width:560px;max-width:95vw;max-height:90vh;overflow:auto;box-shadow:0 8px 32px rgba(0,0,0,0.2);">
    <div style="font-weight:600;font-size:15px;margin-bottom:14px;">${title}</div>
    ${_liAccountFormHtml(acct)}
  </div>`;
  // Close on background click
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  document.body.appendChild(overlay);
}

function showAddLinkedInAccount() {
  _showLiAccountForm('Add LinkedIn Account', null);
}

async function showEditLinkedInAccount(id) {
  const accounts = await _fetchLiAccounts();
  const acct = accounts.find(a => a.id === id);
  if (!acct) { toast('Account not found', 'error'); return; }
  _showLiAccountForm('Edit LinkedIn Account', acct);
}

async function saveLinkedInAccount(id) {
  const msgEl = document.getElementById('li-acct-form-msg');
  const name = document.getElementById('li-acct-name').value.trim();
  if (!name) { msgEl.textContent = 'Name is required.'; msgEl.style.color = 'var(--red)'; return; }

  const body = { name };
  const url = document.getElementById('li-acct-url').value.trim();
  const pbKey = document.getElementById('li-acct-pb-key').value.trim();
  const sn = document.getElementById('li-acct-salesnav').value.trim();
  const cn = document.getElementById('li-acct-connect').value.trim();
  const mn = document.getElementById('li-acct-message').value.trim();
  const ib = document.getElementById('li-acct-inbox').value.trim();

  if (url) body.linkedin_profile_url = url;
  else if (id) body.linkedin_profile_url = '';  // clear on edit
  if (pbKey) body.pb_api_key = pbKey;
  if (sn) body.sales_nav_agent_id = sn;
  else if (id) body.sales_nav_agent_id = '';
  if (cn) body.connect_agent_id = cn;
  else if (id) body.connect_agent_id = '';
  if (mn) body.message_agent_id = mn;
  else if (id) body.message_agent_id = '';
  if (ib) body.inbox_scraper_agent_id = ib;
  else if (id) body.inbox_scraper_agent_id = '';

  const saveBtn = document.getElementById('li-acct-save-btn');
  saveBtn.disabled = true; saveBtn.style.opacity = '0.6';
  msgEl.textContent = 'Saving...'; msgEl.style.color = 'var(--text-muted)';

  try {
    if (id) {
      await api('/linkedin-accounts/' + id, { method: 'PATCH', body: JSON.stringify(body) });
    } else {
      await api('/linkedin-accounts', { method: 'POST', body: JSON.stringify(body) });
    }
    const overlay = document.getElementById('li-acct-form-overlay');
    if (overlay) overlay.remove();
    toast(id ? 'LinkedIn account updated.' : 'LinkedIn account created.');
    loadLinkedInAccounts();
  } catch (e) {
    msgEl.textContent = 'Error: ' + e.message; msgEl.style.color = 'var(--red)';
  } finally {
    saveBtn.disabled = false; saveBtn.style.opacity = '';
  }
}

async function _renameLinkedInAccount(id, el) {
  const current = el.textContent;
  const newName = await customPrompt('Rename this account:', current);
  if (!newName || newName === current) return;
  try {
    await api('/linkedin-accounts/' + id, { method: 'PATCH', body: JSON.stringify({ name: newName }) });
    el.textContent = newName;
    toast('Renamed to "' + newName + '"', 'success');
  } catch (e) { toast('Rename failed: ' + e.message, 'error'); }
}

async function syncAccountsFromPB() {
  const btn = event?.target;
  if (btn) { btn.disabled = true; btn.textContent = 'Syncing...'; }
  try {
    const res = await api('/linkedin-accounts/sync-from-pb', { method: 'POST' });
    toast(`Synced ${res.synced} account(s): ${res.created} created, ${res.updated} updated`, 'success');
    loadLinkedInAccounts();
  } catch (e) {
    toast('Sync failed: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Sync from PhantomBuster'; }
  }
}

async function validateLinkedInAccount(id) {
  const el = document.getElementById('li-acct-health-' + id);
  if (el) el.innerHTML = '<span style="color:var(--text-muted);">Validating phantoms...</span>';
  try {
    const res = await api('/linkedin-accounts/' + id + '/validate', { method: 'POST' });
    if (!el) return;
    const phantoms = res.phantoms || {};
    const lines = Object.entries(phantoms).map(([label, p]) => {
      if (p.status === 'valid') return `<span style="color:#059669;">✓ ${label}: ${p.name}</span>`;
      if (p.status === 'not_configured') return `<span style="color:var(--text-muted);">— ${label}: not configured</span>`;
      if (p.status === 'deleted') return `<span style="color:var(--red);">✗ ${label}: DELETED from PB</span>`;
      if (p.status === 'auth_error') return `<span style="color:var(--red);">✗ ${label}: PB auth error</span>`;
      return `<span style="color:var(--red);">✗ ${label}: ${p.detail || p.status}</span>`;
    });
    const color = res.overall === 'healthy' ? '#059669' : (res.overall === 'degraded' ? '#ca8a04' : 'var(--red)');
    let identityHtml = '';
    if (res.detected_identity) {
      const di = res.detected_identity;
      const link = di.linkedin_url ? ` <a href="${esc(di.linkedin_url)}" target="_blank" style="color:#0a66c2;">↗</a>` : '';
      identityHtml = `<div style="margin-bottom:4px;font-weight:600;color:#0a66c2;">LinkedIn: ${esc(di.name)}${link}</div>`;
    }
    el.innerHTML = identityHtml + `<div style="color:${color};font-weight:600;margin-bottom:2px;">${res.overall.toUpperCase()}</div>` + lines.join('<br>');
    // If identity was detected and account name changed, refresh the list
    if (res.detected_identity && res.account_name !== 'Primary Outreach Account') {
      const nameEl = el.closest('[style*="display:flex"]')?.querySelector('[style*="font-weight:600;font-size:13px"]');
      if (nameEl) nameEl.textContent = res.account_name;
    }
  } catch (e) {
    if (el) el.innerHTML = '<span style="color:var(--red);">Validation failed: ' + esc(e.message) + '</span>';
  }
}

async function deleteLinkedInAccount(id, name) {
  if (!await customConfirm('Deactivate LinkedIn account "' + name + '"? It will no longer be available for campaigns.', { destructive: true })) return;
  try {
    await api('/linkedin-accounts/' + id, { method: 'DELETE' });
    toast('LinkedIn account deactivated.');
    loadLinkedInAccounts();
  } catch (e) {
    toast('Error: ' + e.message, 'error');
  }
}

// helper: escape HTML to prevent XSS in dynamic content
function _esc(s) { if (!s) return ''; const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

// ── Settings: PhantomBuster Credentials ───────────────────────────────────────

async function loadPbCredentials() {
  const statusEl = document.getElementById('pb-cred-status');
  // Default state while loading
  statusEl.textContent = 'Not configured';
  statusEl.style.background = 'var(--surface2)';
  statusEl.style.color = 'var(--text-muted)';
  try {
    const data = await api('/credentials/phantombuster');
    if (data.configured) {
      statusEl.textContent = 'Connected';
      statusEl.style.background = 'var(--green-bg)';
      statusEl.style.color = 'var(--green)';
      // Fill masked values as placeholders
      const k = data.keys || {};
      if (k.api_key) document.getElementById('pb-cred-api-key').placeholder = k.api_key;
      if (k.connect_agent_id) document.getElementById('pb-cred-connect-id').placeholder = k.connect_agent_id;
      if (k.message_agent_id) document.getElementById('pb-cred-message-id').placeholder = k.message_agent_id;
      if (k.list_id) document.getElementById('pb-cred-list-id').placeholder = k.list_id;
    }
  } catch (e) {
    console.error('loadPbCredentials:', e);
    statusEl.textContent = 'Error loading';
    statusEl.style.background = 'var(--red-bg, #ffeef0)';
    statusEl.style.color = 'var(--red)';
  }
}

function _pbButtons() {
  return Array.from(document.querySelectorAll('[onclick^="savePbCredentials"],[onclick^="testPbCredentials"],[onclick^="deletePbCredentials"]'));
}
function _setPbButtonsDisabled(disabled) {
  _pbButtons().forEach(b => { b.disabled = disabled; b.style.opacity = disabled ? '0.6' : ''; });
}

async function savePbCredentials() {
  const msgEl = document.getElementById('pb-cred-msg');
  const body = {};
  const apiKey = document.getElementById('pb-cred-api-key').value.trim();
  const connectId = document.getElementById('pb-cred-connect-id').value.trim();
  const messageId = document.getElementById('pb-cred-message-id').value.trim();
  const listId = document.getElementById('pb-cred-list-id').value.trim();
  if (apiKey) body.api_key = apiKey;
  if (connectId) body.connect_agent_id = connectId;
  if (messageId) body.message_agent_id = messageId;
  if (listId) body.list_id = listId;
  if (!Object.keys(body).length) { msgEl.textContent = 'Enter at least one field.'; msgEl.style.color = 'var(--red)'; return; }
  _setPbButtonsDisabled(true);
  msgEl.textContent = 'Saving...'; msgEl.style.color = 'var(--text-muted)';
  try {
    await api('/credentials/phantombuster', { method: 'PUT', body: JSON.stringify(body) });
    msgEl.textContent = 'Saved!'; msgEl.style.color = 'var(--green)';
    // Clear input values (keep as placeholders on reload)
    document.getElementById('pb-cred-api-key').value = '';
    document.getElementById('pb-cred-connect-id').value = '';
    document.getElementById('pb-cred-message-id').value = '';
    document.getElementById('pb-cred-list-id').value = '';
    loadPbCredentials();
    setTimeout(() => { msgEl.textContent = ''; }, 3000);
  } catch (e) { msgEl.textContent = 'Error: ' + e.message; msgEl.style.color = 'var(--red)'; }
  finally { _setPbButtonsDisabled(false); }
}

async function testPbCredentials() {
  const msgEl = document.getElementById('pb-cred-msg');
  _setPbButtonsDisabled(true);
  msgEl.textContent = 'Testing...'; msgEl.style.color = 'var(--text-muted)';
  try {
    const result = await api('/credentials/phantombuster/test', { method: 'POST' });
    if (result.ok) {
      msgEl.textContent = result.detail || 'Connected!'; msgEl.style.color = 'var(--green)';
    } else {
      msgEl.textContent = result.error || 'Connection failed'; msgEl.style.color = 'var(--red)';
    }
  } catch (e) { msgEl.textContent = 'Error: ' + e.message; msgEl.style.color = 'var(--red)'; }
  finally { _setPbButtonsDisabled(false); }
}

async function deletePbCredentials() {
  if (!await customConfirm('Remove all PhantomBuster credentials? LinkedIn automation will stop for your account.', {destructive: true})) return;
  _setPbButtonsDisabled(true);
  try {
    await api('/credentials/phantombuster', { method: 'DELETE' });
    toast('PhantomBuster credentials removed.');
    loadPbCredentials();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  finally { _setPbButtonsDisabled(false); }
}

// ── Settings: Suppression list ────────────────────────────────────────────────
let _suppressionOffset = 0;
const _SUPPRESSION_PAGE_SIZE = 200;

async function loadSuppressionList(offset) {
  if (offset !== undefined) _suppressionOffset = offset;
  const tbody = document.getElementById('suppression-tbody');
  try {
    const data = await api(`/unsubscribes/?limit=${_SUPPRESSION_PAGE_SIZE}&offset=${_suppressionOffset}`);
    const rows = data.items || [];
    const total = data.total || 0;
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="4" style="color:var(--text-muted);text-align:center;">No suppressed emails.</td></tr>';
      const pag = document.getElementById('suppression-pagination');
      if (pag) pag.innerHTML = '';
      return;
    }
    tbody.innerHTML = rows.map(r => `<tr>
      <td>${esc(r.email)}</td>
      <td><span class="badge badge-gray">${esc(r.reason || '')}</span></td>
      <td style="color:var(--text-muted);font-size:12px;">${r.created_at ? esc(new Date(r.created_at).toLocaleDateString()) : ''}</td>
      <td><button class="btn btn-outline btn-sm" style="color:var(--red);" data-email="${esc(r.email)}" onclick="removeUnsubscribe(this.dataset.email)" title="Remove.">Remove</button></td>
    </tr>`).join('');
    // Render pagination if needed
    const pag = document.getElementById('suppression-pagination');
    if (pag && total > _SUPPRESSION_PAGE_SIZE) {
      const page = Math.floor(_suppressionOffset / _SUPPRESSION_PAGE_SIZE) + 1;
      const totalPages = Math.ceil(total / _SUPPRESSION_PAGE_SIZE);
      pag.innerHTML = `<div style="display:flex;align-items:center;gap:8px;justify-content:center;margin-top:8px;">
        <button class="btn btn-outline btn-sm" ${page <= 1 ? 'disabled' : ''} onclick="loadSuppressionList(${_suppressionOffset - _SUPPRESSION_PAGE_SIZE})" title="Prev.">← Prev</button>
        <span style="font-size:13px;color:var(--text-muted);">Page ${page} of ${totalPages} (${total} total)</span>
        <button class="btn btn-outline btn-sm" ${page >= totalPages ? 'disabled' : ''} onclick="loadSuppressionList(${_suppressionOffset + _SUPPRESSION_PAGE_SIZE})" title="Go to the next page of suppressed addresses.">Next →</button>
      </div>`;
    } else if (pag) {
      pag.innerHTML = total ? `<div style="text-align:center;margin-top:8px;font-size:13px;color:var(--text-muted);">${total} email${total === 1 ? '' : 's'} suppressed</div>` : '';
    }
  } catch(e) {
    tbody.innerHTML = '<tr><td colspan="4" style="color:var(--red);">Failed to load.</td></tr>';
  }
}

async function addUnsubscribe() {
  const email = document.getElementById('unsub-email-input').value.trim();
  const reason = document.getElementById('unsub-reason-input').value;
  const status = document.getElementById('suppression-status');
  if (!email) return;
  try {
    await api('/unsubscribes/', { method: 'POST', body: JSON.stringify({ email, reason }) });
    document.getElementById('unsub-email-input').value = '';
    status.style.color = 'var(--green)';
    status.textContent = `✓ ${email} suppressed.`;
    setTimeout(() => status.textContent = '', 3000);
    loadSuppressionList();
  } catch(e) {
    status.style.color = 'var(--red)';
    status.textContent = 'Error: ' + e.message;
  }
}

async function removeUnsubscribe(email) {
  try {
    await api('/unsubscribes/' + encodeURIComponent(email), { method: 'DELETE' });
    loadSuppressionList();
  } catch(e) {
    toast('Failed to remove: ' + e.message, 'error');
  }
}

async function importSuppressionFile() {
  const input = document.getElementById('suppression-file-input');
  const statusEl = document.getElementById('suppression-import-status');
  const nameEl = document.getElementById('suppression-file-name');
  const file = input.files[0];
  if (!file) return;

  nameEl.textContent = file.name;
  statusEl.style.color = 'var(--text-muted)';
  statusEl.textContent = 'Uploading…';

  const form = new FormData();
  form.append('file', file);

  try {
    const res = await fetch('/api/unsubscribes/import', {
      method: 'POST',
      headers: _authToken ? { 'Authorization': `Bearer ${_authToken}` } : {},
      body: form,
    });
    let data;
    try { data = await res.json(); } catch(_) { throw new Error(`HTTP ${res.status} ${res.statusText}`); }
    if (!res.ok) throw new Error(data.detail || 'Import failed');
    statusEl.style.color = 'var(--green)';
    statusEl.textContent = `✓ ${data.added} email${data.added !== 1 ? 's' : ''} added` +
      (data.skipped ? `, ${data.skipped} already suppressed` : '') + '.';
    input.value = '';
    loadSuppressionList();
  } catch(e) {
    statusEl.style.color = 'var(--red)';
    statusEl.textContent = 'Error: ' + e.message;
  }
}

// ── Inboxes (removed — Instantly-era feature) ──────────────────────────────────
function loadInboxes() {} // stub — removed
function loadDomainHealth() {} // stub — removed

/* --- original loadInboxes / saveReplyTo / saveActiveInboxes / loadDomainHealth removed --- */
/* replaced with stubs above to avoid runtime errors from any residual callers */

// ── Smart validate ─────────────────────────────────────────────────────────────
function validateSmart() {
  if (selectedProspects.size > 0) {
    _runValidation([...selectedProspects]);
  } else {
    _runValidation(null);
  }
}

// Update validate button label based on selection
document.addEventListener('change', (e) => {
  if (e.target.closest('#prospects-tbody')) {
    const btn = document.getElementById('validate-smart-btn');
    if (!btn) return;
    btn.textContent = selectedProspects.size > 0
      ? `Validate ${selectedProspects.size} Selected`
      : 'Validate All';
  }
});

// ── CSV import ─────────────────────────────────────────────────────────────────
function toggleCsvImport() {
  const panel = document.getElementById('csv-import-panel');
  if (!panel) return;
  const isVisible = panel.style.display !== 'none';
  panel.style.display = isVisible ? 'none' : 'block';
  // Highlight the Import button to show panel is open
  const btns = document.querySelectorAll('.leads-toolbar button');
  btns.forEach(b => { if (b.textContent.includes('Import')) b.style.background = isVisible ? '' : 'var(--navy-light)'; });
}

async function importCsv() {
  const fileInput = document.getElementById('csv-import-file');
  const segmentInput = document.getElementById('csv-import-segment');
  const status = document.getElementById('csv-import-status');
  const file = fileInput?.files?.[0];
  if (!file) { toast('Select a CSV file first.', 'error'); return; }
  if (!file.name.endsWith('.csv')) { toast('Please select a CSV file.', 'error'); return; }
  const segmentName = segmentInput?.value.trim();
  if (!segmentName) { toast('Enter a segment name. Contacts must belong to a segment.', 'error'); segmentInput?.focus(); return; }
  status.textContent = 'Importing…';
  status.style.color = 'var(--text-muted)';
  const form = new FormData();
  form.append('file', file);
  form.append('segment_name', segmentName);
  try {
    const res = await fetch('/api/prospects/import', {
      method: 'POST',
      headers: _authToken ? { 'Authorization': `Bearer ${_authToken}` } : {},
      body: form,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }
    const data = await res.json();
    status.style.color = 'var(--green)';
    status.textContent = `✓ ${data.imported} imported, ${data.skipped_duplicates} duplicates skipped${data.errors ? ', ' + data.errors + ' errors' : ''}`;
    toast(`${data.imported} prospects imported`);
    if (fileInput) fileInput.value = '';
    await loadProspects();
    await loadSetupChecklist();
  } catch(e) {
    status.style.color = 'var(--red)';
    status.textContent = 'Import failed: ' + e.message;
    toast('Import failed: ' + e.message, 'error');
  }
}

// ── Sales Navigator Import ──────────────────────────────────────────────────

function toggleSalesNavImport() {
  const panel = document.getElementById('salesnav-import-panel');
  if (!panel) return;
  const isVisible = panel.style.display !== 'none';
  panel.style.display = isVisible ? 'none' : 'block';
  const btn = document.getElementById('salesnav-import-btn');
  if (btn) btn.style.background = isVisible ? '' : 'var(--navy-light)';
  // Hide CSV panel when opening Sales Nav panel
  if (!isVisible) {
    const csvPanel = document.getElementById('csv-import-panel');
    if (csvPanel) csvPanel.style.display = 'none';
  }
  if (!isVisible) _loadSalesNavFormData();
}

async function _loadSalesNavFormData() {
  // Load LinkedIn accounts that have sales_nav_agent_id
  try {
    const acctRes = await api('/linkedin-accounts');
    const accounts = (acctRes.items || []).filter(a => a.sales_nav_agent_id);
    const sel = document.getElementById('salesnav-account-select');
    if (sel) {
      sel.innerHTML = accounts.length
        ? accounts.map(a => `<option value="${a.id}">${a.name}</option>`).join('')
        : '<option value="">No accounts with Sales Nav</option>';
    }
  } catch(e) {
    const sel = document.getElementById('salesnav-account-select');
    if (sel) sel.innerHTML = '<option value="">Failed to load accounts</option>';
  }
  // Load segments for the dropdown
  try {
    const segRes = await api('/prospects/segments');
    const segSel = document.getElementById('salesnav-segment-select');
    if (segSel) {
      segSel.innerHTML = '<option value="">-- No segment --</option>'
        + (segRes || []).map(s => `<option value="${s.id}">${s.name}</option>`).join('');
    }
  } catch(e) { /* ignore */ }
}

async function startSalesNavImport() {
  const urlInput = document.getElementById('salesnav-search-url');
  const accountSel = document.getElementById('salesnav-account-select');
  const segmentSel = document.getElementById('salesnav-segment-select');
  const maxLeadsInput = document.getElementById('salesnav-max-leads');
  const status = document.getElementById('salesnav-import-status');

  const searchUrl = (urlInput?.value || '').trim();
  const accountId = accountSel?.value;
  const segmentId = segmentSel?.value || null;
  const maxLeads = parseInt(maxLeadsInput?.value) || 100;

  if (!searchUrl || !searchUrl.includes('linkedin.com')) {
    toast('Enter a valid LinkedIn Sales Navigator search URL.', 'error');
    urlInput?.focus();
    return;
  }
  if (!accountId) {
    toast('Select a LinkedIn account with Sales Nav configured.', 'error');
    return;
  }

  status.style.color = 'var(--text-muted)';
  status.textContent = 'Launching Sales Nav phantom...';

  try {
    const body = {
      search_url: searchUrl,
      account_id: accountId,
      max_leads: maxLeads,
    };
    if (segmentId) body.segment_id = segmentId;

    const res = await api('/prospects/import-salesnav', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    if (!res.job_id) {
      throw new Error('No job_id returned from server.');
    }

    status.textContent = 'Phantom launched. Polling for results...';
    _pollSalesNavStatus(res.job_id, accountId, segmentId);
  } catch(e) {
    status.style.color = 'var(--red)';
    status.textContent = 'Launch failed: ' + (e.message || e);
    toast('Sales Nav import failed: ' + (e.message || e), 'error');
  }
}

async function _pollSalesNavStatus(jobId, accountId, segmentId) {
  const status = document.getElementById('salesnav-import-status');
  const pollInterval = 5000; // 5 seconds
  const maxPolls = 120; // 10 minutes max (120 * 5s)
  let pollCount = 0;
  let errorCount = 0;

  const poll = async () => {
    pollCount++;
    if (pollCount > maxPolls) {
      status.style.color = 'var(--red)';
      status.textContent = 'Timed out waiting for phantom to finish. Check PhantomBuster dashboard.';
      return;
    }
    try {
      let url = `/prospects/import-salesnav/status?job_id=${encodeURIComponent(jobId)}&account_id=${encodeURIComponent(accountId)}`;
      if (segmentId) url += `&segment_id=${encodeURIComponent(segmentId)}`;
      const res = await api(url);
      errorCount = 0; // reset on success

      if (res.status === 'running') {
        status.style.color = 'var(--text-muted)';
        status.textContent = 'Phantom still running... checking again in 5s.';
        setTimeout(poll, pollInterval);
      } else if (res.status === 'complete') {
        status.style.color = 'var(--green)';
        status.textContent = `Done: ${res.imported} imported, ${res.duplicates} duplicates skipped${res.failed ? ', ' + res.failed + ' failed' : ''}`;
        toast(`${res.imported} prospects imported from Sales Navigator`);
        await loadProspects();
      } else if (res.status === 'failed') {
        status.style.color = 'var(--red)';
        status.textContent = 'Import failed: ' + (res.detail || 'Unknown error');
        toast('Sales Nav import failed', 'error');
      } else {
        status.style.color = 'var(--red)';
        status.textContent = 'Unexpected status: ' + res.status;
      }
    } catch(e) {
      errorCount++;
      status.style.color = 'var(--red)';
      status.textContent = 'Poll error: ' + (e.message || e);
      if (errorCount < 3) {
        setTimeout(poll, pollInterval * 2);
      } else {
        status.textContent += ' (giving up after 3 consecutive errors)';
      }
    }
  };

  setTimeout(poll, pollInterval);
}

// ── Per-step instructions toggle + dynamic fields ──────────────────────────────
let _seqStepsCache = null;

async function loadStepInstructionFields() {
  const container = document.getElementById('step-instr-fields');
  if (!container) return;
  try {
    if (!_seqStepsCache) {
      const config = await api('/settings/sequence-config');
      _seqStepsCache = (config.steps || []).filter(s =>
        s.step_type === 'send_email' || s.step_type === 'send_linkedin_connect' || s.step_type === 'send_linkedin_message'
      );
    }
    container.innerHTML = '';
    let linkedinNum = 0;
    _seqStepsCache.forEach((step, i) => {
      const isLinkedIn = step.step_type.startsWith('send_linkedin');
      if (!isLinkedIn) return; // skip non-LinkedIn steps
      linkedinNum++;
      const action = step.step_type === 'send_linkedin_connect' ? 'Connect' : 'Message';
      const label = `LinkedIn ${action} (Day ${step.day_offset})`;
      const placeholder = step.step_type === 'send_linkedin_connect' ? 'e.g. Mention mutual interest in safety' : 'e.g. Share relevant insight';
      const div = document.createElement('div');
      div.className = 'form-group';
      div.style.marginBottom = '0';
      div.innerHTML = `<label>${label}</label><input type="text" id="b-step-instr-${i}" data-step-type="${step.step_type}" data-day="${step.day_offset}" placeholder="${placeholder}" style="font-size:12px;" />`;
      container.appendChild(div);
    });
  } catch(e) {
    container.innerHTML = '<div style="font-size:11px;color:var(--text-muted);">Could not load sequence steps.</div>';
  }
}

function toggleStepInstructions() {
  const body = document.getElementById('step-instr-body');
  const arrow = document.getElementById('step-instr-arrow');
  if (!body) return;
  const isOpen = body.style.display !== 'none';
  body.style.display = isOpen ? 'none' : 'block';
  if (arrow) arrow.textContent = isOpen ? '▶' : '▼';
  if (!isOpen) loadStepInstructionFields();
}

function collectStepInstructions() {
  const fields = document.querySelectorAll('[id^="b-step-instr-"]');
  const instructions = {};
  fields.forEach(f => {
    const val = f.value.trim();
    if (!val) return;
    const stepType = f.dataset.stepType;
    const day = f.dataset.day;
    const action = stepType === 'send_linkedin_connect' ? 'linkedin_connect' : 'linkedin_message';
    instructions[`${action}_day${day}`] = val;
  });
  return instructions;
}

// ── Sync style with feedback ───────────────────────────────────────────────────
async function syncStyleWithFeedback() {
  const btn = document.getElementById('sync-style-btn');
  const status = document.getElementById('style-save-status');
  if (btn) { btn.disabled = true; btn.textContent = '↺ Syncing…'; }
  try {
    const result = await api('/style/sync', { method: 'POST' });
    toast(`Style synced from ${result.emails_analyzed} sent emails.`);
    if (status) { status.style.color = 'var(--green)'; status.textContent = `✓ Synced from ${result.emails_analyzed} emails`; }
    // Reload the editor with the new style
    const data = await api('/settings/writing-style');
    const ta = document.getElementById('style-editor');
    if (ta && data.content) ta.value = data.content;
    setTimeout(() => { if (status) status.textContent = ''; }, 4000);
  } catch(e) {
    toast('Sync failed: ' + e.message, 'error');
    if (status) { status.style.color = 'var(--red)'; status.textContent = 'Sync failed'; }
    setTimeout(() => { if (status) status.textContent = ''; }, 4000);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '↺ Sync from Outlook'; }
  }
}

// ── Setup checklist ────────────────────────────────────────────────────────────
const CHECKLIST_ACTIONS = {
  style:    () => { switchTab('copywriting'); },
  timezone: () => { switchTab('settings'); setTimeout(() => toggleAccordion('timezone'), 100); },
  inboxes:  () => { switchTab('settings'); },
  leads:    () => { toggleAccordion('leads'); },
  campaign: () => { toggleAccordion('workflow'); },
};

async function loadSetupChecklist() {
  try {
    const d = await api('/settings/setup-status');
    if (d.all_done) {
      document.getElementById('setup-checklist').style.display = 'none';
      return;
    }
    if (localStorage.getItem('checklist-dismissed')) return;
    const el = document.getElementById('setup-checklist');
    const stepsEl = document.getElementById('setup-steps');
    if (!el || !stepsEl) return;
    const doneCount = d.steps.filter(s => s.done).length;
    stepsEl.innerHTML = d.steps.map((step, i) => {
      const done = step.done;
      const isNext = !done && d.steps.slice(0, i).every(s => s.done);
      const bg = done ? 'rgba(45,106,79,0.08)' : isNext ? 'var(--accent)' : 'var(--surface2)';
      const color = done ? 'var(--green)' : isNext ? '#fff' : 'var(--text-muted)';
      const border = done ? '1px solid rgba(45,106,79,0.25)' : isNext ? '1px solid var(--accent)' : '1px solid var(--border)';
      const cursor = done ? 'default' : 'pointer';
      const num = i + 1;
      return `<div onclick="${done ? '' : `CHECKLIST_ACTIONS['${step.key}']()`}" style="display:flex;align-items:center;gap:6px;padding:6px 14px;font-size:12px;font-weight:${isNext?'600':'500'};background:${bg};border:${border};color:${color};cursor:${cursor};border-radius:var(--r-sm);transition:all 0.15s;">
        <span style="font-size:11px;">${done ? '&#10003;' : num}</span>
        <span>${esc(step.label)}</span>
        ${isNext ? '<span style="font-size:10px;margin-left:2px;">&#8594;</span>' : ''}
      </div>`;
    }).join('');
    // Progress bar
    const pct = Math.round(doneCount / d.steps.length * 100);
    stepsEl.innerHTML += `<div style="width:100%;margin-top:8px;"><div style="background:var(--border);height:4px;border-radius:2px;overflow:hidden;"><div style="width:${pct}%;height:100%;background:var(--accent);transition:width 0.5s;"></div></div><div style="font-size:10px;color:var(--text-muted);margin-top:4px;">${doneCount}/${d.steps.length} complete</div></div>`;
    el.style.display = 'block';
  } catch(e) { console.error('loadSetupChecklist:', e); }
}

function dismissChecklist() {
  document.getElementById('setup-checklist').style.display = 'none';
  localStorage.setItem('checklist-dismissed', '1');
}

// ── Delete single sequence ────────────────────────────────────────────────────
async function deleteWfSequence(seqId) {
  if (!wfCampaignId) return;
  const seq = wfSequences.find(s => s.id === seqId);
  const name = seq ? `${seq.prospect_name || seq.prospect_email || 'this lead'}` : 'this lead';
  if (!await customConfirm(`Delete sequence for ${name}? You can regenerate it later.`, {destructive: true})) return;
  try {
    await api(`/campaigns/${wfCampaignId}/sequences/${seqId}`, { method: 'DELETE' });
    wfSequences = wfSequences.filter(s => s.id !== seqId);
    if (window._wfAllSequences) {
      window._wfAllSequences = window._wfAllSequences.filter(s => s.id !== seqId);
    }
    // Remove the row from the DOM immediately
    const row = document.querySelector(`tr[data-seq-id="${seqId}"]`);
    if (row) row.remove();
    // Update the count in the header and top bar
    const countEl = document.getElementById('wf-seq-count');
    if (countEl) countEl.textContent = `${wfSequences.length} lead${wfSequences.length!==1?'s':''} · Click any row to view full sequence`;
    const topLeads = document.getElementById('top-stat-leads');
    if (topLeads) topLeads.innerHTML = `<strong>${wfSequences.length}</strong> leads`;
    // Update pagination info
    _wfUpdatePageInfo();
    toast('Sequence deleted.');
  } catch(e) {
    toast('Delete failed: ' + e.message, 'error');
  }
}

// ── Sequence pagination ──────────────────────────────────────────────────────
const _WF_SEQ_PAGE_SIZE = 50;

function _wfSeqNext() {
  const seqs = window._wfAllSequences || wfSequences;
  const maxPage = Math.ceil(seqs.length / _WF_SEQ_PAGE_SIZE) - 1;
  if (window._wfSeqPage < maxPage) {
    window._wfSeqPage++;
    _wfRenderSeqPage();
  }
}

function _wfSeqPrev() {
  if (window._wfSeqPage > 0) {
    window._wfSeqPage--;
    _wfRenderSeqPage();
  }
}

function _wfRenderSeqPage() {
  const seqs = window._wfAllSequences || wfSequences;
  const start = window._wfSeqPage * _WF_SEQ_PAGE_SIZE;
  const pageRows = seqs.slice(start, start + _WF_SEQ_PAGE_SIZE);
  const tbody = document.getElementById('wf-seq-tbody');
  if (tbody) {
    tbody.innerHTML = pageRows.map(seq => buildUnifiedLeadRow(seq, wfOrchMap[seq.prospect_id])).join('');
  }
  _wfUpdatePageInfo();
}

function _wfUpdatePageInfo() {
  const seqs = window._wfAllSequences || wfSequences;
  const totalPages = Math.ceil(seqs.length / _WF_SEQ_PAGE_SIZE) || 1;
  const pageInfo = document.getElementById('wf-seq-page-info');
  if (pageInfo) pageInfo.textContent = `Page ${(window._wfSeqPage||0)+1} of ${totalPages}`;
  const prev = document.getElementById('wf-seq-prev');
  const next = document.getElementById('wf-seq-next');
  if (prev) prev.disabled = (window._wfSeqPage||0) <= 0;
  if (next) next.disabled = (window._wfSeqPage||0) >= totalPages - 1;
}

// ── Regenerate single sequence ─────────────────────────────────────────────────
async function regenerateWfSequence(seqId) {
  if (!wfCampaignId) return;
  const statusEl = document.getElementById(`save-status-${seqId}`);
  if (statusEl) { statusEl.style.color = 'var(--text-muted)'; statusEl.textContent = '↺ Regenerating…'; }
  // Disable buttons on this row
  const row = document.querySelector(`[data-seq-id="${seqId}"]`);
  const btns = row?.querySelectorAll('button');
  btns?.forEach(b => b.disabled = true);
  try {
    const updated = await api(`/campaigns/${wfCampaignId}/sequences/${seqId}/regenerate`, { method: 'POST' });
    // Update in-memory
    const idx = wfSequences.findIndex(s => s.id === seqId);
    if (idx !== -1) wfSequences[idx] = updated;
    // Update fields in DOM
    for (let n = 1; n <= 6; n++) {
      const email = updated[`email${n}`] || {};
      const subj = document.getElementById(`subj-${seqId}-${n}`);
      const body = document.getElementById(`body-${seqId}-${n}`);
      if (subj) subj.value = email.subject || '';
      if (body) body.value = email.body || '';
    }
    if (statusEl) { statusEl.style.color = 'var(--green)'; statusEl.textContent = '✓ Regenerated'; setTimeout(() => statusEl.textContent = '', 3000); }
    toast('Sequence regenerated.');
  } catch(e) {
    if (statusEl) { statusEl.style.color = 'var(--red)'; statusEl.textContent = 'Failed'; setTimeout(() => statusEl.textContent = '', 3000); }
    toast('Regenerate failed: ' + e.message, 'error');
  } finally {
    btns?.forEach(b => b.disabled = false);
  }
}

// ── Skip sequence ──────────────────────────────────────────────────────────────
async function skipWfSequence(seqId) {
  if (!wfCampaignId) return;
  if (!await customConfirm('Skip this lead? They won\'t be enrolled when the campaign launches.')) return;
  try {
    await api(`/campaigns/${wfCampaignId}/sequences/${seqId}`, {
      method: 'PATCH',
      body: JSON.stringify({ approved: false, status: 'skipped' }),
    });
    // Remove from in-memory and DOM
    wfSequences = wfSequences.filter(s => s.id !== seqId);
    const row = document.querySelector(`[data-seq-id="${seqId}"]`);
    if (row) row.remove();
    toast('Lead skipped.');
  } catch(e) { toast('Skip failed: ' + e.message, 'error'); }
}

// ══════════════════════════════════════════════════════════════════════════════
// SEQUENCES TAB
// ══════════════════════════════════════════════════════════════════════════════

let seqAllStates = [];
let seqAllTasks = [];
let seqFilterStatus = '';
let seqSearchTerm = '';
let seqDrawerLeadId = null;

// The sequence step definitions  -  loaded dynamically from config, with hardcoded fallback
let SEQ_STEPS = [
  { day: 1,  type: 'linkedin_connect', label: 'LinkedIn Connect' },
  { day: 7,  type: 'linkedin_message', label: 'LinkedIn Message' },
];
function _rebuildSeqSteps() {
  if (!_seqStepsCache || !_seqStepsCache.length) return;
  const typeMap = { send_email: 'email', send_linkedin_connect: 'linkedin_connect', send_linkedin_message: 'linkedin_message' };
  let emailNum = 0;
  SEQ_STEPS = _seqStepsCache.map(s => {
    const t = typeMap[s.step_type] || s.step_type;
    let label = s.label || '';
    if (t === 'email') { emailNum++; if (!label) label = `Email ${emailNum}`; }
    else if (t === 'linkedin_connect' && !label) label = 'LinkedIn Connect';
    else if (t === 'linkedin_message' && !label) label = 'LinkedIn Message';
    return { day: s.day_offset, type: t, label };
  });
  _rebuildEmailSeqMap();
}
function _rebuildEmailSeqMap() {
  for (const k in EMAIL_SEQ_MAP) delete EMAIL_SEQ_MAP[k];
  let n = 0; SEQ_STEPS.forEach((s, i) => { if (s.type === 'email') { n++; if (n <= 6) EMAIL_SEQ_MAP[i] = n; } });
}
// Build EMAIL_SEQ_MAP from SEQ_STEPS so it stays correct if steps are reordered
_rebuildEmailSeqMap();

async function loadSequences() {
  // Skip API calls if orchestration panel elements don't exist in the DOM
  if (!document.getElementById('seq-stats') && !document.getElementById('seq-tbody')) return;
  const showTerminal = document.getElementById('seq-show-terminal')?.checked || false;
  try {
    const [states, tasks] = await Promise.all([
      api(`/orchestration/states?include_terminal=${showTerminal}`).catch(() => []),
      api('/orchestration/tasks').catch(() => []),
    ]);
    seqAllStates = states;
    seqAllTasks = tasks;
    renderSeqStats(states, tasks);
    renderSeqTable(states);
    renderSeqTasks(tasks);
    // Update nav badge
    const openCount = tasks.filter(t => ['open','in_progress'].includes(t.status)).length;
    const navBadge = document.getElementById('seq-tasks-nav-badge');
    if (navBadge) { navBadge.textContent = openCount || ''; navBadge.style.display = openCount ? '' : 'none'; }
  } catch(e) {
    toast('Failed to load sequences: ' + e.message, 'error');
  }
}

function renderSeqStats(states, tasks) {
  const active    = states.filter(s => ['active','waiting_for_next_step'].includes(s.status)).length;
  const paused    = states.filter(s => s.status === 'paused').length;
  const completed = states.filter(s => s.status === 'completed').length;
  const stopped   = states.filter(s => s.status.startsWith('stopped_')).length;
  const openTasks = tasks.filter(t => ['open','in_progress'].includes(t.status)).length;

  const statsEl = document.getElementById('seq-stats');
  if (!statsEl) return;
  statsEl.innerHTML = `
    <div class="card" style="cursor:pointer;" onclick="document.getElementById('seq-status-filter').value='active';seqFilterStatus='active';renderSeqTable(seqAllStates)">
      <div class="card-value" style="color:var(--green);">${active}</div>
      <div class="card-label">Active</div>
    </div>
    <div class="card" style="cursor:pointer;" onclick="document.getElementById('seq-status-filter').value='paused';seqFilterStatus='paused';renderSeqTable(seqAllStates)">
      <div class="card-value" style="color:var(--gold);">${paused}</div>
      <div class="card-label">Paused</div>
    </div>
    <div class="card" style="cursor:pointer;" onclick="document.getElementById('seq-status-filter').value='completed';seqFilterStatus='completed';renderSeqTable(seqAllStates)">
      <div class="card-value" style="color:var(--navy);">${completed}</div>
      <div class="card-label">Completed</div>
    </div>
    <div class="card" style="cursor:pointer;" onclick="document.getElementById('seq-status-filter').value='stopped_positive_reply';seqFilterStatus='stopped_positive_reply';renderSeqTable(seqAllStates)">
      <div class="card-value" style="color:var(--red);">${stopped}</div>
      <div class="card-label">Stopped</div>
    </div>
    <div class="card" style="cursor:pointer;" onclick="openAccordion('seq-tasks')">
      <div class="card-value" style="color:${openTasks > 0 ? 'var(--red)' : 'var(--text-muted)'};">${openTasks}</div>
      <div class="card-label">Open Tasks</div>
    </div>`;
}

function renderSeqTable(states) {
  const search = seqSearchTerm.toLowerCase();
  let filtered = states;

  if (seqFilterStatus) {
    filtered = filtered.filter(s => s.status === seqFilterStatus || s.status.startsWith(seqFilterStatus));
  }
  if (search) {
    filtered = filtered.filter(s =>
      (s.lead_name  || '').toLowerCase().includes(search) ||
      (s.lead_email || '').toLowerCase().includes(search) ||
      (s.lead_company || '').toLowerCase().includes(search)
    );
  }

  const tbody = document.getElementById('seq-tbody');
  if (!tbody) return;
  if (!filtered.length) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:36px;">No sequences found.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtered.map(s => {
    const dots = renderStepDots(s);
    const statusHtml = seqStatusBadge(s.status);
    const scoreHtml = s.engagement_score >= 5
      ? `<span class="badge badge-green" style="font-size:11px;display:inline-flex;align-items:center;gap:3px;">${icon('zap',11)} ${s.engagement_score}</span>`
      : `<span style="color:var(--text-muted);font-size:12px;">${s.engagement_score}</span>`;
    const nextHtml = seqFormatNext(s.scheduled_next_at);
    const isTerminal = s.status.startsWith('stopped_') || s.status === 'completed';

    return `<tr style="cursor:pointer;" onclick="openSeqDrawer('${esc(s.lead_id)}')">
      <td>
        <div style="font-weight:600;font-size:13px;">${esc(s.lead_name || 'Unknown')}</div>
        <div style="font-size:11px;color:var(--text-muted);">${esc(s.lead_email || '')}</div>
      </td>
      <td style="color:var(--text-dim);font-size:13px;">${esc(s.lead_company || ' - ')}</td>
      <td>${dots}</td>
      <td style="text-align:center;">${scoreHtml}</td>
      <td>${statusHtml}</td>
      <td style="font-size:11px;">${nextHtml}</td>
      <td onclick="event.stopPropagation();" style="text-align:right;">
        ${isTerminal ? '' : s.status === 'paused'
          ? `<button class="btn btn-outline btn-sm" onclick="pauseSeq('${esc(s.lead_id)}');event.stopPropagation()" title="Pause this lead's sequence. No further steps will fire until resumed.">▶ Resume</button>`
          : `<button class="btn btn-outline btn-sm" onclick="pauseSeq('${esc(s.lead_id)}');event.stopPropagation()" title="Pause this lead's sequence. No further steps will fire until resumed.">⏸ Pause</button>`}
      </td>
    </tr>`;
  }).join('');
}

function renderStepDots(state, steps) {
  // ``steps`` is the per-campaign sequence shape passed in from
  // buildUnifiedLeadRow (wfSeqShape). Falls back to the workspace-wide
  // SEQ_STEPS so the cross-campaign /orchestration/states view in
  // loadSequences() keeps working unchanged.
  //
  // Nasser v1.1 bug (2026-04-11): the orch ``current_step_index`` only
  // advances when the ORCHESTRATOR runs a step. For hybrid email+LinkedIn
  // campaigns email sends are handled by Instantly and never advance this
  // counter. We use ``email_sent_count`` (ground truth from SendLog rows
  // flipped to "sent" by the email_sent webhook) as the authoritative
  // signal for email step state. LinkedIn steps still use
  // ``current_step_index`` + ``completed_steps``.
  const stepList = Array.isArray(steps) && steps.length ? steps : SEQ_STEPS;
  const emailSent = state.email_sent_count ?? 0;
  const isTerminal = state.status.startsWith('stopped_') || state.status === 'completed';
  const isPaused = state.status === 'paused';
  const _stepIdx = state.current_step_index || 0;
  const _totalDays = stepList.find(s => s._sequence_total_days)?._sequence_total_days || stepList[stepList.length - 1]?.day || 30;
  // Compute actual days elapsed since enrollment (not the step's day_offset)
  const _enrolledAt = state.enrolled_at ? new Date(state.enrolled_at) : null;
  const dayIn = _enrolledAt ? Math.min(Math.max(1, Math.ceil((Date.now() - _enrolledAt.getTime()) / 86400000)), _totalDays) : 1;

  // Date helpers for per-step status
  const _dotAnchor = state.enrolled_at ? new Date(state.enrolled_at) : null;
  const _dotToday = new Date(); _dotToday.setHours(0, 0, 0, 0);

  // Walk the shape once and precompute each step's status. Email steps
  // use emailSent count; LinkedIn/other use date-based logic.
  let emailRunningIdx = 0;
  const stepStates = stepList.map((step, i) => {
    const isEmail = step.type === 'email' || step.type === 'send_email';
    const isLI    = step.type.startsWith('linkedin') || step.type.startsWith('send_linkedin');
    if (isEmail) {
      emailRunningIdx += 1;
      if (emailRunningIdx <= emailSent) return { status: 'done', isLI, step };
      if (emailRunningIdx === emailSent + 1) {
        if (isTerminal) return { status: 'terminal_pending', isLI, step };
        if (isPaused)   return { status: 'paused', isLI, step };
        return { status: 'current', isLI, step };
      }
      return { status: 'pending', isLI, step };
    }
    // LinkedIn / other: date-based logic
    let stepDate = null;
    if (_dotAnchor) {
      stepDate = new Date(_dotAnchor);
      stepDate.setDate(stepDate.getDate() + ((step.day || 0) - 1));
      stepDate.setHours(0, 0, 0, 0);
    }
    const pastOrToday = stepDate && stepDate <= _dotToday;
    const isConnect = isLI && (step.type === 'linkedin_connect' || step.type === 'send_linkedin_connect');
    const isMessage = isLI && (step.type === 'linkedin_message' || step.type === 'send_linkedin_message');
    const liSent = isConnect ? !!state.linkedin_connect_sent
                 : isMessage ? !!state.linkedin_message_sent
                 : false;
    const liQueued = isConnect ? !!state.linkedin_connect_queued
                   : isMessage ? !!state.linkedin_message_queued
                   : false;
    const liAccepted = isConnect && !!state.linkedin_connected;
    if (liSent || liAccepted) return { status: 'done', isLI, step };
    if (liQueued && !liSent)  return { status: 'li_queued', isLI, step };
    if (isTerminal) return { status: 'terminal_pending', isLI, step };
    if (isPaused)   return { status: 'paused', isLI, step };
    if (pastOrToday) return { status: 'current', isLI, step };
    return { status: 'pending', isLI, step };
  });

  const dots = stepStates.map(({ status, isLI, step }) => {
    let bg, size = '8px';
    const title = `Day ${step.day}: ${step.label}`;
    if (status === 'done')                 { bg = 'var(--green)'; }
    else if (status === 'li_queued')       { bg = 'var(--navy)'; }
    else if (status === 'current')         { bg = 'var(--gold)'; size = '10px'; }
    else if (status === 'paused')          { bg = 'var(--border-mid)'; }
    else if (status === 'terminal_pending'){ bg = 'var(--border-mid)'; }
    else                                    { bg = isLI ? 'var(--navy-light)' : 'var(--border-mid)'; }
    const border = isLI ? '1.5px solid var(--navy)' : 'none';
    return `<span class="seq-step-dot${status==='current'?' current':''}" title="${title}" style="width:${size};height:${size};background:${bg};border:${border};"></span>`;
  }).join('');

  return `<div class="seq-step-dots">${dots}<span style="font-size:10px;color:var(--text-muted);margin-left:5px;">Day ${dayIn}/${_totalDays}</span></div>`;
}

function seqStatusBadge(status) {
  const map = {
    active:                  '<span class="badge badge-green">Active</span>',
    waiting_for_next_step:   '<span class="badge badge-blue">Waiting</span>',
    paused:                  '<span class="badge badge-yellow">Paused</span>',
    completed:               '<span class="badge badge-gray">Completed</span>',
    stopped_positive_reply:  '<span class="badge badge-green">Replied ✓</span>',
    stopped_negative_reply:  '<span class="badge badge-red">Not Interested</span>',
    stopped_unsubscribed:    '<span class="badge badge-red">Unsubscribed</span>',
    stopped_bounced:         '<span class="badge badge-red">Bounced</span>',
    stopped_disqualified:    '<span class="badge badge-gray">Disqualified</span>',
  };
  return map[status] || `<span class="badge badge-gray">${esc(status)}</span>`;
}

function seqFormatNext(iso) {
  if (!iso) return '<span style="color:var(--text-muted);"> - </span>';
  const d = new Date(iso), now = new Date();
  const diffMs = d - now;
  const diffDays = diffMs / 86400000;
  const absDate = d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  if (diffDays < -1)   return `<span style="color:var(--red);font-size:11px;">Overdue ${Math.abs(Math.floor(diffDays))}d &middot; ${absDate}</span>`;
  if (diffDays < 0)    return `<span style="color:var(--red);font-size:11px;">Due today &middot; ${absDate}</span>`;
  if (diffDays < 1)    return `<span style="color:var(--gold);font-size:11px;">Today &middot; ${absDate}</span>`;
  if (diffDays < 2)    return `<span style="font-size:11px;">Tomorrow &middot; ${absDate}</span>`;
  return `<span style="font-size:11px;">In ${Math.ceil(diffDays)}d &middot; ${absDate}</span>`;
}

// ── Scheduler ─────────────────────────────────────────────────────────────────
async function runScheduler() {
  const btn = document.getElementById('run-scheduler-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Running…'; }
  try {
    const r = await api('/orchestration/scheduler/run', { method: 'POST' });
    toast(`Scheduler: ${r.steps_executed} sent, ${r.steps_skipped} skipped, ${r.steps_failed} failed.`);
    await loadSequences();
    refreshSchedulerStatus();
  } catch(e) {
    toast('Scheduler error: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '▶ Run Scheduler'; }
  }
}

// ── Scheduler status indicator ────────────────────────────────────────────────
async function refreshSchedulerStatus() {
  try {
    const r = await api('/health/full');
    const last = r.checks && r.checks.scheduler && r.checks.scheduler.last_run;
    let label = 'Scheduler: never';
    if (last) {
      const diff = Math.floor((Date.now() - new Date(last).getTime()) / 1000);
      if (diff < 60) label = 'Scheduler: <1m ago';
      else if (diff < 3600) label = 'Scheduler: ' + Math.floor(diff / 60) + 'm ago';
      else if (diff < 86400) label = 'Scheduler: ' + Math.floor(diff / 3600) + 'h ago';
      else label = 'Scheduler: ' + Math.floor(diff / 86400) + 'd ago';
    }
    document.querySelectorAll('#scheduler-status-main,#scheduler-status-other').forEach(el => { el.textContent = label; });
  } catch(_) { /* silent */ }
}
// Scheduler status is started inside bootApp() after auth is established.

// ── Pause / Resume ────────────────────────────────────────────────────────────
async function pauseSeq(leadId) {
  const isPaused = (seqAllStates.find(s => s.lead_id === leadId) || {}).status === 'paused';
  if (isPaused) {
    try {
      await api(`/orchestration/leads/${leadId}/resume`, { method: 'POST' });
      toast('Sequence resumed.');
      loadSequences();
      if (typeof wfCampaignId !== 'undefined' && wfCampaignId) {
        loadWfSequences(wfCampaignId);
      }
    } catch(e) { toast('Resume failed: ' + e.message, 'error'); }
  } else {
    try {
      await api(`/orchestration/leads/${leadId}/pause`, { method: 'POST', body: JSON.stringify({ reason: 'manual_hold' }) });
      toast('Sequence paused.');
      loadSequences();
      if (typeof wfCampaignId !== 'undefined' && wfCampaignId) {
        loadWfSequences(wfCampaignId);
      }
    } catch(e) { toast('Pause failed: ' + e.message, 'error'); }
  }
}

// ── Enroll modal ──────────────────────────────────────────────────────────────
async function openEnrollModal() {
  document.getElementById('seq-enroll-modal').classList.add('open');
  try {
    const data = await api('/prospects/?limit=500&offset=0');
    const prospects = data.prospects || data;
    const sel = document.getElementById('enroll-prospect-select');
    sel.innerHTML = '<option value="">Select lead…</option>' +
      prospects.map(p => `<option value="${p.id}">${esc(p.first_name)} ${esc(p.last_name||'')}  -  ${esc(p.email)}</option>`).join('');
  } catch(e) { console.error('Failed to load prospects:', e); }
}
function closeEnrollModal() {
  document.getElementById('seq-enroll-modal').classList.remove('open');
}
async function submitEnroll() {
  const leadId = document.getElementById('enroll-prospect-select').value;
  const seqId  = document.getElementById('enroll-sequence-select').value;
  if (!leadId) { toast('Please select a lead.', 'error'); return; }
  try {
    await api(`/orchestration/leads/${leadId}/enroll`, { method: 'POST', body: JSON.stringify({ sequence_id: seqId }) });
    toast('Lead enrolled in the 30-day sequence!');
    closeEnrollModal();
    loadSequences();
  } catch(e) { toast('Enroll failed: ' + e.message, 'error'); }
}
// Close enroll modal on overlay click
{ const _enrollModal = document.getElementById('seq-enroll-modal');
  if (_enrollModal) _enrollModal.addEventListener('click', function(e) {
    if (e.target === this) closeEnrollModal();
  });
}

// ── Unified drawer (campaign view: shows timeline + email editors together) ───
async function openUnifiedDrawer(seqId, prospectId) {
  seqDrawerLeadId = prospectId;
  document.getElementById('seq-drawer').classList.add('open');
  document.getElementById('seq-drawer-overlay').classList.add('open');
  document.getElementById('seq-drawer-content').innerHTML = '<div style="text-align:center;padding:48px;color:var(--text-muted);">Loading…</div>';
  document.getElementById('seq-drawer-lead-info').innerHTML = '';

  try {
    const seq = wfSequences.find(s => s.id === seqId);
    const [state, timeline] = await Promise.all([
      api(`/orchestration/leads/${prospectId}/state`).catch(() => null),
      api(`/orchestration/leads/${prospectId}/timeline`).catch(() => []),
    ]);
    const orchEntry = wfOrchMap[prospectId] || null;
    const leadInfo = orchEntry || {
      lead_id: prospectId,
      lead_name: seq?.prospect_name || 'Lead',
      lead_email: seq?.prospect_email || '',
      lead_company: seq?.prospect_company || '',
      lead_linkedin_url: null,
      linkedin_connected: false,
    };
    // Merge email_sent_count from wfOrchMap (which has SendLog data from
    // the /states endpoint) into the per-lead state (which lacks it).
    // Also fall back to synthetic state if no real orch state exists.
    let effectiveState = state || (orchEntry?._synthetic ? orchEntry : null);
    if (effectiveState && orchEntry) {
      // Merge all ground-truth fields from the bulk /states endpoint
      // into the per-lead state (which lacks SendLog + LinkedIn data)
      effectiveState = {
        ...effectiveState,
        email_sent_count: orchEntry.email_sent_count ?? effectiveState.email_sent_count,
        email_sent_steps: orchEntry.email_sent_steps ?? effectiveState.email_sent_steps,
        email_sent_dates: orchEntry.email_sent_dates ?? effectiveState.email_sent_dates,
        linkedin_connect_sent: orchEntry.linkedin_connect_sent ?? effectiveState.linkedin_connect_sent,
        linkedin_connect_queued: orchEntry.linkedin_connect_queued ?? effectiveState.linkedin_connect_queued,
        linkedin_message_sent: orchEntry.linkedin_message_sent ?? effectiveState.linkedin_message_sent,
        linkedin_message_queued: orchEntry.linkedin_message_queued ?? effectiveState.linkedin_message_queued,
        linkedin_connected: orchEntry.linkedin_connected ?? effectiveState.linkedin_connected,
      };
    }
    renderUnifiedDrawerContent(seq, leadInfo, effectiveState, timeline);
  } catch(e) {
    document.getElementById('seq-drawer-content').innerHTML = `<div style="padding:20px;color:var(--red);">Error: ${esc(e.message)}</div>`;
  }
}

function renderUnifiedDrawerContent(seq, leadInfo, state, timeline) {
  const _steps = wfSeqShape || SEQ_STEPS;
  const _stepIdx = state?.current_step_index || 0;
  const _totalDays = _steps.find(s => s._sequence_total_days)?._sequence_total_days || _steps[_steps.length - 1]?.day || 30;
  const _drawerEnrolledAt = state?.enrolled_at ? new Date(state.enrolled_at) : null;
  const dayIn = _drawerEnrolledAt ? Math.min(Math.max(1, Math.ceil((Date.now() - _drawerEnrolledAt.getTime()) / 86400000)), _totalDays) : null;
  const isTerminal = state ? (state.status.startsWith('stopped_') || state.status === 'completed') : false;
  const seqId = seq?.id;

  document.getElementById('seq-drawer-lead-info').innerHTML = `
    <div>
      <div style="font-size:15px;font-weight:700;color:var(--navy);">${esc(leadInfo.lead_name || 'Lead')}</div>
      <div style="font-size:12px;color:var(--text-muted);margin-top:2px;">${esc(leadInfo.lead_email||'')}${leadInfo.lead_company ? ' · '+esc(leadInfo.lead_company) : ''}</div>
      <div style="margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
        ${state ? seqStatusBadge(state.status) : statusBadge(seq?.status||'pending')}
        ${state ? `<span style="font-size:11px;color:var(--text-muted);">Score: <strong>${state.engagement_score}</strong></span>` : ''}
        ${dayIn ? `<span style="font-size:11px;color:var(--text-muted);">Day ${dayIn}/${_totalDays}</span>` : ''}
        ${leadInfo.linkedin_connected ? '<span class="badge-linkedin" style="display:inline-flex;align-items:center;gap:3px;">' + icon('link',11) + ' Connected</span>' : ''}
      </div>
    </div>`;

  let html = '';

  // Action buttons row
  const hasLI = leadInfo.lead_linkedin_url;
  if (state && !isTerminal) {
    const isPaused = state.status === 'paused';
    html += `<div style="display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap;">`;
    html += isPaused
      ? `<button class="btn btn-green btn-sm" onclick="pauseSeq('${esc(leadInfo.lead_id)}').then(()=>closeSeqDrawer())" title="Pause this lead's sequence. No further steps will fire until resumed.">▶ Resume Sequence</button>`
      : `<button class="btn btn-outline btn-sm" onclick="pauseSeq('${esc(leadInfo.lead_id)}').then(()=>closeSeqDrawer())" title="Pause this lead's sequence. No further steps will fire until resumed.">⏸ Pause Sequence</button>`;
    if (hasLI) html += `<a href="${esc(leadInfo.lead_linkedin_url)}" target="_blank" class="btn btn-outline btn-sm" style="text-decoration:none;display:inline-flex;align-items:center;gap:4px;">${icon('link',12)} Open LinkedIn</a>`;
    html += `</div>`;
  } else if (!state && seqId) {
    // Not in orch yet  -  show approve/skip buttons
    html += `<div style="display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap;">
      <button class="btn btn-primary btn-sm" onclick="approveWfSequence('${esc(seqId)}')" title="Approve this generated sequence so it is included when the campaign launches.">✓ Approve &amp; Enroll</button>
      <button class="btn btn-secondary btn-sm" onclick="skipWfSequence('${esc(seqId)}')" style="color:var(--text-muted);" title="Skip this lead. They will not be enrolled when the campaign launches.">Skip Lead</button>
    </div>`;
  }

  // ── 30-day timeline with inline email editors ─────────────────────────────
  if (state) {
    html += `<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:var(--text-muted);margin-bottom:14px;">30-Day Sequence</div>`;
    html += `<div class="seq-timeline">`;

    // Anchor date for computing real dates per step
    const _anchorIso = state.enrolled_at || null;
    const _anchorDate = _anchorIso ? new Date(_anchorIso) : null;

    // Use the per-campaign shape (wfSeqShape) so the drawer reflects
    // the sequence this campaign was actually launched with, not the
    // workspace-wide default. Build a local email index so the email
    // editor lookup works even when SEQ_STEPS / EMAIL_SEQ_MAP don't
    // match the campaign's real layout.
    const timelineSteps = (Array.isArray(wfSeqShape) && wfSeqShape.length) ? wfSeqShape : SEQ_STEPS;
    const localEmailMap = {};
    { let n = 0; timelineSteps.forEach((s, i) => { if (s.type === 'email') { n++; localEmailMap[i] = n; } }); }

    // Email steps use SendLog-backed email_sent_count (ground truth from
    // Instantly webhooks). LinkedIn / other steps use per-step date logic
    // (anchor + day_offset vs today) instead of orchIdx which doesn't map
    // to the timeline's visual order.
    const emailSentCount = state.email_sent_count ?? 0;

    // Helper: compute a step's target date from anchor
    function _stepDate(dayOffset) {
      if (!_anchorDate) return null;
      const d = new Date(_anchorDate);
      d.setDate(d.getDate() + dayOffset);
      d.setHours(0, 0, 0, 0);
      return d;
    }
    const _today = new Date(); _today.setHours(0, 0, 0, 0);

    timelineSteps.forEach((step, idx) => {
      const isLI    = step.type.startsWith('linkedin');
      const isEmail = step.type === 'email';
      const isLast  = idx === timelineSteps.length - 1;
      const emailOrdinal = localEmailMap[idx]; // 1-based email number, or undefined

      // Per-step target date for date-based status logic
      const stepTargetDate = _stepDate(step.day);
      const stepInPast   = stepTargetDate && stepTargetDate < _today;
      const stepIsToday  = stepTargetDate && stepTargetDate.getTime() === _today.getTime();
      const stepInFuture = stepTargetDate && stepTargetDate > _today;

      // Decide done/current status per-step using the right signal.
      let done = false, current = false;
      if (isEmail && emailOrdinal) {
        done    = emailOrdinal <= emailSentCount;
        current = emailOrdinal === emailSentCount + 1;
      } else if (isLI) {
        // LinkedIn steps: 3 states based on ground truth:
        // 1. Sent (PB confirmed invitationSent / linkedin_connect_sent_at)
        // 2. Queued (orch dispatched to PB but not yet confirmed sent)
        // 3. Pending (not dispatched yet / date in future)
        const isConnect = step.type === 'linkedin_connect' || step.type === 'send_linkedin_connect';
        const isMessage = step.type === 'linkedin_message' || step.type === 'send_linkedin_message';
        const liSent = isConnect ? !!state.linkedin_connect_sent
                     : isMessage ? !!state.linkedin_message_sent
                     : false;
        const liQueued = isConnect ? !!state.linkedin_connect_queued
                       : isMessage ? !!state.linkedin_message_queued
                       : false;
        const liAccepted = isConnect && !!leadInfo?.linkedin_connected;
        done = liSent || liAccepted;
        current = !done && (liQueued || stepInPast || stepIsToday);
      } else {
        done    = false;
        current = stepInPast || stepIsToday;
      }
      if (isTerminal) current = false;

      // LinkedIn-specific intermediate state: queued to PB but not confirmed
      const _isLIConnect = isLI && (step.type === 'linkedin_connect' || step.type === 'send_linkedin_connect');
      const _isLIMessage = isLI && (step.type === 'linkedin_message' || step.type === 'send_linkedin_message');
      const _liQueued = _isLIConnect ? !!state.linkedin_connect_queued : _isLIMessage ? !!state.linkedin_message_queued : false;
      const _liSent = _isLIConnect ? !!state.linkedin_connect_sent : _isLIMessage ? !!state.linkedin_message_sent : false;
      const _liPending = isLI && _liQueued && !_liSent && !done;

      let dotBg, dotColor, dotText, statusLabel, statusColor;
      if (done) {
        dotBg = 'var(--green)'; dotColor = '#fff'; dotText = '✓';
        statusLabel = isLI && !!leadInfo?.linkedin_connected ? 'Connected' : 'Sent';
        statusColor = 'var(--green)';
      } else if (_liPending) {
        // Queued to PhantomBuster but not yet confirmed sent on LinkedIn
        dotBg = '#e8f0fe'; dotColor = 'var(--navy)'; dotText = '⏱';
        statusLabel = 'Queued'; statusColor = 'var(--navy)';
      } else if (isEmail && current && state.status === 'active') {
        dotBg = 'var(--gold)'; dotColor = '#fff'; dotText = '→';
        statusLabel = 'Queued'; statusColor = 'var(--gold)';
      } else if (isEmail && current && state.status === 'waiting_for_next_step') {
        dotBg = '#e8f0fe'; dotColor = 'var(--navy)'; dotText = '⏱';
        // Use the step's own date for the label, not the global scheduled_next_at
        statusLabel = stepTargetDate ? seqFormatNext(stepTargetDate.toISOString()) : seqFormatNext(state.scheduled_next_at);
        statusColor = 'var(--navy)';
      } else if (isLI && current) {
        // LinkedIn step is due (date has passed or is today)
        const _isLiMsg = step.type === 'linkedin_message' || step.type === 'send_linkedin_message';
        const _notConnected = _isLiMsg && !leadInfo?.linkedin_connected;
        if (_notConnected && stepInPast) {
          // Message step past due but lead hasn't accepted connection — being deferred
          dotBg = '#e8f0fe'; dotColor = 'var(--navy)'; dotText = '⏱';
          statusLabel = 'Waiting for connection'; statusColor = 'var(--navy)';
        } else {
          dotBg = 'var(--gold)'; dotColor = '#fff'; dotText = '→';
          statusLabel = stepInPast ? 'Action needed' : 'Due today';
          statusColor = 'var(--gold)';
        }
      } else if (state.status === 'paused') {
        dotBg = 'var(--surface2)'; dotColor = 'var(--text-muted)'; dotText = '○';
        statusLabel = 'Paused'; statusColor = 'var(--text-muted)';
      } else if (isTerminal && !done && !isEmail) {
        // Only show "Skipped" for non-email steps (LinkedIn) that weren't completed.
        // Email steps are driven by Instantly independently - orch terminal state
        // doesn't mean emails were skipped.
        dotBg = 'var(--surface2)'; dotColor = 'var(--text-muted)'; dotText = '○';
        statusLabel = 'Skipped'; statusColor = 'var(--text-muted)';
      } else if (isTerminal && !done && isEmail) {
        // Email step in terminal orch - show based on date (Instantly will send it)
        dotBg = 'var(--surface2)'; dotColor = 'var(--text-muted)'; dotText = '○';
        statusLabel = (stepInFuture && stepTargetDate) ? seqFormatNext(stepTargetDate.toISOString()) : 'Pending';
        statusColor = 'var(--text-muted)';
      } else {
        dotBg = isLI ? '#e8f4ff' : 'var(--surface2)';
        dotColor = isLI ? '#0a66c2' : 'var(--text-muted)';
        dotText = '○';
        // Future steps: show relative date if available
        statusLabel = (stepInFuture && stepTargetDate) ? seqFormatNext(stepTargetDate.toISOString()) : 'Pending';
        statusColor = 'var(--text-muted)';
      }

      // Inline email editor for email steps with generated content.
      // Use the per-campaign local map so this matches timelineSteps.
      const emailNum = localEmailMap[idx];
      const emailContent = emailNum && seq ? seq[`email${emailNum}`] : null;
      let emailEditorHtml = '';
      if (emailNum && seqId && emailContent?.subject) {
        emailEditorHtml = `<div style="margin-top:8px;padding:10px;background:var(--surface);border:1px solid var(--border);">
          <label style="font-size:10px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.06em;">Subject</label>
          <input type="text" id="subj-${seqId}-${emailNum}" value="${esc(emailContent.subject||'')}" style="font-size:12px;margin-bottom:6px;" />
          <label style="font-size:10px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.06em;">Body</label>
          <textarea id="body-${seqId}-${emailNum}" rows="6" style="font-size:12px;font-family:inherit;line-height:1.5;">${esc(emailContent.body||'')}</textarea>
          <div id="preview-${seqId}-${emailNum}" style="margin-top:6px;padding:10px 12px;background:var(--surface2);border-radius:var(--r-sm);font-size:12px;line-height:1.6;color:var(--text);white-space:pre-wrap;">
            <span style="font-size:9px;text-transform:uppercase;letter-spacing:0.08em;color:var(--text-muted);display:block;margin-bottom:4px;">Preview (as recipient sees it)</span>
            ${escBody(emailContent.body||'')}
          </div>
          <div style="display:flex;gap:6px;margin-top:6px;flex-wrap:wrap;">
            <button class="btn btn-primary btn-sm" onclick="saveWfSequence('${esc(seqId)}')" title="Save your inline edits to this sequence.">Save</button>
            <button class="btn btn-outline btn-sm" onclick="approveWfSequence('${esc(seqId)}')" title="Approve this generated sequence so it is included when the campaign launches.">✓ Approve</button>
            <button class="btn btn-outline btn-sm" onclick="regenerateWfSequence('${esc(seqId)}')" title="Discard this lead's sequence and ask Claude to generate a fresh one. Uses API credits.">↺ Regen</button>
            <span id="save-status-${seqId}" style="font-size:12px;color:var(--green);line-height:2.2;"></span>
          </div>
        </div>`;
      } else if (emailNum && seqId && !isTerminal) {
        emailEditorHtml = `<div style="margin-top:8px;">
          <button class="btn btn-outline btn-sm" onclick="regenerateWfSequence('${esc(seqId)}')" title="Discard this lead's sequence and ask Claude to generate a fresh one. Uses API credits.">Generate Email ${emailNum}</button>
        </div>`;
      }

      html += `<div class="seq-tl-row">
        <div class="seq-tl-dot-col">
          <div class="seq-tl-dot" style="background:${dotBg};color:${dotColor};border:2px solid ${done?'var(--green)':isLI?'#0a66c2':'var(--border-mid)'};">${dotText}</div>
          ${!isLast ? '<div class="seq-tl-line"></div>' : ''}
        </div>
        <div class="seq-tl-body">
          <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:2px;">
            <span style="font-size:13px;font-weight:600;color:${isLI?'#0a66c2':'var(--text)'};display:inline-flex;align-items:center;gap:5px;">${isLI?icon('link',13):icon('mail',13)}${step.label}</span>
            <span style="font-size:10px;color:var(--text-muted);">Day ${step.day}${(() => {
              // For sent email steps, use actual sent_at date (source of truth from Instantly)
              // For pending steps, use config-based scheduled date
              const sentDates = state.email_sent_dates || {};
              const emailNum = localEmailMap[idx];
              if (done && emailNum && sentDates[emailNum]) {
                const d = new Date(sentDates[emailNum]);
                return ' \u00b7 ' + d.toLocaleDateString('en-GB', {day:'2-digit', month:'short'});
              }
              if (_anchorDate) {
                const d = new Date(_anchorDate);
                d.setDate(d.getDate() + (step.day - 1));
                return ' \u00b7 ' + d.toLocaleDateString('en-GB', {day:'2-digit', month:'short'});
              }
              return '';
            })()}</span>
            <span style="font-size:10px;font-weight:600;color:${statusColor};">${statusLabel}</span>
          </div>
          ${isLI && !isTerminal ? renderLIActions(leadInfo, step, done) : ''}
          ${emailEditorHtml}
        </div>
      </div>`;
    });

    html += `</div>`; // .seq-timeline

  } else if (seq) {
    // No orch state yet  -  simple email editor list (not enrolled)
    html += `<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:var(--text-muted);margin-bottom:14px;">Email Content</div>`;
    // Build step names from sequence config
    const defaultStepNames = ['Problem Intro', 'Follow-up Insight', 'Case Study', 'Pattern Break', 'Light Nudge', 'Break-up'];
    const emailStepsFromConfig = (_seqStepsCache || []).filter(s => s.step_type === 'send_email');
    let drawerEmailIdx = 0;
    [1,2,3,4,5,6].forEach(n => {
      const e = seq[`email${n}`] || {};
      if (!e.subject && !e.body) return; // skip empty emails
      drawerEmailIdx++;
      const cfgStep = emailStepsFromConfig[drawerEmailIdx - 1];
      const stepLabel = cfgStep?.label || `Email ${drawerEmailIdx}: ${defaultStepNames[drawerEmailIdx - 1] || 'Follow-up'}`;
      const dayLabel = cfgStep ? ` (Day ${cfgStep.day_offset})` : '';
      const subjB = (n === 1 && seq.email1_subject_b) ? seq.email1_subject_b : null;
      html += `<div style="margin-bottom:12px;border:1px solid var(--border);">
        <div style="padding:7px 12px;background:var(--surface);font-size:10px;font-weight:700;color:var(--text-muted);text-transform:uppercase;">${esc(stepLabel)}${dayLabel}</div>
        <div style="padding:10px 12px;">
          <label style="font-size:10px;color:var(--text-muted);">Subject${subjB ? ' (A)' : ''}</label>
          <input type="text" id="subj-${seqId}-${n}" value="${esc(e.subject||'')}" style="font-size:12px;margin-bottom:6px;" placeholder="Not generated yet" />
          ${subjB ? `<label style="font-size:10px;color:var(--accent);font-weight:600;">Subject (B variant)</label>
          <input type="text" id="subj-b-${seqId}" value="${esc(subjB)}" style="font-size:12px;margin-bottom:6px;border-color:var(--accent);" />` : ''}
          <label style="font-size:10px;color:var(--text-muted);">Body</label>
          <textarea id="body-${seqId}-${n}" rows="5" style="font-size:12px;font-family:inherit;line-height:1.5;">${esc(e.body||'')}</textarea>
          <div style="margin-top:6px;padding:10px 12px;background:var(--surface2);border-radius:var(--r-sm);font-size:12px;line-height:1.6;color:var(--text);white-space:pre-wrap;">
            <span style="font-size:9px;text-transform:uppercase;letter-spacing:0.08em;color:var(--text-muted);display:block;margin-bottom:4px;">Preview (as recipient sees it)</span>
            ${escBody(e.body||'')}
          </div>
        </div>
      </div>`;
    });
    // LinkedIn content editors
    html += `<div style="margin-bottom:12px;border:1px solid var(--border);">
      <div style="padding:7px 12px;background:var(--surface);font-size:10px;font-weight:700;color:#0a66c2;text-transform:uppercase;">LinkedIn Content</div>
      <div style="padding:10px 12px;">
        <label style="font-size:10px;color:var(--text-muted);">Connection Note (max 30 words)</label>
        <input type="text" id="li-note-${seqId}" value="${esc(seq.linkedin_connect_note||'')}" style="font-size:12px;margin-bottom:6px;" placeholder="Not generated yet" />
        <label style="font-size:10px;color:var(--text-muted);">Follow-up Message (max 50 words)</label>
        <textarea id="li-msg-${seqId}" rows="3" style="font-size:12px;font-family:inherit;line-height:1.5;">${esc(seq.linkedin_message||'')}</textarea>
      </div>
    </div>`;
    const hasContent = [1,2,3,4,5,6].some(n => seq[`email${n}`]?.subject) || seq.linkedin_connect_note || seq.linkedin_message;
    if (hasContent) {
      html += `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px;">
        <button class="btn btn-primary btn-sm" onclick="saveWfSequence('${esc(seqId)}')" title="Save your inline edits to this sequence.">Save Changes</button>
        <button class="btn btn-outline btn-sm" onclick="approveWfSequence('${esc(seqId)}')" title="Approve this generated sequence so it is included when the campaign launches.">✓ Approve</button>
        <button class="btn btn-outline btn-sm" onclick="regenerateWfSequence('${esc(seqId)}')" title="Discard this lead's sequence and ask Claude to generate a fresh one. Uses API credits.">↺ Regenerate All</button>
        <button class="btn btn-outline btn-sm" style="color:var(--red);" onclick="deleteWfSequence('${esc(seqId)}')" title="Delete this lead's sequence. You can regenerate it later.">✕ Delete</button>
        <span id="save-status-${seqId}" style="font-size:12px;color:var(--green);line-height:2.2;"></span>
      </div>`;
    } else {
      html += `<div style="margin-bottom:20px;">
        <button class="btn btn-primary btn-sm" onclick="regenerateWfSequence('${esc(seqId)}')" title="Discard this lead's sequence and ask Claude to generate a fresh one. Uses API credits.">Generate Sequence for this Lead</button>
      </div>`;
    }
  }

  // Engagement events
  const events = (timeline || []).filter(e => e.type === 'event');
  if (events.length) {
    html += `<div style="margin-top:24px;">
      <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:var(--text-muted);margin-bottom:12px;">Engagement Events</div>
      <div style="display:flex;flex-direction:column;gap:6px;">
        ${events.slice(0, 25).map(e => `
          <div style="display:flex;align-items:center;gap:10px;padding:8px 12px;background:var(--surface);border:1px solid var(--border);">
            <span style="font-size:18px;flex-shrink:0;">${seqEventEmoji(e.event_type)}</span>
            <div style="flex:1;">
              <div style="font-size:12px;font-weight:500;">${seqFmtEventType(e.event_type)}</div>
              <div style="font-size:11px;color:var(--text-muted);">${new Date(e.timestamp||e.ingested_at).toLocaleString()}</div>
            </div>
          </div>`).join('')}
      </div>
    </div>`;
  }

  document.getElementById('seq-drawer-content').innerHTML = html;
}

// ── Lead detail drawer ────────────────────────────────────────────────────────
async function openSeqDrawer(leadId) {
  seqDrawerLeadId = leadId;
  document.getElementById('seq-drawer').classList.add('open');
  document.getElementById('seq-drawer-overlay').classList.add('open');
  document.getElementById('seq-drawer-content').innerHTML = '<div style="text-align:center;padding:48px;color:var(--text-muted);">Loading…</div>';
  document.getElementById('seq-drawer-lead-info').innerHTML = '';

  try {
    const [stateResult, timelineResult] = await Promise.allSettled([
      api(`/orchestration/leads/${leadId}/state`),
      api(`/orchestration/leads/${leadId}/timeline`),
    ]);
    let state = stateResult.status === 'fulfilled' ? stateResult.value : null;
    const timeline = timelineResult.status === 'fulfilled' ? timelineResult.value : [];
    const leadInfo = seqAllStates.find(s => s.lead_id === leadId) || { lead_id: leadId };
    // Merge ground-truth fields from bulk /states data
    if (state && leadInfo) {
      state = {
        ...state,
        email_sent_count: leadInfo.email_sent_count ?? state.email_sent_count,
        email_sent_steps: leadInfo.email_sent_steps ?? state.email_sent_steps,
        email_sent_dates: leadInfo.email_sent_dates ?? state.email_sent_dates,
        linkedin_connect_sent: leadInfo.linkedin_connect_sent ?? state.linkedin_connect_sent,
        linkedin_connect_queued: leadInfo.linkedin_connect_queued ?? state.linkedin_connect_queued,
        linkedin_message_sent: leadInfo.linkedin_message_sent ?? state.linkedin_message_sent,
        linkedin_message_queued: leadInfo.linkedin_message_queued ?? state.linkedin_message_queued,
        linkedin_connected: leadInfo.linkedin_connected ?? state.linkedin_connected,
      };
    }
    renderSeqDrawerContent(leadInfo, state, timeline);
  } catch(e) {
    document.getElementById('seq-drawer-content').innerHTML = `<div style="padding:20px;color:var(--red);">Error: ${esc(e.message)}</div>`;
  }
}

function closeSeqDrawer() {
  document.getElementById('seq-drawer').classList.remove('open');
  document.getElementById('seq-drawer-overlay').classList.remove('open');
  seqDrawerLeadId = null;
}

function renderSeqDrawerContent(leadInfo, state, timeline) {
  // If no orchestration state exists (lead not enrolled), provide sensible defaults
  if (!state) {
    state = { status: 'pending', enrolled_at: null, engagement_score: 0, current_step_index: 0 };
  }
  if (!timeline) timeline = [];
  const _steps = SEQ_STEPS;
  const _stepIdx = state.current_step_index || 0;
  const _totalDays = _steps.find(s => s._sequence_total_days)?._sequence_total_days || _steps[_steps.length - 1]?.day || 30;
  const _seqEnrolledAt = state.enrolled_at ? new Date(state.enrolled_at) : null;
  const dayIn = _seqEnrolledAt ? Math.min(Math.max(1, Math.ceil((Date.now() - _seqEnrolledAt.getTime()) / 86400000)), _totalDays) : 1;
  const isTerminal = (state.status || '').startsWith('stopped_') || state.status === 'completed';

  document.getElementById('seq-drawer-lead-info').innerHTML = `
    <div>
      <div style="font-size:15px;font-weight:700;color:var(--navy);">${esc(leadInfo.lead_name || 'Lead')}</div>
      <div style="font-size:12px;color:var(--text-muted);margin-top:2px;">${esc(leadInfo.lead_email||'')}${leadInfo.lead_company ? ' · '+esc(leadInfo.lead_company) : ''}</div>
      <div style="margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
        ${seqStatusBadge(state.status)}
        <span style="font-size:11px;color:var(--text-muted);">Score: <strong>${state.engagement_score}</strong></span>
        <span style="font-size:11px;color:var(--text-muted);">Day ${dayIn}/${_totalDays}</span>
        ${leadInfo.linkedin_connected ? '<span class="badge-linkedin" style="display:inline-flex;align-items:center;gap:3px;">' + icon('link',11) + ' Connected</span>' : ''}
      </div>
    </div>`;

  let html = '';

  // Action buttons
  if (!isTerminal) {
    const isPaused = state.status === 'paused';
    html += `<div style="display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap;">`;
    if (isPaused) {
      html += `<button class="btn btn-green btn-sm" onclick="pauseSeq('${esc(leadInfo.lead_id)}');closeSeqDrawer()" title="Pause this lead's sequence. No further steps will fire until resumed.">▶ Resume Sequence</button>`;
    } else {
      html += `<button class="btn btn-outline btn-sm" onclick="pauseSeq('${esc(leadInfo.lead_id)}');closeSeqDrawer()" title="Pause this lead's sequence. No further steps will fire until resumed.">⏸ Pause Sequence</button>`;
    }
    if (leadInfo.lead_linkedin_url) {
      html += `<a href="${esc(leadInfo.lead_linkedin_url)}" target="_blank" class="btn btn-outline btn-sm" style="text-decoration:none;display:inline-flex;align-items:center;gap:4px;">${icon('link',12)} Open LinkedIn Profile</a>`;
    }
    html += `</div>`;
  }

  // Step timeline
  html += `<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:var(--text-muted);margin-bottom:14px;">Sequence Timeline</div>`;
  html += `<div class="seq-timeline">`;

  // Anchor date for real dates
  const _seqAnchorIso = state.enrolled_at || null;
  const _seqAnchorDate = _seqAnchorIso ? new Date(_seqAnchorIso) : null;

  // Email steps: SendLog-backed email_sent_count. LinkedIn/other: date-based.
  const emailSentCount = state.email_sent_count ?? 0;
  const emailStepMap = {};
  { let n = 0; SEQ_STEPS.forEach((s, i) => { if (s.type === 'email') { n++; emailStepMap[i] = n; } }); }

  // Date helpers for per-step status
  function _seqStepDate(dayOffset) {
    if (!_seqAnchorDate) return null;
    const d = new Date(_seqAnchorDate);
    d.setDate(d.getDate() + dayOffset);
    d.setHours(0, 0, 0, 0);
    return d;
  }
  const _seqToday = new Date(); _seqToday.setHours(0, 0, 0, 0);

  SEQ_STEPS.forEach((step, idx) => {
    const isLI     = step.type.startsWith('linkedin');
    const isEmail  = step.type === 'email';
    const isLast   = idx === SEQ_STEPS.length - 1;
    const emailOrdinal = emailStepMap[idx];

    const stepDate    = _seqStepDate(step.day);
    const stepInPast  = stepDate && stepDate < _seqToday;
    const stepIsToday = stepDate && stepDate.getTime() === _seqToday.getTime();
    const stepInFuture = stepDate && stepDate > _seqToday;

    let done = false, current = false;
    if (isEmail && emailOrdinal) {
      done    = emailOrdinal <= emailSentCount;
      current = emailOrdinal === emailSentCount + 1;
    } else if (isLI) {
      const isConnect = step.type === 'linkedin_connect' || step.type === 'send_linkedin_connect';
      const isMessage = step.type === 'linkedin_message' || step.type === 'send_linkedin_message';
      const liSent = isConnect ? !!state.linkedin_connect_sent
                   : isMessage ? !!state.linkedin_message_sent
                   : false;
      const liQueued = isConnect ? !!state.linkedin_connect_queued
                     : isMessage ? !!state.linkedin_message_queued
                     : false;
      const liAccepted = isConnect && !!leadInfo?.linkedin_connected;
      done = liSent || liAccepted;
      current = !done && (liQueued || stepInPast || stepIsToday);
    } else {
      done = false;
      current = stepInPast || stepIsToday;
    }
    if (isTerminal) current = false;

    const _s_isLIConnect = isLI && (step.type === 'linkedin_connect' || step.type === 'send_linkedin_connect');
    const _s_isLIMessage = isLI && (step.type === 'linkedin_message' || step.type === 'send_linkedin_message');
    const _s_liQueued = _s_isLIConnect ? !!state.linkedin_connect_queued : _s_isLIMessage ? !!state.linkedin_message_queued : false;
    const _s_liSent = _s_isLIConnect ? !!state.linkedin_connect_sent : _s_isLIMessage ? !!state.linkedin_message_sent : false;
    const _s_liPending = isLI && _s_liQueued && !_s_liSent && !done;

    let dotBg, dotColor, dotText, statusLabel, statusColor;
    if (done) {
      dotBg = 'var(--green)'; dotColor = '#fff'; dotText = '✓';
      statusLabel = isLI && !!leadInfo?.linkedin_connected ? 'Connected' : 'Sent';
      statusColor = 'var(--green)';
    } else if (_s_liPending) {
      dotBg = '#e8f0fe'; dotColor = 'var(--navy)'; dotText = '⏱';
      statusLabel = 'Queued'; statusColor = 'var(--navy)';
    } else if (isEmail && current && state.status === 'active') {
      dotBg = 'var(--gold)'; dotColor = '#fff'; dotText = '→';
      statusLabel = 'Queued'; statusColor = 'var(--gold)';
    } else if (isEmail && current && state.status === 'waiting_for_next_step') {
      dotBg = '#e8f0fe'; dotColor = 'var(--navy)'; dotText = '⏱';
      statusLabel = stepDate ? seqFormatNext(stepDate.toISOString()) : seqFormatNext(state.scheduled_next_at);
      statusColor = 'var(--navy)';
    } else if (isLI && current) {
      dotBg = 'var(--gold)'; dotColor = '#fff'; dotText = '→';
      statusLabel = stepInPast ? 'Action needed' : 'Due today';
      statusColor = 'var(--gold)';
    } else if (state.status === 'paused') {
      dotBg = 'var(--surface2)'; dotColor = 'var(--text-muted)'; dotText = '○';
      statusLabel = 'Paused'; statusColor = 'var(--text-muted)';
    } else if (isTerminal && !done && !isEmail) {
      dotBg = 'var(--surface2)'; dotColor = 'var(--text-muted)'; dotText = '○';
      statusLabel = 'Skipped'; statusColor = 'var(--text-muted)';
    } else if (isTerminal && !done && isEmail) {
      dotBg = 'var(--surface2)'; dotColor = 'var(--text-muted)'; dotText = '○';
      statusLabel = (stepInFuture && stepDate) ? seqFormatNext(stepDate.toISOString()) : 'Pending';
      statusColor = 'var(--text-muted)';
    } else {
      dotBg = isLI ? '#e8f4ff' : 'var(--surface2)';
      dotColor = isLI ? '#0a66c2' : 'var(--text-muted)';
      dotText = '○';
      statusLabel = (stepInFuture && stepDate) ? seqFormatNext(stepDate.toISOString()) : 'Pending';
      statusColor = 'var(--text-muted)';
    }

    html += `<div class="seq-tl-row">
      <div class="seq-tl-dot-col">
        <div class="seq-tl-dot" style="background:${dotBg};color:${dotColor};border:2px solid ${done?'var(--green)':isLI?'#0a66c2':'var(--border-mid)'};">${dotText}</div>
        ${!isLast ? '<div class="seq-tl-line"></div>' : ''}
      </div>
      <div class="seq-tl-body">
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:2px;">
          <span style="font-size:13px;font-weight:600;color:${isLI?'#0a66c2':'var(--text)'};display:inline-flex;align-items:center;gap:5px;">${isLI?icon('link',13):icon('mail',13)}${step.label}</span>
          <span style="font-size:10px;color:var(--text-muted);">Day ${step.day}${(() => {
            const sentDates = state.email_sent_dates || {};
            const emailNum = emailStepMap[idx];
            if (done && emailNum && sentDates[emailNum]) {
              const d = new Date(sentDates[emailNum]);
              return ' \u00b7 ' + d.toLocaleDateString('en-GB', {day:'2-digit', month:'short'});
            }
            if (_seqAnchorDate) {
              const d = new Date(_seqAnchorDate);
              d.setDate(d.getDate() + (step.day - 1));
              return ' \u00b7 ' + d.toLocaleDateString('en-GB', {day:'2-digit', month:'short'});
            }
            return '';
          })()}</span>
          <span style="font-size:10px;font-weight:600;color:${statusColor};">${statusLabel}</span>
        </div>
        ${isLI && !isTerminal ? renderLIActions(leadInfo, step, done) : ''}
      </div>
    </div>`;
  });

  html += `</div>`; // seq-timeline

  // Engagement events
  const events = (timeline || []).filter(e => e.type === 'event');
  if (events.length) {
    html += `<div style="margin-top:24px;">
      <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:var(--text-muted);margin-bottom:12px;">Engagement Events</div>
      <div style="display:flex;flex-direction:column;gap:6px;">
        ${events.slice(0, 25).map(e => `
          <div style="display:flex;align-items:center;gap:10px;padding:8px 12px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r-sm);">
            <span style="font-size:18px;flex-shrink:0;">${seqEventEmoji(e.event_type)}</span>
            <div style="flex:1;">
              <div style="font-size:12px;font-weight:500;">${seqFmtEventType(e.event_type)}</div>
              <div style="font-size:11px;color:var(--text-muted);">${new Date(e.timestamp||e.ingested_at).toLocaleString()}</div>
            </div>
          </div>`).join('')}
      </div>
    </div>`;
  }

  document.getElementById('seq-drawer-content').innerHTML = html;
}

function renderLIActions(leadInfo, step, isDone) {
  if (isDone) return '';
  const lid = leadInfo.lead_id;
  const connected = leadInfo.linkedin_connected;

  if (step.type === 'linkedin_connect') {
    return `<div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap;">
      <button class="btn btn-outline btn-sm" onclick="seqLinkedInAction('${esc(lid)}','connect_sent')" title="Record this LinkedIn action for this lead.">${icon('send',12)} Mark Connection Sent</button>
      <button class="btn btn-green btn-sm" onclick="seqLinkedInAction('${esc(lid)}','connected')" title="Record this LinkedIn action for this lead.">${icon('users',12)} Mark as Connected</button>
    </div>`;
  }
  if (step.type === 'linkedin_message') {
    if (!connected) return `<div style="margin-top:8px;font-size:11px;color:var(--text-muted);display:inline-flex;align-items:center;gap:4px;">${icon('alert-triangle',11)} Not connected yet  -  connect first before sending a message.</div>
      <div style="margin-top:6px;"><button class="btn btn-green btn-sm" onclick="seqLinkedInAction('${esc(lid)}','connected')" title="Record this LinkedIn action for this lead.">${icon('users',12)} Mark as Connected</button></div>`;
    return `<div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap;">
      <button class="btn btn-outline btn-sm" onclick="seqLinkedInAction('${esc(lid)}','message_sent')" title="Record this LinkedIn action for this lead.">${icon('send',12)} Mark Message Sent</button>
    </div>`;
  }
  return '';
}

async function seqLinkedInAction(leadId, actionType) {
  try {
    await api(`/orchestration/leads/${leadId}/linkedin/action`, { method: 'POST', body: JSON.stringify({ action_type: actionType }) });
    const labels = { connect_sent: 'Connection request recorded.', connected: 'Lead marked as connected!', message_sent: 'LinkedIn message recorded.' };
    toast(labels[actionType] || 'Action recorded.');
    // Refresh drawer and main list
    const [states, tasks] = await Promise.all([
      api(`/orchestration/states?include_terminal=${document.getElementById('seq-show-terminal')?.checked||false}`).catch(() => []),
      api('/orchestration/tasks').catch(() => []),
    ]);
    seqAllStates = states;
    seqAllTasks = tasks;
    // Also update wfOrchMap so the unified drawer sees fresh data
    wfOrchMap = {};
    states.forEach(s => { wfOrchMap[s.lead_id] = s; });
    renderSeqStats(states, tasks);
    renderSeqTable(states);
    renderSeqTasks(tasks);
    // Re-open the appropriate drawer depending on context
    const seqMatch = wfSequences.find(s => s.prospect_id === leadId);
    if (seqMatch && wfCampaignId) {
      openUnifiedDrawer(seqMatch.id, leadId);
    } else {
      openSeqDrawer(leadId);
    }
  } catch(e) { toast('Failed: ' + e.message, 'error'); }
}

function seqEventEmoji(type) {
  const m = { email_sent:'Sent', email_opened:'Opened', email_clicked:'Clicked', email_replied_positive:'Reply+', email_replied_negative:'Reply-', email_bounced_hard:'Bounced', email_bounced_soft:'Soft Bounce', unsubscribed:'Unsub', linkedin_connect_sent:'LI Sent', linkedin_connect_accepted:'LI Accept', linkedin_message_sent:'LI Msg', linkedin_message_replied:'LI Reply', website_visit_signal:'Web Visit' };
  return m[type] || '•';
}
function seqFmtEventType(type) {
  return (type||'').replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
}

// ── Manual Tasks ──────────────────────────────────────────────────────────────
function renderSeqTasks(tasks) {
  const list = document.getElementById('seq-tasks-list');
  if (!list) return;
  const badge = document.getElementById('seq-tasks-badge');
  const active = tasks.filter(t => ['open','in_progress'].includes(t.status));

  if (badge) { badge.textContent = active.length || ''; badge.style.display = active.length ? '' : 'none'; }

  if (!active.length) {
    list.innerHTML = `<div style="text-align:center;padding:36px;color:var(--text-muted);display:flex;align-items:center;justify-content:center;gap:6px;">${icon('check-circle',14)} No open tasks  -  looking good</div>`;
    return;
  }

  const pOrd = { urgent:0, high:1, medium:2, low:3 };
  const sorted = [...active].sort((a,b) => (pOrd[a.priority]??9) - (pOrd[b.priority]??9));

  const pColor = { urgent:'var(--red)', high:'var(--gold)', medium:'var(--blue)', low:'var(--text-muted)' };
  list.innerHTML = sorted.map(t => {
    const isLI = (t.reason||'').toLowerCase().includes('linkedin');
    const li = seqAllStates.find(s => s.lead_id === t.lead_id) || {};
    const lid = t.lead_id;
    return `<div class="seq-task-card">
      <div style="flex-shrink:0;margin-top:2px;color:var(--text-muted);">${isLI ? icon('link',22) : icon('clipboard',22)}</div>
      <div style="flex:1;">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:5px;flex-wrap:wrap;">
          <span style="font-weight:600;font-size:13px;cursor:pointer;color:var(--navy);" onclick="openSeqDrawer('${lid}')">${esc(li.lead_name||t.lead_id)}</span>
          <span style="font-size:10px;font-weight:700;text-transform:uppercase;color:${pColor[t.priority]||'var(--text-muted)'};">${t.priority}</span>
          ${t.status==='in_progress'?'<span class="badge badge-blue" style="font-size:10px;">In Progress</span>':''}
        </div>
        <div style="font-size:12px;color:var(--text-dim);margin-bottom:9px;">${esc(t.suggested_action)}</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;">
          ${isLI ? `
            <button class="btn btn-outline btn-sm" onclick="seqTaskLIAction('${esc(t.task_id)}','${esc(lid)}','connect_sent')" title="Record this LinkedIn action for the task.">${icon('send',12)} Connection Sent</button>
            <button class="btn btn-green btn-sm" onclick="seqTaskLIAction('${esc(t.task_id)}','${esc(lid)}','connected')" title="Record this LinkedIn action for the task.">${icon('users',12)} Connected</button>
            <button class="btn btn-outline btn-sm" onclick="seqTaskLIAction('${esc(t.task_id)}','${esc(lid)}','message_sent')" title="Record this LinkedIn action for the task.">${icon('message-circle',12)} Message Sent</button>` : ''}
          <button class="btn btn-outline btn-sm" onclick="seqUpdateTask('${esc(t.task_id)}','completed')" title="Update the status of this manual task.">✓ Done</button>
          <button class="btn btn-secondary btn-sm" onclick="seqUpdateTask('${esc(t.task_id)}','in_progress')" title="Update the status of this manual task.">▶ In Progress</button>
          <button class="btn btn-secondary btn-sm" onclick="seqUpdateTask('${esc(t.task_id)}','dismissed')" title="Update the status of this manual task.">✕ Dismiss</button>
        </div>
      </div>
      <div style="font-size:10px;color:var(--text-muted);text-align:right;flex-shrink:0;white-space:nowrap;">Due<br>${new Date(t.due_date).toLocaleDateString()}</div>
    </div>`;
  }).join('');
}

async function seqTaskLIAction(taskId, leadId, actionType) {
  try {
    await Promise.all([
      api(`/orchestration/leads/${leadId}/linkedin/action`, { method:'POST', body:JSON.stringify({action_type:actionType}) }),
      api(`/orchestration/tasks/${taskId}/status`, { method:'POST', body:JSON.stringify({status:'completed'}) }),
    ]);
    toast('LinkedIn action recorded & task completed.');
    loadSequences();
  } catch(e) { toast('Failed: ' + e.message, 'error'); }
}

async function seqUpdateTask(taskId, status) {
  try {
    await api(`/orchestration/tasks/${taskId}/status`, { method:'POST', body:JSON.stringify({status}) });
    toast('Task updated.');
    loadSequences();
  } catch(e) { toast('Update failed: ' + e.message, 'error'); }
}

// ── Top bar helpers ─────────────────────────────────────────────────────────────
// ── LinkedIn Queue Health Banner ──────────────────────────────────────────────
// Fetches queue-health from the backend and shows a red/yellow banner if there
// are stuck items, worker issues, or PB circuit open.
async function _fetchLinkedInQueueHealth() {
  const banner = document.getElementById('li-queue-health-banner');
  if (!banner) return;
  try {
    const data = await api('/orchestration/linkedin/queue-health');
    const alerts = data.alerts || [];
    if (alerts.length === 0) {
      banner.classList.add('hidden');
      return;
    }
    const isError = alerts.some(a => a.includes('stuck') || a.includes('not cycling') || a.includes('circuit'));
    banner.style.background = isError ? '#fef2f2' : '#fffbeb';
    banner.style.border = '1px solid ' + (isError ? '#fca5a5' : '#fcd34d');
    banner.innerHTML = `
      <div style="display:flex;align-items:flex-start;gap:10px;">
        <span style="font-size:16px;">${isError ? '🔴' : '⚠️'}</span>
        <div>
          <strong style="color:${isError ? '#991b1b' : '#92400e'};">LinkedIn Queue ${isError ? 'Issue' : 'Warning'}</strong>
          <ul style="margin:4px 0 0 16px;padding:0;color:${isError ? '#7f1d1d' : '#78350f'};">
            ${alerts.map(a => '<li>' + a + '</li>').join('')}
          </ul>
          ${data.stuck && data.stuck.count > 0 ? '<div style="margin-top:6px;font-size:11px;color:var(--text-muted);">Stuck items: ' + data.stuck.items.map(i => i.prospect_name + ' (' + i.action_type + ', ' + i.hours_stuck + 'h)').join(', ') + '</div>' : ''}
        </div>
      </div>`;
    banner.classList.remove('hidden');
  } catch (e) {
    // Endpoint may not exist yet — silently hide banner
    banner.classList.add('hidden');
  }
}

function updateTopBarStats(camp, sequences, stats) {
  const total = sequences.length;
  const connected = Object.values(wfOrchMap).filter(s => s.linkedin_connected).length;
  const messaged = Object.values(wfOrchMap).filter(s => s.linkedin_messaged).length;
  // Update top stats to match HTML IDs
  const el0 = document.getElementById('top-stat-leads');
  const el1 = document.getElementById('top-stat-connected');
  const el2 = document.getElementById('top-stat-messaged');
  if (el0) el0.innerHTML = `<strong>${total}</strong> leads`;
  if (el1) el1.innerHTML = `<strong>${connected}</strong> connected`;
  if (el2) el2.innerHTML = `<strong>${messaged}</strong> messaged`;
  // Show/hide action buttons
  const genBtn = document.getElementById('top-generate-btn');
  const launchBtn = document.getElementById('top-launch-btn');
  if (genBtn) genBtn.style.display = camp.status === 'active' ? 'none' : '';
  if (launchBtn) launchBtn.style.display = (total > 0 && camp.status === 'draft') ? '' : 'none';
}

function updateCampaignStatusPill(status) {
  const el = document.getElementById('campaign-status-pill');
  if (!el) return;
  const colors = { draft:'var(--text-muted)', launching:'var(--gold)', active:'var(--green)', paused:'var(--yellow)', completed:'var(--blue)', archived:'var(--text-muted)' };
  const color = colors[status] || 'var(--text-muted)';
  el.innerHTML = `<span class="status-pill" style="background:${color}11;color:${color};border-color:${color}33;"><span class="status-pill-dot" style="background:${color};"></span>${esc(status)}</span>`;
}

// ── Global keyboard: Escape to close, Tab focus trap in modals ───────────────
function _getOpenModal() {
  const ids = ['contacts-modal','seq-drawer','inbox-mgmt-drawer','seq-enroll-modal','analytics-edit-modal','conf-email-modal','conf-seq-modal','inbox-manager-modal','outlook-auth-modal'];
  for (const id of ids) {
    const el = document.getElementById(id);
    if (el && (el.classList.contains('open') || (!el.classList.contains('hidden') && el.offsetParent !== null))) return { el, id };
  }
  return null;
}
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const m = _getOpenModal();
    if (!m) return;
    const closers = { 'contacts-modal':closeContactsModal, 'seq-drawer':closeSeqDrawer, 'inbox-mgmt-drawer':closeInboxDrawer, 'seq-enroll-modal':closeEnrollModal, 'analytics-edit-modal':closeAnalyticsEdit, 'conf-email-modal':closeConfModal, 'conf-seq-modal':closeSeqModal, 'inbox-manager-modal':closeInboxManager, 'outlook-auth-modal':closeOutlookAuth };
    if (closers[m.id]) closers[m.id]();
    return;
  }
  // Focus trap: keep Tab inside open modal
  if (e.key === 'Tab') {
    const m = _getOpenModal();
    if (!m) return;
    const focusable = m.el.querySelectorAll('button,input,select,textarea,a,[tabindex]:not([tabindex="-1"])');
    if (!focusable.length) return;
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
});

// ── Boot ───────────────────────────────────────────────────────────────────────
let _booted = false;
async function bootApp() {
  if (_booted) return;
  _booted = true;
  applyProjectSearchLabels();
  replaceHtmlIcons();
  restoreCollapseStates();
  restorePanelStates();
  // Populate seqDefaults from the backend so every downstream renderer
  // uses the same shape constants the Python code does. Fire-and-forget
  //  -  the hardcoded fallback object already has safe defaults, so
  // pre-first-response renders (race window < ~100ms) are still correct.
  loadSeqDefaults();
  // Load initial data with error handling so failures don't break the whole app
  // Pre-load sequence config so preview, per-step instructions, and journey dots work
  if (!_seqStepsCache) {
    api('/settings/sequence-config').then(config => {
      _seqStepsCache = (config.steps || []).filter(s =>
        s.step_type === 'send_email' || s.step_type === 'send_linkedin_connect' || s.step_type === 'send_linkedin_message'
      );
      _rebuildSeqSteps();
    }).catch(() => {});
  }
  await Promise.allSettled([
    loadProspects().catch(e => console.error('bootApp: loadProspects failed:', e)),
    loadWorkflowCampaigns().catch(e => console.error('bootApp: loadWorkflowCampaigns failed:', e)),
    loadSetupChecklist().catch(e => console.error('bootApp: loadSetupChecklist failed:', e)),
  ]).then(results => {
    const failed = results.filter(r => r.status === 'rejected');
    if (failed.length > 0) {
      toast('Some data failed to load. Try refreshing.', 'error');
    }
  });
  // Initialize outreach view
  switchTab('outreach');
  // Load reply stats and scheduler status now that auth is established
  refreshSchedulerStatus();
  setInterval(refreshSchedulerStatus, 60000);
}

// ── Enrich Profiles ───────────────────────────────────────────────────────────
async function enrichProfiles() {
  const btn = document.getElementById('enrich-profiles-btn');
  const statusEl = document.getElementById('validate-status');
  if (btn) { btn.disabled = true; btn.textContent = 'Enriching...'; }
  if (statusEl) { statusEl.classList.remove('hidden'); statusEl.innerHTML = '<span style="display:inline-flex;align-items:center;gap:6px;"><svg width="14" height="14" viewBox="0 0 24 24" style="animation:spin 1s linear infinite;"><circle cx="12" cy="12" r="10" stroke="var(--primary)" stroke-width="3" fill="none" stroke-dasharray="31.4 31.4" stroke-linecap="round"/></svg> Enriching profiles  -  scraping company websites and LinkedIn data. This may take a few minutes...</span>'; }
  try {
    let body;
    if (selectedProspects.size > 0) {
      body = { prospect_ids: [...selectedProspects] };
    } else {
      body = { all: true };
    }
    const result = await api('/prospects/enrich', {
      method: 'POST',
      body: JSON.stringify(body),
    });
    const enriched = result.enriched || 0;
    const skipped = result.skipped || 0;
    const failed = result.failed || 0;
    if (statusEl) { statusEl.textContent = `Enrichment done: ${enriched} enriched, ${skipped} skipped, ${failed} failed.`; setTimeout(() => statusEl.classList.add('hidden'), 8000); }
    toast(`Enrichment complete: ${enriched} enriched, ${skipped} skipped, ${failed} failed.`, enriched > 0 ? 'success' : 'warning');
    loadProspects();
  } catch (e) {
    toast('Enrichment failed: ' + e.message, 'error');
    if (statusEl) { statusEl.textContent = 'Enrichment failed: ' + e.message; setTimeout(() => statusEl.classList.add('hidden'), 8000); }
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Enrich Profiles'; }
  }
}

// Update enrich button label based on selection
document.addEventListener('change', (e) => {
  if (e.target.closest('#prospects-tbody')) {
    const btn = document.getElementById('enrich-profiles-btn');
    if (!btn) return;
    btn.textContent = selectedProspects.size > 0
      ? `Enrich ${selectedProspects.size} Selected`
      : 'Enrich Profiles';
  }
});

// ── Account View ──────────────────────────────────────────────────────────────
let _currentLeadsView = 'leads';
let _accountsCache = null;

function switchLeadsAccountView(view) {
  _currentLeadsView = view;
  const leadsBtn = document.getElementById('view-leads-btn');
  const accountsBtn = document.getElementById('view-accounts-btn');
  const leadsWrap = document.getElementById('leads-table-wrap');
  const accountsWrap = document.getElementById('accounts-table-wrap');
  const footer = document.querySelector('.leads-footer');

  const searchInput = document.getElementById('prospect-search-input');
  const filterBtn = searchInput ? searchInput.parentElement.querySelector('.sub-toggle') : null;
  const filterBody = document.getElementById('leads-filter-body');
  const segmentsBar = document.getElementById('segments-bar');
  const sectionHeader = document.getElementById('sh-leads');
  const healthBar = document.getElementById('leads-health-bar');
  const pagination = document.getElementById('prospects-pagination');

  if (view === 'accounts') {
    leadsBtn.style.background = 'transparent';
    leadsBtn.style.color = 'var(--text-dim)';
    accountsBtn.style.background = 'var(--navy)';
    accountsBtn.style.color = '#fff';
    if (leadsWrap) leadsWrap.style.display = 'none';
    if (accountsWrap) accountsWrap.style.display = 'block';
    if (footer) footer.style.display = 'none';
    if (searchInput) searchInput.style.display = 'none';
    if (filterBtn) filterBtn.style.display = 'none';
    if (filterBody) filterBody.style.display = 'none';
    if (segmentsBar) segmentsBar.style.display = 'none';
    if (sectionHeader) sectionHeader.style.display = 'none';
    if (healthBar) healthBar.style.display = 'none';
    if (pagination) pagination.style.display = 'none';
    loadAccounts();
  } else {
    accountsBtn.style.background = 'transparent';
    accountsBtn.style.color = 'var(--text-dim)';
    leadsBtn.style.background = 'var(--navy)';
    leadsBtn.style.color = '#fff';
    if (leadsWrap) leadsWrap.style.display = 'block';
    if (accountsWrap) accountsWrap.style.display = 'none';
    if (footer) footer.style.display = 'flex';
    if (searchInput) searchInput.style.display = '';
    if (filterBtn) filterBtn.style.display = '';
    if (filterBody) filterBody.style.display = '';
    if (segmentsBar) segmentsBar.style.display = 'flex';
    if (sectionHeader) sectionHeader.style.display = '';
    if (healthBar) healthBar.style.display = '';
    if (pagination) pagination.style.display = 'flex';
  }
}

async function loadAccounts() {
  const tbody = document.getElementById('accounts-tbody');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text-muted);padding:28px;"><div class="loading" style="margin:0 auto;"></div></td></tr>';
  try {
    const data = await api('/prospects/accounts');
    const accounts = data.accounts || data || [];
    _accountsCache = accounts;
    if (!accounts.length) {
      tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text-muted);padding:28px;">No accounts found. Accounts are derived from prospect company data.</td></tr>';
      return;
    }
    // Sort by engagement descending, then prospect count
    accounts.sort((a, b) => {
      const engA = (a.sent && a.sent > 0) ? ((a.replied || 0) / a.sent * 100) : 0;
      const engB = (b.sent && b.sent > 0) ? ((b.replied || 0) / b.sent * 100) : 0;
      if (engB !== engA) return engB - engA;
      return (b.prospect_count || 0) - (a.prospect_count || 0);
    });
    tbody.innerHTML = accounts.map(a => {
      const eng = (a.sent && a.sent > 0) ? ((a.replied || 0) / a.sent * 100).toFixed(1) + '%' : ' - ';
      const engColor = (a.sent && a.sent > 0 && a.replied > 0) ? 'var(--green)' : 'var(--text-muted)';
      return `<tr style="cursor:pointer;" onclick="filterProspectsByAccount('${esc(a.company_name || a.domain || '')}')">
        <td style="font-weight:500;">${esc(a.company_name || ' - ')}</td>
        <td style="font-size:12px;color:var(--text-dim);">${esc(a.domain || ' - ')}</td>
        <td style="font-size:12px;">${esc(a.industry || ' - ')}</td>
        <td style="text-align:right;">${a.prospect_count || 0}</td>
        <td style="text-align:right;">${a.opened || 0}</td>
        <td style="text-align:right;">${a.replied || 0}</td>
        <td style="text-align:right;color:var(--accent);font-weight:600;">${a.meetings || 0}</td>
        <td style="text-align:right;font-weight:600;color:${engColor};">${eng}</td>
      </tr>`;
    }).join('');
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--red);padding:28px;">Failed to load accounts: ${esc(e.message)}</td></tr>`;
  }
}

function filterProspectsByAccount(companyName) {
  // Switch back to leads view and filter by company
  switchLeadsAccountView('leads');
  const searchInput = document.getElementById('prospect-search-input');
  if (searchInput) {
    searchInput.value = companyName;
    filterProspects();
  }
  const label = document.getElementById('view-mode-label');
  if (label) {
    label.style.display = 'inline';
    label.textContent = 'Showing: ' + companyName + ' (clear)';
  }
}

// ── Enhanced Analytics: Segment Performance ──────────────────────────────────
async function loadSegmentPerformance() {
  const el = document.getElementById('segments-perf-content');
  if (!el) return;
  try {
    const data = await api('/analytics/segments');
    const segments = data.segments || data || [];
    if (!segments.length) {
      el.innerHTML = '<div style="text-align:center;padding:16px;color:var(--text-muted);font-size:13px;">No segment data available yet.</div>';
      return;
    }
    // Sort by reply rate descending
    segments.sort((a, b) => (b.reply_rate || 0) - (a.reply_rate || 0));
    const bestReplyRate = segments[0]?.reply_rate || 0;
    const pct = v => v == null ? ' - ' : v + '%';
    const num = v => v == null ? ' - ' : v;
    const th = 'padding:8px 10px;font-weight:600;color:var(--text-muted);font-size:11px;text-transform:uppercase;letter-spacing:0.5px;';
    el.innerHTML =
      '<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:13px;"><thead><tr style="border-bottom:1px solid var(--border);text-align:left;">' +
      '<th style="' + th + '">Segment</th>' +
      '<th style="' + th + 'text-align:right;">Prospects</th>' +
      '<th style="' + th + 'text-align:right;">Sent</th>' +
      '<th style="' + th + 'text-align:right;">Opened</th>' +
      '<th style="' + th + 'text-align:right;">Replied</th>' +
      '<th style="' + th + 'text-align:right;">Meetings</th>' +
      '<th style="' + th + 'text-align:right;">Reply Rate</th>' +
      '<th style="' + th + 'text-align:right;">Cost</th>' +
      '</tr></thead><tbody>' +
      segments.map(s => {
        const isBest = s.reply_rate === bestReplyRate && bestReplyRate > 0;
        const rowStyle = isBest ? 'border-bottom:1px solid var(--border);background:var(--green-bg);' : 'border-bottom:1px solid var(--border);';
        return '<tr style="' + rowStyle + '">' +
          '<td style="padding:8px 10px;font-weight:500;">' + esc(s.name || s.segment || ' - ') + (isBest ? ' <span style="color:var(--green);font-size:10px;font-weight:700;">BEST</span>' : '') + '</td>' +
          '<td style="padding:8px 10px;text-align:right;">' + num(s.prospects) + '</td>' +
          '<td style="padding:8px 10px;text-align:right;">' + num(s.sent) + '</td>' +
          '<td style="padding:8px 10px;text-align:right;">' + num(s.opened) + '</td>' +
          '<td style="padding:8px 10px;text-align:right;">' + num(s.replied) + '</td>' +
          '<td style="padding:8px 10px;text-align:right;color:var(--accent);font-weight:600;">' + num(s.meetings) + '</td>' +
          '<td style="padding:8px 10px;text-align:right;font-weight:600;color:' + (s.reply_rate > 5 ? 'var(--green)' : 'var(--text)') + ';">' + pct(s.reply_rate) + '</td>' +
          '<td style="padding:8px 10px;text-align:right;">' + (s.cost != null ? '$' + s.cost.toFixed(2) : ' - ') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  } catch (e) {
    el.innerHTML = '<div style="color:var(--red);font-size:13px;">Failed to load segment data: ' + esc(e.message) + '</div>';
  }
}

// ── Enhanced Analytics: Sequence Stage Drop-off ──────────────────────────────
async function loadSequenceDropoff() {
  const el = document.getElementById('seq-dropoff-content');
  if (!el) return;
  try {
    const data = await api('/analytics/sequence-stages');
    const stages = data.stages || data || [];
    if (!stages.length) {
      el.innerHTML = '<div style="text-align:center;padding:16px;color:var(--text-muted);font-size:13px;">No sequence stage data available yet.</div>';
      return;
    }
    const maxSent = Math.max(...stages.map(s => s.sent || 0), 1);
    let html = '<div style="display:flex;flex-direction:column;gap:6px;">';
    stages.forEach((s, i) => {
      const barWidth = ((s.sent || 0) / maxSent * 100).toFixed(1);
      const isEmail = (s.type || s.channel || 'email').toLowerCase().includes('email');
      const barColor = isEmail ? 'var(--green)' : 'var(--blue)';
      const channelLabel = isEmail ? 'Email' : 'LinkedIn';
      let dropoff = '';
      if (i > 0 && stages[i - 1].sent > 0) {
        const dropPct = ((1 - (s.sent || 0) / stages[i - 1].sent) * 100).toFixed(1);
        dropoff = '<span style="font-size:11px;color:var(--red);margin-left:8px;">-' + dropPct + '% drop</span>';
      }
      html += '<div style="display:flex;align-items:center;gap:10px;">' +
        '<div style="width:80px;font-size:12px;font-weight:500;color:var(--text-dim);flex-shrink:0;">' + esc(s.name || (channelLabel + ' ' + (i + 1))) + '</div>' +
        '<div style="flex:1;background:var(--border);border-radius:3px;height:22px;overflow:hidden;position:relative;">' +
          '<div style="height:100%;width:' + barWidth + '%;background:' + barColor + ';border-radius:3px;transition:width 0.4s;"></div>' +
        '</div>' +
        '<div style="width:100px;text-align:right;font-size:12px;font-weight:600;color:var(--text);flex-shrink:0;">' + (s.sent || 0) + ' sent' + dropoff + '</div>' +
        '</div>';
    });
    html += '</div>';
    html += '<div style="display:flex;gap:14px;margin-top:10px;font-size:11px;color:var(--text-muted);">' +
      '<span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--green);vertical-align:middle;margin-right:4px;"></span>Email</span>' +
      '<span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--blue);vertical-align:middle;margin-right:4px;"></span>LinkedIn</span>' +
      '</div>';
    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = '<div style="color:var(--red);font-size:13px;">Failed to load stage data: ' + esc(e.message) + '</div>';
  }
}

// ── Enhanced Analytics: Cost Per Meeting ─────────────────────────────────────
async function loadCostPerMeeting() {
  const el = document.getElementById('cpm-content');
  if (!el) return;
  try {
    const data = await api('/analytics/cost-per-meeting');
    const totalSpend = data.total_spend || 0;
    const meetings = data.meetings_booked || 0;
    const cpm = meetings > 0 ? (totalSpend / meetings) : 0;
    const anthropicCost = data.anthropic_cost || 0;
    const subscriptionCost = data.subscription_cost || 0;
    el.innerHTML =
      '<div class="cards" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr));margin-bottom:14px;">' +
        '<div class="card" style="text-align:center;">' +
          '<div class="card-label">Total Spend</div>' +
          '<div class="card-value" style="color:var(--navy);">$' + totalSpend.toFixed(2) + '</div>' +
        '</div>' +
        '<div class="card" style="text-align:center;">' +
          '<div class="card-label">Meetings Booked</div>' +
          '<div class="card-value" style="color:var(--accent);">' + meetings + '</div>' +
        '</div>' +
        '<div class="card" style="text-align:center;">' +
          '<div class="card-label">Cost / Meeting</div>' +
          '<div class="card-value" style="color:' + (cpm > 50 ? 'var(--red)' : cpm > 20 ? 'var(--yellow)' : 'var(--green)') + ';">' + (meetings > 0 ? '$' + cpm.toFixed(2) : ' - ') + '</div>' +
        '</div>' +
      '</div>' +
      '<div style="font-size:12px;color:var(--text-muted);line-height:1.8;">' +
        '<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--border);"><span>Anthropic tokens</span><span style="font-weight:600;color:var(--text);">$' + anthropicCost.toFixed(2) + '</span></div>' +
        '<div style="display:flex;justify-content:space-between;padding:4px 0;"><span>Subscriptions</span><span style="font-weight:600;color:var(--text);">$' + subscriptionCost.toFixed(2) + '</span></div>' +
      '</div>';
  } catch (e) {
    el.innerHTML = '<div style="color:var(--red);font-size:13px;">Failed to load cost data: ' + esc(e.message) + '</div>';
  }
}

// ── Enhanced Analytics: Channel Attribution ──────────────────────────────────
async function loadChannelAttribution() {
  const el = document.getElementById('channel-attr-content');
  if (!el) return;
  try {
    const data = await api('/analytics/channel-attribution');
    const channels = data.channels || data || [];
    if (!channels.length) {
      el.innerHTML = '<div style="text-align:center;padding:16px;color:var(--text-muted);font-size:13px;">No channel attribution data available yet.</div>';
      return;
    }
    const maxReplies = Math.max(...channels.map(c => c.replies || 0), 1);
    let html = '<div style="display:flex;flex-direction:column;gap:6px;">';
    channels.forEach(c => {
      const barWidth = ((c.replies || 0) / maxReplies * 100).toFixed(1);
      const isEmail = (c.channel || c.type || 'email').toLowerCase().includes('email');
      const barColor = isEmail ? 'var(--green)' : 'var(--blue)';
      html += '<div style="display:flex;align-items:center;gap:10px;">' +
        '<div style="width:120px;font-size:12px;font-weight:500;color:var(--text-dim);flex-shrink:0;">' + esc(c.name || c.step || ' - ') + '</div>' +
        '<div style="flex:1;background:var(--border);border-radius:3px;height:22px;overflow:hidden;">' +
          '<div style="height:100%;width:' + barWidth + '%;background:' + barColor + ';border-radius:3px;transition:width 0.4s;"></div>' +
        '</div>' +
        '<div style="width:80px;text-align:right;font-size:12px;font-weight:600;color:var(--text);flex-shrink:0;">' + (c.replies || 0) + ' replies</div>' +
        '</div>';
    });
    html += '</div>';
    html += '<div style="display:flex;gap:14px;margin-top:10px;font-size:11px;color:var(--text-muted);">' +
      '<span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--green);vertical-align:middle;margin-right:4px;"></span>Email</span>' +
      '<span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--blue);vertical-align:middle;margin-right:4px;"></span>LinkedIn</span>' +
      '</div>';
    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = '<div style="color:var(--red);font-size:13px;">Failed to load channel data: ' + esc(e.message) + '</div>';
  }
}

// ── Inbox (unified mailbox view) — removed (Instantly-era feature) ───────────
function loadInboxMgmt() {} // stub
function closeInboxDrawer() {
  const drawer = document.getElementById('inbox-mgmt-drawer');
  const overlay = document.getElementById('inbox-mgmt-drawer-overlay');
  if (drawer) drawer.classList.remove('open');
  if (overlay) overlay.classList.remove('open');
}

// ── Newsletter Module ──────────────────────────────────────────────────────

let nlList = [];
let nlCurrent = null;
let nlOwners = [];
let _nlSendPollTimer = null;

function nlStatusPill(status) {
  const map = {
    draft: { bg: 'var(--border)', color: 'var(--text-dim)' },
    scheduled: { bg: '#3b82f620', color: '#2563eb' },
    sending: { bg: '#f59e0b20', color: '#d97706' },
    sent: { bg: '#10b98120', color: '#059669' },
    failed: { bg: '#ef444420', color: '#dc2626' },
  };
  const s = map[status] || map.draft;
  return '<span style="display:inline-block;font-size:10px;font-weight:600;padding:2px 8px;border-radius:10px;background:' + s.bg + ';color:' + s.color + ';">' + esc(status || 'draft') + '</span>';
}

// ── List view ────────────────────────────────────────────────────────────────

async function loadNewsletters() {
  const loading = document.getElementById('nl-loading');
  const empty = document.getElementById('nl-empty');
  const cards = document.getElementById('nl-cards');
  if (loading) loading.classList.remove('hidden');
  if (empty) empty.classList.add('hidden');
  if (cards) cards.innerHTML = '';

  try {
    const trackFilter = document.getElementById('nl-filter-track')?.value || '';
    const statusFilter = document.getElementById('nl-filter-status')?.value || '';
    let qs = '?per_page=50';
    if (trackFilter) qs += '&track=' + trackFilter;
    if (statusFilter) qs += '&status=' + statusFilter;
    const data = await api('/newsletters' + qs);
    nlList = data.items || [];
    if (loading) loading.classList.add('hidden');
    renderNewsletterList();
  } catch (e) {
    if (loading) loading.classList.add('hidden');
    toast('Failed to load newsletters: ' + e.message, 'error');
  }
}

function filterNewsletterList() { loadNewsletters(); }

function renderNewsletterList() {
  const empty = document.getElementById('nl-empty');
  const cards = document.getElementById('nl-cards');
  if (!nlList.length) {
    if (empty) empty.classList.remove('hidden');
    if (cards) cards.innerHTML = '';
    return;
  }
  if (empty) empty.classList.add('hidden');
  if (!cards) return;

  cards.innerHTML = nlList.map(nl => {
    const date = nl.updated_at ? new Date(nl.updated_at).toLocaleDateString() : '';
    const recipCount = nl.recipient_count || 0;
    const trackBadge = nl.track ? '<span style="font-size:9px;font-weight:600;text-transform:uppercase;padding:1px 5px;border-radius:6px;background:' + (nl.track === 'client' ? '#8b5cf620' : '#0ea5e920') + ';color:' + (nl.track === 'client' ? '#7c3aed' : '#0284c7') + ';">' + esc(nl.track) + '</span>' : '';
    return '<div class="card" style="padding:16px;cursor:pointer;" onclick="openNewsletterEditor(\'' + nl.id + '\')">'
      + '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">'
      + '<div style="display:flex;align-items:center;gap:6px;overflow:hidden;">'
      + '<div style="font-size:14px;font-weight:600;color:var(--navy);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:200px;">' + esc(nl.subject_line || nl.title || 'Untitled') + '</div>'
      + trackBadge
      + '</div>'
      + nlStatusPill(nl.status)
      + '</div>'
      + '<div style="display:flex;align-items:center;justify-content:space-between;font-size:11px;color:var(--text-muted);">'
      + '<span>' + icon('users', 12) + ' ' + recipCount + ' contacts &middot; ' + esc(date) + '</span>'
      + (nl.status !== 'sending' ? '<button class="btn btn-secondary btn-sm" style="padding:2px 6px;font-size:11px;color:var(--red);border-color:var(--red);" onclick="event.stopPropagation();deleteNewsletter(\'' + nl.id + '\')" title="Delete.">' + icon('x', 12) + '</button>' : '')
      + '</div>'
      + '</div>';
  }).join('');
}

// ── CRUD ─────────────────────────────────────────────────────────────────────

async function createNewsletter() {
  const btn = event?.target;
  if (btn) { btn.disabled = true; btn.textContent = 'Creating...'; }
  try {
    const nl = await api('/newsletters', {
      method: 'POST',
      body: JSON.stringify({ title: 'New Newsletter', track: 'prospect', subject_line: '', content_json: {} }),
    });
    toast('Newsletter created', 'success');
    await openNewsletterEditor(nl.id);
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '+ New Newsletter'; }
  }
}

async function deleteNewsletter(id) {
  if (!await customConfirm('Delete this newsletter?', {destructive: true})) return;
  try {
    await api('/newsletters/' + id, { method: 'DELETE' });
    toast('Deleted', 'success');
    loadNewsletters();
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
}

async function deleteAllNewsletters() {
  if (!await customConfirm('Delete ALL newsletters? This cannot be undone.', {destructive: true})) return;
  if (!await customConfirm('Are you sure? This will delete every newsletter except those currently sending.', {destructive: true})) return;
  try {
    const res = await api('/newsletters/clear', { method: 'DELETE' });
    toast('Deleted ' + (res.deleted || 0) + ' newsletter(s)', 'success');
    loadNewsletters();
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
}

async function duplicateNewsletter() {
  if (!nlCurrent) return;
  try {
    const nl = await api('/newsletters/' + nlCurrent.id + '/duplicate', { method: 'POST' });
    toast('Duplicated', 'success');
    await openNewsletterEditor(nl.id);
  } catch (e) {
    toast('Duplicate failed: ' + e.message, 'error');
  }
}

// ── Editor ───────────────────────────────────────────────────────────────────

async function openNewsletterEditor(id) {
  _nlStopSendPoll();
  const listView = document.getElementById('nl-list-view');
  const editorView = document.getElementById('nl-editor-view');
  const unsubView = document.getElementById('nl-unsub-view');
  if (listView) listView.classList.add('hidden');
  if (unsubView) unsubView.classList.add('hidden');
  if (editorView) editorView.classList.remove('hidden');

  try {
    nlCurrent = await api('/newsletters/' + id);
    renderNewsletterEditor();
    loadNlOwners();
    loadNlRecipients();
    checkNlOutlookStatus();
    if (nlCurrent.status === 'sending') _nlStartSendPoll();
  } catch (e) {
    toast('Failed to load newsletter: ' + e.message, 'error');
    closeNewsletterEditor();
  }
}

function closeNewsletterEditor() {
  _nlStopSendPoll();
  const listView = document.getElementById('nl-list-view');
  const editorView = document.getElementById('nl-editor-view');
  if (listView) listView.classList.remove('hidden');
  if (editorView) editorView.classList.add('hidden');
  nlCurrent = null;
  loadNewsletters();
}

function renderNewsletterEditor() {
  if (!nlCurrent) return;
  const nl = nlCurrent;
  document.getElementById('nl-editor-title').textContent = nl.subject_line || nl.title || 'Untitled';
  document.getElementById('nl-editor-status').innerHTML = nlStatusPill(nl.status);
  document.getElementById('nl-ed-subject').value = nl.subject_line || '';

  // Restore body text from content_json
  const bodyText = (nl.content_json && nl.content_json.body_text) || '';
  document.getElementById('nl-ed-body').value = bodyText;

  // Track selector
  const trackSel = document.getElementById('nl-ed-track');
  if (trackSel) trackSel.value = nl.track || 'prospect';
  onNlTrackChange();  // update contact panel visibility

  // Show HubSpot sync status
  const hsStatus = document.getElementById('nl-hubspot-sync-status');
  if (hsStatus && nl.hubspot_last_synced_at) {
    hsStatus.textContent = 'Last synced: ' + new Date(nl.hubspot_last_synced_at).toLocaleString();
  } else if (hsStatus) {
    hsStatus.textContent = '';
  }

  // Show iterate controls if compose history exists
  const iterateEl = document.getElementById('nl-iterate-controls');
  if (iterateEl) {
    const hasHistory = nl.content_json && nl.content_json.compose_history && nl.content_json.compose_history.length > 0;
    iterateEl.classList.toggle('hidden', !hasHistory);
  }

  // Show upload status if source data exists
  const uploadStatus = document.getElementById('nl-upload-status');
  if (uploadStatus && nl.content_json) {
    const sd = nl.content_json.source_data;
    const sf = nl.content_json.source_filename;
    if (sd && sd.length > 0) {
      uploadStatus.innerHTML = '<span style="color:var(--green);">' + icon('check', 12) + ' ' + sd.length + ' rows loaded from ' + esc(sf || 'file') + '</span>';
    }
  }

  // Action buttons visibility
  const isSending = nl.status === 'sending';
  const isSent = nl.status === 'sent';
  const isScheduled = nl.status === 'scheduled';
  const isDraft = nl.status === 'draft' || nl.status === 'failed';

  document.getElementById('nl-btn-save').classList.toggle('hidden', isSending);
  document.getElementById('nl-btn-push').classList.toggle('hidden', isSending);
  document.getElementById('nl-btn-send').classList.toggle('hidden', isSending);
  document.getElementById('nl-btn-test').classList.toggle('hidden', isSending);
  document.getElementById('nl-btn-schedule').classList.toggle('hidden', isScheduled || isSending);
  document.getElementById('nl-btn-cancel-sched').classList.toggle('hidden', !isScheduled);

  // Send status panel
  const statusPanel = document.getElementById('nl-send-status-panel');
  if (isSending || isSent) {
    statusPanel.classList.remove('hidden');
    _nlRefreshSendStatus();
  } else {
    statusPanel.classList.add('hidden');
  }

  // Info text
  const pushInfo = document.getElementById('nl-push-info');
  const schedInfo = document.getElementById('nl-schedule-info');
  if (isSent) {
    pushInfo.textContent = 'This newsletter has been sent. Editing will reset it to draft.';
  } else if (isSending) {
    pushInfo.textContent = 'Send in progress...';
  } else {
    pushInfo.textContent = '';
  }
  if (isScheduled && nl.scheduled_at) {
    schedInfo.textContent = 'Scheduled for: ' + new Date(nl.scheduled_at).toLocaleString();
    schedInfo.classList.remove('hidden');
  } else {
    schedInfo.classList.add('hidden');
  }
}

// ── Outlook connection status (reuses the same OAuth modal as conference) ─────

async function checkNlOutlookStatus() {
  try {
    const s = await api('/outlook/auth/status');
    const badge = document.getElementById('nl-outlook-badge');
    if (!badge) return;
    if (s.authenticated) {
      badge.style.background = '#dcfce7';
      badge.style.color = '#15803d';
      badge.style.borderColor = 'rgba(21,128,61,0.2)';
      badge.innerHTML = '&#9679; Connected' + (s.account ? ' (' + esc(s.account) + ')' : '');
      badge.onclick = async () => {
        if (!await customConfirm('Disconnect Outlook?')) return;
        await api('/outlook/auth/disconnect', { method: 'POST' });
        checkNlOutlookStatus();
      };
    } else {
      badge.style.background = 'var(--red-bg)';
      badge.style.color = 'var(--red)';
      badge.style.borderColor = 'rgba(220,38,38,0.2)';
      badge.innerHTML = '&#9679; Outlook not connected';
      badge.onclick = () => connectOutlook();
    }
  } catch (e) { console.error('checkNlOutlookStatus:', e); }
}

// ── Owner selector ───────────────────────────────────────────────────────────

async function loadNlOwners() {
  try {
    nlOwners = await api('/newsletter-owners');
    const select = document.getElementById('nl-ed-owner');
    // Only show current user as selectable owner (API rejects setting others)
    const currentUserId = nlCurrent && nlCurrent.owner_id;
    select.innerHTML = nlOwners.filter(u => u.id === currentUserId).map(u => '<option value="' + u.id + '" selected>' + esc(u.name || u.email) + (u.has_outlook ? '' : ' (not connected)') + '</option>').join('');
    // If current owner not in list (edge case), show all but disabled
    if (!select.value && nlOwners.length) {
      select.innerHTML = nlOwners.map(u => '<option value="' + u.id + '" ' + (u.id === currentUserId ? 'selected' : 'disabled') + '>' + esc(u.name || u.email) + (u.has_outlook ? '' : ' (not connected)') + '</option>').join('');
    }
    select.onchange = function() {
      const owner = nlOwners.find(u => u.id === this.value);
      const warn = document.getElementById('nl-owner-warning');
      if (owner && !owner.has_outlook) warn.classList.remove('hidden');
      else warn.classList.add('hidden');
    };
    if (select.value) select.onchange();
  } catch (e) {
    console.warn('loadNlOwners failed:', e.message);
  }
}

// ── Save newsletter ──────────────────────────────────────────────────────────

async function saveNewsletter() {
  if (!nlCurrent) return;
  const btn = document.getElementById('nl-btn-save');
  if (btn) { btn.disabled = true; btn.textContent = 'Saving...'; }
  try {
    const subject = document.getElementById('nl-ed-subject').value.trim();
    const bodyText = document.getElementById('nl-ed-body').value;
    const ownerId = document.getElementById('nl-ed-owner').value;
    const track = document.getElementById('nl-ed-track')?.value;

    const payload = {
      title: subject || 'Untitled',
      subject_line: subject,
      content_json: { body_text: bodyText },
    };
    if (ownerId) payload.owner_id = ownerId;
    if (track) payload.track = track;

    nlCurrent = await api('/newsletters/' + nlCurrent.id, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
    renderNewsletterEditor();
    toast('Saved', 'success');
  } catch (e) {
    toast('Save failed: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Save Draft'; }
  }
}

// ── Push to Drafts ───────────────────────────────────────────────────────────

async function pushToDrafts() {
  if (!nlCurrent) return;
  const subject = document.getElementById('nl-ed-subject').value.trim();
  const bodyText = document.getElementById('nl-ed-body').value.trim();
  const ownerId = document.getElementById('nl-ed-owner').value;

  if (!subject) { toast('Subject line is required', 'warning'); return; }
  if (!bodyText) { toast('Body text is required', 'warning'); return; }
  if (!ownerId) { toast('Select a sending email first', 'warning'); return; }

  // Save first
  await saveNewsletter();

  const btn = document.getElementById('nl-btn-push');
  if (btn) { btn.disabled = true; btn.textContent = 'Pushing...'; }
  try {
    const res = await api('/newsletters/' + nlCurrent.id + '/push-drafts', {
      method: 'POST',
      body: JSON.stringify({ body_text: bodyText }),
    });
    toast('Created ' + (res.created || 0) + ' drafts in Outlook', 'success');
    nlCurrent = await api('/newsletters/' + nlCurrent.id);
    renderNewsletterEditor();
  } catch (e) {
    toast('Push failed: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Push to Drafts'; }
  }
}

// ── Send newsletter (async) ──────────────────────────────────────────────────

async function sendNewsletter() {
  if (!nlCurrent) return;
  if (!await customConfirm('Send this newsletter to all pending contacts now?')) return;

  // Save first
  await saveNewsletter();

  const btn = document.getElementById('nl-btn-send');
  if (btn) { btn.disabled = true; btn.textContent = 'Sending...'; }
  try {
    await api('/newsletters/' + nlCurrent.id + '/send', { method: 'POST' });
    toast('Send started', 'success');
    nlCurrent = await api('/newsletters/' + nlCurrent.id);
    renderNewsletterEditor();
    _nlStartSendPoll();
  } catch (e) {
    toast('Send failed: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = icon('send', 12) + ' Send Now'; }
  }
}

// ── Send status polling ──────────────────────────────────────────────────────

function _nlStartSendPoll() {
  _nlStopSendPoll();
  _nlSendPollTimer = setInterval(async () => {
    if (!nlCurrent) { _nlStopSendPoll(); return; }
    try {
      const s = await api('/newsletters/' + nlCurrent.id + '/send-status');
      _nlRenderSendStats(s);
      if (s.status !== 'sending') {
        _nlStopSendPoll();
        nlCurrent = await api('/newsletters/' + nlCurrent.id);
        renderNewsletterEditor();
        if (s.status === 'sent') toast('Newsletter sent successfully', 'success');
        else if (s.status === 'failed') toast('Newsletter send failed', 'error');
      }
    } catch (e) { /* ignore poll errors */ }
  }, 3000);
}

function _nlStopSendPoll() {
  if (_nlSendPollTimer) { clearInterval(_nlSendPollTimer); _nlSendPollTimer = null; }
}

async function _nlRefreshSendStatus() {
  if (!nlCurrent) return;
  try {
    const s = await api('/newsletters/' + nlCurrent.id + '/send-status');
    _nlRenderSendStats(s);
  } catch (e) { /* ignore */ }
}

function _nlRenderSendStats(s) {
  const bar = document.getElementById('nl-send-bar');
  const statsEl = document.getElementById('nl-send-stats');
  const total = s.total || 1;
  const done = (s.sent || 0) + (s.failed || 0);
  const pct = Math.round((done / total) * 100);
  if (bar) bar.style.width = pct + '%';
  if (statsEl) {
    statsEl.innerHTML = '<span style="color:#059669;">' + icon('check', 12) + ' ' + (s.sent || 0) + ' sent</span>'
      + '<span style="color:#dc2626;">' + icon('x', 12) + ' ' + (s.failed || 0) + ' failed</span>'
      + '<span>' + icon('clock', 12) + ' ' + (s.pending || 0) + ' pending</span>'
      + '<span style="color:var(--text-muted);">' + pct + '% complete</span>';
  }
}

// ── Test send ────────────────────────────────────────────────────────────────

function showNlTestSendModal() {
  document.getElementById('nl-test-email').value = _currentUser?.email || '';
  document.getElementById('nl-test-send-modal').classList.remove('hidden');
}
function closeNlTestSendModal() { document.getElementById('nl-test-send-modal').classList.add('hidden'); }

async function confirmNlTestSend() {
  if (!nlCurrent) return;
  const email = document.getElementById('nl-test-email').value.trim();
  if (!email) { toast('Email is required', 'warning'); return; }

  // Save first
  await saveNewsletter();

  const btn = document.getElementById('nl-test-send-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Sending...'; }
  try {
    const res = await api('/newsletters/' + nlCurrent.id + '/send-test', {
      method: 'POST',
      body: JSON.stringify({ email }),
    });
    if (res.status === 'sent') toast('Test email sent to ' + email, 'success');
    else toast('Test send failed', 'error');
    closeNlTestSendModal();
  } catch (e) {
    toast('Test send failed: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Send Test'; }
  }
}

// ── Schedule ─────────────────────────────────────────────────────────────────

function showNlScheduleModal() {
  // Default to 1 hour from now in local time
  const d = new Date(Date.now() + 3600000);
  // Format as local YYYY-MM-DDTHH:MM for datetime-local input
  const local = d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0') + 'T' + String(d.getHours()).padStart(2,'0') + ':' + String(d.getMinutes()).padStart(2,'0');
  document.getElementById('nl-schedule-datetime').value = local;
  document.getElementById('nl-schedule-modal').classList.remove('hidden');
}
function closeNlScheduleModal() { document.getElementById('nl-schedule-modal').classList.add('hidden'); }

async function confirmNlSchedule() {
  if (!nlCurrent) return;
  const dt = document.getElementById('nl-schedule-datetime').value;
  if (!dt) { toast('Select a date and time', 'warning'); return; }

  // Save first
  await saveNewsletter();

  const btn = document.getElementById('nl-schedule-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Scheduling...'; }
  try {
    await api('/newsletters/' + nlCurrent.id + '/schedule', {
      method: 'POST',
      // datetime-local value is in local time — convert to UTC ISO string
      body: JSON.stringify({ scheduled_at: new Date(dt).toISOString() }),
    });
    toast('Newsletter scheduled', 'success');
    closeNlScheduleModal();
    nlCurrent = await api('/newsletters/' + nlCurrent.id);
    renderNewsletterEditor();
  } catch (e) {
    toast('Schedule failed: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Schedule'; }
  }
}

async function cancelNlSchedule() {
  if (!nlCurrent) return;
  if (!await customConfirm('Cancel the scheduled send?')) return;
  try {
    await api('/newsletters/' + nlCurrent.id + '/cancel-schedule', { method: 'POST' });
    toast('Schedule cancelled', 'success');
    nlCurrent = await api('/newsletters/' + nlCurrent.id);
    renderNewsletterEditor();
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
}

// ── Preview ──────────────────────────────────────────────────────────────────

async function previewNewsletter() {
  if (!nlCurrent) return;
  // Save first to ensure preview is current
  await saveNewsletter();
  try {
    const res = await fetch(API + '/newsletters/' + nlCurrent.id + '/preview', {
      headers: _authToken ? { 'Authorization': 'Bearer ' + _authToken } : {},
      credentials: 'same-origin',
    });
    if (!res.ok) throw new Error(res.statusText);
    const html = await res.text();
    const iframe = document.getElementById('nl-preview-iframe');
    if (iframe) {
      iframe.src = '';
      iframe.srcdoc = html;
    }
    document.getElementById('nl-preview-modal').classList.remove('hidden');
  } catch (e) {
    toast('Preview failed: ' + e.message, 'error');
  }
}
function closeNlPreviewModal() {
  document.getElementById('nl-preview-modal').classList.add('hidden');
  const iframe = document.getElementById('nl-preview-iframe');
  if (iframe) iframe.src = '';
}

// ── Recipients ───────────────────────────────────────────────────────────────

let _nlRecipPage = 1;
let _nlRecipSearch = '';

async function loadNlRecipients(page, search) {
  if (!nlCurrent) return;
  if (page !== undefined) _nlRecipPage = page;
  if (search !== undefined) _nlRecipSearch = search;
  try {
    let url = '/newsletters/' + nlCurrent.id + '/recipients?per_page=50&page=' + _nlRecipPage;
    if (_nlRecipSearch) url += '&search=' + encodeURIComponent(_nlRecipSearch);
    const data = await api(url);
    const items = data.items || [];
    const totalPages = Math.ceil((data.total || 0) / 50) || 1;
    const countEl = document.getElementById('nl-recip-count');
    if (countEl) countEl.textContent = '(' + (data.total || items.length) + ')';

    // Search bar
    let searchEl = document.getElementById('nl-recip-search');
    if (!searchEl) {
      const listParent = document.getElementById('nl-recip-list')?.parentElement;
      if (listParent) {
        const searchDiv = document.createElement('div');
        searchDiv.style.cssText = 'margin-bottom:6px;';
        searchDiv.innerHTML = '<input id="nl-recip-search" type="text" placeholder="Search contacts..." style="width:100%;padding:4px 8px;font-size:12px;border:1px solid var(--border);border-radius:4px;background:var(--bg-secondary);color:var(--text-primary);" />';
        listParent.insertBefore(searchDiv, document.getElementById('nl-recip-list'));
        searchEl = document.getElementById('nl-recip-search');
        let _debounce;
        searchEl.addEventListener('input', function() {
          clearTimeout(_debounce);
          _debounce = setTimeout(() => loadNlRecipients(1, this.value), 300);
        });
      }
    }
    if (searchEl && searchEl.value !== _nlRecipSearch) searchEl.value = _nlRecipSearch;

    const listEl = document.getElementById('nl-recip-list');
    if (!listEl) return;
    if (!items.length) {
      listEl.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:8px 0;">' + (_nlRecipSearch ? 'No contacts matching "' + esc(_nlRecipSearch) + '".' : 'No contacts yet. Use Populate, Import CSV, or Add manually.') + '</div>';
      return;
    }
    listEl.innerHTML = items.map(r => {
      const statusBadge = r.status && r.status !== 'pending' ? ' <span style="font-size:9px;padding:1px 4px;border-radius:4px;background:' + (r.status === 'sent' ? '#10b98115' : r.status === 'failed' ? '#ef444415' : 'var(--border)') + ';color:' + (r.status === 'sent' ? '#059669' : r.status === 'failed' ? '#dc2626' : 'var(--text-muted)') + ';">' + esc(r.status) + '</span>' : '';
      return '<div style="display:flex;align-items:center;justify-content:space-between;padding:3px 0;border-bottom:1px solid var(--border);">'
        + '<div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
        + '<span style="font-weight:500;">' + esc(r.contact_email) + '</span>'
        + statusBadge
        + (r.contact_name ? '<span style="color:var(--text-muted);"> &middot; ' + esc(r.contact_name) + '</span>' : '')
        + (r.contact_company ? '<span style="color:var(--text-muted);"> &middot; ' + esc(r.contact_company) + '</span>' : '')
        + '</div>'
        + '<button class="btn btn-secondary btn-sm" style="padding:1px 4px;color:var(--red);flex-shrink:0;" onclick="removeNlRecipient(\'' + esc(r.contact_email) + '\')" title="Remove.">' + icon('x', 10) + '</button>'
        + '</div>';
    }).join('');
    // Pagination controls
    if (totalPages > 1) {
      listEl.innerHTML += '<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;font-size:11px;color:var(--text-muted);">'
        + '<span>Page ' + _nlRecipPage + ' of ' + totalPages + ' (' + data.total + ' total)</span>'
        + '<span>'
        + (_nlRecipPage > 1 ? '<button class="btn btn-secondary btn-sm" style="padding:1px 8px;margin-right:4px;" onclick="loadNlRecipients(' + (_nlRecipPage-1) + ')">Prev</button>' : '')
        + (_nlRecipPage < totalPages ? '<button class="btn btn-secondary btn-sm" style="padding:1px 8px;" onclick="loadNlRecipients(' + (_nlRecipPage+1) + ')">Next</button>' : '')
        + '</span></div>';
    }
  } catch (e) {
    console.warn('loadNlRecipients failed:', e.message);
  }
}

async function populateNlRecipients() {
  if (!nlCurrent) return;
  const btn = event?.target;
  if (btn) { btn.disabled = true; }
  try {
    const res = await api('/newsletters/' + nlCurrent.id + '/recipients/populate', { method: 'POST' });
    toast('Added ' + (res.added || 0) + ' contacts (' + (res.skipped_duplicates || 0) + ' dupes, ' + (res.skipped_unsubscribed || 0) + ' suppressed)', 'success');
    loadNlRecipients();
  } catch (e) {
    toast('Populate failed: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; }
  }
}

async function onNlTrackChange() {
  const track = document.getElementById('nl-ed-track')?.value;
  const standardBtns = document.getElementById('nl-contacts-standard');
  const hubspotBtns = document.getElementById('nl-contacts-hubspot');
  if (standardBtns) standardBtns.style.display = track === 'hubspot' ? 'none' : 'flex';
  if (hubspotBtns) hubspotBtns.style.display = track === 'hubspot' ? 'flex' : 'none';
  // Auto-save track change so sync endpoint works immediately
  if (nlCurrent && nlCurrent.track !== track) {
    try {
      nlCurrent = await api('/newsletters/' + nlCurrent.id, {
        method: 'PATCH', body: JSON.stringify({ track }),
      });
    } catch (e) { /* silent — will be saved with next Save Draft */ }
  }
}

async function syncHubspotRecipients() {
  if (!nlCurrent) return;
  const btn = event?.target?.closest('button');
  if (btn) { btn.disabled = true; btn.textContent = 'Syncing…'; }
  const statusEl = document.getElementById('nl-hubspot-sync-status');
  if (statusEl) statusEl.textContent = 'Syncing from HubSpot…';
  try {
    // Ensure track is saved as hubspot before syncing
    if (nlCurrent.track !== 'hubspot') {
      nlCurrent = await api('/newsletters/' + nlCurrent.id, {
        method: 'PATCH', body: JSON.stringify({ track: 'hubspot' }),
      });
    }
    const res = await api('/newsletters/' + nlCurrent.id + '/sync-hubspot', { method: 'POST' });
    toast('HubSpot sync: +' + (res.added || 0) + ' added, -' + (res.removed || 0) + ' removed (' + (res.total_recipients || 0) + ' total)', 'success');
    loadNlRecipients();
    nlCurrent = await api('/newsletters/' + nlCurrent.id);
    if (statusEl && nlCurrent.hubspot_last_synced_at) {
      statusEl.textContent = 'Last synced: ' + new Date(nlCurrent.hubspot_last_synced_at).toLocaleString();
    }
  } catch (e) {
    toast('HubSpot sync failed: ' + e.message, 'error');
    if (statusEl) statusEl.textContent = 'Sync failed';
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '<span data-icon="refresh-cw" data-icon-size="12"></span> Sync from HubSpot'; }
  }
}

function showNlAddRecipient() {
  document.getElementById('nl-recip-email').value = '';
  document.getElementById('nl-recip-name').value = '';
  document.getElementById('nl-recip-company').value = '';
  document.getElementById('nl-add-recip-modal').classList.remove('hidden');
}
function closeNlAddRecipModal() { document.getElementById('nl-add-recip-modal').classList.add('hidden'); }

async function confirmAddNlRecipient() {
  if (!nlCurrent) return;
  const email = document.getElementById('nl-recip-email').value.trim();
  if (!email) { toast('Email is required', 'warning'); return; }
  try {
    await api('/newsletters/' + nlCurrent.id + '/recipients', {
      method: 'POST',
      body: JSON.stringify({
        email,
        name: document.getElementById('nl-recip-name').value.trim(),
        company: document.getElementById('nl-recip-company').value.trim(),
      }),
    });
    toast('Added', 'success');
    closeNlAddRecipModal();
    loadNlRecipients();
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
}

async function removeNlRecipient(email) {
  if (!nlCurrent) return;
  try {
    await api('/newsletters/' + nlCurrent.id + '/recipients/' + encodeURIComponent(email), { method: 'DELETE' });
    loadNlRecipients();
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
}

function showNlImportModal() { document.getElementById('nl-import-modal').classList.remove('hidden'); }
function closeNlImportModal() { document.getElementById('nl-import-modal').classList.add('hidden'); }

async function confirmNlImport() {
  if (!nlCurrent) return;
  const fileInput = document.getElementById('nl-import-file');
  const file = fileInput?.files?.[0];
  if (!file) { toast('Select a CSV file first', 'warning'); return; }
  if (file.size > 5 * 1024 * 1024) { toast('File too large (max 5 MB)', 'error'); return; }
  const formData = new FormData();
  formData.append('file', file);
  try {
    const res = await fetch(API + '/newsletters/' + nlCurrent.id + '/recipients/import', {
      method: 'POST',
      headers: _authToken ? { 'Authorization': 'Bearer ' + _authToken } : {},
      credentials: 'same-origin',
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || res.statusText);
    }
    const data = await res.json();
    toast('Imported ' + (data.added || 0) + ' contacts', 'success');
    closeNlImportModal();
    loadNlRecipients();
  } catch (e) {
    toast('Import failed: ' + e.message, 'error');
  }
}

// ── AI Compose ───────────────────────────────────────────────────────────────

async function uploadNlContent() {
  if (!nlCurrent) return;
  const fileInput = document.getElementById('nl-compose-file');
  const file = fileInput?.files?.[0];
  if (!file) { toast('Select a file first', 'warning'); return; }
  if (file.size > 10 * 1024 * 1024) { toast('File too large (max 10 MB)', 'error'); return; }

  const statusEl = document.getElementById('nl-upload-status');
  statusEl.textContent = 'Uploading...';

  const formData = new FormData();
  formData.append('file', file);
  try {
    const res = await fetch(API + '/newsletters/' + nlCurrent.id + '/upload-content', {
      method: 'POST',
      headers: _authToken ? { 'Authorization': 'Bearer ' + _authToken } : {},
      credentials: 'same-origin',
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || res.statusText);
    }
    const data = await res.json();
    statusEl.style.color = '';
    statusEl.innerHTML = '<span style="color:var(--green);">' + icon('check', 12) + ' ' + data.rows + ' rows uploaded from ' + esc(data.filename || 'file') + '</span>';
    if (data.columns?.length) {
      statusEl.innerHTML += '<br><span style="font-size:10px;color:var(--text-muted);">Columns: ' + data.columns.map(c => esc(c)).join(', ') + '</span>';
    }
    toast('Content uploaded: ' + data.rows + ' rows', 'success');
  } catch (e) {
    statusEl.textContent = 'Upload failed: ' + e.message;
    statusEl.style.color = 'var(--red)';
    toast('Upload failed: ' + e.message, 'error');
  }
}

async function composeNewsletter() {
  if (!nlCurrent) return;

  // Save subject first
  const subject = document.getElementById('nl-ed-subject').value.trim();
  if (!subject) { toast('Enter a subject line first', 'warning'); return; }

  const btn = document.getElementById('nl-btn-compose');
  const statusEl = document.getElementById('nl-compose-status');
  if (btn) { btn.disabled = true; }
  statusEl.textContent = 'Composing... (this may take 10-20s)';

  try {
    // Save first to persist subject
    await saveNewsletter();

    const prompt = document.getElementById('nl-compose-prompt').value.trim();
    const ref = document.getElementById('nl-compose-ref').value.trim();

    const payload = {};
    if (prompt) payload.prompt = prompt;
    if (ref) payload.reference_files = [ref];

    const res = await api('/newsletters/' + nlCurrent.id + '/compose', {
      method: 'POST',
      body: JSON.stringify(payload),
    });

    // Update body text
    document.getElementById('nl-ed-body').value = res.body_text;
    statusEl.innerHTML = '<span style="color:var(--green);">' + icon('check', 12) + ' Composed (' + res.tokens_input + ' in / ' + res.tokens_output + ' out tokens)</span>';

    // Show iterate controls
    document.getElementById('nl-iterate-controls').classList.remove('hidden');

    // Refresh nlCurrent
    nlCurrent = await api('/newsletters/' + nlCurrent.id);

    toast('Newsletter composed', 'success');
  } catch (e) {
    statusEl.textContent = 'Compose failed: ' + e.message;
    statusEl.style.color = 'var(--red)';
    toast('Compose failed: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; }
  }
}

async function iterateNewsletter() {
  if (!nlCurrent) return;
  const input = document.getElementById('nl-iterate-input');
  const instructions = input?.value?.trim();
  if (!instructions) { toast('Enter instructions for what to change', 'warning'); return; }

  const btn = document.getElementById('nl-btn-iterate');
  if (btn) { btn.disabled = true; btn.textContent = 'Iterating...'; }

  try {
    const res = await api('/newsletters/' + nlCurrent.id + '/iterate', {
      method: 'POST',
      body: JSON.stringify({ instructions }),
    });

    document.getElementById('nl-ed-body').value = res.body_text;
    input.value = '';

    const statusEl = document.getElementById('nl-compose-status');
    statusEl.innerHTML = '<span style="color:var(--green);">' + icon('check', 12) + ' Revised (' + res.tokens_input + ' in / ' + res.tokens_output + ' out)</span>';

    nlCurrent = await api('/newsletters/' + nlCurrent.id);

    toast('Newsletter revised', 'success');
  } catch (e) {
    toast('Iterate failed: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Iterate'; }
  }
}

// ── Unsubscribe management ───────────────────────────────────────────────────

let _nlUnsubPage = 1;

function openNlUnsubView() {
  document.getElementById('nl-list-view').classList.add('hidden');
  document.getElementById('nl-editor-view').classList.add('hidden');
  document.getElementById('nl-unsub-view').classList.remove('hidden');
  loadNlUnsubs(1);
}

function closeNlUnsubView() {
  document.getElementById('nl-unsub-view').classList.add('hidden');
  document.getElementById('nl-list-view').classList.remove('hidden');
}

async function loadNlUnsubs(page) {
  _nlUnsubPage = page || 1;
  try {
    const data = await api('/newsletters/unsubscribes?page=' + _nlUnsubPage + '&per_page=25');
    const countEl = document.getElementById('nl-unsub-count');
    if (countEl) countEl.textContent = '(' + (data.total || 0) + ')';
    const listEl = document.getElementById('nl-unsub-list');
    if (!listEl) return;
    if (!(data.items || []).length) {
      listEl.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:8px 0;">No unsubscribed emails.</div>';
    } else {
      listEl.innerHTML = data.items.map(u => '<div style="display:flex;align-items:center;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--border);">'
        + '<div><span style="font-weight:500;">' + esc(u.email) + '</span>'
        + (u.reason ? '<span style="color:var(--text-muted);font-size:11px;"> &middot; ' + esc(u.reason) + '</span>' : '')
        + (u.unsubscribed_at ? '<span style="color:var(--text-muted);font-size:11px;"> &middot; ' + new Date(u.unsubscribed_at).toLocaleDateString() + '</span>' : '')
        + '</div>'
        + '<button class="btn btn-secondary btn-sm" style="padding:1px 6px;font-size:11px;" onclick="removeNlUnsub(\'' + esc(u.email) + '\')" title="Re-subscribe this email.">Re-subscribe</button>'
        + '</div>').join('');
    }
    // Pager
    const pagerEl = document.getElementById('nl-unsub-pager');
    const totalPages = Math.ceil((data.total || 0) / 25) || 1;
    if (pagerEl && totalPages > 1) {
      pagerEl.innerHTML = '<button class="btn btn-secondary btn-sm" ' + (_nlUnsubPage <= 1 ? 'disabled' : '') + ' onclick="loadNlUnsubs(' + (_nlUnsubPage - 1) + ')">&larr;</button>'
        + '<span>Page ' + _nlUnsubPage + ' of ' + totalPages + '</span>'
        + '<button class="btn btn-secondary btn-sm" ' + (_nlUnsubPage >= totalPages ? 'disabled' : '') + ' onclick="loadNlUnsubs(' + (_nlUnsubPage + 1) + ')">&rarr;</button>';
    } else if (pagerEl) {
      pagerEl.innerHTML = '';
    }
  } catch (e) {
    toast('Failed to load unsubscribes: ' + e.message, 'error');
  }
}

function showNlAddUnsubModal() {
  document.getElementById('nl-unsub-email').value = '';
  document.getElementById('nl-unsub-reason').value = '';
  document.getElementById('nl-add-unsub-modal').classList.remove('hidden');
}
function closeNlAddUnsubModal() { document.getElementById('nl-add-unsub-modal').classList.add('hidden'); }

async function confirmNlAddUnsub() {
  const email = document.getElementById('nl-unsub-email').value.trim();
  if (!email) { toast('Email is required', 'warning'); return; }
  try {
    await api('/newsletters/unsubscribes', {
      method: 'POST',
      body: JSON.stringify({ email, reason: document.getElementById('nl-unsub-reason').value.trim() || undefined }),
    });
    toast('Added to unsubscribe list', 'success');
    closeNlAddUnsubModal();
    loadNlUnsubs(_nlUnsubPage);
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
}

async function removeNlUnsub(email) {
  if (!await customConfirm('Re-subscribe ' + email + '?')) return;
  try {
    await api('/newsletters/unsubscribes/' + encodeURIComponent(email), { method: 'DELETE' });
    toast('Re-subscribed', 'success');
    loadNlUnsubs(_nlUnsubPage);
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
}

// ══════════════════════════════════════════════════════════════════════════
// GTM ANALYTICS
// ══════════════════════════════════════════════════════════════════════════

let _gtmData = null;
let _gtmConferences = [];
let _gtmChart = null;

const GTM_METRIC_LABELS = {
  connections_sent: 'Connections Sent',
  messages_sent: 'Messages Sent',
  responses: 'Responses',
  meetings_booked: 'Meetings Booked',
  first_emails_sent: 'First Emails Sent',
  follow_ups_sent: 'Follow-ups Sent',
  opened: 'Opened',
  bounced: 'Bounced',
  leads_registered: 'Leads Registered',
  emails_sent: 'Emails Sent',
  referral_meetings: 'Referral Meetings',
};

const GTM_SECTION_META = {
  linkedin:    { title: 'LinkedIn Outreach',   metrics: ['connections_sent','messages_sent','responses','meetings_booked'] },
  cold_email:  { title: 'Cold Email',          metrics: ['first_emails_sent','follow_ups_sent','opened','bounced','responses','meetings_booked'] },
  inbound:     { title: 'Inbound Requests',    metrics: ['leads_registered'] },
  conference:  { title: 'Conference Outreach',  metrics: ['emails_sent','responses','meetings_booked'] },
  referral:    { title: 'Referrals',            metrics: ['meetings_booked'] },
};

const GTM_ENTRY_METRICS = {
  linkedin:    [['meetings_booked','Meetings Booked']],
  cold_email:  [['meetings_booked','Meetings Booked']],
  inbound:     [['inbound_lead','Inbound Lead']],
  conference:  [['meetings_booked','Meetings Booked']],
  referral:    [['referral_meetings','Referral Meetings']],
};

function _gtmDateParams() {
  const mode = document.getElementById('gtm-date-mode').value;
  if (mode === 'range') {
    const s = document.getElementById('gtm-start-date').value;
    const e = document.getElementById('gtm-end-date').value;
    if (s && e) return `start_date=${s}&end_date=${e}`;
  }
  // Monthly: current year
  const now = new Date();
  return `start_date=${now.getFullYear()}-01-01&end_date=${now.getFullYear()}-12-31`;
}

async function loadGtmAnalytics() {
  const loading = document.getElementById('gtm-loading');
  const empty = document.getElementById('gtm-empty');
  const content = document.getElementById('gtm-content');
  loading.classList.remove('hidden'); empty.classList.add('hidden'); content.classList.add('hidden');
  document.getElementById('gtm-chart-panel').classList.add('hidden');

  // Populate user filter
  const userSel = document.getElementById('gtm-user-select');
  if (userSel.options.length <= 1 && _allUsers.length) {
    _allUsers.forEach(u => {
      const o = document.createElement('option');
      o.value = u.id; o.textContent = u.name || u.email;
      userSel.appendChild(o);
    });
  }

  // Populate entry modal user dropdown too
  const entryUserSel = document.getElementById('gtm-entry-user');
  if (entryUserSel.options.length <= 1 && _allUsers.length) {
    _allUsers.forEach(u => {
      const o = document.createElement('option');
      o.value = u.id; o.textContent = u.name || u.email;
      entryUserSel.appendChild(o);
    });
  }

  try {
    // Build query params
    let params = _gtmDateParams();
    const userId = document.getElementById('gtm-user-select').value;
    const channel = document.getElementById('gtm-channel-select').value;
    const conf = document.getElementById('gtm-conf-select').value;
    if (userId) params += `&user_id=${userId}`;
    if (channel) params += `&category=${channel}`;
    if (conf) params += `&conference_name=${conf}`;

    const [data, conferences] = await Promise.all([
      api(`/gtm-analytics/summary?${params}`),
      api('/gtm-analytics/conferences'),
    ]);
    _gtmData = data;
    _gtmConferences = conferences;

    // Populate conference filter
    const confSel = document.getElementById('gtm-conf-select');
    const curVal = confSel.value;
    confSel.innerHTML = '<option value="">All Conferences</option>';
    conferences.forEach(c => {
      const o = document.createElement('option');
      o.value = c; o.textContent = c;
      if (c === curVal) o.selected = true;
      confSel.appendChild(o);
    });

    loading.classList.add('hidden');
    if (!data || !data.months || data.months.length === 0) {
      empty.classList.remove('hidden');
      return;
    }
    content.classList.remove('hidden');
    renderGtmAnalytics(data);
  } catch (e) {
    loading.classList.add('hidden');
    content.classList.remove('hidden');
    content.innerHTML = `<div style="color:var(--red);padding:12px;">Failed to load GTM analytics: ${esc(e.message)}</div>`;
  }
}

function renderGtmAnalytics(data) {
  const content = document.getElementById('gtm-content');
  const months = data.months || [];
  const sections = data.sections || {};
  const channel = document.getElementById('gtm-channel-select').value;

  // Show and render chart
  document.getElementById('gtm-chart-panel').classList.remove('hidden');
  updateGtmChart();

  let html = '';

  // Determine which sections to show
  const sectionKeys = channel ? [channel] : Object.keys(GTM_SECTION_META);

  sectionKeys.forEach(key => {
    const meta = GTM_SECTION_META[key];
    if (!meta) return;
    const sectionData = sections[key];
    if (!sectionData) return;

    html += renderGtmSection(key, meta.title, meta.metrics, sectionData, months);
  });

  // Grand total meetings
  if (!channel && data.grand_total_meetings) {
    html += `<div class="panel" style="margin-top:16px;">
      <div style="padding:14px 16px;font-size:14px;font-weight:600;color:var(--navy);border-bottom:1px solid var(--border);">
        Total Meetings (All Channels)
      </div>
      <div style="overflow-x:auto;">
        <table class="analytics-table" style="width:100%;font-size:13px;">
          <thead><tr><th style="text-align:left;padding:8px 12px;">Metric</th>`;
    months.forEach(m => { html += `<th style="text-align:right;padding:8px 10px;">${_gtmMonthLabel(m)}</th>`; });
    html += `<th style="text-align:right;padding:8px 12px;font-weight:700;background:var(--surface-raised);">Cumulative</th></tr></thead>
          <tbody><tr><td style="padding:8px 12px;font-weight:500;">Meetings</td>`;
    const gm = data.grand_total_meetings;
    months.forEach(m => { html += `<td style="text-align:right;padding:8px 10px;">${gm[m] || 0}</td>`; });
    html += `<td style="text-align:right;padding:8px 12px;font-weight:700;background:var(--surface-raised);">${gm.cumulative || 0}</td></tr></tbody></table></div></div>`;
  }

  // Conference breakdown
  if (sections.conference && sections.conference.by_conference && sections.conference.by_conference.length) {
    html += `<div class="panel" style="margin-top:16px;">
      <div class="section-header" id="sh-gtm-conf-breakdown" data-body="gtm-conf-breakdown-body" onclick="toggleSection('sh-gtm-conf-breakdown')">
        <div class="panel-title" style="margin-bottom:0;">Conference Breakdown</div>
        <span class="collapse-chevron">&#9660;</span>
      </div>
      <div id="gtm-conf-breakdown-body" class="section-body">
        <div style="overflow-x:auto;">
          <table class="analytics-table" style="width:100%;font-size:13px;">
            <thead><tr><th style="text-align:left;padding:8px 12px;">Conference</th><th style="text-align:right;padding:8px 10px;">Emails Sent</th><th style="text-align:right;padding:8px 10px;">Responses</th><th style="text-align:right;padding:8px 10px;">Meetings</th></tr></thead><tbody>`;
    sections.conference.by_conference.forEach(c => {
      html += `<tr><td style="padding:8px 12px;">${esc(c.conference_name)}</td><td style="text-align:right;padding:8px 10px;">${c.emails_sent}</td><td style="text-align:right;padding:8px 10px;">${c.responses}</td><td style="text-align:right;padding:8px 10px;">${c.meetings_booked}</td></tr>`;
    });
    html += `</tbody></table></div></div></div>`;
  }

  // Manual entries list
  html += `<div class="panel" style="margin-top:16px;">
    <div class="section-header" id="sh-gtm-manual" data-body="gtm-manual-body" onclick="toggleSection('sh-gtm-manual')">
      <div class="panel-title" style="margin-bottom:0;">Manual Entries</div>
      <span class="collapse-chevron">&#9660;</span>
    </div>
    <div id="gtm-manual-body" class="section-body">
      <div id="gtm-manual-list" style="color:var(--text-muted);font-size:13px;">Loading...</div>
    </div>
  </div>`;

  content.innerHTML = html;
  loadGtmManualEntries();
}

function renderGtmSection(key, title, metricKeys, sectionData, months) {
  const sectionId = `sh-gtm-${key}`;
  const bodyId = `gtm-${key}-body`;

  let html = `<div class="panel" style="margin-top:16px;">
    <div class="section-header" id="${sectionId}" data-body="${bodyId}" onclick="toggleSection('${sectionId}')">
      <div class="panel-title" style="margin-bottom:0;">${esc(title)}</div>
      <span class="collapse-chevron">&#9660;</span>
    </div>
    <div id="${bodyId}" class="section-body">
      <div style="overflow-x:auto;">
        <table class="analytics-table" style="width:100%;font-size:13px;">
          <thead><tr><th style="text-align:left;padding:8px 12px;">Metric</th>`;

  months.forEach(m => {
    html += `<th style="text-align:right;padding:8px 10px;">${_gtmMonthLabel(m)}</th>`;
  });
  html += `<th style="text-align:right;padding:8px 12px;font-weight:700;background:var(--surface-raised);">Cumulative</th></tr></thead><tbody>`;

  metricKeys.forEach(mk => {
    const data = sectionData[mk];
    if (!data) return;
    const label = GTM_METRIC_LABELS[mk] || mk;
    html += `<tr><td style="padding:8px 12px;font-weight:500;">${esc(label)}</td>`;
    months.forEach(m => {
      const val = data[m] || 0;
      html += `<td style="text-align:right;padding:8px 10px;">${val}</td>`;
    });
    html += `<td style="text-align:right;padding:8px 12px;font-weight:700;background:var(--surface-raised);">${data.cumulative || 0}</td></tr>`;
  });

  html += `</tbody></table></div></div></div>`;
  return html;
}

function _gtmMonthLabel(m) {
  const [y, mo] = m.split('-');
  const names = ['','Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return `${names[parseInt(mo)] || mo} ${y}`;
}

// ── Inbound inbox connection ────────────────────────────────────────────

async function checkGtmInboundStatus() {
  const badge = document.getElementById('gtm-inbound-badge');
  const target = document.getElementById('gtm-inbound-target');
  try {
    const s = await api('/gtm-analytics/inbound-inbox/status');
    target.textContent = s.target_inbox ? `Target: ${s.target_inbox}` : '';
    if (s.connected) {
      badge.style.background = '#dcfce7'; badge.style.color = '#166534';
      badge.textContent = '\u25CF Connected' + (s.account ? ' (' + s.account + ')' : '');
      badge.onclick = async () => {
        if (!await customConfirm('Disconnect inbound inbox?')) return;
        await api('/gtm-analytics/inbound-inbox/disconnect', { method: 'POST' });
        checkGtmInboundStatus();
      };
    } else {
      badge.style.background = '#fee2e2'; badge.style.color = '#b91c1c';
      badge.textContent = '\u25CF Not connected';
      badge.onclick = () => openGtmInboundAuth();
    }
  } catch(e) {
    badge.style.background = '#fef3c7'; badge.style.color = '#92400e';
    badge.textContent = '\u25CF Unknown';
  }
}

function openGtmInboundAuth() {
  document.getElementById('gtm-inbound-step1').classList.remove('hidden');
  document.getElementById('gtm-inbound-step2').classList.add('hidden');
  document.getElementById('gtm-inbound-step3').classList.add('hidden');
  const btn = document.getElementById('gtm-inbound-connect-btn');
  btn.disabled = false; btn.textContent = 'Connect Outlook Account';
  document.getElementById('gtm-inbound-auth-modal').classList.remove('hidden');
  document.getElementById('gtm-inbound-auth-modal').style.display = 'flex';
}

function closeGtmInboundAuth() {
  if (window._gtmInboundPoll) { clearInterval(window._gtmInboundPoll); window._gtmInboundPoll = null; }
  document.getElementById('gtm-inbound-auth-modal').classList.add('hidden');
  document.getElementById('gtm-inbound-auth-modal').style.display = '';
  checkGtmInboundStatus();
}

async function startGtmInboundAuth(btn) {
  btn.disabled = true; btn.textContent = 'Connecting\u2026';
  try {
    const data = await api('/gtm-analytics/inbound-inbox/connect', { method: 'POST' });
    if (data.already_authenticated) {
      document.getElementById('gtm-inbound-step1').classList.add('hidden');
      document.getElementById('gtm-inbound-step3').classList.remove('hidden');
      document.getElementById('gtm-inbound-account').textContent = data.account || '';
      return;
    }
    document.getElementById('gtm-inbound-step1').classList.add('hidden');
    document.getElementById('gtm-inbound-step2').classList.remove('hidden');
    document.getElementById('gtm-inbound-user-code').textContent = data.user_code;
    const link = document.getElementById('gtm-inbound-verify-link');
    link.href = data.verification_uri; link.textContent = 'Open ' + data.verification_uri.replace('https://','') + ' \u2197';
    document.getElementById('gtm-inbound-waiting').textContent = 'Waiting for sign-in\u2026';

    // Trigger background polling on server
    try { await api('/gtm-analytics/inbound-inbox/complete', { method: 'POST' }); } catch(e) {}

    // Poll for completion
    const deadline = Date.now() + (data.expires_in || 900) * 1000;
    if (window._gtmInboundPoll) clearInterval(window._gtmInboundPoll);
    window._gtmInboundPoll = setInterval(async () => {
      if (Date.now() > deadline) {
        clearInterval(window._gtmInboundPoll);
        document.getElementById('gtm-inbound-waiting').textContent = 'Code expired. Close and try again.';
        return;
      }
      try {
        const s = await api('/gtm-analytics/inbound-inbox/auth-status');
        if (s.authenticated) {
          clearInterval(window._gtmInboundPoll);
          document.getElementById('gtm-inbound-step2').classList.add('hidden');
          document.getElementById('gtm-inbound-step3').classList.remove('hidden');
          document.getElementById('gtm-inbound-account').textContent = s.account || '';
        } else if (s.failed) {
          clearInterval(window._gtmInboundPoll);
          document.getElementById('gtm-inbound-waiting').textContent = s.error || 'Sign-in failed.';
        }
      } catch(e) {}
    }, 3000);
  } catch(e) {
    toast('Connection error: ' + e.message, 'error');
    btn.disabled = false; btn.textContent = 'Connect Outlook Account';
  }
}

// ── Chart rendering ─────────────────────────────────────────────────────

const GTM_CHANNEL_COLORS = {
  linkedin:   { bg: 'rgba(10,102,194,0.7)',  border: 'rgb(10,102,194)' },
  cold_email: { bg: 'rgba(59,130,246,0.7)',  border: 'rgb(59,130,246)' },
  inbound:    { bg: 'rgba(16,185,129,0.7)',  border: 'rgb(16,185,129)' },
  conference: { bg: 'rgba(245,158,11,0.7)',  border: 'rgb(245,158,11)' },
  referral:   { bg: 'rgba(139,92,246,0.7)',  border: 'rgb(139,92,246)' },
};

const GTM_CHANNEL_LABELS = {
  linkedin: 'LinkedIn', cold_email: 'Cold Email', inbound: 'Inbound',
  conference: 'Conference', referral: 'Referral',
};

function _gtmChartDataForMetric(metric) {
  if (!_gtmData) return { labels: [], datasets: [] };
  const months = _gtmData.months || [];
  const sections = _gtmData.sections || {};
  const channel = document.getElementById('gtm-channel-select').value;
  const labels = months.map(m => _gtmMonthLabel(m));
  const datasets = [];

  const channelKeys = channel ? [channel] : Object.keys(sections);

  channelKeys.forEach(key => {
    const sec = sections[key];
    if (!sec) return;
    const colors = GTM_CHANNEL_COLORS[key] || { bg: 'rgba(107,114,128,0.7)', border: 'rgb(107,114,128)' };
    let values;

    if (metric === 'meetings_booked') {
      const mb = sec.meetings_booked;
      if (!mb) return;
      values = months.map(m => mb[m] || 0);
    } else if (metric === 'responses') {
      const r = sec.responses || sec.leads_registered;
      if (!r) return;
      values = months.map(m => r[m] || 0);
    } else if (metric === 'activity') {
      // Sum all "sent/connection" type metrics for this channel
      values = months.map(m => {
        let v = 0;
        if (sec.connections_sent) v += sec.connections_sent[m] || 0;
        if (sec.messages_sent) v += sec.messages_sent[m] || 0;
        if (sec.first_emails_sent) v += sec.first_emails_sent[m] || 0;
        if (sec.follow_ups_sent) v += sec.follow_ups_sent[m] || 0;
        if (sec.emails_sent) v += sec.emails_sent[m] || 0;
        if (sec.leads_registered) v += sec.leads_registered[m] || 0;
        return v;
      });
    } else if (metric === 'emails_sent') {
      values = months.map(m => {
        let v = 0;
        if (sec.first_emails_sent) v += sec.first_emails_sent[m] || 0;
        if (sec.follow_ups_sent) v += sec.follow_ups_sent[m] || 0;
        if (sec.emails_sent) v += sec.emails_sent[m] || 0;
        if (sec.connections_sent) v += sec.connections_sent[m] || 0;
        if (sec.messages_sent) v += sec.messages_sent[m] || 0;
        return v;
      });
    } else if (metric === 'first_emails_only') {
      const d = sec.first_emails_sent || sec.emails_sent;
      if (!d) return;
      values = months.map(m => d[m] || 0);
    } else if (metric === 'follow_ups_only') {
      const d = sec.follow_ups_sent;
      if (!d) return;
      values = months.map(m => d[m] || 0);
    } else if (metric === 'opened') {
      const d = sec.opened;
      if (!d) return;
      values = months.map(m => d[m] || 0);
    } else if (metric === 'bounced') {
      const d = sec.bounced;
      if (!d) return;
      values = months.map(m => d[m] || 0);
    } else if (metric === 'connections_sent') {
      const d = sec.connections_sent;
      if (!d) return;
      values = months.map(m => d[m] || 0);
    } else if (metric === 'inbound_leads') {
      const d = sec.leads_registered;
      if (!d) return;
      values = months.map(m => d[m] || 0);
    }

    if (values && values.some(v => v > 0)) {
      datasets.push({
        label: GTM_CHANNEL_LABELS[key] || key,
        data: values,
        backgroundColor: colors.bg,
        borderColor: colors.border,
        borderWidth: 1,
        borderRadius: 3,
      });
    }
  });

  return { labels, datasets };
}

function updateGtmChart() {
  if (!_gtmData) return;
  const metric = document.getElementById('gtm-chart-metric').value;
  const stacked = document.getElementById('gtm-chart-stacked').checked;
  const chartData = _gtmChartDataForMetric(metric);

  if (_gtmChart) {
    _gtmChart.destroy();
    _gtmChart = null;
  }

  const canvas = document.getElementById('gtm-chart-canvas');
  if (!canvas) return;

  const ctx = canvas.getContext('2d');
  _gtmChart = new Chart(ctx, {
    type: 'bar',
    data: chartData,
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          position: 'top',
          labels: { usePointStyle: true, pointStyle: 'rectRounded', padding: 16, font: { size: 12 } },
        },
        tooltip: {
          callbacks: {
            footer: function(items) {
              if (!stacked || items.length < 2) return '';
              const total = items.reduce((s, i) => s + i.parsed.y, 0);
              return 'Total: ' + total;
            }
          }
        },
      },
      scales: {
        x: {
          stacked: stacked,
          grid: { display: false },
          ticks: { font: { size: 12 } },
        },
        y: {
          stacked: stacked,
          beginAtZero: true,
          ticks: { precision: 0, font: { size: 12 } },
          grid: { color: 'rgba(0,0,0,0.06)' },
        },
      },
    },
  });
}

function onGtmFilterChange() {
  loadGtmAnalytics();
}

function onGtmDateModeChange() {
  const mode = document.getElementById('gtm-date-mode').value;
  const rangeControls = document.getElementById('gtm-range-controls');
  if (mode === 'range') {
    rangeControls.classList.remove('hidden');
    rangeControls.style.display = 'inline-flex';
    // Set defaults
    if (!document.getElementById('gtm-start-date').value) {
      const now = new Date();
      const end = now.toISOString().split('T')[0];
      const start = new Date(now - 90 * 86400000).toISOString().split('T')[0];
      document.getElementById('gtm-start-date').value = start;
      document.getElementById('gtm-end-date').value = end;
    }
  } else {
    rangeControls.classList.add('hidden');
    rangeControls.style.display = 'none';
  }
  loadGtmAnalytics();
}

function setGtmRange(preset) {
  const now = new Date();
  const end = now.toISOString().split('T')[0];
  let days = 90;
  if (preset === '7d') days = 7;
  else if (preset === '30d') days = 30;
  const start = new Date(now - days * 86400000).toISOString().split('T')[0];
  document.getElementById('gtm-start-date').value = start;
  document.getElementById('gtm-end-date').value = end;
  loadGtmAnalytics();
}

// ── Manual entry modal ──────────────────────────────────────────────────

function openGtmManualEntry() {
  const now = new Date();
  document.getElementById('gtm-entry-month').value = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}`;
  document.getElementById('gtm-entry-value').value = '1';
  document.getElementById('gtm-entry-note').value = '';
  document.getElementById('gtm-entry-conf-name').value = '';
  onGtmEntryCategoryChange();
  const m = document.getElementById('gtm-manual-modal');
  m.classList.remove('hidden');
  m.style.display = 'flex';
}

function closeGtmManualModal() {
  const m = document.getElementById('gtm-manual-modal');
  m.classList.add('hidden');
  m.style.display = '';
}

function onGtmEntryCategoryChange() {
  const cat = document.getElementById('gtm-entry-category').value;
  const metricSel = document.getElementById('gtm-entry-metric');
  metricSel.innerHTML = '';
  (GTM_ENTRY_METRICS[cat] || []).forEach(([val, label]) => {
    const o = document.createElement('option');
    o.value = val; o.textContent = label;
    metricSel.appendChild(o);
  });
  // Show conference name field only for conference category
  const confRow = document.getElementById('gtm-entry-conf-row');
  if (cat === 'conference') confRow.classList.remove('hidden');
  else confRow.classList.add('hidden');
}

async function saveGtmManualEntry() {
  const month = document.getElementById('gtm-entry-month').value;
  const userId = document.getElementById('gtm-entry-user').value || null;
  const category = document.getElementById('gtm-entry-category').value;
  const metricKey = document.getElementById('gtm-entry-metric').value;
  const value = parseInt(document.getElementById('gtm-entry-value').value) || 0;
  const note = document.getElementById('gtm-entry-note').value || null;
  const confName = category === 'conference' ? (document.getElementById('gtm-entry-conf-name').value || null) : null;

  if (!month) { toast('Please select a month', 'error'); return; }

  try {
    await api('/gtm-analytics/entries', {
      method: 'POST',
      body: JSON.stringify({
        user_id: userId,
        category,
        metric_key: metricKey,
        month,
        value,
        note,
        source_ref: confName,
      }),
    });
    toast('Entry saved');
    closeGtmManualModal();
    loadGtmAnalytics();
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
}

async function loadGtmManualEntries() {
  const el = document.getElementById('gtm-manual-list');
  if (!el) return;
  try {
    const entries = await api('/gtm-analytics/entries');
    if (!entries.length) {
      el.innerHTML = '<div style="padding:8px 0;">No manual entries yet. Use the "+ Manual Entry" button to add one.</div>';
      return;
    }
    let html = `<table class="analytics-table" style="width:100%;font-size:13px;">
      <thead><tr><th style="text-align:left;padding:6px 10px;">Month</th><th style="text-align:left;padding:6px 10px;">Channel</th><th style="text-align:left;padding:6px 10px;">Metric</th><th style="text-align:right;padding:6px 10px;">Value</th><th style="text-align:left;padding:6px 10px;">Note</th><th style="text-align:left;padding:6px 10px;">Source</th><th style="padding:6px 10px;"></th></tr></thead><tbody>`;
    entries.forEach(e => {
      html += `<tr>
        <td style="padding:6px 10px;">${esc(e.month)}</td>
        <td style="padding:6px 10px;">${esc(e.category)}</td>
        <td style="padding:6px 10px;">${esc(e.metric_key)}</td>
        <td style="text-align:right;padding:6px 10px;">${e.value}</td>
        <td style="padding:6px 10px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(e.note || '')}</td>
        <td style="padding:6px 10px;">${esc(e.source_ref || '')}</td>
        <td style="padding:6px 10px;"><button class="btn btn-secondary btn-sm" onclick="deleteGtmEntry('${e.id}')" title="Delete this manual entry" style="font-size:11px;padding:2px 8px;">Del</button></td>
      </tr>`;
    });
    html += '</tbody></table>';
    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = `<div style="color:var(--red);">Failed to load entries: ${esc(e.message)}</div>`;
  }
}

async function deleteGtmEntry(id) {
  if (!await customConfirm('Delete this manual entry?', {destructive: true})) return;
  try {
    await api(`/gtm-analytics/entries/${id}`, { method: 'DELETE' });
    toast('Entry deleted');
    loadGtmAnalytics();
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
}

// ══════════════════════════════════════════════════════════════════════════
// FOLLOW-UPS MACHINE (MAXINE)
// ══════════════════════════════════════════════════════════════════════════

let _fuDeals = [];
let _fuTrelloBoards = [];
let _fuSortCol = 'days_since_activity';
let _fuSortAsc = false;
let _fuLastSyncAt = null;
let _fuConfigured = false;
let _fuOwnerMap = {}; // {hubspot_owner_id: "Display Name"}

const _STAGE_COLORS = {
  'Pre-Qualified Leads': '#6366f1',
  'Qualified Prospect': '#8b5cf6',
  'Discovery': '#3b82f6',
  'Tailored Demo': '#f59e0b',
  'Proposal': '#10b981',
};

function _fuDaysLabel(days) {
  if (days === null || days === undefined) return 'Never';
  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  return days + 'd ago';
}

async function loadFuConfig() {
  try {
    const config = await api('/gtm-analytics/followups/config');
    const hsBadge = document.getElementById('fu-hubspot-badge');
    const trBadge = document.getElementById('fu-trello-badge');
    if (config.hubspot_configured) {
      hsBadge.style.background = '#dcfce7'; hsBadge.style.color = '#166534';
      hsBadge.textContent = 'HubSpot';
    } else {
      hsBadge.style.background = '#fee2e2'; hsBadge.style.color = '#b91c1c';
      hsBadge.textContent = 'HubSpot: not set';
    }
    if (config.trello_configured) {
      trBadge.style.background = '#dcfce7'; trBadge.style.color = '#166534';
      trBadge.textContent = 'Trello';
    } else {
      trBadge.style.background = '#f3f4f6'; trBadge.style.color = '#6b7280';
      trBadge.textContent = 'Trello: optional';
    }

    _fuConfigured = !!(config.followups_active_stage_ids && config.followups_active_stage_ids.length);
    document.getElementById('fu-setup-banner').classList.toggle('hidden', _fuConfigured);

    // Owner name mapping
    const ownerMapRaw = config.followups_owner_map || '';
    const textarea = document.getElementById('fu-owner-map');
    if (textarea) textarea.value = ownerMapRaw;
    // Parse owner map
    _fuOwnerMap = {};
    ownerMapRaw.split('\n').forEach(line => {
      const [id, name] = (line || '').split('=');
      if (id && name) _fuOwnerMap[id.trim()] = name.trim();
    });

    if (config.hubspot_configured) {
      try {
        const pipelines = await api('/gtm-analytics/followups/hubspot-stages');
        const savedIds = config.followups_active_stage_ids || [];
        let html = '';
        pipelines.forEach(p => {
          html += `<div style="font-weight:600;margin-bottom:4px;margin-top:8px;">${esc(p.label)}</div>`;
          p.stages.forEach(s => {
            const checked = savedIds.includes(s.id) ? 'checked' : '';
            html += `<label style="display:block;padding:2px 0;cursor:pointer;"><input type="checkbox" class="fu-stage-cb" value="${s.id}" ${checked} /> ${esc(s.label)}</label>`;
          });
        });
        document.getElementById('fu-stages-list').innerHTML = html || 'No pipelines found';
      } catch(e) {
        document.getElementById('fu-stages-list').innerHTML = `<span style="color:var(--red);">${esc(e.message)}</span>`;
      }
    }

    if (config.trello_configured) {
      try {
        _fuTrelloBoards = await api('/gtm-analytics/followups/trello-boards');
        const boardSel = document.getElementById('fu-trello-board');
        boardSel.innerHTML = '<option value="">Select board...</option>';
        _fuTrelloBoards.forEach(b => {
          const o = document.createElement('option');
          o.value = b.id; o.textContent = b.name;
          if (b.id === config.followups_trello_board_id) o.selected = true;
          boardSel.appendChild(o);
        });
        if (config.followups_trello_board_id) {
          _populateFuLists(config.followups_trello_board_id, config.followups_trello_list_id);
        }
      } catch(e) { console.error('Trello boards:', e); }
    }
  } catch(e) { console.error('Config:', e); }
}

function _populateFuLists(boardId, selectedListId) {
  const listSel = document.getElementById('fu-trello-list');
  listSel.innerHTML = '<option value="">Select list...</option>';
  const board = _fuTrelloBoards.find(b => b.id === boardId);
  if (board && board.lists) {
    board.lists.forEach(l => {
      const o = document.createElement('option');
      o.value = l.id; o.textContent = l.name;
      if (l.id === selectedListId) o.selected = true;
      listSel.appendChild(o);
    });
  }
}

function onFuBoardChange() {
  _populateFuLists(document.getElementById('fu-trello-board').value, '');
}

async function saveFuConfig() {
  const stageIds = Array.from(document.querySelectorAll('.fu-stage-cb:checked')).map(cb => cb.value);
  const boardId = document.getElementById('fu-trello-board').value;
  const listId = document.getElementById('fu-trello-list').value;
  const ownerMapText = (document.getElementById('fu-owner-map').value || '').trim();
  try {
    await api('/gtm-analytics/followups/config', {
      method: 'PUT',
      body: JSON.stringify({ active_stage_ids: stageIds, trello_board_id: boardId, trello_list_id: listId, owner_map: ownerMapText }),
    });
    toast('Settings saved');
    _fuConfigured = stageIds.length > 0;
    document.getElementById('fu-setup-banner').classList.toggle('hidden', _fuConfigured);
    // Re-parse owner map
    _fuOwnerMap = {};
    ownerMapText.split('\n').forEach(line => {
      const [id, name] = (line || '').split('=');
      if (id && name) _fuOwnerMap[id.trim()] = name.trim();
    });
    renderFuTable();
  } catch(e) { toast('Failed: ' + e.message, 'error'); }
}

async function loadFuDeals() {
  const container = document.getElementById('fu-table-container');
  container.style.opacity = '0.5';
  try {
    _fuDeals = await api('/gtm-analytics/followups');
    _fuLastSyncAt = new Date();  // FIX-6: always update, even on empty results
    _updateFuLastSync();
    _populateFuFilters();
    renderFuTable();
  } catch(e) {
    container.innerHTML = `<div style="color:var(--red);padding:8px;">${esc(e.message)}</div>`;
  } finally {
    container.style.opacity = '1';
  }
}

function _fuResolveOwner(d) {
  if (d.owner_name) return d.owner_name;
  if (d.hubspot_owner_id && _fuOwnerMap[d.hubspot_owner_id]) return _fuOwnerMap[d.hubspot_owner_id];
  return '';
}

function _populateFuFilters() {
  // Owner filter
  const ownerSel = document.getElementById('fu-filter-owner');
  const curOwner = ownerSel.value;
  const owners = new Set();
  _fuDeals.forEach(d => { const o = _fuResolveOwner(d); if (o) owners.add(o); });
  ownerSel.innerHTML = '<option value="">All Owners</option>';
  [...owners].sort().forEach(o => {
    const opt = document.createElement('option');
    opt.value = o; opt.textContent = o;
    if (o === curOwner) opt.selected = true;
    ownerSel.appendChild(opt);
  });

}

function _updateFuLastSync() {
  const el = document.getElementById('fu-last-sync');
  if (!el) return;
  if (!_fuLastSyncAt) { el.textContent = ''; return; }
  const mins = Math.round((Date.now() - _fuLastSyncAt.getTime()) / 60000);
  el.textContent = mins < 1 ? 'Synced just now' : `Synced ${mins}m ago`;
}

function _fuSort(col) {
  if (_fuSortCol === col) { _fuSortAsc = !_fuSortAsc; }
  else { _fuSortCol = col; _fuSortAsc = col === 'deal_name'; }
  renderFuTable();
}

function renderFuTable() {
  const container = document.getElementById('fu-table-container');
  const summaryEl = document.getElementById('fu-summary');
  const search = (document.getElementById('fu-search').value || '').toLowerCase();
  let deals = _fuDeals;

  // Apply filters
  const filterOwner = document.getElementById('fu-filter-owner').value;
  const overdueOnly = document.getElementById('fu-overdue-only').checked;

  if (search) {
    deals = deals.filter(d =>
      (d.deal_name || '').toLowerCase().includes(search) ||
      (d.company_name || '').toLowerCase().includes(search) ||
      (_fuResolveOwner(d) || '').toLowerCase().includes(search) ||
      (d.contact_name || '').toLowerCase().includes(search) ||
      (d.suggested_next_step || '').toLowerCase().includes(search)
    );
  }
  if (filterOwner) deals = deals.filter(d => _fuResolveOwner(d) === filterOwner);
  if (overdueOnly) deals = deals.filter(d => d.is_overdue);

  // Summary
  const overdueCount = deals.filter(d => d.is_overdue).length;
  summaryEl.textContent = `${deals.length} deal${deals.length !== 1 ? 's' : ''}${overdueCount ? ` \u00b7 ${overdueCount} overdue` : ''}`;

  if (!deals.length) {
    container.innerHTML = '<div style="color:var(--text-muted);font-size:13px;padding:20px 0;text-align:center;">No deals found.</div>';
    return;
  }

  // Sort (FIX-8: owner sort uses resolved name, not raw field)
  deals = [...deals].sort((a, b) => {
    let va, vb;
    if (_fuSortCol === 'owner_name') {
      va = _fuResolveOwner(a); vb = _fuResolveOwner(b);
    } else {
      va = a[_fuSortCol]; vb = b[_fuSortCol];
    }
    if (va === null || va === undefined || va === '') va = _fuSortAsc ? '\uffff' : '';
    if (vb === null || vb === undefined || vb === '') vb = _fuSortAsc ? '\uffff' : '';
    if (typeof va === 'string') va = va.toLowerCase();
    if (typeof vb === 'string') vb = vb.toLowerCase();
    if (va < vb) return _fuSortAsc ? -1 : 1;
    if (va > vb) return _fuSortAsc ? 1 : -1;
    return 0;
  });

  const hasTrello = deals.some(d => d.trello_card_url);

  const sortIcon = (col) => _fuSortCol === col ? (_fuSortAsc ? ' \u25B2' : ' \u25BC') : '';
  const thStyle = 'padding:6px 10px;cursor:pointer;user-select:none;white-space:nowrap;';

  let html = `<table class="analytics-table" style="width:100%;font-size:13px;">
    <thead><tr>
      <th style="text-align:left;${thStyle}" onclick="_fuSort('deal_name')">Deal${sortIcon('deal_name')}</th>
      <th style="text-align:left;${thStyle}" onclick="_fuSort('company_name')">Company${sortIcon('company_name')}</th>
      <th style="text-align:left;${thStyle}" onclick="_fuSort('deal_stage')">Stage${sortIcon('deal_stage')}</th>
      <th style="text-align:left;${thStyle}" onclick="_fuSort('owner_name')">Owner${sortIcon('owner_name')}</th>
      <th style="text-align:left;${thStyle}" onclick="_fuSort('days_since_activity')">Last Activity${sortIcon('days_since_activity')}</th>
      <th style="text-align:left;${thStyle}">Next Step</th>
      ${hasTrello ? `<th style="text-align:left;padding:6px 10px;">Trello</th>` : ''}
      <th style="padding:6px 10px;width:80px;">Actions</th>
    </tr></thead><tbody>`;

  // FIX-1: Store deal data for delegated event handling (avoids XSS from inline onclick)
  window._fuDealMap = {};
  deals.forEach(d => { window._fuDealMap[d.id] = d; });

  deals.forEach((d, idx) => {
    const overdue = d.is_overdue;
    const daysLabel = _fuDaysLabel(d.days_since_activity);
    const daysStyle = overdue ? 'color:var(--red);font-weight:600;' : 'color:var(--text-muted);';
    const stageColor = _STAGE_COLORS[d.deal_stage] || '#6b7280';
    const trelloLink = d.trello_card_url
      ? `<a href="${d.trello_card_url}" target="_blank" rel="noopener" style="font-size:12px;">View</a>`
      : '';
    // FIX-3: correct HubSpot URL with portal ID
    const hubspotLink = d.hubspot_deal_id
      ? `<a href="https://app.hubspot.com/contacts/21866628/deal/${d.hubspot_deal_id}" target="_blank" rel="noopener" title="Open in HubSpot" style="font-size:11px;color:var(--text-muted);text-decoration:none;margin-left:4px;">\u2197</a>`
      : '';

    // FIX-5: snooze dropdown with options, available on ALL deals
    let actionHtml;
    if (d.snoozed_until) {
      actionHtml = `<button class="btn btn-secondary btn-sm fu-unsnooze-btn" data-deal-id="${d.id}" style="font-size:11px;padding:2px 8px;">Unsnooze</button>`;
    } else {
      actionHtml = `<select class="fu-snooze-sel" data-deal-id="${d.id}" style="font-size:11px;padding:2px 6px;border:1px solid var(--border-mid);border-radius:var(--r-sm);"><option value="">Snooze</option><option value="7">7d</option><option value="14">14d</option><option value="30">30d</option></select>`;
    }

    const snoozeBadge = d.snoozed_until ? `<span style="font-size:10px;background:#fef3c7;color:#92400e;padding:1px 6px;border-radius:99px;margin-left:6px;">snoozed</span>` : '';

    // FIX-10: no opacity; use left border for overdue instead
    const rowStyle = overdue ? 'border-left:3px solid var(--red);' : '';

    html += `<tr style="${rowStyle}">
      <td style="padding:6px 10px;font-weight:500;">${esc(d.deal_name || '')}${hubspotLink}${snoozeBadge}</td>
      <td style="padding:6px 10px;">${esc(d.company_name || '')}</td>
      <td style="padding:6px 10px;"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${stageColor};margin-right:6px;vertical-align:middle;"></span>${esc(d.deal_stage || '')}</td>
      <td style="padding:6px 10px;">${esc(_fuResolveOwner(d))}</td>
      <td style="padding:6px 10px;${daysStyle}">${daysLabel}</td>
      <td class="fu-next-step-cell" data-deal-id="${d.id}" style="padding:6px 10px;font-size:12px;color:var(--text-dim);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;${d.suggested_next_step ? 'cursor:pointer;' : ''}">${esc(d.suggested_next_step || '')}</td>
      ${hasTrello ? `<td style="padding:6px 10px;">${trelloLink}</td>` : ''}
      <td style="padding:6px 10px;text-align:center;">${actionHtml}</td>
    </tr>`;
  });
  html += '</tbody></table>';
  container.innerHTML = html;

  // FIX-1: Delegated event listeners (safe from XSS, no inline JS)
  container.querySelectorAll('.fu-next-step-cell').forEach(cell => {
    cell.addEventListener('click', function() {
      const deal = window._fuDealMap[this.dataset.dealId];
      if (deal && deal.suggested_next_step) showNextStep(this, deal.deal_name, deal.suggested_next_step);
    });
  });
  // FIX-5+7: Snooze select with double-click protection
  container.querySelectorAll('.fu-snooze-sel').forEach(sel => {
    sel.addEventListener('change', function() {
      const days = this.value;
      if (!days) return;
      this.disabled = true;
      snoozeDeal(this.dataset.dealId, days).finally(() => { this.disabled = false; });
      this.selectedIndex = 0;
    });
  });
  container.querySelectorAll('.fu-unsnooze-btn').forEach(btn => {
    btn.addEventListener('click', function() {
      this.disabled = true;
      unsnoozeDeal(this.dataset.dealId).finally(() => { this.disabled = false; });
    });
  });
}

async function scanFollowups(btn) {
  btn.disabled = true; btn.textContent = 'Scanning...';
  document.getElementById('fu-table-container').style.opacity = '0.5';
  try {
    const result = await api('/gtm-analytics/followups/scan', { method: 'POST' });
    toast(`${result.deals_synced} deals synced, ${result.newly_overdue} newly overdue`);
    _fuLastSyncAt = new Date();
    _updateFuLastSync();
    loadFuDeals();
  } catch(e) {
    toast('Scan failed: ' + e.message, 'error');
  } finally {
    btn.disabled = false; btn.textContent = 'Scan Now';
    document.getElementById('fu-table-container').style.opacity = '1';
  }
}

async function snoozeDeal(id, days) {
  if (!days) return;
  try {
    await api(`/gtm-analytics/followups/${id}/snooze?days=${days}`, { method: 'POST' });
    toast(`Snoozed ${days}d`);
    loadFuDeals();
  } catch(e) { toast('Failed: ' + e.message, 'error'); }
}

async function unsnoozeDeal(id) {
  try {
    await api(`/gtm-analytics/followups/${id}/unsnooze`, { method: 'POST' });
    toast('Unsnoozed');
    loadFuDeals();
  } catch(e) { toast('Failed: ' + e.message, 'error'); }
}

function showNextStep(el, dealName, text) {
  const old = document.getElementById('next-step-popover');
  if (old) { old.remove(); return; }

  const rect = el.getBoundingClientRect();
  const spaceBelow = window.innerHeight - rect.bottom;
  const pop = document.createElement('div');
  pop.id = 'next-step-popover';
  pop.style.cssText = `position:fixed;z-index:9999;background:var(--navy,#1e293b);color:#fff;padding:12px 16px;border-radius:8px;font-size:13px;max-width:320px;box-shadow:0 4px 20px rgba(0,0,0,0.3);line-height:1.5;`;
  // Position above if not enough space below
  if (spaceBelow < 100) {
    pop.style.bottom = (window.innerHeight - rect.top + 8) + 'px';
  } else {
    pop.style.top = (rect.bottom + 8) + 'px';
  }
  pop.style.left = Math.min(rect.left, window.innerWidth - 340) + 'px';
  pop.innerHTML = `<div style="font-weight:600;margin-bottom:6px;font-size:12px;opacity:0.7;">${dealName}</div><div>${text}</div>`;
  document.body.appendChild(pop);

  const close = (e) => { if (!pop.contains(e.target) && e.target !== el) { pop.remove(); document.removeEventListener('click', close); } };
  setTimeout(() => document.addEventListener('click', close), 10);
}

// Start auth check (shows login screen or boots app)
checkAuth();
