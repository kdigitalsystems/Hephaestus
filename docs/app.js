let globalData = {};
let currentSector = '';
let currentCompaniesList = []; 

let sortCol = 'market_cap';
let sortAsc = false;

// Format numbers for currency, percentages, or standard ratios
const formatNum = (num, type = 'currency') => {
    if (num === null || num === undefined || isNaN(num)) return "N/A";
    if (type === 'percent') return (num * 100).toFixed(2) + '%';
    if (type === 'ratio') return num.toFixed(2);
    if (num >= 1e12) return '$' + (num / 1e12).toFixed(2) + 'T';
    if (num >= 1e9) return '$' + (num / 1e9).toFixed(2) + 'B';
    if (num >= 1e6) return '$' + (num / 1e6).toFixed(2) + 'M';
    return '$' + num.toLocaleString();
};

// Fetch the exported backend data
fetch('dashboard_data.json')
    .then(response => response.json())
    .then(data => {
        globalData = data.industries;
        document.getElementById('last-updated').innerText = "Live Data Synced";
        renderLevel1();
    })
    .catch(error => {
        console.error("Data load failed:", error);
        document.getElementById('last-updated').innerText = "Failed to load data";
    });

// LEVEL 1: Industry Grid
function renderLevel1() {
    document.getElementById('view-industries').classList.remove('hidden');
    document.getElementById('view-companies').classList.add('hidden');
    document.getElementById('view-details').classList.add('hidden');

    const grid = document.getElementById('industry-grid');
    grid.innerHTML = '';

    const sortedSectors = Object.keys(globalData).sort();

    sortedSectors.forEach(sector => {
        const card = document.createElement('div');
        card.className = 'card';
        card.onclick = () => { 
            currentSector = sector; 
            currentCompaniesList = [...globalData[sector]];
            document.getElementById('current-industry-title').innerText = sector;
            renderLevel2(); 
        };
        card.innerHTML = `<h2>${sector}</h2><p>${globalData[sector].length} Equities</p>`;
        grid.appendChild(card);
    });
}

// Global Search
function handleSearch() {
    const query = document.getElementById('search-input').value.toLowerCase();
    
    if (query.length < 2) {
        renderLevel1();
        return;
    }
    
    let results = [];
    for (const [sector, companies] of Object.entries(globalData)) {
        companies.forEach(c => {
            if (c.name.toLowerCase().includes(query) || (c.ticker && c.ticker.toLowerCase().includes(query))) {
                results.push(c);
            }
        });
    }
    
    currentCompaniesList = results;
    document.getElementById('current-industry-title').innerText = `Search Results (${results.length})`;
    renderLevel2(true);
}

// Table Sorting
function handleSort(col) {
    if (sortCol === col) {
        sortAsc = !sortAsc;
    } else {
        sortCol = col;
        sortAsc = (col === 'name' || col === 'ticker'); 
    }
    renderLevel2(false);
}

// LEVEL 2: Company Table
function renderLevel2(isSearch = false) {
    if (isSearch) {
        document.getElementById('view-industries').classList.add('hidden');
        document.getElementById('view-companies').classList.remove('hidden');
        document.getElementById('view-details').classList.add('hidden');
    } else {
        document.getElementById('view-industries').classList.add('hidden');
        document.getElementById('view-companies').classList.remove('hidden');
        document.getElementById('view-details').classList.add('hidden');
    }
    
    document.querySelectorAll('.sort-icon').forEach(icon => icon.innerText = '');
    document.getElementById(`sort-${sortCol}`).innerText = sortAsc ? '▲' : '▼';

    let companies = currentCompaniesList;
    
    companies.sort((a, b) => {
        let valA = a[sortCol];
        let valB = b[sortCol];
        
        if (valA === null || valA === undefined || valA === "N/A") valA = sortAsc ? Infinity : -Infinity;
        if (valB === null || valB === undefined || valB === "N/A") valB = sortAsc ? Infinity : -Infinity;

        if (typeof valA === 'string') {
            return sortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
        } else {
            return sortAsc ? valA - valB : valB - valA;
        }
    });

    const tbody = document.getElementById('company-table-body');
    tbody.innerHTML = '';

    companies.forEach(company => {
        const tr = document.createElement('tr');
        tr.onclick = () => renderLevel3(company);
        
        const changeClass = company.change >= 0 ? 'positive' : 'negative';
        const sign = company.change > 0 ? '+' : '';

        tr.innerHTML = `
            <td style="font-weight: bold; color: var(--accent);">${company.name}</td>
            <td>${company.ticker}</td>
            <td>$${(company.price || 0).toFixed(2)}</td>
            <td class="${changeClass}">${sign}${(company.change || 0).toFixed(2)}%</td>
            <td>${formatNum(company.market_cap)}</td>
            <td>${formatNum(company.trailing_pe, 'ratio')}</td>
        `;
        tbody.appendChild(tr);
    });
}

// Helper function to find a company's live data by ticker for the X-Ray
const getCompanyByTicker = (ticker) => {
    for (const [sector, companies] of Object.entries(globalData)) {
        const found = companies.find(c => c.ticker === ticker);
        if (found) return found;
    }
    return null;
};

// LEVEL 3: Deep Dive Terminal
function renderLevel3(company) {
    document.getElementById('view-companies').classList.add('hidden');
    document.getElementById('view-details').classList.remove('hidden');

    document.getElementById('detail-name').innerText = company.name;
    document.getElementById('detail-ticker').innerText = company.ticker;
    document.getElementById('detail-industry').innerText = company.industry || 'Uncategorized';
    
    // Render Robinhood-Style TradingView Chart
    document.getElementById('tv_chart_container').innerHTML = ''; 
    if (company.ticker && company.ticker !== "N/A") {
        new TradingView.widget({
            "autosize": true,
            "symbol": company.ticker,
            "timezone": "America/New_York",
            "theme": "dark",
            "style": "3", 
            "locale": "en",
            "enable_publishing": false,
            "backgroundColor": "#161b22", 
            "gridColor": "#161b22", 
            "hide_top_toolbar": true, 
            "hide_legend": true,
            "save_image": false,
            "container_id": "tv_chart_container",
            "allow_symbol_change": false,
            "range": "60M" 
        });
    } else {
        document.getElementById('tv_chart_container').innerHTML = '<div style="display: flex; height: 100%; align-items: center; justify-content: center; color: var(--text-muted);">Chart unavailable for this entity</div>';
    }

    // Populate Metrics
    document.getElementById('detail-price').innerText = `$${(company.price || 0).toFixed(2)}`;
    const changeEl = document.getElementById('detail-change');
    changeEl.innerText = `${company.change > 0 ? '+' : ''}${(company.change || 0).toFixed(2)}%`;
    changeEl.className = `metric-value ${company.change >= 0 ? 'positive' : 'negative'}`;
    document.getElementById('detail-high').innerText = formatNum(company.high_52w, 'currency');
    document.getElementById('detail-low').innerText = formatNum(company.low_52w, 'currency');

    document.getElementById('detail-mcap').innerText = formatNum(company.market_cap);
    document.getElementById('detail-ev').innerText = formatNum(company.enterprise_value);
    document.getElementById('detail-tpe').innerText = formatNum(company.trailing_pe, 'ratio');
    document.getElementById('detail-fpe').innerText = formatNum(company.forward_pe, 'ratio');
    document.getElementById('detail-rev').innerText = formatNum(company.revenue);
    document.getElementById('detail-margin').innerText = formatNum(company.margin, 'percent');

    document.getElementById('detail-rec').innerText = company.recommendation;
    document.getElementById('detail-target').innerText = formatNum(company.target_price, 'currency');
    document.getElementById('detail-div').innerText = company.dividend;
    document.getElementById('detail-pb').innerText = formatNum(company.price_to_book, 'ratio');

    document.getElementById('detail-ceo').innerText = company.ceo;
    document.getElementById('detail-emp').innerText = company.employees ? company.employees.toLocaleString() : "N/A";
    document.getElementById('detail-summary').innerText = company.summary;

    // --- Render Supply Chain X-Ray ---
    const renderXRayCard = (dep, directionClass) => {
        const linkedCompany = getCompanyByTicker(dep.ticker);
        
        // Setup live metric if available
        let miniMetricHtml = '';
        if (linkedCompany) {
            const changeClass = linkedCompany.change >= 0 ? 'positive' : 'negative';
            const sign = linkedCompany.change > 0 ? '+' : '';
            miniMetricHtml = `<span class="mini-metric ${changeClass}">${sign}${(linkedCompany.change || 0).toFixed(2)}%</span>`;
        }

        const card = document.createElement('div');
        card.className = `xray-card ${directionClass}`;
        card.innerHTML = `
            <div>
                <span style="font-weight: bold; color: var(--text-main); font-size: 0.95rem;">${dep.name}</span>
                <span style="color: var(--text-muted); font-size: 0.8rem; margin-left: 5px;">${dep.ticker ? '('+dep.ticker+')' : ''}</span>
                ${miniMetricHtml}
            </div>
            <span class="dep-pill">${dep.type}</span>
        `;
        
        // Add jump navigation if tracked
        if (linkedCompany) {
            card.onclick = () => {
                window.scrollTo({ top: 0, behavior: 'smooth' });
                renderLevel3(linkedCompany);
            };
        } else {
            card.style.cursor = 'default';
            card.title = "Detailed data not tracked for this entity.";
        }
        
        return card;
    };

    // Populate Upstream
    const upContainer = document.getElementById('detail-upstream');
    upContainer.innerHTML = '';
    if (company.upstream && company.upstream.length > 0) {
        company.upstream.forEach(dep => upContainer.appendChild(renderXRayCard(dep, 'upstream')));
    } else {
        upContainer.innerHTML = '<span style="color: var(--text-muted); font-style: italic; font-size: 0.9rem;">No known upstream suppliers tracked.</span>';
    }

    // Populate Downstream
    const downContainer = document.getElementById('detail-downstream');
    downContainer.innerHTML = '';
    if (company.downstream && company.downstream.length > 0) {
        company.downstream.forEach(dep => downContainer.appendChild(renderXRayCard(dep, 'downstream')));
    } else {
        downContainer.innerHTML = '<span style="color: var(--text-muted); font-style: italic; font-size: 0.9rem;">No known downstream exposure tracked.</span>';
    }
}
