"""Climate platform for HDL AC Control."""

import logging
import voluptuous as vol

from homeassistant.components.climate import ClimateEntity, PLATFORM_SCHEMA
from homeassistant.components.climate.const import (
    HVACMode,
    ClimateEntityFeature,
)
from homeassistant.const import CONF_NAME, UnitOfTemperature, ATTR_TEMPERATURE
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN, CONF_DEVICES, CONF_ADDRESS
from .hdl_ac_core import build_packet, HVAC_MODE_COOL, HVAC_MODE_FAN, HVAC_MODE_DRY

_LOGGER = logging.getLogger(__name__)

# Climate platform schema
DEVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ADDRESS): cv.string,
        vol.Required(CONF_NAME): cv.string,
    }
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_DEVICES): vol.All(cv.ensure_list, [DEVICE_SCHEMA]),
    }
)


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up HDL AC climate devices."""
    
    # Get gateway from hass.data
    if DOMAIN not in hass.data:
        _LOGGER.error("HDL AC Control integration not initialized")
        return False
    
    gateway = hass.data[DOMAIN]["gateway"]
    
    # Parse devices from config
    devices = config.get(CONF_DEVICES, [])
    
    entities = []
    for device_config in devices:
        address = device_config[CONF_ADDRESS]
        name = device_config[CONF_NAME]
        
        try:
            # Parse subnet.device
            parts = address.split(".")
            if len(parts) != 2:
                _LOGGER.error(
                    f"Invalid address format '{address}'. Use 'subnet.device' (e.g., '1.14')"
                )
                continue
            
            subnet = int(parts[0])
            device_id = int(parts[1])
            
            # Create entity
            entity = HdlAcClimate(gateway, name, subnet, device_id)
            entities.append(entity)
            _LOGGER.info(f"Added HDL AC device: {name} ({address})")
            
        except Exception as e:
            _LOGGER.error(f"Failed to add device {name} ({address}): {e}")
            continue
    
    if entities:
        add_entities(entities, True)
        return True
    
    _LOGGER.warning("No HDL AC devices configured")
    return False


class HdlAcClimate(ClimateEntity):
    """Representation of an HDL AC unit."""

    def __init__(self, gateway, name: str, subnet: int, device_id: int):
        """Initialize the climate entity."""
        self._gateway = gateway
        self._name = name
        self._subnet = subnet
        self._device_id = device_id
        self._hvac_mode = HVACMode.OFF
        self._target_temperature = 24  # Default temperature
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_min_temp = 18
        self._attr_max_temp = 30
        self._attr_target_temperature_step = 1
        
        # Track last status update and command to prevent feedback loops
        import time
        self._last_status_update = 0
        self._last_command_sent = 0
        self._last_state_change = 0  # Track when state actually changed
        # For revert protection: track BOTH old and new values
        self._old_temp = None  # Previous temp before change
        self._new_temp = None  # New temp after change
        self._old_mode = None  # Previous mode before change
        self._new_mode = None  # New mode after change
        self._old_is_on = None  # Previous on/off before change
        self._new_is_on = None  # New on/off after change
        self._confirmation_count = 0  # Count confirmations of new state
        
        # Register callback for status updates
        self._gateway.register_callback(subnet, device_id, self._handle_status_update)
        
        _LOGGER.info(f"Registered HDL AC: {name} (subnet={subnet}, device={device_id})")

    @property
    def name(self):
        """Return the name of the climate device."""
        return self._name

    @property
    def unique_id(self):
        """Return a unique ID."""
        return f"hdl_ac_{self._subnet}_{self._device_id}"

    @property
    def hvac_mode(self):
        """Return current HVAC mode."""
        return self._hvac_mode

    @property
    def hvac_modes(self):
        """Return the list of available HVAC modes."""
        # Only COOL and FAN modes (removed DRY/dehumidify as requested)
        return [HVACMode.OFF, HVACMode.COOL, HVACMode.FAN_ONLY]

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return (
            ClimateEntityFeature.TARGET_TEMPERATURE 
            | ClimateEntityFeature.TURN_ON 
            | ClimateEntityFeature.TURN_OFF
        )
    
    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return self._attr_temperature_unit
    
    @property
    def target_temperature(self):
        """Return the target temperature."""
        return self._target_temperature
    
    @property
    def min_temp(self):
        """Return the minimum temperature."""
        return self._attr_min_temp
    
    @property
    def max_temp(self):
        """Return the maximum temperature."""
        return self._attr_max_temp
    
    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return self._attr_target_temperature_step

    def set_hvac_mode(self, hvac_mode):
        """Set new target HVAC mode."""
        if hvac_mode == HVACMode.OFF:
            self.turn_off()
        elif hvac_mode in [HVACMode.COOL, HVACMode.FAN_ONLY]:
            self._hvac_mode = hvac_mode
            self.turn_on()
        else:
            _LOGGER.warning(f"Unsupported HVAC mode: {hvac_mode}")
    
    def set_temperature(self, **kwargs):
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        
        # Update target temperature
        self._target_temperature = int(temperature)
        
        # If AC is currently on, send command with new temperature
        if self._hvac_mode != HVACMode.OFF:
            self.turn_on()
        
        self.schedule_update_ha_state()

    def turn_on(self):
        """Turn AC on with current mode and temperature."""
        import time
        try:
            # Map Home Assistant HVAC mode to HDL mode byte
            hvac_mode_map = {
                HVACMode.COOL: HVAC_MODE_COOL,
                HVACMode.FAN_ONLY: HVAC_MODE_FAN,
                HVACMode.DRY: HVAC_MODE_DRY,
            }
            
            # Get HDL mode byte (default to COOL if not specified)
            hdl_mode = hvac_mode_map.get(self._hvac_mode, HVAC_MODE_COOL)
            
            # Build ON packet with temperature and mode
            frame = build_packet(
                "on",
                self._subnet,
                self._device_id,
                self._gateway.protocol_schema,
                temperature=self._target_temperature,
                hvac_mode=hdl_mode
            )
            
            # Track when we send commands to ignore immediate status echoes
            self._last_command_sent = time.time()
            self._last_state_change = time.time()
            self._confirmation_count = 0
            
            # Protect the values we're sending from being reverted
            self._old_temp = self._target_temperature  # Current temp
            self._new_temp = self._target_temperature  # Sending same temp
            self._old_mode = self._hvac_mode if self._hvac_mode != HVACMode.OFF else None
            self._new_mode = hdl_mode
            self._old_is_on = self._hvac_mode != HVACMode.OFF
            self._new_is_on = True  # Turning ON
            
            # Send via gateway
            success = self._gateway.send_packet(frame)
            
            if success:
                # Update state only if send was successful
                if self._hvac_mode == HVACMode.OFF:
                    self._hvac_mode = HVACMode.COOL  # Default to COOL when turning on
                self.schedule_update_ha_state()
                _LOGGER.info(
                    f"Turned ON: {self._name} (mode={self._hvac_mode}, temp={self._target_temperature}°C)"
                )
            else:
                _LOGGER.error(f"Failed to turn ON: {self._name}")
                
        except Exception as e:
            _LOGGER.error(f"Error turning ON {self._name}: {e}")

    def turn_off(self):
        """Turn AC off."""
        import time
        try:
            # Build OFF packet
            frame = build_packet(
                "off",
                self._subnet,
                self._device_id,
                self._gateway.protocol_schema
            )
            
            # Track when we send commands to ignore immediate status echoes
            self._last_command_sent = time.time()
            self._last_state_change = time.time()
            self._confirmation_count = 0
            
            # Protect OFF state from being reverted
            self._old_temp = self._target_temperature
            self._new_temp = self._target_temperature  # Temp stays same
            self._old_mode = self._hvac_mode
            self._new_mode = None  # No mode when OFF
            self._old_is_on = True
            self._new_is_on = False  # Turning OFF
            
            # Send via gateway
            success = self._gateway.send_packet(frame)
            
            if success:
                self._hvac_mode = HVACMode.OFF
                self.schedule_update_ha_state()
                _LOGGER.info(f"Turned OFF: {self._name}")
            else:
                _LOGGER.error(f"Failed to turn OFF: {self._name}")
                
        except Exception as e:
            _LOGGER.error(f"Error turning OFF {self._name}: {e}")
    
    def _handle_status_update(self, status: dict):
        """
        Handle status update from gateway broadcast.
        SMART REVERT PROTECTION: Blocks OLD values, confirms NEW values.
        
        Args:
            status: Dictionary with 'is_on', 'temperature', 'hvac_mode' keys
        """
        import time
        
        current_time = time.time()
        
        # Ignore status updates that come within 2 seconds of sending a command FROM HA
        if current_time - self._last_command_sent < 2.0:
            _LOGGER.debug(f"⏭️ Ignoring status update (within 2s of HA command) for {self._name}")
            return
        
        _LOGGER.info(f"🎯 Received status update for {self._name}: {status}")
        
        # Ignore DRY mode broadcasts since we removed it
        if status['hvac_mode'] == HVAC_MODE_DRY:
            _LOGGER.debug(f"Ignoring DRY mode broadcast for {self._name}")
            return
        
        # Determine current is_on state
        current_is_on = self._hvac_mode != HVACMode.OFF
        
        # Check for changes
        temp_changed = status['temperature'] is not None and status['temperature'] != self._target_temperature
        is_on_changed = status['is_on'] is not None and status['is_on'] != current_is_on
        mode_changed = False
        
        if status['hvac_mode'] is not None:
            if status['hvac_mode'] == HVAC_MODE_COOL:
                mode_changed = self._hvac_mode != HVACMode.COOL and self._hvac_mode != HVACMode.OFF
            elif status['hvac_mode'] == HVAC_MODE_FAN:
                mode_changed = self._hvac_mode != HVACMode.FAN_ONLY and self._hvac_mode != HVACMode.OFF
        
        is_change = temp_changed or mode_changed or is_on_changed
        
        # Protection window: 2 seconds after a change
        protection_active = (current_time - self._last_state_change) < 2.0
        
        if protection_active:
            # Check if this broadcast is CONFIRMING the new values
            is_confirming_temp = (
                self._new_temp is not None and
                status['temperature'] == self._new_temp
            )
            is_confirming_mode = (
                self._new_mode is not None and
                status['hvac_mode'] == self._new_mode
            )
            is_confirming_on = (
                self._new_is_on is not None and
                status['is_on'] == self._new_is_on
            )
            
            # Check if this broadcast is REVERTING to old values
            is_reverting_temp = (
                self._old_temp is not None and
                status['temperature'] is not None and
                status['temperature'] == self._old_temp and
                status['temperature'] != self._new_temp
            )
            is_reverting_mode = (
                self._old_mode is not None and
                status['hvac_mode'] is not None and
                status['hvac_mode'] == self._old_mode and
                status['hvac_mode'] != self._new_mode
            )
            is_reverting_on = (
                self._old_is_on is not None and
                status['is_on'] is not None and
                status['is_on'] == self._old_is_on and
                status['is_on'] != self._new_is_on
            )
            
            if is_reverting_temp or is_reverting_mode or is_reverting_on:
                _LOGGER.warning(
                    f"🚫 REJECTING revert for {self._name}: "
                    f"Trying to revert temp={self._new_temp}°C→{status['temperature']}°C, "
                    f"mode={self._new_mode}→{status['hvac_mode']}, on={self._new_is_on}→{status['is_on']}"
                )
                return
            
            # If confirming, count it
            if is_confirming_temp or is_confirming_mode or is_confirming_on:
                self._confirmation_count += 1
                _LOGGER.debug(f"✓ Confirmation #{self._confirmation_count} for {self._name}")
                
                # After 2 confirmations, clear protection
                if self._confirmation_count >= 2:
                    _LOGGER.info(f"✅ State confirmed for {self._name}, clearing protection")
                    self._old_temp = None
                    self._new_temp = None
                    self._old_mode = None
                    self._new_mode = None
                    self._old_is_on = None
                    self._new_is_on = None
                    self._confirmation_count = 0
        
        # Accept the change
        if is_change:
            _LOGGER.info(f"✅ ACCEPTING change for {self._name}")
            self._last_state_change = current_time
            self._confirmation_count = 0  # Reset confirmation counter
            
            # Store old and new values for protection
            if temp_changed:
                self._old_temp = self._target_temperature
                self._new_temp = status['temperature']
            if mode_changed:
                self._old_mode = self._hvac_mode
                self._new_mode = status['hvac_mode']
            if is_on_changed:
                self._old_is_on = current_is_on
                self._new_is_on = status['is_on']
        else:
            _LOGGER.debug(f"ℹ️ No change for {self._name}")
        
        # Update timestamp
        self._last_status_update = current_time
        
        # Apply the update
        self._apply_status_update(status)
    
    def _apply_status_update(self, status: dict):
        """Apply the status update immediately."""
        try:
            updated = False
            changes = []
            
            # Update HVAC mode first (if available, regardless of is_on state)
            if status['hvac_mode'] is not None:
                # Map HDL mode to Home Assistant mode
                if status['hvac_mode'] == HVAC_MODE_COOL:
                    new_mode = HVACMode.COOL
                elif status['hvac_mode'] == HVAC_MODE_FAN:
                    new_mode = HVACMode.FAN_ONLY
                elif status['hvac_mode'] == HVAC_MODE_DRY:
                    # Map DRY to COOL since we removed DRY mode
                    new_mode = HVACMode.COOL
                    _LOGGER.debug(f"Mapping DRY mode to COOL for {self._name}")
                else:
                    new_mode = None  # Unknown mode
                
                # Update mode if we determined one AND it's different
                if new_mode is not None:
                    # Check if AC is on based on is_on flag
                    if status['is_on'] is True:
                        # AC is ON with a specific mode
                        if self._hvac_mode != new_mode:
                            old_mode = self._hvac_mode
                            self._hvac_mode = new_mode
                            updated = True
                            changes.append(f"mode: {old_mode} → {new_mode}")
                    elif status['is_on'] is False:
                        # AC is OFF
                        if self._hvac_mode != HVACMode.OFF:
                            old_mode = self._hvac_mode
                            self._hvac_mode = HVACMode.OFF
                            updated = True
                            changes.append(f"mode: {old_mode} → OFF")
                    else:
                        # is_on is None but we have a mode - AC is probably ON
                        # Apply the mode change regardless of current state
                        if self._hvac_mode != new_mode:
                            old_mode = self._hvac_mode
                            self._hvac_mode = new_mode
                            updated = True
                            changes.append(f"mode: {old_mode} → {new_mode}")
            elif status['is_on'] is False:
                # No mode but explicitly OFF
                if self._hvac_mode != HVACMode.OFF:
                    old_mode = self._hvac_mode
                    self._hvac_mode = HVACMode.OFF
                    updated = True
                    changes.append(f"mode: {old_mode} → OFF")
            
            # Update temperature (always accept temperature changes)
            if status['temperature'] is not None:
                if self._target_temperature != status['temperature']:
                    old_temp = self._target_temperature
                    self._target_temperature = status['temperature']
                    updated = True
                    changes.append(f"temp: {old_temp}°C → {status['temperature']}°C")
            
            # If anything changed, update Home Assistant
            if updated:
                _LOGGER.info(f"✅ {self._name} updated: {', '.join(changes)}")
                self.schedule_update_ha_state()
            else:
                _LOGGER.debug(f"No changes for {self._name} (already in sync)")
                
        except Exception as e:
            _LOGGER.error(f"Error handling status update for {self._name}: {e}", exc_info=True)

