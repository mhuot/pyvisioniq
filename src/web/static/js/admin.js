/**
 * admin.js - Admin dashboard for PyVisionic storage diagnostics.
 * Fetches /admin/api/storage-stats and populates tables.
 * Auto-refreshes every 30 seconds with a visible countdown.
 */

(function () {
    'use strict';

    const REFRESH_INTERVAL = 30;
    let countdown = REFRESH_INTERVAL;
    let countdownTimer = null;

    function formatBytes(bytes) {
        if (bytes === 0) return '0 B';
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(1024));
        return (bytes / Math.pow(1024, i)).toFixed(1) + ' ' + sizes[i];
    }

    function formatTimestamp(ts) {
        if (!ts) return '--';
        try {
            const date = new Date(ts);
            if (isNaN(date.getTime())) return ts;
            return date.toLocaleString();
        } catch (e) {
            return ts;
        }
    }

    function renderCsvStats(csvData) {
        const tbody = document.getElementById('csv-stats-body');
        if (!csvData || !csvData.files) {
            tbody.innerHTML = '<tr><td colspan="4">CSV data unavailable</td></tr>';
            return;
        }

        let html = '';
        for (const [name, info] of Object.entries(csvData.files)) {
            const statusClass = info.exists ? '' : ' class="admin-row-warning"';
            html += '<tr' + statusClass + '>';
            html += '<td>' + name + '</td>';
            html += '<td>' + (info.rows || 0).toLocaleString() + '</td>';
            html += '<td>' + formatBytes(info.size_bytes || 0) + '</td>';
            html += '<td>' + formatTimestamp(info.latest_timestamp) + '</td>';
            html += '</tr>';
        }
        tbody.innerHTML = html || '<tr><td colspan="4">No CSV files found</td></tr>';
    }

    function renderOracleStats(oracleData) {
        const tbody = document.getElementById('oracle-stats-body');
        const poolDiv = document.getElementById('oracle-pool-stats');

        if (!oracleData || !oracleData.available) {
            tbody.innerHTML = '<tr><td colspan="3">Oracle not available</td></tr>';
            const errorMsg = oracleData && oracleData.error ? oracleData.error : 'Not configured';
            poolDiv.innerHTML = '<p class="admin-pool-error">Oracle unavailable: ' + errorMsg + '</p>';
            return;
        }

        // Pool metrics
        const pool = oracleData.pool || {};
        if (pool.error) {
            poolDiv.innerHTML = '<p class="admin-pool-error">Pool error: ' + pool.error + '</p>';
        } else {
            poolDiv.innerHTML =
                '<div class="admin-pool-grid" role="list">' +
                '<div class="admin-pool-item" role="listitem"><span class="admin-pool-label">DSN</span><span class="admin-pool-value">' + (oracleData.dsn || '--') + '</span></div>' +
                '<div class="admin-pool-item" role="listitem"><span class="admin-pool-label">Busy</span><span class="admin-pool-value">' + (pool.busy || 0) + '</span></div>' +
                '<div class="admin-pool-item" role="listitem"><span class="admin-pool-label">Open</span><span class="admin-pool-value">' + (pool.open || 0) + '</span></div>' +
                '<div class="admin-pool-item" role="listitem"><span class="admin-pool-label">Min/Max</span><span class="admin-pool-value">' + (pool.min || 0) + '/' + (pool.max || 0) + '</span></div>' +
                '</div>';
        }

        // Table stats
        const tables = oracleData.tables || {};
        let html = '';
        for (const [name, info] of Object.entries(tables)) {
            if (info.error) {
                html += '<tr class="admin-row-warning"><td>' + name + '</td><td colspan="2">Error: ' + info.error + '</td></tr>';
            } else {
                html += '<tr>';
                html += '<td>' + name + '</td>';
                html += '<td>' + (info.rows || 0).toLocaleString() + '</td>';
                html += '<td>' + formatTimestamp(info.latest_timestamp) + '</td>';
                html += '</tr>';
            }
        }
        tbody.innerHTML = html || '<tr><td colspan="3">No Oracle tables found</td></tr>';
    }

    function renderSyncStats(syncData) {
        const tbody = document.getElementById('sync-stats-body');
        if (!syncData) {
            tbody.innerHTML = '<tr><td colspan="5">Sync data unavailable (requires dual backend)</td></tr>';
            return;
        }

        let html = '';
        for (const [name, info] of Object.entries(syncData)) {
            const inSync = info.in_sync;
            const statusText = inSync ? 'In Sync' : 'Out of Sync';
            const statusClass = inSync ? 'admin-sync-ok' : 'admin-sync-diff';
            html += '<tr>';
            html += '<td>' + name + '</td>';
            html += '<td>' + (info.csv_rows || 0).toLocaleString() + '</td>';
            html += '<td>' + (info.oracle_rows || 0).toLocaleString() + '</td>';
            html += '<td>' + (info.diff || 0) + '</td>';
            html += '<td><span class="' + statusClass + '" role="status">' + statusText + '</span></td>';
            html += '</tr>';
        }
        tbody.innerHTML = html;
    }

    let currentRawPage = 1;

    async function loadRawResponses(page) {
        currentRawPage = page || 1;
        const tbody = document.getElementById('raw-responses-body');
        const pagination = document.getElementById('raw-responses-pagination');

        try {
            const response = await fetch('/admin/api/raw-responses?page=' + currentRawPage + '&per_page=20');
            if (!response.ok) {
                if (response.status === 404) {
                    tbody.innerHTML = '<tr><td colspan="6">Oracle storage not available</td></tr>';
                    pagination.innerHTML = '';
                    return;
                }
                throw new Error('HTTP ' + response.status);
            }

            const data = await response.json();
            if (data.error) {
                tbody.innerHTML = '<tr><td colspan="6">' + data.error + '</td></tr>';
                pagination.innerHTML = '';
                return;
            }

            const items = data.items || [];
            if (items.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6">No raw responses stored yet</td></tr>';
                pagination.innerHTML = '';
                return;
            }

            let html = '';
            for (const item of items) {
                html += '<tr>';
                html += '<td>' + item.id + '</td>';
                html += '<td>' + formatTimestamp(item.timestamp) + '</td>';
                html += '<td>' + formatTimestamp(item.api_last_updated) + '</td>';
                html += '<td>' + (item.is_cached || '--') + '</td>';
                html += '<td>' + (item.hyundai_data_fresh || '--') + '</td>';
                html += '<td><button class="admin-btn-view" data-id="' + item.id + '" aria-label="View response ' + item.id + '">View</button></td>';
                html += '</tr>';
            }
            tbody.innerHTML = html;

            // Attach click handlers
            tbody.querySelectorAll('.admin-btn-view').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    viewRawResponse(parseInt(this.getAttribute('data-id'), 10));
                });
            });

            // Pagination controls
            let pagHtml = '';
            if (data.pages > 1) {
                if (currentRawPage > 1) {
                    pagHtml += '<button class="admin-btn-page" data-page="' + (currentRawPage - 1) + '" aria-label="Previous page">Prev</button> ';
                }
                pagHtml += '<span class="admin-page-info" aria-live="polite">Page ' + currentRawPage + ' of ' + data.pages + ' (' + data.total + ' total)</span>';
                if (currentRawPage < data.pages) {
                    pagHtml += ' <button class="admin-btn-page" data-page="' + (currentRawPage + 1) + '" aria-label="Next page">Next</button>';
                }
            } else {
                pagHtml = '<span class="admin-page-info" aria-live="polite">' + data.total + ' total</span>';
            }
            pagination.innerHTML = pagHtml;

            pagination.querySelectorAll('.admin-btn-page').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    loadRawResponses(parseInt(this.getAttribute('data-page'), 10));
                });
            });
        } catch (error) {
            console.error('Error loading raw responses:', error);
            tbody.innerHTML = '<tr><td colspan="6">Error loading raw responses</td></tr>';
        }
    }

    async function viewRawResponse(id) {
        var dialog = document.getElementById('raw-response-dialog');
        var jsonPre = document.getElementById('raw-response-json');
        jsonPre.textContent = 'Loading...';
        dialog.showModal();

        try {
            var response = await fetch('/admin/api/raw-response/' + id);
            if (!response.ok) throw new Error('HTTP ' + response.status);
            var data = await response.json();
            jsonPre.textContent = JSON.stringify(data.response_json || data, null, 2);
        } catch (error) {
            jsonPre.textContent = 'Error loading response: ' + error.message;
        }
    }

    function renderHealth(data) {
        const backendType = document.getElementById('backend-type');
        const backendStatus = document.getElementById('backend-status');
        const readFrom = document.getElementById('read-from');

        if (backendType) backendType.textContent = (data.backend || '--').toUpperCase();

        if (backendStatus) {
            const available = data.available !== false;
            const statusText = available ? 'Healthy' : 'Error';
            const statusClass = available ? 'admin-status-ok' : 'admin-status-error';
            backendStatus.innerHTML = '<span class="' + statusClass + '">' + statusText + '</span>';
        }

        if (readFrom) readFrom.textContent = (data.read_from || data.backend || '--').toUpperCase();
    }

    async function loadStats() {
        try {
            const response = await fetch('/admin/api/storage-stats');

            if (response.status === 401 || response.status === 403) {
                document.querySelector('main').innerHTML =
                    '<section class="admin-section"><h2>Access Denied</h2>' +
                    '<p>You do not have admin access. <a href="/login">Sign in</a></p></section>';
                return;
            }

            if (!response.ok) {
                throw new Error('HTTP ' + response.status);
            }

            const data = await response.json();

            renderHealth(data);

            // Determine structure based on backend type
            if (data.backend === 'dual') {
                renderCsvStats(data.csv || {});
                renderOracleStats(data.oracle || {});
                renderSyncStats(data.sync || null);
            } else if (data.backend === 'csv') {
                renderCsvStats(data);
                document.getElementById('oracle-stats-body').innerHTML =
                    '<tr><td colspan="3">Oracle not configured (backend: csv)</td></tr>';
                document.getElementById('oracle-pool-stats').innerHTML =
                    '<p>Oracle not configured</p>';
                document.getElementById('sync-stats-body').innerHTML =
                    '<tr><td colspan="5">Sync comparison requires dual backend</td></tr>';
            } else if (data.backend === 'oracle') {
                renderOracleStats(data);
                document.getElementById('csv-stats-body').innerHTML =
                    '<tr><td colspan="4">CSV not configured (backend: oracle)</td></tr>';
                document.getElementById('sync-stats-body').innerHTML =
                    '<tr><td colspan="5">Sync comparison requires dual backend</td></tr>';
            }
        } catch (error) {
            console.error('Error loading storage stats:', error);
            const statusEl = document.getElementById('backend-status');
            if (statusEl) {
                statusEl.innerHTML = '<span class="admin-status-error">Error loading stats</span>';
            }
        }
    }

    function startCountdown() {
        countdown = REFRESH_INTERVAL;
        if (countdownTimer) clearInterval(countdownTimer);
        countdownTimer = setInterval(function () {
            countdown--;
            const el = document.getElementById('refresh-countdown');
            if (el) el.textContent = countdown + 's';
            if (countdown <= 0) {
                countdown = REFRESH_INTERVAL;
                loadStats();
            }
        }, 1000);
    }

    // Initial load
    document.addEventListener('DOMContentLoaded', function () {
        loadStats();
        loadRawResponses(1);
        startCountdown();

        var closeBtn = document.getElementById('close-dialog');
        var dialog = document.getElementById('raw-response-dialog');
        if (closeBtn && dialog) {
            closeBtn.addEventListener('click', function () {
                dialog.close();
            });
            // Close on backdrop click
            dialog.addEventListener('click', function (e) {
                if (e.target === dialog) {
                    dialog.close();
                }
            });
        }
    });
})();
