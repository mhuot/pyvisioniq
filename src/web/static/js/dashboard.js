// Global map variables
let locationsMap = null;
let tripMap = null;
let mapMarkers = [];

// Chart instances
let batteryChart = null;
let energyChart = null;
let tempEfficiencyChart = null;

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
        // Reload trips with current pagination settings
        loadTripsWithPagination(currentTimeRange, currentStartDate, currentEndDate);
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
                // Store current battery level for active charging session display
                currentData.battery = data.battery_level;
                
                // Add charging effect to battery level display
                if (data.is_charging) {
                    batteryLevel.classList.add('charging');
                } else {
                    batteryLevel.classList.remove('charging');
                }
                
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
                        // Store current charging power for active session display
                        currentData.chargingPower = data.charging_power;
                    } else {
                        chargingRate.textContent = 'Charging';
                        currentData.chargingPower = null;
                    }
                } else {
                    chargingIndicator.style.display = 'none';
                    chargingRate.style.display = 'none';
                    currentData.chargingPower = null;
                }
            }
            
            if (data.range !== null && range) {
                const rangeValue = currentUnits === 'metric' ? 
                    data.range : conversions.kmToMiles(data.range);
                range.textContent = Math.round(rangeValue) + (currentUnits === 'metric' ? ' km' : ' mi');
            }
            
            if (temperature) {
                // Check if we have weather data from Meteo
                if (data.weather && data.weather_source === 'meteo') {
                    // Weather data from Meteo comes in Fahrenheit
                    const tempF = data.weather.temperature;
                    const tempValue = currentUnits === 'metric' ? 
                        conversions.fahrenheitToCelsius(tempF) : tempF;
                    
                    let weatherText = Math.round(tempValue) + '°' + (currentUnits === 'metric' ? 'C' : 'F');
                    
                    // Add weather description if available
                    if (data.weather.description) {
                        weatherText += ` - ${data.weather.description}`;
                    }
                    
                    temperature.innerHTML = weatherText;
                    
                    // Add title attribute with more details including vehicle sensor comparison
                    let titleText = '';
                    if (data.weather.feels_like && data.weather.humidity) {
                        const feelsLike = currentUnits === 'metric' ? 
                            conversions.fahrenheitToCelsius(data.weather.feels_like) : data.weather.feels_like;
                        titleText = `Feels like: ${Math.round(feelsLike)}°, Humidity: ${data.weather.humidity}%, Wind: ${data.weather.wind_speed} mph`;
                    }
                    
                    // Add vehicle sensor comparison if available
                    if (data.vehicle_temp !== null && data.vehicle_temp !== undefined) {
                        const vehicleTempValue = currentUnits === 'metric' ? 
                            data.vehicle_temp : conversions.celsiusToFahrenheit(data.vehicle_temp);
                        titleText += `\nVehicle sensor: ${Math.round(vehicleTempValue)}°${currentUnits === 'metric' ? 'C' : 'F'}`;
                        
                        // Show difference
                        if (data.meteo_temp !== null) {
                            const diff = Math.abs(data.meteo_temp - data.vehicle_temp);
                            titleText += ` (${diff.toFixed(1)}° difference)`;
                        }
                    }
                    
                    if (titleText) {
                        temperature.title = titleText;
                    }
                } else if (data.temperature !== null) {
                    // Fallback to vehicle sensor (already in Celsius)
                    const tempValue = currentUnits === 'metric' ? 
                        data.temperature : conversions.celsiusToFahrenheit(data.temperature);
                    temperature.textContent = Math.round(tempValue) + '°' + (currentUnits === 'metric' ? 'C' : 'F') + ' (vehicle)';
                }
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
            
            // Update data freshness indicator based on api_last_updated
            const dataFreshness = document.getElementById('data-freshness');
            if (dataFreshness && data.api_last_updated) {
                try {
                    const apiUpdateTime = new Date(data.api_last_updated);
                    const now = new Date();
                    const ageMinutes = Math.floor((now - apiUpdateTime) / (1000 * 60));
                    
                    let freshnessText, className, title;
                    
                    if (ageMinutes < 5) {
                        // Very fresh data (less than 5 minutes)
                        freshnessText = ' (fresh - ' + ageMinutes + 'm ago)';
                        className = 'data-freshness fresh';
                        title = 'Vehicle data is very recent (' + ageMinutes + ' minutes old)';
                    } else if (ageMinutes < 60) {
                        // Recent data (less than 1 hour)
                        freshnessText = ' (' + ageMinutes + 'm ago)';
                        className = 'data-freshness recent';
                        title = 'Vehicle data is ' + ageMinutes + ' minutes old';
                    } else if (ageMinutes < 1440) {
                        // Old data (less than 24 hours)
                        const ageHours = Math.floor(ageMinutes / 60);
                        const remainingMinutes = ageMinutes % 60;
                        freshnessText = ' (' + ageHours + 'h ' + remainingMinutes + 'm ago)';
                        className = 'data-freshness old';
                        title = 'Vehicle data is ' + ageHours + ' hours and ' + remainingMinutes + ' minutes old - click Refresh Data';
                    } else {
                        // Very old data (more than 24 hours)
                        const ageDays = Math.floor(ageMinutes / 1440);
                        freshnessText = ' (' + ageDays + ' days ago)';
                        className = 'data-freshness very-old';
                        title = 'Vehicle data is ' + ageDays + ' days old - click Refresh Data for current information';
                    }
                    
                    dataFreshness.textContent = freshnessText;
                    dataFreshness.className = className;
                    dataFreshness.title = title;
                    
                } catch (error) {
                    // Fallback to is_cached if api_last_updated parsing fails
                    if (data.is_cached === true) {
                        dataFreshness.textContent = ' (cached data)';
                        dataFreshness.className = 'data-freshness cached';
                        dataFreshness.title = 'This data was served from cache - click Refresh Data for fresh API data';
                    } else if (data.is_cached === false) {
                        dataFreshness.textContent = ' (fresh from API)';
                        dataFreshness.className = 'data-freshness fresh';
                        dataFreshness.title = 'This data was just fetched from the vehicle API';
                    } else {
                        dataFreshness.textContent = '';
                        dataFreshness.className = 'data-freshness';
                    }
                }
            } else if (dataFreshness) {
                // Fallback to is_cached if no api_last_updated
                if (data.is_cached === true) {
                    dataFreshness.textContent = ' (cached data)';
                    dataFreshness.className = 'data-freshness cached';
                    dataFreshness.title = 'This data was served from cache - click Refresh Data for fresh API data';
                } else if (data.is_cached === false) {
                    dataFreshness.textContent = ' (fresh from API)';
                    dataFreshness.className = 'data-freshness fresh';
                    dataFreshness.title = 'This data was just fetched from the vehicle API';
                } else {
                    dataFreshness.textContent = '';
                    dataFreshness.className = 'data-freshness';
                }
            }
        } catch (error) {
            console.error('Error loading current status:', error);
        }
    }
    
    // Load initial data
    loadCurrentStatus();
    loadBatteryHistory(24); // Default to 24 hours
    loadCollectionStatus();
    loadEfficiencyStats(24);
    loadChargingSessions(24);
    loadTemperatureEfficiency(24);
    
    // Initialize and load map
    initializeLocationsMap().then(() => {
        loadLocationsMap(24);
    });
    
    // Master time range button handlers
    const timeRangeButtons = document.querySelectorAll('.master-time-range-controls .time-range-btn');
    let currentTimeRange = '24'; // Default to 24 hours
    let currentStartDate = null;
    let currentEndDate = null;
    
    const customDateRange = document.getElementById('custom-date-range');
    const applyCustomRangeBtn = document.getElementById('apply-custom-range');
    const masterStartDate = document.getElementById('master-start-date');
    const masterEndDate = document.getElementById('master-end-date');
    
    timeRangeButtons.forEach(button => {
        button.addEventListener('click', function() {
            // Update active state
            timeRangeButtons.forEach(btn => {
                btn.classList.remove('active');
                btn.setAttribute('aria-pressed', 'false');
            });
            this.classList.add('active');
            this.setAttribute('aria-pressed', 'true');
            
            // Update current time range
            currentTimeRange = this.getAttribute('data-hours');
            
            // Show/hide custom date range
            if (currentTimeRange === 'custom') {
                customDateRange.style.display = 'flex';
                // Set default dates if not already set
                if (!masterStartDate.value) {
                    const today = new Date();
                    const lastWeek = new Date(today.getTime() - 7 * 24 * 60 * 60 * 1000);
                    masterStartDate.value = lastWeek.toISOString().split('T')[0];
                    masterEndDate.value = today.toISOString().split('T')[0];
                }
            } else {
                customDateRange.style.display = 'none';
                currentStartDate = null;
                currentEndDate = null;
                // Reload all time-dependent data
                loadAllDataForTimeRange(currentTimeRange);
            }
        });
    });
    
    // Custom date range apply button
    if (applyCustomRangeBtn) {
        applyCustomRangeBtn.addEventListener('click', function() {
            currentStartDate = masterStartDate.value;
            currentEndDate = masterEndDate.value;
            
            if (!currentStartDate || !currentEndDate) {
                showNotification('Please select both start and end dates', 'warning');
                return;
            }
            
            if (currentStartDate > currentEndDate) {
                showNotification('Start date must be before end date', 'warning');
                return;
            }
            
            // Load data with custom date range
            loadAllDataForTimeRange('custom', currentStartDate, currentEndDate);
        });
    }
    
    // Function to load all data for a specific time range
    async function loadAllDataForTimeRange(hours, startDate = null, endDate = null) {
        // Show loading indicator
        showNotification('Updating data for selected time range...', 'info');
        
        // Load all time-dependent data in parallel
        await Promise.all([
            loadBatteryHistory(hours, startDate, endDate),
            loadTripsWithPagination(hours, startDate, endDate),
            loadLocationsMap(hours, startDate, endDate),
            loadEfficiencyStats(hours, startDate, endDate),
            loadChargingSessions(hours, startDate, endDate),
            loadTemperatureEfficiency(hours, startDate, endDate)
        ]);
        
        showNotification('Data updated', 'success');
    }
    
    // Helper function to suggest next longer time range
    function getNextLongerTimeRange(currentRange) {
        const ranges = ['24', '48', '168', '720', 'all'];
        const currentIndex = ranges.indexOf(currentRange);
        if (currentIndex >= 0 && currentIndex < ranges.length - 1) {
            return ranges[currentIndex + 1];
        }
        return 'all';
    }
    
    // Helper function to get human-readable time range
    function getTimeRangeLabel(hours) {
        switch(hours) {
            case '24': return '24 hours';
            case '48': return '48 hours';
            case '168': return '7 days';
            case '720': return '30 days';
            case 'all': return 'all time';
            case 'custom': return 'custom range';
            default: return hours + ' hours';
        }
    }
    
    // Function to show empty state with time range suggestion
    function showEmptyStateWithSuggestion(container, currentRange, dataType = 'data') {
        const nextRange = getNextLongerTimeRange(currentRange);
        const currentLabel = getTimeRangeLabel(currentRange);
        const nextLabel = getTimeRangeLabel(nextRange);
        
        const emptyStateHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">📭</div>
                <h3>No ${dataType} in ${currentLabel}</h3>
                <p>Try expanding the time range to see recent activity</p>
                <button class="btn-secondary expand-range-btn" data-range="${nextRange}">
                    Try ${nextLabel}
                </button>
            </div>
        `;
        
        container.innerHTML = emptyStateHTML;
        
        // Add click handler for the suggestion button
        const expandBtn = container.querySelector('.expand-range-btn');
        if (expandBtn) {
            expandBtn.addEventListener('click', async function() {
                const suggestedRange = this.getAttribute('data-range');
                
                // If this is a map container, restore it before loading new data
                if (container.id === 'locations-map') {
                    container.innerHTML = '';
                    container.className = 'map-container';
                    // Re-initialize the map
                    await initializeLocationsMap();
                }
                
                // Update the active time range button
                const timeRangeButtons = document.querySelectorAll('.master-time-range-controls .time-range-btn');
                timeRangeButtons.forEach(btn => {
                    btn.classList.remove('active');
                    btn.setAttribute('aria-pressed', 'false');
                    if (btn.getAttribute('data-hours') === suggestedRange) {
                        btn.classList.add('active');
                        btn.setAttribute('aria-pressed', 'true');
                    }
                });
                
                // Update current range and load data
                currentTimeRange = suggestedRange;
                loadAllDataForTimeRange(suggestedRange);
            });
        }
    }

    // Refresh button handler
    refreshBtn.addEventListener('click', async function() {
        // First, get current API usage
        try {
            const statusResponse = await fetch('/api/collection-status');
            const statusData = await statusResponse.json();
            
            const callsToday = statusData.calls_today || 0;
            const dailyLimit = statusData.daily_limit || 30;
            const callsRemaining = dailyLimit - callsToday;
            
            // Show warning dialog with accessible formatting
            const message = `Manual refresh will use 1 of your ${dailyLimit} daily API calls.\n\n` +
                          `Current usage: ${callsToday} of ${dailyLimit} calls\n` +
                          `Calls remaining: ${callsRemaining}\n\n` +
                          `Do you want to continue?`;
            
            // For screen readers, announce the warning
            const announcement = `Warning: Manual refresh will use 1 API call. You have used ${callsToday} of ${dailyLimit} calls today.`;
            const liveRegion = document.createElement('div');
            liveRegion.setAttribute('role', 'alert');
            liveRegion.setAttribute('aria-live', 'assertive');
            liveRegion.className = 'sr-only';
            liveRegion.textContent = announcement;
            document.body.appendChild(liveRegion);
            
            // Give screen readers time to announce before showing dialog
            await new Promise(resolve => setTimeout(resolve, 100));
            
            const userConfirmed = confirm(message);
            document.body.removeChild(liveRegion);
            
            if (!userConfirmed) {
                return;
            }
            
            // Check if limit reached
            if (callsToday >= dailyLimit) {
                showNotification('Daily API limit reached. Try again tomorrow.', 'warning');
                return;
            }
            
        } catch (error) {
            console.error('Error checking API status:', error);
            // Continue anyway if we can't check status
        }
        
        refreshBtn.disabled = true;
        refreshBtn.textContent = 'Refreshing...';
        
        try {
            const response = await fetch('/api/refresh');
            const data = await response.json();
            
            if (response.ok) {
                showNotification(data.message || 'Data refreshed successfully', 'success');
                // Reload all data including collection status
                loadCurrentStatus();
                if (currentTimeRange === 'custom') {
                    loadAllDataForTimeRange('custom', currentStartDate, currentEndDate);
                } else {
                    loadBatteryHistory(currentTimeRange);
                    loadTripsWithPagination(currentTimeRange);
                    loadEfficiencyStats(currentTimeRange);
                    loadLocationsMap(currentTimeRange);
                    loadChargingSessions(currentTimeRange);
                    loadTemperatureEfficiency(currentTimeRange);
                }
                loadCollectionStatus(); // Update API usage display
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
    
    async function loadBatteryHistory(hours = 24, startDate = null, endDate = null) {
        try {
            // Show loading state
            const chartContainer = document.querySelector('#battery-chart').parentElement;
            chartContainer.style.position = 'relative';
            
            // Add loading overlay
            const loadingOverlay = document.createElement('div');
            loadingOverlay.className = 'chart-loading';
            loadingOverlay.innerHTML = '<div class="loading-spinner"></div><p>Loading data...</p>';
            chartContainer.appendChild(loadingOverlay);
            
            // Build query parameters
            const params = new URLSearchParams({ hours });
            if (startDate) params.append('start_date', startDate);
            if (endDate) params.append('end_date', endDate);
            
            const response = await fetch(`/api/battery-history?${params}`);
            const data = await response.json();
            currentData.batteryHistory = data;
            
            // Remove loading overlay
            if (loadingOverlay.parentElement) {
                loadingOverlay.remove();
            }
            
            updateBatteryChart(data);
        } catch (error) {
            console.error('Error loading battery history:', error);
            // Remove loading overlay on error
            const loadingOverlay = document.querySelector('.chart-loading');
            if (loadingOverlay) {
                loadingOverlay.remove();
            }
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
                conversions.celsiusToFahrenheit(d.temperature) : d.temperature,
            is_cached: d.is_cached
        }));
        
        // Update existing chart or create new one
        if (batteryChart) {
            // Update existing chart data
            batteryChart.data.datasets[0].data = chartData.map(d => ({x: d.x, y: d.battery}));
            batteryChart.data.datasets[1].data = chartData.map(d => ({x: d.x, y: d.temperature}));
            
            // Update point colors based on cache status
            batteryChart.data.datasets[0].pointBackgroundColor = chartData.map(d => 
                d.is_cached ? 'rgba(52, 152, 219, 0.5)' : '#3498db'
            );
            batteryChart.data.datasets[0].pointBorderColor = chartData.map(d => 
                d.is_cached ? 'rgba(52, 152, 219, 0.8)' : '#2980b9'
            );
            
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
                    pointHoverRadius: 5,
                    // Different colors for cached vs fresh data
                    pointBackgroundColor: chartData.map(d => 
                        d.is_cached ? 'rgba(52, 152, 219, 0.5)' : '#3498db'
                    ),
                    pointBorderColor: chartData.map(d => 
                        d.is_cached ? 'rgba(52, 152, 219, 0.8)' : '#2980b9'
                    )
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
                            padding: 15,
                            generateLabels: function(chart) {
                                const original = Chart.defaults.plugins.legend.labels.generateLabels(chart);
                                // Add custom legend items for data source
                                original.push({
                                    text: 'Fresh data',
                                    fillStyle: '#3498db',
                                    strokeStyle: '#2980b9',
                                    lineWidth: 2,
                                    hidden: false,
                                    index: original.length
                                });
                                original.push({
                                    text: 'Cached data',
                                    fillStyle: 'rgba(52, 152, 219, 0.5)',
                                    strokeStyle: 'rgba(52, 152, 219, 0.8)',
                                    lineWidth: 2,
                                    hidden: false,
                                    index: original.length + 1
                                });
                                return original;
                            }
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
                            },
                            afterLabel: function(context) {
                                // Add cache status for battery level dataset
                                if (context.datasetIndex === 0) {
                                    const dataIndex = context.dataIndex;
                                    const isCached = chartData[dataIndex].is_cached;
                                    return isCached ? '(Cached data)' : '(Fresh data)';
                                }
                                return '';
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
        
        trips.forEach((trip) => {
            const row = document.createElement('tr');
            row.className = 'clickable';
            
            const date = new Date(trip.date);
            const distance = currentUnits === 'metric' ? 
                trip.distance : conversions.kmToMiles(trip.distance);
            const avgSpeed = trip.average_speed ? 
                (currentUnits === 'metric' ? trip.average_speed : conversions.kmToMiles(trip.average_speed)) : null;
            
            // Calculate efficiency display
            let efficiencyDisplay = '-';
            if (trip.efficiency_wh_per_km) {
                if (currentUnits === 'metric') {
                    efficiencyDisplay = `${Math.round(trip.efficiency_wh_per_km)} Wh/km`;
                } else {
                    // Convert Wh/km to mi/kWh
                    const miPerKwh = conversions.whPerKmToMiPerKwh(trip.efficiency_wh_per_km);
                    efficiencyDisplay = `${miPerKwh.toFixed(1)} mi/kWh`;
                }
            }
            
            row.innerHTML = `
                <td>${formatDate(date)}</td>
                <td>${distance.toFixed(1)} ${currentUnits === 'metric' ? 'km' : 'mi'}</td>
                <td>${trip.duration || '-'} min</td>
                <td>${avgSpeed ? avgSpeed.toFixed(1) : '-'} ${currentUnits === 'metric' ? 'km/h' : 'mph'}</td>
                <td>${efficiencyDisplay}</td>
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
        // Handle special cases
        if (date === 'Current Location' || !date) {
            return 'Current Location';
        }
        
        // Clean pandas timestamp format (remove .0 suffix)
        if (typeof date === 'string') {
            date = date.replace(/\.0+$/, '');
        }
        
        // Try to create Date object
        const dateObj = new Date(date);
        
        // Check if date is valid
        if (isNaN(dateObj.getTime())) {
            return date.toString(); // Return original if can't parse
        }
        
        const options = { 
            month: 'short', 
            day: 'numeric', 
            hour: '2-digit', 
            minute: '2-digit' 
        };
        return dateObj.toLocaleDateString('en-US', options);
    }
    
    async function loadCollectionStatus() {
        try {
            const response = await fetch('/api/collection-status');
            const data = await response.json();
            
            const callsToday = document.getElementById('api-calls-today');
            const nextCollection = document.getElementById('next-collection');
            const headerApiCalls = document.getElementById('header-api-calls');
            
            if (callsToday) {
                callsToday.textContent = `${data.calls_today}/${data.daily_limit}`;
                callsToday.className = data.calls_today >= data.daily_limit ? 'limit-reached' : '';
            }
            
            // Update header API usage indicator
            if (headerApiCalls) {
                const callsText = `${data.calls_today}/${data.daily_limit}`;
                headerApiCalls.textContent = callsText;
                
                // Update aria-label for screen readers
                const percentage = Math.round((data.calls_today / data.daily_limit) * 100);
                let ariaLabel = `${data.calls_today} of ${data.daily_limit} API calls used today (${percentage}%)`;
                
                // Apply warning classes and update aria-label
                headerApiCalls.className = '';
                if (data.calls_today >= data.daily_limit) {
                    headerApiCalls.className = 'limit-reached';
                    ariaLabel += '. Daily limit reached.';
                } else if (data.calls_today >= data.daily_limit * 0.8) {
                    headerApiCalls.className = 'limit-warning';
                    ariaLabel += '. Approaching daily limit.';
                }
                
                headerApiCalls.setAttribute('aria-label', ariaLabel);
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

    async function initializeLocationsMap() {
        try {
            const mapElement = document.getElementById('locations-map');
            if (!mapElement) {
                console.error('Map element not found: locations-map');
                return;
            }
            
            // If we already have a valid map instance, just return
            if (locationsMap && locationsMap._container && locationsMap._container.parentNode) {
                return;
            }
            
            // Check if the container has an existing Leaflet map that we don't know about
            if (mapElement._leaflet_id && mapElement.querySelector('.leaflet-container')) {
                // Try to find and use the existing map instance
                // This can happen after showing/hiding empty states
                console.log('Map container already has Leaflet instance, skipping initialization');
                return;
            }
            
            // Clean up any existing map instance
            if (locationsMap) {
                try {
                    locationsMap.remove();
                } catch (e) {
                    console.warn('Error removing existing map:', e);
                }
                locationsMap = null;
                mapMarkers = [];
            }
            
            // Ensure the map container has the right class
            mapElement.className = 'map-container';
            
            // Only clear if it has empty state content
            if (mapElement.innerHTML.includes('empty-state')) {
                mapElement.innerHTML = '';
                // Wait for DOM to update
                await new Promise(resolve => setTimeout(resolve, 50));
            }
            
            // Double-check the container is truly empty of Leaflet instances
            if (mapElement._leaflet_id) {
                console.warn('Container still has _leaflet_id after cleanup, aborting initialization');
                return;
            }
            
            // Create new map instance
            locationsMap = L.map('locations-map').setView([44.9778, -93.2650], 10);
            
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '© OpenStreetMap contributors'
            }).addTo(locationsMap);
            
        } catch (error) {
            console.error('Error initializing locations map:', error);
            // If initialization fails, ensure we clear the reference
            locationsMap = null;
        }
    }
    
    async function loadLocationsMap(hours = 'all', startDate = null, endDate = null) {
        try {
            // Initialize map if it doesn't exist
            if (!locationsMap) {
                await initializeLocationsMap();
                
                // If initialization failed, skip loading
                if (!locationsMap) {
                    console.warn('Failed to initialize locations map, skipping load');
                    return;
                }
            }
            
            const params = new URLSearchParams({ hours });
            if (startDate) params.append('start_date', startDate);
            if (endDate) params.append('end_date', endDate);
            
            const response = await fetch(`/api/locations?${params}`);
            if (response.ok) {
                const locations = await response.json();
                console.log(`Loaded ${locations.length} locations`);
                
                mapMarkers.forEach(marker => locationsMap.removeLayer(marker));
                mapMarkers = [];
                
                // Check for empty state
                if (locations.length === 0 && hours !== 'all' && hours !== 'custom') {
                    const mapContainer = document.getElementById('locations-map');
                    if (mapContainer) {
                        // Store original map content before replacing
                        if (!mapContainer.dataset.originalContent) {
                            mapContainer.dataset.originalContent = 'true';
                        }
                        
                        // Clear any existing map instance to avoid memory leaks
                        if (locationsMap) {
                            locationsMap.remove();
                            locationsMap = null;
                        }
                        
                        showEmptyStateWithSuggestion(mapContainer, hours, 'trip locations');
                        return;
                    }
                }
                
                // Ensure map is initialized before adding locations
                if (!locationsMap) {
                    await initializeLocationsMap();
                }
                
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
    
    async function loadEfficiencyStats(hours = 'all', startDate = null, endDate = null) {
        try {
            const params = new URLSearchParams({ hours });
            if (startDate) params.append('start_date', startDate);
            if (endDate) params.append('end_date', endDate);
            
            const response = await fetch(`/api/efficiency-stats?${params}`);
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
    
    async function loadTemperatureEfficiency(hours = 'all', startDate = null, endDate = null) {
        try {
            const params = new URLSearchParams({ hours });
            if (startDate) params.append('start_date', startDate);
            if (endDate) params.append('end_date', endDate);
            
            const response = await fetch(`/api/temperature-efficiency?${params}`);
            const data = await response.json();
            
            if (!response.ok || !data.raw_data || data.raw_data.length === 0) {
                const container = document.getElementById('temperature-stats');
                if (container) {
                    if (hours !== 'all' && hours !== 'custom') {
                        showEmptyStateWithSuggestion(container, hours, 'efficiency data');
                    } else {
                        container.innerHTML = '<div class="no-data">Not enough data to show temperature impact on efficiency</div>';
                    }
                }
                return;
            }
            
            // Create scatter plot with trend line
            const ctx = document.getElementById('temperature-efficiency-chart').getContext('2d');
            
            // Prepare data for scatter plot
            const scatterData = data.raw_data.map(point => ({
                x: point.temperature,
                y: point.efficiency
            }));
            
            // Prepare data for bar chart (binned data)
            const barLabels = data.temperature_bins.map(bin => bin.temperature_range);
            const barData = data.temperature_bins.map(bin => bin.avg_efficiency);
            
            // Update or create chart
            if (tempEfficiencyChart) {
                tempEfficiencyChart.data.datasets[0].data = scatterData;
                tempEfficiencyChart.data.datasets[1].data = barData;
                tempEfficiencyChart.data.labels = barLabels;
                tempEfficiencyChart.update('none');
            } else {
                tempEfficiencyChart = new Chart(ctx, {
                    type: 'scatter',
                    data: {
                        labels: barLabels,
                        datasets: [{
                            label: 'Trip Efficiency',
                            data: scatterData,
                            backgroundColor: 'rgba(52, 152, 219, 0.5)',
                            borderColor: 'rgba(52, 152, 219, 1)',
                            pointRadius: 5,
                            type: 'scatter'
                        }, {
                            label: 'Average by Temperature Range',
                            data: barData,
                            backgroundColor: 'rgba(231, 76, 60, 0.7)',
                            borderColor: 'rgba(231, 76, 60, 1)',
                            borderWidth: 2,
                            type: 'bar',
                            yAxisID: 'y',
                            xAxisID: 'x2'
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: {
                            mode: 'index',
                            intersect: false
                        },
                        plugins: {
                            title: {
                                display: true,
                                text: 'How Temperature Affects Your EV Efficiency',
                                font: { size: 16 }
                            },
                            legend: {
                                display: true,
                                position: 'top'
                            },
                            tooltip: {
                                callbacks: {
                                    label: function(context) {
                                        if (context.dataset.type === 'scatter') {
                                            return `${context.parsed.x.toFixed(1)}°C: ${context.parsed.y.toFixed(2)} mi/kWh`;
                                        } else {
                                            const binIndex = context.dataIndex;
                                            const bin = data.temperature_bins[binIndex];
                                            return [
                                                `Average: ${context.parsed.y.toFixed(2)} mi/kWh`,
                                                `Trips: ${bin.trip_count}`,
                                                `Distance: ${bin.total_distance} km`
                                            ];
                                        }
                                    }
                                }
                            }
                        },
                        scales: {
                            x: {
                                type: 'linear',
                                position: 'bottom',
                                title: {
                                    display: true,
                                    text: 'Temperature (°C)'
                                },
                                grid: {
                                    display: true
                                }
                            },
                            x2: {
                                type: 'category',
                                position: 'bottom',
                                display: false,
                                grid: {
                                    display: false
                                }
                            },
                            y: {
                                type: 'linear',
                                title: {
                                    display: true,
                                    text: 'Efficiency (mi/kWh)'
                                },
                                min: 0
                            }
                        }
                    }
                });
            }
            
            // Display statistics
            let statsHtml = '<div class="temp-efficiency-stats">';
            statsHtml += '<h3>Temperature Impact Summary</h3>';
            statsHtml += '<div class="stats-grid">';
            
            // Find best and worst temperature ranges
            const bestBin = data.temperature_bins.reduce((a, b) => 
                a.avg_efficiency > b.avg_efficiency ? a : b
            );
            const worstBin = data.temperature_bins.reduce((a, b) => 
                a.avg_efficiency < b.avg_efficiency ? a : b
            );
            
            statsHtml += `
                <div class="stat-item">
                    <span class="stat-label">Best Efficiency Range:</span>
                    <span class="stat-value">${bestBin.temperature_range}</span>
                    <span class="stat-detail">${bestBin.avg_efficiency} mi/kWh average</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">Worst Efficiency Range:</span>
                    <span class="stat-value">${worstBin.temperature_range}</span>
                    <span class="stat-detail">${worstBin.avg_efficiency} mi/kWh average</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">Efficiency Loss:</span>
                    <span class="stat-value">${((1 - worstBin.avg_efficiency / bestBin.avg_efficiency) * 100).toFixed(1)}%</span>
                    <span class="stat-detail">From best to worst conditions</span>
                </div>
            `;
            
            statsHtml += '</div></div>';
            document.getElementById('temperature-stats').innerHTML = statsHtml;
            
        } catch (error) {
            console.error('Error loading temperature efficiency data:', error);
            document.getElementById('temperature-stats').innerHTML = 
                '<div class="no-data">Error loading temperature efficiency data</div>';
        }
    }
    
    async function loadChargingSessions(hours = 'all', startDate = null, endDate = null) {
        try {
            const params = new URLSearchParams({ hours });
            if (startDate) params.append('start_date', startDate);
            if (endDate) params.append('end_date', endDate);
            
            console.log(`Loading charging sessions with params: ${params.toString()}`);
            const response = await fetch(`/api/charging-sessions?${params}`);
            const sessions = await response.json();
            console.log(`Received ${sessions.length} charging sessions:`, sessions);
            
            const container = document.getElementById('charging-sessions-container');
            if (!container) return;
            
            if (sessions.length === 0) {
                if (hours !== 'all' && hours !== 'custom') {
                    showEmptyStateWithSuggestion(container, hours, 'charging sessions');
                } else {
                    container.innerHTML = '<div class="no-data">No charging sessions recorded yet</div>';
                }
                return;
            }
            
            let html = '<div class="sessions-grid">';
            
            sessions.forEach(session => {
                console.log(`Session ${session.session_id}: is_complete=${session.is_complete}, active=${!session.is_complete}`);
                
                const startTime = new Date(session.start_time);
                let duration = session.duration_minutes;
                
                // For active sessions, calculate real-time duration
                if (!session.is_complete) {
                    console.log(`Active session found: ${session.session_id}`);
                    const now = new Date();
                    duration = Math.floor((now - startTime) / 60000); // Convert to minutes
                }
                
                const formatDuration = (minutes) => {
                    if (!minutes && minutes !== 0) return '--';
                    const hours = Math.floor(minutes / 60);
                    const mins = Math.round(minutes % 60);
                    return hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;
                };
                
                const sessionClass = session.is_complete ? 'session-complete' : 'session-active';
                
                html += `
                    <div class="charging-session ${sessionClass}">
                        <div class="session-header">
                            <h4>${formatDate(startTime)}</h4>
                            ${!session.is_complete ? '<span class="charging-badge">⚡ Charging</span>' : ''}
                        </div>
                        <div class="session-details">
                            <div class="session-stat">
                                <span class="stat-label">Duration</span>
                                <span class="stat-value">${formatDuration(duration)}${!session.is_complete ? ' (ongoing)' : ''}</span>
                            </div>
                            <div class="session-stat">
                                <span class="stat-label">Battery</span>
                                <span class="stat-value">${session.start_battery}% → ${!session.is_complete && currentData.battery ? currentData.battery + '%' : session.end_battery + '%'}</span>
                            </div>
                            <div class="session-stat">
                                <span class="stat-label">Energy Added</span>
                                <span class="stat-value">${session.energy_added.toFixed(1)} kWh</span>
                            </div>
                            <div class="session-stat">
                                <span class="stat-label">${!session.is_complete ? 'Current Power' : 'Max Power'}</span>
                                <span class="stat-value">${!session.is_complete && currentData.chargingPower ? currentData.chargingPower.toFixed(1) : session.max_power.toFixed(1)} kW</span>
                            </div>
                        </div>
                    </div>
                `;
            });
            
            html += '</div>';
            container.innerHTML = html;
            
        } catch (error) {
            console.error('Error loading charging sessions:', error);
            const container = document.getElementById('charging-sessions-container');
            if (container) {
                container.innerHTML = '<div class="no-data">Error loading charging sessions</div>';
            }
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
                loadTripsWithPagination(currentTimeRange);
            }
        });
    }
    
    if (nextPageBtn) {
        nextPageBtn.addEventListener('click', () => {
            if (currentPage < totalPages) {
                currentPage++;
                loadTripsWithPagination(currentTimeRange);
            }
        });
    }
    
    if (perPageSelect) {
        perPageSelect.addEventListener('change', (e) => {
            perPage = parseInt(e.target.value);
            currentPage = 1;
            loadTripsWithPagination(currentTimeRange);
        });
    }
    
    async function loadTripsWithPagination(hours = 'all', startDate = null, endDate = null) {
        try {
            // Get filter values
            const minDistance = document.getElementById('min-distance').value;
            const maxDistance = document.getElementById('max-distance').value;
            
            const params = new URLSearchParams({
                page: currentPage,
                per_page: perPage,
                hours: hours
            });
            
            // Use master date range if custom
            if (hours === 'custom' && startDate && endDate) {
                params.append('start_date', startDate);
                params.append('end_date', endDate);
            }
            
            if (minDistance) params.append('min_distance', minDistance);
            if (maxDistance) params.append('max_distance', maxDistance);
            
            const response = await fetch(`/api/trips?${params}`);
            const data = await response.json();
            
            totalPages = data.total_pages || 1;
            currentPage = data.page || 1;
            
            // Check if we have no trips and should show empty state
            const trips = data.trips || [];
            if (trips.length === 0 && hours !== 'all' && hours !== 'custom') {
                const tableContainer = document.querySelector('.table-container');
                if (tableContainer) {
                    showEmptyStateWithSuggestion(tableContainer, hours, 'trips');
                    
                    // Hide pagination controls
                    if (pageInfo) pageInfo.style.display = 'none';
                    if (prevPageBtn) prevPageBtn.style.display = 'none';
                    if (nextPageBtn) nextPageBtn.style.display = 'none';
                    
                    return;
                }
            } else {
                // Show pagination controls if hidden
                if (pageInfo) pageInfo.style.display = 'block';
                if (prevPageBtn) prevPageBtn.style.display = 'inline-block';
                if (nextPageBtn) nextPageBtn.style.display = 'inline-block';
            }
            
            updateTripsTable(trips);
            
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
    loadTripsWithPagination(24);
    
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
            
            // Create stats grid with proper unit conversions
            const distance = currentUnits === 'metric' ? 
                trip.distance : conversions.kmToMiles(trip.distance);
            const avgSpeed = currentUnits === 'metric' ? 
                trip.average_speed : conversions.kmToMiles(trip.average_speed);
            const maxSpeed = currentUnits === 'metric' ? 
                trip.max_speed : conversions.kmToMiles(trip.max_speed);
                
            const statsHtml = `
                <div class="stat-card">
                    <h3>Distance</h3>
                    <p>${distance.toFixed(1)} ${currentUnits === 'metric' ? 'km' : 'mi'}</p>
                </div>
                <div class="stat-card">
                    <h3>Duration</h3>
                    <p>${trip.duration} min</p>
                </div>
                <div class="stat-card">
                    <h3>Avg Speed</h3>
                    <p>${avgSpeed.toFixed(1)} ${currentUnits === 'metric' ? 'km/h' : 'mph'}</p>
                </div>
                <div class="stat-card">
                    <h3>Max Speed</h3>
                    <p>${maxSpeed.toFixed(1)} ${currentUnits === 'metric' ? 'km/h' : 'mph'}</p>
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