#!/usr/bin/env python3
"""
CSV Data Manager for PyVisionIQ
Handles data rotation, compression, and efficient loading
"""

import os
import pandas as pd
import gzip
import shutil
from datetime import datetime, timedelta
from pathlib import Path


class CSVDataManager:
    def __init__(self, csv_file='vehicle_data.csv', archive_dir='data_archive'):
        self.csv_file = Path(csv_file)
        self.archive_dir = Path(archive_dir)
        self.archive_dir.mkdir(exist_ok=True)
        
        # Cache for recent data
        self._data_cache = None
        self._cache_timestamp = None
        self._cache_duration = timedelta(minutes=5)
        
    def append_data(self, data_dict):
        """Append new data to CSV"""
        df = pd.DataFrame([data_dict])
        
        # Write with header if new file
        if not self.csv_file.exists():
            df.to_csv(self.csv_file, index=False)
        else:
            df.to_csv(self.csv_file, mode='a', header=False, index=False)
            
        # Clear cache
        self._data_cache = None
        
        # Check if rotation needed
        self._check_rotation()
        
    def get_all_data(self, use_cache=True):
        """Get all data with caching"""
        if use_cache and self._is_cache_valid():
            return self._data_cache
            
        # Load current file
        if self.csv_file.exists():
            current_data = pd.read_csv(self.csv_file)
            current_data['Timestamp'] = pd.to_datetime(current_data['Timestamp'])
        else:
            current_data = pd.DataFrame()
            
        # Load archived data for complete history
        archived_data = self._load_archived_data()
        
        # Combine all data
        if not archived_data.empty and not current_data.empty:
            all_data = pd.concat([archived_data, current_data], ignore_index=True)
        elif not current_data.empty:
            all_data = current_data
        else:
            all_data = archived_data
            
        # Update cache
        self._data_cache = all_data
        self._cache_timestamp = datetime.now()
        
        return all_data
        
    def get_recent_data(self, hours=24):
        """Get data from last N hours"""
        all_data = self.get_all_data()
        if all_data.empty:
            return all_data
            
        cutoff = datetime.now() - timedelta(hours=hours)
        return all_data[all_data['Timestamp'] > cutoff]
        
    def get_data_range(self, start_date, end_date):
        """Get data within date range"""
        all_data = self.get_all_data()
        if all_data.empty:
            return all_data
            
        mask = (all_data['Timestamp'] >= start_date) & (all_data['Timestamp'] <= end_date)
        return all_data[mask]
        
    def _check_rotation(self):
        """Rotate CSV file if it's too old or too large"""
        if not self.csv_file.exists():
            return
            
        # Rotate if file is larger than 10MB
        file_size_mb = self.csv_file.stat().st_size / (1024 * 1024)
        if file_size_mb > 10:
            self._rotate_file("size_limit")
            return
            
        # Rotate if file has data older than 1 year
        try:
            df = pd.read_csv(self.csv_file, nrows=1)
            df['Timestamp'] = pd.to_datetime(df['Timestamp'])
            oldest_date = df['Timestamp'].min()
            
            if oldest_date < datetime.now() - timedelta(days=365):
                self._rotate_file("annual")
        except:
            pass
            
    def _rotate_file(self, reason="manual"):
        """Archive current CSV file"""
        if not self.csv_file.exists():
            return
            
        # Create archive filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f"vehicle_data_{reason}_{timestamp}.csv.gz"
        archive_path = self.archive_dir / archive_name
        
        # Compress and move
        with open(self.csv_file, 'rb') as f_in:
            with gzip.open(archive_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
                
        # Remove original
        self.csv_file.unlink()
        
        # Clear cache
        self._data_cache = None
        
        print(f"Rotated data to {archive_path}")
        
    def _load_archived_data(self):
        """Load all archived data files"""
        archived_dfs = []
        
        for archive_file in sorted(self.archive_dir.glob("*.csv.gz")):
            try:
                df = pd.read_csv(archive_file, compression='gzip')
                df['Timestamp'] = pd.to_datetime(df['Timestamp'])
                archived_dfs.append(df)
            except Exception as e:
                print(f"Error loading {archive_file}: {e}")
                
        if archived_dfs:
            return pd.concat(archived_dfs, ignore_index=True)
        else:
            return pd.DataFrame()
            
    def _is_cache_valid(self):
        """Check if cache is still valid"""
        if self._data_cache is None or self._cache_timestamp is None:
            return False
            
        return datetime.now() - self._cache_timestamp < self._cache_duration
        
    def get_statistics(self):
        """Get data statistics"""
        all_data = self.get_all_data()
        
        if all_data.empty:
            return {
                'total_records': 0,
                'date_range': 'No data',
                'current_file_size': '0 KB',
                'archived_files': 0
            }
            
        current_size = self.csv_file.stat().st_size / 1024 if self.csv_file.exists() else 0
        archived_count = len(list(self.archive_dir.glob("*.csv.gz")))
        
        return {
            'total_records': len(all_data),
            'date_range': f"{all_data['Timestamp'].min()} to {all_data['Timestamp'].max()}",
            'current_file_size': f"{current_size:.1f} KB",
            'archived_files': archived_count,
            'oldest_record': all_data['Timestamp'].min(),
            'newest_record': all_data['Timestamp'].max()
        }


# Example usage and migration
if __name__ == "__main__":
    manager = CSVDataManager()
    
    # Get statistics
    stats = manager.get_statistics()
    print("Data Statistics:")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    
    # Example: Get last 7 days of data
    recent_data = manager.get_recent_data(hours=24*7)
    print(f"\nRecords from last 7 days: {len(recent_data)}")