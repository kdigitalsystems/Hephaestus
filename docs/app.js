let globalData = {};
let dashboardMeta = {};
let predictionData = { predictions: [], calibration: {} };
let allCompanies = [];
let currentCompaniesList = [];
let currentRoute = { view: 'overview' };
let sortCol = 'market_cap';
let sortAsc = false;
let watchlist = new Set();
let searchInputTimer = null;
let chartRenderToken = 0;
const LIVE_SEARCH_MIN_CHARS = 3;
const PREFIX_SEARCH_MIN_CHARS = 2;

function readStoredValue(key, fallback) {
    try {
        return localStorage.getItem(key) || fallback;
    } catch (_error) {
        return fallback;
    }
}

function writeStoredValue(key, value) {
    try {
        localStorage.setItem(key, value);
    } catch (_error) {
        // Storage may be unavailable in tests or strict privacy modes.
    }
}

let currentTheme = readStoredValue('hephaestus_theme', 'dark');

const clearElement = (element) => {
    while (element.firstChild) element.removeChild(element.firstChild);
};

const makeElement = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
};

const setText = (id, value) => {
    const element = document.getElementById(id);
    if (element) element.textContent = value;
};

const formatNum = (num, type = 'currency') => {
    if (num === null || num === undefined || isNaN(num)) return "N/A";
    const value = Number(num);
    if (type === 'percent') return (value * 100).toFixed(2) + '%';
    if (type === 'ratio') return value.toFixed(2);
    if (value >= 1e12) return '$' + (value / 1e12).toFixed(2) + 'T';
    if (value >= 1e9) return '$' + (value / 1e9).toFixed(2) + 'B';
    if (value >= 1e6) return '$' + (value / 1e6).toFixed(2) + 'M';
    return '$' + value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

const displayCompanyName = (name) => {
    let value = String(name || '').replace(/\s+/g, ' ').trim();
    [
        /\s+(Class\s+[A-Z]\s+|New\s+)?Common Stock$/i,
        /\s+Ordinary Shares.*$/i,
        /\s+American Depositary Shares.*$/i,
        /\s+Depositary Shares.*$/i,
        /\s+Warrants.*$/i,
    ].forEach(pattern => {
        value = value.replace(pattern, '').trim();
    });
    return value || String(name || '').trim() || 'Unknown';
};

const sentenceEntityName = (name) => displayCompanyName(name).replace(/\.+$/, '');

const relationshipCount = (company) => (company.upstream?.length || 0) + (company.downstream?.length || 0);
const companyMetrics = (company) => company.investor_metrics || {
    upstream_count: company.upstream?.length || 0,
    downstream_count: company.downstream?.length || 0,
    total_links: relationshipCount(company),
    approved_count: 0,
    pending_count: 0,
    rejected_count: 0,
    manual_count: 0,
    web_source_count: 0,
    ai_research_count: 0,
    average_confidence: null,
    concentration_score: 0,
    top_upstream: [],
    top_downstream: [],
    last_verified: 'N/A',
    risk_score: 0,
    supplier_risk: 0,
    customer_risk: 0,
    confidence_score: 0,
    review_score: 0,
    freshness_score: 0
};

const relationshipStatus = (relationship) => {
    const tokens = String(relationship.review_status || 'pending')
        .toLowerCase()
        .split(/[\/,]/)
        .map(token => token.trim());
    if (tokens.includes('approved')) return 'approved';
    if (tokens.includes('rejected')) return 'rejected';
    return 'pending';
};

const findExactTicker = (query) => {
    const normalized = String(query || '').trim().toUpperCase();
    if (!normalized) return null;
    return allCompanies.find(company => String(company.ticker || '').toUpperCase() === normalized) || null;
};

function loadWatchlist() {
    try {
        watchlist = new Set(JSON.parse(localStorage.getItem('hephaestus_watchlist') || '[]'));
    } catch (_error) {
        watchlist = new Set();
    }
}

function saveWatchlist() {
    writeStoredValue('hephaestus_watchlist', JSON.stringify([...watchlist].sort()));
}

function applyTheme(theme) {
    currentTheme = theme === 'light' ? 'light' : 'dark';
    document.documentElement.dataset.theme = currentTheme;
    writeStoredValue('hephaestus_theme', currentTheme);

    const label = document.getElementById('theme-toggle-label');
    const button = document.getElementById('theme-toggle');
    if (label) label.textContent = currentTheme === 'dark' ? 'Dark' : 'Light';
    if (button) {
        const nextTheme = currentTheme === 'dark' ? 'light' : 'dark';
        button.setAttribute('aria-label', `Switch to ${nextTheme} theme`);
        button.title = `Switch to ${nextTheme} theme`;
    }
}

function toggleTheme() {
    applyTheme(currentTheme === 'dark' ? 'light' : 'dark');
}

if (document.addEventListener) {
    document.addEventListener('keydown', event => {
        if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey) return;
        const activeTag = document.activeElement?.tagName;
        if (activeTag === 'INPUT' || activeTag === 'TEXTAREA' || activeTag === 'SELECT') return;
        const input = document.getElementById('search-input');
        if (!input || document.getElementById('view-industries')?.classList.contains('hidden')) return;
        event.preventDefault();
        input.focus();
    });
}

const hydrateCompanies = (data) => {
    allCompanies = Object.entries(data).flatMap(([sector, companies]) =>
        companies.map(company => ({
            ...company,
            sector,
            connection_count: relationshipCount(company)
        }))
    );
};

fetch('dashboard_data.json')
    .then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
    })
    .then(data => {
        globalData = data.industries || {};
        dashboardMeta = data;
        loadWatchlist();
        applyTheme(currentTheme);
        hydrateCompanies(globalData);
        populateFilters();
        renderOverview();
        setText('last-updated', getLastUpdatedLabel());
        applyRouteFromHash(false);
    })
    .catch(error => {
        console.error("Data load failed:", error);
        setText('last-updated', 'Failed to load data');
    });

fetch('predictions.json')
    .then(response => response.ok ? response.json() : Promise.reject(new Error(`HTTP ${response.status}`)))
    .then(data => {
        predictionData = data || { predictions: [], calibration: {} };
        if (currentRoute.view === 'predictions') renderPredictionsView();
    })
    .catch(error => console.warn('Prediction data load failed:', error));

function getLastUpdatedLabel() {
    const dates = allCompanies
        .map(company => company.last_updated)
        .filter(value => value && value !== 'N/A')
        .sort()
        .reverse();
    return dates.length ? `Data synced ${dates[0]}` : 'Live Data Synced';
}

function resetDashboard() {
    document.getElementById('search-input').value = '';
    document.getElementById('sector-filter').value = '';
    document.getElementById('dependency-filter').value = '';
    document.getElementById('connected-filter').checked = false;
    currentCompaniesList = [];
    renderLevel1();
}

function navigateOverview() {
    setRoute({ view: 'overview' });
}

function navigateQuality() {
    setRoute({ view: 'quality' });
}

function navigateWatchlist() {
    setRoute({ view: 'watchlist' });
}

function navigatePredictions() {
    setRoute({ view: 'predictions' });
}

function navigateCompare(route = {}, push = true) {
    setRoute({ view: 'compare', ...route }, push);
}

function navigateSector(sector) {
    setRoute({ view: 'sector', sector });
}

function navigateCompanies(route = {}, push = true) {
    setRoute({ view: 'companies', ...route }, push);
}

function navigateCompany(ticker) {
    if (!ticker) return;
    setRoute({ view: 'company', ticker, previous: currentRoute });
}

function navigateBackToCompanies() {
    if (currentRoute.previous && ['companies', 'sector', 'compare', 'watchlist', 'predictions'].includes(currentRoute.previous.view)) {
        setRoute(currentRoute.previous);
        return;
    }
    setRoute({ view: 'companies' });
}

function routeToHash(route) {
    const params = new URLSearchParams();
    Object.entries(route).forEach(([key, value]) => {
        if (key === 'view' || key === 'previous') return;
        if (value !== undefined && value !== null && value !== '') params.set(key, value);
    });
    const query = params.toString();
    return query ? `#${route.view}?${query}` : `#${route.view}`;
}

function hashToRoute(hash) {
    const cleaned = (hash || '#overview').replace(/^#/, '');
    const [view = 'overview', query = ''] = cleaned.split('?');
    const params = new URLSearchParams(query);
    const route = { view };
    params.forEach((value, key) => {
        route[key] = value;
    });
    return route;
}

function setRoute(route, push = true) {
    const hash = routeToHash(route);
    if (push && window.location.hash !== hash) {
        history.pushState(null, '', hash);
    } else if (!push && window.location.hash !== hash) {
        history.replaceState(null, '', hash);
    } else if (!window.location.hash) {
        history.replaceState(null, '', hash);
    }
    applyRoute(route);
}

function updateRouteHash(route, push = true) {
    const hash = routeToHash(route);
    if (push && window.location.hash !== hash) {
        history.pushState(null, '', hash);
    } else if (!push && window.location.hash !== hash) {
        history.replaceState(null, '', hash);
    }
    currentRoute = route;
    updateActiveNav(route);
}

function applyRouteFromHash(push = false) {
    const route = hashToRoute(window.location.hash);
    if (!window.location.hash) {
        setRoute({ view: 'overview' }, push);
        return;
    }
    applyRoute(route);
}

function applyRoute(route) {
    currentRoute = route;
    updateActiveNav(route);

    if (route.view === 'company') {
        const company = getCompanyByTicker(route.ticker);
        if (company) {
            renderLevel3(company, route.previous || null);
            return;
        }
        renderLevel1();
        return;
    }

    if (route.view === 'companies') {
        document.getElementById('search-input').value = route.query || '';
        document.getElementById('sector-filter').value = route.sector || '';
        document.getElementById('dependency-filter').value = route.dependency || '';
        document.getElementById('connected-filter').checked = route.connected === '1';
        applyFilters(false, true);
        return;
    }

    if (route.view === 'quality') {
        setRoute({ view: 'overview' }, false);
        return;
    }

    if (route.view === 'watchlist') {
        renderWatchlistView();
        return;
    }

    if (route.view === 'predictions') {
        renderPredictionsView();
        return;
    }

    if (route.view === 'compare') {
        document.getElementById('compare-a').value = route.a || '';
        document.getElementById('compare-b').value = route.b || '';
        renderCompareView(false);
        return;
    }

    if (route.view === 'sector') {
        renderSectorView(route.sector || '');
        return;
    }

    resetDashboard();
}

function updateActiveNav(route) {
    const activeView = route.view === 'company' || route.view === 'sector' ? 'companies' : route.view;
    document.querySelectorAll('[data-nav]').forEach(button => {
        button.classList.toggle('active', button.dataset.nav === activeView);
    });
}

window.addEventListener('popstate', () => applyRouteFromHash(false));
window.addEventListener('hashchange', () => applyRouteFromHash(false));

function showCompanies() {
    document.getElementById('view-industries').classList.add('hidden');
    document.getElementById('view-quality').classList.add('hidden');
    document.getElementById('view-watchlist').classList.add('hidden');
    document.getElementById('view-predictions').classList.add('hidden');
    document.getElementById('view-compare').classList.add('hidden');
    document.getElementById('view-sector').classList.add('hidden');
    document.getElementById('view-companies').classList.remove('hidden');
    document.getElementById('view-details').classList.add('hidden');
}

function renderOverview() {
    renderLevel1();
}

function renderOverviewStats() {
    const container = document.getElementById('overview-stats');
    if (!container) return;
    clearElement(container);
    const linkedCompanies = allCompanies.filter(company => relationshipCount(company) > 0).length;
    const supplyLinks = Number(dashboardMeta.investor_metrics?.unique_links || 0);
    const sectors = Object.keys(globalData).filter(key => Array.isArray(globalData[key])).length;
    const topSector = Object.entries(globalData)
        .filter(([, companies]) => Array.isArray(companies))
        .map(([sector, companies]) => ({
            sector,
            links: companies.reduce((sum, company) => sum + relationshipCount(company), 0)
        }))
        .sort((a, b) => b.links - a.links)[0];
    [
        ['Companies', allCompanies.length.toLocaleString()],
        ['Supply links', supplyLinks.toLocaleString()],
        ['Linked names', linkedCompanies.toLocaleString()],
        ['Top sector', topSector ? topSector.sector : `${sectors} sectors`],
    ].forEach(([label, value]) => {
        const item = makeElement('div', 'overview-stat');
        item.appendChild(makeElement('strong', '', value));
        item.appendChild(makeElement('span', '', label));
        container.appendChild(item);
    });
    renderQuickActions();
}

function renderQuickActions() {
    const container = document.getElementById('quick-actions');
    if (!container) return;
    clearElement(container);
    const mostConnected = dashboardMeta.investor_metrics?.most_connected || [];
    const topTickers = mostConnected
        .map(company => company.ticker)
        .filter(Boolean)
        .slice(0, 4);
    if (!topTickers.length) return;

    const label = makeElement('span', 'quick-actions-label', 'Open top briefs');
    container.appendChild(label);
    topTickers.forEach(ticker => {
        const button = makeElement('button', 'quick-action-chip', ticker);
        button.type = 'button';
        button.title = `Open ${ticker} company brief`;
        button.onclick = () => navigateCompany(ticker);
        container.appendChild(button);
    });
}

function populateFilters() {
    const sectorFilter = document.getElementById('sector-filter');
    const dependencyFilter = document.getElementById('dependency-filter');

    Object.keys(globalData).sort().forEach(sector => {
        const option = makeElement('option', '', sector);
        option.value = sector;
        sectorFilter.appendChild(option);
    });

    const dependencyTypes = new Set();
    allCompanies.forEach(company => {
        [...(company.upstream || []), ...(company.downstream || [])].forEach(dep => {
            if (dep.type) dependencyTypes.add(dep.type);
        });
    });

    [...dependencyTypes].sort().forEach(type => {
        const option = makeElement('option', '', type);
        option.value = type;
        dependencyFilter.appendChild(option);
    });
}

function renderLevel1() {
    window.scrollTo({ top: 0, behavior: 'smooth' });
    document.getElementById('view-industries').classList.remove('hidden');
    document.getElementById('view-quality').classList.add('hidden');
    document.getElementById('view-watchlist').classList.add('hidden');
    document.getElementById('view-predictions').classList.add('hidden');
    document.getElementById('view-compare').classList.add('hidden');
    document.getElementById('view-sector').classList.add('hidden');
    document.getElementById('view-companies').classList.add('hidden');
    document.getElementById('view-details').classList.add('hidden');

    renderOverviewStats();
    const grid = document.getElementById('industry-grid');
    clearElement(grid);

    Object.keys(globalData).sort().forEach(sector => {
        const companies = globalData[sector].map(company => ({
            ...company,
            sector,
            connection_count: relationshipCount(company)
        }));
        const links = companies.reduce((sum, company) => sum + company.connection_count, 0);
        const card = makeElement('button', 'card sector-card');
        card.onclick = () => {
            navigateSector(sector);
        };
        card.appendChild(makeElement('span', 'sector-name', sector));
        card.appendChild(makeElement('span', 'sector-meta', `${companies.length} equities - ${links} links`));
        grid.appendChild(card);
    });
}

function handleSearchInput() {
    window.clearTimeout(searchInputTimer);
    updateSearchHelper(true);
    searchInputTimer = window.setTimeout(() => applyFilters(true, false), 250);
}

function handleSearchKeydown(event) {
    if (event.key !== 'Enter') return;
    if (event.preventDefault) event.preventDefault();
    commitSearch();
}

function commitSearch() {
    window.clearTimeout(searchInputTimer);
    applyFilters(true, true, true);
}

function clearFilters() {
    window.clearTimeout(searchInputTimer);
    document.getElementById('search-input').value = '';
    document.getElementById('sector-filter').value = '';
    document.getElementById('dependency-filter').value = '';
    document.getElementById('connected-filter').checked = false;
    navigateCompanies({}, false);
}

function clearSearchInput() {
    const input = document.getElementById('search-input');
    input.value = '';
    input.focus();
    window.clearTimeout(searchInputTimer);
    applyFilters(true);
}

function updateSearchHelper(searchPending = false) {
    const helper = document.getElementById('search-helper');
    const input = document.getElementById('search-input');
    if (!helper || !input) return;
    const rawQuery = input.value.trim();
    if (!rawQuery) {
        helper.textContent = 'Search ticker, company, supplier, customer, product, or sector.';
        return;
    }
    const exactTicker = findExactTicker(rawQuery);
    if (exactTicker) {
        helper.textContent = `Exact ticker match: ${exactTicker.ticker}. Press Enter or Search to open the company brief.`;
        return;
    }
    if (rawQuery.length < LIVE_SEARCH_MIN_CHARS) {
        helper.textContent = `Keep typing, or press Enter to search ticker prefixes such as "${rawQuery.toUpperCase()}".`;
        return;
    }
    if (searchPending) {
        helper.textContent = `Searching for "${rawQuery}"...`;
        return;
    }
    const count = currentCompaniesList.length;
    helper.textContent = `${count} result${count === 1 ? '' : 's'} match "${rawQuery}".`;
}

function applyFilters(updateRoute = true, committedSearch = false, openExactTicker = false) {
    const query = document.getElementById('search-input').value.toLowerCase().trim();
    const sector = document.getElementById('sector-filter').value;
    const dependency = document.getElementById('dependency-filter').value;
    const onlyConnected = document.getElementById('connected-filter').checked;
    const hasStructuredFilter = Boolean(sector || dependency || onlyConnected);
    const exactTicker = !sector && !dependency && !onlyConnected ? findExactTicker(query) : null;
    const tickerPrefixSearch = !hasStructuredFilter && committedSearch && query.length >= PREFIX_SEARCH_MIN_CHARS &&
        allCompanies.some(company => (company.ticker || '').toLowerCase().startsWith(query));

    if (exactTicker && updateRoute && openExactTicker) {
        navigateCompany(exactTicker.ticker);
        return;
    }

    if (query && query.length < LIVE_SEARCH_MIN_CHARS && !tickerPrefixSearch && !hasStructuredFilter) {
        currentCompaniesList = [];
        if (currentRoute.view !== 'overview') {
            if (updateRoute) updateRouteHash({ view: 'overview' }, false);
            else currentRoute = { view: 'overview' };
            renderLevel1();
        }
        updateSearchHelper();
        return;
    }

    let results = allCompanies.filter(company => {
        const deps = [...(company.upstream || []), ...(company.downstream || [])];
        const counterpartyText = deps.map(dep => `${dep.name || ''} ${dep.ticker || ''} ${dep.product || ''} ${dep.type || ''}`).join(' ').toLowerCase();
        const matchesQuery = exactTicker ? String(company.ticker || '').toUpperCase() === exactTicker.ticker :
            tickerPrefixSearch ? (company.ticker || '').toLowerCase().startsWith(query) : !query ||
            (company.name || '').toLowerCase().includes(query) ||
            (company.ticker || '').toLowerCase().includes(query) ||
            (company.sector || '').toLowerCase().includes(query) ||
            (company.industry || '').toLowerCase().includes(query) ||
            counterpartyText.includes(query);
        const matchesSector = !sector || company.sector === sector;
        const matchesDependency = !dependency || deps.some(dep => dep.type === dependency);
        const matchesConnected = !onlyConnected || company.connection_count > 0;
        return matchesQuery && matchesSector && matchesDependency && matchesConnected;
    });

    currentCompaniesList = results;
    setText('current-industry-title', query ? `Search Results (${results.length})` : sector || 'All Companies');
    renderLevel2();

    if (updateRoute) {
        const shouldPush = currentRoute.view !== 'companies';
        updateRouteHash({
            view: 'companies',
            query,
            sector,
            dependency,
            connected: onlyConnected ? '1' : ''
        }, shouldPush);
    }
}

function handleSort(col) {
    if (sortCol === col) {
        sortAsc = !sortAsc;
    } else {
        sortCol = col;
        sortAsc = (col === 'name' || col === 'ticker');
    }
    renderLevel2();
}

function renderLevel2() {
    showCompanies();
    window.scrollTo({ top: 0, behavior: 'smooth' });

    document.querySelectorAll('.sort-icon').forEach(icon => icon.textContent = '');
    setText(`sort-${sortCol}`, sortAsc ? '^' : 'v');
    setText('result-count', `${currentCompaniesList.length} results`);
    updateSearchHelper();

    const companies = [...currentCompaniesList];
    companies.sort((a, b) => {
        let valA = a[sortCol];
        let valB = b[sortCol];

        if (valA === null || valA === undefined || valA === "N/A") valA = sortAsc ? Infinity : -Infinity;
        if (valB === null || valB === undefined || valB === "N/A") valB = sortAsc ? Infinity : -Infinity;

        if (typeof valA === 'string') {
            return sortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
        }
        return sortAsc ? valA - valB : valB - valA;
    });

    const tbody = document.getElementById('company-table-body');
    clearElement(tbody);

    if (!companies.length) {
        const tr = document.createElement('tr');
        const td = makeElement('td', 'table-empty', 'No companies match the current filters.');
        td.colSpan = 7;
        tr.appendChild(td);
        tbody.appendChild(tr);
        return;
    }

    companies.forEach(company => {
        tbody.appendChild(renderCompanyTableRow(company));
    });
}

function renderCompanyTableRow(company) {
    const tr = document.createElement('tr');
    tr.onclick = () => navigateCompany(company.ticker);

    const changeClass = company.change >= 0 ? 'positive' : 'negative';
    const sign = company.change > 0 ? '+' : '';
    const nameCell = makeElement('td', 'company-cell');
    const nameWrap = makeElement('div', 'company-name-wrap');
    nameWrap.appendChild(makeElement('strong', '', displayCompanyName(company.name)));
    nameWrap.appendChild(makeElement('span', '', company.industry || company.sector || 'Uncategorized'));
    nameCell.appendChild(nameWrap);
    nameCell.title = company.name || '';

    tr.appendChild(nameCell);
    const tickerCell = makeElement('td', '');
    tickerCell.appendChild(makeElement('span', 'table-ticker', company.ticker || ''));
    tr.appendChild(tickerCell);
    tr.appendChild(makeElement('td', '', `$${(company.price || 0).toFixed(2)}`));
    tr.appendChild(makeElement('td', changeClass, `${sign}${(company.change || 0).toFixed(2)}%`));
    tr.appendChild(makeElement('td', '', formatNum(company.market_cap)));
    const linkCell = makeElement('td', '');
    linkCell.appendChild(makeElement('span', company.connection_count ? 'link-count-pill active' : 'link-count-pill', String(company.connection_count || 0)));
    tr.appendChild(linkCell);
    tr.appendChild(makeElement('td', '', formatNum(company.trailing_pe, 'ratio')));
    return tr;
}

const getCompanyByTicker = (ticker) => {
    const normalized = String(ticker || '').trim().toUpperCase();
    return allCompanies.find(company => String(company.ticker || '').toUpperCase() === normalized) || null;
};

function renderLevel3(company, previousRoute = null) {
    currentRoute = {
        view: 'company',
        ticker: company.ticker,
        previous: previousRoute || { view: 'companies' }
    };
    window.scrollTo({ top: 0, behavior: 'smooth' });
    document.getElementById('view-industries').classList.add('hidden');
    document.getElementById('view-quality').classList.add('hidden');
    document.getElementById('view-companies').classList.add('hidden');
    document.getElementById('view-details').classList.remove('hidden');
    document.getElementById('view-watchlist').classList.add('hidden');
    document.getElementById('view-predictions').classList.add('hidden');
    document.getElementById('view-compare').classList.add('hidden');
    document.getElementById('view-sector').classList.add('hidden');

    setText('detail-name', displayCompanyName(company.name));
    setText('detail-ticker', company.ticker || 'N/A');
    setText('detail-industry', company.industry || company.sector || 'Uncategorized');
    updateDetailBackLabel(currentRoute.previous);

    renderStory(company);
    renderChart(company);
    renderDecisionBrief(company);
    renderMetrics(company);
    renderSupplyGraph(company);
    renderXRay(company);
}

function updateDetailBackLabel(previousRoute) {
    const button = document.getElementById('detail-back-button');
    if (!button) return;
    const labels = {
        companies: 'Back to screener',
        sector: 'Back to sector',
        compare: 'Back to compare',
        watchlist: 'Back to watchlist',
    };
    button.textContent = labels[previousRoute?.view] || 'Back to screener';
}

function renderStory(company) {
    const upstream = company.upstream || [];
    const downstream = company.downstream || [];
    const primarySupplier = upstream[0]?.name;
    const primaryCustomer = downstream[0]?.name;
    const metrics = companyMetrics(company);

    setText('detail-story-title', `${company.ticker || company.name} dependency snapshot`);

    let story = `${displayCompanyName(company.name)} has ${upstream.length} tracked upstream supplier relationship${upstream.length === 1 ? '' : 's'} and ${downstream.length} tracked downstream customer relationship${downstream.length === 1 ? '' : 's'}.`;
    if (primarySupplier) story += ` Its upstream exposure includes ${sentenceEntityName(primarySupplier)}.`;
    if (primaryCustomer) story += ` Its downstream exposure includes ${sentenceEntityName(primaryCustomer)}.`;
    if (!upstream.length && !downstream.length) story += ' This company is a good candidate for the next discovery pass.';
    setText('detail-story', story);
}

function renderChart(company) {
    const chartContainer = document.getElementById('tv_chart_container');
    const renderToken = ++chartRenderToken;
    if (!chartContainer) return;
    clearElement(chartContainer);
    chartContainer.classList.add('chart-loading');
    const fallback = makeElement('div', 'chart-unavailable', 'Loading market chart...');
    chartContainer.appendChild(fallback);

    const markChartUnavailable = () => {
        if (renderToken !== chartRenderToken) return;
        if (!chartContainer.querySelector('iframe')) {
            fallback.textContent = 'Market chart unavailable for this entity';
            chartContainer.classList.remove('chart-loading');
        }
    };

    if (company.ticker && company.ticker !== "N/A" && window.TradingView) {
        new window.TradingView.widget({
            autosize: true,
            symbol: company.ticker,
            timezone: "America/New_York",
            theme: "dark",
            style: "3",
            locale: "en",
            enable_publishing: false,
            backgroundColor: "#151a20",
            gridColor: "#242c35",
            hide_top_toolbar: true,
            hide_legend: true,
            save_image: false,
            container_id: "tv_chart_container",
            allow_symbol_change: false,
            range: "60M",
        });

        let checks = 0;
        const chartReadyCheck = window.setInterval(() => {
            if (renderToken !== chartRenderToken) {
                window.clearInterval(chartReadyCheck);
                return;
            }
            checks += 1;
            if (chartContainer.querySelector('iframe')) {
                if (fallback.parentNode) fallback.remove();
                if (!chartContainer.querySelector('.chart-caption')) {
                    chartContainer.appendChild(makeElement('div', 'chart-caption', `${company.ticker} market chart`));
                }
                chartContainer.classList.remove('chart-loading');
                window.clearInterval(chartReadyCheck);
            } else if (checks >= 10) {
                markChartUnavailable();
                window.clearInterval(chartReadyCheck);
            }
        }, 500);
    } else {
        markChartUnavailable();
    }
}

function renderDecisionBrief(company) {
    const metrics = companyMetrics(company);
    const watchButton = document.getElementById('watchlist-toggle');
    watchButton.textContent = watchlist.has(company.ticker) ? 'Tracking' : 'Track';
    watchButton.classList.toggle('active', watchlist.has(company.ticker));
    setText('brief-confidence', Number(metrics.upstream_count || 0).toLocaleString());
    setText('brief-approved', Number(metrics.downstream_count || 0).toLocaleString());
    setText('brief-concentration', `${Math.round(Number(metrics.concentration_score || 0) * 100)}%`);
    setText('brief-verified', `${Number(metrics.risk_score || 0)}/100`);

    const badges = document.getElementById('detail-risk-badges');
    clearElement(badges);
    if (!metrics.total_links) badges.appendChild(makeElement('span', 'trust-badge pending', 'Discovery candidate'));

    const related = document.getElementById('detail-related');
    clearElement(related);
    related.appendChild(makeElement('div', 'related-title', 'Related Companies'));
    const relationships = [...(company.upstream || []), ...(company.downstream || [])]
        .filter(dep => dep.ticker)
        .sort((a, b) => (Number(b.confidence || 0) - Number(a.confidence || 0)))
        .slice(0, 8);
    if (!relationships.length) {
        related.appendChild(makeElement('span', 'empty-state', 'No related companies tracked yet.'));
        return;
    }
    const chips = makeElement('div', 'related-chip-row');
    relationships.forEach(dep => {
        const chip = makeElement('button', 'related-chip', dep.ticker);
        const linkedCompany = getCompanyByTicker(dep.ticker);
        chip.type = 'button';
        chip.title = dep.name ? displayCompanyName(dep.name) : dep.ticker;
        chip.onclick = () => linkedCompany ? navigateCompany(linkedCompany.ticker) : navigateCompanies({ query: dep.ticker });
        chips.appendChild(chip);
    });
    related.appendChild(chips);
}

function toggleCurrentWatchlist() {
    const ticker = currentRoute.ticker;
    if (!ticker) return;
    if (watchlist.has(ticker)) watchlist.delete(ticker);
    else watchlist.add(ticker);
    saveWatchlist();
    const company = getCompanyByTicker(ticker);
    if (company) renderDecisionBrief(company);
}

function navigateCompareFromCurrent() {
    const ticker = currentRoute.ticker || '';
    const nextPeer = [...(getCompanyByTicker(ticker)?.upstream || []), ...(getCompanyByTicker(ticker)?.downstream || [])]
        .map(dep => dep.ticker)
        .find(depTicker => getCompanyByTicker(depTicker));
    navigateCompare({ a: ticker, b: nextPeer || '' });
}

function renderMetrics(company) {
    setText('detail-price', `$${(company.price || 0).toFixed(2)}`);
    const changeEl = document.getElementById('detail-change');
    changeEl.textContent = `${company.change > 0 ? '+' : ''}${(company.change || 0).toFixed(2)}%`;
    changeEl.className = `metric-value ${company.change >= 0 ? 'positive' : 'negative'}`;
    setText('detail-high', formatNum(company.high_52w, 'currency'));
    setText('detail-low', formatNum(company.low_52w, 'currency'));
    setText('detail-mcap', formatNum(company.market_cap));
    setText('detail-ev', formatNum(company.enterprise_value));
    setText('detail-tpe', formatNum(company.trailing_pe, 'ratio'));
    setText('detail-fpe', formatNum(company.forward_pe, 'ratio'));
    setText('detail-rev', formatNum(company.revenue));
    setText('detail-margin', formatNum(company.margin, 'percent'));
    setText('detail-rec', company.recommendation || 'N/A');
    setText('detail-target', formatNum(company.target_price, 'currency'));
    setText('detail-div', company.dividend || 'N/A');
    setText('detail-pb', formatNum(company.price_to_book, 'ratio'));
    setText('detail-ceo', company.ceo || 'N/A');
    setText('detail-emp', company.employees ? company.employees.toLocaleString() : "N/A");
    setText('detail-summary', company.summary || 'No summary available.');
}

function renderSupplyGraph(company) {
    const graph = document.getElementById('supply-graph');
    clearElement(graph);
    const upstream = company.upstream || [];
    const downstream = company.downstream || [];

    const makeGraphColumn = (direction, title, links) => {
        const column = makeElement('div', `graph-column ${direction}`);
        const heading = makeElement('div', 'graph-column-title');
        heading.appendChild(makeElement('span', '', title));
        heading.appendChild(makeElement('strong', '', String(links.length)));
        column.appendChild(heading);

        const list = makeElement('div', 'graph-node-list');
        if (!links.length) {
            list.appendChild(makeElement('span', 'empty-state', direction === 'upstream' ? 'No upstream suppliers tracked.' : 'No downstream customers tracked.'));
            column.appendChild(list);
            return column;
        }

        links.forEach(dep => {
            const node = makeElement('button', `graph-node ${direction} status-${relationshipStatus(dep)}`);
            node.type = 'button';
            node.appendChild(makeElement('strong', '', dep.ticker || displayCompanyName(dep.name)));
            node.appendChild(makeElement('span', '', dep.product || dep.type || 'Supply Link'));
            node.onclick = () => getCompanyByTicker(dep.ticker) ? navigateCompany(dep.ticker) : openEvidenceModal(dep, company, direction);
            list.appendChild(node);
        });
        column.appendChild(list);
        return column;
    };

    graph.appendChild(makeGraphColumn('upstream', 'Upstream', upstream));

    const center = makeElement('button', 'graph-node graph-center');
    center.type = 'button';
    center.appendChild(makeElement('strong', '', company.ticker || 'N/A'));
    center.appendChild(makeElement('span', '', `${relationshipCount(company)} links`));
    graph.appendChild(center);

    graph.appendChild(makeGraphColumn('downstream', 'Downstream', downstream));
}

function renderXRay(company) {
    const renderXRayCard = (dep, directionClass) => {
        const linkedCompany = getCompanyByTicker(dep.ticker);
        const card = makeElement('div', `xray-card ${directionClass}`);

        const topLine = makeElement('div', 'xray-topline');
        const companyLine = makeElement('div', 'xray-company-line');
        companyLine.appendChild(makeElement('span', 'xray-name', displayCompanyName(dep.name)));
        companyLine.appendChild(makeElement('span', 'xray-ticker', dep.ticker ? `(${dep.ticker})` : ''));

        if (linkedCompany) {
            const changeClass = linkedCompany.change >= 0 ? 'positive' : 'negative';
            const sign = linkedCompany.change > 0 ? '+' : '';
            companyLine.appendChild(makeElement('span', `mini-metric ${changeClass}`, `${sign}${(linkedCompany.change || 0).toFixed(2)}%`));
        }

        topLine.appendChild(companyLine);
        topLine.appendChild(makeElement('span', `dep-pill ${relationshipStatus(dep)}`, dep.type || 'Supply Link'));
        card.appendChild(topLine);

        if (dep.product && dep.product !== dep.type) {
            card.appendChild(makeElement('div', 'relationship-product', dep.product));
        }

        const meta = makeElement('div', 'relationship-meta');
        const confidence = dep.confidence !== undefined && dep.confidence !== null ? `${Math.round(Number(dep.confidence) * 100)}% confidence` : 'Confidence N/A';
        meta.appendChild(makeElement('span', 'source-badge', confidence));
        meta.appendChild(makeElement('span', 'source-badge', dep.source_type || 'Source N/A'));
        meta.appendChild(makeElement('span', 'source-badge', dep.last_verified ? `Verified ${dep.last_verified}` : 'Verification N/A'));
        card.appendChild(meta);

        if (dep.evidence_excerpt) {
            card.appendChild(makeElement('p', 'relationship-evidence', dep.evidence_excerpt));
        }

        const actions = makeElement('div', 'relationship-actions');
        const hasSourceUrl = dep.source && /^https?:\/\//i.test(dep.source);
        const evidenceButton = makeElement('button', 'mini-button', dep.evidence_excerpt || hasSourceUrl ? 'Evidence' : 'Details');
        evidenceButton.type = 'button';
        evidenceButton.onclick = event => {
            event.stopPropagation();
            openEvidenceModal(dep, getCompanyByTicker(currentRoute.ticker) || {}, directionClass);
        };
        actions.appendChild(evidenceButton);
        if (hasSourceUrl) {
            const sourceLink = makeElement('a', 'mini-button', 'Source');
            sourceLink.href = dep.source;
            sourceLink.target = '_blank';
            sourceLink.rel = 'noopener noreferrer';
            sourceLink.onclick = event => event.stopPropagation();
            actions.appendChild(sourceLink);
        }
        if (linkedCompany) {
            const compareButton = makeElement('button', 'mini-button', 'Compare');
            compareButton.type = 'button';
            compareButton.onclick = event => {
                event.stopPropagation();
                navigateCompare({ a: currentRoute.ticker, b: linkedCompany.ticker });
            };
            actions.appendChild(compareButton);
        }
        card.appendChild(actions);

        if (linkedCompany) {
            card.onclick = () => {
                window.scrollTo({ top: 0, behavior: 'smooth' });
                navigateCompany(linkedCompany.ticker);
            };
        } else {
            card.style.cursor = 'default';
            card.title = dep.source || "Detailed data not tracked for this entity.";
        }

        return card;
    };

    const upContainer = document.getElementById('detail-upstream');
    clearElement(upContainer);
    if (company.upstream && company.upstream.length > 0) {
        company.upstream.forEach(dep => upContainer.appendChild(renderXRayCard(dep, 'upstream')));
    } else {
        upContainer.appendChild(makeElement('span', 'empty-state', 'No known upstream suppliers tracked.'));
    }

    const downContainer = document.getElementById('detail-downstream');
    clearElement(downContainer);
    if (company.downstream && company.downstream.length > 0) {
        company.downstream.forEach(dep => downContainer.appendChild(renderXRayCard(dep, 'downstream')));
    } else {
        downContainer.appendChild(makeElement('span', 'empty-state', 'No known downstream exposure tracked.'));
    }
}

function openEvidenceModal(dep, company = {}, direction = '') {
    const modal = document.getElementById('evidence-modal');
    const body = document.getElementById('modal-body');
    clearElement(body);
    setText('modal-title', `${company.ticker || 'Company'} ${direction === 'upstream' ? '<-' : '->'} ${dep.ticker || dep.name || 'Counterparty'}`);
    [
        ['Counterparty', `${displayCompanyName(dep.name)} ${dep.ticker ? `(${dep.ticker})` : ''}`],
        ['Relationship', dep.type || 'Supply Link'],
        ['Product / service', dep.product || 'N/A'],
        ['Confidence', dep.confidence !== undefined && dep.confidence !== null ? `${Math.round(Number(dep.confidence) * 100)}%` : 'N/A'],
        ['Source type', dep.source_type || 'N/A'],
        ['Last verified', dep.last_verified || 'N/A'],
    ].forEach(([label, value]) => {
        const row = makeElement('div', 'modal-row');
        row.appendChild(makeElement('span', '', label));
        row.appendChild(makeElement('strong', '', value));
        body.appendChild(row);
    });
    if (dep.evidence_excerpt) {
        body.appendChild(makeElement('p', 'modal-evidence', dep.evidence_excerpt));
    } else {
        body.appendChild(makeElement('p', 'modal-evidence muted', 'No evidence excerpt was saved for this relationship.'));
    }
    if (dep.source && /^https?:\/\//i.test(dep.source)) {
        const link = makeElement('a', 'source-link modal-source', 'Open source');
        link.href = dep.source;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        body.appendChild(link);
    }
    modal.classList.remove('hidden');
}

function closeEvidenceModal() {
    document.getElementById('evidence-modal').classList.add('hidden');
}

function hideAllViews() {
    ['view-industries', 'view-quality', 'view-companies', 'view-details', 'view-watchlist', 'view-predictions', 'view-compare', 'view-sector'].forEach(id => {
        document.getElementById(id).classList.add('hidden');
    });
}

function renderWatchlistView() {
    currentRoute = { view: 'watchlist' };
    window.scrollTo({ top: 0, behavior: 'smooth' });
    hideAllViews();
    document.getElementById('view-watchlist').classList.remove('hidden');
    const grid = document.getElementById('watchlist-grid');
    clearElement(grid);
    const companies = [...watchlist].map(getCompanyByTicker).filter(Boolean);
    setText('watchlist-count', `${companies.length} saved`);
    if (!companies.length) {
        grid.appendChild(makeElement('span', 'empty-state', 'Track companies from a Decision Brief to build a local research queue.'));
        return;
    }
    companies
        .sort((a, b) => companyMetrics(b).risk_score - companyMetrics(a).risk_score)
        .forEach(company => grid.appendChild(renderCompanySignalCard(company)));
}

function renderPredictionsView() {
    currentRoute = { view: 'predictions' };
    window.scrollTo({ top: 0, behavior: 'smooth' });
    hideAllViews();
    document.getElementById('view-predictions').classList.remove('hidden');
    const predictions = Array.isArray(predictionData.predictions) ? predictionData.predictions : [];
    const calibration = predictionData.calibration || {};
    const generatedAt = predictionData.generated_at ? new Date(predictionData.generated_at) : null;
    setText('prediction-updated', generatedAt && !Number.isNaN(generatedAt.valueOf()) ? `Updated ${generatedAt.toLocaleDateString()}` : 'Awaiting signal run');
    setText('prediction-disclaimer', predictionData.disclaimer || 'Research signals only. They are not investment advice or trading instructions.');

    const calibrationEl = document.getElementById('prediction-calibration');
    clearElement(calibrationEl);
    const resolved = Number(calibration.resolved_predictions || 0);
    const hitRate = calibration.hit_rate === null || calibration.hit_rate === undefined ? 'Calibrating' : `${Math.round(Number(calibration.hit_rate) * 100)}% hit rate`;
    [
        [`Top ${predictionData.universe_size || predictions.length} companies`, `${predictionData.horizon_days || 30}-day horizon`],
        [hitRate, `${resolved} resolved signal${resolved === 1 ? '' : 's'}`],
        ['One-hop graph signals', 'Direct inputs + relationship evidence'],
    ].forEach(([title, detail]) => {
        const item = makeElement('div', 'prediction-calibration-item');
        item.appendChild(makeElement('strong', '', title));
        item.appendChild(makeElement('span', '', detail));
        calibrationEl.appendChild(item);
    });

    const grid = document.getElementById('prediction-grid');
    clearElement(grid);
    if (!predictions.length) {
        grid.appendChild(makeElement('p', 'empty-state', 'No generated signals are available yet.'));
        return;
    }
    predictions.forEach(prediction => {
        const card = makeElement('article', `prediction-card ${prediction.direction || 'neutral'}`);
        const header = makeElement('div', 'prediction-card-header');
        const title = makeElement('div', '');
        title.appendChild(makeElement('strong', 'prediction-ticker', prediction.ticker || 'N/A'));
        title.appendChild(makeElement('span', 'prediction-company', displayCompanyName(prediction.company_name)));
        header.appendChild(title);
        header.appendChild(makeElement('span', `prediction-direction ${prediction.direction || 'neutral'}`, String(prediction.direction || 'neutral').toUpperCase()));
        card.appendChild(header);
        card.appendChild(makeElement('p', 'prediction-summary', prediction.scenario_summary || 'No scenario summary available.'));
        const metrics = makeElement('div', 'prediction-metrics');
        [
            ['Confidence', `${Math.round(Number(prediction.confidence || 0) * 100)}%`],
            ['Direct', Number(prediction.direct_signal || 0).toFixed(2)],
            ['Network', Number(prediction.network_signal || 0).toFixed(2)],
        ].forEach(([label, value]) => {
            const metric = makeElement('div', '');
            metric.appendChild(makeElement('span', '', label));
            metric.appendChild(makeElement('strong', '', value));
            metrics.appendChild(metric);
        });
        card.appendChild(metrics);
        const paths = prediction.connection_paths || [];
        if (paths.length) {
            const pathsEl = makeElement('div', 'prediction-paths');
            pathsEl.appendChild(makeElement('span', 'prediction-path-label', 'Top graph inputs'));
            paths.slice(0, 2).forEach(path => {
                pathsEl.appendChild(makeElement('span', 'prediction-path', `${path.connected_ticker} - ${path.relationship_type} (${Number(path.contribution || 0).toFixed(2)})`));
            });
            card.appendChild(pathsEl);
        }
        const actions = makeElement('div', 'relationship-actions');
        const open = makeElement('button', 'mini-button', 'Open brief');
        open.type = 'button';
        open.onclick = () => navigateCompany(prediction.ticker);
        actions.appendChild(open);
        card.appendChild(actions);
        grid.appendChild(card);
    });
}

function renderCompanySignalCard(company) {
    const metrics = companyMetrics(company);
    const card = makeElement('article', 'signal-card');
    const header = makeElement('div', 'signal-card-header');
    header.appendChild(makeElement('strong', '', `${company.ticker} - ${displayCompanyName(company.name)}`));
    header.appendChild(makeElement('span', 'source-badge pending', `Risk ${metrics.risk_score}/100`));
    card.appendChild(header);
    card.appendChild(makeElement('p', '', `${metrics.total_links} tracked links, supplier risk ${metrics.supplier_risk}/100, customer risk ${metrics.customer_risk}/100.`));
    const actions = makeElement('div', 'relationship-actions');
    const open = makeElement('button', 'mini-button', 'Open brief');
    open.onclick = () => navigateCompany(company.ticker);
    actions.appendChild(open);
    const compare = makeElement('button', 'mini-button', 'Compare');
    compare.onclick = () => navigateCompare({ a: company.ticker });
    actions.appendChild(compare);
    card.appendChild(actions);
    return card;
}

function renderSectorView(sector) {
    currentRoute = { view: 'sector', sector };
    window.scrollTo({ top: 0, behavior: 'smooth' });
    hideAllViews();
    document.getElementById('view-sector').classList.remove('hidden');
    const companies = (globalData[sector] || []).map(company => ({ ...company, sector, connection_count: relationshipCount(company) }));
    const linked = companies.filter(company => relationshipCount(company) > 0);
    setText('sector-title', sector || 'Unknown sector');
    setText('sector-count', `${companies.length.toLocaleString()} companies`);
    const summary = document.getElementById('sector-summary');
    clearElement(summary);
    const sectorLinks = linked.reduce((sum, company) => sum + (companyMetrics(company).total_links || 0), 0);
    [
        ['Companies', companies.length],
        ['Linked companies', linked.length],
        ['Supply links', sectorLinks],
        ['Avg risk', linked.length ? Math.round(linked.reduce((sum, company) => sum + companyMetrics(company).risk_score, 0) / linked.length) : 0],
    ].forEach(([label, value]) => {
        const card = makeElement('article', 'radar-card');
        card.appendChild(makeElement('div', 'radar-card-title', label));
        card.appendChild(makeElement('span', 'stat-value', value.toLocaleString()));
        summary.appendChild(card);
    });
    const tbody = document.getElementById('sector-company-table-body');
    clearElement(tbody);
    companies
        .sort((a, b) => {
            const capA = Number(a.market_cap || 0);
            const capB = Number(b.market_cap || 0);
            return capB - capA;
        })
        .forEach(company => tbody.appendChild(renderCompanyTableRow(company)));
}

function renderCompareView(updateRoute = true) {
    window.scrollTo({ top: 0, behavior: 'smooth' });
    hideAllViews();
    document.getElementById('view-compare').classList.remove('hidden');
    const tickerA = document.getElementById('compare-a').value.trim().toUpperCase();
    const tickerB = document.getElementById('compare-b').value.trim().toUpperCase();
    const route = { view: 'compare', a: tickerA, b: tickerB };
    currentRoute = route;
    if (updateRoute) updateRouteHash(route, false);
    const companies = [getCompanyByTicker(tickerA), getCompanyByTicker(tickerB)].filter(Boolean);
    setText('compare-count', `${companies.length}/2 selected`);
    const grid = document.getElementById('compare-grid');
    clearElement(grid);
    if (!companies.length) {
        grid.appendChild(makeElement('span', 'empty-state', 'Enter two tickers to compare valuation context and supply-chain risk.'));
        return;
    }
    companies.forEach(company => {
        const metrics = companyMetrics(company);
        const card = makeElement('article', 'compare-card');
        card.appendChild(makeElement('h3', '', `${company.ticker} - ${displayCompanyName(company.name)}`));
        const rows = [
            ['Market cap', formatNum(company.market_cap)],
            ['Tracked links', metrics.total_links],
            ['Risk score', `${metrics.risk_score}/100`],
            ['Supplier risk', `${metrics.supplier_risk}/100`],
            ['Customer risk', `${metrics.customer_risk}/100`],
            ['Top supplier', metrics.top_upstream?.[0]?.ticker || 'N/A'],
            ['Top customer', metrics.top_downstream?.[0]?.ticker || 'N/A'],
        ];
        rows.forEach(([label, value]) => {
            const row = makeElement('div', 'modal-row');
            row.appendChild(makeElement('span', '', label));
            row.appendChild(makeElement('strong', '', String(value)));
            card.appendChild(row);
        });
        const open = makeElement('button', 'mini-button', 'Open brief');
        open.onclick = () => navigateCompany(company.ticker);
        card.appendChild(open);
        grid.appendChild(card);
    });
}
