const fs = require('fs');
const path = require('path');


const PLACEHOLDERS = [
    'not found in source text',
    'no evidence',
    'not explicitly stated',
    'source text does not',
    'could not find',
    'unable to find',
];


function unsupportedAiDecision(decision) {
    const source = String(decision.source_url || '').toLowerCase();
    const evidence = String(decision.evidence_excerpt || '').replace(/\s+/g, ' ').trim();
    return !source.includes('manual') && (
        evidence.length < 20 || PLACEHOLDERS.some(value => evidence.toLowerCase().includes(value))
    );
}


function sanitizeReviewDecisions(payload, reviewedAt = new Date().toISOString()) {
    let changed = 0;
    for (const decision of payload.decisions || []) {
        if (decision.review_status !== 'approved' || !unsupportedAiDecision(decision)) continue;
        decision.review_status = 'rejected';
        decision.review_note = 'Automated evidence cleanup: AI-derived relationship has no usable source excerpt.';
        decision.reviewed_at = reviewedAt;
        changed += 1;
    }
    return changed;
}


if (require.main === module) {
    const target = path.resolve(process.argv[2] || path.join(__dirname, '..', 'data', 'edge_review_decisions.json'));
    const payload = JSON.parse(fs.readFileSync(target, 'utf8'));
    const changed = sanitizeReviewDecisions(payload);
    fs.writeFileSync(target, `${JSON.stringify(payload, null, 2)}\n`);
    console.log(`Rejected ${changed} unsupported AI approval(s) in ${target}.`);
}


module.exports = { sanitizeReviewDecisions, unsupportedAiDecision };
