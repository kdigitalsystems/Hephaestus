const assert = require('assert');
const { sanitizeReviewDecisions, unsupportedAiDecision } = require('../scripts/sanitize_review_decisions');


const unsupported = {
    source_url: 'AI Multi-Source Research',
    evidence_excerpt: 'Not found in source text',
    review_status: 'approved',
};
const supported = {
    source_url: 'AI Multi-Source Research',
    evidence_excerpt: 'Supplier provides verified control modules to Customer.',
    review_status: 'approved',
};
const manual = {
    source_url: 'Manual System Jumpstart',
    evidence_excerpt: null,
    review_status: 'approved',
};
const urlWithManualInPath = {
    source_url: 'https://vendor.example.com/docs/service-manual.pdf',
    evidence_excerpt: '',
    review_status: 'approved',
};
const boundary = {
    source_url: 'AI Multi-Source Research',
    evidence_excerpt: 'exactly nineteen ch',
    review_status: 'approved',
};
const payload = { decisions: [unsupported, supported, manual, urlWithManualInPath, boundary] };

assert.strictEqual(unsupportedAiDecision(unsupported), true);
assert.strictEqual(unsupportedAiDecision(supported), false);
assert.strictEqual(unsupportedAiDecision(manual), false);
assert.strictEqual(unsupportedAiDecision(urlWithManualInPath), true);
assert.strictEqual(unsupportedAiDecision(boundary), true);
assert.strictEqual(unsupportedAiDecision({ source_url: 'AI Multi-Source Research', evidence_excerpt: 'exactly twenty chars' }), false);
assert.strictEqual(sanitizeReviewDecisions(payload, '2026-08-25T00:00:00Z'), 3);
assert.strictEqual(unsupported.review_status, 'rejected');
assert.strictEqual(unsupported.reviewed_at, '2026-08-25T00:00:00Z');
assert.strictEqual(supported.review_status, 'approved');
assert.strictEqual(manual.review_status, 'approved');
assert.strictEqual(urlWithManualInPath.review_status, 'rejected');
