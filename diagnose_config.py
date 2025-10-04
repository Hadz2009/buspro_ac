#!/usr/bin/env python3
"""
Configuration Diagnostic Tool for HDL AC Control
================================================
This script helps diagnose why not all AC units are showing up in Home Assistant.

Usage:
    python diagnose_config.py

It will:
1. Check your configuration.yaml for common issues
2. Validate device addresses
3. Look for duplicate addresses
4. Show how many devices should be loaded
"""

import sys
import re
from pathlib import Path


def find_config_file():
    """Try to find the configuration.yaml file."""
    # Common Home Assistant config paths
    possible_paths = [
        Path.home() / ".homeassistant" / "configuration.yaml",
        Path.home() / "homeassistant" / "configuration.yaml",
        Path("/config/configuration.yaml"),  # Docker/HAOS
        Path("configuration.yaml"),  # Current directory
    ]
    
    for path in possible_paths:
        if path.exists():
            return path
    
    return None


def parse_yaml_devices(config_content):
    """Parse devices from YAML content (simple parser for this specific format)."""
    devices = []
    in_climate_section = False
    in_buspro_platform = False
    in_devices_list = False
    current_device = {}
    
    lines = config_content.split('\n')
    
    for i, line in enumerate(lines):
        # Skip comments and empty lines
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        
        # Check if we're entering climate section
        if line.startswith('climate:'):
            in_climate_section = True
            continue
        
        # Check if we found buspro_ac platform
        if in_climate_section and 'platform: buspro_ac' in line:
            in_buspro_platform = True
            continue
        
        # Check if we found devices list
        if in_buspro_platform and 'devices:' in line:
            in_devices_list = True
            continue
        
        # Parse device entries
        if in_devices_list:
            # Check if we've exited the devices list (dedent or new section)
            if line and not line.startswith(' ') and not line.startswith('\t'):
                break
            
            # Look for address
            if 'address:' in line:
                match = re.search(r'address:\s*["\']?([0-9]+\.[0-9]+)["\']?', line)
                if match:
                    current_device['address'] = match.group(1)
                    current_device['line_num'] = i + 1
            
            # Look for name
            if 'name:' in line:
                match = re.search(r'name:\s*["\']?([^"\']+)["\']?', line)
                if match:
                    current_device['name'] = match.group(1).strip()
            
            # Check if we have a complete device
            if 'address' in current_device and 'name' in current_device:
                devices.append(current_device.copy())
                current_device = {}
            
            # Check if we're starting a new device entry
            if re.match(r'\s*-\s*address:', line):
                current_device = {}
    
    return devices


def validate_address(address):
    """Validate device address format."""
    match = re.match(r'^(\d+)\.(\d+)$', address)
    if not match:
        return False, "Invalid format (should be subnet.device like '1.14')"
    
    subnet = int(match.group(1))
    device = int(match.group(2))
    
    if subnet < 1 or subnet > 254:
        return False, f"Subnet {subnet} out of range (1-254)"
    
    if device < 1 or device > 254:
        return False, f"Device {device} out of range (1-254)"
    
    return True, "Valid"


def main():
    """Main diagnostic function."""
    print("=" * 70)
    print("HDL AC Control - Configuration Diagnostic Tool")
    print("=" * 70)
    print()
    
    # Find config file
    print("🔍 Looking for configuration.yaml...")
    config_path = find_config_file()
    
    if not config_path:
        print("❌ Could not find configuration.yaml automatically.")
        print()
        print("Please provide the full path to your configuration.yaml:")
        user_path = input("> ").strip()
        config_path = Path(user_path)
        
        if not config_path.exists():
            print(f"❌ File not found: {config_path}")
            sys.exit(1)
    
    print(f"✅ Found config: {config_path}")
    print()
    
    # Read config
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config_content = f.read()
    except Exception as e:
        print(f"❌ Failed to read config: {e}")
        sys.exit(1)
    
    # Parse devices
    print("📋 Parsing AC devices from configuration...")
    devices = parse_yaml_devices(config_content)
    
    if not devices:
        print("❌ No AC devices found in configuration!")
        print()
        print("Make sure your configuration.yaml has the following format:")
        print()
        print("climate:")
        print("  - platform: buspro_ac")
        print("    devices:")
        print('      - address: "1.14"')
        print('        name: "Living Room AC"')
        print('      - address: "1.13"')
        print('        name: "Bedroom AC"')
        print()
        sys.exit(1)
    
    print(f"✅ Found {len(devices)} device(s) configured")
    print()
    
    # Display devices
    print("=" * 70)
    print("Configured Devices:")
    print("=" * 70)
    
    addresses_seen = {}
    errors = []
    
    for i, device in enumerate(devices, 1):
        address = device['address']
        name = device['name']
        line_num = device.get('line_num', '?')
        
        # Validate address
        valid, msg = validate_address(address)
        status = "✅" if valid else "❌"
        
        print(f"{i}. {status} {name}")
        print(f"   Address: {address}")
        print(f"   Line: {line_num}")
        
        if not valid:
            print(f"   ⚠️  ERROR: {msg}")
            errors.append(f"Device '{name}' (line {line_num}): {msg}")
        
        # Check for duplicates
        if address in addresses_seen:
            dup_name = addresses_seen[address]['name']
            dup_line = addresses_seen[address]['line']
            print(f"   ⚠️  DUPLICATE: Same address as '{dup_name}' (line {dup_line})")
            errors.append(f"Duplicate address {address}: '{name}' (line {line_num}) and '{dup_name}' (line {dup_line})")
        else:
            addresses_seen[address] = {'name': name, 'line': line_num}
        
        print()
    
    # Summary
    print("=" * 70)
    print("Summary:")
    print("=" * 70)
    print(f"Total devices configured: {len(devices)}")
    print(f"Unique addresses: {len(addresses_seen)}")
    print(f"Errors found: {len(errors)}")
    print()
    
    if errors:
        print("⚠️  ISSUES FOUND:")
        for error in errors:
            print(f"  • {error}")
        print()
    
    if len(devices) > 5 and len(errors) == 0:
        print("✅ Configuration looks good!")
        print()
        print("If Home Assistant is only showing 5 AC units:")
        print()
        print("1. Check Home Assistant logs:")
        print("   Settings → System → Logs")
        print("   Look for 'custom_components.buspro_ac' errors")
        print()
        print("2. Make sure you've restarted Home Assistant after changing config")
        print()
        print("3. Check Developer Tools → States")
        print("   Search for 'climate.buspro' to see all loaded entities")
        print()
        print("4. Enable debug logging in configuration.yaml:")
        print()
        print("   logger:")
        print("     default: info")
        print("     logs:")
        print("       custom_components.buspro_ac: debug")
        print()
    elif len(devices) <= 5:
        print("ℹ️  You have 5 or fewer devices configured.")
        print("   If you want to add more, edit your configuration.yaml")
        print()
    else:
        print("⚠️  Please fix the errors above and restart Home Assistant")
        print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ Aborted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

