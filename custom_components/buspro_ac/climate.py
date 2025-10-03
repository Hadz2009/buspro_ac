"""Climate platform for HDL AC Control."""

import logging
import voluptuous as vol

from homeassistant.components.climate import ClimateEntity, PLATFORM_SCHEMA
from homeassistant.components.climate.const import (
    HVACMode,
    ClimateEntityFeature,
    FanMode,
    ATTR_FAN_MODE,
)
from homeassistant.const import CONF_NAME, UnitOfTemperature, ATTR_TEMPERATURE
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN, CONF_DEVICES, CONF_ADDRESS
from .hdl_ac_core import (
    build_packet,
    build_status_request,
    HVAC_MODE_COOL, 
    HVAC_MODE_FAN, 
    HVAC_MODE_DRY,
    FAN_SPEED_AUTO,
    FAN_SPEED_HIGH,
    FAN_SPEED_MEDIUM,
    FAN_SPEED_LOW,
)

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
    import asyncio
    
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
        
        # Request initial status for all ACs after a short delay
        async def request_initial_status():
            """Request status from all ACs with delays to avoid flooding."""
            await asyncio.sleep(2)  # Wait 2 seconds for entities to fully initialize
            
            for entity in entities:
                try:
                    _LOGGER.info(f"Requesting initial status for {entity.name}")
                    frame = build_status_request(
                        entity._subnet,
                        entity._device_id,
                        gateway.protocol_schema
                    )
                    gateway.send_packet(frame)
                    await asyncio.sleep(0.1)  # 100ms delay between requests
                except Exception as e:
                    _LOGGER.error(f"Failed to request status for {entity.name}: {e}")
        
        # Schedule the status request task
        hass.async_create_task(request_initial_status())
        
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
        self._fan_mode = FanMode.AUTO  # Default fan mode
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_min_temp = 18
        self._attr_max_temp = 30
        self._attr_target_temperature_step = 1
        
        # Optimistic update pattern: track command timing and last known device state
        import time
        self._last_command_sent = 0  # Timestamp when we sent a command from HA
        self._last_status = {}  # Last status received from device
        self._pending_command = None  # What we're waiting to be confirmed
        
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
            | ClimateEntityFeature.FAN_MODE
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
    
    @property
    def fan_mode(self):
        """Return the current fan mode."""
        return self._fan_mode
    
    @property
    def fan_modes(self):
        """Return the list of available fan modes."""
        return [FanMode.AUTO, FanMode.HIGH, FanMode.MEDIUM, FanMode.LOW]

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
    
    def set_fan_mode(self, fan_mode):
        """Set new fan mode."""
        import time
        try:
            # Map Home Assistant fan mode to HDL fan speed byte
            fan_mode_map = {
                FanMode.AUTO: FAN_SPEED_AUTO,
                FanMode.HIGH: FAN_SPEED_HIGH,
                FanMode.MEDIUM: FAN_SPEED_MEDIUM,
                FanMode.LOW: FAN_SPEED_LOW,
            }
            
            fan_speed_byte = fan_mode_map.get(fan_mode, FAN_SPEED_AUTO)
            
            # Map Home Assistant HVAC mode to HDL mode byte
            hvac_mode_map = {
                HVACMode.COOL: HVAC_MODE_COOL,
                HVACMode.FAN_ONLY: HVAC_MODE_FAN,
                HVACMode.DRY: HVAC_MODE_DRY,
            }
            
            hdl_mode = hvac_mode_map.get(self._hvac_mode, HVAC_MODE_COOL)
            
            # Build packet with current temp/mode + new fan speed
            frame = build_packet(
                "on",
                self._subnet,
                self._device_id,
                self._gateway.protocol_schema,
                temperature=self._target_temperature,
                hvac_mode=hdl_mode,
                fan_speed=fan_speed_byte
            )
            
            # Optimistic update
            self._last_command_sent = time.time()
            self._pending_command = {
                'is_on': True,
                'temperature': self._target_temperature,
                'hvac_mode': hdl_mode,
                'fan_speed': fan_speed_byte
            }
            
            # Send via gateway
            success = self._gateway.send_packet(frame)
            
            if success:
                self._fan_mode = fan_mode
                self.schedule_update_ha_state()
                _LOGGER.info(
                    f"Set fan mode: {self._name} (fan={fan_mode})"
                )
            else:
                _LOGGER.error(f"Failed to set fan mode: {self._name}")
                
        except Exception as e:
            _LOGGER.error(f"Error setting fan mode {self._name}: {e}")

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
            
            # Map fan mode to fan speed byte
            fan_mode_map = {
                FanMode.AUTO: FAN_SPEED_AUTO,
                FanMode.HIGH: FAN_SPEED_HIGH,
                FanMode.MEDIUM: FAN_SPEED_MEDIUM,
                FanMode.LOW: FAN_SPEED_LOW,
            }
            fan_speed_byte = fan_mode_map.get(self._fan_mode, FAN_SPEED_AUTO)
            
            # Build ON packet with temperature, mode, and fan speed
            frame = build_packet(
                "on",
                self._subnet,
                self._device_id,
                self._gateway.protocol_schema,
                temperature=self._target_temperature,
                hvac_mode=hdl_mode,
                fan_speed=fan_speed_byte
            )
            
            # Optimistic update: record what we're sending
            self._last_command_sent = time.time()
            self._pending_command = {
                'is_on': True,
                'temperature': self._target_temperature,
                'hvac_mode': hdl_mode,
                'fan_speed': fan_speed_byte
            }
            
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
            
            # Optimistic update: record what we're sending (OFF command)
            self._last_command_sent = time.time()
            self._pending_command = {
                'is_on': False,
                'temperature': self._target_temperature,  # Preserve temperature
                'hvac_mode': None
            }
            
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
        Handle status update from gateway broadcast using optimistic update pattern.
        
        Optimistic updates:
        - UI updates immediately when HA sends command (instant feedback)
        - Ignore device status for 2-3s after sending command (device processing)
        - After window, accept whatever device reports as truth
        
        Args:
            status: Dictionary with 'is_on', 'temperature', 'hvac_mode' keys
        """
        import time
        
        current_time = time.time()
        
        # OPTIMISTIC UPDATE WINDOW: Ignore device status for 2.5s after sending HA command
        # This gives device time to process and prevents fighting with our own commands
        command_window_active = (current_time - self._last_command_sent) < 2.5
        
        if command_window_active:
            _LOGGER.debug(
                f"⏭️ Ignoring status update during command window for {self._name} "
                f"(sent {current_time - self._last_command_sent:.1f}s ago)"
            )
            return
        
        # DEBOUNCING: Ignore if this status is identical to the last one received
        # This prevents state flapping from rapid, duplicate broadcasts
        if status == self._last_status:
            _LOGGER.debug(f"🔄 Ignoring duplicate status for {self._name}")
            return
        
        _LOGGER.info(f"🎯 Received status update for {self._name}: {status}")
        
        # Ignore DRY mode broadcasts since we removed it from UI
        if status['hvac_mode'] == HVAC_MODE_DRY:
            _LOGGER.debug(f"Ignoring DRY mode broadcast for {self._name}")
            # Still update last_status to prevent re-processing this packet
            self._last_status = status.copy()
            return
        
        # Store as last known device state
        self._last_status = status.copy()
        
        # Clear pending command (device has now reported state)
        if self._pending_command:
            _LOGGER.debug(f"✅ Clearing pending command for {self._name}")
            self._pending_command = None
        
        # Apply the device status to HA state (device is source of truth)
        self._apply_status_update(status)
    
    def _apply_status_update(self, status: dict):
        """Apply the status update immediately, preserving temperature when going OFF."""
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
                        # AC is OFF - preserve temperature
                        if self._hvac_mode != HVACMode.OFF:
                            old_mode = self._hvac_mode
                            self._hvac_mode = HVACMode.OFF
                            updated = True
                            changes.append(f"mode: {old_mode} → OFF (temp preserved: {self._target_temperature}°C)")
                    else:
                        # is_on is None but we have a mode - AC is probably ON
                        # Apply the mode change regardless of current state
                        if self._hvac_mode != new_mode:
                            old_mode = self._hvac_mode
                            self._hvac_mode = new_mode
                            updated = True
                            changes.append(f"mode: {old_mode} → {new_mode}")
            elif status['is_on'] is False:
                # No mode but explicitly OFF - preserve temperature
                if self._hvac_mode != HVACMode.OFF:
                    old_mode = self._hvac_mode
                    self._hvac_mode = HVACMode.OFF
                    updated = True
                    changes.append(f"mode: {old_mode} → OFF (temp preserved: {self._target_temperature}°C)")
            
            # Update temperature ONLY if AC is ON or if temperature is explicitly provided
            # When AC is OFF, preserve the existing target temperature
            if status['temperature'] is not None and status['is_on'] is not False:
                if self._target_temperature != status['temperature']:
                    old_temp = self._target_temperature
                    self._target_temperature = status['temperature']
                    updated = True
                    changes.append(f"temp: {old_temp}°C → {status['temperature']}°C")
            elif status['temperature'] is not None and status['is_on'] is False:
                # AC is OFF but has temperature - log but don't update
                _LOGGER.debug(
                    f"AC is OFF, preserving target temp {self._target_temperature}°C "
                    f"(device reported {status['temperature']}°C)"
                )
            
            # Update fan mode if present in status
            if status.get('fan_speed') is not None:
                # Map fan speed byte to Home Assistant fan mode
                fan_speed_map_reverse = {
                    FAN_SPEED_AUTO: FanMode.AUTO,
                    FAN_SPEED_HIGH: FanMode.HIGH,
                    FAN_SPEED_MEDIUM: FanMode.MEDIUM,
                    FAN_SPEED_LOW: FanMode.LOW,
                }
                new_fan_mode = fan_speed_map_reverse.get(status['fan_speed'], FanMode.AUTO)
                if self._fan_mode != new_fan_mode:
                    old_fan = self._fan_mode
                    self._fan_mode = new_fan_mode
                    updated = True
                    changes.append(f"fan: {old_fan} → {new_fan_mode}")
            
            # If anything changed, update Home Assistant
            if updated:
                _LOGGER.info(f"✅ {self._name} updated: {', '.join(changes)}")
                self.schedule_update_ha_state()
            else:
                _LOGGER.debug(f"No changes for {self._name} (already in sync)")
                
        except Exception as e:
            _LOGGER.error(f"Error handling status update for {self._name}: {e}", exc_info=True)

