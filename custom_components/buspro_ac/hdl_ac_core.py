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
# Fan Speed Constants
# ============================================================================

FAN_SPEED_AUTO = 0x00
FAN_SPEED_HIGH = 0x01
FAN_SPEED_MEDIUM = 0x02
FAN_SPEED_LOW = 0x03

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
        - fan_speed_position: byte index for fan speed (position 15)
        - base_off_frame: off template frame
        - base_on_frame: on template frame
        - base_cool_frame: cool mode template frame (if available)
        - base_status_request_frame: status request template frame (if available)
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
    
    # Load status request template if available
    base_status_request_frame = None
    if 'status_request' in templates:
        try:
            _, frame_status_req = split_packet(templates['status_request'])
            validate_frame(frame_status_req, "status_request")
            base_status_request_frame = frame_status_req
            if not silent:
                _LOGGER.debug("Loaded status_request template")
        except Exception as e:
            if not silent:
                _LOGGER.warning(f"Failed to load status_request template: {e}")
    
    if not silent:
        _LOGGER.info("Protocol discovery complete")
    
    return {
        'address_positions': address_positions,
        'opcode_positions': opcode_positions,
        'temperature_position': temperature_position,
        'mode_position': mode_position,
        'fan_speed_position': 15,  # Known position from packet analysis
        'base_off_frame': frame_off,
        'base_on_frame': frame_on,
        'base_cool_frame': base_cool_frame,
        'base_status_request_frame': base_status_request_frame,
        'prefix': prefix_off,
    }


# ============================================================================
# Packet Builder
# ============================================================================

def build_status_request(subnet: int, device: int, schema: Dict) -> bytes:
    """
    Build a status request packet for given device address.
    
    Args:
        subnet: subnet number (e.g., 1)
        device: device number (e.g., 13)
        schema: protocol schema from discover_protocol()
        
    Returns:
        Complete frame bytes (starting with AA AA)
    """
    if schema.get('base_status_request_frame') is None:
        raise ValueError("Status request template not available in schema")
    
    # Copy base template
    frame = bytearray(schema['base_status_request_frame'])
    
    # Data area starts at position 3 (after AA AA and length byte)
    data_area_offset = 3
    
    # Update device address at positions 6-7
    frame[data_area_offset + 6] = subnet
    frame[data_area_offset + 7] = device
    
    # Extract data area for CRC calculation
    data_area = frame[data_area_offset:]
    
    # Update length byte (length includes itself!)
    frame[2] = len(data_area) + 1
    
    # Recompute and append CRC (includes length byte)
    length_and_data = bytearray(frame[2:])
    append_hdl_crc(length_and_data)
    
    # Write back length byte + data area with new CRC
    frame[2:] = length_and_data
    
    return bytes(frame)


def build_packet(verb: str, subnet: int, device: int, schema: Dict, 
                 temperature: int = None, hvac_mode: int = None, fan_speed: int = None) -> bytes:
    """
    Build a packet for given verb, device address, temperature, HVAC mode, and fan speed.
    
    Args:
        verb: "on" or "off"
        subnet: subnet number (e.g., 1)
        device: device number (e.g., 13)
        schema: protocol schema from discover_protocol()
        temperature: target temperature in Celsius (16-30), optional
        hvac_mode: HVAC mode (HVAC_MODE_COOL/FAN/DRY), optional
        fan_speed: fan speed (FAN_SPEED_AUTO/HIGH/MEDIUM/LOW), optional
        
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
    
    # Set fan speed if provided and position is known
    if fan_speed is not None and schema.get('fan_speed_position') is not None:
        fan_pos = schema['fan_speed_position']
        frame[data_area_offset + fan_pos] = fan_speed
    
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
    
    This parser uses FIXED byte positions discovered from real packet analysis.
    Processes Type 0x18 (temperature/mode), 0x19 (extended status), and Type 0x1A (fan speed) broadcasts.
    
    Type 0x18 Packet Structure (after AA AA 18):
    Position 0-1:  Device address (subnet, device_id)
    Position 10:   Current room temperature from sensor (0x1A=26°C actual reading)
    Position 11:   Target setpoint temperature (0x15=21°C, 0x18=24°C, etc.)
    Position 15:   ON/OFF indicator (0x20=OFF, 0x01=ON COOL, 0x21=ON FAN)
    Position 17:   HVAC mode (0x00=COOL, 0x02=FAN, 0x04=DRY)
    
    Type 0x19 Packet Structure (after AA AA 19):
    Position 0-1:  Device address (subnet, device_id)
    Position 9:    ON/OFF indicator (0x00=OFF, 0x01=ON) - NOTE: Different from 0x18/0x1A!
    Position 10:   Target setpoint temperature (NOTE: Different from 0x18/0x1A!)
    Position 16:   Fan speed (0x00=AUTO, 0x01=HIGH, 0x02=MEDIUM, 0x03=LOW)
    Position 17:   HVAC mode (0x00=COOL, 0x02=FAN, 0x04=DRY)
    
    Type 0x1A Packet Structure (after AA AA 1A):
    Position 0-1:  Device address (subnet, device_id)
    Position 10:   Current room temperature from sensor
    Position 11:   Target setpoint temperature
    Position 15:   ON/OFF indicator
    Position 16:   Fan speed (0x00=AUTO, 0x01=HIGH, 0x02=MEDIUM, 0x03=LOW)
    Position 17:   HVAC mode (0x00=COOL, 0x02=FAN, 0x04=DRY)
    
    Args:
        packet: Complete packet bytes (may include prefix + frame)
        schema: protocol schema from discover_protocol()
        
    Returns:
        Dictionary with: {
            'subnet': int,
            'device_id': int,
            'is_on': bool,
            'temperature': int or None (target setpoint temperature),
            'current_temperature': int or None (actual room sensor reading),
            'hvac_mode': int or None (HVAC_MODE_COOL/FAN/DRY),
            'fan_speed': int or None (FAN_SPEED_AUTO/HIGH/MEDIUM/LOW)
        }
        Returns None if packet is not a valid Type 0x18, 0x19, or 0x1A status packet
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
            _LOGGER.debug(f"No AA AA marker found, skipping packet")
            return None
        
        frame = packet[aa_pos:]
        
        # Validate frame basics
        if len(frame) < 10:
            _LOGGER.debug(f"Frame too short: {len(frame)} bytes")
            return None
        
        if frame[0] != 0xAA or frame[1] != 0xAA:
            _LOGGER.debug(f"Frame doesn't start with AA AA")
            return None
        
        length = frame[2]
        
        # ⭐ Process Type 0x18 (temperature/mode), 0x19 (extended status), and Type 0x1A (fan speed) broadcasts
        if length not in [0x18, 0x19, 0x1A]:
            _LOGGER.debug(f"Ignoring non-0x18/0x19/0x1A packet (length={length:#04x})")
            return None
        
        # Validate frame length matches
        expected_data_len = length - 1
        actual_data_len = len(frame) - 3
        
        if actual_data_len != expected_data_len:
            _LOGGER.debug(f"Length mismatch: expected {expected_data_len}, got {actual_data_len}")
            return None
        
        # Extract data area (skip AA AA and length byte, exclude 2 CRC bytes at end)
        data_area = frame[3:-2]
        
        # Validate data area has enough bytes for fixed positions
        min_bytes_needed = 18 if length == 0x18 else 18  # Both need at least 18 bytes
        if len(data_area) < min_bytes_needed:
            _LOGGER.debug(f"Data area too short: {len(data_area)} bytes (need at least {min_bytes_needed})")
            return None
        
        # ═══════════════════════════════════════════════════════════════
        # FIXED POSITION PARSING - No scanning, no guessing!
        # ═══════════════════════════════════════════════════════════════
        
        # Position 0-1: Device address
        subnet = data_area[0]
        device_id = data_area[1]
        
        # Temperature position varies by packet type:
        # - Type 0x18: position 11
        # - Type 0x19: position 10
        # - Type 0x1A: position 11
        if length == 0x19:
            temp_byte = data_area[10] if len(data_area) > 10 else None
        else:
            temp_byte = data_area[11] if len(data_area) > 11 else None
        
        # Validate temperature range (16-35°C typical for AC setpoints)
        if temp_byte is not None and 16 <= temp_byte <= 35:
            temperature = temp_byte
        else:
            temperature = None  # Invalid range or not applicable
        
        # Current room temperature (sensor reading) - typically at position 10
        # This is the actual measured temperature, not the setpoint
        current_temp_byte = data_area[10] if len(data_area) > 10 else None
        
        # Exploratory logging to help identify current temp position
        if len(data_area) > 13:
            _LOGGER.debug(
                f"  🔍 Temp exploration: Pos10=0x{data_area[10]:02x}({data_area[10]}°C), "
                f"Pos11=0x{data_area[11]:02x}({data_area[11]}°C), "
                f"Pos12=0x{data_area[12]:02x}({data_area[12]}°C), "
                f"Pos13=0x{data_area[13]:02x}({data_area[13]}°C)"
            )
        
        # Validate current temperature range (10-50°C wider range for actual readings)
        if current_temp_byte is not None and 10 <= current_temp_byte <= 50:
            current_temperature = current_temp_byte
        else:
            current_temperature = None  # Invalid range or not applicable
        
        # ON/OFF position varies by packet type:
        # - Type 0x18: position 15 (0x20=OFF, 0x01=ON COOL, 0x21=ON FAN)
        # - Type 0x19: position 9 (0x00=OFF, 0x01=ON)
        # - Type 0x1A: position 15 (0x20=OFF, 0x01=ON COOL, 0x21=ON FAN)
        if length == 0x19:
            on_off_byte = data_area[9] if len(data_area) > 9 else 0x00
            is_on = (on_off_byte == 0x01)
        else:
            on_off_byte = data_area[15]
            is_on = (on_off_byte != 0x20)
        
        # Position 17: HVAC mode
        # 0x00 = COOL mode
        # 0x02 = FAN mode
        # 0x04 = DRY mode
        mode_byte = data_area[17]
        
        # Map to standard HVAC mode constants
        if mode_byte == 0x00:
            hvac_mode = HVAC_MODE_COOL
        elif mode_byte == 0x02:
            hvac_mode = HVAC_MODE_FAN
        elif mode_byte == 0x04:
            hvac_mode = HVAC_MODE_DRY
        else:
            hvac_mode = None
        
        # Position 16: Fan speed (in 0x19 and 0x1A packets)
        # 0x00 = AUTO
        # 0x01 = HIGH
        # 0x02 = MEDIUM
        # 0x03 = LOW
        fan_speed = None
        if length in [0x19, 0x1A] and len(data_area) > 16:
            fan_speed_byte = data_area[16]
            if fan_speed_byte in [0x00, 0x01, 0x02, 0x03]:
                fan_speed = fan_speed_byte
        
        # ═══════════════════════════════════════════════════════════════
        # END FIXED POSITION PARSING
        # ═══════════════════════════════════════════════════════════════
        
        mode_str = f"0x{hvac_mode:02x}" if hvac_mode is not None else "None"
        fan_str = f"0x{fan_speed:02x}" if fan_speed is not None else "None"
        packet_type = f"0x{length:02x}"
        
        _LOGGER.debug(
            f"✓ Parsed Type {packet_type} packet: {subnet}.{device_id} | "
            f"ON={is_on} | Current={current_temperature}°C | Target={temperature}°C | Mode={mode_str} | Fan={fan_str}"
        )
        _LOGGER.debug(f"  Position 10 (Current): 0x{current_temp_byte:02x}, Position 11 (Target): 0x{temp_byte:02x}, Position 15 (ON/OFF): 0x{on_off_byte:02x}, Position 17 (Mode): 0x{mode_byte:02x}")
        if fan_speed is not None:
            _LOGGER.debug(f"  Position 16 (Fan): 0x{fan_speed:02x}")
        
        return {
            'subnet': subnet,
            'device_id': device_id,
            'is_on': is_on,
            'temperature': temperature,
            'current_temperature': current_temperature,
            'hvac_mode': hvac_mode,
            'fan_speed': fan_speed,
        }
        
    except Exception as e:
        _LOGGER.debug(f"Failed to parse status packet: {e}", exc_info=True)
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

