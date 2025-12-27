"""Configuration loader for business-specific settings."""
import yaml
import os
from typing import Dict, Any, Optional
from pathlib import Path


class ConfigLoader:
    """Loads and validates business configuration from YAML file."""
    
    def __init__(self, config_path: str):
        """Initialize config loader.
        
        Args:
            config_path: Path to YAML configuration file
        """
        self.config_path = Path(config_path)
        self.config: Optional[Dict[str, Any]] = None
        self.load()
    
    def load(self) -> Dict[str, Any]:
        """Load configuration from file."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        
        with open(self.config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        
        self._validate()
        return self.config
    
    def _validate(self):
        """Validate required configuration sections."""
        required_sections = ['business', 'services', 'staff', 'hours']
        for section in required_sections:
            if section not in self.config:
                raise ValueError(f"Missing required config section: {section}")
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by dot-notation key (e.g., 'business.name')."""
        keys = key.split('.')
        value = self.config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value
    
    def get_business_name(self) -> str:
        """Get business name."""
        return self.get('business.name', 'Business')
    
    def get_business_type(self) -> str:
        """Get business type."""
        return self.get('business.type', 'business')
    
    def get_services(self) -> list:
        """Get list of services."""
        return self.get('services', [])
    
    def get_staff(self) -> list:
        """Get list of staff members."""
        return self.get('staff', [])
    
    def get_hours(self) -> dict:
        """Get business hours."""
        return self.get('hours', {})
    
    def get_personality(self) -> dict:
        """Get personality/tone settings."""
        return self.get('personality', {})
    
    def get_booking_rules(self) -> dict:
        """Get booking rules."""
        return self.get('booking', {})

