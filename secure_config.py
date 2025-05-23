'''Secure configuration management with encryption for sensitive data'''
import os
import json
import base64
from pathlib import Path
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from getpass import getpass
import sys

class SecureConfig:
    def __init__(self, config_file='config.enc'):
        self.config_file = Path(config_file)
        self.config_dir = Path('.pyvisioniq')
        self.config_path = self.config_dir / self.config_file
        self.key_file = self.config_dir / '.key'
        self._cipher = None
        self._config = {}
        
        # Create config directory if it doesn't exist
        self.config_dir.mkdir(exist_ok=True)
        
        # Set restrictive permissions on config directory
        if os.name != 'nt':  # Unix-like systems
            os.chmod(self.config_dir, 0o700)
    
    def _derive_key(self, password: str, salt: bytes) -> bytes:
        '''Derive encryption key from password using PBKDF2'''
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return key
    
    def _get_or_create_key(self, password: str = None) -> Fernet:
        '''Get or create encryption key'''
        if self._cipher:
            return self._cipher
            
        if self.key_file.exists():
            # Load existing salt
            with open(self.key_file, 'rb') as f:
                salt = f.read()
        else:
            # Generate new salt
            salt = os.urandom(16)
            with open(self.key_file, 'wb') as f:
                f.write(salt)
            if os.name != 'nt':
                os.chmod(self.key_file, 0o600)
        
        if password is None:
            password = os.getenv('PYVISIONIQ_MASTER_PASSWORD')
            if not password:
                password = getpass('Enter master password: ')
        
        key = self._derive_key(password, salt)
        self._cipher = Fernet(key)
        return self._cipher
    
    def load_config(self, password: str = None) -> dict:
        '''Load and decrypt configuration'''
        if not self.config_path.exists():
            return {}
        
        try:
            cipher = self._get_or_create_key(password)
            
            with open(self.config_path, 'rb') as f:
                encrypted_data = f.read()
            
            decrypted_data = cipher.decrypt(encrypted_data)
            self._config = json.loads(decrypted_data.decode())
            return self._config
        except Exception as e:
            print(f"Error loading config: {e}", file=sys.stderr)
            return {}
    
    def save_config(self, config: dict, password: str = None):
        '''Encrypt and save configuration'''
        try:
            cipher = self._get_or_create_key(password)
            
            data = json.dumps(config, indent=2).encode()
            encrypted_data = cipher.encrypt(data)
            
            with open(self.config_path, 'wb') as f:
                f.write(encrypted_data)
            
            if os.name != 'nt':
                os.chmod(self.config_path, 0o600)
            
            self._config = config
            print("Configuration saved successfully", file=sys.stderr)
        except Exception as e:
            print(f"Error saving config: {e}", file=sys.stderr)
            raise
    
    def get(self, key: str, default=None):
        '''Get configuration value'''
        return self._config.get(key, default)
    
    def set(self, key: str, value):
        '''Set configuration value'''
        self._config[key] = value
    
    def get_all(self) -> dict:
        '''Get all configuration values'''
        return self._config.copy()
    
    def update(self, updates: dict):
        '''Update multiple configuration values'''
        self._config.update(updates)
    
    def delete(self, key: str):
        '''Delete configuration value'''
        if key in self._config:
            del self._config[key]
    
    def exists(self) -> bool:
        '''Check if configuration file exists'''
        return self.config_path.exists()
    
    def initialize_from_env(self):
        '''Initialize configuration from environment variables'''
        env_mapping = {
            'BLUELINKUSER': 'username',
            'BLUELINKPASS': 'password',
            'BLUELINKPIN': 'pin',
            'BLUELINKREGION': 'region',
            'BLUELINKBRAND': 'brand',
            'BLUELINKVID': 'vehicle_id',
            'BLUELINKUPDATE': 'auto_update',
            'BLUELINKLIMIT': 'api_limit',
            'BLUELINKPORT': 'port',
            'BLUELINKHOST': 'host',
            'BLUELINKCSV': 'csv_file'
        }
        
        config = {}
        for env_var, config_key in env_mapping.items():
            value = os.getenv(env_var)
            if value is not None:
                # Convert string booleans to actual booleans
                if value.lower() in ('true', 'false'):
                    value = value.lower() == 'true'
                # Convert numeric strings to integers
                elif config_key in ('api_limit', 'port', 'region', 'brand'):
                    try:
                        value = int(value)
                    except ValueError:
                        pass
                config[config_key] = value
        
        return config
    
    def export_to_env(self) -> dict:
        '''Export configuration to environment variable format'''
        config_mapping = {
            'username': 'BLUELINKUSER',
            'password': 'BLUELINKPASS',
            'pin': 'BLUELINKPIN',
            'region': 'BLUELINKREGION',
            'brand': 'BLUELINKBRAND',
            'vehicle_id': 'BLUELINKVID',
            'auto_update': 'BLUELINKUPDATE',
            'api_limit': 'BLUELINKLIMIT',
            'port': 'BLUELINKPORT',
            'host': 'BLUELINKHOST',
            'csv_file': 'BLUELINKCSV'
        }
        
        env_vars = {}
        for config_key, env_var in config_mapping.items():
            value = self._config.get(config_key)
            if value is not None:
                # Convert booleans to string
                if isinstance(value, bool):
                    value = 'True' if value else 'False'
                env_vars[env_var] = str(value)
        
        return env_vars


# CLI for managing configuration
if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='PyVisionIQ Secure Configuration Manager')
    parser.add_argument('command', choices=['init', 'show', 'set', 'get', 'delete', 'export'],
                        help='Command to execute')
    parser.add_argument('--key', help='Configuration key')
    parser.add_argument('--value', help='Configuration value')
    parser.add_argument('--password', help='Master password (prompt if not provided)')
    parser.add_argument('--from-env', action='store_true', 
                        help='Initialize from environment variables')
    
    args = parser.parse_args()
    
    config = SecureConfig()
    
    if args.command == 'init':
        if args.from_env:
            # Initialize from environment variables
            env_config = config.initialize_from_env()
            if not env_config:
                print("No environment variables found")
                sys.exit(1)
            
            config.save_config(env_config, args.password)
            print("Configuration initialized from environment variables")
            
            # Show what was saved (without sensitive data)
            for key in env_config:
                if key in ('password', 'pin'):
                    print(f"  {key}: ***")
                else:
                    print(f"  {key}: {env_config[key]}")
        else:
            print("Creating new configuration...")
            new_config = {}
            
            # Prompt for required values
            new_config['username'] = input("Username: ")
            new_config['password'] = getpass("Password: ")
            new_config['pin'] = getpass("PIN: ")
            new_config['region'] = int(input("Region code: "))
            new_config['brand'] = int(input("Brand code (1=Kia, 2=Hyundai, 3=Genesis): "))
            new_config['vehicle_id'] = input("Vehicle ID: ")
            
            # Optional values with defaults
            new_config['auto_update'] = input("Enable auto update? (True/False) [False]: ").lower() == 'true'
            new_config['api_limit'] = int(input("API rate limit per day [30]: ") or "30")
            new_config['port'] = int(input("Web server port [8001]: ") or "8001")
            new_config['host'] = input("Web server host [0.0.0.0]: ") or "0.0.0.0"
            new_config['csv_file'] = input("CSV file path [./vehicle_data.csv]: ") or "./vehicle_data.csv"
            
            config.save_config(new_config, args.password)
    
    elif args.command == 'show':
        loaded = config.load_config(args.password)
        if loaded:
            print("\nCurrent configuration:")
            for key, value in loaded.items():
                if key in ('password', 'pin'):
                    print(f"  {key}: ***")
                else:
                    print(f"  {key}: {value}")
        else:
            print("No configuration found or invalid password")
    
    elif args.command == 'set':
        if not args.key or args.value is None:
            print("Both --key and --value are required")
            sys.exit(1)
        
        config.load_config(args.password)
        
        # Handle type conversion
        if args.key in ('api_limit', 'port', 'region', 'brand'):
            value = int(args.value)
        elif args.key == 'auto_update':
            value = args.value.lower() == 'true'
        else:
            value = args.value
        
        config.set(args.key, value)
        config.save_config(config.get_all(), args.password)
        print(f"Set {args.key} = {value if args.key not in ('password', 'pin') else '***'}")
    
    elif args.command == 'get':
        if not args.key:
            print("--key is required")
            sys.exit(1)
        
        config.load_config(args.password)
        value = config.get(args.key)
        if value is not None:
            if args.key in ('password', 'pin'):
                print("***")
            else:
                print(value)
        else:
            print(f"Key '{args.key}' not found")
    
    elif args.command == 'delete':
        if not args.key:
            print("--key is required")
            sys.exit(1)
        
        config.load_config(args.password)
        config.delete(args.key)
        config.save_config(config.get_all(), args.password)
        print(f"Deleted {args.key}")
    
    elif args.command == 'export':
        config.load_config(args.password)
        env_vars = config.export_to_env()
        
        print("\n# Export these environment variables:")
        for var, value in env_vars.items():
            if var in ('BLUELINKPASS', 'BLUELINKPIN'):
                print(f"export {var}='***'")
            else:
                print(f"export {var}='{value}'")