/* Data collection status: API budget, next poll, and recent polling decisions.
 *
 * Shared by the dashboard and the Data & Collection page. The dashboard needs
 * it for the header API-usage indicator; the Data & Collection page hosts the
 * detailed view. Kept separate from dashboard.js so the latter, with its charts
 * and map, does not have to load on a page that shows neither.
 *
 * Every element lookup is optional, so each page renders whichever parts of
 * this it actually contains.
 */

(function () {
    'use strict';

    const REASON_LABELS = {
        dcfc: 'DC fast charging',
        ac_charge_start: 'AC charge starting',
        ac_charge_steady: 'AC charge steady',
        post_trip: 'Post-trip watch',
        idle_day: 'Idle (day)',
        idle_night: 'Idle (night)',
        budget_clamp: 'Budget rationing',
        budget_exhausted: 'Budget exhausted',
        fixed: 'Fixed interval'
    };

    const WARN_FRACTION = 0.8;
    const REFRESH_MS = 60000;

    function formatWhen(value) {
        const date = value instanceof Date ? value : new Date(value);
        if (Number.isNaN(date.valueOf())) { return String(value); }
        return date.toLocaleString([], {
            month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'
        });
    }

    async function loadCollectionStatus() {
        try {
            const response = await fetch('/api/collection-status');
            const data = await response.json();

            const callsToday = document.getElementById('api-calls-today');
            if (callsToday) {
                callsToday.textContent = `${data.calls_today}/${data.daily_limit}`;
                callsToday.className =
                    data.calls_today >= data.daily_limit ? 'limit-reached' : '';
            }

            const headerApiCalls = document.getElementById('header-api-calls');
            if (headerApiCalls) {
                headerApiCalls.textContent = `${data.calls_today}/${data.daily_limit}`;
                const percentage = Math.round((data.calls_today / data.daily_limit) * 100);
                let ariaLabel =
                    `${data.calls_today} of ${data.daily_limit} API calls used today (${percentage}%)`;

                headerApiCalls.className = '';
                if (data.calls_today >= data.daily_limit) {
                    headerApiCalls.className = 'limit-reached';
                    ariaLabel += '. Daily limit reached.';
                } else if (data.calls_today >= data.daily_limit * WARN_FRACTION) {
                    headerApiCalls.className = 'limit-warning';
                    ariaLabel += '. Approaching daily limit.';
                }
                headerApiCalls.setAttribute('aria-label', ariaLabel);
            }

            const nextCollection = document.getElementById('next-collection');
            if (nextCollection && data.next_collection) {
                const minutes = Math.round((new Date(data.next_collection) - new Date()) / 60000);
                nextCollection.textContent = minutes > 0 ? `in ${minutes} minutes` : 'soon';
            }
        } catch (error) {
            console.error('Error loading collection status:', error);
        }
    }

    async function loadPollingStatus() {
        const container = document.getElementById('polling-decisions');
        const modeElement = document.getElementById('polling-mode');
        if (!container && !modeElement) { return; }

        try {
            const response = await fetch('/api/polling-status');
            const status = await response.json();

            if (modeElement) {
                modeElement.textContent = status.adaptive_enabled ? 'Adaptive' : 'Fixed';
            }
            if (!container) { return; }

            const decisions = status.recent_decisions || [];
            if (!decisions.length) {
                container.innerHTML = '<p class="no-data">No polling decisions recorded yet</p>';
                return;
            }

            const rowsHtml = decisions.slice().reverse().map(decision => {
                const reasonLabel = REASON_LABELS[decision.reason] || decision.reason;
                const backoffNote = Number(decision.backoff) > 1
                    ? ` (${Number(decision.backoff).toFixed(1)}x backoff)` : '';
                return `
                    <tr>
                        <td>${formatWhen(decision.timestamp)}</td>
                        <td>${reasonLabel}</td>
                        <td>${Math.round(decision.interval_minutes)} min${backoffNote}</td>
                        <td>${decision.calls_today}/${decision.daily_limit}</td>
                    </tr>
                `;
            }).join('');

            container.innerHTML = `
                <h3>Recent Polling Decisions</h3>
                <div class="table-container">
                    <table id="polling-table" aria-label="Recent polling decisions">
                        <thead>
                            <tr>
                                <th scope="col">Decided At</th>
                                <th scope="col">Reason</th>
                                <th scope="col">Next Poll</th>
                                <th scope="col">Budget Used</th>
                            </tr>
                        </thead>
                        <tbody>${rowsHtml}</tbody>
                    </table>
                </div>
            `;
        } catch (error) {
            console.error('Error loading polling status:', error);
        }
    }

    function refresh() {
        loadCollectionStatus();
        loadPollingStatus();
    }

    window.PYVISIONIC_COLLECTION = { refresh: refresh };

    document.addEventListener('DOMContentLoaded', function () {
        refresh();
        setInterval(refresh, REFRESH_MS);
    });
})();
