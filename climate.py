"""Climate platform for HDL AC Control."""

import logging
import voluptuous as vol

from homeassistant.components.climate import ClimateEntity, PLATFORM_SCHEMA
from homeassistant.components.climate.const import (
    HVACMode,
    ClimateEntityFeature,
)
from homeassistant.const import CONF_NAME, UnitOfTemperature
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN, CONF_DEVICES, CONF_ADDRESS
from .hdl_ac_core import build_packet

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
        self._is_on = False
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        
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
        return HVACMode.COOL if self._is_on else HVACMode.OFF

    @property
    def hvac_modes(self):
        """Return the list of available HVAC modes."""
        return [HVACMode.OFF, HVACMode.COOL]

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return ClimateEntityFeature(0)  # Basic on/off only for now

    def set_hvac_mode(self, hvac_mode):
        """Set new target HVAC mode."""
        if hvac_mode == HVACMode.OFF:
            self.turn_off()
        elif hvac_mode == HVACMode.COOL:
            self.turn_on()
        else:
            _LOGGER.warning(f"Unsupported HVAC mode: {hvac_mode}")

    def turn_on(self):
        """Turn AC on."""
        try:
            # Build ON packet
            frame = build_packet(
                "on",
                self._subnet,
                self._device_id,
                self._gateway.protocol_schema
            )
            
            # Send via gateway
            success = self._gateway.send_packet(frame)
            
            if success:
                self._is_on = True
                self.schedule_update_ha_state()
                _LOGGER.info(f"Turned ON: {self._name}")
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
                self._is_on = False
                self.schedule_update_ha_state()
                _LOGGER.info(f"Turned OFF: {self._name}")
            else:
                _LOGGER.error(f"Failed to turn OFF: {self._name}")
                
        except Exception as e:
            _LOGGER.error(f"Error turning OFF {self._name}: {e}")

