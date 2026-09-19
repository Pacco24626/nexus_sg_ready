"""Integrazione Nexus SG Ready: lo Smart Grid della pompa di calore sull'esubero fotovoltaico.

Comanda i due contatti SG-Ready (relè gia' presenti in Home Assistant) in base
all'esubero calcolato al lordo della pompa: immissione in rete piu' quello che
la pompa sta assorbendo. Stadio 1 (solo contatto 1) quando l'esubero supera la
prima soglia, stadio 2 (entrambi) oltre la seconda se abilitato; il solo
contatto 2, che blocca la macchina, non viene mai chiesto e una sentinella lo
corregge se compare.

Alla prima installazione parte in «osservazione»: decide e scrive il perche'
senza toccare i contatti, per confrontarla con il sistema che li comanda oggi.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS
from .impianto import Impianto

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Configura una voce: una pompa di calore con i suoi contatti."""
    impianto = Impianto(hass, entry)
    await impianto.async_start()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = impianto

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_ricarica))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    scaricato = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if scaricato:
        impianto: Impianto = hass.data[DOMAIN].pop(entry.entry_id)
        await impianto.async_stop()
    return scaricato


async def _async_ricarica(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
