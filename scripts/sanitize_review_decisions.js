const fs = require('fs');
const path = require('path');


// Mirrors EVIDENCE_PLACEHOLDERS in backend/evidence_quality.py.
const PLACEHOLDERS = [
    'not found in source text',
    'no evidence',
    'not explicitly stated',
    'not explicitly mentioned',
    'not directly stated',
    'not directly mentioned',
    'not mentioned in the text',
    'not stated in the text',
    'no direct mention',
    'no specific mention',
    'source text does not',
    'the text does not',
    'the provided text',
    'based on the provided',
    'the text states',
    'the text mentions',
    'could not find',
    'unable to find',
    'well-known supplier',
    'well known supplier',
    'well-known customer',
    'well known customer',
    'is known to supply',
    'general knowledge',
];


// Mirrors backend/evidence_quality.py: only curated manual provenance labels are
// exempt; a URL that merely contains the word "manual" is still AI-derived evidence.
function requiresSourceEvidence(source) {
    const label = String(source || '').trim().toLowerCase();
    if (label.startsWith('http://') || label.startsWith('https://')) return true;
    return !label.includes('manual');
}


function unsupportedAiDecision(decision) {
    const evidence = String(decision.evidence_excerpt || '').replace(/\s+/g, ' ').trim();
    return requiresSourceEvidence(decision.source_url) && (
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
    const args = process.argv.slice(2);
    const checkOnly = args.includes('--check');
    const target = path.resolve(args.find(arg => !arg.startsWith('--')) || path.join(__dirname, '..', 'data', 'edge_review_decisions.json'));
    const payload = JSON.parse(fs.readFileSync(target, 'utf8'));
    const changed = sanitizeReviewDecisions(payload);
    if (checkOnly) {
        // The pipeline writes this file with Python's JSON formatting; comparing
        // bytes after a JavaScript rewrite would fail on formatting alone.
        if (changed) {
            console.error(`${changed} approved decision(s) in ${target} lack usable evidence; run scripts/sanitize_review_decisions.js.`);
            process.exit(1);
        }
        console.log(`All approved decisions in ${target} carry usable evidence.`);
    } else {
        fs.writeFileSync(target, `${JSON.stringify(payload, null, 2)}\n`);
        console.log(`Rejected ${changed} unsupported AI approval(s) in ${target}.`);
    }
}


module.exports = { sanitizeReviewDecisions, unsupportedAiDecision, requiresSourceEvidence };
