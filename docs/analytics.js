// Page-view counting for the public site. Off until a GoatCounter code is set below.
//
// GoatCounter (https://www.goatcounter.com, free for non-commercial sites) sets no
// cookies and keeps no personal data. To turn counting on, create a site there and put
// its code (the "hephaestus" of hephaestus.goatcounter.com) in GOATCOUNTER_CODE. With
// the code empty this file does nothing. Visitors who send Do Not Track or Global
// Privacy Control are never counted, and neither are local copies of the site.
const GOATCOUNTER_CODE = '';

function analyticsAllowed(code, nav, loc) {
    if (!/^[a-z0-9][a-z0-9-]{0,62}$/.test(String(code || ''))) return false;
    if (!loc || loc.protocol !== 'https:' || /^(?:localhost|127\.0\.0\.1|\[::1\])$/.test(loc.hostname)) return false;
    const doNotTrack = nav && (nav.doNotTrack === '1' || nav.doNotTrack === 'yes' || nav.msDoNotTrack === '1');
    return !(doNotTrack || (nav && nav.globalPrivacyControl === true));
}

// The dashboard routes with the hash ("#company?ticker=NVDA"), so each view counts as
// its own page; which companies people open is the useful number.
function analyticsPath(loc) {
    const hash = String(loc.hash || '');
    return `${loc.pathname}${/^#[a-z]/i.test(hash) ? hash.slice(0, 120) : ''}`;
}

function startAnalytics(win, doc) {
    if (!analyticsAllowed(GOATCOUNTER_CODE, win.navigator, win.location)) return false;
    win.goatcounter = { no_onload: true };
    const script = doc.createElement('script');
    script.async = true;
    script.src = 'https://gc.zgo.at/count.js';
    script.setAttribute('data-goatcounter', `https://${GOATCOUNTER_CODE}.goatcounter.com/count`);
    script.addEventListener('load', () => {
        const count = () => win.goatcounter && typeof win.goatcounter.count === 'function'
            && win.goatcounter.count({ path: analyticsPath(win.location) });
        count();
        win.addEventListener('hashchange', count);
    });
    doc.head.appendChild(script);
    return true;
}

if (typeof window !== 'undefined' && typeof document !== 'undefined' && document.head) {
    startAnalytics(window, document);
}
