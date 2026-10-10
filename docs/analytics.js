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
// its own page; which companies people open is the useful number. Only the view and the
// few parameters below are sent, and only when they look like what they should be: the
// search box and the exposure and compare inputs also write to the hash, and what a
// visitor types there must never reach a third party.
const ANALYTICS_VIEWS = ['overview', 'companies', 'company', 'predictions', 'compare', 'exposure', 'watchlist', 'sector'];
const ANALYTICS_PARAMS = [
    ['ticker', /^[a-z0-9][a-z0-9.-]{0,9}$/i, true],
    ['a', /^[a-z0-9][a-z0-9.-]{0,9}$/i, true],
    ['b', /^[a-z0-9][a-z0-9.-]{0,9}$/i, true],
    ['sector', /^[a-z][a-z &,-]{0,39}$/i, false],
];

function analyticsPath(loc) {
    const match = String(loc.hash || '').match(/^#([a-z]+)(?:\?(.*))?$/i);
    const view = match ? match[1].toLowerCase() : '';
    // The bare address and #overview are one page.
    if (!ANALYTICS_VIEWS.includes(view) || view === 'overview') return loc.pathname;
    const params = new URLSearchParams(match[2] || '');
    const kept = ANALYTICS_PARAMS
        .map(([key, shape, upper]) => [key, params.get(key), shape, upper])
        .filter(([, value, shape]) => value && shape.test(value))
        .map(([key, value, , upper]) => `${key}=${encodeURIComponent(upper ? value.toUpperCase() : value)}`);
    return `${loc.pathname}#${view}${kept.length ? `?${kept.join('&')}` : ''}`;
}

function startAnalytics(win, doc) {
    if (!analyticsAllowed(GOATCOUNTER_CODE, win.navigator, win.location)) return false;
    win.goatcounter = { no_onload: true };
    const script = doc.createElement('script');
    script.async = true;
    script.src = 'https://gc.zgo.at/count.js';
    script.setAttribute('data-goatcounter', `https://${GOATCOUNTER_CODE}.goatcounter.com/count`);
    script.addEventListener('load', () => {
        // Back/Forward fire both hashchange and the app's own route event, and the app also
        // announces the landing page it routed to; a view counts once until it changes.
        let lastPath = null;
        const count = () => {
            if (!win.goatcounter || typeof win.goatcounter.count !== 'function') return;
            const path = analyticsPath(win.location);
            if (path === lastPath) return;
            lastPath = path;
            win.goatcounter.count({ path });
        };
        count();
        win.addEventListener('hashchange', count);
        // history.pushState, which the app navigates with, fires no hashchange of its own.
        win.addEventListener('hephaestus:route', count);
    });
    doc.head.appendChild(script);
    return true;
}

if (typeof window !== 'undefined' && typeof document !== 'undefined' && document.head) {
    startAnalytics(window, document);
}
