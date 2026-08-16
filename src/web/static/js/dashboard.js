// Global map variables
let locationsMap = null;
let tripMap = null;
let mapMarkers = [];

// Chart instances
let batteryChart = null;
let energyChart = null;
let tempEfficiencyChart = null;
let chargingTempChart = null;

// Time spans painted behind the battery chart (charging periods and trips)
let batteryOverlays = { charging: [], trips: [] };

const OVERLAY_COLORS = {
    l1Fill: 'rgba(46, 204, 113, 0.15)',
    l1Legend: 'rgba(46, 204, 113, 0.45)',
    l2Fill: 'rgba(22, 160, 87, 0.3)',
    l2Legend: 'rgba(22, 160, 87, 0.65)',
    dcfcFill: 'rgba(155, 89, 182, 0.3)',
    dcfcLegend: 'rgba(155, 89, 182, 0.65)',
    tripFill: 'rgba(243, 156, 18, 0.2)',
    tripLegend: 'rgba(243, 156, 18, 0.55)'
};

// kW thresholds separating charge levels (L1 ~1.4, L2 ~3-11, DCFC 50+)
const CHARGE_LEVEL_KW = { dcfc: 20, l2: 2.5 };
// %-SOC-per-hour fallback when no power reading landed inside the span
const CHARGE_RATE_PCT_PER_HOUR = { dcfc: 30, l2: 5 };

// Chart.js plugin that shades charging and trip time spans behind the datasets
const batteryOverlayPlugin = {
    id: 'batteryOverlayBands',
    beforeDatasetsDraw(chart) {
        const { ctx, chartArea, scales } = chart;
        if (!chartArea || !scales.x) return;

        const drawSpans = (spans, fillStyle) => {
            ctx.save();
            ctx.fillStyle = fillStyle;
            spans.forEach(span => {
                let left = scales.x.getPixelForValue(span.start.valueOf());
                let right = scales.x.getPixelForValue(span.end.valueOf());
                if (right < chartArea.left || left > chartArea.right) return;
                left = Math.max(left, chartArea.left);
                right = Math.min(right, chartArea.right);
                // Keep very short spans visible
                if (right - left < 3) {
                    left -= 1.5;
                    right = left + 3;
                }
                ctx.fillRect(left, chartArea.top, right - left, chartArea.bottom - chartArea.top);
            });
            ctx.restore();
        };

        const fillsByType = {
            dcfc: OVERLAY_COLORS.dcfcFill,
            l2: OVERLAY_COLORS.l2Fill,
            l1: OVERLAY_COLORS.l1Fill
        };
        ['l1', 'l2', 'dcfc'].forEach(type => {
            drawSpans(
                batteryOverlays.charging.filter(span => (span.type || 'l1') === type),
                fillsByType[type]
            );
        });
        drawSpans(batteryOverlays.trips, OVERLAY_COLORS.tripFill);
    }
};

// Tag each charging span as l1/l2/dcfc using the peak charging power reported
// inside it, falling back to SOC gain rate when no power reading landed there
function classifyChargingSpans(spans, rows) {
    const readings = rows
        .map(row => ({
            timestamp: new Date(row.timestamp),
            power: Number(row.charging_power) || 0,
            level: row.battery_level
        }))
        .filter(reading => !Number.isNaN(reading.timestamp.valueOf()))
        .sort((a, b) => a.timestamp - b.timestamp);

    const TOLERANCE_MS = 60000;
    return spans.map(span => {
        // Stored session max_power (e.g. imported charger-network data) counts
        // alongside whatever the polled readings caught
        let peakPower = span.maxPower || 0;
        readings.forEach(reading => {
            if (
                reading.timestamp.valueOf() >= span.start.valueOf() - TOLERANCE_MS &&
                reading.timestamp.valueOf() <= span.end.valueOf() + TOLERANCE_MS
            ) {
                peakPower = Math.max(peakPower, reading.power);
            }
        });

        // No power reading inside the span: estimate from SOC gain rate
        let socRate = 0;
        if (peakPower === 0) {
            const before = readings.filter(r => r.timestamp <= span.start).pop();
            const after = readings.find(r => r.timestamp >= span.end);
            if (before && after && after.timestamp > before.timestamp) {
                const hours = (after.timestamp - before.timestamp) / 3600000;
                socRate = (after.level - before.level) / hours;
            }
        }
        return { ...span, type: classifyChargeLevel(peakPower, socRate) };
    });
}

// Shared L1/L2/DCFC decision used by both the chart bands and the sessions table
function classifyChargeLevel(peakPowerKw, socRatePerHour) {
    if (peakPowerKw >= CHARGE_LEVEL_KW.dcfc) return 'dcfc';
    if (peakPowerKw >= CHARGE_LEVEL_KW.l2) return 'l2';
    if (peakPowerKw > 0) return 'l1';
    if (socRatePerHour >= CHARGE_RATE_PCT_PER_HOUR.dcfc) return 'dcfc';
    if (socRatePerHour >= CHARGE_RATE_PCT_PER_HOUR.l2) return 'l2';
    return 'l1';
}

const CHARGE_TYPE_LABELS = { l1: 'L1', l2: 'L2', dcfc: 'DC Fast' };

// Shades a session's time span behind the charge detail modal's SOC chart
const chargeSpanShadePlugin = {
    id: 'chargeSpanShade',
    beforeDatasetsDraw(chart, args, opts) {
        const { ctx, chartArea, scales } = chart;
        if (!chartArea || !scales.x || !opts || !opts.start) return;
        let left = scales.x.getPixelForValue(opts.start);
        let right = scales.x.getPixelForValue(opts.end);
        left = Math.max(left, chartArea.left);
        right = Math.min(right, chartArea.right);
        if (right - left < 2) {
            left -= 1;
            right = left + 2;
        }
        ctx.save();
        ctx.fillStyle = opts.color || 'rgba(46, 204, 113, 0.15)';
        ctx.fillRect(left, chartArea.top, right - left, chartArea.bottom - chartArea.top);
        ctx.restore();
    }
};

// Estimate when charging reaches 80% and 100% at the current power
function buildChargingEta(batteryLevel, powerKw) {
    if (!Number.isFinite(batteryLevel) || !(powerKw > 0) || batteryLevel >= 100) {
        return '';
    }
    const usableKwh = (window.PYVISIONIC_CONFIG &&
        window.PYVISIONIC_CONFIG.batteryUsableKwh) || 74.0;
    const now = new Date();

    const formatHours = hours => {
        const totalMinutes = Math.round(hours * 60);
        if (totalMinutes < 60) return `${totalMinutes}m`;
        return `${Math.floor(totalMinutes / 60)}h ${totalMinutes % 60}m`;
    };

    return [80, 100]
        .filter(target => batteryLevel < target)
        .map(target => {
            const hours = ((target - batteryLevel) / 100) * usableKwh / powerKw;
            const eta = new Date(now.valueOf() + hours * 3600000);
            const timeText = eta.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
            let dayNote = '';
            if (eta.toDateString() !== now.toDateString()) {
                const tomorrow = new Date(now.valueOf() + 86400000);
                dayNote = eta.toDateString() === tomorrow.toDateString() ?
                    ' tomorrow' :
                    ` ${eta.toLocaleDateString([], { weekday: 'short' })}`;
            }
            return `${target}% in ≈ ${formatHours(hours)} (${timeText}${dayNote})`;
        })
        .join(' · ');
}

// Naive local ISO string (the backend stores naive local timestamps)
function toLocalIso(dateValue) {
    const pad = n => String(n).padStart(2, '0');
    return `${dateValue.getFullYear()}-${pad(dateValue.getMonth() + 1)}-${pad(dateValue.getDate())}` +
        `T${pad(dateValue.getHours())}:${pad(dateValue.getMinutes())}:${pad(dateValue.getSeconds())}`;
}

// Group consecutive is_charging readings into contiguous time spans
function computeChargingSpans(rows) {
    const spans = [];
    let spanStart = null;
    let spanEnd = null;
    rows.forEach(row => {
        const isCharging = row.is_charging === true || row.is_charging === 'True';
        const timestamp = new Date(row.timestamp);
        if (isCharging) {
            if (!spanStart) spanStart = timestamp;
            spanEnd = timestamp;
        } else if (spanStart) {
            spans.push({ start: spanStart, end: spanEnd });
            spanStart = null;
            spanEnd = null;
        }
    });
    if (spanStart) spans.push({ start: spanStart, end: spanEnd });
    return spans;
}

// Convert charging sessions (real start/end times) into time spans.
// Sessions catch fast charges that start and finish between polling samples,
// where no reading ever reports is_charging.
function computeSessionSpans(sessions) {
    return sessions
        .filter(session => session.start_time && session.end_time)
        .map(session => ({
            start: new Date(String(session.start_time).replace(' ', 'T')),
            end: new Date(String(session.end_time).replace(' ', 'T')),
            maxPower: Number(session.max_power) || 0
        }))
        .filter(span =>
            !Number.isNaN(span.start.valueOf()) &&
            !Number.isNaN(span.end.valueOf()) &&
            span.end > span.start
        );
}

// Merge overlapping spans so unioned sources don't double-paint
function mergeSpans(spans) {
    const sorted = [...spans].sort((a, b) => a.start - b.start);
    const merged = [];
    sorted.forEach(span => {
        const last = merged[merged.length - 1];
        if (last && span.start <= last.end) {
            if (span.end > last.end) last.end = span.end;
            last.maxPower = Math.max(last.maxPower || 0, span.maxPower || 0);
        } else {
            merged.push({ start: span.start, end: span.end, maxPower: span.maxPower || 0 });
        }
    });
    return merged;
}

// Convert trips (start date + duration in minutes) into time spans
function computeTripSpans(trips) {
    return trips
        .filter(trip => trip.date)
        .map(trip => {
            // Normalize "YYYY-MM-DD HH:mm:ss" to ISO so Safari can parse it
            const start = new Date(String(trip.date).replace(' ', 'T'));
            const durationMinutes = Math.max(Number(trip.duration) || 0, 1);
            return { start, end: new Date(start.valueOf() + durationMinutes * 60000) };
        })
        .filter(span => !Number.isNaN(span.start.valueOf()));
}

document.addEventListener('DOMContentLoaded', function() {
    const refreshBtn = document.getElementById('refresh-btn');
    const unitsToggle = document.getElementById('units-toggle');
    const unitsText = unitsToggle.querySelector('.units-text');
    const tempUnitsToggle = document.getElementById('temp-units-toggle');
    const tempUnitsText = tempUnitsToggle ? tempUnitsToggle.querySelector('.temp-units-text') : null;
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

    // Weather-tab charts receive mi/kWh and Celsius from the API regardless of
    // display preference, so they convert at render time.
    function displayEfficiency(miPerKwh) {
        if (miPerKwh === null || miPerKwh === undefined) { return miPerKwh; }
        return currentUnits === 'metric' ? 1000 / (miPerKwh * 1.60934) : miPerKwh;
    }

    function efficiencyUnitLabel() {
        return currentUnits === 'metric' ? 'Wh/km' : 'mi/kWh';
    }

    function displayDistance(miles) {
        if (miles === null || miles === undefined) { return miles; }
        return currentUnits === 'metric' ? miles * 1.60934 : miles;
    }

    function distanceUnitLabel() {
        return currentUnits === 'metric' ? 'km' : 'mi';
    }

    function displayTemp(celsius) {
        if (celsius === null || celsius === undefined) { return celsius; }
        return currentTempUnits === 'c' ? celsius : conversions.celsiusToFahrenheit(celsius);
    }

    // Temperature bin labels arrive from the API pre-formatted in Celsius,
    // e.g. "-25 to -20°C". They are category labels on the chart, so without
    // rewriting them the axis keeps reading Celsius no matter what the units
    // toggle says.
    // Bands backed by only a handful of trips are labelled as indicative.
    const THIN_BAND_TRIPS = 20;

    // Diverging about freezing, matching the by-month chart.
    function temperatureBandColor(celsius) {
        if (celsius === null || celsius === undefined) { return 'rgb(143,143,143)'; }
        const ratio = Math.max(-1, Math.min(1, celsius / 25));
        const cold = [33, 102, 172];
        const warm = [178, 24, 43];
        const mid = [143, 143, 143];
        const target = ratio < 0 ? cold : warm;
        const weight = Math.abs(ratio);
        const ch = i => Math.round(mid[i] + (target[i] - mid[i]) * weight);
        return `rgb(${ch(0)}, ${ch(1)}, ${ch(2)})`;
    }

    // One preference drives the range view on both efficiency charts, so they
    // never show different framings of the same data side by side.
    function chartViewIsRange() {
        return localStorage.getItem('pyvisionic.chartView') === 'range';
    }

    // Two-tone stacked bar: solid up to the range at an 80% charge, lighter
    // above it to 100%. One mark answers both questions, and the boundary is
    // the daily charge limit people actually plan around.
    function rangeDatasets(fullRanges, colors) {
        return [
            {
                label: 'Range at 80% charge',
                data: fullRanges.map(r => r * 0.8),
                backgroundColor: colors,
                borderWidth: 0,
                stack: 'range'
            },
            {
                label: '80% to full charge',
                data: fullRanges.map(r => r * 0.2),
                backgroundColor: colors.map(c => c.replace('rgb(', 'rgba(').replace(')', ', 0.35)')),
                borderWidth: 0,
                borderRadius: 4,
                stack: 'range'
            }
        ];
    }

    function relabelBand(range) {
        const bounds = String(range).match(/(-?\d+(?:\.\d+)?)\s*to\s*(-?\d+(?:\.\d+)?)/);
        if (!bounds) { return range; }
        return `${displayTemp(parseFloat(bounds[1])).toFixed(0)} to ` +
            `${displayTemp(parseFloat(bounds[2])).toFixed(0)}${tempUnitLabel()}`;
    }

    function tempUnitLabel() {
        return currentUnits === 'metric' ? '°C' : '°F';
    }
    
    let currentUnits = localStorage.getItem('units') || 'metric';
    // Temperature is independent of distance: Wh/km vs mi/kWh is distance-derived,
    // Celsius vs Fahrenheit is not, and plenty of drivers want miles with Celsius.
    // Existing users inherit whatever their single toggle implied.
    let currentTempUnits = localStorage.getItem('tempUnits') ||
        (currentUnits === 'metric' ? 'c' : 'f');
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
        if (tempUnitsText) { tempUnitsText.textContent = currentTempUnits === 'c' ? '\u00B0C' : '\u00B0F'; }
        if (tempUnitsToggle) { tempUnitsToggle.classList.toggle('imperial', currentTempUnits === 'f'); }
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

        // Weather-tab charts bake their axis titles in at creation, so they are
        // destroyed rather than updated; otherwise the labels keep the old unit.
        if (tempEfficiencyChart) { tempEfficiencyChart.destroy(); tempEfficiencyChart = null; }
        if (chargingTempChart) { chargingTempChart.destroy(); chargingTempChart = null; }
        loadTemperatureEfficiency(currentTimeRange, currentStartDate, currentEndDate);
        loadChargingTemperatureImpact(currentTimeRange, currentStartDate, currentEndDate);
        if (window.PYVISIONIC_WEATHER_REFRESH) { window.PYVISIONIC_WEATHER_REFRESH(); }
    });

    if (tempUnitsToggle) {
        tempUnitsToggle.addEventListener('click', function () {
            currentTempUnits = currentTempUnits === 'c' ? 'f' : 'c';
            localStorage.setItem('tempUnits', currentTempUnits);
            updateUnitsDisplay();

            loadCurrentStatus();
            if (currentData.batteryHistory) { updateBatteryChart(currentData.batteryHistory); }
            if (tempEfficiencyChart) { tempEfficiencyChart.destroy(); tempEfficiencyChart = null; }
            if (chargingTempChart) { chargingTempChart.destroy(); chargingTempChart = null; }
            loadTemperatureEfficiency(currentTimeRange, currentStartDate, currentEndDate);
            loadChargingTemperatureImpact(currentTimeRange, currentStartDate, currentEndDate);
            if (window.PYVISIONIC_WEATHER_REFRESH) { window.PYVISIONIC_WEATHER_REFRESH(); }
        });
    }

    // Efficiency / range view toggle for the two efficiency bar charts. One
    // stored preference drives both, so the tab never shows mixed framings.
    document.addEventListener('click', function (event) {
        const button = event.target.closest('.chart-view-btn');
        if (!button) { return; }
        localStorage.setItem('pyvisionic.chartView', button.dataset.view);
        document.querySelectorAll('.chart-view-btn').forEach(other => {
            const on = other.dataset.view === button.dataset.view;
            other.classList.toggle('active', on);
            other.setAttribute('aria-pressed', String(on));
        });
        if (tempEfficiencyChart) { tempEfficiencyChart.destroy(); tempEfficiencyChart = null; }
        loadTemperatureEfficiency(currentTimeRange, currentStartDate, currentEndDate);
        if (window.PYVISIONIC_WEATHER_REFRESH) { window.PYVISIONIC_WEATHER_REFRESH(); }
    });

    // Charge-type filter for the temperature/charging chart.
    document.addEventListener('click', function (event) {
        const button = event.target.closest('.chart-filter-btn');
        if (!button) { return; }
        window.PYVISIONIC_CHARGE_TYPE = button.dataset.chargeType;
        document.querySelectorAll('.chart-filter-btn').forEach(other => {
            const on = other === button;
            other.classList.toggle('active', on);
            other.setAttribute('aria-pressed', String(on));
        });
        if (chargingTempChart) { chargingTempChart.destroy(); chargingTempChart = null; }
        loadChargingTemperatureImpact(currentTimeRange, currentStartDate, currentEndDate);
    });
    
    async function loadCurrentStatus() {
        try {
            const response = await fetch('/api/current-status');
            const data = await response.json();
            
            const vehicle = data.vehicle || null;
            const setText = (id, text) => {
                const el = document.getElementById(id);
                if (el) { el.textContent = text; }
            };
            if (vehicle) {
                setText('doors-state', vehicle.doors_locked ? 'Locked' : 'Unlocked');
                setText('cable-state', vehicle.plugged_in ? 'Plugged in' : 'Unplugged');
                setText('twelve-v', vehicle.twelve_v !== null ? `${vehicle.twelve_v}%` : '--');
                const items = [...(vehicle.openings || []).map(o => `Open: ${o}`),
                               ...(vehicle.climate || []).map(c => `Climate: ${c}`),
                               ...(vehicle.warnings || [])];
                const box = document.getElementById('vehicle-alerts');
                const list = document.getElementById('vehicle-alerts-list');
                if (box && list) {
                    list.innerHTML = items.map(i => `<li>${i}</li>`).join('');
                    box.hidden = items.length === 0;
                }
            }

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
            
            // Handle charging indicator, rate, and time-to-target estimates
            const chargingIndicator = document.getElementById('charging-indicator');
            const chargingRate = document.getElementById('charging-rate');
            const chargingEta = document.getElementById('charging-eta');
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
                    if (chargingEta) {
                        const etaText = buildChargingEta(
                            Number(data.battery_level), Number(data.charging_power)
                        );
                        chargingEta.textContent = etaText;
                        chargingEta.style.display = etaText ? 'block' : 'none';
                    }
                } else {
                    chargingIndicator.style.display = 'none';
                    chargingRate.style.display = 'none';
                    if (chargingEta) chargingEta.style.display = 'none';
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
    if (window.PYVISIONIC_COLLECTION) { window.PYVISIONIC_COLLECTION.refresh(); }
    loadEfficiencyStats(24);
    loadChargingSessions(24);
    loadTemperatureEfficiency(24);
    loadChargingTemperatureImpact(24);
    
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
            loadTemperatureEfficiency(hours, startDate, endDate),
            loadChargingTemperatureImpact(hours, startDate, endDate)
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
                if (window.PYVISIONIC_COLLECTION) { window.PYVISIONIC_COLLECTION.refresh(); }
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

            // Fetch trips for the same range so they can be shaded on the chart
            const tripParams = new URLSearchParams(params);
            tripParams.append('page', '1');
            tripParams.append('per_page', '500');

            const [batteryRows, tripsData, sessionsData] = await Promise.all([
                fetch(`/api/battery/history?${params}`).then(r => r.json()),
                fetch(`/api/trips?${tripParams}`).then(r => r.json()).catch(() => null),
                fetch(`/api/charging-sessions?${params}`).then(r => r.json()).catch(() => null)
            ]);

            // The endpoint returns a bare array; filter custom date ranges client-side
            let rows = Array.isArray(batteryRows) ? batteryRows : (batteryRows.data || []);
            if (hours === 'custom' && startDate && endDate) {
                const rangeStart = new Date(`${startDate}T00:00:00`);
                const rangeEnd = new Date(`${endDate}T23:59:59.999`);
                rows = rows.filter(row => {
                    const ts = new Date(row.timestamp);
                    return ts >= rangeStart && ts <= rangeEnd;
                });
            }
            const data = { data: rows };
            currentData.batteryHistory = data;

            // Union point-derived spans with session records: sessions supply real
            // start/end times and catch fast charges shorter than one polling interval
            batteryOverlays.charging = classifyChargingSpans(
                mergeSpans(
                    computeChargingSpans(rows).concat(
                        computeSessionSpans(Array.isArray(sessionsData) ? sessionsData : [])
                    )
                ),
                rows
            );
            batteryOverlays.trips = computeTripSpans(
                tripsData && Array.isArray(tripsData.trips) ? tripsData.trips : []
            );

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
            is_cached: d.is_cached,
            is_charging: d.is_charging === true || d.is_charging === 'True',
            charging_power: d.charging_power
        }));
        
        // Update existing chart or create new one
        if (batteryChart) {
            // Update existing chart data
            batteryChart.$pointMeta = chartData;
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
            plugins: [batteryOverlayPlugin],
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
                                const overlayLegendEntries = [
                                    ['L1 charging', OVERLAY_COLORS.l1Legend],
                                    ['L2 charging', OVERLAY_COLORS.l2Legend],
                                    ['DC fast charge', OVERLAY_COLORS.dcfcLegend],
                                    ['Trip', OVERLAY_COLORS.tripLegend]
                                ];
                                overlayLegendEntries.forEach(([text, color]) => {
                                    original.push({
                                        text: text,
                                        fillStyle: color,
                                        strokeStyle: color,
                                        lineWidth: 0,
                                        hidden: false,
                                        index: original.length
                                    });
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
                                // Add cache and charging status for battery level dataset
                                if (context.datasetIndex === 0) {
                                    const points = context.chart.$pointMeta || chartData;
                                    const point = points[context.dataIndex];
                                    if (!point) return '';
                                    let status = point.is_cached ? '(Cached data)' : '(Fresh data)';
                                    if (point.is_charging) {
                                        const power = Number(point.charging_power);
                                        status += power > 0 ?
                                            ` ⚡ Charging (${power.toFixed(1)} kW)` : ' ⚡ Charging';
                                    }
                                    return status;
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
        batteryChart.$pointMeta = chartData;
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
            
            // Efficiency follows the units toggle: Wh/km in metric, mi/kWh in imperial.
            let efficiencyDisplay = '-';
            if (trip.efficiency_wh_per_km) {
                if (currentUnits === 'metric') {
                    efficiencyDisplay = `${Math.round(trip.efficiency_wh_per_km)} Wh/km`;
                } else {
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
    
    // Collection status and polling decisions live in collection-status.js,
    // shared with the Data & Collection page.

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
                            // trips.distance is recorded in miles, so this was labelling
                            // miles as kilometres regardless of preference.
                            popupContent += `<br>Distance: ${displayDistance(loc.distance).toFixed(1)} ${distanceUnitLabel()}`;
                            popupContent += `<br>Duration: ${loc.duration} min`;
                            if (loc.efficiency) {
                                // The API sends mi/kWh here; this previously
                                // printed that value labelled as Wh/km.
                                const eff = currentUnits === 'metric' ?
                                    `${Math.round(1000 / (loc.efficiency * 1.60934))} Wh/km` :
                                    `${loc.efficiency.toFixed(1)} mi/kWh`;
                                popupContent += `<br>Efficiency: ${eff}`;
                            }
                        }
                        if (loc.temperature !== null && loc.temperature !== undefined) {
                            popupContent += `<br>Temperature: ${displayTemp(loc.temperature).toFixed(1)}${tempUnitLabel()}`;
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
                    valueElement.textContent = `${displayEfficiency(data.average).toFixed(2)} ${efficiencyUnitLabel()}`;
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
            
            const ctx = document.getElementById('temperature-efficiency-chart').getContext('2d');

            // A bar per temperature band, not a scatter of every trip. 1,481
            // overlapping points hid the relationship they were meant to show,
            // and individual short trips are dominated by their own noise. The
            // band averages are energy-weighted, so a long run counts for more
            // than a two-mile hop.
            // Displayed on the app's convention: mi/kWh in imperial, Wh/km in
            // metric, same as the trips table and the efficiency cards. Note
            // these run in opposite directions, so the bars invert with the
            // toggle: taller is better in mi/kWh, worse in Wh/km.
            const bins = data.temperature_bins;
            const energyLabel = efficiencyUnitLabel();
            const barLabels = bins.map(bin => relabelBand(bin.temperature_range));
            const barData = bins.map(bin => displayEfficiency(bin.avg_efficiency));

            const packKwh = (window.PYVISIONIC_CONFIG &&
                window.PYVISIONIC_CONFIG.batteryUsableKwh) || 74.0;
            const rangeView = chartViewIsRange();
            const rampColors = bins.map(bin => temperatureBandColor(bin.avg_temperature));
            const fullRanges = bins.map(bin => displayDistance(packKwh * bin.avg_efficiency));

            if (tempEfficiencyChart) { tempEfficiencyChart.destroy(); }
            tempEfficiencyChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: barLabels,
                    datasets: rangeView ? rangeDatasets(fullRanges, rampColors) : [{
                        label: `Efficiency (${energyLabel})`,
                        data: barData,
                        // Same diverging ramp as the by-month chart, so the two
                        // read as one system. Redundant with the axis here,
                        // which is fine -- it reinforces rather than encodes.
                        backgroundColor: rampColors,
                        borderWidth: 0,
                        borderRadius: 4
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        title: {
                            display: true,
                            text: rangeView ? 'Estimated Range by Temperature'
                                : 'Efficiency by Temperature',
                            font: { size: 16 }
                        },
                        legend: { display: rangeView, position: 'top' },
                        tooltip: {
                            callbacks: {
                                label: function (context) {
                                    const bin = bins[context.dataIndex];
                                    const full = fullRanges[context.dataIndex];
                                    const lines = rangeView ? [
                                        `${(full * 0.8).toFixed(0)} ${distanceUnitLabel()} at an 80% charge`,
                                        `${full.toFixed(0)} ${distanceUnitLabel()} at 100%`,
                                        `${displayEfficiency(bin.avg_efficiency).toFixed(2)} ${energyLabel}`
                                    ] : [
                                        `${context.parsed.y.toFixed(2)} ${energyLabel} ` +
                                            `(${(currentUnits === 'metric' ? bin.wh_per_km : bin.wh_per_mile).toFixed(0)} ` +
                                            `${currentUnits === 'metric' ? 'Wh/km' : 'Wh/mi'})`,
                                        `About ${full.toFixed(0)} ${distanceUnitLabel()} on a full charge`,
                                        `${bin.trip_count} trips, ${displayDistance(bin.total_distance).toFixed(0)} ${distanceUnitLabel()}`
                                    ];
                                    if (bin.trip_count < THIN_BAND_TRIPS) {
                                        lines.push('Few trips — treat as indicative');
                                    }
                                    return lines;
                                }
                            }
                        }
                    },
                    scales: {
                        x: {
                            stacked: rangeView,
                            title: { display: true, text: `Temperature (${tempUnitLabel()})` },
                            grid: { display: false }
                        },
                        y: {
                            stacked: rangeView,
                            beginAtZero: true,
                            title: {
                                display: true,
                                text: rangeView
                                    ? `Estimated range (${distanceUnitLabel()})`
                                    : `Efficiency (${energyLabel})`
                            }
                        }
                    }
                }
            });

            // Display statistics
            let statsHtml = '<div class="temp-efficiency-stats">';
            statsHtml += '<h3>Temperature Impact Summary</h3>';
            statsHtml += '<div class="stats-grid">';
            
            // Rank only bands with enough trips to mean something. The 35-40C
            // band holds 7 trips and the coldest holds 9; either could otherwise
            // win on a handful of unusual drives.
            const ranked = bins.filter(bin => bin.trip_count >= THIN_BAND_TRIPS);
            const pool = ranked.length >= 2 ? ranked : bins;
            // Rank on Wh/mi always, where lower is unambiguously better, then
            // convert only for display. Ranking on the displayed value would
            // need its comparison flipped with the units toggle.
            const bestBin = pool.reduce((a, b) => (a.wh_per_mile < b.wh_per_mile ? a : b));
            const worstBin = pool.reduce((a, b) => (a.wh_per_mile > b.wh_per_mile ? a : b));
            const shown = bin => displayEfficiency(bin.avg_efficiency).toFixed(2);

            // Range on a full charge is what people actually plan around, and a
            // percentage lost is easier to act on than an energy multiplier:
            // 1.88x more energy per mile is the same thing as 47% less range,
            // but only one of those tells you whether you can get home.
            const rangeMiles = bin => packKwh * bin.avg_efficiency;
            const bestRange = displayDistance(rangeMiles(bestBin));
            const worstRange = displayDistance(rangeMiles(worstBin));
            const rangeLossPct = (1 - rangeMiles(worstBin) / rangeMiles(bestBin)) * 100;

            statsHtml += `
                <div class="stat-item">
                    <span class="stat-label">Most Efficient Range:</span>
                    <span class="stat-value">${relabelBand(bestBin.temperature_range)}</span>
                    <span class="stat-detail">${shown(bestBin)} ${energyLabel} · ${bestBin.trip_count} trips</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">Least Efficient Range:</span>
                    <span class="stat-value">${relabelBand(worstBin.temperature_range)}</span>
                    <span class="stat-detail">${shown(worstBin)} ${energyLabel} · ${worstBin.trip_count} trips</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">Range Lost in the Cold:</span>
                    <span class="stat-value">${rangeLossPct.toFixed(0)}%</span>
                    <span class="stat-detail">A full charge goes about ${bestRange.toFixed(0)} ${distanceUnitLabel()} at ${relabelBand(bestBin.temperature_range)}, but only ${worstRange.toFixed(0)} ${distanceUnitLabel()} at ${relabelBand(worstBin.temperature_range)}</span>
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

    // A band needs a few sessions before "best" or "worst" means anything; with
    // one or two, the label just names wherever a single session landed.
    const MIN_BAND_SESSIONS = 3;

    function summarizeChargingPoints(points, apiSummary) {
        if (!points || !points.length) { return null; }

        const mean = (list, pick) => list.reduce((sum, p) => sum + pick(p), 0) / list.length;
        const powers = points.map(p => p.avg_power);
        const temps = points.map(p => p.temperature);

        // Bands stay on a 5°C grid and are relabelled for display, so switching
        // units cannot silently change which sessions group together.
        const bands = new Map();
        points.forEach(point => {
            const low = Math.floor(point.temperature / 5) * 5;
            if (!bands.has(low)) { bands.set(low, []); }
            bands.get(low).push(point);
        });

        const banded = Array.from(bands.entries())
            .map(([low, group]) => ({
                low,
                range: `${displayTemp(low).toFixed(0)} to ${displayTemp(low + 5).toFixed(0)}` +
                    tempUnitLabel(),
                avg_power: mean(group, p => p.avg_power),
                avg_duration_minutes: mean(group, p => p.duration_minutes),
                session_count: group.length
            }))
            .sort((a, b) => a.low - b.low);

        // Ranking bands by average power is only meaningful within a single
        // charge type. Across a mixture the ranking tracks where fast charges
        // happened to fall, not temperature, so it is withheld entirely rather
        // than shown with a caveat nobody reads.
        const mixedTypes = new Set(points.map(p => p.charge_type)).size > 1;
        const ranked = mixedTypes
            ? []
            : banded.filter(b => b.session_count >= MIN_BAND_SESSIONS);
        const byPower = [...ranked].sort((a, b) => b.avg_power - a.avg_power);

        return {
            bands: banded,
            total_sessions: points.length,
            temperature_range: { min: Math.min(...temps), max: Math.max(...temps) },
            avg_power_range: { min: Math.min(...powers), max: Math.max(...powers) },
            average_power: mean(points, p => p.avg_power),
            average_duration_minutes: mean(points, p => p.duration_minutes),
            total_energy_kwh: points.reduce((sum, p) => sum + (p.energy_added || 0), 0),
            mixed_types: mixedTypes,
            best_temperature_band: byPower.length ? byPower[0] : null,
            worst_temperature_band: byPower.length > 1 ? byPower[byPower.length - 1] : null,
            api_total_sessions: apiSummary ? apiSummary.total_sessions : null
        };
    }

    async function loadChargingTemperatureImpact(hours = 'all', startDate = null, endDate = null) {
        try {
            const params = new URLSearchParams({ hours });
            if (startDate) params.append('start_date', startDate);
            if (endDate) params.append('end_date', endDate);

            const response = await fetch(`/api/charging-temperature-impact?${params}`);
            const data = await response.json();

            const container = document.getElementById('charging-temperature-stats');
            if (!container) {
                return;
            }

            if (!response.ok || !data.raw_data || data.raw_data.length === 0) {
                if (hours !== 'all' && hours !== 'custom') {
                    showEmptyStateWithSuggestion(container, hours, 'charging data');
                } else {
                    container.innerHTML = '<div class="no-data">Not enough charging data to analyze temperature impact</div>';
                }
                if (chargingTempChart) {
                    chargingTempChart.destroy();
                    chargingTempChart = null;
                }
                return;
            }

            const ctx = document.getElementById('charging-temperature-chart').getContext('2d');

            // L1 sessions sit near 1.3 kW while DC fast charging reaches 160 kW.
            // On a shared linear axis the 97% that are AC collapse onto the
            // baseline, so one population is shown at a time.
            const activeType = window.PYVISIONIC_CHARGE_TYPE || 'dcfc';
            const selected = activeType === 'all' ? data.raw_data :
                data.raw_data.filter(point => point.charge_type === activeType);

            const summary = summarizeChargingPoints(selected, data.summary);
            if (!summary) {
                if (chargingTempChart) { chargingTempChart.destroy(); chargingTempChart = null; }
                const statsHost = document.getElementById('charging-temperature-stats');
                if (statsHost) {
                    statsHost.innerHTML =
                        '<div class="no-data">No charging sessions of this type in range.</div>';
                }
                return;
            }

            const scatterData = selected.map(point => ({
                x: displayTemp(point.temperature),
                y: point.avg_power
            }));

            // Bands are recomputed from the filtered set, so they describe one
            // charge type rather than a mixture of all of them.
            const barLabels = summary.bands.map(band => band.range);
            const barData = summary.bands.map(band => band.avg_power);

            if (chargingTempChart) {
                chargingTempChart.data.datasets[0].data = scatterData;
                chargingTempChart.data.datasets[1].data = barData;
                chargingTempChart.options.plugins.title.text = 'Charging Power vs Temperature';
                chargingTempChart.data.labels = barLabels;
                chargingTempChart.update('none');
            } else {
                chargingTempChart = new Chart(ctx, {
                    type: 'scatter',
                    data: {
                        labels: barLabels,
                        datasets: [{
                            label: 'Session Avg Power',
                            data: scatterData,
                            backgroundColor: 'rgba(46, 204, 113, 0.5)',
                            borderColor: 'rgba(39, 174, 96, 1)',
                            pointRadius: 5,
                            type: 'scatter'
                        }, {
                            label: 'Average by Temperature Range',
                            data: barData,
                            backgroundColor: 'rgba(155, 89, 182, 0.6)',
                            borderColor: 'rgba(142, 68, 173, 1)',
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
                                text: 'Charging Power vs Temperature',
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
                                            return `${context.parsed.x.toFixed(1)}${tempUnitLabel()}: ${context.parsed.y.toFixed(2)} kW`;
                                        } else {
                                            const bin = data.temperature_bins[context.dataIndex];
                                            return [
                                                `Average Power: ${context.parsed.y.toFixed(2)} kW`,
                                                `Avg Duration: ${bin.avg_duration_minutes.toFixed(1)} min`,
                                                `Sessions: ${bin.session_count}`,
                                                `Energy Added: ${bin.total_energy.toFixed(2)} kWh`
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
                                    text: `Temperature (${tempUnitLabel()})`
                                }
                            },
                            x2: {
                                type: 'category',
                                position: 'bottom',
                                display: false
                            },
                            y: {
                                type: 'linear',
                                title: {
                                    display: true,
                                    text: 'Average Charging Power (kW)'
                                },
                                min: 0
                            }
                        }
                    }
                });
            }

            // Summary is computed from whatever the filter is showing, not taken
            // from the API. The API figures cover every charge type at once, and
            // because DC fast charging is ~100x the power of Level 1, a single
            // fast charge landing in a 5°C band lifted that band's average from
            // 0.84 kW to 5.73 kW. That made -20 to -15°C read as the best
            // charging temperature, which is backwards: Level 1 power is
            // charger-limited and flat at 0.77-0.94 kW across the whole range,
            // so band ranking was tracking where fast charges happened to occur.
            const best = summary.best_temperature_band;
            const worst = summary.worst_temperature_band;
            const statsHtml = `
                <div class="stats-grid">
                    <div class="stat-item">
                        <span class="stat-label">Sessions Analyzed</span>
                        <span class="stat-value">${summary.total_sessions}${summary.api_total_sessions && summary.total_sessions !== summary.api_total_sessions ? ` of ${summary.api_total_sessions}` : ''}</span>
                    </div>
                    <div class="stat-item">
                        <span class="stat-label">Temperature Range</span>
                        <span class="stat-value">${displayTemp(summary.temperature_range.min).toFixed(1)}${tempUnitLabel()} → ${displayTemp(summary.temperature_range.max).toFixed(1)}${tempUnitLabel()}</span>
                    </div>
                    <div class="stat-item">
                        <span class="stat-label">Average Power Range</span>
                        <span class="stat-value">${summary.avg_power_range.min.toFixed(2)} - ${summary.avg_power_range.max.toFixed(2)} kW</span>
                    </div>
                    <div class="stat-item">
                        <span class="stat-label">Mean Power</span>
                        <span class="stat-value">${summary.average_power.toFixed(2)} kW</span>
                    </div>
                    <div class="stat-item">
                        <span class="stat-label">Average Session Length</span>
                        <span class="stat-value">${summary.average_duration_minutes.toFixed(0)} min</span>
                    </div>
                    <div class="stat-item">
                        <span class="stat-label">Total Energy Added</span>
                        <span class="stat-value">${summary.total_energy_kwh.toFixed(1)} kWh</span>
                    </div>
                    ${best ? `
                    <div class="stat-item">
                        <span class="stat-label">Best Temperature Band</span>
                        <span class="stat-value">${best.range} (${best.avg_power.toFixed(2)} kW)</span>
                        <span class="stat-detail">${best.session_count} sessions · ${best.avg_duration_minutes.toFixed(1)} min avg</span>
                    </div>` : ''}
                    ${worst ? `
                    <div class="stat-item">
                        <span class="stat-label">Challenging Band</span>
                        <span class="stat-value">${worst.range} (${worst.avg_power.toFixed(2)} kW)</span>
                        <span class="stat-detail">${worst.session_count} sessions · ${worst.avg_duration_minutes.toFixed(1)} min avg</span>
                    </div>` : ''}
                    ${summary.mixed_types ? `
                    <div class="stat-item stat-item-wide">
                        <span class="stat-label">Best / Challenging Band</span>
                        <span class="stat-detail">Not shown while charge types are combined: ranking
                        bands by average power would track where DC fast charges happened to occur,
                        not temperature. Pick a single charge type to compare bands.</span>
                    </div>` : ''}
                </div>
            `;
            container.innerHTML = statsHtml;

        } catch (error) {
            console.error('Error loading charging temperature impact:', error);
            const container = document.getElementById('charging-temperature-stats');
            if (container) {
                container.innerHTML = '<div class="no-data">Error loading charging temperature data</div>';
            }
        }
    }
    
    let chargeDetailsById = {};
    let chargeModalChart = null;
    let chargeModalMap = null;

    function closeChargeModal() {
        const chargeModal = document.getElementById('chargeModal');
        if (chargeModal) chargeModal.style.display = 'none';
        if (chargeModalMap) {
            chargeModalMap.remove();
            chargeModalMap = null;
        }
    }

    async function showChargeModal(detail) {
        const { session } = detail;
        const chargeModal = document.getElementById('chargeModal');
        if (!chargeModal) return;

        document.getElementById('charge-modal-title').textContent =
            `Charging Session - ${formatDate(detail.start)}`;

        const usableKwh = (window.PYVISIONIC_CONFIG &&
            window.PYVISIONIC_CONFIG.batteryUsableKwh) || 74.0;
        const durationHours = detail.duration > 0 ? detail.duration / 60 : null;
        const storedAvgPower = Number(session.avg_power);
        const avgPower = storedAvgPower > 0 ? storedAvgPower : null;
        const chargeRate = (detail.socGain != null && durationHours) ?
            detail.socGain / durationHours : null;
        const socSpan = (detail.startLevel != null && detail.endLevel != null) ?
            `${detail.startLevel}% → ${detail.endLevel}%` : '--';

        document.getElementById('charge-stats').innerHTML = `
            <div class="stat-card">
                <h3>Type</h3>
                <p><span class="charge-type charge-type-${detail.type}">${CHARGE_TYPE_LABELS[detail.type]}</span></p>
            </div>
            <div class="stat-card">
                <h3>Battery</h3>
                <p>${socSpan}</p>
            </div>
            <div class="stat-card">
                <h3>Gained</h3>
                <p>${detail.socGain != null ? (detail.socGain >= 0 ? '+' : '') + detail.socGain + '%' : '--'}</p>
            </div>
            <div class="stat-card">
                <h3>Energy Added</h3>
                <p>${detail.energyDisplay}</p>
            </div>
            <div class="stat-card">
                <h3>Duration</h3>
                <p>${detail.duration >= 60 ?
                    Math.floor(detail.duration / 60) + 'h ' + Math.round(detail.duration % 60) + 'm' :
                    Math.round(detail.duration) + 'm'}${!session.is_complete ? ' (ongoing)' : ''}</p>
            </div>
            <div class="stat-card">
                <h3>Peak Power</h3>
                <p>${detail.peakPower > 0 ? detail.peakPower.toFixed(1) + ' kW' : '--'}</p>
            </div>
            <div class="stat-card">
                <h3>Avg Power</h3>
                <p>${avgPower ? avgPower.toFixed(1) + ' kW' : '--'}</p>
            </div>
            <div class="stat-card">
                <h3>Charge Rate</h3>
                <p>${chargeRate != null ? chargeRate.toFixed(1) + ' %/hr' : '--'}</p>
            </div>
            <div class="stat-card">
                <h3>Full Pack Time</h3>
                <p>${(avgPower && avgPower > 0) ?
                    (usableKwh / avgPower).toFixed(1) + ' h at this rate' : '--'}</p>
            </div>
            <div class="stat-card">
                <h3>Network</h3>
                <p>${session.network || (detail.type === 'l1' ? 'Home' : '—')}</p>
            </div>
            <div class="stat-card">
                <h3>Source</h3>
                <p>${/^(ea|rc)_/.test(String(session.session_id || '')) ? 'Charger network log' :
                    (String(session.session_id || '').startsWith('ha_') ? 'Home plug meter' : 'Vehicle polling')}</p>
            </div>
            ${session.location_name ? `
            <div class="stat-card">
                <h3>Location</h3>
                <p>${session.location_name}</p>
            </div>` : ''}
            ${Number(session.cost_usd) > 0 ? `
            <div class="stat-card">
                <h3>Cost</h3>
                <p>$${Number(session.cost_usd).toFixed(2)}</p>
            </div>` : ''}
        `;

        chargeModal.style.display = 'block';

        // SOC chart from readings around the session
        const padMs = 45 * 60000;
        const windowStart = new Date(detail.start.valueOf() - padMs);
        const windowEnd = new Date(Math.min(detail.end.valueOf() + padMs, Date.now()));
        const chartContainer = document.getElementById('charge-chart-container');
        const noReadings = document.getElementById('charge-no-readings');

        let readings = [];
        try {
            const rows = await fetch(
                `/api/battery/history?start=${encodeURIComponent(toLocalIso(windowStart))}` +
                `&end=${encodeURIComponent(toLocalIso(windowEnd))}`
            ).then(r => r.json());
            readings = (Array.isArray(rows) ? rows : [])
                .map(row => ({
                    x: new Date(row.timestamp).valueOf(),
                    y: row.battery_level,
                    power: Number(row.charging_power) || 0
                }))
                .filter(point => Number.isFinite(point.y));
        } catch (error) {
            console.error('Error loading session readings:', error);
        }

        if (chargeModalChart) {
            chargeModalChart.destroy();
            chargeModalChart = null;
        }

        if (readings.length >= 2) {
            chartContainer.style.display = 'block';
            noReadings.style.display = 'none';
            const levels = readings.map(point => point.y);
            const fillsByType = {
                dcfc: OVERLAY_COLORS.dcfcFill,
                l2: OVERLAY_COLORS.l2Fill,
                l1: OVERLAY_COLORS.l1Fill
            };
            const ctx = document.getElementById('charge-soc-chart').getContext('2d');
            chargeModalChart = new Chart(ctx, {
                type: 'line',
                plugins: [chargeSpanShadePlugin],
                data: {
                    datasets: [{
                        label: 'Battery (%)',
                        data: readings,
                        borderColor: '#3498db',
                        backgroundColor: 'rgba(52, 152, 219, 0.1)',
                        tension: 0.2,
                        pointRadius: 4,
                        pointHoverRadius: 6
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                        title: {
                            display: true,
                            text: 'State of charge around this session (shaded = charging)'
                        },
                        chargeSpanShade: {
                            start: detail.start.valueOf(),
                            end: detail.end.valueOf(),
                            color: fillsByType[detail.type]
                        },
                        tooltip: {
                            callbacks: {
                                label: context => {
                                    const point = readings[context.dataIndex];
                                    let label = `${context.parsed.y}%`;
                                    if (point && point.power > 0) {
                                        label += ` - charging at ${point.power.toFixed(1)} kW`;
                                    }
                                    return label;
                                }
                            }
                        }
                    },
                    scales: {
                        x: {
                            type: 'time',
                            time: { tooltipFormat: 'MMM dd, HH:mm' }
                        },
                        y: {
                            suggestedMin: Math.max(0, Math.min(...levels) - 5),
                            suggestedMax: Math.min(100, Math.max(...levels) + 5),
                            title: { display: true, text: 'Battery (%)' }
                        }
                    }
                }
            });
        } else {
            chartContainer.style.display = 'none';
            noReadings.style.display = 'block';
        }

        // Location mini-map when the session has coordinates
        const mapContainer = document.getElementById('charge-map');
        if (chargeModalMap) {
            chargeModalMap.remove();
            chargeModalMap = null;
        }
        const lat = Number(session.location_lat);
        const lon = Number(session.location_lon);
        if (Number.isFinite(lat) && Number.isFinite(lon) && lat !== 0) {
            mapContainer.style.display = 'block';
            setTimeout(() => {
                chargeModalMap = L.map('charge-map').setView([lat, lon], 14);
                L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                    attribution: '© OpenStreetMap contributors'
                }).addTo(chargeModalMap);
                L.marker([lat, lon]).addTo(chargeModalMap)
                    .bindPopup(session.location_name ||
                        `Charged here (${CHARGE_TYPE_LABELS[detail.type]})`);
            }, 100);
        } else {
            mapContainer.style.display = 'none';
        }
    }

    async function loadChargingSessions(hours = 'all', startDate = null, endDate = null) {
        try {
            const params = new URLSearchParams({ hours });
            if (startDate) params.append('start_date', startDate);
            if (endDate) params.append('end_date', endDate);

            // Battery readings supply trustworthy SOC and power figures; the
            // session records' stored stats are unreliable for older data
            const [sessions, batteryRows] = await Promise.all([
                fetch(`/api/charging-sessions?${params}`).then(r => r.json()),
                fetch(`/api/battery/history?${params}`).then(r => r.json()).catch(() => [])
            ]);

            const container = document.getElementById('charging-sessions-container');
            if (!container) return;

            if (!Array.isArray(sessions) || sessions.length === 0) {
                if (hours !== 'all' && hours !== 'custom') {
                    showEmptyStateWithSuggestion(container, hours, 'charging sessions');
                } else {
                    container.innerHTML = '<div class="no-data">No charging sessions recorded yet</div>';
                }
                return;
            }

            const readings = (Array.isArray(batteryRows) ? batteryRows : [])
                .map(row => ({
                    timestamp: new Date(row.timestamp),
                    level: row.battery_level,
                    power: Number(row.charging_power) || 0
                }))
                .filter(r => !Number.isNaN(r.timestamp.valueOf()))
                .sort((a, b) => a.timestamp - b.timestamp);

            const formatDuration = (minutes) => {
                if (!minutes && minutes !== 0) return '--';
                const wholeHours = Math.floor(minutes / 60);
                const mins = Math.round(minutes % 60);
                return wholeHours > 0 ? `${wholeHours}h ${mins}m` : `${mins}m`;
            };

            const sessionDetails = sessions.map(session => {
                const start = new Date(String(session.start_time).replace(' ', 'T'));
                const end = session.end_time ?
                    new Date(String(session.end_time).replace(' ', 'T')) : new Date();

                let duration = session.duration_minutes;
                if (!session.is_complete) {
                    duration = Math.floor((Date.now() - start) / 60000);
                }

                // Derive SOC and peak power from the battery readings around the
                // session, with a tolerance so precision differences between the
                // session boundaries and reading timestamps can't exclude the
                // boundary readings themselves
                const TOLERANCE_MS = 60000;
                const spanStart = start.valueOf() - TOLERANCE_MS;
                const spanEnd = end.valueOf() + TOLERANCE_MS;

                const before = readings.filter(r => r.timestamp.valueOf() <= spanStart + 2 * TOLERANCE_MS).pop();
                // Prefer the last reading inside the session over the first one
                // after it - the latter can be post-drive and lower, producing
                // phantom negative gains
                const inSpan = readings.filter(r =>
                    r.timestamp.valueOf() >= spanStart && r.timestamp.valueOf() <= spanEnd
                );
                let after;
                if (!session.is_complete) {
                    after = readings[readings.length - 1];
                } else if (inSpan.length > 0) {
                    after = inSpan[inSpan.length - 1];
                } else {
                    after = readings.find(r => r.timestamp.valueOf() >= spanEnd);
                }

                // Imported charger-network sessions (ea_*) carry authoritative
                // metered values; prefer them over reading-derived estimates
                const sessionId = String(session.session_id || '');
                const isImported = /^(ea|ha|rc)_/.test(sessionId);

                let peakPower = Math.max(0, Number(session.max_power) || 0);
                inSpan.forEach(r => {
                    peakPower = Math.max(peakPower, r.power);
                });

                // A reading counts as the boundary level only if it is fresh
                // relative to the boundary; a stale pre-span reading can be
                // from before a drive and wildly wrong
                const STALE_MS = 90 * 60000;
                let startLevel = null;
                if (isImported && Number.isFinite(session.start_battery)) {
                    startLevel = session.start_battery;
                } else if (
                    before &&
                    start.valueOf() - before.timestamp.valueOf() <= STALE_MS &&
                    // SOC cannot fall while charging: a "before" reading above
                    // the first in-span reading is a stale mid-drive value
                    (inSpan.length === 0 || before.level <= inSpan[0].level)
                ) {
                    startLevel = before.level;
                } else if (inSpan.length > 0) {
                    startLevel = inSpan[0].level;
                } else if (Number.isFinite(session.start_battery)) {
                    startLevel = session.start_battery;
                }
                let endLevel = null;
                if (isImported && Number.isFinite(session.end_battery)) {
                    endLevel = session.end_battery;
                } else if (after && !session.is_complete) {
                    endLevel = after.level;
                } else {
                    // Closest reading to the session end wins (within freshness):
                    // the last in-span reading can be hours old if collections
                    // failed late in the session, while a reading shortly after
                    // the end reflects the final level accurately
                    const lastInSpan = inSpan.length > 0 ? inSpan[inSpan.length - 1] : null;
                    const firstAfter = readings.find(r => r.timestamp.valueOf() >= end.valueOf());
                    const candidates = [lastInSpan, firstAfter].filter(r =>
                        r && Math.abs(r.timestamp.valueOf() - end.valueOf()) <= STALE_MS
                    );
                    if (candidates.length > 0) {
                        candidates.sort((a, b) =>
                            Math.abs(a.timestamp.valueOf() - end.valueOf()) -
                            Math.abs(b.timestamp.valueOf() - end.valueOf())
                        );
                        endLevel = candidates[0].level;
                    } else if (lastInSpan) {
                        endLevel = lastInSpan.level;
                    } else if (Number.isFinite(session.end_battery)) {
                        endLevel = session.end_battery;
                    }
                }
                let socGain = (Number.isFinite(startLevel) && Number.isFinite(endLevel)) ?
                    Math.round(endLevel - startLevel) : null;
                // A session with real charging power can't lose charge; small
                // negatives are SOC quantization jitter
                if (socGain != null && socGain < 0 && peakPower > 0) {
                    socGain = 0;
                }

                // Energy: metered value for imported sessions (no approximation
                // marker), SOC delta x usable pack capacity otherwise
                const usableKwh = (window.PYVISIONIC_CONFIG &&
                    window.PYVISIONIC_CONFIG.batteryUsableKwh) || 74.0;
                let energyDisplay = '--';
                if (isImported && Number(session.energy_added) > 0) {
                    energyDisplay = `${Number(session.energy_added).toFixed(1)} kWh`;
                } else if (socGain != null && socGain > 0) {
                    energyDisplay = `≈ ${((socGain / 100) * usableKwh).toFixed(1)} kWh`;
                }

                let socRate = 0;
                if (peakPower === 0 && before && after && after.timestamp > before.timestamp) {
                    const spanHours = (after.timestamp - before.timestamp) / 3600000;
                    socRate = (after.level - before.level) / spanHours;
                }
                const type = classifyChargeLevel(peakPower, socRate);

                return {
                    session, start, end, duration, type, isImported,
                    startLevel, endLevel, socGain, energyDisplay, peakPower
                };
            });

            chargeDetailsById = {};
            sessionDetails.forEach(detail => {
                chargeDetailsById[String(detail.session.session_id)] = detail;
            });

            const formatEndTime = (detail) => {
                if (!detail.session.is_complete) return '—';
                const sameDay = detail.start.toDateString() === detail.end.toDateString();
                return sameDay ?
                    detail.end.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) :
                    formatDate(detail.end);
            };

            // Wall-to-battery efficiency exists only where the smart plug metered
            // the session and the SOC gain was big enough to measure, so most
            // rows legitimately show "--".
            const efficiencyPoints = window.PYVISIONIC_EFFICIENCY ?
                await window.PYVISIONIC_EFFICIENCY.load() : [];

            const rowsHtml = sessionDetails.map(detail => {
                const { session, type, startLevel, endLevel, socGain, energyDisplay, peakPower } = detail;
                const measured = window.PYVISIONIC_EFFICIENCY ?
                    window.PYVISIONIC_EFFICIENCY.match(efficiencyPoints, detail.start, 120) : null;
                const efficiencyCell = measured ?
                    `<span title="${measured.ac_kwh} kWh drawn, ${measured.pack_kwh} kWh delivered">` +
                        `${measured.efficiency_pct.toFixed(0)}%` +
                        `<span class="efficiency-tolerance"> ±${measured.uncertainty_pct.toFixed(0)}</span>` +
                    `</span>` : '--';
                return `
                    <tr class="charge-row" data-session="${session.session_id}">
                        <td>${formatDate(detail.start)}</td>
                        <td>${formatEndTime(detail)}</td>
                        <td>${formatDuration(detail.duration)}${!session.is_complete ? ' (ongoing)' : ''}</td>
                        <td><span class="charge-type charge-type-${type}">${CHARGE_TYPE_LABELS[type]}</span></td>
                        <td>${startLevel != null ? startLevel + '%' : '--'} → ${endLevel != null ? endLevel + '%' : '--'}</td>
                        <td>${socGain != null ? (socGain >= 0 ? '+' : '') + socGain + '%' : '--'}</td>
                        <td>${energyDisplay}</td>
                        <td>${efficiencyCell}</td>
                        <td>${peakPower > 0 ? peakPower.toFixed(1) + ' kW' : '--'}</td>
                        <td>${session.is_complete ? 'Complete' : '⚡ Charging'}</td>
                    </tr>
                `;
            }).join('');

            container.innerHTML = `
                <h3 id="charging-subheading">Click on rows below to see details</h3>
                <div class="table-container">
                    <table id="charging-table" aria-label="Charging session details">
                        <thead>
                            <tr>
                                <th scope="col">Started</th>
                                <th scope="col">Ended</th>
                                <th scope="col">Duration</th>
                                <th scope="col">Type</th>
                                <th scope="col">Battery</th>
                                <th scope="col">Gained</th>
                                <th scope="col">Energy Added</th>
                                <th scope="col" title="Share of metered wall energy that reached the battery">Efficiency</th>
                                <th scope="col">Peak Power</th>
                                <th scope="col">Status</th>
                            </tr>
                        </thead>
                        <tbody>${rowsHtml}</tbody>
                    </table>
                </div>
            `;

            container.querySelectorAll('.charge-row').forEach(row => {
                row.addEventListener('click', () => {
                    const detail = chargeDetailsById[row.dataset.session];
                    if (detail) showChargeModal(detail);
                });
            });

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
        if (event.target === document.getElementById('chargeModal')) {
            closeChargeModal();
        }
    });

    const chargeModalCloseBtn = document.getElementById('charge-modal-close');
    if (chargeModalCloseBtn) {
        chargeModalCloseBtn.addEventListener('click', closeChargeModal);
    }
    
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
        if (window.PYVISIONIC_COLLECTION) { window.PYVISIONIC_COLLECTION.refresh(); }
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

/* ===========================================================================
   Tabbed navigation + charging efficiency chart
   =========================================================================== */

(function () {
    'use strict';

    const TAB_STORAGE_KEY = 'pyvisionic.activeTab';

    // The units helpers in the main dashboard scope live inside its
    // DOMContentLoaded callback and are not visible here, so this block reads
    // the same persisted preference directly.
    function unitsAreMetric() {
        return (localStorage.getItem('units') || 'metric') === 'metric';
    }

    // Temperature has its own preference, independent of distance.
    function tempIsCelsius() {
        const stored = localStorage.getItem('tempUnits');
        if (stored) { return stored === 'c'; }
        return unitsAreMetric();
    }

    function localTempLabel() {
        return tempIsCelsius() ? '°C' : '°F';
    }

    function localTemp(celsius) {
        if (celsius === null || celsius === undefined) { return celsius; }
        return tempIsCelsius() ? celsius : celsius * 9 / 5 + 32;
    }
    let chargeEfficiencyChart = null;
    let efficiencyLoaded = false;

    function activeHours() {
        const active = document.querySelector('.master-time-range-controls .time-range-btn.active');
        return active ? active.getAttribute('data-hours') : '24';
    }

    /* --- tabs ------------------------------------------------------------ */

    function tabs() {
        return Array.from(document.querySelectorAll('[role="tab"]'));
    }

    function selectTab(tab, setFocus) {
        tabs().forEach(other => {
            const selected = other === tab;
            other.setAttribute('aria-selected', String(selected));
            other.setAttribute('tabindex', selected ? '0' : '-1');
            other.classList.toggle('active', selected);
            const panel = document.getElementById(other.getAttribute('aria-controls'));
            if (panel) { panel.hidden = !selected; }
        });
        if (setFocus) { tab.focus(); }

        try { localStorage.setItem(TAB_STORAGE_KEY, tab.id); } catch (e) { /* private mode */ }

        // Chart.js sizes a canvas from its container; one that was hidden at
        // creation time measures zero and renders squashed. Nudge every chart
        // in the newly visible panel to remeasure.
        if (window.Chart) {
            Object.values(Chart.instances || {}).forEach(instance => {
                if (instance && typeof instance.resize === 'function') { instance.resize(); }
            });
        }
        if (tab.id === 'tab-charging' && !healthLoaded) { loadBatteryHealth(); }
        if (tab.id === 'tab-weather') {
            if (!efficiencyLoaded) { loadChargingEfficiency(); }
            if (!monthlyLoaded) { loadMonthlyEfficiency(); }
        }
    }

    function onTabKeydown(event) {
        const all = tabs();
        const index = all.indexOf(event.currentTarget);
        let next = null;
        if (event.key === 'ArrowRight') { next = all[(index + 1) % all.length]; }
        else if (event.key === 'ArrowLeft') { next = all[(index - 1 + all.length) % all.length]; }
        else if (event.key === 'Home') { next = all[0]; }
        else if (event.key === 'End') { next = all[all.length - 1]; }
        if (next) { event.preventDefault(); selectTab(next, true); }
    }

    function initTabs() {
        const all = tabs();
        if (!all.length) { return; }
        all.forEach(tab => {
            tab.addEventListener('click', () => selectTab(tab, false));
            tab.addEventListener('keydown', onTabKeydown);
        });
        let restore = null;
        try { restore = localStorage.getItem(TAB_STORAGE_KEY); } catch (e) { /* ignore */ }
        const initial = (restore && document.getElementById(restore)) || all[0];
        selectTab(initial, false);
    }

    /* --- charging efficiency --------------------------------------------- */

    function renderEfficiencyTable(points) {
        const host = document.getElementById('charge-efficiency-table');
        if (!host) { return; }
        if (!points.length) { host.innerHTML = '<p class="no-data">No sessions yet.</p>'; return; }
        const rows = points.map(p => `<tr>
            <td>${new Date(p.start_time).toLocaleString()}</td>
            <td>${p.ac_kwh.toFixed(2)}</td>
            <td>${p.pack_kwh.toFixed(2)}</td>
            <td>${p.efficiency_pct.toFixed(1)}%</td>
            <td>${p.temperature === null ? '--' : localTemp(p.temperature).toFixed(1)}</td>
        </tr>`).join('');
        host.innerHTML = `<table><caption class="sr-only">Charging efficiency by session</caption>
            <thead><tr><th scope="col">Session start</th><th scope="col">Wall kWh</th>
            <th scope="col">Battery kWh</th><th scope="col">Efficiency</th>
            <th scope="col">Temp ${localTempLabel()}</th></tr></thead><tbody>${rows}</tbody></table>`;
    }

    function loadChargingEfficiency() {
        const canvas = document.getElementById('charge-efficiency-chart');
        if (!canvas) { return; }
        efficiencyLoaded = true;

        fetch(`/api/charging-efficiency?hours=${encodeURIComponent(activeHours())}`)
            .then(response => response.json())
            .then(data => {
                const points = (data && data.points) || [];
                const summary = (data && data.summary) || {};
                const note = document.getElementById('charge-efficiency-note');

                const headline = document.getElementById('charge-eff-headline');
                const lost = document.getElementById('charge-eff-lost');
                const count = document.getElementById('charge-eff-count');
                if (headline) {
                    headline.textContent = summary.efficiency_pct === null ||
                        summary.efficiency_pct === undefined ? '--' : `${summary.efficiency_pct}%`;
                }
                if (lost) { lost.textContent = `${(summary.total_lost_kwh || 0).toFixed(1)} kWh`; }
                if (count) { count.textContent = String(summary.count || 0); }

                renderEfficiencyTable(points);

                if (!points.length) {
                    if (note) {
                        note.textContent = 'No charging sessions in this range were large enough ' +
                            'to measure. Widen the time range, or wait for a longer session.';
                    }
                    if (chargeEfficiencyChart) { chargeEfficiencyChart.destroy(); chargeEfficiencyChart = null; }
                    return;
                }

                const series = points.map(p => ({ x: new Date(p.start_time), y: p.efficiency_pct }));
                if (chargeEfficiencyChart) { chargeEfficiencyChart.destroy(); }
                chargeEfficiencyChart = new Chart(canvas.getContext('2d'), {
                    type: 'line',
                    data: {
                        datasets: [{
                            label: 'Wall-to-battery efficiency',
                            data: series,
                            borderColor: '#2471a3',
                            backgroundColor: '#2471a3',
                            borderWidth: 2,
                            pointRadius: 5,
                            pointHoverRadius: 8,
                            tension: 0.2
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: { mode: 'nearest', intersect: false },
                        plugins: {
                            legend: { display: false },
                            tooltip: {
                                callbacks: {
                                    label: context => {
                                        const p = points[context.dataIndex];
                                        return [
                                            `Efficiency: ${p.efficiency_pct}% (±${p.uncertainty_pct}%)`,
                                            `Wall: ${p.ac_kwh} kWh over ${p.duration_hours} h`,
                                            `Battery: ${p.pack_kwh} kWh (+${p.soc_gain}%)`,
                                            `Lost: ${p.lost_kwh} kWh`,
                                            p.temperature === null ? '' : `Ambient: ${localTemp(p.temperature).toFixed(1)} ${localTempLabel()}`
                                        ].filter(Boolean);
                                    }
                                }
                            }
                        },
                        scales: {
                            x: { type: 'time', time: { unit: 'day' }, title: { display: true, text: 'Session start' } },
                            y: {
                                title: { display: true, text: 'Efficiency (%)' },
                                suggestedMin: 40,
                                suggestedMax: 100,
                                ticks: { callback: value => `${value}%` }
                            }
                        }
                    }
                });
            })
            .catch(error => {
                console.error('Failed to load charging efficiency:', error);
                const note = document.getElementById('charge-efficiency-note');
                if (note) { note.textContent = 'Could not load charging efficiency data.'; }
            });
    }

    /* --- driving efficiency by month ------------------------------------- */

    let monthlyChart = null;
    let monthlyLoaded = false;

    // Diverging ramp about freezing: temperature has a meaningful midpoint, so
    // two hues with a neutral middle rather than a rainbow. Endpoints are the
    // ColorBrewer RdBu poles, which stay separable under colour-vision deficiency.
    function temperatureColor(celsius) {
        if (celsius === null || celsius === undefined) { return '#9e9e9e'; }
        const span = 25;
        const ratio = Math.max(-1, Math.min(1, celsius / span));
        const cold = [33, 102, 172];
        const warm = [178, 24, 43];
        // #999 reads at only 2.85:1 on the chart surface; this clears the 3:1
        // floor for graphical objects so near-freezing months stay visible.
        const mid = [143, 143, 143];
        const target = ratio < 0 ? cold : warm;
        const weight = Math.abs(ratio);
        const channel = i => Math.round(mid[i] + (target[i] - mid[i]) * weight);
        return `rgb(${channel(0)}, ${channel(1)}, ${channel(2)})`;
    }

    function renderMonthlyTable(months) {
        const host = document.getElementById('monthly-efficiency-table');
        if (!host) { return; }
        const metric = unitsAreMetric();
        const energyLabel = metric ? 'Wh/km' : 'mi/kWh';
        const distanceLabel = metric ? 'km' : 'mi';
        const energy = m => (metric ? m.wh_per_km : m.mi_per_kwh);
        const distance = m => (metric ? m.miles * 1.60934 : m.miles);

        const rows = months.map(m => `<tr>
            <td>${m.month}</td>
            <td>${energy(m).toFixed(metric ? 0 : 2)}</td>
            <td>${m.mi_per_kwh.toFixed(2)}</td>
            <td>${m.temperature === null ? '--' : localTemp(m.temperature).toFixed(1)}</td>
            <td>${distance(m).toFixed(0)}</td>
        </tr>`).join('');
        host.innerHTML = `<table><caption class="sr-only">Driving efficiency by month</caption>
            <thead><tr><th scope="col">Month</th><th scope="col">${energyLabel}</th>
            <th scope="col">mi/kWh</th><th scope="col">Avg temp ${localTempLabel()}</th>
            <th scope="col">${distanceLabel}</th></tr></thead><tbody>${rows}</tbody></table>`;
    }

    function loadMonthlyEfficiency() {
        const canvas = document.getElementById('monthly-efficiency-chart');
        if (!canvas) { return; }
        monthlyLoaded = true;

        fetch('/api/efficiency-by-month')
            .then(response => response.json())
            .then(data => {
                const months = (data && data.months) || [];
                if (!months.length) { return; }
                renderMonthlyTable(months);

                // This IIFE is outside the scope holding currentUnits, so it
                // reads the same persisted preference the toggle writes.
                const metric = (localStorage.getItem('units') || 'metric') === 'metric';
                // Same convention as the temperature chart and the trips table:
                // mi/kWh in imperial, Wh/km in metric.
                const unitLabel = metric ? 'Wh/km' : 'mi/kWh';
                const value = m => (metric ? m.wh_per_km : m.mi_per_kwh);
                const tempLabel = localTempLabel();
                const temp = c => localTemp(c);
                const distLabel = metric ? 'km' : 'mi';
                const dist = mi => (metric ? mi * 1.60934 : mi);

                const packKwh = (window.PYVISIONIC_CONFIG &&
                    window.PYVISIONIC_CONFIG.batteryUsableKwh) || 74.0;
                const rangeView = localStorage.getItem('pyvisionic.chartView') === 'range';
                const rampColors = months.map(m => temperatureColor(m.temperature));  // ramp stays on Celsius
                const fullRanges = months.map(m => dist(packKwh * m.mi_per_kwh));

                if (monthlyChart) { monthlyChart.destroy(); }
                monthlyChart = new Chart(canvas.getContext('2d'), {
                    type: 'bar',
                    data: {
                        labels: months.map(m => m.month),
                        datasets: rangeView ? [
                            {
                                label: 'Range at 80% charge',
                                data: fullRanges.map(r => r * 0.8),
                                backgroundColor: rampColors,
                                borderWidth: 0,
                                stack: 'range'
                            },
                            {
                                label: '80% to full charge',
                                data: fullRanges.map(r => r * 0.2),
                                backgroundColor: rampColors.map(c =>
                                    c.replace('rgb(', 'rgba(').replace(')', ', 0.35)')),
                                borderWidth: 0,
                                borderRadius: 4,
                                stack: 'range'
                            }
                        ] : [{
                            label: `Efficiency (${unitLabel})`,
                            data: months.map(value),
                            backgroundColor: rampColors,
                            borderWidth: 0,
                            borderRadius: 4
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { display: rangeView, position: 'top' },
                            tooltip: {
                                callbacks: {
                                    label: context => {
                                        const m = months[context.dataIndex];
                                        const full = fullRanges[context.dataIndex];
                                        if (rangeView) {
                                            return [
                                                `${(full * 0.8).toFixed(0)} ${distLabel} at an 80% charge`,
                                                `${full.toFixed(0)} ${distLabel} at 100%`,
                                                m.temperature === null ? '' : `Avg temperature: ${temp(m.temperature).toFixed(1)} ${tempLabel}`
                                            ].filter(Boolean);
                                        }
                                        return [
                                            `${value(m).toFixed(metric ? 0 : 2)} ${unitLabel} (${m.wh_per_mile.toFixed(0)} Wh/mi)`,
                                            m.temperature === null ? '' : `Avg temperature: ${temp(m.temperature).toFixed(1)} ${tempLabel}`,
                                            `${dist(m.miles).toFixed(0)} ${distLabel} over ${m.trips} trips`
                                        ].filter(Boolean);
                                    }
                                }
                            }
                        },
                        scales: {
                            x: { stacked: rangeView, title: { display: true, text: 'Month' } },
                            y: {
                                stacked: rangeView,
                                beginAtZero: true,
                                title: {
                                    display: true,
                                    text: rangeView
                                        ? `Estimated range (${distLabel})`
                                        : `Efficiency (${unitLabel})`
                                }
                            }
                        }
                    }
                });
            })
            .catch(error => console.error('Failed to load monthly efficiency:', error));
    }

    /* --- battery health -------------------------------------------------- */

    let healthChart = null;
    let healthLoaded = false;

    function loadBatteryHealth() {
        const canvas = document.getElementById('battery-health-chart');
        if (!canvas) { return; }
        healthLoaded = true;

        fetch('/api/battery-health')
            .then(response => response.json())
            .then(data => {
                const points = (data && data.points) || [];
                const note = document.getElementById('battery-health-note');
                const set = (id, text) => {
                    const el = document.getElementById(id);
                    if (el) { el.textContent = text; }
                };

                set('pack-estimate', data.current_estimate_kwh ?
                    `${data.current_estimate_kwh} kWh` : '--');
                set('pack-sessions', String(data.session_count || points.length || 0));
                set('pack-span', data.span_years ? data.span_years.toFixed(1) : '--');

                // The verdict is the point of this section. A trendline through
                // scatter that cannot resolve a year of degradation would look
                // authoritative and mean nothing, so say so instead.
                if (note && data.reason) {
                    note.textContent = data.verdict === 'insufficient_data'
                        ? `Not enough history yet. ${data.reason}`
                        : `No measurable degradation yet. ${data.reason}`;
                }

                if (!points.length) {
                    if (healthChart) { healthChart.destroy(); healthChart = null; }
                    return;
                }

                if (healthChart) { healthChart.destroy(); }
                healthChart = new Chart(canvas.getContext('2d'), {
                    type: 'scatter',
                    data: {
                        datasets: [{
                            label: 'Measured capacity',
                            data: points.map(p => ({ x: new Date(p.date), y: p.pack_kwh })),
                            backgroundColor: '#2471a3',
                            borderColor: '#2471a3',
                            pointRadius: 6,
                            pointHoverRadius: 9
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { display: false },
                            tooltip: {
                                callbacks: {
                                    label: context => {
                                        const p = points[context.dataIndex];
                                        return [
                                            `${p.pack_kwh} kWh (±${p.uncertainty_pct}%)`,
                                            `${p.energy_kwh} kWh over ${p.soc_points}% of charge`
                                        ];
                                    }
                                }
                            }
                        },
                        scales: {
                            x: { type: 'time', time: { unit: 'month' },
                                 title: { display: true, text: 'Session date' } },
                            y: { title: { display: true, text: 'Implied usable capacity (kWh)' } }
                        }
                    }
                });
            })
            .catch(error => console.error('Failed to load battery health:', error));
    }

    /* --- shared lookup so other tables can show the same measurement ------ */

    let allPointsPromise = null;

    function loadAllEfficiencyPoints() {
        if (!allPointsPromise) {
            allPointsPromise = fetch('/api/charging-efficiency?hours=all')
                .then(response => (response.ok ? response.json() : { points: [] }))
                .then(data => (data && data.points) || [])
                .catch(() => []);
        }
        return allPointsPromise;
    }

    // Efficiency points are timestamped from the SOC-rising window, which starts
    // a little after the session itself, so match on nearest start within a
    // tolerance rather than on equality.
    function matchEfficiency(points, sessionStart, toleranceMinutes) {
        if (!points || !points.length || !sessionStart) { return null; }
        const target = sessionStart.getTime();
        const limit = (toleranceMinutes || 120) * 60000;
        let best = null;
        let bestGap = Infinity;
        points.forEach(point => {
            const gap = Math.abs(new Date(point.start_time).getTime() - target);
            if (gap < bestGap && gap <= limit) { best = point; bestGap = gap; }
        });
        return best;
    }

    window.PYVISIONIC_WEATHER_REFRESH = function () {
        if (monthlyLoaded) { loadMonthlyEfficiency(); }
        if (efficiencyLoaded) { loadChargingEfficiency(); }
    };

    window.PYVISIONIC_EFFICIENCY = {
        load: loadAllEfficiencyPoints,
        match: matchEfficiency
    };

    document.addEventListener('DOMContentLoaded', function () {
        // Reflect the stored chart view on the toggle buttons.
        const view = localStorage.getItem('pyvisionic.chartView') || 'efficiency';
        document.querySelectorAll('.chart-view-btn').forEach(button => {
            const on = button.dataset.view === view;
            button.classList.toggle('active', on);
            button.setAttribute('aria-pressed', String(on));
        });
    });

    document.addEventListener('DOMContentLoaded', initTabs);
    document.addEventListener('click', event => {
        // Reload efficiency when the master time range changes and we are on that tab.
        if (event.target.closest('.master-time-range-controls .time-range-btn')) {
            const weather = document.getElementById('tab-weather');
            if (weather && weather.getAttribute('aria-selected') === 'true') {
                setTimeout(loadChargingEfficiency, 0);
            } else {
                efficiencyLoaded = false;
            }
        }
    });
})();

/* ===========================================================================
   Modal dialog accessibility
   The close control was a <span>, so it was unreachable by keyboard and
   announced as "times". It is now a button; this adds Escape-to-close and
   returns focus to whatever opened the dialog.
   =========================================================================== */

(function () {
    'use strict';

    let lastFocused = null;

    function openModals() {
        return Array.from(document.querySelectorAll('.modal'))
            .filter(modal => modal.style.display && modal.style.display !== 'none');
    }

    function closeModal(modal) {
        modal.style.display = 'none';
        if (lastFocused && document.contains(lastFocused)) {
            lastFocused.focus();
        }
        lastFocused = null;
    }

    document.addEventListener('keydown', function (event) {
        if (event.key !== 'Escape') { return; }
        const open = openModals();
        if (open.length) {
            event.preventDefault();
            open.forEach(closeModal);
        }
    });

    // Remember the trigger so focus can return to it, and move focus into the
    // dialog so a keyboard user is not left behind the overlay.
    document.addEventListener('click', function (event) {
        const trigger = event.target.closest('.charge-row, .trip-row, tr[data-session]');
        if (trigger) {
            lastFocused = document.activeElement;
            setTimeout(function () {
                const open = openModals();
                if (open.length) {
                    const closer = open[open.length - 1].querySelector('.modal-close');
                    if (closer) { closer.focus(); }
                }
            }, 0);
        }
    });
})();
