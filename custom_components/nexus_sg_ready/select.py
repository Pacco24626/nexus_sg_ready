"""La modalita': automatico, osservazione o spento. Sopravvive ai riavvii."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, MODALITA
from .impianto import Impianto

ICONE = {
    "automatico": "mdi:transmission-tower-export",
    "osservazione": "mdi:eye-outline",
    "spento": "mdi:power-plug-off-outline",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    impianto: Impianto = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ModalitaSelect(impianto)])


class ModalitaSelect(SelectEntity, RestoreEntity):
    """Alla prima installazione parte in osservazione: decide ma non comanda."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "modalita"
    _attr_options = MODALITA

    def __init__(self, impianto: Impianto) -> None:
        self._impianto = impianto
        self._attr_unique_id = f"{impianto.entry.entry_id}_modalita"
        self._attr_device_info = impianto.device_info

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        ultimo = await self.async_get_last_state()
        if ultimo is not None:
            # Un rilascio uscendo dall'automatico non ancora riuscito prima del
            # riavvio (o del ricaricamento) va portato a termine.
            if ultimo.attributes.get("rilascio_pendente"):
                self._impianto.rilascio_pendente = True
            if ultimo.state in MODALITA:
                await self._impianto.async_imposta_modalita(ultimo.state)
        self.async_on_remove(self._impianto.async_add_listener(self.async_write_ha_state))

    @property
    def current_option(self) -> str:
        return self._impianto.modalita

    @property
    def icon(self) -> str:
        return ICONE.get(self._impianto.modalita, "mdi:transmission-tower")

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        return {"rilascio_pendente": self._impianto.rilascio_pendente}

    async def async_select_option(self, option: str) -> None:
        await self._impianto.async_imposta_modalita(option)
        self.async_write_ha_state()
