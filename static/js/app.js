// =============================================================================
// Auth module — must load before DOMContentLoaded app logic
// =============================================================================

let currentUser = null; // { role, userId, sessionId }
let metricsRefreshTimer = null;
let dashboardFilterState = { days: 30, team: 'all', tool: 'all' };

const _fetch = typeof apiFetch === 'function' ? apiFetch : fetch.bind(window);

function _wireEmailForm() {
    const emailForm = document.getElementById('user-email-form');
    if (emailForm && !emailForm.dataset.wired) {
        emailForm.dataset.wired = '1';
        emailForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = emailForm.querySelector('button[type="submit"]');
            btn.disabled = true;
            btn.textContent = 'Signing in…';
            try {
                const res = await _fetch('/api/auth/user/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email: document.getElementById('user-email').value }),
                });
                if (res.ok) {
                    currentUser = await res.json();
                    _onAuthSuccess();
                } else {
                    const err = await res.json().catch(() => ({}));
                    _showAuthError('google', err.detail || 'Login failed');
                }
            } catch {
                _showAuthError('google', 'Network error — is the harness running?');
            } finally {
                btn.disabled = false;
                btn.textContent = 'Sign in as User';
            }
        });
    }
}

async function initAuth() {
    const embedded = window.VANTAGE_CONFIG?.mode === 'embedded' && window.VANTAGE_CONFIG?.bridgeToken;
    if (embedded) {
        document.getElementById('login-overlay')?.classList.add('hidden');
    }
    try {
        const res = await _fetch('/api/auth/me');
        if (res.ok) {
            currentUser = await res.json();
            _onAuthSuccess();
            return;
        }
    } catch { /* harness unreachable — fall through to login */ }
    if (embedded) {
        const grid = document.getElementById('metrics-grid');
        if (grid) {
            grid.innerHTML = '<div class="loading">Telemetry embed auth failed — sign in to Harness UI and reopen this tab.</div>';
        }
        return;
    }
    _showLoginScreen();
    _initGoogleAuth();
}

async function _initGoogleAuth() {
    try {
        const res = await _fetch('/api/auth/config');
        if (!res.ok) return;
        const authCfg = await res.json();

        if (authCfg.googleEnabled) {
            const googleSection = document.getElementById('google-auth-section');
            const emailSection = document.getElementById('email-auth-section');
            googleSection?.classList.remove('hidden');

            if (authCfg.emailLoginEnabled) {
                emailSection?.classList.remove('hidden');
                document.getElementById('email-auth-divider')?.classList.remove('hidden');
                _wireEmailForm();
            } else {
                emailSection?.classList.add('hidden');
            }

            // Wait for Google GIS SDK to be available (it loads async)
            let waited = 0;
            while (typeof google === 'undefined' && waited < 3000) {
                await new Promise(r => setTimeout(r, 100));
                waited += 100;
            }
            if (typeof google === 'undefined') return;

            const btnHost = document.getElementById('google-signin-btn');
            if (!btnHost) return;
            btnHost.innerHTML = '';

            google.accounts.id.initialize({
                client_id: authCfg.googleClientId,
                callback: _handleGoogleCredential,
            });
            google.accounts.id.renderButton(btnHost, {
                theme: 'filled_black',
                size: 'large',
                width: 280,
                text: 'signin_with',
            });
        } else {
            // Google not configured — show email fallback form
            const emailSection = document.getElementById('email-auth-section');
            if (emailSection) emailSection.classList.remove('hidden');
            _wireEmailForm();
        }
    } catch (e) {
        console.warn('[Auth] Auth config fetch failed', e);
    }
}

async function _handleGoogleCredential(credentialResponse) {
    try {
        const res = await _fetch('/api/auth/google', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ credential: credentialResponse.credential }),
        });
        if (res.ok) {
            currentUser = await res.json();
            _onAuthSuccess();
        } else {
            const err = await res.json().catch(() => ({}));
            _showAuthError('google', err.detail || 'Google login failed');
        }
    } catch {
        _showAuthError('google', 'Network error — is the harness running?');
    }
}

function _showLoginScreen() {
    document.getElementById('login-overlay').classList.remove('hidden');

    // Wire up login tab switching
    document.querySelectorAll('.login-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.login-tab').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.auth-panel').forEach(p => p.classList.add('hidden'));
            btn.classList.add('active');
            const panel = document.getElementById(`auth-panel-${btn.dataset.authTab}`);
            if (panel) panel.classList.remove('hidden');
            if (btn.dataset.authTab === 'user') {
                _initGoogleAuth();
            }
        });
    });

    // Wire up admin login form
    const form = document.getElementById('admin-login-form');
    if (form && !form.dataset.wired) {
        form.dataset.wired = '1';
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = form.querySelector('button[type="submit"]');
            btn.disabled = true;
            btn.textContent = 'Signing in…';
            try {
                const res = await _fetch('/api/auth/admin/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        username: document.getElementById('admin-username').value,
                        password: document.getElementById('admin-password').value,
                    }),
                });
                if (res.ok) {
                    currentUser = await res.json();
                    _onAuthSuccess();
                } else {
                    const err = await res.json().catch(() => ({}));
                    _showAuthError('admin', err.detail || 'Invalid credentials');
                }
            } catch {
                _showAuthError('admin', 'Network error — is the harness running?');
            } finally {
                btn.disabled = false;
                btn.textContent = 'Sign in as Admin';
            }
        });
    }
}

function _showAuthError(type, msg) {
    const el = document.getElementById(`${type}-login-error`);
    if (el) { el.textContent = msg; el.classList.remove('hidden'); }
}

function _onAuthSuccess() {
    // Hide login overlay
    document.getElementById('login-overlay').classList.add('hidden');

    // Show user info in navbar
    const roleEl = document.getElementById('nav-user-role');
    const idEl = document.getElementById('nav-user-id');
    const infoEl = document.getElementById('nav-user-info');
    if (roleEl) { roleEl.textContent = currentUser.role; roleEl.className = `nav-user-role role-${currentUser.role}`; }
    if (idEl) idEl.textContent = currentUser.userId;
    if (infoEl) infoEl.classList.remove('hidden');

    // Apply role-based tab visibility immediately; metrics refresh reinforces it.
    applySessionVisibility(currentUser.role === 'user');

    // Load data (idempotent — called after login and on page load if already authed)
    fetchMetrics();
    startMetricsAutoRefresh();

    // My API Token section — user sessions only
    if (currentUser.role === 'user') {
        initMyTokenSection();
    } else {
        document.getElementById('my-token-section')?.classList.add('hidden');
    }
}

async function logout() {
    stopMetricsAutoRefresh();
    await _fetch('/api/auth/logout', { method: 'POST' }).catch(() => {});
    currentUser = null;
    // Reset navbar
    const infoEl = document.getElementById('nav-user-info');
    if (infoEl) infoEl.classList.add('hidden');
    // Restore all tabs (so admin tabs reappear on next admin login)
    document.querySelectorAll('.admin-only-tab').forEach(tab => { tab.style.display = ''; });
    document.querySelectorAll('.user-only-element').forEach(el => { el.style.display = ''; });
    // Re-activate dashboard tab
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    const dashBtn = document.querySelector('[data-target="dashboard"]');
    const dashContent = document.getElementById('dashboard');
    if (dashBtn) dashBtn.classList.add('active');
    if (dashContent) dashContent.classList.add('active');
    // Clear error messages
    document.querySelectorAll('.login-error').forEach(el => el.classList.add('hidden'));
    document.getElementById('admin-password').value = '';
    _showLoginScreen();
    _initGoogleAuth();
}

function startMetricsAutoRefresh() {
    if (metricsRefreshTimer) return;
    metricsRefreshTimer = window.setInterval(() => {
        if (currentUser && !document.hidden) {
            fetchMetrics();
        }
    }, 15000);
}

function stopMetricsAutoRefresh() {
    if (!metricsRefreshTimer) return;
    window.clearInterval(metricsRefreshTimer);
    metricsRefreshTimer = null;
}

function applySessionVisibility(isUserScope) {
    document.querySelectorAll('.admin-only-tab').forEach(tab => {
        tab.style.display = isUserScope ? 'none' : '';
    });
    document.querySelectorAll('.user-only-element').forEach(el => {
        el.style.display = isUserScope ? '' : 'none';
    });

    const adminOnlyTabs = ['tab-spenders', 'tab-teams', 'tab-clients'];
    adminOnlyTabs.forEach((tabId) => {
        const tabButton = document.querySelector(`.dashboard-tab-btn[data-target="${tabId}"]`);
        const tabPane = document.getElementById(tabId);
        if (tabButton) tabButton.style.display = isUserScope ? 'none' : '';
        if (tabPane) tabPane.style.display = isUserScope ? 'none' : '';
        if (isUserScope && tabButton?.classList.contains('active')) {
            tabButton.classList.remove('active');
            tabPane?.classList.remove('active');
            document.querySelector('.dashboard-tab-btn[data-target="tab-activity"]')?.classList.add('active');
            document.getElementById('tab-activity')?.classList.add('active');
        }
    });
}

// =============================================================================
// End auth module
// =============================================================================

document.addEventListener('vantage:ready', () => {
    // Global active configuration state
    window.activeConfig = null;

    // Kick off auth check — data loading happens inside _onAuthSuccess()
    initAuth();

    // Main Tab switching logic
    const tabBtns = document.querySelectorAll('.tab-btn');
    const tabContents = document.querySelectorAll('.tab-content');

    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            tabBtns.forEach(b => b.classList.remove('active'));
            tabContents.forEach(c => c.classList.remove('active'));

            btn.classList.add('active');
            const target = document.getElementById(btn.dataset.target);
            target.classList.add('active');

            // Lazy load configurations when Design Hub is focused
            if (btn.dataset.target === 'design') {
                if (!window.activeConfig) {
                    loadAdminConfig();
                }
            }
        });
    });

    // Sub-tab switching across all sections
    const subTabBtns = document.querySelectorAll('.sub-tab-btn[data-subtarget]');

    subTabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const parentTabContent = btn.closest('.tab-content') || document;
            const parentSubTabBtns = parentTabContent.querySelectorAll('.sub-tab-btn');
            const parentSubTabContents = parentTabContent.querySelectorAll('.sub-tab-content');

            parentSubTabBtns.forEach(b => b.classList.remove('active'));
            parentSubTabContents.forEach(c => c.classList.remove('active'));

            btn.classList.add('active');
            const target = document.getElementById(btn.dataset.subtarget);
            if (target) {
                target.classList.add('active');
            }
        });
    });

    // Playground listeners
    const btnSimulate = document.getElementById('btn-simulate-route');
    if (btnSimulate) {
        btnSimulate.addEventListener('click', () => {
            triggerPlaygroundSimulation(false);
        });
    }

    const btnRun = document.getElementById('btn-run-prompt');
    if (btnRun) {
        btnRun.addEventListener('click', () => {
            triggerPlaygroundSimulation(true);
        });
    }

    // Rules & Policies listeners
    const btnSavePolicies = document.getElementById('btn-save-policies');
    if (btnSavePolicies) {
        btnSavePolicies.addEventListener('click', saveAdminConfig);
    }

    // ROI Calculator listeners
    setupROICalculator();

    // Context Diagram listeners
    setupContextDiagram();

    // Setup Search & Filter event listeners for Activity Stream
    const activitySearch = document.getElementById('activity-search');
    const filterRoute = document.getElementById('filter-route');
    const filterClient = document.getElementById('filter-client');
    const filterLimit = document.getElementById('filter-limit');
    const filterStreamType = document.getElementById('filter-stream-type');
    const auditSearch = document.getElementById('audit-search');
    const productivitySearch = document.getElementById('productivity-search');

    if (activitySearch) activitySearch.addEventListener('input', filterAndRenderActivityTable);
    if (filterRoute) filterRoute.addEventListener('change', filterAndRenderActivityTable);
    if (filterClient) filterClient.addEventListener('change', filterAndRenderActivityTable);
    if (filterLimit) filterLimit.addEventListener('change', () => {
        filterAndRenderActivityTable();
        renderAuditTable();
        renderProductivityTable();
    });
    if (filterStreamType) filterStreamType.addEventListener('change', filterAndRenderActivityTable);
    if (auditSearch) auditSearch.addEventListener('input', renderAuditTable);
    if (productivitySearch) productivitySearch.addEventListener('input', renderProductivityTable);

    document.getElementById('insight-period')?.addEventListener('change', (e) => {
        dashboardFilterState.days = Number(e.target.value) || 30;
        fetchMetrics();
    });
    document.getElementById('insight-team')?.addEventListener('change', (e) => {
        dashboardFilterState.team = e.target.value || 'all';
        fetchMetrics();
    });
    document.getElementById('insight-tool')?.addEventListener('change', (e) => {
        dashboardFilterState.tool = e.target.value || 'all';
        fetchMetrics();
    });
    document.getElementById('btn-refresh-insights')?.addEventListener('click', fetchMetrics);
});

// Cache global metrics fetch
async function fetchMetrics() {
    try {
        const params = new URLSearchParams({ days: String(dashboardFilterState.days) });
        if (dashboardFilterState.team !== 'all') params.set('teamId', dashboardFilterState.team);
        if (dashboardFilterState.tool !== 'all') params.set('client', dashboardFilterState.tool);
        const res = await _fetch(`/metrics?${params.toString()}`);
        const data = await res.json();
        renderDashboard(data);
    } catch (err) {
        console.error("Failed to fetch metrics:", err);
        const grid = document.getElementById('metrics-grid');
        if (grid) {
            grid.innerHTML = '<div class="loading">Failed to load metrics.</div>';
        }
    }
}

function renderDashboard(data) {
    const isUserScope = data._scope === 'user' || currentUser?.role === 'user';
    const scopedId = data._scopedUserId || (isUserScope ? currentUser?.userId : '') || '';

    // 0. Scope banner — shown only for user sessions
    let banner = document.getElementById('scope-banner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'scope-banner';
        const hero = document.querySelector('.content-container .hero');
        if (hero) hero.after(banner);
    }
    if (isUserScope) {
        banner.className = 'scope-banner scope-banner-user';
        banner.innerHTML = `👤 Showing your personal data only — <strong>${escapeHtml(scopedId)}</strong>`;
        banner.style.display = '';
    } else {
        banner.style.display = 'none';
    }

    applySessionVisibility(isUserScope);

    const perspective = document.getElementById('dashboard-perspective');
    const title = document.getElementById('dashboard-title');
    const subtitle = document.getElementById('dashboard-subtitle');
    if (perspective) perspective.textContent = isUserScope ? 'Developer perspective' : 'Leadership perspective';
    if (title) title.textContent = isUserScope ? 'My Engineering AI Insights' : 'Engineering AI Intelligence';
    if (subtitle) subtitle.textContent = isUserScope
        ? 'Your AI usage, delivery outcomes, focus, flow and governance signals.'
        : 'Delivery outcomes, AI adoption, engineering flow, cost and governance.';

    // 1. Render Metrics Grid
    const metricLabel = isUserScope ? 'My AI Requests' : 'AI Requests';
    const insight = data.insights || {};
    const pr = insight.pr || {};
    const time = insight.time || {};
    const tokens = insight.tokens || {};
    const previous = data.comparison || {};
    const grid = document.getElementById('metrics-grid');
    if (grid) {
        grid.innerHTML = `
            <div class="metric-card insight-kpi">
                <div class="metric-label">${metricLabel}</div>
                <div class="metric-value">${data.totalRequests || 0}</div>
                <div class="metric-context">${formatDelta(data.totalRequests || 0, previous.totalRequests || 0)} vs previous period</div>
            </div>
            <div class="metric-card insight-kpi">
                <div class="metric-label">${isUserScope ? 'My Tokens' : 'Tokens Consumed'}</div>
                <div class="metric-value blue">${formatCompact(tokens.total || 0)}</div>
                <div class="metric-context">${formatCompact(tokens.prompt || 0)} prompt · ${formatCompact(tokens.completion || 0)} completion</div>
            </div>
            <div class="metric-card insight-kpi">
                <div class="metric-label">${isUserScope ? 'My Active Coding Time' : 'Active Coding Time'}</div>
                <div class="metric-value green">${formatDuration((time.activeCodingSec || 0) * 1000)}</div>
                <div class="metric-context">${formatDelta(time.activeCodingSec || 0, previous.activeCodingSec || 0)} vs previous period</div>
            </div>
            <div class="metric-card insight-kpi">
                <div class="metric-label">PRs Merged</div>
                <div class="metric-value purple">${pr.merged || 0}</div>
                <div class="metric-context">${pr.medianCycleTimeHours == null ? 'Awaiting lifecycle data' : `${pr.medianCycleTimeHours}h median cycle time`}</div>
            </div>
            <div class="metric-card insight-kpi">
                <div class="metric-label">Actual AI Cost</div>
                <div class="metric-value">$${(data.actualCostUsd || 0).toFixed(2)}</div>
                <div class="metric-context">$${(data.estimatedSavingsUsd || 0).toFixed(2)} estimated savings</div>
            </div>
            <div class="metric-card insight-kpi">
                <div class="metric-label">Governance Events</div>
                <div class="metric-value orange">${insight.security?.policyBlocks || 0}</div>
                <div class="metric-context">${insight.security?.redactions || 0} privacy redactions applied</div>
            </div>
        `;
    }

    renderPhase1Insights(data, isUserScope);

    // 2. Render Top Spenders / My Usage
    const usersCard = document.querySelector('#users-table')?.closest('.table-card');
    const usersHeading = usersCard?.querySelector('h2');
    if (usersHeading) usersHeading.textContent = isUserScope ? 'My Usage' : 'Top Spenders';

    const usersTbody = document.querySelector('#users-table tbody');
    if (usersTbody) {
        if (data.byUser && data.byUser.length > 0) {
            usersTbody.innerHTML = data.byUser.map(u => `
                <tr>
                    <td style="font-family: monospace; color: var(--text-muted)">${escapeHtml(u.userId)}</td>
                    <td>${escapeHtml(u.teamId)}</td>
                    <td>${u.requests}</td>
                    <td>${u.cloudRequests}</td>
                    <td style="color: var(--route-local); font-weight: 600;">$${(u.estimatedSavingsUsd || 0).toFixed(2)}</td>
                    <td style="color: var(--text-muted)">$${(u.actualCostUsd || 0).toFixed(2)}</td>
                </tr>
            `).join('');
        } else {
            usersTbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding: 2rem;">No data available yet</td></tr>';
        }
    }

    // 3. Per-Team Breakdown — hidden for user scope (team data is admin-only)
    const teamsCard = document.querySelector('#teams-table')?.closest('.table-card');
    if (teamsCard) teamsCard.style.display = isUserScope ? 'none' : '';

    const teamsTbody = document.querySelector('#teams-table tbody');
    if (teamsTbody && !isUserScope) {
        if (data.byTeam && data.byTeam.length > 0) {
            teamsTbody.innerHTML = data.byTeam.map(t => `
                <tr>
                    <td><strong>${escapeHtml(t.teamId)}</strong></td>
                    <td>${t.requests}</td>
                    <td>${t.localRequests}</td>
                    <td>${t.cloudRequests}</td>
                    <td style="color: var(--route-local); font-weight: 600;">$${(t.estimatedSavingsUsd || 0).toFixed(2)}</td>
                    <td style="color: var(--text-muted)">$${(t.actualCostUsd || 0).toFixed(2)}</td>
                </tr>
            `).join('');
        } else {
            teamsTbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding: 2rem;">No data available</td></tr>';
        }
    }

    // 4. Cache activities globally and trigger filter/render
    const personalRecords = (rows) => {
        if (!isUserScope || !scopedId) return rows || [];
        return (rows || []).filter((row) => row.userId === scopedId);
    };
    window.recentActivities = personalRecords(data.recent);
    window.auditActivities = personalRecords(data.audit);
    window.productivityActivities = personalRecords(data.productivity);
    window.usageActivities = personalRecords(data.usage);
    window.costActivities = personalRecords(data.cost);
    window.cursorUsageActivities = personalRecords(data.cursorUsage);
    window.cursorCommitActivities = personalRecords(data.cursorCommit);
    window.agentTurnActivities = personalRecords(data.agentTurn);
    window.sessionOutcomeActivities = personalRecords(data.sessionOutcome);
    window.rawEventActivities = personalRecords(data.rawEvents);
    window.byClient = isUserScope ? [] : (data.byClient || []);

    renderByClientTable(window.byClient);
    populateClientFilterOptions(window.byClient);
    renderAuditTable();
    renderProductivityTable();
    filterAndRenderActivityTable();
}

function formatCompact(value) {
    return new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(Number(value || 0));
}

function formatDuration(ms) {
    const seconds = Math.max(0, Number(ms || 0) / 1000);
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    return `${(seconds / 3600).toFixed(1)}h`;
}

function formatDelta(current, previous) {
    current = Number(current || 0); previous = Number(previous || 0);
    if (!previous) return current ? 'New activity' : 'No change';
    const pct = ((current - previous) / previous) * 100;
    return `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`;
}

function setFilterOptions(selectId, values, labelKey) {
    const select = document.getElementById(selectId);
    if (!select) return;
    const current = select.value || 'all';
    const existing = new Set([...select.options].map(o => o.value));
    values.forEach((value) => {
        const key = typeof value === 'string' ? value : value[labelKey];
        if (!key || key === 'unknown' || existing.has(key)) return;
        const option = document.createElement('option');
        option.value = key;
        option.textContent = labelKey === 'tool' ? formatClientName(key) : key;
        select.appendChild(option);
    });
    select.value = [...select.options].some(o => o.value === current) ? current : 'all';
}

function renderPhase1Insights(data, isUserScope) {
    const insight = data.insights || {};
    const tools = insight.byTool || [];
    const models = insight.byModel || [];
    const pr = insight.pr || {};
    const flow = insight.flow || {};
    const quality = insight.dataQuality || {};
    const security = insight.security || {};

    setFilterOptions('insight-team', data.byTeam || [], 'teamId');
    setFilterOptions('insight-tool', tools, 'tool');

    const maxTokens = Math.max(1, ...tools.map(x => x.tokens || 0));
    const adoption = document.getElementById('tool-adoption-bars');
    if (adoption) adoption.innerHTML = tools.length ? tools.map(x => `
        <div class="bar-row"><div class="bar-meta"><strong>${escapeHtml(formatClientName(x.tool))}</strong><span>${x.activeUsers} users · ${formatCompact(x.tokens)} tokens</span></div><div class="bar-track"><span style="width:${Math.max(4, 100 * (x.tokens || 0) / maxTokens)}%"></span></div></div>
    `).join('') : '<div class="empty-insight">No AI tool usage reported for this period.</div>';

    const signals = document.getElementById('leadership-signals');
    if (signals) signals.innerHTML = [
        ['Active developers', insight.activeDevelopers || 0, data.comparison?.activeDevelopers || 0],
        ['Tokens consumed', formatCompact(insight.tokens?.total || 0), formatCompact(data.comparison?.tokens || 0)],
        ['AI cost', `$${(data.actualCostUsd || 0).toFixed(2)}`, `$${(data.comparison?.actualCostUsd || 0).toFixed(2)}`],
    ].map(x => `<div class="signal-row"><span>${x[0]}</span><strong>${x[1]}</strong><small>prior ${x[2]}</small></div>`).join('');

    const delivery = document.getElementById('delivery-snapshot');
    if (delivery) delivery.innerHTML = snapshotItems([
        ['Opened', pr.opened || 0], ['Reviewed', pr.reviewed || 0], ['Merged', pr.merged || 0],
        ['Median cycle', pr.medianCycleTimeHours == null ? '—' : `${pr.medianCycleTimeHours}h`],
    ]);

    const attention = document.getElementById('attention-list');
    if (attention) {
        const notices = [];
        if ((quality.versionedCoveragePercent || 0) < 80) notices.push(['warning', `Only ${quality.versionedCoveragePercent || 0}% of events use the versioned schema.`]);
        if ((security.policyBlocks || 0) > 0) notices.push(['security', `${security.policyBlocks} cloud egress attempts were policy-blocked.`]);
        if (!(pr.opened || 0)) notices.push(['info', 'No PR lifecycle events yet; delivery insights are incomplete.']);
        if (!notices.length) notices.push(['good', 'No immediate coverage or governance concerns detected.']);
        attention.innerHTML = notices.map(x => `<div class="attention-item ${x[0]}">${escapeHtml(x[1])}</div>`).join('');
    }

    const toolBody = document.querySelector('#tool-insights-table tbody');
    if (toolBody) toolBody.innerHTML = tools.length ? tools.map(x => `<tr><td><strong>${escapeHtml(formatClientName(x.tool))}</strong></td><td>${x.activeUsers}</td><td>${x.events}</td><td>${formatCompact(x.tokens)}</td><td>${formatDuration(x.durationMs)}</td><td>$${(x.costUsd || 0).toFixed(2)}</td></tr>`).join('') : emptyRow(6, 'No AI usage telemetry for this period.');

    const tokenMix = document.getElementById('token-mix');
    const totalTokens = Math.max(1, insight.tokens?.total || 0);
    if (tokenMix) tokenMix.innerHTML = `<div class="token-total">${formatCompact(insight.tokens?.total || 0)}<span>total tokens</span></div><div class="mix-row"><span>Prompt</span><strong>${Math.round(100 * (insight.tokens?.prompt || 0) / totalTokens)}%</strong></div><div class="mix-row"><span>Completion</span><strong>${Math.round(100 * (insight.tokens?.completion || 0) / totalTokens)}%</strong></div>`;

    const modelBody = document.querySelector('#model-insights-table tbody');
    if (modelBody) modelBody.innerHTML = models.length ? models.map(x => `<tr><td>${escapeHtml(x.model)}</td><td>${x.requests}</td><td>${formatCompact(x.tokens)}</td><td>$${(x.costUsd || 0).toFixed(2)}</td></tr>`).join('') : emptyRow(4, 'No model telemetry available.');

    const prBody = document.querySelector('#pr-insights-table tbody');
    const prs = data.pullRequests || [];
    if (prBody) prBody.innerHTML = prs.length ? prs.map(x => `<tr><td><strong>#${escapeHtml(x.pullRequestId)}</strong><br><small>${escapeHtml(truncateText(x.title, 36))}</small></td><td>${escapeHtml(x.repoId)}</td><td>${escapeHtml(x.userId)}</td><td>${escapeHtml(formatClientName(x.tool))}</td><td><span class="status-pill status-${escapeHtml(x.status)}">${escapeHtml(x.status)}</span></td><td>${x.reviewCount}</td><td>${x.filesChanged}</td><td>${formatCompact(x.tokens)}</td><td>${x.cycleTimeHours == null ? '—' : `${x.cycleTimeHours}h`}</td></tr>`).join('') : emptyRow(9, 'No PR lifecycle events yet. The schema is ready for a source-control integration.');

    document.getElementById('flow-summary').innerHTML = snapshotItems([
        ['Active coding', formatDuration((insight.time?.activeCodingSec || 0) * 1000)],
        ['Sessions', flow.sessions || 0], ['Rework signal', `${flow.reworkRatePercent || 0}%`],
        ['Context switches', flow.contextSwitches || 0], ['AI wait', formatDuration(insight.time?.aiWaitMs || 0)],
    ]);
    document.getElementById('flow-guidance').innerHTML = `<strong>${isUserScope ? 'Use this for self-reflection.' : 'Use this to locate system friction.'}</strong><p>Active time, rework and file switching depend on task type and repository complexity. They should be correlated with PR outcomes and never used as an individual leaderboard.</p>`;

    document.getElementById('security-summary').innerHTML = snapshotItems([['Policy blocks', security.policyBlocks || 0], ['Sensitive signals', security.sensitiveEvents || 0], ['Redactions', security.redactions || 0]]);
    document.getElementById('security-guidance').innerHTML = '<strong>Interpretation matters.</strong><p>A policy block is a prevented event, not a confirmed exposure. A redaction means privacy processing was applied before persistence or forwarding.</p>';

    const qualityBars = document.getElementById('quality-bars');
    if (qualityBars) qualityBars.innerHTML = qualityBar('Versioned event coverage', quality.versionedCoveragePercent || 0) + qualityBar('Automatic collection coverage', quality.automaticCoveragePercent || 0) + qualityBar('High-confidence attribution', quality.highConfidenceAttributionPercent || 0);
    document.getElementById('quality-notes').innerHTML = `<strong>${quality.totalEvents || 0} events in scope</strong><p>${quality.attributedEvents || 0} have an explicit attribution confidence. A zero may mean no activity or missing collection; use this view before drawing conclusions.</p>`;
}

function snapshotItems(items) { return items.map(x => `<div class="snapshot-item"><span>${escapeHtml(x[0])}</span><strong>${escapeHtml(String(x[1]))}</strong></div>`).join(''); }
function emptyRow(cols, text) { return `<tr><td colspan="${cols}" class="empty-insight">${escapeHtml(text)}</td></tr>`; }
function qualityBar(label, value) { return `<div class="bar-row"><div class="bar-meta"><strong>${escapeHtml(label)}</strong><span>${Number(value).toFixed(1)}%</span></div><div class="bar-track quality"><span style="width:${Math.max(1, Math.min(100, value))}%"></span></div></div>`; }

// =============================================================================
// My API Token section
// =============================================================================

async function initMyTokenSection() {
    const section = document.getElementById('my-token-section');
    if (!section) return;

    section.classList.remove('hidden');

    // Load and render existing tokens
    await refreshMyTokens();

    // Generate new token
    document.getElementById('btn-generate-token')?.addEventListener('click', async () => {
        const btn = document.getElementById('btn-generate-token');
        btn.disabled = true;
        btn.textContent = 'Generating…';
        try {
            const res = await fetch('/api/auth/my-token', { method: 'POST' });
            if (!res.ok) { const e = await res.json(); alert(e.detail || 'Failed'); return; }
            const data = await res.json();
            showTokenReveal(data.token);
            await refreshMyTokens();
        } finally {
            btn.disabled = false;
            btn.textContent = '+ Generate New Token';
        }
    });

    // Copy token
    document.getElementById('btn-copy-token')?.addEventListener('click', () => {
        const input = document.getElementById('token-reveal-value');
        if (!input) return;
        navigator.clipboard.writeText(input.value).then(() => {
            const btn = document.getElementById('btn-copy-token');
            btn.textContent = 'Copied!';
            setTimeout(() => { btn.textContent = 'Copy'; }, 2000);
        });
    });

    // Dismiss reveal
    document.getElementById('btn-dismiss-token')?.addEventListener('click', () => {
        hideTokenReveal();
    });
}

async function refreshMyTokens() {
    const tbody = document.querySelector('#my-tokens-table tbody');
    if (!tbody) return;
    try {
        const res = await fetch('/api/auth/my-tokens');
        if (!res.ok) { tbody.innerHTML = '<tr><td colspan="6">Failed to load tokens</td></tr>'; return; }
        const data = await res.json();
        renderMyTokensTable(data.tokens || []);
    } catch {
        tbody.innerHTML = '<tr><td colspan="6">Error loading tokens</td></tr>';
    }
}

function renderMyTokensTable(tokens) {
    const tbody = document.querySelector('#my-tokens-table tbody');
    if (!tbody) return;
    if (tokens.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:2rem;color:var(--text-muted)">
            No active tokens — click <strong>Generate New Token</strong> to create one.
        </td></tr>`;
        return;
    }
    tbody.innerHTML = tokens.map(t => {
        const created = t.createdAt ? t.createdAt.replace('T', ' ').slice(0, 19) : '—';
        const lastUsed = t.lastUsedAt ? t.lastUsedAt.replace('T', ' ').slice(0, 19) : 'Never';
        const scopes = (t.scopes || []).join(', ');
        return `<tr>
            <td><code class="token-prefix">${escapeHtml(t.tokenPrefix)}</code></td>
            <td><span class="scope-chip">${escapeHtml(scopes)}</span></td>
            <td style="color:var(--text-muted)">${escapeHtml(t.note || '—')}</td>
            <td style="color:var(--text-muted);font-size:0.82rem">${escapeHtml(created)}</td>
            <td style="color:var(--text-muted);font-size:0.82rem">${escapeHtml(lastUsed)}</td>
            <td>
                <button class="btn-rotate-token" data-token-id="${escapeHtml(t.id)}">
                    🔄 Rotate
                </button>
            </td>
        </tr>`;
    }).join('');

    // Wire rotate buttons
    tbody.querySelectorAll('.btn-rotate-token').forEach(btn => {
        btn.addEventListener('click', async () => {
            if (!confirm('Rotate this token? The old token will stop working immediately.')) return;
            btn.disabled = true;
            btn.textContent = 'Rotating…';
            try {
                const res = await fetch(`/api/auth/my-token/${btn.dataset.tokenId}/rotate`, { method: 'POST' });
                if (!res.ok) { const e = await res.json(); alert(e.detail || 'Failed'); return; }
                const data = await res.json();
                showTokenReveal(data.token);
                await refreshMyTokens();
            } finally {
                btn.disabled = false;
                btn.textContent = '🔄 Rotate';
            }
        });
    });
}

function showTokenReveal(token) {
    const box = document.getElementById('token-reveal-box');
    const input = document.getElementById('token-reveal-value');
    if (!box || !input) return;
    input.value = token;
    box.classList.remove('hidden');
    input.select();
}

function hideTokenReveal() {
    const box = document.getElementById('token-reveal-box');
    const input = document.getElementById('token-reveal-value');
    if (box) box.classList.add('hidden');
    if (input) input.value = '';
}

// =============================================================================
// End My API Token section
// =============================================================================

const CLIENT_DISPLAY_NAMES = {
    'claude-code': 'Claude Code',
    'cursor': 'Cursor',
    'codex': 'Codex',
    'continue': 'Continue',
    'api': 'API Client',
    'unknown': 'Unknown',
};

function eventTokenCount(x) {
    const explicit = Number(x?.totalTokens || x?.estimatedTokens || 0);
    if (explicit > 0) return explicit;
    if (x?.type === 'cursor_usage' && x?.hashCount) {
        return Number(x.hashCount) * 25;
    }
    return 0;
}

function eventDurationMs(x) {
    const explicit = Number(x?.durationMs || x?.latencyMs || 0);
    if (explicit > 0) return explicit;
    if (x?.type === 'cursor_usage' && x?.cursorTimestampMin != null && x?.cursorTimestampMax != null) {
        const delta = Number(x.cursorTimestampMax) - Number(x.cursorTimestampMin);
        if (delta > 0) return delta;
    }
    return 0;
}

function formatRawEventSummary(x) {
    const type = x.type || 'inference';
    const tokens = eventTokenCount(x);
    switch (type) {
        case 'usage':
            return `${x.model || 'unknown'} · ${formatCompact(tokens)} tokens`;
        case 'cursor_usage':
            return `${x.source || 'ai'} · ${x.model || 'unknown'} · ${formatCompact(tokens)} tok`;
        case 'inference':
            return `${(x.route || 'unknown').toUpperCase()} · ${x.model || 'unknown'}`;
        case 'cursor_commit':
            return `commit ${(x.commitHash || '').slice(0, 8)} · AI ${x.v2AiPercentage ?? '?'}%`;
        case 'cost':
            return `${(x.route || 'unknown').toUpperCase()} · ${x.model || 'unknown'}`;
        case 'agent_turn':
            return `Turn · ${x.turnType || 'unknown'}`;
        case 'productivity':
            return `Session · +${x.linesAdded || 0}/−${x.linesDeleted || 0} lines`;
        case 'search_performed':
            return `${x.searchMode || 'search'} · ${x.provider || 'unknown'} · ${truncateText(x.query || '', 40)}`;
        default:
            return x.action || x.reason || x.source || type;
    }
}

function formatRawEventDetails(x) {
    const type = x.type || 'inference';
    const tokens = eventTokenCount(x);
    const duration = eventDurationMs(x);
    switch (type) {
        case 'usage':
            return `${x.promptTokens || 0} prompt / ${x.completionTokens || 0} completion · ${formatDuration(duration)}`;
        case 'cursor_usage': {
            const file = (x.fileName || '').split(/[/\\]/).pop() || '—';
            const est = x.tokenEstimateSource ? ' (est.)' : '';
            return `${x.hashCount || 0} hashes · ${x.uniqueFileCount || 0} files · ${file}${est}`;
        }
        case 'inference':
            return `${formatCompact(tokens)} tokens · ${Math.round(duration)}ms · ${x.reason || ''}`;
        case 'cursor_commit':
            return `${x.branchName || ''} · +${x.linesAdded || 0}/−${x.linesDeleted || 0} lines`;
        case 'cost':
            return `est $${(x.estimatedCostUsd || 0).toFixed(4)} → actual $${(x.actualCostUsd || 0).toFixed(4)}`;
        case 'agent_turn':
            return `${x.cwd || ''} · ${x.lastAssistantMessageLen || 0} chars · ${formatDuration(duration)}`;
        case 'productivity':
            return `${x.activeCodingTimeSec || 0}s active · ${x.filesModifiedCount || 0} files`;
        case 'search_performed': {
            const urls = (x.sources || []).map((s) => s.url).filter(Boolean).slice(0, 2).join(' | ');
            return `${x.resultCount || 0} result(s) · ${formatDuration(duration)}${urls ? ` · ${urls}` : ''}`;
        }
        default:
            return x.details || truncateText(JSON.stringify(x), 120);
    }
}

function formatClientName(client) {
    const key = (client || 'unknown').toLowerCase();
    return CLIENT_DISPLAY_NAMES[key] || (key.charAt(0).toUpperCase() + key.slice(1));
}

function renderByClientTable(byClient) {
    const tbody = document.querySelector('#clients-table tbody');
    if (!tbody) return;

    const list = byClient || [];
    if (list.length > 0) {
        tbody.innerHTML = list.map((c) => `
            <tr>
                <td><strong>${escapeHtml(formatClientName(c.client))}</strong></td>
                <td>${c.events || 0}</td>
                <td style="color: var(--route-local); font-weight: 600;">+${c.linesAdded || 0}</td>
                <td style="color: var(--warning); font-weight: 600;">−${c.linesDeleted || 0}</td>
                <td>${c.tokens || 0}</td>
                <td style="color: var(--text-muted)">$${(c.actualCostUsd || 0).toFixed(2)}</td>
                <td style="color: var(--route-local); font-weight: 600;">$${(c.savingsUsd || 0).toFixed(2)}</td>
            </tr>
        `).join('');
    } else {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding: 2rem;">No client telemetry yet — start the Telemetry MCP server from a supported client.</td></tr>';
    }
}

function populateClientFilterOptions(byClient) {
    const select = document.getElementById('filter-client');
    if (!select) return;
    const current = select.value || 'all';

    const clients = (byClient || []).map((c) => c.client).filter(Boolean);
    select.innerHTML = ['<option value="all">All Clients</option>']
        .concat(clients.map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(formatClientName(c))}</option>`))
        .join('');

    if (clients.includes(current) || current === 'all') {
        select.value = current;
    }
}

function getTelemetryLimit() {
    const limitFilter = document.getElementById('filter-limit')?.value || '20';
    if (limitFilter === 'all') return null;
    return parseInt(limitFilter, 10);
}

function applyLimit(list, limit) {
    // Every caller passes an already-reversed (newest-first) array, so the
    // "last N" the user wants is the first N elements here, not the tail —
    // slice(-limit) was returning the OLDEST N of the batch instead.
    if (!limit) return list;
    return list.slice(0, limit);
}

function truncateText(value, maxLen = 80) {
    const text = String(value || '');
    if (text.length <= maxLen) return text;
    return `${text.slice(0, maxLen - 1)}…`;
}


function rawPayloadTitle(value) {
    try {
        return JSON.stringify(value || {}, null, 2);
    } catch {
        return String(value || '');
    }
}

function renderAuditTable() {
    const tbody = document.querySelector('#audit-table tbody');
    if (!tbody || !window.auditActivities) return;

    const searchQuery = (document.getElementById('audit-search')?.value || '').toLowerCase().trim();
    const limit = getTelemetryLimit();
    let list = [...window.auditActivities];

    if (searchQuery) {
        list = list.filter((x) => {
            return (
                (x.userId || '').toLowerCase().includes(searchQuery) ||
                (x.teamId || '').toLowerCase().includes(searchQuery) ||
                (x.action || '').toLowerCase().includes(searchQuery) ||
                (x.target || '').toLowerCase().includes(searchQuery) ||
                (x.details || '').toLowerCase().includes(searchQuery) ||
                (x.status || '').toLowerCase().includes(searchQuery)
            );
        });
    }

    const reversed = applyLimit([...list].reverse(), limit);

    if (reversed.length > 0) {
        tbody.innerHTML = reversed.map((x) => `
            <tr>
                <td>${formatTimestamp(x.timestamp)}</td>
                <td class="mono-cell">${escapeHtml(x.userId || '')}</td>
                <td>${escapeHtml(x.teamId || '')}</td>
                <td><span class="badge-audit">${escapeHtml(x.action || '')}</span></td>
                <td class="mono-cell">${escapeHtml(truncateText(x.target, 60))}</td>
                <td>${escapeHtml(x.status || '')}</td>
                <td class="details-cell">${escapeHtml(truncateText(x.details, 100))}</td>
            </tr>
        `).join('');
    } else {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding: 2rem;">No audit events yet — start the Telemetry MCP server and edit files in your workspace.</td></tr>';
    }
}

function renderProductivityTable() {
    const tbody = document.querySelector('#productivity-table tbody');
    if (!tbody || !window.productivityActivities) return;

    const searchQuery = (document.getElementById('productivity-search')?.value || '').toLowerCase().trim();
    const limit = getTelemetryLimit();
    let list = [...window.productivityActivities];

    if (searchQuery) {
        list = list.filter((x) => {
            const files = Array.isArray(x.filesModifiedList) ? x.filesModifiedList.join(' ') : '';
            return (
                (x.userId || '').toLowerCase().includes(searchQuery) ||
                (x.teamId || '').toLowerCase().includes(searchQuery) ||
                files.toLowerCase().includes(searchQuery)
            );
        });
    }

    const reversed = applyLimit([...list].reverse(), limit);

    if (reversed.length > 0) {
        tbody.innerHTML = reversed.map((x) => {
            const files = Array.isArray(x.filesModifiedList) ? x.filesModifiedList : [];
            const filePreview = files.length ? files.slice(0, 3).join(', ') + (files.length > 3 ? ` (+${files.length - 3} more)` : '') : '—';
            const activeSec = x.activeCodingTimeSec ?? x.durationSec ?? x.activeSeconds;
            const outcome = x.outcome || '—';
            const outcomeClass = outcome === 'committed' ? 'status-merged' : outcome === 'abandoned' ? 'status-closed' : '';
            return `
                <tr>
                    <td>${formatTimestamp(x.occurredAt || x.timestamp)}</td>
                    <td class="mono-cell">${escapeHtml(x.userId || '')}</td>
                    <td>${escapeHtml(x.teamId || '')}</td>
                    <td>${activeSec != null ? formatDuration(Number(activeSec) * 1000) : '—'}</td>
                    <td style="color: var(--route-local); font-weight: 600;">+${x.linesAdded || 0}</td>
                    <td style="color: var(--warning); font-weight: 600;">−${x.linesDeleted || 0}</td>
                    <td>${x.filesModifiedCount || files.length || 0}</td>
                    <td>${outcome === '—' ? '—' : `<span class="status-pill ${outcomeClass}">${escapeHtml(outcome)}</span>`}</td>
                    <td class="details-cell">${escapeHtml(truncateText(filePreview, 120))}</td>
                </tr>
            `;
        }).join('');
    } else {
        tbody.innerHTML = '<tr><td colspan="9" style="text-align:center; padding: 2rem;">No productivity sessions yet — file edits in the watched workspace will appear here.</td></tr>';
    }
}

function isCursorActivityEvent(row) {
    const client = (row.client || '').toLowerCase();
    if (row.streamType === 'cursor' || row.streamType === 'cursor_commit') {
        return true;
    }
    return client === 'cursor';
}

function buildUnifiedEvents() {
    if (Array.isArray(window.rawEventActivities)) {
        return window.rawEventActivities.map((x) => {
            const type = x.type || 'inference';
            return {
                timestamp: x.occurredAt || x.timestamp,
                userId: x.userId,
                teamId: x.teamId,
                streamType: type,
                client: x.client,
                summary: formatRawEventSummary(x),
                details: formatRawEventDetails(x),
                tokens: eventTokenCount(x),
                durationMs: eventDurationMs(x),
                raw: x,
            };
        }).sort((a, b) => new Date(a.timestamp || 0) - new Date(b.timestamp || 0));
    }

    const inference = (window.recentActivities || []).map((x) => ({
        timestamp: x.timestamp,
        userId: x.userId,
        teamId: x.teamId,
        streamType: 'inference',
        client: x.client,
        summary: `${(x.route || 'unknown').toUpperCase()} · ${x.model || 'unknown'}`,
        details: `${x.reason || ''} · ${x.estimatedTokens || 0} tokens · ${x.latencyMs || 0}ms`,
        raw: x,
    }));

    const cursorAi = (window.cursorUsageActivities || []).map((x) => {
        const file = (x.fileName || '').split('/').pop() || '—';
        return {
            timestamp: x.timestamp,
            userId: x.userId,
            teamId: x.teamId,
            streamType: 'cursor',
            client: x.client || 'cursor',
            summary: `${x.source || 'ai'} · ${x.model || 'unknown'}`,
            details: x.details || `${file} · ${x.fileExtension || ''} ext`,
            raw: x,
        };
    });

    const cursorCommits = (window.cursorCommitActivities || []).map((x) => ({
        timestamp: x.timestamp,
        userId: x.userId,
        teamId: x.teamId,
        streamType: 'cursor_commit',
        client: x.client || 'cursor',
        summary: `commit ${(x.commitHash || '').slice(0, 8)} · AI ${x.v2AiPercentage || '?'}%`,
        details: `${x.branchName || ''} · +${x.linesAdded || 0}/−${x.linesDeleted || 0} lines`,
        raw: x,
    }));

    const audit = (window.auditActivities || []).map((x) => ({
        timestamp: x.timestamp,
        userId: x.userId,
        teamId: x.teamId,
        streamType: 'audit',
        client: x.client,
        summary: x.action || 'audit',
        details: `${x.target || ''} · ${x.details || ''}`,
        raw: x,
    }));

    const productivity = (window.productivityActivities || []).map((x) => ({
        timestamp: x.timestamp,
        userId: x.userId,
        teamId: x.teamId,
        streamType: 'productivity',
        client: x.client,
        summary: `Session · +${x.linesAdded || 0}/−${x.linesDeleted || 0} lines`,
        details: x.details || `${x.activeCodingTimeSec || 0}s active · ${x.filesModifiedCount || 0} files`,
        raw: x,
    }));

    const usage = (window.usageActivities || []).map((x) => ({
        timestamp: x.timestamp,
        userId: x.userId,
        teamId: x.teamId,
        streamType: 'usage',
        client: x.client,
        summary: `${x.model || 'unknown'} · ${x.totalTokens || 0} tokens`,
        details: x.details || `${x.promptTokens || 0} prompt / ${x.completionTokens || 0} completion · ${x.durationMs || 0}ms`,
        raw: x,
    }));

    const cost = (window.costActivities || []).map((x) => ({
        timestamp: x.timestamp,
        userId: x.userId,
        teamId: x.teamId,
        streamType: 'cost',
        client: x.client,
        summary: `${(x.route || 'unknown').toUpperCase()} · ${x.model || 'unknown'}`,
        details: x.details || `est $${(x.estimatedCostUsd || 0).toFixed(4)} → actual $${(x.actualCostUsd || 0).toFixed(4)} · saved $${(x.savingsUsd || 0).toFixed(4)}`,
        raw: x,
    }));

    const agentTurn = (window.agentTurnActivities || []).map((x) => ({
        timestamp: x.timestamp,
        userId: x.userId,
        teamId: x.teamId,
        streamType: 'agent_turn',
        client: x.client || 'codex',
        summary: `Turn · ${x.turnType || 'unknown'}`,
        details: x.details || `${x.cwd || ''} · ${x.lastAssistantMessageLen || 0} chars`,
        raw: x,
    }));

    const sessionOutcome = (window.sessionOutcomeActivities || []).map((x) => ({
        timestamp: x.timestamp,
        userId: x.userId,
        teamId: x.teamId,
        streamType: 'session_outcome',
        client: x.client,
        summary: `Session ${x.outcome || 'unknown'} · +${x.linesAdded || 0}/−${x.linesDeleted || 0} lines`,
        details: x.details || `${x.activeCodingTimeSec || 0}s active · ${x.commitHash ? `commit ${x.commitHash.slice(0, 8)}` : 'no commit'}`,
        raw: x,
    }));

    return [...inference, ...cursorAi, ...cursorCommits, ...audit, ...productivity, ...usage, ...cost, ...agentTurn, ...sessionOutcome].sort((a, b) => {
        return new Date(a.timestamp || 0) - new Date(b.timestamp || 0);
    });
}

function updateStreamPanels(streamType) {
    const inferencePanel = document.getElementById('inference-stream-panel');
    const unifiedPanel = document.getElementById('unified-stream-panel');
    const routeControls = document.querySelectorAll('.filter-route-group');
    const clientControls = document.querySelectorAll('.filter-client-group');
    const useUnified = streamType !== 'inference';

    if (inferencePanel) inferencePanel.classList.toggle('hidden', useUnified);
    if (unifiedPanel) unifiedPanel.classList.toggle('hidden', !useUnified);
    routeControls.forEach((el) => {
        el.classList.toggle('hidden', useUnified);
    });
    clientControls.forEach((el) => {
        el.classList.toggle('hidden', false);
    });
}

function filterAndRenderActivityTable() {
    const streamType = document.getElementById('filter-stream-type')?.value || 'inference';
    updateStreamPanels(streamType);

    if (streamType === 'inference') {
        renderInferenceActivityTable();
        return;
    }

    renderUnifiedActivityTable(streamType);
}

function renderInferenceActivityTable() {
    const activityTbody = document.querySelector('#activity-table tbody');
    if (!activityTbody || !window.recentActivities) return;

    const searchQuery = (document.getElementById('activity-search')?.value || '').toLowerCase().trim();
    const routeFilter = document.getElementById('filter-route')?.value || 'all';
    const clientFilter = document.getElementById('filter-client')?.value || 'all';
    const limit = getTelemetryLimit();

    let list = [...window.recentActivities];

    if (searchQuery) {
        list = list.filter(x => {
            return (
                (x.userId || '').toLowerCase().includes(searchQuery) ||
                (x.teamId || '').toLowerCase().includes(searchQuery) ||
                (x.model || '').toLowerCase().includes(searchQuery) ||
                (x.reason || '').toLowerCase().includes(searchQuery) ||
                (x.route || '').toLowerCase().includes(searchQuery) ||
                (x.client || '').toLowerCase().includes(searchQuery)
            );
        });
    }

    if (routeFilter !== 'all') {
        list = list.filter(x => (x.route || '').toLowerCase() === routeFilter);
    }

    if (clientFilter !== 'all') {
        list = list.filter((x) => (x.client || 'unknown').toLowerCase() === clientFilter);
    }

    const reversed = applyLimit([...list].reverse(), limit);

    if (reversed.length > 0) {
        activityTbody.innerHTML = reversed.map(x => {
            const ts = formatTimestamp(x.timestamp);
            const route = x.route || '';
            const routeClass = route === 'cloud' ? 'badge-cloud' : 'badge-local';
            const fallbackHtml = x.fallbackUsed ? '<span class="badge-warning">Yes</span>' : '<span class="badge-none">No</span>';
            const reqSavings = Math.max(0, (x.estimatedAllCloudCostUsd || 0) - (x.actualCostUsd || 0));

            return `
                <tr>
                    <td>${ts}</td>
                    <td class="mono-cell">${escapeHtml(x.userId || '')}</td>
                    <td>${escapeHtml(x.teamId || '')}</td>
                    <td><strong>${escapeHtml(formatClientName(x.client))}</strong></td>
                    <td><span class="${routeClass}">${escapeHtml(route.toUpperCase())}</span></td>
                    <td class="mono-cell brand-text">${escapeHtml(x.model || '')}</td>
                    <td class="details-cell">${escapeHtml(x.reason || '')}</td>
                    <td>${x.estimatedTokens || 0}</td>
                    <td>${fallbackHtml}</td>
                    <td>${x.repoContextHits || 0}</td>
                    <td>${x.latencyMs || 0}</td>
                    <td style="color: var(--route-local); font-weight: 600;">$${reqSavings.toFixed(2)}</td>
                </tr>
            `;
        }).join('');
    } else {
        activityTbody.innerHTML = '<tr><td colspan="12" style="text-align:center; padding: 2rem;">No matching gateway routing activity</td></tr>';
    }
}

function renderUnifiedActivityTable(streamType) {
    const tbody = document.querySelector('#unified-activity-table tbody');
    if (!tbody) return;

    const searchQuery = (document.getElementById('activity-search')?.value || '').toLowerCase().trim();
    const clientFilter = document.getElementById('filter-client')?.value || 'all';
    const limit = getTelemetryLimit();
    let list = buildUnifiedEvents();

    if (streamType === 'cursor') {
        list = list.filter(isCursorActivityEvent);
    } else if (streamType !== 'all') {
        list = list.filter((x) => x.streamType === streamType);
    }

    if (clientFilter !== 'all') {
        list = list.filter((x) => (x.client || '').toLowerCase() === clientFilter);
    }

    if (searchQuery) {
        list = list.filter((x) => {
            return (
                (x.userId || '').toLowerCase().includes(searchQuery) ||
                (x.teamId || '').toLowerCase().includes(searchQuery) ||
                (x.summary || '').toLowerCase().includes(searchQuery) ||
                (x.details || '').toLowerCase().includes(searchQuery) ||
                (x.streamType || '').toLowerCase().includes(searchQuery)
            );
        });
    }

    const reversed = applyLimit([...list].reverse(), limit);
    const badgeClass = {
        inference: 'badge-inference',
        audit: 'badge-audit',
        productivity: 'badge-productivity',
        cursor: 'badge-cloud',
        cursor_commit: 'badge-local',
        usage: 'badge-inference',
        cost: 'badge-warning',
        search_performed: 'badge-inference',
        agent_turn: 'badge-productivity',
        session_outcome: 'badge-local',
    };

    if (reversed.length > 0) {
        tbody.innerHTML = reversed.map((x) => {
            const fullPayload = rawPayloadTitle(x.raw || x);
            return `
                <tr>
                    <td>${formatTimestamp(x.timestamp)}</td>
                    <td class="mono-cell">${escapeHtml(x.userId || '')}</td>
                    <td>${escapeHtml(x.teamId || '')}</td>
                    <td><span class="${badgeClass[x.streamType] || 'badge-none'}">${escapeHtml(x.streamType)}</span></td>
                    <td>${x.client ? escapeHtml(formatClientName(x.client)) : '???'}</td>
                    <td>${escapeHtml(truncateText(x.summary, 80))}</td>
                    <td>${formatCompact(x.tokens || eventTokenCount(x.raw || x))}</td>
                    <td>${formatDuration(x.durationMs || eventDurationMs(x.raw || x))}</td>
                    <td class="details-cell raw-payload-cell" title="${escapeHtml(fullPayload)}">${escapeHtml(truncateText(x.details, 120))}</td>
                </tr>
            `;
        }).join('');
    } else {
        tbody.innerHTML = '<tr><td colspan="9" style="text-align:center; padding: 2rem;">No matching client telemetry events</td></tr>';
    }
}

// ── Design Hub Policies & Configuration loading ───────────────────────────────
async function loadAdminConfig() {
    try {
        const res = await fetch('/admin/config');
        if (!res.ok) throw new Error(`Status ${res.status}`);
        window.activeConfig = await res.json();
        
        // Populate inputs
        populateRulesEditor();
    } catch (err) {
        console.error("Failed to load gateway configuration:", err);
        const footer = document.querySelector('.policies-deploy-footer');
        if (footer) {
            footer.innerHTML = `<span class="status-msg error">Failed to connect to gateway configuration API. Verify the server is running.</span>`;
        }
    }
}

function populateRulesEditor() {
    if (!window.activeConfig) return;
    const config = window.activeConfig.config;
    const policy = window.activeConfig.policy;
    const rules = window.activeConfig.rules;

    // Toggle states
    document.getElementById('policy-cloud-enabled').checked = policy.cloudEnabled !== false;
    document.getElementById('policy-fallback-enabled').checked = rules.fallback?.enabled !== false;
    document.getElementById('policy-fallback-unhelpful').checked = rules.fallback?.triggerOnUnhelpfulResponse !== false;
    document.getElementById('policy-cache-enabled').checked = config.cache?.enabled !== false;

    // Numeric states
    document.getElementById('policy-min-complex-len').value = rules.complex?.minPromptLength || 2000;
    document.getElementById('policy-max-unhelpful-len').value = rules.unhelpful?.maxResponseLength || 500;

    // Chips
    renderChips('sensitive-patterns-editor', policy.sensitivePatterns || [], 'sensitive');
    renderChips('complex-keywords-editor', rules.complex?.keywords || [], 'complex');
    renderChips('freshness-terms-editor', rules.freshness?.terms || [], 'freshness');
    renderChips('unhelpful-patterns-editor', rules.unhelpful?.patterns || [], 'unhelpful');

    // Setup chip adding listeners (once)
    if (!window.chipListenersAdded) {
        setupChipEditorAdders();
        window.chipListenersAdded = true;
    }
}

function renderChips(containerId, list, type) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = '';
    
    list.forEach((item, index) => {
        const chip = document.createElement('span');
        chip.className = `chip-tag ${type === 'sensitive' ? 'sensitive-tag' : type === 'freshness' ? 'freshness-tag' : ''}`;
        chip.innerHTML = `
            <span>${escapeHtml(item)}</span>
            <button class="chip-remove-btn" type="button" data-index="${index}">&times;</button>
        `;
        chip.querySelector('.chip-remove-btn').addEventListener('click', () => {
            list.splice(index, 1);
            renderChips(containerId, list, type);
        });
        container.appendChild(chip);
    });
}

function setupChipEditorAdders() {
    const wireAdd = (inputId, btnId, list, containerId, type) => {
        const input = document.getElementById(inputId);
        const btn = document.getElementById(btnId);
        if (!input || !btn) return;

        const addAction = () => {
            const val = input.value.trim();
            if (val && !list.includes(val)) {
                list.push(val);
                input.value = '';
                renderChips(containerId, list, type);
            }
        };

        btn.addEventListener('click', addAction);
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                addAction();
            }
        });
    };

    const policy = window.activeConfig.policy;
    const rules = window.activeConfig.rules;

    wireAdd('input-new-sensitive', 'btn-add-sensitive', policy.sensitivePatterns, 'sensitive-patterns-editor', 'sensitive');
    wireAdd('input-new-complex', 'btn-add-complex', rules.complex.keywords, 'complex-keywords-editor', 'complex');
    wireAdd('input-new-freshness', 'btn-add-freshness', rules.freshness.terms, 'freshness-terms-editor', 'freshness');
    wireAdd('input-new-unhelpful', 'btn-add-unhelpful', rules.unhelpful.patterns, 'unhelpful-patterns-editor', 'unhelpful');
}

async function saveAdminConfig() {
    const statusMsg = document.getElementById('save-status-msg');
    if (statusMsg) {
        statusMsg.className = 'status-msg active';
        statusMsg.innerText = 'Saving configuration...';
    }

    try {
        const policy = window.activeConfig.policy;
        const rules = window.activeConfig.rules;

        // Collect current toggles & values
        policy.cloudEnabled = document.getElementById('policy-cloud-enabled').checked;
        rules.fallback.enabled = document.getElementById('policy-fallback-enabled').checked;
        rules.fallback.triggerOnUnhelpfulResponse = document.getElementById('policy-fallback-unhelpful').checked;
        window.activeConfig.config.cache.enabled = document.getElementById('policy-cache-enabled').checked;
        
        rules.complex.minPromptLength = parseInt(document.getElementById('policy-min-complex-len').value) || 2000;
        rules.unhelpful.maxResponseLength = parseInt(document.getElementById('policy-max-unhelpful-len').value) || 500;

        const response = await fetch('/admin/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ policy, rules })
        });

        if (!response.ok) {
            throw new Error(`Server returned code ${response.status}`);
        }

        if (statusMsg) {
            statusMsg.className = 'status-msg success active';
            statusMsg.innerText = '✅ Policies saved and hot-reloaded successfully!';
            setTimeout(() => { statusMsg.classList.remove('active'); }, 4000);
        }

        // Refresh dashboard metrics in case cache was reset or reload affected endpoints
        fetchMetrics();
    } catch (err) {
        console.error("Failed to save configuration:", err);
        if (statusMsg) {
            statusMsg.className = 'status-msg error active';
            statusMsg.innerText = `❌ Error: ${err.message || err}`;
            setTimeout(() => { statusMsg.classList.remove('active'); }, 6000);
        }
    }
}

// ── Playground Routing Simulation Engine ──────────────────────────────────────────
function triggerPlaygroundSimulation(runLive = false) {
    const userPrompt = document.getElementById('playground-user-prompt').value.trim();
    const systemPrompt = document.getElementById('playground-system-prompt').value.trim();
    const requestedModel = document.getElementById('playground-model').value;
    const terminal = document.getElementById('playground-terminal-output');

    if (!userPrompt) {
        alert("Please enter a User Prompt to test.");
        return;
    }

    if (!window.activeConfig) {
        // Fallback placeholder config if server hasn't loaded config yet
        window.activeConfig = {
            config: { ollama: { fastModel: "qwen3.5:9b", mainModel: "qwen3-coder:30b", midModel: "qwen2.5-coder:14b" }, cloud: { provider: "gemini", geminiModel: "gemini-2.5-flash" } },
            policy: { cloudEnabled: true, sensitivePatterns: ["api[_-]?key", "password", "secret"] },
            rules: { complex: { keywords: ["refactor", "architect"], minPromptLength: 2000 }, freshness: { terms: ["latest", "current", "today"] } }
        };
    }

    // Clear and reset pipeline flows
    resetPipelineSteps();
    
    // Animate scanning states
    setStepState('step-sensitive', 'active-step', 'Analyzing text patterns against guardrail regexes...');
    
    // Simulate steps sequentially
    setTimeout(() => {
        const fullText = (systemPrompt ? systemPrompt + "\n" : "") + userPrompt;
        const simulationResult = runLocalSimulationEngine(fullText, requestedModel);
        
        // 1. Sensitive Guardrail Results
        if (simulationResult.stepSensitive.success) {
            setStepState('step-sensitive', 'success', 'Passed: No sensitive credentials patterns detected.');
        } else {
            setStepState('step-sensitive', 'failed', simulationResult.stepSensitive.desc);
        }

        // 2. Start Complexity Step
        setStepState('step-complexity', 'active-step', 'Checking character count and programming logic terms...');
        
        setTimeout(() => {
            if (simulationResult.stepComplexity.success) {
                setStepState('step-complexity', 'success', simulationResult.stepComplexity.desc);
            } else {
                setStepState('step-complexity', 'success', simulationResult.stepComplexity.desc);
            }

            // 3. Start Freshness Step
            setStepState('step-freshness', 'active-step', 'Scanning queries for real-time freshness terms...');

            setTimeout(() => {
                if (simulationResult.stepFreshness.matched) {
                    setStepState('step-freshness', 'success', simulationResult.stepFreshness.desc);
                } else {
                    setStepState('step-freshness', 'success', 'Passed: Stale query context allowed.');
                }

                // 4. Decision
                setStepState('step-decision', 'active-step', 'Synthesizing pipeline variables...');

                setTimeout(() => {
                    const finalTier = simulationResult.decision.route === 'cloud' ? 'Cloud Escalation' : 'Local Tier';
                    const finalModel = simulationResult.decision.model;
                    setStepState('step-decision', 'success', `Target: ${finalTier} | Model: ${finalModel} (${simulationResult.decision.reason})`);

                    // Update Badge
                    document.getElementById('badge-route-taken').innerText = simulationResult.decision.route.toUpperCase();
                    document.getElementById('badge-route-taken').className = `metric-badge ${simulationResult.decision.route === 'cloud' ? 'badge-cloud' : 'badge-local'}`;

                    if (runLive) {
                        executePlaygroundPrompt(systemPrompt, userPrompt, requestedModel, simulationResult.decision.model);
                    } else {
                        terminal.innerHTML = `<div style="color: var(--route-local); font-weight: 600; margin-bottom: 0.5rem;">[Simulation Success] Routing Analysis:</div>` + 
                            `• Target route: <strong>${finalTier.toUpperCase()}</strong>\n` +
                            `• Selected Model: <strong>${finalModel}</strong>\n` +
                            `• Routing Reason: <em>${simulationResult.decision.reason}</em>\n\n` +
                            `<span style="color: var(--text-muted)">Click "Run Prompt" to execute the model and see actual outputs.</span>`;
                    }
                }, 500);
            }, 500);
        }, 500);
    }, 500);
}

function runLocalSimulationEngine(fullText, requestedModel) {
    const rules = window.activeConfig.rules;
    const policy = window.activeConfig.policy;
    const config = window.activeConfig.config;

    const fastModel = config.ollama?.fastModel || "qwen3.5:9b";
    const mainModel = config.ollama?.mainModel || "qwen3-coder:30b";
    const midModel = config.ollama?.midModel || "qwen2.5-coder:14b";
    const cloudModel = config.cloud?.geminiModel || "gemini-2.5-flash";

    let stepSensitive = { success: true, desc: "Passed: No sensitive credentials patterns detected." };
    let stepComplexity = { success: true, desc: "" };
    let stepFreshness = { matched: false, desc: "No freshness keywords matched." };
    let decision = { route: "", model: "", reason: "" };

    // 1. Sensitive Check
    const sensitivePatterns = policy.sensitivePatterns || [];
    let matchedSensitive = null;
    for (const pattern of sensitivePatterns) {
        try {
            const rx = new RegExp(pattern, 'i');
            if (rx.test(fullText)) {
                matchedSensitive = pattern;
                break;
            }
        } catch(e) {}
    }

    if (matchedSensitive) {
        stepSensitive = { success: false, desc: `Credential match: /${matchedSensitive}/ (Escalation Blocked)` };
        decision = {
            route: "local",
            model: fastModel,
            reason: "sensitive_content_local_only"
        };
    }

    // 2. Freshness Check
    const freshnessTerms = rules.freshness?.terms || [];
    let matchedFreshness = null;
    if (freshnessTerms.length > 0) {
        const pattern = "\\b(" + freshnessTerms.map(t => escapeRegExp(t)).join("|") + ")\\b";
        const rx = new RegExp(pattern, 'i');
        if (rx.test(fullText)) {
            const matches = fullText.match(rx);
            matchedFreshness = matches ? matches[0] : "keyword";
        }
    }

    if (matchedFreshness) {
        stepFreshness = { matched: true, desc: `Web Grounding required (matched "${matchedFreshness}")` };
    }

    // 3. Complexity Check
    const minComplexLen = parseInt(rules.complex?.minPromptLength || 2000);
    const complexKeywords = rules.complex?.keywords || [];
    let matchedComplexKeyword = null;
    if (complexKeywords.length > 0) {
        const pattern = "\\b(" + complexKeywords.map(k => escapeRegExp(k)).join("|") + ")\\b";
        const rx = new RegExp(pattern, 'i');
        if (rx.test(fullText)) {
            const matches = fullText.match(rx);
            matchedComplexKeyword = matches ? matches[0] : "keyword";
        }
    }

    const lengthComplex = fullText.length > minComplexLen;
    const isComplex = lengthComplex || matchedComplexKeyword;

    if (isComplex) {
        stepComplexity = { 
            success: true, 
            desc: lengthComplex 
                ? `Complex: Length (${fullText.length} chars) > limit (${minComplexLen})` 
                : `Complex: Matched keyword "${matchedComplexKeyword}"` 
        };
    } else {
        stepComplexity = {
            success: false,
            desc: `Standard: Length (${fullText.length} chars) is short.`
        };
    }

    // 4. Decision Selection
    if (!decision.route) {
        const localNames = [fastModel, mainModel, midModel];
        if (requestedModel && localNames.includes(requestedModel)) {
            decision = {
                route: "local",
                model: requestedModel,
                reason: "requested_local_model"
            };
        } else if (!isComplex) {
            const model = fullText.length < 800 ? fastModel : mainModel;
            decision = {
                route: "local",
                model: model,
                reason: fullText.length < 800 ? "short_prompt_fast_local" : "default_local_main"
            };
        } else {
            if (midModel && !matchedFreshness) {
                decision = {
                    route: "local",
                    model: midModel,
                    reason: "complex_prompt_mid_local"
                };
            } else if (policy.cloudEnabled) {
                decision = {
                    route: "cloud",
                    model: cloudModel,
                    reason: matchedFreshness ? "complex_prompt_freshness_escalation" : "complex_prompt_cloud_escalation"
                };
            } else {
                decision = {
                    route: "local",
                    model: mainModel,
                    reason: "complex_prompt_local_fallback"
                };
            }
        }
    }

    return {
        stepSensitive,
        stepComplexity,
        stepFreshness,
        decision
    };
}

function resetPipelineSteps() {
    const steps = ['step-sensitive', 'step-complexity', 'step-freshness', 'step-decision'];
    steps.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.className = 'pipeline-step pending';
            const desc = el.querySelector('.step-desc');
            if (desc) {
                if (id === 'step-sensitive') desc.innerText = 'Scanning for passwords, API keys, tokens...';
                if (id === 'step-complexity') desc.innerText = 'Evaluating token length and keyword signals...';
                if (id === 'step-freshness') desc.innerText = 'Checking for real-time keywords (SearXNG websearch or Gemini search grounding)...';
                if (id === 'step-decision') desc.innerText = 'Selecting target inference engine...';
            }
        }
    });

    document.getElementById('badge-route-taken').innerText = '—';
    document.getElementById('badge-route-taken').className = 'metric-badge';
    document.getElementById('badge-latency').innerText = '— ms';
    document.getElementById('badge-tokens').innerText = '— tokens';
}

function setStepState(stepId, state, text) {
    const el = document.getElementById(stepId);
    if (!el) return;
    el.className = `pipeline-step ${state}`;
    const desc = el.querySelector('.step-desc');
    if (desc) {
        desc.innerText = text;
    }
}

async function executePlaygroundPrompt(systemPrompt, userPrompt, requestedModel, targetModel) {
    const terminal = document.getElementById('playground-terminal-output');
    const stream = document.getElementById('playground-stream').checked;
    const userId = document.getElementById('playground-user-id').value.trim() || 'playground-user';
    const teamId = document.getElementById('playground-team-id').value.trim() || 'playground-team';
    
    terminal.innerHTML = '<span class="terminal-placeholder">Executing prompt on gateway...</span>';
    
    const messages = [];
    if (systemPrompt) {
        messages.push({ role: 'system', content: systemPrompt });
    }
    messages.push({ role: 'user', content: userPrompt });

    const payload = {
        model: requestedModel || 'vantage',
        messages: messages,
        stream: stream
    };

    const startTime = Date.now();

    try {
        const res = await fetch('/v1/chat/completions', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-User-Id': userId,
                'X-Team-Id': teamId,
                'X-Harness-Source': 'playground'
            },
            body: JSON.stringify(payload)
        });

        if (!res.ok) {
            const errJson = await res.json().catch(() => ({}));
            throw new Error(errJson.error?.message || errJson.error || `HTTP ${res.status}`);
        }

        if (stream) {
            terminal.innerHTML = '<span class="terminal-text"></span><span class="terminal-cursor" style="display: inline-block; width: 8px; height: 15px; background: #60a5fa; margin-left: 2px; vertical-align: middle; animation: blink 1s step-end infinite;"></span>';
            
            // Inject blink keyframes if not present
            if (!document.getElementById('terminal-blink-style')) {
                const style = document.createElement('style');
                style.id = 'terminal-blink-style';
                style.textContent = '@keyframes blink { from, to { opacity: 0; } 50% { opacity: 1; } }';
                document.head.appendChild(style);
            }

            const textSpan = terminal.querySelector('.terminal-text');
            const cursorSpan = terminal.querySelector('.terminal-cursor');
            
            const reader = res.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let buffer = '';
            let tokenCount = 0;

            while (true) {
                const { value, done } = await reader.read();
                if (done) break;
                
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop(); // keep remainder

                for (const line of lines) {
                    const cleaned = line.trim();
                    if (!cleaned) continue;
                    if (cleaned === 'data: [DONE]') continue;
                    
                    if (cleaned.startsWith('data: ')) {
                        try {
                            const data = JSON.parse(cleaned.slice(6));
                            const content = data.choices?.[0]?.delta?.content || '';
                            if (content) {
                                textSpan.textContent += content;
                                terminal.scrollTop = terminal.scrollHeight;
                                tokenCount++;
                            }
                        } catch(e) {
                            // ignore json parse errors on partial chunks
                        }
                    }
                }
            }

            if (cursorSpan) cursorSpan.remove();

            const totalTime = Date.now() - startTime;
            document.getElementById('badge-latency').innerText = `${totalTime} ms`;
            document.getElementById('badge-tokens').innerText = `~${tokenCount} tokens`;
        } else {
            const data = await res.json();
            const text = data.choices?.[0]?.message?.content || data.response || '';
            terminal.innerText = text;
            
            const totalTime = Date.now() - startTime;
            document.getElementById('badge-latency').innerText = `${totalTime} ms`;
            
            const usage = data.usage?.total_tokens || countTokensFallback(text + userPrompt);
            document.getElementById('badge-tokens').innerText = `${usage} tokens`;
        }

        // Trigger metrics reload on dashboard
        fetchMetrics();
    } catch (err) {
        console.error("Playground invocation failed:", err);
        terminal.innerHTML = `<span style="color: var(--danger)">Execution Error: ${err.message || err}</span>`;
        document.getElementById('badge-latency').innerText = `Error`;
    }
}

// ── ROI Simulator Formulas & Event wire-up ────────────────────────────────────
function setupROICalculator() {
    const inputs = ['calc-requests', 'calc-avg-tokens', 'calc-cloud-cost', 'calc-local-ratio', 'calc-cache-hit-rate'];
    inputs.forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;

        const handleUpdate = () => {
            // Update labels
            if (id === 'calc-local-ratio') {
                document.getElementById('calc-local-ratio-val').innerText = `${el.value}%`;
            }
            if (id === 'calc-cache-hit-rate') {
                document.getElementById('calc-cache-hit-rate-val').innerText = `${el.value}%`;
            }
            recalculateROI();
        };

        el.addEventListener('input', handleUpdate);
        el.addEventListener('change', handleUpdate);
    });

    // Run first calculation on load
    setTimeout(recalculateROI, 500);
}

function recalculateROI() {
    const requests = parseFloat(document.getElementById('calc-requests').value) || 0;
    const avgTokens = parseFloat(document.getElementById('calc-avg-tokens').value) || 0;
    const cloudCostPer1k = parseFloat(document.getElementById('calc-cloud-cost').value) || 0;
    const localRatio = parseFloat(document.getElementById('calc-local-ratio').value) / 100;
    const cacheHitRate = parseFloat(document.getElementById('calc-cache-hit-rate').value) / 100;

    const totalTokens = requests * avgTokens;
    const baselineCost = (totalTokens / 1000) * cloudCostPer1k;
    
    // Caching matches first: saving entire call cost.
    // Local handles a percentage of the remaining cache-misses.
    // Cloud takes the final residual load.
    const cloudRequests = requests * (1 - cacheHitRate) * (1 - localRatio);
    const vantageCost = (cloudRequests * avgTokens / 1000) * cloudCostPer1k;

    const savings = Math.max(0, baselineCost - vantageCost);
    const savingsPercent = baselineCost > 0 ? (savings / baselineCost) * 100 : 0;

    // Render numbers
    document.getElementById('val-baseline-cost').innerText = `$${baselineCost.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
    document.getElementById('val-vantage-cost').innerText = `$${vantageCost.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
    document.getElementById('val-savings-amount').innerText = `$${savings.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
    document.getElementById('val-savings-percentage').innerText = `${savingsPercent.toFixed(1)}%`;
    document.getElementById('val-savings-progress-fill').style.width = `${savingsPercent}%`;
}

// ── UTILITIES ─────────────────────────────────────────────────────────────────
function countTokensFallback(str) {
    if (!str) return 0;
    // Fast estimate: ~4 chars per token
    return Math.max(1, Math.round(str.length / 4));
}

function escapeRegExp(string) {
    return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function escapeHtml(unsafe) {
    if (!unsafe) return '';
    return String(unsafe)
         .replace(/&/g, "&amp;")
         .replace(/</g, "&lt;")
         .replace(/>/g, "&gt;")
         .replace(/"/g, "&quot;")
         .replace(/'/g, "&#039;");
}

function formatTimestamp(ts) {
    if (!ts) return '';
    try {
        const dt = new Date(ts);
        return dt.toLocaleString();
    } catch (e) {
        return ts;
    }
}

// ── Interactive Context Diagram Logic ──────────────────────────────────────────
function setupContextDiagram() {
    const nodes = document.querySelectorAll('.diagram-node');
    const infoPanel = document.getElementById('diagram-info-panel');
    if (!nodes.length || !infoPanel) return;

    // Component details dictionary
    const nodeDetails = {
        ide: {
            title: "💻 IDE Extensions (VS Code & Cursor)",
            icon: "💻",
            description: "Developer tools communicate with the Vantage Harness gateway via an OpenAI-compatible API on port <code>50123</code>. The editor behaves as if it is connecting to a remote cloud API, allowing seamless integration with no changes to the developer workflow.",
            interfaces: ["POST /v1/chat/completions", "Server-Sent Events (SSE)"],
            rules: [
                "Clients send requests to <code>http://localhost:50123/v1</code>.",
                "Uses standard chat models or custom model name mappings.",
                "Compatible with Continue extension, Cline, and Cursor base URL overrides."
            ]
        },
        gateway: {
            title: "🛡️ Vantage Harness Gateway Router",
            icon: "🛡️",
            description: "FastAPI app (<code>request_gateway.py</code>) that owns routing, auth, admin/config-reload endpoints, and app-wide state (config, caches, Ollama health flag). Each request is handed to <strong>Completion Service</strong> (<code>completion_service.py</code>), which runs the pipeline: ASGI compression → exact cache → semantic cache → intent detection → BM25 repo context / SearXNG websearch → output shaper → model dispatch → store response → SSE stream.",
            interfaces: ["FastAPI engine", "Completion Service", "Rules Engine", "Policy Guardrails", "Model Router", "BM25 Scorer", "Compression Middleware", "Output Shaper", "Semantic Cache"],
            rules: [
                "<strong>Completion Service</strong>: owns the per-request orchestration; the gateway builds a fresh context (config, caches, health flag) and hands it in on every call.",
                "<strong>ASGI Compression</strong>: rolling window + SmartCrush + code-block dedup applied before any route handler.",
                "Checks for sensitive credentials and blocks cloud escalation when matched.",
                "Extracts complexity markers and freshness terms to route to the right local tier, SearXNG websearch, or cloud.",
                "<strong>BM25 Corpus Scorer</strong>: IDF-weighted repo file ranking replaces the old keyword counter.",
                "<strong>Output Shaper</strong>: appends byte-stable verbosity instructions to cloud system prompts (all providers).",
                "<strong>Semantic Cache</strong>: cosine similarity lookup via nomic-embed-text before inference (~5–20 ms)."
            ]
        },
        local: {
            title: "🧠 Local Inference Stack (Ollama)",
            icon: "🧠",
            description: "Executes lightweight, secure, and private code generation models directly on local developer hardware (GPUs/CPUs). This stack is highly optimized and results in a $0.00 cloud call unit cost. Also hosts nomic-embed-text for semantic cache embeddings.",
            interfaces: ["Ollama API (:11434)", "Ollama Model Puller", "Health Check Monitoring", "nomic-embed-text (Semantic Cache)"],
            rules: [
                "<strong>qwen2.5-coder:7b</strong> (<code>ollama.fastModel</code>): handles short questions (&lt; 800 chars) and queries flagged by sensitive policy.",
                "<strong>qwen3.5:9b</strong> (<code>ollama.mainModel</code>): primary engine for standard coding requests (800 - 2000 chars).",
                "<strong>qwen2.5-coder:7b</strong> (<code>ollama.midModel</code>): serves highly complex prompts (refactoring, architecture) that don't need real-time data.",
                "<strong>nomic-embed-text:latest</strong>: embedding model used by the Semantic Cache to generate query vectors.",
                "All three model names are config-driven (<code>config/vantage.config.json</code>) — edit via Admin &gt; Config.",
                "Automatically falls back to cloud routing if consecutive health check runs fail."
            ]
        },
        postgres: {
            title: "🐘 PostgreSQL",
            icon: "🐘",
            description: "System-of-record for the gateway, replacing the old metrics.jsonl + auth.sqlite split. Reached through an asyncpg connection pool initialised on FastAPI startup (<code>app/db.py</code>) and torn down on shutdown.",
            interfaces: ["asyncpg connection pool", "metrics table", "api_tokens table", "admins table", "ui_sessions table"],
            rules: [
                "<strong>metrics</strong>: every inference/cache-hit/telemetry record from <code>observability.add_metric()</code> — falls back to <code>data/metrics.jsonl</code> only if the DB insert fails.",
                "<strong>api_tokens</strong>: per-user/team bearer tokens issued via the <code>/admin/tokens</code> endpoints.",
                "<strong>admins</strong> &amp; <strong>ui_sessions</strong>: dashboard login accounts and session state (<code>auth.py</code> / <code>ui_auth.py</code>).",
                "Runs as the <code>db</code> service in docker-compose; the gateway container depends on its healthcheck before starting.",
                "Connection string is config-driven: <code>database.url</code> or the <code>DATABASE_URL</code> env override."
            ]
        },
        searxng: {
            title: "🌐 SearXNG Web Search",
            icon: "🌐",
            description: "Self-hosted metasearch engine used for the gateway's <code>websearch</code> route. When a query needs real-time information and the search-first policy applies, the gateway calls SearXNG directly, injects the results as context, then falls through to local dispatch — no cloud LLM call needed just for a lookup.",
            interfaces: ["SearXNG JSON API (:8080)", "app/websearch.py — WebSearchClient"],
            rules: [
                "Runs as the <code>searxng</code> sibling container; reachable at <code>http://searxng:8080</code> inside docker-compose via the <code>WEBSEARCH_BASE_URL</code> env override.",
                "Config-gated: <code>websearch.enabled</code> (off by default) and <code>websearch.provider: \"searxng\"</code> in <code>vantage.config.json</code>.",
                "Not health-gated at startup — the gateway must still boot cleanly even if SearXNG is slow to warm up or disabled.",
                "Results are formatted and injected into the prompt by <code>context_engineering.enrich_body_with_websearch_context()</code>, same pattern as repo context injection."
            ]
        },
        cloud: {
            title: "☁️ Cloud Escalation & Web Search Grounding",
            icon: "☁️",
            description: "Commercial high-power cloud APIs used to handle extremely complex instructions or search-grounded freshness requests that local models cannot successfully fulfill.",
            interfaces: ["Gemini Developer API", "Google Search Tool", "Cursor SDK Subprocess"],
            rules: [
                "Invokes <strong>gemini-2.5-flash</strong> for complex tasks when local fallback rules trigger.",
                "Enables Google Search Grounding dynamically for prompts containing freshness keywords.",
                "Protects against temporary outages using exponential backoff and jitter retries (default 3 attempts)."
            ]
        },
        observability: {
            title: "📊 Observability, Cache & Metrics",
            icon: "📊",
            description: "Monitors overall efficiency, savings rate, and routes execution data in real-time. Features a two-layer cache hierarchy that bypasses model execution entirely for redundant or semantically similar requests.",
            interfaces: ["PostgreSQL metrics table", "data/metrics.jsonl (fallback)", "FinOps Savings API", "Exact Cache (LRU/Redis)", "Semantic Cache (cosine similarity)"],
            rules: [
                "<strong>Exact Cache</strong>: SHA-256(model + messages) → LRU or Redis. ~0 ms lookup. First cache layer.",
                "<strong>Semantic Cache</strong>: embeds raw user query with nomic-embed-text, cosine similarity ≥ 0.92 returns cached response. ~5–20 ms. Second cache layer.",
                "Both caches are populated on every successful inference response and consulted in order on subsequent requests.",
                "Records token counts, routing tiers, latency, actual costs, and estimated baseline costs to the <strong>PostgreSQL</strong> <code>metrics</code> table; falls back to <code>data/metrics.jsonl</code> only if the DB insert fails.",
                "Calculates actual cost and estimated savings for real-time display in the dashboard."
            ]
        }
    };

    // Keep reference to paths to highlight connections
    const pathMappings = {
        ide: ['path-ide-gateway'],
        gateway: ['path-ide-gateway', 'path-gateway-local', 'path-gateway-cloud', 'path-gateway-observability', 'path-gateway-postgres', 'path-gateway-searxng'],
        local: ['path-gateway-local'],
        cloud: ['path-gateway-cloud'],
        observability: ['path-gateway-observability'],
        postgres: ['path-gateway-postgres'],
        searxng: ['path-gateway-searxng']
    };

    nodes.forEach(node => {
        const nodeId = node.dataset.node;
        if (!nodeDetails[nodeId]) return;

        // Hover events
        node.addEventListener('mouseenter', () => {
            highlightNode(node, nodeId);
            displayNodeDetails(nodeId);
        });

        node.addEventListener('mouseleave', () => {
            resetNodeHighlights();
        });

        // Click event (for mobile or persistent display)
        node.addEventListener('click', (e) => {
            e.stopPropagation();
            highlightNode(node, nodeId, true);
            displayNodeDetails(nodeId);
        });
    });

    function highlightNode(targetNode, nodeId, isPersistent = false) {
        // Dim other nodes
        nodes.forEach(n => {
            if (n === targetNode) {
                n.style.opacity = '1';
                n.classList.add('focused-node');
            } else {
                n.style.opacity = '0.4';
                n.classList.remove('focused-node');
            }
        });

        // Highlight corresponding paths
        const pathsToHighlight = pathMappings[nodeId] || [];
        document.querySelectorAll('.conn-path').forEach(p => {
            if (pathsToHighlight.includes(p.id)) {
                p.classList.add('highlighted');
                // Set match-color based on target path connection
                if (p.id.includes('local')) {
                    p.style.stroke = '#10b981';
                } else if (p.id.includes('cloud')) {
                    p.style.stroke = '#6366f1';
                } else if (p.id.includes('observability')) {
                    p.style.stroke = '#f59e0b';
                } else if (p.id.includes('postgres')) {
                    p.style.stroke = '#4d8fc4';
                } else if (p.id.includes('searxng')) {
                    p.style.stroke = '#2dd4bf';
                } else {
                    p.style.stroke = 'var(--brand-primary)';
                }
            } else {
                p.classList.remove('highlighted');
                // Reset stroke
                if (p.id.includes('local')) {
                    p.style.stroke = '#059669';
                } else if (p.id.includes('cloud')) {
                    p.style.stroke = '#4f46e5';
                } else if (p.id.includes('observability')) {
                    p.style.stroke = '#f59e0b';
                } else if (p.id.includes('postgres')) {
                    p.style.stroke = '#336791';
                } else if (p.id.includes('searxng')) {
                    p.style.stroke = '#0d9488';
                } else {
                    p.style.stroke = '#9ca3af';
                }
            }
        });
    }

    function resetNodeHighlights() {
        nodes.forEach(n => {
            n.style.opacity = '1';
            n.classList.remove('focused-node');
        });

        document.querySelectorAll('.conn-path').forEach(p => {
            p.classList.remove('highlighted');
            // Reset stroke
            if (p.id.includes('local')) {
                p.style.stroke = '#059669';
            } else if (p.id.includes('cloud')) {
                p.style.stroke = '#4f46e5';
            } else if (p.id.includes('observability')) {
                p.style.stroke = '#f59e0b';
            } else if (p.id.includes('postgres')) {
                p.style.stroke = '#336791';
            } else if (p.id.includes('searxng')) {
                p.style.stroke = '#0d9488';
            } else {
                p.style.stroke = '#9ca3af';
            }
        });
    }

    function displayNodeDetails(nodeId) {
        const details = nodeDetails[nodeId];
        if (!details) return;

        const interfacesHtml = details.interfaces.map(i => `<li>${i}</li>`).join('');
        const rulesHtml = details.rules.map(r => `<li>${r}</li>`).join('');

        infoPanel.innerHTML = `
            <div class="diagram-info-card" style="text-align: left;">
                <div class="info-card-header" style="display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1rem; padding-bottom: 0.75rem; border-bottom: 1px solid var(--border-light);">
                    <span style="font-size: 2rem;">${details.icon}</span>
                    <div>
                        <h4 style="margin: 0; font-size: 1.25rem; font-weight: 700; color: var(--text-main);">${details.title}</h4>
                        <span style="font-size: 0.75rem; text-transform: uppercase; font-weight: 700; color: var(--brand-secondary); letter-spacing: 0.5px;">Component Architecture Details</span>
                    </div>
                </div>
                <div class="info-card-body">
                    <p style="margin-bottom: 1rem; line-height: 1.5; font-size: 0.92rem; color: var(--text-muted);">${details.description}</p>
                    
                    <div class="info-card-interfaces" style="margin-top: 1rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-main); margin-bottom: 0.5rem;">Core Sub-Modules &amp; Interfaces</h5>
                        <ul style="list-style: none; padding: 0; margin: 0 0 1.25rem 0; display: flex; flex-wrap: wrap; gap: 0.5rem;">
                            ${interfacesHtml}
                        </ul>
                    </div>
                    
                    <div class="info-card-rules">
                        <h5 style="font-size: 0.8rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-main); margin-bottom: 0.5rem;">Active Integration Rules</h5>
                        <ul style="padding-left: 1.25rem; margin: 0; line-height: 1.5; font-size: 0.9rem; color: var(--text-muted);">
                            ${rulesHtml}
                        </ul>
                    </div>
                </div>
            </div>
        `;
    }
}


// Dashboard Tabs Logic
document.addEventListener('click', function(e) {
    if (e.target.matches('.dashboard-tabs-header .dashboard-tab-btn')) {
        const btn = e.target;
        const targetId = btn.getAttribute('data-target');
        
        const container = btn.closest('.dashboard-tabs-container');
        if (!container) return;
        
        container.querySelectorAll('.dashboard-tab-btn').forEach(b => b.classList.remove('active'));
        container.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
        
        btn.classList.add('active');
        const targetPane = document.getElementById(targetId);
        if (targetPane) targetPane.classList.add('active');
    }
});
