#!/usr/bin/env python3
"""Analyze error files in cache directory to understand API limit patterns."""

import json
import os
from datetime import datetime
from collections import defaultdict, Counter
from pathlib import Path

def analyze_error_files():
    cache_dir = Path("/home/ubuntu/new-pyvisionic/cache")
    
    # Data structures for analysis
    errors_by_hour = defaultdict(list)
    errors_by_date = defaultdict(list)
    error_types = Counter()
    error_messages = Counter()
    night_errors = 0  # 8PM-4AM (20:00-04:00)
    day_errors = 0    # 4AM-8PM (04:00-20:00)
    api_calls_by_date = defaultdict(int)
    successful_calls_by_date = defaultdict(int)
    successful_calls_by_hour = defaultdict(int)
    
    # Process all error files
    error_files = sorted([f for f in cache_dir.glob("error_*.json")])
    
    print(f"Total error files found: {len(error_files)}")
    print("=" * 80)
    
    for error_file in error_files:
        try:
            # Parse timestamp from filename
            filename = error_file.name
            # Format: error_YYYYMMDD_HHMMSS.json
            date_str = filename.split('_')[1]
            time_str = filename.split('_')[2].replace('.json', '')
            
            year = int(date_str[:4])
            month = int(date_str[4:6])
            day = int(date_str[6:8])
            hour = int(time_str[:2])
            minute = int(time_str[2:4])
            second = int(time_str[4:6])
            
            timestamp = datetime(year, month, day, hour, minute, second)
            date_key = timestamp.strftime("%Y-%m-%d")
            hour_key = hour
            
            # Read error content
            with open(error_file, 'r') as f:
                error_data = json.load(f)
            
            # Store error info
            error_info = {
                'timestamp': timestamp,
                'file': filename,
                'data': error_data
            }
            
            errors_by_hour[hour_key].append(error_info)
            errors_by_date[date_key].append(error_info)
            
            # Count API calls (assuming each error is a failed API call)
            api_calls_by_date[date_key] += 1
            
            # Check if night or day error
            if hour >= 20 or hour < 4:
                night_errors += 1
            else:
                day_errors += 1
            
            # Extract error type and message from the correct fields
            error_type = error_data.get('error_type', '')
            error_msg = error_data.get('error_message', '')
            
            if error_msg:
                error_messages[error_msg] += 1
                
                # Try to categorize error type
                error_msg_lower = error_msg.lower()
                if 'rate limit' in error_msg_lower or 'too many' in error_msg_lower or 'quota' in error_msg_lower:
                    error_types['rate_limit'] += 1
                elif 'unauthorized' in error_msg_lower:
                    error_types['unauthorized'] += 1
                elif 'timeout' in error_msg_lower:
                    error_types['timeout'] += 1
                elif 'connection' in error_msg_lower:
                    error_types['connection'] += 1
                elif 'keyerror' in error_msg_lower:
                    error_types['keyerror'] += 1
                else:
                    error_types['other'] += 1
            
        except Exception as e:
            print(f"Error processing {filename}: {e}")
    
    # Process successful history files
    history_files = sorted([f for f in cache_dir.glob("history_*.json")])
    print(f"Total history files found: {len(history_files)}")
    
    for history_file in history_files:
        try:
            # Parse timestamp from filename
            # Format: history_YYYYMMDD_HHMMSS_<hash>.json
            parts = history_file.name.split('_')
            date_str = parts[1]
            time_str = parts[2]
            
            year = int(date_str[:4])
            month = int(date_str[4:6])
            day = int(date_str[6:8])
            hour = int(time_str[:2])
            
            timestamp = datetime(year, month, day, hour, 0, 0)
            date_key = timestamp.strftime("%Y-%m-%d")
            
            successful_calls_by_date[date_key] += 1
            successful_calls_by_hour[hour] += 1
            
        except Exception as e:
            print(f"Error processing history file {history_file.name}: {e}")
    
    # Print analysis results
    print("\n1. ERRORS BY HOUR OF DAY")
    print("-" * 40)
    for hour in range(24):
        count = len(errors_by_hour[hour])
        bar = '*' * min(count, 50)
        print(f"{hour:02d}:00 | {count:4d} | {bar}")
    
    print("\n2. NIGHT vs DAY ERRORS")
    print("-" * 40)
    print(f"Night errors (8PM-4AM): {night_errors}")
    print(f"Day errors (4AM-8PM): {day_errors}")
    print(f"Night/Day ratio: {night_errors/day_errors if day_errors > 0 else 'inf':.2f}")
    
    print("\n3. SUCCESSFUL CALLS BY HOUR OF DAY")
    print("-" * 40)
    for hour in range(24):
        count = successful_calls_by_hour[hour]
        bar = '*' * min(count, 50)
        print(f"{hour:02d}:00 | {count:4d} | {bar}")
    
    print("\n4. ERRORS BY DATE")
    print("-" * 40)
    for date in sorted(errors_by_date.keys()):
        count = len(errors_by_date[date])
        print(f"{date}: {count} errors")
    
    print("\n5. TOTAL API CALLS PER DAY")
    print("-" * 40)
    all_dates = sorted(set(list(errors_by_date.keys()) + list(successful_calls_by_date.keys())))
    for date in all_dates:
        errors = len(errors_by_date.get(date, []))
        successes = successful_calls_by_date.get(date, 0)
        total = errors + successes
        print(f"{date}: {total} total calls ({successes} successful, {errors} errors)")
    
    print("\n6. ERROR TYPES")
    print("-" * 40)
    for error_type, count in error_types.most_common():
        print(f"{error_type}: {count}")
    
    print("\n7. TOP ERROR MESSAGES")
    print("-" * 40)
    for msg, count in error_messages.most_common(10):
        print(f"{count}x: {msg[:100]}...")
    
    # Check for consistent cutoff around 8PM
    print("\n8. CHECKING FOR 8PM CUTOFF PATTERN")
    print("-" * 40)
    for date in sorted(errors_by_date.keys()):
        date_errors = errors_by_date[date]
        # Find first and last error times
        if date_errors:
            times = sorted([e['timestamp'] for e in date_errors])
            first_error = times[0].strftime("%H:%M:%S")
            last_error = times[-1].strftime("%H:%M:%S")
            
            # Check if errors start around 8PM
            evening_errors = [e for e in date_errors if e['timestamp'].hour >= 20]
            if evening_errors:
                first_evening = min(evening_errors, key=lambda x: x['timestamp'])
                print(f"{date}: First error at {first_error}, Last at {last_error}, "
                      f"First evening error at {first_evening['timestamp'].strftime('%H:%M:%S')}")
    
    # Look for specific API limit messages
    print("\n9. API LIMIT RELATED MESSAGES")
    print("-" * 40)
    api_limit_msgs = [msg for msg in error_messages.keys() 
                      if any(keyword in msg.lower() for keyword in 
                             ['limit', 'quota', 'exceeded', 'too many', 'rate'])]
    for msg in api_limit_msgs:
        print(f"{error_messages[msg]}x: {msg}")
    
    # Analyze error patterns - do they stop at certain times?
    print("\n10. ERROR TIMING PATTERNS")
    print("-" * 40)
    for date in sorted(errors_by_date.keys()):
        date_errors = errors_by_date[date]
        if date_errors:
            hours_with_errors = sorted(set(e['timestamp'].hour for e in date_errors))
            # Check for gaps
            if hours_with_errors:
                gaps = []
                for i in range(len(hours_with_errors)-1):
                    if hours_with_errors[i+1] - hours_with_errors[i] > 1:
                        gaps.append(f"{hours_with_errors[i]:02d}:00-{hours_with_errors[i+1]:02d}:00")
                
                print(f"{date}: Errors in hours {hours_with_errors}")
                if gaps:
                    print(f"  Gaps: {', '.join(gaps)}")

if __name__ == "__main__":
    analyze_error_files()