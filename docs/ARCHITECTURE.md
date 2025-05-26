# PyVisionic Architecture Documentation

This document provides comprehensive architectural diagrams and technical documentation for the PyVisionic application.

## Table of Contents
- [System Architecture Overview](#system-architecture-overview)
- [Data Flow Diagram](#data-flow-diagram)
- [Web UI Architecture](#web-ui-architecture)
- [API Endpoints Structure](#api-endpoints-structure)
- [Data Storage Schema](#data-storage-schema)

## System Architecture Overview

The following diagram shows the complete system architecture including external services, internal components, and data flow:

```mermaid
graph TB
    subgraph "External Services"
        API[Hyundai/Kia Connect API<br/>30 calls/day limit]
        Weather[Open-Meteo API<br/>Weather data]
        OSM[OpenStreetMap<br/>Map tiles]
    end
    
    subgraph "PyVisionic Container"
        subgraph "Background Services"
            DC[Data Collector<br/>Continuous collection<br/>48min intervals]
        end
        
        subgraph "API Layer"
            Client[CachedVehicleClient<br/>Authentication<br/>Caching<br/>Error handling]
            SSL[SSL Patch<br/>Certificate fix]
        end
        
        subgraph "Storage Layer"
            CSV[CSVStorage<br/>4 CSV files<br/>Deduplication]
            Cache[Cache Files<br/>JSON responses<br/>48hr retention]
        end
        
        subgraph "Web Layer"
            Flask[Flask App<br/>12 API endpoints<br/>Gunicorn WSGI]
            Static[Static Assets<br/>CSS/JS/Images]
        end
        
        subgraph "Data Files"
            Trips[trips.csv]
            Battery[battery_status.csv]
            Locations[locations.csv]
            Sessions[charging_sessions.csv]
        end
    end
    
    subgraph "User Interface"
        Browser[Web Browser<br/>Dashboard UI]
        Charts[Chart.js<br/>Battery/Energy charts]
        Maps[Leaflet Maps<br/>Trip locations]
    end
    
    API --> Client
    Weather --> Client
    Client --> SSL
    Client --> Cache
    DC --> Client
    Client --> CSV
    CSV --> Trips
    CSV --> Battery
    CSV --> Locations
    CSV --> Sessions
    Flask --> CSV
    Flask --> Cache
    Flask --> Static
    Browser --> Flask
    Browser --> Charts
    Browser --> Maps
    Maps --> OSM
```

## Data Flow Diagram

This sequence diagram illustrates how data flows through the system from collection to display:

```mermaid
sequenceDiagram
    participant DC as Data Collector
    participant API as Hyundai/Kia API
    participant Cache as Cache Files
    participant CSV as CSV Storage
    participant Flask as Flask App
    participant UI as Web UI
    participant Weather as Open-Meteo API
    
    Note over DC: Every 48 minutes
    DC->>Cache: Check cache validity
    alt Cache Invalid
        DC->>API: Request vehicle data
        API-->>DC: Vehicle data response
        DC->>Cache: Store response
        DC->>Weather: Get temperature data
        Weather-->>DC: Weather response
        DC->>CSV: Parse and store data
        CSV->>CSV: Deduplicate trips
        CSV->>CSV: Detect charging sessions
    end
    
    Note over UI: User visits dashboard
    UI->>Flask: GET /
    Flask-->>UI: HTML dashboard
    
    UI->>Flask: GET /api/current-status
    Flask->>CSV: Read latest battery data
    CSV-->>Flask: Battery status
    Flask-->>UI: JSON response
    UI->>UI: Update status display
    
    UI->>Flask: GET /api/battery-history
    Flask->>CSV: Read battery history
    CSV-->>Flask: Historical data
    Flask-->>UI: JSON response
    UI->>UI: Render charts
    
    Note over UI: User clicks refresh
    UI->>Flask: POST /api/refresh
    Flask->>DC: Force data collection
    DC->>API: Fresh data request
    API-->>DC: Latest data
    DC->>CSV: Update storage
    Flask-->>UI: Success response
    UI->>UI: Reload dashboard
```

## Web UI Architecture

The dashboard user interface structure and JavaScript interactions:

```mermaid
graph TB
    subgraph "Dashboard UI Structure"
        subgraph "Header Section"
            Title[PyVisionic Title]
            Status[Data Collection Status<br/>API calls: X/30<br/>Next: in Y minutes]
        end
        
        subgraph "Main Content"
            subgraph "Current Status Card"
                Battery[Battery Level: X%]
                Range[Range: X km/mi]
                Charging[⚡ Charging: X kW]
                Location[Last Location]
                Weather[Weather: X°C/F]
            end
            
            subgraph "Charts Section"
                BatteryChart[Battery History Chart<br/>Chart.js Line Chart]
                EnergyChart[Energy Usage Chart<br/>Chart.js Doughnut]
                TempChart[Temperature Impact<br/>Chart.js Scatter + Bar]
            end
            
            subgraph "Data Tables"
                TripsTable[Recent Trips<br/>Paginated table]
                ChargingTable[Charging Sessions<br/>History display]
            end
            
            subgraph "Interactive Map"
                LocationsMap[Trip Locations<br/>Leaflet map<br/>Red dots for trips<br/>Blue dot for current]
            end
        end
        
        subgraph "Modals"
            TripModal[Trip Details Modal<br/>Individual trip map<br/>Energy breakdown]
            CacheModal[Cache Management<br/>File browser<br/>Clear options]
        end
        
        subgraph "Controls"
            UnitsToggle[Metric/Imperial Toggle]
            RefreshBtn[Manual Refresh]
            ClearCache[Clear Cache]
        end
    end
    
    subgraph "JavaScript Interactions"
        Dashboard[dashboard.js<br/>Main controller]
        AJAX[AJAX requests<br/>Real-time updates]
        Charts[Chart.js instances]
        MapCtrl[Map controllers]
        Pagination[Trip pagination]
        Modals[Modal handlers]
        Units[Unit conversion]
        Notifications[Toast notifications]
    end
    
    Dashboard --> AJAX
    Dashboard --> Charts
    Dashboard --> MapCtrl
    Dashboard --> Pagination
    Dashboard --> Modals
    Dashboard --> Units
    Dashboard --> Notifications
    
    AJAX --> Status
    AJAX --> Battery
    AJAX --> BatteryChart
    AJAX --> EnergyChart
    AJAX --> TempChart
    AJAX --> TripsTable
    AJAX --> LocationsMap
```

## API Endpoints Structure

The Flask application's API endpoints and their relationships to data sources:

```mermaid
graph LR
    subgraph "Flask Routes"
        Root["/"]
        
        subgraph "Data Endpoints"
            CurrentStatus["/api/current-status<br/>Latest vehicle data"]
            BatteryHistory["/api/battery-history<br/>Historical battery data"]
            Trips["/api/trips<br/>Paginated trip list"]
            TripDetail["/api/trip/<id><br/>Individual trip data"]
            Locations["/api/locations<br/>All trip coordinates"]
            ChargingSessions["/api/charging-sessions<br/>Charging history"]
            EfficiencyStats["/api/efficiency-stats<br/>Performance statistics"]
            TempEfficiency["/api/temperature-efficiency<br/>Temperature impact analysis"]
        end
        
        subgraph "Control Endpoints"
            Refresh["/api/refresh<br/>Force data collection"]
            CollectionStatus["/api/collection-status<br/>API usage tracking"]
            ClearCache["/api/clear-cache<br/>Cache management"]
        end
        
        subgraph "Cache Interface"
            CacheFiles["/cache/<path><br/>Cache file browser"]
        end
    end
    
    subgraph "Data Sources"
        CSVFiles[CSV Storage<br/>trips.csv<br/>battery_status.csv<br/>locations.csv<br/>charging_sessions.csv]
        CacheJSON[Cache Files<br/>JSON responses]
        APIHistory[API Call History<br/>Rate limit tracking]
    end
    
    CurrentStatus --> CSVFiles
    BatteryHistory --> CSVFiles
    Trips --> CSVFiles
    TripDetail --> CSVFiles
    Locations --> CSVFiles
    ChargingSessions --> CSVFiles
    EfficiencyStats --> CSVFiles
    TempEfficiency --> CSVFiles
    
    Refresh --> CSVFiles
    CollectionStatus --> APIHistory
    ClearCache --> CacheJSON
    CacheFiles --> CacheJSON
```

## Data Storage Schema

The entity relationship diagram for PyVisionic's data storage:

```mermaid
erDiagram
    TRIPS {
        string date
        float distance
        int duration
        float average_speed
        float max_speed
        float total_consumed
        float regenerated_energy
        float drivetrain_consumed
        float climate_consumed
        float accessories_consumed
        float battery_care_consumed
        float odometer_start
        float end_latitude
        float end_longitude
        float end_temperature
        float meteo_temp
        float vehicle_temp
    }
    
    BATTERY_STATUS {
        datetime timestamp
        datetime api_last_updated
        string vehicle_id
        float odometer
        int battery_level
        boolean is_charging
        float charging_power
        int range
        float latitude
        float longitude
        datetime location_last_updated
        float temperature
        float meteo_temp
        float vehicle_temp
    }
    
    LOCATIONS {
        datetime timestamp
        float latitude
        float longitude
        string source
        float meteo_temp
        float vehicle_temp
    }
    
    CHARGING_SESSIONS {
        datetime session_id
        datetime start_time
        datetime end_time
        int start_battery
        int end_battery
        float energy_added
        int duration_minutes
        float avg_power
        float max_power
        string status
    }
    
    CACHE_FILES {
        string filename
        datetime timestamp
        json data
        string cache_type
    }
    
    API_HISTORY {
        date last_reset
        int calls_today
        datetime last_call
        int daily_limit
    }
```

## Key Features

### Data Collection
- **Rate Limited**: Respects Hyundai/Kia's 30 calls/day limit
- **Smart Scheduling**: Evenly distributes collections throughout the day (every 48 minutes)
- **Caching**: Maintains 48-hour cache to minimize API calls
- **Weather Integration**: Fetches temperature data from Open-Meteo API

### Data Storage
- **CSV Format**: Lightweight, portable data storage
- **Deduplication**: Prevents duplicate trip entries
- **Session Detection**: Automatically identifies charging sessions
- **Historical Data**: Maintains complete history for analysis

### Web Interface
- **Real-time Updates**: Live battery status and charging information
- **Interactive Charts**: Battery history, energy usage, temperature impact
- **Trip Management**: Paginated trip list with detailed views
- **Map Visualization**: Shows trip endpoints and current location
- **Unit Conversion**: Toggle between metric and imperial units

### API Design
- **RESTful**: Clean, predictable endpoint structure
- **JSON Responses**: Consistent data format
- **Error Handling**: Comprehensive error classification
- **Pagination**: Efficient handling of large datasets

## Security Considerations

1. **API Credentials**: Stored in environment variables
2. **SSL Support**: Custom SSL patching for API compatibility
3. **No Direct API Exposure**: All API calls go through the backend
4. **Rate Limit Protection**: Prevents exceeding API quotas

## Performance Optimizations

1. **Worker Configuration**: 4 Gunicorn workers with 2 threads each
2. **Chart Updates**: Modify existing charts instead of recreating
3. **Data Caching**: Reduces API calls and improves response times
4. **Lazy Loading**: Maps and charts load on demand
5. **Pagination**: Limits data transfer for large datasets

## Deployment

The application is containerized using Docker with:
- Python 3.11 slim base image
- Gunicorn WSGI server
- Background data collector process
- Volume mounts for persistent data
- Environment-based configuration

See `docker-compose.yml` for deployment configuration.