// Global map variables
let locationsMap = null;
let tripMap = null;
let mapMarkers = [];

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
            updateEnergyChart(currentData.trips);
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
        
        // Ensure all traces have connectgaps set to false to show gaps for null values
        if (fig.data) {
            fig.data.forEach(trace => {
                if (trace.type === 'scatter' || trace.type === 'line' || !trace.type) {
                    trace.connectgaps = false;
                }
            });
        }
        
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

    function updateEnergyChart(trips) {
        if (!trips || trips.length === 0) {
            energyChartDiv.innerHTML = '<div class="no-data">No trip data available for energy breakdown</div>';
            return;
        }

        // Calculate total energy consumption by category
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

        // Create pie chart for energy consumption breakdown
        const consumptionData = [{
            values: [energyData.drivetrain, energyData.climate, energyData.accessories, energyData.battery_care],
            labels: ['Drivetrain', 'Climate', 'Accessories', 'Battery Care'],
            type: 'pie',
            hole: 0.3,
            marker: {
                colors: ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728']
            },
            textinfo: 'label+percent',
            textposition: 'outside'
        }];

        const layout = {
            title: {
                text: `Energy Consumption Breakdown (${tripCount} trips)`,
                font: { size: 16 }
            },
            annotations: [{
                font: { size: 14 },
                showarrow: false,
                text: `${Math.round(energyData.regenerated)} Wh<br>Regenerated`,
                x: 0.5,
                y: 0.5
            }],
            showlegend: true,
            margin: { t: 50, b: 50, l: 50, r: 50 }
        };

        Plotly.newPlot(energyChartDiv, consumptionData, layout, {responsive: true});
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
                updateEnergyChart(data);
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
                updateEnergyChart(data.trips || []);
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
                const speedUnit = units === 'imperial' ? 'mph' : 'km/h';
                const distanceUnit = units === 'imperial' ? 'mi' : 'km';
                
                // Format location and temperature
                let locationInfo = '--';
                if (trip.end_latitude && trip.end_longitude) {
                    const temp = trip.end_temperature ? 
                        (units === 'imperial' ? 
                            `${Math.round(conversions.celsiusToFahrenheit(trip.end_temperature))}°F` : 
                            `${trip.end_temperature}°C`) : '';
                    locationInfo = `<span title="Lat: ${trip.end_latitude}, Lon: ${trip.end_longitude}">📍</span> ${temp}`;
                }
                
                // Create trip ID - use base64 encoding for the date to avoid URL issues
                // Handle different date formats - remove .000 or .0 at the end
                let dateStr = String(trip.date);
                dateStr = dateStr.replace(/\.0+$/, ''); // Remove .0, .00, .000 etc at the end
                const encodedDate = btoa(dateStr).replace(/=/g, ''); // Remove padding
                const tripId = `${encodedDate}_${trip.distance}_${trip.odometer_start || ''}`;
                
                // Debug logging
                if (trips.indexOf(trip) < 3) { // Log first 3 trips
                    console.log(`Trip ${trips.indexOf(trip)}: date='${trip.date}' -> '${dateStr}' -> '${encodedDate}', distance=${trip.distance}, odometer=${trip.odometer_start}`);
                }
                
                return `
                    <tr class="trip-row" data-trip-id="${tripId}" style="cursor: pointer;">
                        <td>${formatDate(trip.date)}</td>
                        <td>${distance} ${distanceUnit}</td>
                        <td>${trip.duration || '--'} min</td>
                        <td>${avgSpeed || '--'} ${speedUnit}</td>
                        <td>${trip.drivetrain_consumed || '--'}</td>
                        <td>${trip.climate_consumed || '--'}</td>
                        <td>${trip.accessories_consumed || '--'}</td>
                        <td>${trip.total_consumed || '--'}</td>
                        <td>${trip.regenerated_energy || '--'}</td>
                        <td>${locationInfo}</td>
                    </tr>
                `;
            }).join('');
            
            // Add click handlers to trip rows
            document.querySelectorAll('.trip-row').forEach(row => {
                row.addEventListener('click', function() {
                    const tripId = this.getAttribute('data-trip-id');
                    showTripDetails(tripId);
                });
            });
        } else {
            tripsTable.innerHTML = '<tr><td colspan="10" class="no-data">No trip data available</td></tr>';
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

    function initializeLocationsMap() {
        try {
            // Check if element exists
            const mapElement = document.getElementById('locations-map');
            if (!mapElement) {
                console.error('Map element not found: locations-map');
                return;
            }
            
            // Initialize the main locations map
            if (!locationsMap) {
                locationsMap = L.map('locations-map').setView([44.9778, -93.2650], 10); // Minneapolis area default
                
                // Add OpenStreetMap tiles
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
                
                // Clear existing markers
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
                        
                        // Create popup content
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
                    
                    // Fit map to show all markers
                    if (bounds.length > 0) {
                        locationsMap.fitBounds(bounds, { padding: [50, 50] });
                    }
                }
            }
        } catch (error) {
            console.error('Error loading locations:', error);
        }
    }
    
    function showTripMap(trip) {
        if (trip.end_latitude && trip.end_longitude) {
            // Wait for modal to be visible
            setTimeout(() => {
                if (!tripMap) {
                    tripMap = L.map('trip-map').setView([trip.end_latitude, trip.end_longitude], 13);
                    
                    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                        attribution: '© OpenStreetMap contributors'
                    }).addTo(tripMap);
                } else {
                    tripMap.setView([trip.end_latitude, trip.end_longitude], 13);
                    // Clear existing markers
                    tripMap.eachLayer(layer => {
                        if (layer instanceof L.Marker) {
                            tripMap.removeLayer(layer);
                        }
                    });
                }
                
                // Add marker for trip end location
                const marker = L.marker([trip.end_latitude, trip.end_longitude])
                    .addTo(tripMap);
                
                let popupContent = `<strong>Trip End Location</strong>`;
                popupContent += `<br>Date: ${formatDate(trip.date)}`;
                popupContent += `<br>Coordinates: ${trip.end_latitude.toFixed(6)}, ${trip.end_longitude.toFixed(6)}`;
                
                marker.bindPopup(popupContent).openPopup();
                
                // Force map to refresh
                tripMap.invalidateSize();
            }, 100);
        } else {
            // Hide map if no location
            document.getElementById('trip-map').style.display = 'none';
        }
    }
    
    async function loadEfficiencyStats() {
        try {
            const response = await fetch('/api/efficiency-stats');
            if (response.ok) {
                const stats = await response.json();
                
                // Update efficiency cards
                const periods = ['last_day', 'last_week', 'last_month', 'all_time'];
                const periodNames = ['day', 'week', 'month', 'all'];
                
                periods.forEach((period, index) => {
                    const periodData = stats[period];
                    const elementId = periodNames[index];
                    
                    if (periodData) {
                        // Display average efficiency
                        document.getElementById(`efficiency-${elementId}`).textContent = 
                            `${periodData.average} mi/kWh`;
                        
                        // Display details (best/worst)
                        document.getElementById(`efficiency-${elementId}-detail`).textContent = 
                            `Best: ${periodData.best} | Worst: ${periodData.worst} | Trips: ${periodData.trip_count}`;
                        
                        // Color code based on efficiency
                        const valueElement = document.getElementById(`efficiency-${elementId}`);
                        if (periodData.average >= 4.0) {
                            valueElement.style.color = 'var(--success-color)';
                        } else if (periodData.average >= 3.0) {
                            valueElement.style.color = 'var(--warning-color)';
                        } else {
                            valueElement.style.color = 'var(--danger-color)';
                        }
                    } else {
                        document.getElementById(`efficiency-${elementId}`).textContent = '--';
                        document.getElementById(`efficiency-${elementId}-detail`).textContent = 'No data';
                    }
                });
                
                // Log totals for debugging
                console.log('Efficiency totals:', stats.totals);
            }
        } catch (error) {
            console.error('Error loading efficiency stats:', error);
        }
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

    // Show trip details modal
    async function showTripDetails(tripId) {
        console.log('Showing trip details for ID:', tripId);
        try {
            const response = await fetch(`/api/trip/${tripId}`);
            const trip = await response.json();
            
            if (!response.ok) {
                alert('Error loading trip details');
                return;
            }
            
            // Create modal if it doesn't exist
            let modal = document.getElementById('trip-modal');
            if (!modal) {
                modal = createTripModal();
                document.body.appendChild(modal);
            }
            
            // Populate modal with trip data
            updateTripModal(trip);
            
            // Show modal
            modal.style.display = 'block';
            
        } catch (error) {
            console.error('Error loading trip details:', error);
            alert('Failed to load trip details');
        }
    }
    
    function createTripModal() {
        const modal = document.createElement('div');
        modal.id = 'trip-modal';
        modal.className = 'modal';
        modal.innerHTML = `
            <div class="modal-content">
                <span class="modal-close" onclick="document.getElementById('trip-modal').style.display='none'">&times;</span>
                <h2 id="trip-modal-title">Trip Details</h2>
                <div id="trip-modal-body">
                    <div class="trip-stats-grid"></div>
                    <div id="trip-map"></div>
                    <div id="trip-energy-chart" class="chart-container"></div>
                    <div id="trip-speed-chart" class="chart-container"></div>
                    <div id="trip-efficiency-chart" class="chart-container"></div>
                </div>
            </div>
        `;
        
        // Close modal when clicking outside
        modal.onclick = function(event) {
            if (event.target === modal) {
                modal.style.display = 'none';
            }
        };
        
        return modal;
    }
    
    function updateTripModal(trip) {
        const units = currentUnits;
        
        // Update title
        document.getElementById('trip-modal-title').textContent = `Trip on ${formatDate(trip.date)}`;
        
        // Calculate statistics
        const distance = units === 'imperial' 
            ? Math.round(conversions.kmToMiles(trip.distance || 0))
            : (trip.distance || 0);
        const distanceUnit = units === 'imperial' ? 'mi' : 'km';
        const speedUnit = units === 'imperial' ? 'mph' : 'km/h';
        const avgSpeed = units === 'imperial'
            ? Math.round(conversions.kmToMiles(trip.average_speed || 0))
            : (trip.average_speed || 0);
        const maxSpeed = units === 'imperial'
            ? Math.round(conversions.kmToMiles(trip.max_speed || 0))
            : (trip.max_speed || 0);
        
        // Create stats grid
        const statsGrid = document.querySelector('.trip-stats-grid');
        statsGrid.innerHTML = `
            <div class="stat-card">
                <h3>Distance</h3>
                <p>${distance} ${distanceUnit}</p>
            </div>
            <div class="stat-card">
                <h3>Duration</h3>
                <p>${trip.duration || '--'} min</p>
            </div>
            <div class="stat-card">
                <h3>Average Speed</h3>
                <p>${avgSpeed} ${speedUnit}</p>
            </div>
            <div class="stat-card">
                <h3>Max Speed</h3>
                <p>${maxSpeed} ${speedUnit}</p>
            </div>
            <div class="stat-card">
                <h3>Idle Time</h3>
                <p>${trip.idle_time || '--'} min</p>
            </div>
            <div class="stat-card">
                <h3>Efficiency</h3>
                <p>${trip.efficiency_wh_per_km ? (1000 / (trip.efficiency_wh_per_km * 1.60934)).toFixed(2) : '--'} mi/kWh</p>
            </div>
            <div class="stat-card">
                <h3>Net Energy</h3>
                <p>${trip.net_energy || trip.total_consumed || '--'} Wh</p>
            </div>
            <div class="stat-card">
                <h3>Regenerated</h3>
                <p>${trip.regenerated_energy || '--'} Wh</p>
            </div>
        `;
        
        // Create energy breakdown chart
        createEnergyBreakdownChart(trip);
        
        // Create speed/time chart if we have enough data
        if (trip.duration && trip.average_speed) {
            createSpeedChart(trip);
        }
        
        // Create efficiency comparison chart
        createEfficiencyChart(trip);
        
        // Show map if location available
        if (trip.end_latitude && trip.end_longitude) {
            document.getElementById('trip-map').style.display = 'block';
            showTripMap(trip);
        } else {
            document.getElementById('trip-map').style.display = 'none';
        }
    }
    
    function createEnergyBreakdownChart(trip) {
        const data = [{
            values: [
                trip.drivetrain_consumed || 0,
                trip.climate_consumed || 0,
                trip.accessories_consumed || 0,
                trip.battery_care_consumed || 0
            ],
            labels: ['Drivetrain', 'Climate', 'Accessories', 'Battery Care'],
            type: 'pie',
            hole: 0.4,
            marker: {
                colors: ['#3498db', '#e74c3c', '#f39c12', '#2ecc71']
            },
            textinfo: 'label+value',
            textposition: 'outside'
        }];
        
        const layout = {
            title: 'Energy Consumption Breakdown (Wh)',
            height: 300,
            margin: { t: 50, b: 50, l: 50, r: 50 }
        };
        
        Plotly.newPlot('trip-energy-chart', data, layout, {responsive: true});
    }
    
    function createSpeedChart(trip) {
        // Create a simple visualization based on available data
        const data = [{
            x: ['Start', 'Average', 'Max', 'End'],
            y: [0, trip.average_speed || 0, trip.max_speed || 0, 0],
            type: 'scatter',
            mode: 'lines+markers',
            line: { color: '#3498db', width: 3 },
            marker: { size: 8 }
        }];
        
        const layout = {
            title: 'Speed Profile',
            xaxis: { title: 'Trip Progress' },
            yaxis: { title: `Speed (${currentUnits === 'imperial' ? 'mph' : 'km/h'})` },
            height: 300,
            margin: { t: 50, b: 50, l: 60, r: 50 }
        };
        
        Plotly.newPlot('trip-speed-chart', data, layout, {responsive: true});
    }
    
    function createEfficiencyChart(trip) {
        // Compare this trip's efficiency to overall average
        const efficiency = trip.efficiency_wh_per_km || 0;
        
        const data = [{
            x: ['This Trip', 'Average (est)'],
            y: [efficiency, 180], // 180 Wh/km is typical EV efficiency
            type: 'bar',
            marker: {
                color: [efficiency < 180 ? '#2ecc71' : '#e74c3c', '#3498db']
            }
        }];
        
        const layout = {
            title: 'Energy Efficiency Comparison',
            yaxis: { title: `Wh/${currentUnits === 'imperial' ? 'mi' : 'km'}` },
            height: 300,
            margin: { t: 50, b: 50, l: 60, r: 50 }
        };
        
        Plotly.newPlot('trip-efficiency-chart', data, layout, {responsive: true});
    }
    
    // Make showTripDetails available globally
    window.showTripDetails = showTripDetails;

    // Auto-refresh every 30 minutes (to stay within API limits)
    setInterval(() => {
        loadCurrentStatus();
        loadBatteryHistory();
        loadTrips();
        loadCollectionStatus();
        loadEfficiencyStats();
        loadLocationsMap();
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