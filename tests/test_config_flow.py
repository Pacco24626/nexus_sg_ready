"""Prove a tavolino del config flow e del flusso delle opzioni.

    python tests/test_config_flow.py
"""

from __future__ import annotations

import asyncio
import json
import os

import finto_ha  # noqa: F401
from finto_ha import Banco, BooleanSelector, Entry, Hass, NumberSelector, SelectSelector, TimeSelector, section

from nexus_sg_ready.config_flow import NexusSgReadyConfigFlow, NexusSgReadyOptionsFlow
from nexus_sg_ready.logica import CAMPI, PREDEFINITI, SEZIONI, SLOT

b = Banco("CONFIG FLOW")

C1 = "switch.cantina_sg_canale_1_430"
C2 = "switch.cantina_sg_canale_2_431"
RETE = "sensor.nexus_t_energy_monitor_potenza_scambio_rete"
COMP = "binary_sensor.pompa_di_calore_pdc_compressore_attivo"
ACS = "sensor.pompa_di_calore_pdc_temp_accumulo_acs"


def hass_vero(segno_fv: str | None = "prelievo_positivo", scambio_fv: str = RETE):
    hass = Hass()
    for entity_id in (C1, C2, "switch.cantina_sg_canale_1_e_2_429", RETE, COMP, ACS, "light.salotto"):
        hass.stati.imposta(entity_id, "off")
    if segno_fv:
        hass.stati.imposta(
            "sensor.fotovoltaico_mappa_impianto",
            "5",
            {"ruolo": "mappa_fotovoltaico", "entita": {"scambio_rete": scambio_fv}, "opzioni": {"segno_rete": segno_fv}},
        )
    # Un'entita' della stessa integrazione: non va proposta.
    hass.stati.imposta("switch.vecchio_sg_canale_1", "off")
    hass.registro.aggiungi("switch.vecchio_sg_canale_1", "nexus_sg_ready")
    return hass


def campi(schema):
    return {str(k): (k, v) for k, v in schema.items()}


def voce(risultato, sezione, nome):
    _, sez = campi(risultato["schema"])[sezione]
    for chiave, selettore in sez.schema.items():
        if str(chiave) == nome:
            return chiave, selettore
    raise KeyError(nome)


def suggerito(risultato, sezione, nome):
    chiave, _ = voce(risultato, sezione, nome)
    return (chiave.description or {}).get("suggested_value")


def modulo_valido(**modifiche):
    dati = {
        "nome": "PdC cantina",
        "contatti": {"contatto_1": C1, "contatto_2": C2, "correggi_blocco": True},
        "rete": {"scambio_rete": RETE, "segno_rete": "immissione_positiva"},
        "pompa": {"compressore": COMP, "temperatura_acs": ACS, "potenza_stimata": 1400.0, "acs_massima": 47.5},
        "stadi": {"soglia_1": 900.0, "isteresi": 200.0, "stadio_2": False, "soglia_2": 2500.0},
        "tempi": {"ora_inizio": "10:00:00", "ora_fine": "17:30:00", "ritardo_attivazione": 2.0,
                  "ritardo_rilascio": 8.0, "durata_minima": 10.0, "pausa_minima": 10.0,
                  "tenuta_ciclo": True, "tenuta_massima": 60.0},
    }
    for sezione, valori in modifiche.items():
        dati[sezione] = {**dati[sezione], **valori}
    return dati


async def main():
    # -------------------------------------------------------------------------
    b.sezione("1. Apertura del modulo")
    flusso = NexusSgReadyConfigFlow()
    flusso.hass = hass_vero()
    r = await flusso.async_step_user()
    c = campi(r["schema"])
    b.v("modulo utente", r["type"] == "form" and r["step_id"] == "user")
    b.v("nome proposto «Smart Grid PdC»", c["nome"][0].default == "Smart Grid PdC")
    b.v("cinque sezioni nell'ordine", [k for k in c if k != "nome"] == list(SEZIONI)
        and all(isinstance(c[s][1], section) for s in SEZIONI))
    b.v("i tempi partono chiusi, le altre aperte",
        c["tempi"][1].options["collapsed"] and not c["contatti"][1].options["collapsed"])
    b.v("contatti proposti dal nome", suggerito(r, "contatti", "contatto_1") == C1
        and suggerito(r, "contatti", "contatto_2") == C2)
    b.v("un'entita' di questa integrazione non viene proposta", suggerito(r, "contatti", "contatto_1") != "switch.vecchio_sg_canale_1")
    b.v("scambio, compressore e bollitore proposti", suggerito(r, "rete", "scambio_rete") == RETE
        and suggerito(r, "pompa", "compressore") == COMP and suggerito(r, "pompa", "temperatura_acs") == ACS)
    b.v("potenza della pompa non proposta (non c'e')", suggerito(r, "pompa", "potenza_pdc") is None)
    b.v("contatto 1 obbligatorio, contatto 2 facoltativo",
        type(voce(r, "contatti", "contatto_1")[0]).__name__ == "Required"
        and type(voce(r, "contatti", "contatto_2")[0]).__name__ == "Optional")
    b.v("convenzione presa da Nexus Fotovoltaico (stesso scambio)", voce(r, "rete", "segno_rete")[0].default == "prelievo_positivo")
    b.v("soglia 1 predefinita 1000 W", voce(r, "stadi", "soglia_1")[0].default == 1000)
    b.v("stadio 2 spento di default", voce(r, "stadi", "stadio_2")[0].default is False)
    b.v("selettori giusti: numero, interruttore, orario, scelta",
        isinstance(voce(r, "stadi", "soglia_1")[1], NumberSelector)
        and isinstance(voce(r, "stadi", "stadio_2")[1], BooleanSelector)
        and isinstance(voce(r, "tempi", "ora_inizio")[1], TimeSelector)
        and isinstance(voce(r, "rete", "segno_rete")[1], SelectSelector))
    b.v("la soglia ha unita' e limiti", voce(r, "stadi", "soglia_1")[1].config.get("unit_of_measurement") == "W")
    b.v("ogni campo dei parametri compare nel modulo",
        all(any(str(k) == campo.chiave for k in c[campo.sezione][1].schema) for campo in CAMPI))
    b.v("ogni entita' compare nel modulo",
        all(any(str(k) == slot.chiave for k in c[slot.sezione][1].schema) for slot in SLOT))

    flusso = NexusSgReadyConfigFlow()
    flusso.hass = hass_vero(scambio_fv="sensor.altro_scambio")
    r = await flusso.async_step_user()
    b.v("scambio diverso da quello del fotovoltaico: convenzione predefinita",
        voce(r, "rete", "segno_rete")[0].default == "immissione_positiva")

    # -------------------------------------------------------------------------
    b.sezione("2. Invio")
    flusso = NexusSgReadyConfigFlow()
    flusso.hass = hass_vero()
    r = await flusso.async_step_user(modulo_valido())
    b.v("voce creata con il nome scelto", r["type"] == "create_entry" and r["title"] == "PdC cantina")
    dati = r["data"]
    b.v("entita' salvate senza sezioni", dati["entita"] == {"contatto_1": C1, "contatto_2": C2, "scambio_rete": RETE,
                                                          "compressore": COMP, "temperatura_acs": ACS})
    b.v("parametri salvati nel tipo giusto", dati["parametri"]["soglia_1"] == 900.0
        and dati["parametri"]["ora_inizio"] == "10:00" and dati["parametri"]["ora_fine"] == "17:30"
        and dati["parametri"]["tenuta_ciclo"] is True)
    b.v("tutti i parametri presenti", set(dati["parametri"]) == set(PREDEFINITI))

    r = await flusso.async_step_user(modulo_valido(contatti={"contatto_1": ""}))
    b.v("senza contatto 1: errore", r["type"] == "form" and r["errors"] == {"base": "mancano_obbligatorie"})
    b.v("il modulo rimostrato conserva le scelte", suggerito(r, "rete", "scambio_rete") == RETE
        and voce(r, "stadi", "soglia_1")[0].default == 900.0)
    r = await flusso.async_step_user(modulo_valido(stadi={"stadio_2": True}, contatti={"contatto_2": ""}))
    b.v("stadio 2 senza contatto 2: errore", r["errors"] == {"base": "stadio_2_senza_contatto"})
    r = await flusso.async_step_user(modulo_valido(stadi={"stadio_2": True, "soglia_2": 800.0}))
    b.v("soglie invertite: errore", r["errors"] == {"base": "soglie_invertite"})
    r = await flusso.async_step_user(modulo_valido(tempi={"ora_fine": "10:00:00"}))
    b.v("fascia vuota: errore", r["errors"] == {"base": "fascia_vuota"})
    r = await flusso.async_step_user(modulo_valido(contatti={"contatto_2": C1}))
    b.v("stesso relè per i due contatti: errore", r["errors"] == {"base": "entita_ripetuta"})

    # -------------------------------------------------------------------------
    b.sezione("3. Opzioni")
    entry = Entry({"entita": {"contatto_1": C1, "scambio_rete": RETE},
                   "parametri": {**PREDEFINITI, "soglia_1": 1200.0}}, entry_id="sg1")
    opzioni = NexusSgReadyOptionsFlow()
    opzioni.config_entry = entry
    opzioni.hass = hass_vero()
    r = await opzioni.async_step_init()
    b.v("modulo delle opzioni", r["type"] == "form" and r["step_id"] == "init")
    b.v("niente campo nome nelle opzioni", "nome" not in campi(r["schema"]))
    b.v("precompilato con la configurazione attuale", suggerito(r, "contatti", "contatto_1") == C1
        and suggerito(r, "contatti", "contatto_2") is None and voce(r, "stadi", "soglia_1")[0].default == 1200.0)
    r = await opzioni.async_step_init(modulo_valido(stadi={"soglia_1": 1500.0}))
    b.v("opzioni salvate", r["type"] == "create_entry" and r["data"]["parametri"]["soglia_1"] == 1500.0
        and r["data"]["entita"]["contatto_2"] == C2)
    r = await opzioni.async_step_init({"contatti": {"contatto_1": C1}, "rete": {"scambio_rete": RETE},
                                       "pompa": {"compressore": COMP}})
    b.v("un invio parziale conserva i parametri gia' configurati",
        r["type"] == "create_entry" and r["data"]["parametri"]["soglia_1"] == 1200.0)

    # -------------------------------------------------------------------------
    b.sezione("3b. Revisione: contatti chiusi e contatti gia' usati")
    entry = Entry({"entita": {"contatto_1": C1, "contatto_2": C2, "scambio_rete": RETE, "compressore": COMP},
                   "parametri": dict(PREDEFINITI)}, entry_id="sg1")
    opzioni = NexusSgReadyOptionsFlow()
    opzioni.config_entry = entry
    opzioni.hass = hass_vero()
    opzioni.hass.stati.imposta(C2, "on")
    r = await opzioni.async_step_init(modulo_valido(contatti={"contatto_2": ""}))
    b.v("togliere il contatto 2 mentre e' chiuso: errore", r["errors"] == {"base": "contatto_chiuso"})
    r = await opzioni.async_step_init(modulo_valido(contatti={"contatto_2": "switch.cantina_sg_canale_1_e_2_429"}))
    b.v("cambiarlo mentre e' chiuso: errore", r["errors"] == {"base": "contatto_chiuso"})
    r = await opzioni.async_step_init(modulo_valido(stadi={"soglia_1": 1300.0}))
    b.v("lasciandolo com'e' si salva anche se e' chiuso", r["type"] == "create_entry")
    opzioni.hass.stati.imposta(C2, "off")
    r = await opzioni.async_step_init(modulo_valido(contatti={"contatto_2": ""}))
    b.v("aperto si puo' togliere", r["type"] == "create_entry")

    hass = hass_vero()
    hass.config_entries.voci.append(entry)
    flusso = NexusSgReadyConfigFlow()
    flusso.hass = hass
    r = await flusso.async_step_user()
    b.v("seconda voce: i contatti gia' usati non vengono proposti",
        suggerito(r, "contatti", "contatto_1") is None and suggerito(r, "contatti", "contatto_2") is None)
    r = await flusso.async_step_user(modulo_valido())
    b.v("seconda voce sugli stessi contatti: errore", r["errors"] == {"base": "contatto_in_uso"})
    opzioni = NexusSgReadyOptionsFlow()
    opzioni.config_entry = entry
    opzioni.hass = hass
    r = await opzioni.async_step_init(modulo_valido(stadi={"soglia_1": 1100.0}))
    b.v("le opzioni della voce stessa non contano i suoi contatti come «in uso»", r["type"] == "create_entry")

    # -------------------------------------------------------------------------
    b.sezione("4. Traduzioni")
    cartella = os.path.join(os.path.dirname(__file__), "..", "custom_components", "nexus_sg_ready")
    testi = {}
    for nome in ("strings.json", os.path.join("translations", "it.json"), os.path.join("translations", "en.json")):
        with open(os.path.join(cartella, nome), encoding="utf-8") as f:
            testi[nome] = json.load(f)
    for nome, t in testi.items():
        sezioni = t["config"]["step"]["user"]["sections"]
        mancanti = [
            f"{s}.{v}" for s in SEZIONI
            for v in [slot.chiave for slot in SLOT if slot.sezione == s] + [c.chiave for c in CAMPI if c.sezione == s]
            if v not in sezioni[s]["data"]
        ]
        b.v(f"{nome}: ogni campo ha un'etichetta", mancanti == [])
        b.v(f"{nome}: ogni errore ha un testo", set(t["config"]["error"]) >= {
            "mancano_obbligatorie", "entita_ripetuta", "stadio_2_senza_contatto", "soglie_invertite", "fascia_vuota",
            "manca_pompa", "contatto_in_uso", "contatto_chiuso"})
        b.v(f"{nome}: opzioni con le stesse sezioni", t["options"]["step"]["init"]["sections"].keys() == sezioni.keys())
    b.v("strings.json e it.json coincidono", testi["strings.json"] == testi[os.path.join("translations", "it.json")])

    b.fine()


asyncio.run(main())
