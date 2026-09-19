"""Sensori: lo stadio chiesto (con il perche'), lo stato dei contatti e l'esubero lordo."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, STADI, STATI_CONTATTI
from .impianto import Impianto

ICONE_STADIO = {
    "normale": "mdi:transmission-tower",
    "consigliato": "mdi:transmission-tower-export",
    "comando": "mdi:lightning-bolt",
    "blocco": "mdi:cancel",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    impianto: Impianto = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([StadioRichiestoSensor(impianto), StadioContattiSensor(impianto), EsuberoSensor(impianto)])


class _SensoreImpianto(SensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, impianto: Impianto, chiave: str) -> None:
        self._impianto = impianto
        self._attr_translation_key = chiave
        self._attr_unique_id = f"{impianto.entry.entry_id}_{chiave}"
        self._attr_device_info = impianto.device_info

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._impianto.async_add_listener(self.async_write_ha_state))


class StadioRichiestoSensor(_SensoreImpianto):
    """Lo stadio che l'integrazione chiede (anche in osservazione), con il motivo.

    Motivo ed esubero cambiano a ogni giro: nel database finirebbe una riga ogni
    15 secondi. Si registrano solo gli attributi che cambiano di rado, fra cui
    `ultimo_cambio`, il perche' dell'ultimo cambio di stadio.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STADI
    _unrecorded_attributes = frozenset({"motivo", "esubero_lordo", "potenza_pdc", "pompa_in_marcia", "tenuta"})

    def __init__(self, impianto: Impianto) -> None:
        super().__init__(impianto, "stadio_richiesto")

    @property
    def native_value(self) -> str | None:
        decisione = self._impianto.decisione
        return None if decisione is None else STADI[decisione.stadio]

    @property
    def icon(self) -> str:
        return ICONE_STADIO.get(self.native_value or "", "mdi:transmission-tower")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._impianto.attributi


class StadioContattiSensor(_SensoreImpianto):
    """Cosa vede la macchina dai due contatti, chiunque li abbia mossi."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATI_CONTATTI

    def __init__(self, impianto: Impianto) -> None:
        super().__init__(impianto, "stadio_contatti")

    @property
    def native_value(self) -> str | None:
        return self._impianto.stato_contatti

    @property
    def icon(self) -> str:
        return ICONE_STADIO.get(self.native_value or "", "mdi:electric-switch")


class EsuberoSensor(_SensoreImpianto):
    """Immissione in rete piu' quello che assorbe la pompa: la grandezza su cui si decide."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:solar-power-variant"

    def __init__(self, impianto: Impianto) -> None:
        super().__init__(impianto, "esubero_lordo")

    @property
    def native_value(self) -> float | None:
        decisione = self._impianto.decisione
        return None if decisione is None else decisione.esubero
