"""
HDL AC Control - Core Protocol Implementation
==============================================
Learns protocol structure from templates and dynamically builds packets
for any device address with correct CRC calculation.

Uses exact HDL Pascal CRC-16 CCITT algorithm (polynomial 0x1021).
"""

import json
import binascii
import logging
from typing import Tuple, Dict, List
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

# ============================================================================
# HVAC Mode Constants
# ============================================================================

HVAC_MODE_COOL = 0x00
HVAC_MODE_FAN = 0x02
HVAC_MODE_DRY = 0x04

# ============================================================================
# CRC-16 CCITT HDL Pascal Implementation
# ============================================================================

def generate_crc_table() -> List[int]:
    """Generate 256-entry CRC table for HDL CRC-16 CCITT"""
    table = []
    for i in range(256):
        crc = i << 8  # Shift byte to high position
        for _ in range(8):
            if crc & 0x8000:  # Check high bit
                crc = (crc << 1) ^ 0x1021
            else:
                crc = crc << 1
            crc &= 0xFFFF
        table.append(crc)
    return table


CRC_TABLE = generate_crc_table()


def compute_hdl_crc(data_with_length: bytes) -> Tuple[int, int, int]:
    """
    Compute HDL CRC-16 CCITT including length byte, excluding the 2 CRC bytes at end.
    Matches Pascal hdlPackCRC routine.
    
    Args:
        data_with_length: [Length byte] + [Data bytes] where last 2 bytes are CRC positions
        
    Returns:
        (crc_hi, crc_lo, crc_16bit)
    """
    crc = 0x0000
    # Process everything except the last 2 CRC bytes
    data_len = len(data_with_length) - 2
    
    for i in range(data_len):
        byte = data_with_length[i]
        idx = ((crc >> 8) ^ byte) & 0xFF
        crc = ((crc << 8) & 0xFFFF) ^ CRC_TABLE[idx]
        crc &= 0xFFFF
    
    crc_hi = (crc >> 8) & 0xFF
    crc_lo = crc & 0xFF
    
    return crc_hi, crc_lo, crc


def append_hdl_crc(length_and_data: bytearray) -> None:
    """
    Compute and write CRC to last 2 bytes in place.
    Format: [CRCHi, CRCLo]
    
    Args:
        length_and_data: [Length byte] + [Data bytes] with 2 trailing CRC positions
    """
    crc_hi, crc_lo, _ = compute_hdl_crc(length_and_data)
    length_and_data[-2] = crc_hi
    length_and_data[-1] = crc_lo


# ============================================================================
# Frame Parsing & Validation
# ============================================================================

def split_packet(hex_string: str) -> Tuple[bytes, bytes]:
    """
    Split packet into prefix and frame.
    
    Returns:
        (prefix, frame) where frame starts at 0xAA 0xAA
    """
    hex_clean = ''.join(c for c in hex_string.lower() if c in '0123456789abcdef')
    packet_bytes = binascii.unhexlify(hex_clean)
    
    # Find AA AA marker
    aa_pos = -1
    for i in range(len(packet_bytes) - 1):
        if packet_bytes[i] == 0xAA and packet_bytes[i + 1] == 0xAA:
            aa_pos = i
            break
    
    if aa_pos < 0:
        raise ValueError("0xAA 0xAA marker not found in packet")
    
    prefix = packet_bytes[:aa_pos]
    frame = packet_bytes[aa_pos:]
    
    return prefix, frame


def validate_frame(frame: bytes, name: str) -> None:
    """
    Validate frame structure and CRC.
    Frame format: AA AA [Length] [Data area including 2 CRC bytes]
    
    Note: Length byte includes itself in the count!
    So if Length = 21, there are 20 more bytes after it.
    
    Raises ValueError if validation fails.
    """
    if len(frame) < 4:
        raise ValueError(f"{name}: Frame too short (< 4 bytes)")
    
    if frame[0] != 0xAA or frame[1] != 0xAA:
        raise ValueError(f"{name}: Frame does not start with 0xAA 0xAA")
    
    length = frame[2]
    
    # Length includes itself, so data area = length - 1
    expected_data_len = length - 1
    actual_data_len = len(frame) - 3
    
    if actual_data_len != expected_data_len:
        raise ValueError(
            f"{name}: Frame length mismatch. "
            f"Length byte = {length} (expects {expected_data_len} data bytes), "
            f"actual data = {actual_data_len} bytes"
        )
    
    # Extract length byte + data area for CRC calculation
    length_and_data = frame[2:]  # From length byte onwards
    
    if len(length_and_data) < 3:  # Need at least length + 2 CRC bytes
        raise ValueError(f"{name}: Data too short for CRC")
    
    # Extract stored CRC (last 2 bytes)
    stored_crc_hi = length_and_data[-2]
    stored_crc_lo = length_and_data[-1]
    
    # Compute CRC (includes length byte, excludes CRC bytes)
    computed_hi, computed_lo, _ = compute_hdl_crc(length_and_data)
    
    if stored_crc_hi != computed_hi or stored_crc_lo != computed_lo:
        raise ValueError(
            f"{name}: CRC MISMATCH!\n"
            f"  Stored:   {stored_crc_hi:02X} {stored_crc_lo:02X}\n"
            f"  Computed: {computed_hi:02X} {computed_lo:02X}\n"
            f"  Frame: {binascii.hexlify(frame).decode()}"
        )


# ============================================================================
# Protocol Field Discovery
# ============================================================================

def discover_protocol(templates: Dict[str, str], silent: bool = False) -> Dict:
    """
    Auto-discover protocol field positions by comparing templates.
    
    Args:
        templates: Dictionary with 'off', 'on', 'on_1.14' hex strings, 
                   and optionally temperature/mode templates
        silent: If True, suppress logging output
    
    Returns dict with:
        - address_positions: byte indices that change between devices
        - opcode_positions: byte indices that change between on/off
        - temperature_position: byte index for temperature setting
        - mode_position: byte index for HVAC mode
        - base_off_frame: off template frame
        - base_on_frame: on template frame
        - base_cool_frame: cool mode template frame (if available)
        - prefix: packet prefix (before AA AA)
    """
    if not silent:
        _LOGGER.info("Starting protocol auto-discovery")
    
    # Load and validate required frames
    if 'off' not in templates:
        raise ValueError("Missing 'off' template for 1.13")
    if 'on' not in templates:
        raise ValueError("Missing 'on' template for 1.13")
    if 'on_1.14' not in templates:
        raise ValueError("Missing 'on_1.14' template")
    
    if not silent:
        _LOGGER.debug("Validating frames and CRC...")
    
    prefix_off, frame_off = split_packet(templates['off'])
    validate_frame(frame_off, "off")
    
    prefix_on, frame_on = split_packet(templates['on'])
    validate_frame(frame_on, "on")
    
    prefix_on_14, frame_on_14 = split_packet(templates['on_1.14'])
    validate_frame(frame_on_14, "on_1.14")
    
    if not silent:
        _LOGGER.debug("All frames validated successfully")
    
    # Extract data areas (skip AA AA and length byte)
    # Exclude last 2 CRC bytes from comparison
    data_off = frame_off[3:-2]
    data_on = frame_on[3:-2]
    data_on_14 = frame_on_14[3:-2]
    
    # Compare off vs on (same device 1.13) to find ON/OFF control byte
    opcode_positions = []
    for i in range(min(len(data_off), len(data_on))):
        if data_off[i] != data_on[i]:
            opcode_positions.append(i)
    
    if not opcode_positions:
        raise ValueError("Could not discover opcode positions (off vs on identical)")
    
    if not silent:
        _LOGGER.debug(f"Discovered opcode positions: {opcode_positions}")
    
    # Compare on_1.13 vs on_1.14 (same opcode, different device)
    address_positions = []
    for i in range(min(len(data_on), len(data_on_14))):
        if data_on[i] != data_on_14[i]:
            address_positions.append(i)
    
    if not address_positions:
        raise ValueError("Could not discover address positions (on vs on_1.14 identical)")
    
    if not silent:
        _LOGGER.debug(f"Discovered address positions: {address_positions}")
    
    # Discover temperature and mode positions (if templates available)
    temperature_position = None
    mode_position = None
    base_cool_frame = None
    
    if 'cool_23c' in templates and 'fan_24c' in templates:
        if not silent:
            _LOGGER.debug("Discovering temperature and mode positions...")
        
        _, frame_cool_23 = split_packet(templates['cool_23c'])
        validate_frame(frame_cool_23, "cool_23c")
        
        _, frame_fan_24 = split_packet(templates['fan_24c'])
        validate_frame(frame_fan_24, "fan_24c")
        
        data_cool_23 = frame_cool_23[3:-2]
        data_fan_24 = frame_fan_24[3:-2]
        
        # Compare cool_23c vs fan_24c to find mode byte
        # (temperature changes from 0x17 to 0x18 AND mode changes from 0x00 to 0x02)
        differences = []
        for i in range(min(len(data_cool_23), len(data_fan_24))):
            if data_cool_23[i] != data_fan_24[i]:
                differences.append((i, data_cool_23[i], data_fan_24[i]))
        
        # Temperature byte: 0x17 (23°C) vs 0x18 (24°C) - difference of 1
        # Mode byte: 0x00 (cool) vs 0x02 (fan) - difference of 2
        for i, val_23, val_24 in differences:
            if val_24 - val_23 == 1:
                temperature_position = i
            elif val_24 - val_23 == 2:
                mode_position = i
        
        base_cool_frame = frame_cool_23
        
        if not silent:
            _LOGGER.debug(f"Discovered temperature position: {temperature_position}")
            _LOGGER.debug(f"Discovered mode position: {mode_position}")
    
    if not silent:
        _LOGGER.info("Protocol discovery complete")
    
    return {
        'address_positions': address_positions,
        'opcode_positions': opcode_positions,
        'temperature_position': temperature_position,
        'mode_position': mode_position,
        'base_off_frame': frame_off,
        'base_on_frame': frame_on,
        'base_cool_frame': base_cool_frame,
        'prefix': prefix_off,
    }


# ============================================================================
# Packet Builder
# ============================================================================

def build_packet(verb: str, subnet: int, device: int, schema: Dict, 
                 temperature: int = None, hvac_mode: int = None) -> bytes:
    """
    Build a packet for given verb, device address, temperature, and HVAC mode.
    
    Args:
        verb: "on" or "off"
        subnet: subnet number (e.g., 1)
        device: device number (e.g., 13)
        schema: protocol schema from discover_protocol()
        temperature: target temperature in Celsius (16-30), optional
        hvac_mode: HVAC mode (HVAC_MODE_COOL/FAN/DRY), optional
        
    Returns:
        Complete frame bytes (starting with AA AA)
    """
    # Choose base template
    if verb.lower() == "off":
        base_frame = schema['base_off_frame']
    elif verb.lower() == "on":
        # Use cool frame if available and temperature/mode are being set
        if schema.get('base_cool_frame') and (temperature is not None or hvac_mode is not None):
            base_frame = schema['base_cool_frame']
        else:
            base_frame = schema['base_on_frame']
    else:
        raise ValueError(f"Unknown verb: {verb}. Use 'on' or 'off'")
    
    # Copy to mutable buffer
    frame = bytearray(base_frame)
    
    # Data area starts at position 3 (after AA AA and length byte)
    data_area_offset = 3
    
    # HDL protocol structure: positions 6-7 are typically [subnet, device]
    address_positions = schema['address_positions']
    
    # Standard HDL BusPro: byte 6 = subnet, byte 7 = device
    frame[data_area_offset + 6] = subnet
    frame[data_area_offset + 7] = device
    
    # Handle other discovered address positions
    for pos in address_positions:
        if pos == 6:
            frame[data_area_offset + pos] = subnet
        elif pos == 7:
            frame[data_area_offset + pos] = device
    
    # Set temperature if provided and position is known
    if temperature is not None and schema.get('temperature_position') is not None:
        temp_pos = schema['temperature_position']
        # Temperature is direct hex encoding: 18°C = 0x12, 30°C = 0x1E
        if 18 <= temperature <= 30:
            frame[data_area_offset + temp_pos] = temperature
        else:
            _LOGGER.warning(f"Temperature {temperature}°C out of range (18-30), using as-is")
            frame[data_area_offset + temp_pos] = temperature
    
    # Set HVAC mode if provided and position is known
    if hvac_mode is not None and schema.get('mode_position') is not None:
        mode_pos = schema['mode_position']
        frame[data_area_offset + mode_pos] = hvac_mode
    
    # Extract data area
    data_area = frame[data_area_offset:]
    
    # Update length byte (length includes itself!)
    frame[2] = len(data_area) + 1
    
    # Recompute and append CRC (includes length byte)
    length_and_data = bytearray(frame[2:])
    append_hdl_crc(length_and_data)
    
    # Write back length byte + data area with new CRC
    frame[2:] = length_and_data
    
    return bytes(frame)


# ============================================================================
# Template Loading
# ============================================================================

def parse_status_packet(packet: bytes, schema: Dict) -> Dict:
    """
    Parse incoming status packet from HDL gateway broadcast.
    
    Args:
        packet: Complete packet bytes (may include prefix + frame)
        schema: protocol schema from discover_protocol()
        
    Returns:
        Dictionary with: {
            'subnet': int,
            'device_id': int,
            'is_on': bool,
            'temperature': int or None,
            'hvac_mode': int or None (HVAC_MODE_COOL/FAN/DRY)
        }
        Returns None if packet is not a valid AC status/command packet
    """
    import binascii
    
    try:
        _LOGGER.debug(f"📦 Parsing packet: {len(packet)} bytes - {binascii.hexlify(packet).decode()}")
        
        # Find AA AA marker to extract frame
        aa_pos = -1
        for i in range(len(packet) - 1):
            if packet[i] == 0xAA and packet[i + 1] == 0xAA:
                aa_pos = i
                break
        
        if aa_pos < 0:
            _LOGGER.warning(f"❌ No AA AA marker found in packet")
            return None
        
        _LOGGER.debug(f"✓ Found AA AA at position {aa_pos}")
        frame = packet[aa_pos:]
        
        # Basic validation
        if len(frame) < 10:  # Minimum reasonable frame size
            _LOGGER.warning(f"❌ Frame too short: {len(frame)} bytes (need at least 10)")
            return None
        
        if frame[0] != 0xAA or frame[1] != 0xAA:
            _LOGGER.warning(f"❌ Frame doesn't start with AA AA")
            return None
        
        length = frame[2]
        _LOGGER.debug(f"✓ Length byte: {length}")
        
        # Validate length
        expected_data_len = length - 1
        actual_data_len = len(frame) - 3
        
        _LOGGER.debug(f"✓ Expected data: {expected_data_len} bytes, Actual: {actual_data_len} bytes")
        
        if actual_data_len != expected_data_len:
            _LOGGER.warning(f"❌ Length mismatch: expected {expected_data_len}, got {actual_data_len}")
            return None
        
        # Extract data area (skip AA AA and length byte)
        data_area = frame[3:-2]  # Exclude CRC bytes at end
        
        _LOGGER.debug(f"✓ Extracted data area: {len(data_area)} bytes")
        
        # Extract subnet and device_id
        # In STATUS broadcasts: positions 0-1 are target device (subnet, device)
        # In COMMAND packets: positions 6-7 are target device
        # We check positions 0-1 first (status broadcasts)
        if len(data_area) < 2:
            _LOGGER.warning(f"❌ Data area too short: {len(data_area)} bytes (need at least 2)")
            return None
        
        # Try positions 0-1 first (status broadcast format)
        subnet = data_area[0]
        device_id = data_area[1]
        
        _LOGGER.debug(f"✓ Extracted device address: {subnet}.{device_id}")
        
        # For status broadcasts, the structure is different than commands
        # We need to scan for likely temperature and mode values
        
        # Look for temperature (typically 16-35°C range)
        temperature = None
        try:
            for i in range(len(data_area)):
                if 16 <= data_area[i] <= 35:
                    # Found a potential temperature
                    # In your capture, position 10 had 0x19 (25°C)
                    if i >= 8:  # Temperature usually in later positions
                        temperature = data_area[i]
                        _LOGGER.debug(f"✓ Found temperature {temperature}°C at position {i}")
                        break
        except Exception as e:
            _LOGGER.warning(f"⚠️ Error scanning for temperature: {e}")
        
        # Look for HVAC mode (0x00=COOL, 0x02=FAN, 0x04=DRY)
        hvac_mode = None
        try:
            for i in range(len(data_area)):
                if data_area[i] in [HVAC_MODE_COOL, HVAC_MODE_FAN, HVAC_MODE_DRY]:
                    # Found a potential mode byte
                    if i >= 8:  # Mode usually in later positions
                        hvac_mode = data_area[i]
                        _LOGGER.debug(f"✓ Found mode 0x{hvac_mode:02x} at position {i}")
                        break
        except Exception as e:
            _LOGGER.warning(f"⚠️ Error scanning for mode: {e}")
        
        # Try to determine ON/OFF state
        is_on = None
        try:
            # Check various positions for on/off indicator
            if len(data_area) > 8:
                # Status broadcasts often have operation byte around position 8-9
                if data_area[8] in [0x0a, 0x01]:  # 0x0a might indicate ON
                    is_on = True
                    _LOGGER.debug(f"✓ Detected ON state (byte at pos 8 = 0x{data_area[8]:02x})")
                elif data_area[8] == 0x00:
                    is_on = False
                    _LOGGER.debug(f"✓ Detected OFF state (byte at pos 8 = 0x{data_area[8]:02x})")
        except Exception as e:
            _LOGGER.warning(f"⚠️ Error detecting on/off state: {e}")
        
        mode_str = f"0x{hvac_mode:02x}" if hvac_mode is not None else "None"
        _LOGGER.debug(
            f"Parsed status: subnet={subnet}, device={device_id}, "
            f"on={is_on}, temp={temperature}, mode={mode_str}"
        )
        _LOGGER.debug(f"  Raw data area (first 20 bytes): {' '.join(f'{b:02x}' for b in data_area[:20])}")
        
        return {
            'subnet': subnet,
            'device_id': device_id,
            'is_on': is_on,
            'temperature': temperature,
            'hvac_mode': hvac_mode,
        }
        
    except Exception as e:
        _LOGGER.debug(f"Failed to parse status packet: {e}")
        return None


def load_templates(templates_path: str) -> Dict[str, str]:
    """
    Load templates from JSON file.
    
    Args:
        templates_path: Path to templates.json file
        
    Returns:
        Dictionary of templates
    """
    try:
        with open(templates_path, 'r') as f:
            templates = json.load(f)
        _LOGGER.debug(f"Loaded templates from {templates_path}")
        return templates
    except FileNotFoundError:
        raise ValueError(f"Templates file not found: {templates_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in templates file: {e}")

