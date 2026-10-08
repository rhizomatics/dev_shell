# Server Installation

This step is only required if you need to use Live or Exec mode - it's not needed for the simpler API mode.

This is a powerful component, so treat with care, especially if its on your home server. It has controls to limit it to Home Assistant API and/or SQL access.

## HACS Component


A HACS component that taps into the Home Assistant and acts as a session server over web sockets. It has been designed for HomeAssistant 2026.8 or greater.

Needed for `live` mode only, since `api` mode only uses standard Home Assistant APIs.

Install via [HACS](https://hacs.xyz):

 - it's not in the default HACS repository, so you'll have to add `https://github.com/rhizomatics/homeassistant-repl` as a Custom Repository from the top-right dot menu first
 - Search for *Home Assistant REPL* in the HACS menu and choose *Download*
 - Restart Home Assistant for it to recognize the new custom component available
 - From **Settings → Devices & services → Add integration** find **Home Assistant REPL Server** in the list and install, there's no further config needed
   - The component will quiz you first to make sure you know what you're doing
   - Alternatively add `ha_repl_server:` to `configuration.yaml`, which is imported as a config entry) 