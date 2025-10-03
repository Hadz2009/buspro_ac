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
        
        _LOGGER.debug(
            f"Initialized HDL AC: {name} (subnet={subnet}, device={device_id})"
        )

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
        # Include OFF for state tracking, but UI will show separate power toggle
        return [HVACMode.OFF, HVACMode.COOL, HVACMode.FAN_ONLY, HVACMode.DRY]

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
        elif hvac_mode in [HVACMode.COOL, HVACMode.FAN_ONLY, HVACMode.DRY]:
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
        try:
            # Build OFF packet
            frame = build_packet(
                "off",
                self._subnet,
                self._device_id,
                self._gateway.protocol_schema
            )
            
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

