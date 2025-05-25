// Global map variables
let locationsMap = null;
let tripMap = null;
let mapMarkers = [];

// Chart instances
let batteryChart = null;
let energyChart = null;

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
    const energyChartDiv = document.getElementById('energy-chart');
    
    // Unit conversion functions
    const conversions = {
        kmToMiles: (km) => km * 0.621371,
        milesToKm: (miles) => miles * 1.60934,
        celsiusToFahrenheit: (c) => (c * 9/5) + 32,
        fahrenheitToCelsius: (f) => (f - 32) * 5/9,
        whPerKmToMiPerKwh: (whPerKm) => 1000 / (whPerKm * 1.60934)
    };
    
    let currentUnits = localStorage.getItem('units') || 'metric';
    let currentTrips = [];
    let currentData = {
        batteryHistory: null,
        trips: null
    };
    
    // Set initial units
    updateUnitsDisplay();
    
    function updateUnitsDisplay() {
        unitsText.textContent = currentUnits === 'metric' ? 'km' : 'mi';
        unitsToggle.classList.toggle('imperial', currentUnits === 'imperial');
    }
    
    unitsToggle.addEventListener('click', function() {
        currentUnits = currentUnits === 'metric' ? 'imperial' : 'metric';
        localStorage.setItem('units', currentUnits);
        updateUnitsDisplay();
        
        // Re-render everything with new units
        loadCurrentStatus();
        if (currentData.trips) {
            updateTripsTable(currentData.trips);
        }
        if (currentData.batteryHistory) {
            updateBatteryChart(currentData.batteryHistory);
        }
    });
    
    async function loadCurrentStatus() {
        try {
            const response = await fetch('/api/current-status');
            const data = await response.json();
            
            if (data.battery_level !== null && batteryLevel) {
                batteryLevel.textContent = data.battery_level + '%';
                const batteryIcon = document.querySelector('.battery-icon');
                if (batteryIcon) {
                    batteryIcon.className = 'battery-icon';
                    if (data.battery_level > 80) batteryIcon.classList.add('high');
                    else if (data.battery_level > 50) batteryIcon.classList.add('medium');
                    else if (data.battery_level > 20) batteryIcon.classList.add('low');
                    else batteryIcon.classList.add('critical');
                }
            }
            
            // Handle charging indicator and rate
            const chargingIndicator = document.getElementById('charging-indicator');
            const chargingRate = document.getElementById('charging-rate');
            if (chargingIndicator && chargingRate) {
                if (data.is_charging) {
                    chargingIndicator.style.display = 'inline';
                    chargingRate.style.display = 'block';
                    if (data.charging_power !== null && data.charging_power !== undefined) {
                        chargingRate.textContent = `Charging at ${data.charging_power} kW`;
                    } else {
                        chargingRate.textContent = 'Charging';
                    }
                } else {
                    chargingIndicator.style.display = 'none';
                    chargingRate.style.display = 'none';
                }
            }
            
            if (data.range !== null && range) {
                const rangeValue = currentUnits === 'metric' ? 
                    data.range : conversions.kmToMiles(data.range);
                range.textContent = Math.round(rangeValue) + (currentUnits === 'metric' ? ' km' : ' mi');
            }
            
            if (data.temperature !== null && temperature) {
                const tempValue = currentUnits === 'metric' ? 
                    data.temperature : conversions.celsiusToFahrenheit(data.temperature);
                temperature.textContent = Math.round(tempValue) + '°' + (currentUnits === 'metric' ? 'C' : 'F');
            }
            
            if (data.odometer !== null && odometer) {
                const odoValue = currentUnits === 'metric' ? 
                    data.odometer : conversions.kmToMiles(data.odometer);
                odometer.textContent = Math.round(odoValue).toLocaleString() + (currentUnits === 'metric' ? ' km' : ' mi');
            }
            
            if (data.last_updated && lastUpdated) {
                const date = new Date(data.last_updated);
                lastUpdated.textContent = date.toLocaleString();
            }
        } catch (error) {
            console.error('Error loading current status:', error);
        }
    }
    
    // Load initial data
    loadCurrentStatus();
    loadBatteryHistory();
    loadCollectionStatus();
    loadEfficiencyStats();
    
    // Initialize and load map
    initializeLocationsMap();
    loadLocationsMap();

    // Refresh button handler
    refreshBtn.addEventListener('click', async function() {
        refreshBtn.disabled = true;
        refreshBtn.textContent = 'Refreshing...';
        
        try {
            const response = await fetch('/api/refresh');
            const data = await response.json();
            
            if (response.ok) {
                showNotification(data.message || 'Data refreshed successfully', 'success');
                // Reload all data
                loadCurrentStatus();
                loadBatteryHistory();
                loadTripsWithPagination();
                loadEfficiencyStats();
                loadLocationsMap();
            } else {
                // Show specific error message with appropriate styling
                const errorType = data.error_type || 'error';
                const notificationType = errorType === 'rate_limit' ? 'warning' : 'error';
                showNotification(data.message || 'Failed to refresh data', notificationType);
                
                // Log additional error details for debugging
                console.error('Refresh error:', data);
            }
        } catch (error) {
            console.error('Network error:', error);
            showNotification('Network error: Unable to connect to server', 'error');
        } finally {
            refreshBtn.disabled = false;
            refreshBtn.textContent = 'Refresh Data';
        }
    });
    
    async function loadBatteryHistory() {
        try {
            const response = await fetch('/api/battery-history');
            const data = await response.json();
            currentData.batteryHistory = data;
            updateBatteryChart(data);
        } catch (error) {
            console.error('Error loading battery history:', error);
        }
    }
    
    async function loadTrips() {
        try {
            const response = await fetch('/api/trips');
            const data = await response.json();
            const trips = data.trips || data; // Handle both paginated and non-paginated responses
            currentTrips = trips;
            currentData.trips = trips;
            updateTripsTable(trips);
            updateEnergyChart(trips);
        } catch (error) {
            console.error('Error loading trips:', error);
        }
    }
    
    function updateBatteryChart(data) {
        if (!data.data || data.data.length === 0) return;
        
        const ctx = chartDiv.getContext('2d');
        const units = currentUnits;
        
        // Prepare data
        const chartData = data.data.map(d => ({
            x: new Date(d.timestamp),
            battery: d.battery_level,
            temperature: d.temperature !== null && units === 'imperial' ? 
                conversions.celsiusToFahrenheit(d.temperature) : d.temperature
        }));
        
        // Update existing chart or create new one
        if (batteryChart) {
            // Update existing chart data
            batteryChart.data.datasets[0].data = chartData.map(d => ({x: d.x, y: d.battery}));
            batteryChart.data.datasets[1].data = chartData.map(d => ({x: d.x, y: d.temperature}));
            batteryChart.options.scales.y1.title.text = `Temperature (°${units === 'imperial' ? 'F' : 'C'})`;
            batteryChart.update('none'); // Update without animation for performance
            return;
        }
        
        // Create new chart only if it doesn't exist
        batteryChart = new Chart(ctx, {
            type: 'line',
            data: {
                datasets: [{
                    label: 'Battery Level (%)',
                    data: chartData.map(d => ({x: d.x, y: d.battery})),
                    borderColor: '#3498db',
                    backgroundColor: 'rgba(52, 152, 219, 0.1)',
                    yAxisID: 'y',
                    spanGaps: false,  // This creates gaps for null values
                    tension: 0.1,
                    pointRadius: 3,
                    pointHoverRadius: 5
                }, {
                    label: `Temperature (°${units === 'imperial' ? 'F' : 'C'})`,
                    data: chartData.map(d => ({x: d.x, y: d.temperature})),
                    borderColor: '#e74c3c',
                    backgroundColor: 'rgba(231, 76, 60, 0.1)',
                    yAxisID: 'y1',
                    spanGaps: false,
                    tension: 0.1,
                    pointRadius: 3,
                    pointHoverRadius: 5
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: 'index',
                    intersect: false,
                },
                plugins: {
                    title: {
                        display: true,
                        text: 'Battery Level vs Temperature',
                        font: { size: 16 }
                    },
                    legend: {
                        display: true,
                        position: 'top',
                        labels: {
                            usePointStyle: true,
                            padding: 15
                        }
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                let label = context.dataset.label || '';
                                if (label) {
                                    label += ': ';
                                }
                                if (context.parsed.y !== null) {
                                    label += context.parsed.y.toFixed(1);
                                    if (context.datasetIndex === 0) label += '%';
                                    else label += '°';
                                }
                                return label;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        type: 'time',
                        time: {
                            tooltipFormat: 'MMM DD, HH:mm',
                            displayFormats: {
                                hour: 'HH:mm',
                                day: 'MMM DD'
                            }
                        },
                        title: {
                            display: true,
                            text: 'Time'
                        },
                        grid: {
                            display: true,
                            drawOnChartArea: true,
                            color: 'rgba(0, 0, 0, 0.1)'
                        }
                    },
                    y: {
                        type: 'linear',
                        display: true,
                        position: 'left',
                        title: {
                            display: true,
                            text: 'Battery Level (%)'
                        },
                        min: 0,
                        max: 100,
                        grid: {
                            display: true,
                            drawOnChartArea: true,
                            color: 'rgba(0, 0, 0, 0.1)'
                        }
                    },
                    y1: {
                        type: 'linear',
                        display: true,
                        position: 'right',
                        title: {
                            display: true,
                            text: `Temperature (°${units === 'imperial' ? 'F' : 'C'})`
                        },
                        grid: {
                            drawOnChartArea: false,
                        }
                    }
                }
            }
        });
    }

    function updateEnergyChart(trips) {
        if (!trips || trips.length === 0) {
            energyChartDiv.innerHTML = '<div class="no-data">No trip data available for energy breakdown</div>';
            return;
        }

        // Aggregate energy data from all trips
        const energyData = {
            drivetrain: 0,
            climate: 0,
            accessories: 0,
            battery_care: 0,
            regenerated: 0
        };

        let tripCount = 0;
        trips.forEach(trip => {
            if (trip.drivetrain_consumed !== null && trip.drivetrain_consumed !== undefined) {
                energyData.drivetrain += trip.drivetrain_consumed || 0;
                energyData.climate += trip.climate_consumed || 0;
                energyData.accessories += trip.accessories_consumed || 0;
                energyData.battery_care += trip.battery_care_consumed || 0;
                energyData.regenerated += trip.regenerated_energy || 0;
                tripCount++;
            }
        });

        if (tripCount === 0) {
            energyChartDiv.innerHTML = '<div class="no-data">No detailed energy data available</div>';
            return;
        }

        const ctx = energyChartDiv.getContext('2d');
        
        // Update existing chart or create new one
        if (energyChart) {
            // Update existing chart data
            energyChart.data.datasets[0].data = [
                energyData.drivetrain,
                energyData.climate,
                energyData.accessories,
                energyData.battery_care
            ];
            energyChart.options.plugins.title.text = `Energy Consumption Breakdown (${tripCount} trips)`;
            energyChart.update('none'); // Update without animation for performance
            return;
        }

        // Create doughnut chart only if it doesn't exist
        energyChart = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: ['Drivetrain', 'Climate', 'Accessories', 'Battery Care'],
                datasets: [{
                    data: [
                        energyData.drivetrain,
                        energyData.climate,
                        energyData.accessories,
                        energyData.battery_care
                    ],
                    backgroundColor: [
                        'rgba(52, 152, 219, 0.8)',   // Blue
                        'rgba(255, 127, 14, 0.8)',   // Orange
                        'rgba(44, 160, 44, 0.8)',    // Green
                        'rgba(214, 39, 40, 0.8)'     // Red
                    ],
                    borderColor: [
                        'rgba(52, 152, 219, 1)',
                        'rgba(255, 127, 14, 1)',
                        'rgba(44, 160, 44, 1)',
                        'rgba(214, 39, 40, 1)'
                    ],
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    title: {
                        display: true,
                        text: `Energy Consumption Breakdown (${tripCount} trips)`,
                        font: { size: 16 }
                    },
                    legend: {
                        position: 'bottom',
                        labels: {
                            padding: 15,
                            generateLabels: function(chart) {
                                const data = chart.data;
                                if (data.labels.length && data.datasets.length) {
                                    const dataset = data.datasets[0];
                                    const total = dataset.data.reduce((a, b) => a + b, 0);
                                    return data.labels.map((label, i) => {
                                        const value = dataset.data[i];
                                        const percentage = total > 0 ? ((value / total) * 100).toFixed(1) : 0;
                                        return {
                                            text: `${label}: ${percentage}%`,
                                            fillStyle: dataset.backgroundColor[i],
                                            strokeStyle: dataset.borderColor[i],
                                            lineWidth: dataset.borderWidth,
                                            hidden: false,
                                            index: i
                                        };
                                    });
                                }
                                return [];
                            }
                        }
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                const label = context.label || '';
                                const value = context.parsed;
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const percentage = total > 0 ? ((value / total) * 100).toFixed(1) : 0;
                                return `${label}: ${value} Wh (${percentage}%)`;
                            }
                        }
                    }
                },
                elements: {}
            }
        });

        // Plugin to draw center text
        Chart.register({
            id: 'centerText',
            beforeDraw: function(chart) {
                if (chart.config.options.elements && chart.config.options.elements.center) {
                    const ctx = chart.ctx;
                    const centerConfig = chart.config.options.elements.center;
                    
                    ctx.save();
                    ctx.font = '14px ' + centerConfig.fontStyle;
                    ctx.fillStyle = centerConfig.color;
                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'middle';
                    
                    const centerX = (chart.chartArea.left + chart.chartArea.right) / 2;
                    const centerY = (chart.chartArea.top + chart.chartArea.bottom) / 2;
                    
                    ctx.fillText(centerConfig.text, centerX, centerY - 10);
                    
                    ctx.font = '12px ' + centerConfig.fontStyle;
                    ctx.fillStyle = '#666';
                    ctx.fillText(centerConfig.subText, centerX, centerY + 10);
                    
                    ctx.restore();
                }
            }
        });
    }

    function updateTripsTable(trips) {
        tripsTable.innerHTML = '';
        
        trips.forEach((trip, index) => {
            const row = document.createElement('tr');
            row.className = 'clickable';
            
            const date = new Date(trip.date);
            const distance = currentUnits === 'metric' ? 
                trip.distance : conversions.kmToMiles(trip.distance);
            const efficiency = trip.total_consumed && trip.distance > 0 ? 
                trip.total_consumed / trip.distance : null;
            const efficiencyDisplay = efficiency ? 
                (currentUnits === 'metric' ? 
                    `${Math.round(efficiency)} Wh/km` : 
                    `${conversions.whPerKmToMiPerKwh(efficiency).toFixed(1)} mi/kWh`) : '-';
            
            row.innerHTML = `
                <td>${formatDate(date)}</td>
                <td>${distance.toFixed(1)} ${currentUnits === 'metric' ? 'km' : 'mi'}</td>
                <td>${trip.duration || '-'} min</td>
                <td>${trip.average_speed || '-'} ${currentUnits === 'metric' ? 'km/h' : 'mph'}</td>
                <td>${trip.drivetrain_consumed || '-'}</td>
                <td>${trip.climate_consumed || '-'}</td>
                <td>${trip.accessories_consumed || '-'}</td>
                <td>${trip.total_consumed || '-'}</td>
                <td>${trip.regenerated_energy || '-'}</td>
                <td>${trip.end_latitude && trip.end_longitude ? '📍' : '-'}</td>
            `;
            
            // Create trip ID in the format expected by backend: date_distance_odometer
            let dateStr = trip.date.toString();
            dateStr = dateStr.replace(/\.0+$/, '');
            
            // Create composite trip ID
            const tripParts = [
                btoa(dateStr),
                trip.distance,
                trip.odometer_start || '0'
            ];
            const tripId = tripParts.join('_');
            
            row.addEventListener('click', () => {
                showTripModal(tripId);
            });
            
            tripsTable.appendChild(row);
        });
    }
    
    function formatDate(date) {
        if (!(date instanceof Date)) {
            date = new Date(date);
        }
        const options = { 
            month: 'short', 
            day: 'numeric', 
            hour: '2-digit', 
            minute: '2-digit' 
        };
        return date.toLocaleDateString('en-US', options);
    }
    
    async function loadCollectionStatus() {
        try {
            const response = await fetch('/api/collection-status');
            const data = await response.json();
            
            const callsToday = document.getElementById('api-calls-today');
            const nextCollection = document.getElementById('next-collection');
            
            if (callsToday) {
                callsToday.textContent = `${data.calls_today}/${data.daily_limit}`;
                callsToday.className = data.calls_today >= data.daily_limit ? 'limit-reached' : '';
            }
            
            if (nextCollection && data.next_collection) {
                const next = new Date(data.next_collection);
                const now = new Date();
                const diff = Math.round((next - now) / 60000);
                
                if (diff > 0) {
                    nextCollection.textContent = `in ${diff} minutes`;
                } else {
                    nextCollection.textContent = 'soon';
                }
            }
        } catch (error) {
            console.error('Error loading collection status:', error);
        }
    }
    
    function showNotification(message, type = 'info') {
        const notification = document.createElement('div');
        notification.className = `notification ${type}`;
        notification.textContent = message;
        document.body.appendChild(notification);
        
        setTimeout(() => {
            notification.classList.add('fade-out');
            setTimeout(() => notification.remove(), 300);
        }, 3000);
    }

    function initializeLocationsMap() {
        try {
            const mapElement = document.getElementById('locations-map');
            if (!mapElement) {
                console.error('Map element not found: locations-map');
                return;
            }
            
            if (!locationsMap) {
                locationsMap = L.map('locations-map').setView([44.9778, -93.2650], 10);
                
                L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                    attribution: '© OpenStreetMap contributors'
                }).addTo(locationsMap);
            }
        } catch (error) {
            console.error('Error initializing locations map:', error);
        }
    }
    
    async function loadLocationsMap() {
        try {
            if (!locationsMap) {
                console.warn('Locations map not initialized, skipping load');
                return;
            }
            
            const response = await fetch('/api/locations');
            if (response.ok) {
                const locations = await response.json();
                console.log(`Loaded ${locations.length} locations`);
                
                mapMarkers.forEach(marker => locationsMap.removeLayer(marker));
                mapMarkers = [];
                
                if (locations.length > 0) {
                    const bounds = [];
                    
                    locations.forEach(loc => {
                        const icon = loc.is_current ? 
                            L.divIcon({
                                html: '<div style="background-color: #e74c3c; width: 20px; height: 20px; border-radius: 50%; border: 3px solid white; box-shadow: 0 2px 5px rgba(0,0,0,0.3);"></div>',
                                iconSize: [20, 20],
                                className: 'current-location-icon'
                            }) :
                            L.divIcon({
                                html: '<div style="background-color: #3498db; width: 15px; height: 15px; border-radius: 50%; border: 2px solid white; box-shadow: 0 2px 5px rgba(0,0,0,0.3);"></div>',
                                iconSize: [15, 15],
                                className: 'trip-location-icon'
                            });
                        
                        const marker = L.marker([loc.lat, loc.lng], { icon })
                            .addTo(locationsMap);
                        
                        let popupContent = `<strong>${formatDate(loc.date)}</strong>`;
                        if (loc.distance > 0) {
                            popupContent += `<br>Distance: ${loc.distance} km`;
                            popupContent += `<br>Duration: ${loc.duration} min`;
                            if (loc.efficiency) {
                                popupContent += `<br>Efficiency: ${loc.efficiency} Wh/km`;
                            }
                        }
                        if (loc.temperature !== null && loc.temperature !== undefined) {
                            popupContent += `<br>Temperature: ${loc.temperature}°F`;
                        }
                        
                        marker.bindPopup(popupContent);
                        mapMarkers.push(marker);
                        bounds.push([loc.lat, loc.lng]);
                    });
                    
                    if (bounds.length > 0) {
                        locationsMap.fitBounds(bounds, { padding: [50, 50] });
                    }
                } else {
                    locationsMap.setView([44.9778, -93.2650], 10);
                }
            }
        } catch (error) {
            console.error('Error loading locations:', error);
        }
    }
    
    async function loadEfficiencyStats() {
        try {
            const response = await fetch('/api/efficiency-stats');
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            const stats = await response.json();
            
            const displayStat = (elementId, data) => {
                const valueElement = document.getElementById(elementId);
                const detailElement = document.getElementById(elementId + '-detail');
                
                if (valueElement && data) {
                    valueElement.textContent = `${data.average} mi/kWh`;
                }
                if (detailElement && data) {
                    detailElement.textContent = `Best: ${data.best} | Worst: ${data.worst}`;
                }
            };
            
            displayStat('efficiency-day', stats.last_day);
            displayStat('efficiency-week', stats.last_week);
            displayStat('efficiency-month', stats.last_month);
            displayStat('efficiency-all', stats.all_time);
            
        } catch (error) {
            console.error('Error loading efficiency stats:', error);
        }
    }
    
    // Pagination variables
    let currentPage = 1;
    let perPage = 10;
    let totalPages = 1;
    
    // Pagination event handlers
    const prevPageBtn = document.getElementById('prev-page');
    const nextPageBtn = document.getElementById('next-page');
    const pageInfo = document.getElementById('page-info');
    const perPageSelect = document.getElementById('per-page');
    
    if (prevPageBtn) {
        prevPageBtn.addEventListener('click', () => {
            if (currentPage > 1) {
                currentPage--;
                loadTripsWithPagination();
            }
        });
    }
    
    if (nextPageBtn) {
        nextPageBtn.addEventListener('click', () => {
            if (currentPage < totalPages) {
                currentPage++;
                loadTripsWithPagination();
            }
        });
    }
    
    if (perPageSelect) {
        perPageSelect.addEventListener('change', (e) => {
            perPage = parseInt(e.target.value);
            currentPage = 1;
            loadTripsWithPagination();
        });
    }
    
    async function loadTripsWithPagination() {
        try {
            const response = await fetch(`/api/trips?page=${currentPage}&per_page=${perPage}`);
            const data = await response.json();
            
            totalPages = data.total_pages || 1;
            currentPage = data.page || 1;
            
            updateTripsTable(data.trips || []);
            
            // Update pagination controls
            if (pageInfo) pageInfo.textContent = `Page ${currentPage} of ${totalPages}`;
            if (prevPageBtn) prevPageBtn.disabled = currentPage <= 1;
            if (nextPageBtn) nextPageBtn.disabled = currentPage >= totalPages;
            
            // Also update the energy chart with all trips
            if (data.trips) {
                updateEnergyChart(data.trips);
            }
        } catch (error) {
            console.error('Error loading trips with pagination:', error);
        }
    }
    
    // Replace initial loadTrips with pagination version
    loadTripsWithPagination();
    
    // Modal handling
    const modal = document.getElementById('tripModal');
    const modalClose = document.querySelector('.modal-close');
    let tripEnergyChart = null;
    
    if (modalClose) {
        modalClose.addEventListener('click', () => {
            modal.style.display = 'none';
            if (tripMap) {
                tripMap.remove();
                tripMap = null;
            }
        });
    }
    
    window.addEventListener('click', (event) => {
        if (event.target === modal) {
            modal.style.display = 'none';
            if (tripMap) {
                tripMap.remove();
                tripMap = null;
            }
        }
    });
    
    async function showTripModal(tripId) {
        try {
            const response = await fetch(`/api/trip/${tripId}`);
            if (!response.ok) {
                throw new Error('Failed to load trip details');
            }
            
            const trip = await response.json();
            
            // Update modal title
            document.getElementById('modal-title').textContent = 
                `Trip Details - ${formatDate(new Date(trip.date))}`;
            
            // Create stats grid
            const statsHtml = `
                <div class="stat-card">
                    <h3>Distance</h3>
                    <p>${trip.distance} ${currentUnits === 'metric' ? 'km' : 'mi'}</p>
                </div>
                <div class="stat-card">
                    <h3>Duration</h3>
                    <p>${trip.duration} min</p>
                </div>
                <div class="stat-card">
                    <h3>Avg Speed</h3>
                    <p>${trip.average_speed} ${currentUnits === 'metric' ? 'km/h' : 'mph'}</p>
                </div>
                <div class="stat-card">
                    <h3>Max Speed</h3>
                    <p>${trip.max_speed} ${currentUnits === 'metric' ? 'km/h' : 'mph'}</p>
                </div>
                <div class="stat-card">
                    <h3>Efficiency</h3>
                    <p>${currentUnits === 'metric' ? 
                        Math.round(trip.efficiency_wh_per_km) + ' Wh/km' : 
                        conversions.whPerKmToMiPerKwh(trip.efficiency_wh_per_km).toFixed(1) + ' mi/kWh'}</p>
                </div>
                <div class="stat-card">
                    <h3>Net Energy</h3>
                    <p>${trip.net_energy} Wh</p>
                </div>
            `;
            document.getElementById('trip-stats').innerHTML = statsHtml;
            
            // Create energy breakdown chart
            const ctx = document.getElementById('trip-energy-chart').getContext('2d');
            
            if (tripEnergyChart) {
                tripEnergyChart.destroy();
            }
            
            tripEnergyChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: ['Drivetrain', 'Climate', 'Accessories', 'Battery Care', 'Regenerated'],
                    datasets: [{
                        label: 'Energy (Wh)',
                        data: [
                            trip.drivetrain_consumed,
                            trip.climate_consumed,
                            trip.accessories_consumed,
                            trip.battery_care_consumed,
                            -trip.regenerated_energy // Negative for regenerated
                        ],
                        backgroundColor: [
                            'rgba(52, 152, 219, 0.8)',
                            'rgba(255, 127, 14, 0.8)',
                            'rgba(44, 160, 44, 0.8)',
                            'rgba(214, 39, 40, 0.8)',
                            'rgba(46, 204, 113, 0.8)'
                        ]
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        title: {
                            display: true,
                            text: 'Energy Consumption Breakdown'
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            title: {
                                display: true,
                                text: 'Energy (Wh)'
                            }
                        }
                    }
                }
            });
            
            // Create map if location available
            if (trip.end_latitude && trip.end_longitude) {
                setTimeout(() => {
                    const mapDiv = document.getElementById('trip-map');
                    if (tripMap) {
                        tripMap.remove();
                    }
                    
                    tripMap = L.map('trip-map').setView([trip.end_latitude, trip.end_longitude], 13);
                    
                    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                        attribution: '© OpenStreetMap contributors'
                    }).addTo(tripMap);
                    
                    L.marker([trip.end_latitude, trip.end_longitude])
                        .addTo(tripMap)
                        .bindPopup('Trip End Location')
                        .openPopup();
                }, 100);
            } else {
                document.getElementById('trip-map').innerHTML = 
                    '<div class="no-data">No location data available for this trip</div>';
            }
            
            // Show modal
            modal.style.display = 'block';
            
        } catch (error) {
            console.error('Error loading trip details:', error);
            alert('Failed to load trip details');
        }
    }
    
    // Reduce update frequency and stagger the calls
    setInterval(() => {
        loadCollectionStatus();
    }, 60000);
    
    setInterval(() => {
        loadEfficiencyStats();
    }, 300000); // Every 5 minutes
    
    setInterval(() => {
        loadLocationsMap();
    }, 300000); // Every 5 minutes
});

// Add CSS for notifications
const style = document.createElement('style');
style.textContent = `
    .notification {
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 15px 20px;
        border-radius: 4px;
        color: white;
        font-weight: 500;
        z-index: 1000;
        animation: slideIn 0.3s ease-out;
    }
    
    .notification.success {
        background-color: #2ecc71;
    }
    
    .notification.error {
        background-color: #e74c3c;
    }
    
    .notification.info {
        background-color: #3498db;
    }
    
    .notification.warning {
        background-color: #f39c12;
    }
    
    .notification.fade-out {
        animation: slideOut 0.3s ease-out forwards;
    }
    
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