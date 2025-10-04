"""HDL AC Control Integration for Home Assistant."""

import logging
import socket
import threading
import voluptuous as vol
from pathlib import Path

from homeassistant.const import CONF_NAME
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_GATEWAY_IP,
    CONF_GATEWAY_PORT,
    CONF_GATEWAYS,
    CONF_SUBNET,
    DEFAULT_GATEWAY_IP,
    DEFAULT_GATEWAY_PORT,
    TEMPLATES_FILE,
)
from .hdl_ac_core import load_templates, discover_protocol, parse_status_packet

_LOGGER = logging.getLogger(__name__)

# Gateway schema for multi-gateway configuration
GATEWAY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SUBNET): cv.positive_int,
        vol.Required(CONF_GATEWAY_IP): cv.string,
        vol.Optional(CONF_GATEWAY_PORT, default=DEFAULT_GATEWAY_PORT): cv.port,
    }
)

# Configuration schema supporting both old and new formats
CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                # New format: list of gateways
                vol.Optional(CONF_GATEWAYS): vol.All(cv.ensure_list, [GATEWAY_SCHEMA]),
                # Old format: single gateway (backward compatible)
                vol.Optional(CONF_GATEWAY_IP, default=DEFAULT_GATEWAY_IP): cv.string,
                vol.Optional(CONF_GATEWAY_PORT, default=DEFAULT_GATEWAY_PORT): cv.port,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


class HdlGateway:
    """HDL Gateway connection handler with UDP listener for status updates."""

    def __init__(self, gateway_ip: str, gateway_port: int, templates_path: str):
        """Initialize the gateway."""
        self.gateway_ip = gateway_ip
        self.gateway_port = gateway_port
        self._sock = None
        self._listener_thread = None
        self._listener_running = False
        self._callbacks = {}  # {(subnet, device_id): [callback_functions]}
        self._callbacks_lock = threading.Lock()
        
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
    
    def register_callback(self, subnet: int, device_id: int, callback):
        """
        Register a callback for status updates from a specific device.
        
        Args:
            subnet: Device subnet
            device_id: Device ID
            callback: Function to call with status dict
        """
        with self._callbacks_lock:
            key = (subnet, device_id)
            if key not in self._callbacks:
                self._callbacks[key] = []
            self._callbacks[key].append(callback)
            _LOGGER.debug(f"Registered callback for device {subnet}.{device_id}")
    
    def unregister_callback(self, subnet: int, device_id: int, callback):
        """
        Unregister a callback for a specific device.
        
        Args:
            subnet: Device subnet
            device_id: Device ID
            callback: Function to unregister
        """
        with self._callbacks_lock:
            key = (subnet, device_id)
            if key in self._callbacks:
                try:
                    self._callbacks[key].remove(callback)
                    if not self._callbacks[key]:
                        del self._callbacks[key]
                    _LOGGER.debug(f"Unregistered callback for device {subnet}.{device_id}")
                except ValueError:
                    pass
    
    def start_listener(self):
        """Start the UDP listener thread to receive status broadcasts."""
        if self._listener_running:
            _LOGGER.warning("Listener already running")
            return
        
        self._listener_running = True
        self._listener_thread = threading.Thread(target=self._listener_loop, daemon=True)
        self._listener_thread.start()
        _LOGGER.info(f"Started UDP listener on port {self.gateway_port}")
    
    def stop_listener(self):
        """Stop the UDP listener thread."""
        if not self._listener_running:
            return
        
        _LOGGER.info("Stopping UDP listener...")
        self._listener_running = False
        
        # Close the socket to unblock recv
        if self._sock:
            try:
                self._sock.close()
            except:
                pass
        
        # Wait for thread to finish
        if self._listener_thread:
            self._listener_thread.join(timeout=5.0)
        
        _LOGGER.info("UDP listener stopped")
    
    def _listener_loop(self):
        """Background thread that listens for UDP broadcasts."""
        try:
            # Create UDP socket for receiving broadcasts
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            # Bind to all interfaces on the gateway port
            self._sock.bind(('0.0.0.0', self.gateway_port))
            self._sock.settimeout(1.0)  # 1 second timeout for checking _listener_running
            
            _LOGGER.info(f"Listening for HDL broadcasts on 0.0.0.0:{self.gateway_port}")
            
            while self._listener_running:
                try:
                    # Receive packet
                    data, addr = self._sock.recvfrom(1024)
                    
                    # Log received packet
                    _LOGGER.debug(f"Packet received: {len(data)} bytes from {addr[0]}:{addr[1]}")
                    
                    # Parse status packet
                    status = parse_status_packet(data, self.protocol_schema)
                    
                    if status:
                        subnet = status['subnet']
                        device_id = status['device_id']
                        
                        _LOGGER.debug(
                            f"Parsed status for {subnet}.{device_id}: "
                            f"on={status['is_on']}, temp={status['temperature']}, "
                            f"mode={status['hvac_mode']}"
                        )
                        
                        # Notify registered callbacks
                        with self._callbacks_lock:
                            key = (subnet, device_id)
                            
                            if key in self._callbacks:
                                _LOGGER.debug(f"Notifying {len(self._callbacks[key])} callback(s) for {subnet}.{device_id}")
                                for callback in self._callbacks[key]:
                                    try:
                                        callback(status)
                                    except Exception as e:
                                        _LOGGER.error(f"Error in status callback for {subnet}.{device_id}: {e}", exc_info=True)
                            else:
                                _LOGGER.info(f"Status update from unconfigured device {subnet}.{device_id} (ignored)")
                    else:
                        _LOGGER.debug(f"Received packet could not be parsed as status update")
                    
                except socket.timeout:
                    # Timeout is normal, just check if we should continue
                    continue
                except Exception as e:
                    if self._listener_running:
                        _LOGGER.error(f"❌ Error in listener loop: {e}", exc_info=True)
            
        except Exception as e:
            _LOGGER.error(f"Failed to start UDP listener: {e}")
        finally:
            if self._sock:
                try:
                    self._sock.close()
                except:
                    pass
            _LOGGER.debug("Listener loop exited")


def setup(hass, config):
    """Set up the HDL AC Control integration."""
    conf = config.get(DOMAIN, {})
    
    # Find templates.json in integration directory
    integration_dir = Path(__file__).parent
    templates_path = integration_dir / TEMPLATES_FILE
    
    if not templates_path.exists():
        _LOGGER.error(f"Templates file not found: {templates_path}")
        return False
    
    try:
        gateways = {}
        
        # Check for new multi-gateway format
        if CONF_GATEWAYS in conf:
            _LOGGER.info("Using multi-gateway configuration")
            for gw_conf in conf[CONF_GATEWAYS]:
                subnet = gw_conf[CONF_SUBNET]
                gateway_ip = gw_conf[CONF_GATEWAY_IP]
                gateway_port = gw_conf.get(CONF_GATEWAY_PORT, DEFAULT_GATEWAY_PORT)
                
                _LOGGER.info(f"Initializing gateway for subnet {subnet}: {gateway_ip}:{gateway_port}")
                
                # Create gateway instance for this subnet
                gateway = HdlGateway(gateway_ip, gateway_port, str(templates_path))
                gateway.start_listener()
                
                gateways[subnet] = gateway
        else:
            # Backward compatibility: old single-gateway format
            _LOGGER.info("Using legacy single-gateway configuration")
            gateway_ip = conf.get(CONF_GATEWAY_IP, DEFAULT_GATEWAY_IP)
            gateway_port = conf.get(CONF_GATEWAY_PORT, DEFAULT_GATEWAY_PORT)
            
            _LOGGER.info(f"Initializing single gateway: {gateway_ip}:{gateway_port}")
            
            # Create gateway instance
            gateway = HdlGateway(gateway_ip, gateway_port, str(templates_path))
            gateway.start_listener()
            
            # Store as default gateway (subnet None means any/all subnets)
            gateways[None] = gateway
        
        # Store in hass.data for climate platform to access
        hass.data[DOMAIN] = {
            "gateways": gateways,
            # Keep "gateway" for backward compatibility with old configs
            "gateway": gateways.get(None) or next(iter(gateways.values())),
        }
        
        _LOGGER.info(f"HDL AC Control integration initialized successfully with {len(gateways)} gateway(s)")
        return True
        
    except Exception as e:
        _LOGGER.error(f"Failed to initialize HDL AC Control: {e}")
        return False

