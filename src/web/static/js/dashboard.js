document.addEventListener('DOMContentLoaded', function() {
    const refreshBtn = document.getElementById('refresh-btn');
    const unitsToggle = document.getElementById('units-toggle');
    const unitsText = unitsToggle.querySelector('.units-text');
    const batteryLevel = document.getElementById('battery-level');
    const range = document.getElementById('range');
    const temperature = document.getElementById('temperature');
    const odometer = document.getElementById('odometer');
    const lastUpdated = document.getElementById('last-updated');
    const tripsTable = document.getElementById('trips-tbody');
    const chartDiv = document.getElementById('battery-chart');
    
    // Unit conversion functions
    const conversions = {
        kmToMiles: (km) => km * 0.621371,
        milesToKm: (miles) => miles * 1.60934,
        celsiusToFahrenheit: (c) => (c * 9/5) + 32,
        fahrenheitToCelsius: (f) => (f - 32) * 5/9
    };
    
    // Store current data for re-rendering
    let currentData = {
        battery_level: null,
        range: null,
        temperature: null,
        odometer: null,
        trips: [],
        batteryHistory: null
    };
    
    // Current units state
    let currentUnits = localStorage.getItem('units') || 'metric';
    updateUnitsButton();
    
    // Pagination state
    let currentPage = 1;
    let perPage = 10;
    let totalPages = 1;
    let filters = {};
    
    // Handle units toggle
    unitsToggle.addEventListener('click', function() {
        currentUnits = currentUnits === 'metric' ? 'imperial' : 'metric';
        localStorage.setItem('units', currentUnits);
        updateUnitsButton();
        updateDisplay();
    });
    
    function updateUnitsButton() {
        unitsText.textContent = currentUnits === 'metric' ? 'km' : 'mi';
    }
    
    // Update display based on current units
    function updateDisplay() {
        const units = currentUnits;
        
        if (currentData.battery_level !== null) {
            batteryLevel.textContent = currentData.battery_level + '%';
        }
        
        if (currentData.range !== null) {
            const rangeValue = units === 'imperial' 
                ? Math.round(conversions.kmToMiles(currentData.range))
                : currentData.range;
            const rangeUnit = units === 'imperial' ? 'mi' : 'km';
            range.textContent = rangeValue + ' ' + rangeUnit;
        }
        
        if (currentData.temperature !== null) {
            const tempValue = units === 'imperial'
                ? Math.round(conversions.celsiusToFahrenheit(currentData.temperature))
                : currentData.temperature;
            const tempUnit = units === 'imperial' ? '°F' : '°C';
            temperature.textContent = tempValue + tempUnit;
        }
        
        if (currentData.odometer !== null) {
            const odometerValue = units === 'imperial'
                ? Math.round(conversions.kmToMiles(currentData.odometer))
                : currentData.odometer;
            const distanceUnit = units === 'imperial' ? 'mi' : 'km';
            odometer.textContent = formatNumber(odometerValue) + ' ' + distanceUnit;
        }
        
        if (currentData.trips.length > 0) {
            updateTripsTable(currentData.trips);
        }
        
        if (currentData.batteryHistory) {
            updateBatteryChart(currentData.batteryHistory);
        }
    }

    // Load initial data
    loadCurrentStatus();
    loadBatteryHistory();
    loadTrips();
    loadCollectionStatus();

    // Refresh button handler
    refreshBtn.addEventListener('click', async function() {
        refreshBtn.disabled = true;
        refreshBtn.textContent = 'Refreshing...';
        
        try {
            const response = await fetch('/api/refresh');
            const data = await response.json();
            
            if (data.status === 'success') {
                // Reload all data
                await Promise.all([
                    loadCurrentStatus(),
                    loadBatteryHistory(),
                    loadTrips()
                ]);
                showNotification('Data refreshed successfully', 'success');
            } else {
                showNotification('Failed to refresh data: ' + data.message, 'error');
            }
        } catch (error) {
            showNotification('Error refreshing data: ' + error.message, 'error');
        } finally {
            refreshBtn.disabled = false;
            refreshBtn.textContent = 'Refresh Data';
        }
    });

    async function loadCurrentStatus() {
        try {
            const response = await fetch('/api/current-status');
            const data = await response.json();
            
            if (data.battery_level !== undefined) {
                // Store the raw data
                currentData.battery_level = data.battery_level;
                currentData.range = data.range;
                currentData.temperature = data.temperature;
                currentData.odometer = data.odometer;
                
                // Update display with units conversion
                updateDisplay();
                
                lastUpdated.textContent = data.last_updated ? formatDateTime(data.last_updated) : 'Never';
            }
        } catch (error) {
            console.error('Error loading current status:', error);
        }
    }

    async function loadBatteryHistory() {
        try {
            const response = await fetch('/api/battery-history');
            const data = await response.json();
            
            if (data.chart) {
                currentData.batteryHistory = data;
                updateBatteryChart(data);
            }
        } catch (error) {
            console.error('Error loading battery history:', error);
        }
    }
    
    function updateBatteryChart(data) {
        if (!data.chart) return;
        
        // data.chart is already an object, not a JSON string
        const fig = data.chart;
        const units = currentUnits;
        
        // Update temperature axis if needed
        if (units === 'imperial' && fig.data.length > 1) {
            // Convert temperature data to Fahrenheit
            const tempTrace = fig.data[1];
            if (tempTrace && tempTrace.y) {
                tempTrace.y = tempTrace.y.map(temp => 
                    temp !== null ? conversions.celsiusToFahrenheit(temp) : null
                );
            }
            // Update y-axis label
            if (fig.layout.yaxis2) {
                fig.layout.yaxis2.title = 'Temperature (°F)';
            }
        }
        
        Plotly.newPlot(chartDiv, fig.data, fig.layout, {responsive: true});
    }

    async function loadTrips() {
        try {
            // Build query parameters
            const params = new URLSearchParams({
                page: currentPage,
                per_page: perPage,
                ...filters
            });
            
            const response = await fetch(`/api/trips?${params}`);
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            const data = await response.json();
            
            // Handle both old and new API response formats
            if (Array.isArray(data)) {
                // Old format - just an array of trips
                currentData.trips = data;
                updateTripsTable(data);
                updatePagination({
                    page: 1,
                    total_pages: 1,
                    total: data.length,
                    per_page: data.length
                });
            } else {
                // New format with pagination info
                currentData.trips = data.trips || [];
                updateTripsTable(data.trips || []);
                updatePagination(data);
            }
        } catch (error) {
            console.error('Error loading trips:', error);
        }
    }
    
    function updateTripsTable(trips) {
        const units = currentUnits;
        
        if (trips && trips.length > 0) {
            tripsTable.innerHTML = trips.map(trip => {
                const distance = units === 'imperial' 
                    ? Math.round(conversions.kmToMiles(trip.distance || 0))
                    : (trip.distance || 0);
                const avgSpeed = units === 'imperial'
                    ? Math.round(conversions.kmToMiles(trip.average_speed || 0))
                    : (trip.average_speed || 0);
                const maxSpeed = units === 'imperial'
                    ? Math.round(conversions.kmToMiles(trip.max_speed || 0))
                    : (trip.max_speed || 0);
                const speedUnit = units === 'imperial' ? 'mph' : 'km/h';
                const distanceUnit = units === 'imperial' ? 'mi' : 'km';
                
                return `
                    <tr>
                        <td>${formatDate(trip.date)}</td>
                        <td>${distance} ${distanceUnit}</td>
                        <td>${trip.duration || '--'} min</td>
                        <td>${avgSpeed || '--'} ${speedUnit}</td>
                        <td>${trip.drivetrain_consumed || '--'}</td>
                        <td>${trip.climate_consumed || '--'}</td>
                        <td>${trip.accessories_consumed || '--'}</td>
                        <td>${trip.total_consumed || '--'}</td>
                        <td>${trip.regenerated_energy || '--'}</td>
                    </tr>
                `;
            }).join('');
        } else {
            tripsTable.innerHTML = '<tr><td colspan="9" class="no-data">No trip data available</td></tr>';
        }
    }

    function formatDateTime(dateString) {
        const date = new Date(dateString);
        return date.toLocaleString();
    }

    function formatDate(dateString) {
        const date = new Date(dateString);
        // Check if the date string includes time
        if (dateString.includes(' ') || dateString.includes('T')) {
            // Format with both date and time
            return date.toLocaleString('en-US', {
                year: 'numeric',
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
            });
        } else {
            // Format date only
            return date.toLocaleDateString('en-US', {
                year: 'numeric',
                month: 'short',
                day: 'numeric'
            });
        }
    }

    function formatNumber(num) {
        return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    }

    function showNotification(message, type) {
        // Simple notification - could be enhanced with a toast library
        const notification = document.createElement('div');
        notification.className = `notification ${type}`;
        notification.textContent = message;
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 1rem 1.5rem;
            background-color: ${type === 'success' ? 'var(--success-color)' : 'var(--danger-color)'};
            color: white;
            border-radius: var(--border-radius);
            box-shadow: var(--shadow-hover);
            z-index: 1000;
            animation: slideIn 0.3s ease;
        `;
        
        document.body.appendChild(notification);
        
        setTimeout(() => {
            notification.style.animation = 'slideOut 0.3s ease';
            setTimeout(() => notification.remove(), 300);
        }, 3000);
    }

    async function loadCollectionStatus() {
        try {
            const response = await fetch('/api/collection-status');
            const data = await response.json();
            
            const apiCallsToday = document.getElementById('api-calls-today');
            const nextCollection = document.getElementById('next-collection');
            
            if (apiCallsToday) {
                apiCallsToday.textContent = data.calls_today || 0;
            }
            
            if (nextCollection && data.next_collection) {
                const nextTime = new Date(data.next_collection);
                const now = new Date();
                const diffMinutes = Math.round((nextTime - now) / 60000);
                
                if (diffMinutes > 0) {
                    nextCollection.textContent = `in ${diffMinutes} minutes`;
                } else {
                    nextCollection.textContent = nextTime.toLocaleTimeString();
                }
            }
        } catch (error) {
            console.error('Error loading collection status:', error);
        }
    }

    function updatePagination(data) {
        currentPage = data.page;
        totalPages = data.total_pages;
        
        // Update page info
        document.getElementById('page-info').textContent = `Page ${currentPage} of ${totalPages}`;
        
        // Update button states
        document.getElementById('prev-page').disabled = currentPage <= 1;
        document.getElementById('next-page').disabled = currentPage >= totalPages;
    }
    
    // Pagination event handlers
    document.getElementById('prev-page').addEventListener('click', () => {
        if (currentPage > 1) {
            currentPage--;
            loadTrips();
        }
    });
    
    document.getElementById('next-page').addEventListener('click', () => {
        if (currentPage < totalPages) {
            currentPage++;
            loadTrips();
        }
    });
    
    document.getElementById('per-page').addEventListener('change', (e) => {
        perPage = parseInt(e.target.value);
        currentPage = 1; // Reset to first page
        loadTrips();
    });
    
    // Filter event handlers
    document.getElementById('apply-filters').addEventListener('click', () => {
        filters = {};
        
        const startDate = document.getElementById('start-date').value;
        const endDate = document.getElementById('end-date').value;
        const minDistance = document.getElementById('min-distance').value;
        const maxDistance = document.getElementById('max-distance').value;
        
        if (startDate) filters.start_date = startDate;
        if (endDate) filters.end_date = endDate;
        if (minDistance) filters.min_distance = minDistance;
        if (maxDistance) filters.max_distance = maxDistance;
        
        currentPage = 1; // Reset to first page
        loadTrips();
    });
    
    document.getElementById('clear-filters').addEventListener('click', () => {
        document.getElementById('start-date').value = '';
        document.getElementById('end-date').value = '';
        document.getElementById('min-distance').value = '';
        document.getElementById('max-distance').value = '';
        
        filters = {};
        currentPage = 1;
        loadTrips();
    });

    // Auto-refresh every 30 minutes (to stay within API limits)
    setInterval(() => {
        loadCurrentStatus();
        loadBatteryHistory();
        loadTrips();
        loadCollectionStatus();
    }, 30 * 60 * 1000);
    
    // Refresh collection status more frequently
    setInterval(loadCollectionStatus, 60 * 1000);
});

// CSS animations
const style = document.createElement('style');
style.textContent = `
    @keyframes slideIn {
        from {
            transform: translateX(100%);
            opacity: 0;
        }
        to {
            transform: translateX(0);
            opacity: 1;
        }
    }
    
    @keyframes slideOut {
        from {
            transform: translateX(0);
            opacity: 1;
        }
        to {
            transform: translateX(100%);
            opacity: 0;
        }
    }
`;
document.head.appendChild(style);