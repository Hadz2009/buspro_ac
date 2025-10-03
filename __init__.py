"""HDL AC Control Integration for Home Assistant."""

import logging
import socket
import voluptuous as vol
from pathlib import Path

from homeassistant.const import CONF_NAME
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_GATEWAY_IP,
    CONF_GATEWAY_PORT,
    DEFAULT_GATEWAY_IP,
    DEFAULT_GATEWAY_PORT,
    TEMPLATES_FILE,
)
from .hdl_ac_core import load_templates, discover_protocol

_LOGGER = logging.getLogger(__name__)

# Configuration schema
CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_GATEWAY_IP, default=DEFAULT_GATEWAY_IP): cv.string,
                vol.Optional(CONF_GATEWAY_PORT, default=DEFAULT_GATEWAY_PORT): cv.port,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


class HdlGateway:
    """HDL Gateway connection handler."""

    def __init__(self, gateway_ip: str, gateway_port: int, templates_path: str):
        """Initialize the gateway."""
        self.gateway_ip = gateway_ip
        self.gateway_port = gateway_port
        self._sock = None
        
        _LOGGER.info(
            f"Initializing HDL Gateway: {gateway_ip}:{gateway_port}"
        )
        
        # Load templates and discover protocol
        try:
            self.templates = load_templates(templates_path)
            self.protocol_schema = discover_protocol(self.templates, silent=True)
            self.prefix = self.protocol_schema['prefix']
            _LOGGER.info("Protocol discovery successful")
        except Exception as e:
            _LOGGER.error(f"Failed to load templates or discover protocol: {e}")
            raise
    
    def send_packet(self, frame: bytes) -> bool:
        """
        Send packet to gateway via UDP.
        
        Args:
            frame: Frame bytes (starting with AA AA)
            
        Returns:
            True if sent successfully, False otherwise
        """
        try:
            # Assemble complete packet (prefix + frame)
            packet = self.prefix + frame
            
            # Create UDP socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2.0)
            
            # Send packet
            sock.sendto(packet, (self.gateway_ip, self.gateway_port))
            _LOGGER.debug(
                f"Sent {len(packet)} bytes to {self.gateway_ip}:{self.gateway_port}"
            )
            
            sock.close()
            return True
            
        except Exception as e:
            _LOGGER.error(f"Failed to send packet: {e}")
            return False


def setup(hass, config):
    """Set up the HDL AC Control integration."""
    conf = config.get(DOMAIN, {})
    
    gateway_ip = conf.get(CONF_GATEWAY_IP, DEFAULT_GATEWAY_IP)
    gateway_port = conf.get(CONF_GATEWAY_PORT, DEFAULT_GATEWAY_PORT)
    
    # Find templates.json in integration directory
    integration_dir = Path(__file__).parent
    templates_path = integration_dir / TEMPLATES_FILE
    
    if not templates_path.exists():
        _LOGGER.error(f"Templates file not found: {templates_path}")
        return False
    
    try:
        # Create gateway instance
        gateway = HdlGateway(gateway_ip, gateway_port, str(templates_path))
        
        # Store in hass.data for climate platform to access
        hass.data[DOMAIN] = {
            "gateway": gateway,
        }
        
        _LOGGER.info(f"HDL AC Control integration initialized successfully")
        return True
        
    except Exception as e:
        _LOGGER.error(f"Failed to initialize HDL AC Control: {e}")
        return False

