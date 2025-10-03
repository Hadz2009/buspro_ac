# HDL AC Control for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

A native Home Assistant custom integration for controlling HDL BusPro AC units. Simply add device addresses in `configuration.yaml` and AC climate entities appear automatically with ON/OFF controls.

## ✨ Features

- 🔌 **Plug & Play**: Add devices in configuration, they appear automatically
- 🌡️ **Native Climate Entity**: Full Home Assistant climate entity support
- 🔒 **Auto CRC Calculation**: Correct HDL Pascal CRC-16 CCITT implementation
- 📡 **Protocol Discovery**: Automatically learns protocol structure from templates
- 🚀 **Simple Setup**: No complex configuration needed
- 🎯 **Multiple Devices**: Control unlimited AC units

## 📋 Requirements

- Home Assistant 2023.1 or newer
- HDL BusPro Gateway on your network
- AC unit addresses (subnet.device format, e.g., `1.14`)

## 🚀 Installation

### Method 1: HACS (Recommended)

1. Open HACS in Home Assistant
2. Click on "Integrations"
3. Click the three dots in the top right corner
4. Select "Custom repositories"
5. Add this repository URL: `https://github.com/Hadz2009/buspro_ac`
6. Select category: "Integration"
7. Click "Add"
8. Search for "HDL AC Control" in HACS
9. Click "Download"
10. Restart Home Assistant

### Method 2: Manual Installation

1. Download this repository
2. Copy the `buspro_ac` folder to your `<config>/custom_components/` directory
3. Restart Home Assistant

**Your directory structure should look like:**
```
<config>/
├── custom_components/
│   └── buspro_ac/
│       ├── __init__.py
│       ├── manifest.json
│       ├── climate.py
│       ├── hdl_ac_core.py
│       ├── const.py
│       ├── templates.json
│       └── README.md
└── configuration.yaml
```

## ⚙️ Configuration

Add to your `configuration.yaml`:

```yaml
# Configure the HDL gateway
buspro_ac:
  gateway_ip: 192.168.1.25      # Your HDL gateway IP
  gateway_port: 6000             # Default HDL port (usually 6000)

# Add your AC devices
climate:
  - platform: buspro_ac
    devices:
      - address: "1.14"
        name: "Living Room AC"
      - address: "1.13"
        name: "Bedroom AC"
      - address: "1.15"
        name: "Kitchen AC"
```

### Configuration Options

**Integration Settings (`buspro_ac`):**

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `gateway_ip` | No | `192.168.1.25` | IP address of your HDL gateway |
| `gateway_port` | No | `6000` | UDP port for HDL communication |

**Climate Platform (`climate`):**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `platform` | Yes | Must be `buspro_ac` |
| `devices` | Yes | List of AC devices to control |

**Device Configuration:**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `address` | Yes | Device address in `subnet.device` format (e.g., `"1.14"`) |
| `name` | Yes | Friendly name for the device |

## 📖 Usage

After configuration and restart:

1. Your AC devices will appear in Home Assistant as climate entities
2. Find them under **Settings** → **Devices & Services** → **Entities**
3. Entity IDs will be: `climate.living_room_ac`, `climate.bedroom_ac`, etc.

### Basic Controls

**In Home Assistant UI:**
- Toggle ON/OFF from climate card
- Use HVAC mode selector (OFF / COOL)

**In Automations:**
```yaml
# Turn ON
service: climate.set_hvac_mode
target:
  entity_id: climate.living_room_ac
data:
  hvac_mode: cool

# Turn OFF
service: climate.set_hvac_mode
target:
  entity_id: climate.living_room_ac
data:
  hvac_mode: "off"
```

**In Scripts:**
```yaml
turn_on_all_acs:
  sequence:
    - service: climate.turn_on
      target:
        entity_id:
          - climate.living_room_ac
          - climate.bedroom_ac
          - climate.kitchen_ac
```

## 🔧 How It Works

This integration uses an **auto-discovery protocol** that:

1. **Loads templates** - Reads protocol templates from `templates.json`
2. **Validates CRC** - Uses exact HDL Pascal CRC-16 CCITT algorithm
3. **Discovers protocol** - Automatically finds address and command byte positions
4. **Builds packets** - Dynamically creates correct packets for any device
5. **Sends commands** - Transmits via UDP to your HDL gateway

### CRC Implementation

Uses the **exact HDL Pascal CRC-16 CCITT** algorithm:
- Polynomial: `0x1021`
- Left-shift algorithm
- High-byte indexed lookup table
- Includes length byte in calculation
- Format: `[CRCHi, CRCLo]`

## 📝 Finding Your Device Address

Your HDL AC device address is in `subnet.device` format:

1. Open your HDL configuration software (HDL Toolbox)
2. Find your AC controller in the device list
3. Note the subnet ID and device ID
4. Format as `"subnet.device"` (e.g., device 14 on subnet 1 = `"1.14"`)

## 🐛 Troubleshooting

### Integration not loading

**Check logs:**
```yaml
logger:
  default: info
  logs:
    custom_components.buspro_ac: debug
```

**Common issues:**
- Gateway IP incorrect → Check network settings
- Templates file missing → Reinstall integration
- Invalid address format → Use `"1.14"` not `1.14` (quotes required)

### AC not responding

1. **Verify gateway connection**: Can you ping the gateway?
   ```bash
   ping 192.168.1.25
   ```

2. **Check device address**: Is `1.14` the correct address in HDL Toolbox?

3. **Test with original script**: Try the standalone `hdl_ac.py` to verify hardware works

4. **Check Home Assistant logs**: Look for error messages
   ```
   Settings → System → Logs
   ```

### State not updating

This integration uses **optimistic state** (tracks state internally). The AC state in Home Assistant reflects commands sent, not actual hardware state.

**Future versions will add:**
- Real-time state feedback from HDL system
- Status polling

## 🎯 Current Features

- ✅ Turn AC ON
- ✅ Turn AC OFF  
- ✅ HVAC mode control (OFF / COOL)
- ✅ Multiple AC support
- ✅ Auto protocol discovery
- ✅ Correct CRC calculation

## 🚧 Planned Features

- ⏳ Temperature control
- ⏳ HVAC modes (Heat, Fan, Dry, Auto)
- ⏳ Fan speed control
- ⏳ Real-time state feedback
- ⏳ Current temperature sensor
- ⏳ Swing control
- ⏳ Preset modes

## 🤝 Contributing

Found a bug? Have a feature request?

1. Open an issue on [GitHub](https://github.com/Hadz2009/buspro_ac/issues)
2. Provide Home Assistant logs
3. Include your configuration (remove sensitive data)

## 📄 License

This project is licensed under the MIT License.

## 🙏 Acknowledgments

- Based on HDL BusPro protocol reverse engineering
- Inspired by the [eyesoft/home_assistant_buspro](https://github.com/eyesoft/home_assistant_buspro) integration
- Uses exact HDL Pascal CRC-16 CCITT implementation

## 📚 Related Projects

- [HDL BusPro Integration](https://github.com/eyesoft/home_assistant_buspro) - Original HDL integration for lights, switches, and floor heating
- [HDL AC Control CLI](https://github.com/Hadz2009/buspro_ac) - Standalone CLI tool for HDL AC control

---

**Made with ❤️ for the Home Assistant community**

If this integration helps you, consider giving it a ⭐ on GitHub!

