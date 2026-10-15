'use strict';

// Review queue for links the pipeline held for a person. Decisions stay in this
// browser (localStorage) until they are sent to GitHub as a file under
// data/human_review/, which backend/apply_human_review.py applies on the next run.

const REVIEW_REPO = 'kdigitalsystems/Hephaestus';
const REVIEW_BRANCH = 'main';
const REVIEW_STORAGE_KEY = 'hephaestus_review_v1';
// GitHub's new-file editor takes the content in the URL. Measured: URLs past ~6,900
// characters fail with a 500 or a 414, so links stay under 6,000; a larger session is
// uploaded as one file instead (or split, as a fallback).
const MAX_PR_URL_LENGTH = 6000;
const ACTIONS = ['approve', 'reverse', 'reject'];
const ACTION_LABEL = { approve: 'Approve', reverse: 'Reverse', reject: 'Reject' };

// --- Pure helpers (exercised by tests/check_review_page.js) ---------------------------

const pad = (value) => String(value).padStart(2, '0');

function reviewStamp(now) {
    return `${now.getUTCFullYear()}${pad(now.getUTCMonth() + 1)}${pad(now.getUTCDate())}-${pad(now.getUTCHours())}${pad(now.getUTCMinutes())}${pad(now.getUTCSeconds())}`;
}

function reviewedOn(now) {
    return `${now.getUTCFullYear()}-${pad(now.getUTCMonth() + 1)}-${pad(now.getUTCDate())}`;
}

// One decision in the shape backend/apply_human_review.py validates.
function decisionRecord(item, decision) {
    const record = {
        edge_id: Number(item.edge_id),
        source_ticker: String(item.source_ticker || ''),
        target_ticker: String(item.target_ticker || ''),
        type: String(item.type || ''),
        action: decision.action,
    };
    const note = String(decision.note || '').trim().slice(0, 500);
    if (note) record.note = note;
    return record;
}

function buildDecisionFile(records, now) {
    return {
        reviewed_on: reviewedOn(now),
        decisions: [...records].sort((a, b) => a.edge_id - b.edge_id),
    };
}

// Compact, one decision per line: short enough for a link, readable in the PR diff.
function decisionFileText(file) {
    const lines = file.decisions.map(decision => JSON.stringify(decision));
    return `{"reviewed_on":${JSON.stringify(file.reviewed_on)},"decisions":[\n${lines.join(',\n')}\n]}\n`;
}

function uploadUrl(repo = REVIEW_REPO, branch = REVIEW_BRANCH) {
    return `https://github.com/${repo}/upload/${branch}/data/human_review`;
}

function newFileUrl(path, content, repo = REVIEW_REPO, branch = REVIEW_BRANCH) {
    return `https://github.com/${repo}/new/${branch}?filename=${encodeURIComponent(path)}&value=${encodeURIComponent(content)}`;
}

// Split the records into as few files as fit in a GitHub URL each.
function pullRequestLinks(records, now, repo = REVIEW_REPO, maxLength = MAX_PR_URL_LENGTH) {
    const stamp = reviewStamp(now);
    const sorted = [...records].sort((a, b) => a.edge_id - b.edge_id);
    const chunks = [];
    let current = [];
    const urlFor = (chunk, index) => {
        const path = `data/human_review/review-${stamp}${index ? `-${index + 1}` : ''}.json`;
        const content = decisionFileText(buildDecisionFile(chunk, now));
        return { path, content, url: newFileUrl(path, content, repo), count: chunk.length };
    };
    sorted.forEach(record => {
        const candidate = [...current, record];
        if (current.length && urlFor(candidate, chunks.length).url.length > maxLength) {
            chunks.push(current);
            current = [record];
        } else {
            current = candidate;
        }
    });
    if (current.length) chunks.push(current);
    return chunks.map((chunk, index) => urlFor(chunk, index));
}

// The dashboard's own queue (used before review_queue.json exists) has no categories.
function fallbackCategory(note) {
    const text = String(note || '');
    if (/does not name both companies/i.test(text)) return 'unnamed';
    if (/consensus was insufficient/i.test(text)) return 'split_vote';
    if (/opposite direction/i.test(text)) return 'two_way';
    if (!text.startsWith('Ollama consensus review')) return 'awaiting_models';
    return 'other';
}

function normalizeQueue(payload) {
    if (payload && Array.isArray(payload.items)) {
        return { items: payload.items, categories: payload.categories || {}, generatedAt: payload.generated_at || null,
            pendingCount: payload.pending_count ?? payload.items.length, truncated: Boolean(payload.truncated), withSuggestions: true };
    }
    const queue = (payload && payload.quality && Array.isArray(payload.quality.review_queue)) ? payload.quality.review_queue : [];
    return {
        items: queue.map(item => ({ ...item, category: fallbackCategory(item.review_note), suggestion: null, mirror_edge_ids: [] })),
        categories: {
            unnamed: 'Excerpt does not name both companies', split_vote: 'The models disagreed', two_way: 'Published in both directions',
            awaiting_models: 'Not reviewed by the models yet', other: 'Other',
        },
        generatedAt: payload && payload.generated_at ? payload.generated_at : null,
        pendingCount: payload && payload.quality ? payload.quality.pending_count ?? queue.length : queue.length,
        truncated: Boolean(payload && payload.quality && payload.quality.pending_count > queue.length),
        withSuggestions: false,
    };
}

function matchesSearch(item, query) {
    const needle = String(query || '').trim().toLowerCase();
    if (!needle) return true;
    return [item.source_ticker, item.source_name, item.target_ticker, item.target_name]
        .some(value => String(value || '').toLowerCase().includes(needle));
}

// Largest companies first: their pages are the ones people open, so their links are
// worth clearing first. A link's size is its larger endpoint's market cap.
const REVIEW_ORDERS = ['size', 'oldest', 'newest'];

function linkSize(item) {
    const caps = [item.source_market_cap, item.target_market_cap].filter(value => typeof value === 'number' && value > 0);
    return caps.length ? Math.max(...caps) : 0;
}

function sortItems(items, order) {
    const byId = (a, b) => Number(a.edge_id) - Number(b.edge_id);
    const compare = {
        size: (a, b) => linkSize(b) - linkSize(a) || byId(a, b),
        oldest: byId,
        newest: (a, b) => byId(b, a),
    }[order] || byId;
    return [...items].sort(compare);
}

function formatMarketCap(value) {
    if (typeof value !== 'number' || !(value > 0)) return '';
    const [scale, suffix] = value >= 1e12 ? [1e12, 'T'] : value >= 1e9 ? [1e9, 'B'] : [1e6, 'M'];
    const scaled = value / scale;
    return `$${scaled >= 100 ? Math.round(scaled) : scaled.toFixed(1).replace(/\.0$/, '')}${suffix}`;
}

const isHttpUrl = (value) => /^https?:\/\//i.test(String(value || ''));

// Edge ids are reused after a database rebuild, so a saved decision also remembers
// which link it was about and is dropped if the id now names a different one.
const edgeSignature = (item) => `${item.source_ticker}->${item.target_ticker}:${item.type}`;

// Saved decisions that no longer describe a waiting link. Only the full queue can say so:
// the dashboard's fallback copy is shorter (250 of ~480 links), and a link missing from it
// is not shown, not settled. A truncated full queue is the same case for the links it cut.
function staleDecisionIds(decisions, byId, queue) {
    if (queue.fallback) return [];
    return Object.keys(decisions).filter(edgeId => {
        const item = byId.get(Number(edgeId));
        if (!item) return !queue.truncated;
        return decisions[edgeId].signature !== edgeSignature(item);
    });
}

// Tabs share one storage key, so saving this tab's whole copy would erase what another tab
// saved since this one loaded. Only this tab's changes (against what it last read or wrote)
// are applied on top of what is stored now.
const sameValue = (a, b) => JSON.stringify(a) === JSON.stringify(b);

function mergeChanges(base, mine, theirs) {
    const merged = { ...theirs };
    Object.keys(mine).forEach(key => { if (!sameValue(mine[key], base[key])) merged[key] = mine[key]; });
    Object.keys(base).forEach(key => { if (!(key in mine)) delete merged[key]; });
    return merged;
}

// --- State -------------------------------------------------------------------------

const reviewState = {
    queue: null,
    byId: new Map(),
    decisions: {},
    sent: {},
    category: 'all',
    order: 'size',
    query: '',
    hideDecided: false,
    focusId: null,
};

function loadStored() {
    try {
        const stored = JSON.parse(localStorage.getItem(REVIEW_STORAGE_KEY) || '{}');
        return { decisions: stored.decisions || {}, sent: stored.sent || {} };
    } catch (_error) {
        return { decisions: {}, sent: {} };
    }
}

let savedState = { decisions: {}, sent: {} };

const snapshotOf = (state) => JSON.parse(JSON.stringify({ decisions: state.decisions, sent: state.sent }));

function saveStored() {
    try {
        const current = loadStored();
        const decisions = mergeChanges(savedState.decisions, reviewState.decisions, current.decisions);
        const sent = mergeChanges(savedState.sent, reviewState.sent, current.sent);
        localStorage.setItem(REVIEW_STORAGE_KEY, JSON.stringify({ decisions, sent }));
        reviewState.decisions = decisions;
        reviewState.sent = sent;
        savedState = snapshotOf(reviewState);
    } catch (_error) {
        // Private windows can refuse storage; decisions still work for this visit.
    }
}

// Another tab saved: show its decisions here now, rather than at this tab's next save.
function adoptStored() {
    const stored = loadStored();
    reviewState.decisions = stored.decisions;
    reviewState.sent = stored.sent;
    savedState = snapshotOf(reviewState);
}

function records() {
    return Object.entries(reviewState.decisions)
        .filter(([edgeId]) => reviewState.byId.has(Number(edgeId)))
        .map(([edgeId, decision]) => decisionRecord(reviewState.byId.get(Number(edgeId)), decision));
}

// --- Rendering ---------------------------------------------------------------------

const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
};

function visibleItems() {
    return sortItems(reviewState.queue.items.filter(item =>
        (reviewState.category === 'all' || item.category === reviewState.category)
        && matchesSearch(item, reviewState.query)
        && !(reviewState.hideDecided && reviewState.decisions[item.edge_id])
    ), reviewState.order);
}

// Listing names carry the security type ("Apple Inc. Common Stock"); the company is enough.
const SECURITY_SUFFIX = /\s+(?:Common Stock|Common Shares|Ordinary Shares|Class [A-C]\b.*|American Depositary Shares.*|Amer\. Dep\. Shares.*|ADS\b.*|New York Registry Shares.*|Depositary Shares.*)$/i;
const displayName = (name) => String(name || '').replace(SECURITY_SUFFIX, '').trim();

function companyLabel(name, ticker) {
    const fragment = document.createDocumentFragment();
    fragment.appendChild(el('span', 'rq-company-name', displayName(name) || ticker));
    fragment.appendChild(el('span', 'rq-ticker', ticker));
    return fragment;
}

function renderFilters() {
    const container = document.getElementById('review-filters');
    container.replaceChildren();
    const counts = {};
    reviewState.queue.items.forEach(item => { counts[item.category] = (counts[item.category] || 0) + 1; });
    const entries = [['all', 'All', reviewState.queue.items.length],
        ...Object.keys(counts).sort((a, b) => counts[b] - counts[a]).map(key => [key, reviewState.queue.categories[key] || key, counts[key]])];
    entries.forEach(([key, label, count]) => {
        const chip = el('button', 'rq-chip');
        chip.type = 'button';
        chip.setAttribute('aria-pressed', String(reviewState.category === key));
        chip.appendChild(el('span', '', label));
        chip.appendChild(el('span', 'rq-chip-count', String(count)));
        chip.addEventListener('click', () => { reviewState.category = key; render(); });
        container.appendChild(chip);
    });
}

function renderCard(item) {
    const decision = reviewState.decisions[item.edge_id];
    const card = el('li', `rq-card${decision ? ` decided decided-${decision.action}` : ''}`);
    card.id = `edge-${item.edge_id}`;
    card.tabIndex = -1;
    card.dataset.edgeId = String(item.edge_id);

    const top = el('div', 'rq-card-top');
    top.appendChild(el('span', 'rq-category', reviewState.queue.categories[item.category] || item.category));
    if (item.suggestion) {
        top.appendChild(el('span', `rq-suggestion suggest-${item.suggestion.action}`, `Suggested: ${ACTION_LABEL[item.suggestion.action]}`));
    }
    const size = formatMarketCap(linkSize(item));
    if (size) top.appendChild(el('span', 'rq-size', `Larger company ${size}`));
    top.appendChild(el('span', 'rq-edge-id', `#${item.edge_id}`));
    card.appendChild(top);

    const title = el('h2', 'rq-title');
    title.appendChild(companyLabel(item.source_name, item.source_ticker));
    title.appendChild(el('span', 'rq-arrow', ' supplies '));
    title.appendChild(companyLabel(item.target_name, item.target_ticker));
    card.appendChild(title);

    const meta = el('p', 'rq-meta');
    const parts = [item.type, item.product && item.product !== item.type ? item.product : null,
        item.revenue_share ? `${item.revenue_share}% of ${item.source_ticker} revenue` : null,
        typeof item.confidence === 'number' ? `${Math.round(item.confidence * 100)}% confidence` : null].filter(Boolean);
    meta.appendChild(document.createTextNode(parts.join(' · ')));
    if (isHttpUrl(item.source_url)) {
        meta.appendChild(document.createTextNode(' · '));
        const link = el('a', 'source-link', item.source_title || 'Source');
        link.href = item.source_url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        meta.appendChild(link);
    }
    card.appendChild(meta);

    card.appendChild(el('blockquote', 'rq-evidence', item.evidence_excerpt || 'No evidence excerpt was saved.'));
    if (item.suggestion && item.suggestion.reason) card.appendChild(el('p', 'rq-suggestion-reason', item.suggestion.reason));
    if (item.review_note) {
        const why = el('details', 'rq-why');
        why.appendChild(el('summary', '', 'Why it is waiting'));
        why.appendChild(el('p', '', item.review_note));
        card.appendChild(why);
    }
    const mirrors = (item.mirror_edge_ids || []).filter(id => id !== item.edge_id);
    if (mirrors.length) {
        const mirror = el('p', 'rq-mirror');
        mirror.appendChild(document.createTextNode('Also linked the other way: '));
        mirrors.forEach((id, index) => {
            if (index) mirror.appendChild(document.createTextNode(', '));
            if (reviewState.byId.has(id)) {
                const jump = el('a', '', `#${id}`);
                jump.href = `#edge-${id}`;
                mirror.appendChild(jump);
            } else {
                mirror.appendChild(el('span', '', `#${id} (published)`));
            }
        });
        card.appendChild(mirror);
    }

    const actions = el('div', 'rq-actions');
    const labels = {
        approve: `Approve ${item.source_ticker} → ${item.target_ticker}`,
        reverse: `Reverse to ${item.target_ticker} → ${item.source_ticker}`,
        reject: 'Reject',
    };
    ACTIONS.forEach(action => {
        const button = el('button', `rq-action action-${action}${item.suggestion && item.suggestion.action === action ? ' suggested' : ''}`, labels[action]);
        button.type = 'button';
        button.setAttribute('aria-pressed', String(Boolean(decision && decision.action === action)));
        button.addEventListener('click', () => decide(item.edge_id, decision && decision.action === action ? null : action));
        actions.appendChild(button);
    });
    card.appendChild(actions);

    if (decision) {
        const outcome = { approve: `Will approve ${item.source_ticker} → ${item.target_ticker}`,
            reverse: `Will publish ${item.target_ticker} → ${item.source_ticker}`, reject: 'Will reject' }[decision.action];
        const status = el('p', 'rq-decision', reviewState.sent[item.edge_id] ? `${outcome} · sent to GitHub` : outcome);
        card.appendChild(status);
        const note = el('input', 'rq-note');
        note.type = 'text';
        note.maxLength = 500;
        note.placeholder = 'Optional note for the record';
        note.value = decision.note || '';
        note.setAttribute('aria-label', `Note for #${item.edge_id}`);
        // Saving can replace the stored objects with another tab's, so look the decision up again.
        note.addEventListener('change', () => {
            const current = reviewState.decisions[item.edge_id];
            if (!current) return;
            current.note = note.value;
            saveStored();
            renderSubmit();
        });
        card.appendChild(note);
    }
    card.addEventListener('focusin', () => { reviewState.focusId = Number(item.edge_id); });
    return card;
}

function renderList() {
    const list = document.getElementById('review-list');
    list.replaceChildren();
    const items = visibleItems();
    items.forEach(item => list.appendChild(renderCard(item)));
    document.getElementById('review-empty').hidden = items.length > 0;
    // A filter can take the focused card off the page; shortcuts must not keep acting on it.
    if (!items.some(item => Number(item.edge_id) === reviewState.focusId)) reviewState.focusId = null;
    const suggestible = items.filter(item => item.suggestion && !reviewState.decisions[item.edge_id]);
    const accept = document.getElementById('review-accept-suggestions');
    accept.disabled = suggestible.length === 0;
    accept.textContent = suggestible.length ? `Accept ${suggestible.length} suggestion${suggestible.length === 1 ? '' : 's'}` : 'Accept suggestions';
}

function renderSubmit() {
    const all = records();
    const panel = document.getElementById('review-submit');
    panel.hidden = all.length === 0;
    const tally = ACTIONS.map(action => [action, all.filter(record => record.action === action).length]).filter(([, count]) => count);
    document.getElementById('review-submit-summary').textContent =
        `${all.length} decision${all.length === 1 ? '' : 's'}: ${tally.map(([action, count]) => `${count} ${action}`).join(', ')}`;
}

function renderStatus() {
    const queue = reviewState.queue;
    const decided = records().length;
    const when = queue.generatedAt ? ` as of ${new Date(queue.generatedAt).toISOString().slice(0, 16).replace('T', ' ')} UTC` : '';
    let text = `${queue.items.length} link${queue.items.length === 1 ? '' : 's'} waiting${when}; ${decided} decided here.`;
    if (queue.truncated) text += ` Showing the first ${queue.items.length} of ${queue.pendingCount}.`;
    if (!queue.withSuggestions) text += ' Suggestions appear after the next pipeline run.';
    document.getElementById('review-status').textContent = text;

    const warning = document.getElementById('review-warning');
    warning.hidden = !queue.fallback;
    if (queue.fallback) {
        const unseen = Object.keys(reviewState.decisions).filter(edgeId => !reviewState.byId.has(Number(edgeId))).length;
        warning.textContent = `The full review queue could not be loaded (${queue.fallbackReason}), so this is the shorter list from the dashboard: ${queue.items.length} of ${queue.pendingCount} links.`
            + (unseen ? ` ${unseen} saved decision${unseen === 1 ? '' : 's'} for links not in this list ${unseen === 1 ? 'is' : 'are'} kept, and come back when the full queue loads.` : '')
            + ' Reload to try again.';
    }
}

function render() {
    renderFilters();
    renderList();
    renderSubmit();
    renderStatus();
}

function renderKeepingScroll() {
    const scrollY = window.scrollY;
    render();
    window.scrollTo(0, scrollY);
}

// --- Actions -----------------------------------------------------------------------

function decide(edgeId, action) {
    if (action) {
        reviewState.decisions[edgeId] = { ...(reviewState.decisions[edgeId] || {}), action, signature: edgeSignature(reviewState.byId.get(Number(edgeId))), decided_at: new Date().toISOString() };
    } else {
        delete reviewState.decisions[edgeId];
    }
    delete reviewState.sent[edgeId];
    saveStored();
    renderKeepingScroll();
    const card = document.getElementById(`edge-${edgeId}`);
    if (card) card.focus({ preventScroll: true });
}

function acceptSuggestions() {
    const items = visibleItems().filter(item => item.suggestion && !reviewState.decisions[item.edge_id]);
    if (!items.length) return;
    const tally = ACTIONS.map(action => [action, items.filter(item => item.suggestion.action === action).length]).filter(([, count]) => count);
    const summary = tally.map(([action, count]) => `${count} ${action}`).join(', ');
    if (!window.confirm(`Accept ${items.length} suggestion${items.length === 1 ? '' : 's'} in this view (${summary})? You can still change any of them.`)) return;
    const now = new Date().toISOString();
    items.forEach(item => { reviewState.decisions[item.edge_id] = { action: item.suggestion.action, signature: edgeSignature(item), decided_at: now }; });
    saveStored();
    render();
}

function openPullRequest() {
    const all = records();
    if (!all.length) return;
    const links = pullRequestLinks(all, new Date());
    const container = document.getElementById('review-pr-links');
    container.replaceChildren();
    if (links.length === 1) {
        window.open(links[0].url, '_blank', 'noopener');
        container.appendChild(el('p', '', 'GitHub opened in a new tab. Choose "Create a new branch for this commit and start a pull request", then merge it.'));
    } else {
        downloadFile();
        window.open(uploadUrl(), '_blank', 'noopener');
        container.appendChild(el('p', '',
            'Too many for one link: the file was downloaded and GitHub\'s upload page opened. Drop the file there, ' +
            'then choose "Create a new branch … and start a pull request".'));
        const alternative = el('details', '');
        alternative.appendChild(el('summary', '', `Or open ${links.length} smaller pull requests`));
        const list = el('ol', '');
        links.forEach((link, index) => {
            const row = el('li', '');
            const anchor = el('a', 'source-link', `Part ${index + 1}: ${link.count} decision${link.count === 1 ? '' : 's'}`);
            anchor.href = link.url;
            anchor.target = '_blank';
            anchor.rel = 'noopener';
            row.appendChild(anchor);
            list.appendChild(row);
        });
        alternative.appendChild(list);
        container.appendChild(alternative);
    }
    container.hidden = false;
    const sentAt = new Date().toISOString();
    all.forEach(record => { reviewState.sent[record.edge_id] = sentAt; });
    saveStored();
    renderList();
}

function sessionFileText() {
    return decisionFileText(buildDecisionFile(records(), new Date()));
}

function downloadFile() {
    const blob = new Blob([sessionFileText()], { type: 'application/json' });
    const link = el('a');
    link.href = URL.createObjectURL(blob);
    link.download = `rq-${reviewStamp(new Date())}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

async function copyFile() {
    const button = document.getElementById('review-copy');
    try {
        await navigator.clipboard.writeText(sessionFileText());
        button.textContent = 'Copied';
    } catch (_error) {
        button.textContent = 'Copy failed';
    }
    setTimeout(() => { button.textContent = 'Copy'; }, 1600);
}

function clearAll() {
    if (!window.confirm('Clear every decision in this browser? Decisions already sent to GitHub are not affected.')) return;
    reviewState.decisions = {};
    reviewState.sent = {};
    saveStored();
    document.getElementById('review-pr-links').hidden = true;
    render();
}

// --- Keyboard ----------------------------------------------------------------------

function moveFocus(step) {
    const cards = [...document.querySelectorAll('.rq-card')];
    if (!cards.length) return;
    const index = cards.findIndex(card => Number(card.dataset.edgeId) === reviewState.focusId);
    const next = cards[Math.max(0, Math.min(cards.length - 1, index < 0 ? 0 : index + step))];
    reviewState.focusId = Number(next.dataset.edgeId);
    next.focus();
    next.scrollIntoView({ block: 'nearest' });
}

function handleKey(event) {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const tag = document.activeElement && document.activeElement.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    const key = event.key.toLowerCase();
    if (key === 'j' || key === 'arrowdown') { event.preventDefault(); moveFocus(1); return; }
    if (key === 'k' || key === 'arrowup') { event.preventDefault(); moveFocus(-1); return; }
    // Only a card that is on the page: a filter may have hidden the one focused last.
    const item = reviewState.byId.get(reviewState.focusId);
    if (!item || !document.getElementById(`edge-${item.edge_id}`)) return;
    const action = { a: 'approve', r: 'reverse', x: 'reject' }[key];
    if (action) { event.preventDefault(); decide(item.edge_id, action); return; }
    if (key === 's' && item.suggestion) { event.preventDefault(); decide(item.edge_id, item.suggestion.action); return; }
    if (key === 'u') { event.preventDefault(); decide(item.edge_id, null); }
}

// --- Start -------------------------------------------------------------------------

async function fetchQueue() {
    const readJson = (response) => (response.ok ? response.json() : Promise.reject(new Error(`HTTP ${response.status}`)));
    try {
        const payload = await fetch('review_queue.json', { cache: 'no-store' }).then(readJson);
        // A body without a list of items is not a queue; read as an empty one it would drop every saved decision.
        if (!payload || !Array.isArray(payload.items)) throw new Error('unexpected content');
        return normalizeQueue(payload);
    } catch (error) {
        // Before the first run that publishes review_queue.json, or when it cannot be read,
        // use the dashboard's copy, which holds the first 250 links.
        const queue = normalizeQueue(await fetch('dashboard_lite.json').then(readJson));
        queue.fallback = true;
        queue.fallbackReason = error.message;
        return queue;
    }
}

async function startReviewPage() {
    adoptStored();
    try {
        reviewState.queue = await fetchQueue();
    } catch (error) {
        document.getElementById('review-status').textContent = `The review queue could not be loaded (${error.message}).`;
        return;
    }
    reviewState.byId = new Map(reviewState.queue.items.map(item => [Number(item.edge_id), item]));
    // Decisions for links that are no longer waiting have been applied (or settled
    // some other way); drop them so they are never sent twice.
    const stale = staleDecisionIds(reviewState.decisions, reviewState.byId, reviewState.queue);
    stale.forEach(edgeId => {
        delete reviewState.decisions[edgeId];
        delete reviewState.sent[edgeId];
    });
    if (stale.length) saveStored();

    // Browsers restore form controls on Back and reload; the controls must show the state the list is in.
    const search = document.getElementById('review-search');
    search.value = reviewState.query;
    search.addEventListener('input', event => { reviewState.query = event.target.value; renderList(); });
    const hideDecided = document.getElementById('review-hide-decided');
    hideDecided.checked = reviewState.hideDecided;
    hideDecided.addEventListener('change', event => { reviewState.hideDecided = event.target.checked; renderList(); });
    const order = document.getElementById('review-order');
    order.value = reviewState.order;
    order.addEventListener('change', event => {
        reviewState.order = REVIEW_ORDERS.includes(event.target.value) ? event.target.value : 'size';
        renderList();
    });
    document.getElementById('review-accept-suggestions').addEventListener('click', acceptSuggestions);
    document.getElementById('review-open-pr').addEventListener('click', openPullRequest);
    document.getElementById('review-download').addEventListener('click', downloadFile);
    document.getElementById('review-copy').addEventListener('click', copyFile);
    document.getElementById('review-clear').addEventListener('click', clearAll);
    const shortcutsToggle = document.getElementById('review-shortcuts-toggle');
    shortcutsToggle.addEventListener('click', () => {
        const panel = document.getElementById('review-shortcuts');
        panel.hidden = !panel.hidden;
        shortcutsToggle.setAttribute('aria-expanded', String(!panel.hidden));
    });
    document.addEventListener('keydown', handleKey);
    window.addEventListener('storage', event => {
        if (event.key !== null && event.key !== REVIEW_STORAGE_KEY) return;
        adoptStored();
        // A note being typed would be wiped by a re-render; the next action redraws it.
        if (document.activeElement && document.activeElement.className === 'rq-note') renderSubmit();
        else renderKeepingScroll();
    });
    render();
}

if (typeof document !== 'undefined' && document.getElementById && document.getElementById('review-app')) {
    startReviewPage();
}
