"""Config flow: contatti da comandare, misure da leggere, soglie e tempi.

Il modulo e' diviso in cinque sezioni e arriva gia' compilato: le entita' sono
quelle riconosciute dal nome, la convenzione del contatore quella di Nexus
Fotovoltaico se legge lo stesso scambio, i parametri i valori predefiniti. Le
stesse sezioni tornano in «Configura».
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import CONF_ENTITA, CONF_NOME, CONF_PARAMETRI, DOMAIN, IMMISSIONE_POSITIVA, NOME_PREDEFINITO
from .logica import (
    CAMPI,
    PREDEFINITI,
    SEZIONI,
    SLOT,
    Campo,
    entita_da_modulo,
    errori_modulo,
    parametri_da_modulo,
    proposte,
)

# Sezioni chiuse all'apertura del modulo: i tempi hanno valori predefiniti
# ragionevoli e di solito non servono subito.
CHIUSE = ("tempi",)


def _contatti_in_uso(hass: HomeAssistant, tranne: str | None = None) -> set[str]:
    """I relè gia' comandati da un'altra voce: due voci sugli stessi contatti
    si intreccerebbero le sequenze e potrebbero lasciare il solo contatto 2 chiuso."""
    in_uso: set[str] = set()
    for voce in hass.config_entries.async_entries(DOMAIN):
        if voce.entry_id == tranne:
            continue
        entita = {**voce.data, **voce.options}.get(CONF_ENTITA) or {}
        in_uso.update(v for k, v in entita.items() if k in ("contatto_1", "contatto_2") and v)
    return in_uso


def _errore_contatti(entita: Mapping[str, str], in_uso: set[str]) -> dict[str, str]:
    if any(entita.get(k) in in_uso for k in ("contatto_1", "contatto_2") if entita.get(k)):
        return {"base": "contatto_in_uso"}
    return {}


def _errore_contatto_chiuso(hass: HomeAssistant, vecchie: Mapping[str, str], nuove: Mapping[str, str]) -> dict[str, str]:
    """Non si toglie ne' si cambia un contatto che adesso e' chiuso.

    La voce ricaricata non lo vedrebbe piu': se fosse il contatto 2, al primo
    rilascio aprirebbe l'1 e la macchina resterebbe nel blocco senza che nessuno
    se ne accorga.
    """
    for chiave in ("contatto_1", "contatto_2"):
        vecchio = vecchie.get(chiave)
        if not vecchio or vecchio == nuove.get(chiave):
            continue
        stato = hass.states.get(vecchio)
        if stato is not None and stato.state == "on":
            return {"base": "contatto_chiuso"}
    return {}


def _proposte_da_hass(hass: HomeAssistant) -> dict[str, str]:
    """Le entita' proposte: escluse quelle create da questa integrazione e i relè
    gia' usati da un'altra voce."""
    nostre = {
        voce.entity_id for voce in er.async_get(hass).entities.values() if voce.platform == DOMAIN
    }
    return proposte(hass.states.async_entity_ids(), escludi=nostre | _contatti_in_uso(hass))


def _segno_proposto(hass: HomeAssistant, scambio: str | None) -> str:
    """La convenzione di Nexus Fotovoltaico, se legge lo stesso scambio con la rete."""
    if scambio:
        for stato in hass.states.async_all("sensor"):
            attributi = stato.attributes
            if attributi.get("ruolo") != "mappa_fotovoltaico":
                continue
            if (attributi.get("entita") or {}).get("scambio_rete") == scambio:
                segno = (attributi.get("opzioni") or {}).get("segno_rete")
                if segno:
                    return str(segno)
    return IMMISSIONE_POSITIVA


def _selettore(campo: Campo) -> Any:
    if campo.tipo == "booleano":
        return selector.BooleanSelector()
    if campo.tipo == "orario":
        return selector.TimeSelector()
    if campo.tipo == "scelta":
        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(campo.opzioni),
                translation_key=campo.chiave,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=campo.minimo,
            max=campo.massimo,
            step=campo.passo,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement=campo.unita,
        )
    )


def _campi(entita: Mapping[str, str], parametri: Mapping[str, Any]) -> dict[Any, Any]:
    """Le cinque sezioni, con entita' e parametri precompilati.

    Per le entita' si usa il valore suggerito e non quello predefinito: cosi' una
    voce facoltativa si puo' anche svuotare.
    """
    campi: dict[Any, Any] = {}
    for nome_sezione in SEZIONI:
        interno: dict[Any, Any] = {}
        for slot in (s for s in SLOT if s.sezione == nome_sezione):
            descrizione = {"suggested_value": entita[slot.chiave]} if entita.get(slot.chiave) else None
            marcatore = vol.Required if slot.obbligatorio else vol.Optional
            interno[marcatore(slot.chiave, description=descrizione)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=list(slot.domini))
            )
        for campo in (c for c in CAMPI if c.sezione == nome_sezione):
            valore = parametri.get(campo.chiave, campo.predefinito)
            interno[vol.Required(campo.chiave, default=valore)] = _selettore(campo)
        campi[vol.Required(nome_sezione)] = section(
            vol.Schema(interno), {"collapsed": nome_sezione in CHIUSE}
        )
    return campi


class NexusSgReadyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Una voce per pompa di calore: se ne possono configurare piu' d'una."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errori: dict[str, str] = {}
        nome = NOME_PREDEFINITO
        if user_input is not None:
            entita = entita_da_modulo(user_input)
            parametri = parametri_da_modulo(user_input)
            nome = (user_input.get(CONF_NOME) or "").strip() or NOME_PREDEFINITO
            errori = errori_modulo(entita, parametri) or _errore_contatti(entita, _contatti_in_uso(self.hass))
            if not errori:
                return self.async_create_entry(
                    title=nome, data={CONF_ENTITA: entita, CONF_PARAMETRI: parametri}
                )
        else:
            entita = _proposte_da_hass(self.hass)
            parametri = {**PREDEFINITI, "segno_rete": _segno_proposto(self.hass, entita.get("scambio_rete"))}

        schema = vol.Schema({vol.Required(CONF_NOME, default=nome): str, **_campi(entita, parametri)})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errori)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return NexusSgReadyOptionsFlow()


class NexusSgReadyOptionsFlow(OptionsFlow):
    """Tutto si cambia senza reinstallare; il nome rinominando la voce."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        attuali = {**self.config_entry.data, **self.config_entry.options}
        base = {**PREDEFINITI, **(attuali.get(CONF_PARAMETRI) or {})}
        errori: dict[str, str] = {}

        if user_input is not None:
            entita = entita_da_modulo(user_input)
            parametri = parametri_da_modulo(user_input, base)
            errori = (
                errori_modulo(entita, parametri)
                or _errore_contatti(entita, _contatti_in_uso(self.hass, tranne=self.config_entry.entry_id))
                or _errore_contatto_chiuso(self.hass, attuali.get(CONF_ENTITA) or {}, entita)
            )
            if not errori:
                return self.async_create_entry(data={CONF_ENTITA: entita, CONF_PARAMETRI: parametri})
        else:
            entita = dict(attuali.get(CONF_ENTITA) or {})
            parametri = base

        return self.async_show_form(step_id="init", data_schema=vol.Schema(_campi(entita, parametri)), errors=errori)
