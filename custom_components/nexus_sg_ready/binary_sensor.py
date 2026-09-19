"""Anomalia dei contatti: blocco trovato e corretto, o un relè che non conferma."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .impianto import Impianto


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    impianto: Impianto = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AnomaliaSensor(impianto)])


class AnomaliaSensor(BinarySensorEntity):
    """Acceso finche' l'anomalia e' in corso; il dettaglio negli attributi."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "anomalia"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, impianto: Impianto) -> None:
        self._impianto = impianto
        self._attr_unique_id = f"{impianto.entry.entry_id}_anomalia"
        self._attr_device_info = impianto.device_info

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._impianto.async_add_listener(self.async_write_ha_state))

    @property
    def is_on(self) -> bool:
        return self._impianto.anomalia is not None or self._impianto.stato_contatti == "blocco"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        anomalia = self._impianto.anomalia or {}
        return {chiave: anomalia.get(chiave) for chiave in ("tipo", "dettaglio", "quando")}
