let globalData = {};
let allCompanies = [];
let currentCompaniesList = [];
let currentTitle = 'Companies';
let currentRoute = { view: 'overview' };
let sortCol = 'market_cap';
let sortAsc = false;

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
    document.getElementById(id).textContent = value;
};

const formatNum = (num, type = 'currency') => {
    if (num === null || num === undefined || isNaN(num)) return "N/A";
    const value = Number(num);
    if (type === 'percent') return (value * 100).toFixed(2) + '%';
    if (type === 'ratio') return value.toFixed(2);
    if (value >= 1e12) return '$' + (value / 1e12).toFixed(2) + 'T';
    if (value >= 1e9) return '$' + (value / 1e9).toFixed(2) + 'B';
    if (value >= 1e6) return '$' + (value / 1e6).toFixed(2) + 'M';
    return '$' + value.toLocaleString();
};

const relationshipCount = (company) => (company.upstream?.length || 0) + (company.downstream?.length || 0);

const uniqueLinkCount = () => {
    const ids = new Set();
    allCompanies.forEach(company => {
        [...(company.upstream || []), ...(company.downstream || [])].forEach(dep => {
            if (dep.edge_id !== undefined && dep.edge_id !== null) {
                ids.add(String(dep.edge_id));
            }
        });
    });
    return ids.size || Math.round(allCompanies.reduce((sum, company) => sum + relationshipCount(company), 0) / 2);
};

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

function getLastUpdatedLabel() {
    const dates = allCompanies.map(company => company.last_updated).filter(Boolean).sort().reverse();
    return dates.length ? `Data synced ${dates[0]}` : 'Live Data Synced';
}

function resetDashboard() {
    document.getElementById('search-input').value = '';
    document.getElementById('sector-filter').value = '';
    document.getElementById('dependency-filter').value = '';
    document.getElementById('connected-filter').checked = false;
    currentCompaniesList = [];
    currentTitle = 'Companies';
    renderLevel1();
}

function navigateOverview() {
    setRoute({ view: 'overview' });
}

function navigateCompanies(route = {}, push = true) {
    setRoute({ view: 'companies', ...route }, push);
}

function navigateCompany(ticker) {
    if (!ticker) return;
    setRoute({ view: 'company', ticker, previous: currentRoute });
}

function navigateBackToCompanies() {
    if (currentRoute.previous && currentRoute.previous.view === 'companies') {
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
        applyFilters(false);
        return;
    }

    resetDashboard();
}

window.addEventListener('popstate', () => applyRouteFromHash(false));
window.addEventListener('hashchange', () => applyRouteFromHash(false));

function showCompanies() {
    if (!currentCompaniesList.length) {
        currentCompaniesList = [...allCompanies];
        currentTitle = 'All Companies';
        setText('current-industry-title', currentTitle);
    }
    document.getElementById('view-industries').classList.add('hidden');
    document.getElementById('view-companies').classList.remove('hidden');
    document.getElementById('view-details').classList.add('hidden');
}

function renderOverview() {
    const mostConnected = [...allCompanies].sort((a, b) => b.connection_count - a.connection_count)[0];

    setText('stat-companies', allCompanies.length.toLocaleString());
    setText('stat-links', uniqueLinkCount().toLocaleString());
    setText('stat-sectors', Object.keys(globalData).length.toLocaleString());
    setText('stat-connected', mostConnected?.ticker || 'N/A');
    renderConnectedList();
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

function renderConnectedList() {
    const list = document.getElementById('connected-list');
    clearElement(list);

    const connected = [...allCompanies]
        .filter(company => company.connection_count > 0)
        .sort((a, b) => b.connection_count - a.connection_count)
        .slice(0, 6);

    if (!connected.length) {
        list.appendChild(makeElement('span', 'empty-state', 'No supply-chain relationships exported yet.'));
        return;
    }

    connected.forEach((company, index) => {
        const row = makeElement('button', 'rank-item');
        row.onclick = () => navigateCompany(company.ticker);
        row.appendChild(makeElement('span', 'rank-index', String(index + 1)));
        const body = makeElement('span', 'rank-body');
        body.appendChild(makeElement('strong', '', company.name || 'Unknown'));
        body.appendChild(makeElement('small', '', `${company.ticker || 'N/A'} · ${company.connection_count} links`));
        row.appendChild(body);
        list.appendChild(row);
    });
}

function renderLevel1() {
    window.scrollTo({ top: 0, behavior: 'smooth' });
    document.getElementById('view-industries').classList.remove('hidden');
    document.getElementById('view-companies').classList.add('hidden');
    document.getElementById('view-details').classList.add('hidden');

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
            navigateCompanies({ sector });
        };
        card.appendChild(makeElement('span', 'sector-name', sector));
        card.appendChild(makeElement('span', 'sector-meta', `${companies.length} equities · ${links} links`));
        grid.appendChild(card);
    });
}

function applyFilters(updateRoute = true) {
    const query = document.getElementById('search-input').value.toLowerCase().trim();
    const sector = document.getElementById('sector-filter').value;
    const dependency = document.getElementById('dependency-filter').value;
    const onlyConnected = document.getElementById('connected-filter').checked;

    let results = allCompanies.filter(company => {
        const matchesQuery = !query ||
            (company.name || '').toLowerCase().includes(query) ||
            (company.ticker || '').toLowerCase().includes(query);
        const matchesSector = !sector || company.sector === sector;
        const deps = [...(company.upstream || []), ...(company.downstream || [])];
        const matchesDependency = !dependency || deps.some(dep => dep.type === dependency);
        const matchesConnected = !onlyConnected || company.connection_count > 0;
        return matchesQuery && matchesSector && matchesDependency && matchesConnected;
    });

    currentCompaniesList = results;
    currentTitle = query ? `Search Results (${results.length})` : sector || 'All Companies';
    setText('current-industry-title', currentTitle);
    renderLevel2();

    if (updateRoute) {
        const shouldPush = currentRoute.view !== 'companies';
        navigateCompanies({
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
        const tr = document.createElement('tr');
        tr.onclick = () => navigateCompany(company.ticker);

        const changeClass = company.change >= 0 ? 'positive' : 'negative';
        const sign = company.change > 0 ? '+' : '';
        const nameCell = makeElement('td', 'company-cell', company.name || '');

        tr.appendChild(nameCell);
        tr.appendChild(makeElement('td', '', company.ticker || ''));
        tr.appendChild(makeElement('td', '', `$${(company.price || 0).toFixed(2)}`));
        tr.appendChild(makeElement('td', changeClass, `${sign}${(company.change || 0).toFixed(2)}%`));
        tr.appendChild(makeElement('td', '', formatNum(company.market_cap)));
        tr.appendChild(makeElement('td', '', String(company.connection_count || 0)));
        tr.appendChild(makeElement('td', '', formatNum(company.trailing_pe, 'ratio')));
        tbody.appendChild(tr);
    });
}

const getCompanyByTicker = (ticker) => allCompanies.find(company => company.ticker === ticker) || null;

function renderLevel3(company, previousRoute = null) {
    currentRoute = {
        view: 'company',
        ticker: company.ticker,
        previous: previousRoute || { view: 'companies' }
    };
    window.scrollTo({ top: 0, behavior: 'smooth' });
    document.getElementById('view-industries').classList.add('hidden');
    document.getElementById('view-companies').classList.add('hidden');
    document.getElementById('view-details').classList.remove('hidden');

    setText('detail-name', company.name || 'N/A');
    setText('detail-ticker', company.ticker || 'N/A');
    setText('detail-industry', company.industry || company.sector || 'Uncategorized');

    renderStory(company);
    renderChart(company);
    renderMetrics(company);
    renderSupplyMap(company);
    renderXRay(company);
}

function renderStory(company) {
    const upstream = company.upstream || [];
    const downstream = company.downstream || [];
    const primarySupplier = upstream[0]?.name;
    const primaryCustomer = downstream[0]?.name;

    setText('detail-up-count', upstream.length);
    setText('detail-down-count', downstream.length);
    setText('detail-link-count', upstream.length + downstream.length);
    setText('detail-story-title', `${company.ticker || company.name} dependency snapshot`);

    let story = `${company.name} has ${upstream.length} tracked upstream supplier relationship${upstream.length === 1 ? '' : 's'} and ${downstream.length} tracked downstream customer relationship${downstream.length === 1 ? '' : 's'}.`;
    if (primarySupplier) story += ` Its upstream exposure includes ${primarySupplier}.`;
    if (primaryCustomer) story += ` Its downstream exposure includes ${primaryCustomer}.`;
    if (!upstream.length && !downstream.length) story += ' This company is a good candidate for the next discovery pass.';
    setText('detail-story', story);
}

function renderChart(company) {
    const chartContainer = document.getElementById('tv_chart_container');
    clearElement(chartContainer);
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
            range: "60M"
        });
    } else {
        chartContainer.appendChild(makeElement('div', 'chart-unavailable', 'Chart unavailable for this entity'));
    }
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

function renderSupplyMap(company) {
    const map = document.getElementById('supply-map');
    clearElement(map);

    const upstream = (company.upstream || []).slice(0, 6);
    const downstream = (company.downstream || []).slice(0, 6);

    map.appendChild(renderMapColumn('Suppliers', upstream, 'upstream'));
    map.appendChild(renderMapCenter(company));
    map.appendChild(renderMapColumn('Customers', downstream, 'downstream'));
}

function renderMapColumn(title, deps, direction) {
    const column = makeElement('div', `map-column ${direction}`);
    column.appendChild(makeElement('span', 'map-title', title));
    if (!deps.length) {
        column.appendChild(makeElement('span', 'empty-state', 'No tracked links'));
        return column;
    }

    deps.forEach(dep => {
        const node = makeElement('button', 'map-node');
        const linkedCompany = getCompanyByTicker(dep.ticker);
        if (linkedCompany) node.onclick = () => navigateCompany(linkedCompany.ticker);
        node.appendChild(makeElement('strong', '', dep.ticker || dep.name || 'Unknown'));
        node.appendChild(makeElement('small', '', dep.product || dep.type || 'Supply Link'));
        column.appendChild(node);
    });
    return column;
}

function renderMapCenter(company) {
    const center = makeElement('div', 'map-center');
    center.appendChild(makeElement('span', 'center-ticker', company.ticker || 'N/A'));
    center.appendChild(makeElement('strong', '', company.name || 'Unknown'));
    center.appendChild(makeElement('small', '', `${company.connection_count || 0} tracked links`));
    return center;
}

function renderXRay(company) {
    const renderXRayCard = (dep, directionClass) => {
        const linkedCompany = getCompanyByTicker(dep.ticker);
        const card = makeElement('div', `xray-card ${directionClass}`);

        const topLine = makeElement('div', 'xray-topline');
        const companyLine = makeElement('div', 'xray-company-line');
        companyLine.appendChild(makeElement('span', 'xray-name', dep.name || 'Unknown'));
        companyLine.appendChild(makeElement('span', 'xray-ticker', dep.ticker ? `(${dep.ticker})` : ''));

        if (linkedCompany) {
            const changeClass = linkedCompany.change >= 0 ? 'positive' : 'negative';
            const sign = linkedCompany.change > 0 ? '+' : '';
            companyLine.appendChild(makeElement('span', `mini-metric ${changeClass}`, `${sign}${(linkedCompany.change || 0).toFixed(2)}%`));
        }

        topLine.appendChild(companyLine);
        topLine.appendChild(makeElement('span', 'dep-pill', dep.type || 'Supply Link'));
        card.appendChild(topLine);

        if (dep.product && dep.product !== dep.type) {
            card.appendChild(makeElement('div', 'relationship-product', dep.product));
        }

        const meta = makeElement('div', 'relationship-meta');
        const confidence = dep.confidence === null || dep.confidence === undefined ? 'N/A' : `${Math.round(Number(dep.confidence) * 100)}%`;
        meta.appendChild(makeElement('span', 'source-badge', dep.source_type || 'Source'));
        meta.appendChild(makeElement('span', '', `Confidence ${confidence}`));
        meta.appendChild(makeElement('span', '', `Verified ${dep.last_verified || 'N/A'}`));
        if (dep.source && /^https?:\/\//i.test(dep.source)) {
            const sourceLink = makeElement('a', 'source-link', 'Source');
            sourceLink.href = dep.source;
            sourceLink.target = '_blank';
            sourceLink.rel = 'noopener noreferrer';
            sourceLink.onclick = event => event.stopPropagation();
            meta.appendChild(sourceLink);
        }
        card.appendChild(meta);

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
