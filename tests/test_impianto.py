"""Prove a tavolino dell'impianto e delle entita', su un Home Assistant finto.

Il tempo lo guida la prova: un orologio finto (secondi) e l'ora del giorno.

    python tests/test_impianto.py
"""

from __future__ import annotations

import asyncio
import time

import finto_ha
from finto_ha import NOTIFICHE, Banco, Entry, Hass, State

from nexus_sg_ready.binary_sensor import AnomaliaSensor
from nexus_sg_ready.impianto import Impianto
from nexus_sg_ready.logica import PREDEFINITI
from nexus_sg_ready.select import ModalitaSelect
from nexus_sg_ready.sensor import EsuberoSensor, StadioContattiSensor, StadioRichiestoSensor

b = Banco("IMPIANTO")

C1 = "switch.cantina_sg_canale_1_430"
C2 = "switch.cantina_sg_canale_2_431"
RETE = "sensor.nexus_t_energy_monitor_potenza_scambio_rete"
COMP = "binary_sensor.pompa_di_calore_pdc_compressore_attivo"
ACS = "sensor.pompa_di_calore_pdc_temp_accumulo_acs"
ENTITA = {"contatto_1": C1, "contatto_2": C2, "scambio_rete": RETE, "compressore": COMP, "temperatura_acs": ACS}


class Banco2:
    """Hass finto, voce, impianto e orologio guidato."""

    def __init__(self, c1="off", c2="off", rete="-400", parametri=None, unita="W") -> None:
        self.hass = Hass()
        self.hass.stati.imposta(C1, c1)
        self.hass.stati.imposta(C2, c2)
        self.hass.stati.imposta(RETE, rete, {"unit_of_measurement": unita})
        self.hass.stati.imposta(COMP, "off")
        self.hass.stati.imposta(ACS, "46")
        self.entry = Entry(
            {"entita": dict(ENTITA), "parametri": {**PREDEFINITI, **(parametri or {})}},
            title="Smart Grid PdC",
            entry_id="sg1",
        )
        self.imp = Impianto(self.hass, self.entry)
        self.t = 0.0
        self.imp._orologio = lambda: self.t
        self.imp._ora_locale = lambda: 12 * 60 + int(self.t // 60)
        self.imp.attuatore._grazia = 0.01

    def rete(self, watt: float) -> None:
        self.hass.stati.imposta(RETE, str(watt), {"unit_of_measurement": "W"})

    async def minuti(self, quanti: int) -> None:
        """Avanza di un minuto alla volta, un giro per minuto, aspettando i comandi."""
        for _ in range(quanti):
            self.t += 60
            await self.imp.async_giro()
            await self.entry.attendi_compiti()

    @property
    def chiamate(self):
        return [(servizio, entita) for _, servizio, entita in self.hass.services.chiamate]


async def main():
    # -------------------------------------------------------------------------
    b.sezione("1. Avvio in osservazione")
    x = Banco2()
    await x.imp.async_start()
    b.v("parte in osservazione", x.imp.modalita == "osservazione")
    b.v("registra il giro periodico", len(x.hass.intervalli) == 1 and x.hass.intervalli[0][1].total_seconds() == 15)
    b.v("ascolta le rinomine del registro",
        any(tipo == finto_ha.EVENT_ENTITY_REGISTRY_UPDATED for tipo, _ in x.hass.bus.ascoltatori))
    b.v("prima decisione: normale", x.imp.decisione.stadio == 0 and x.imp.stato_contatti == "normale")
    x.rete(1500)
    await x.minuti(4)
    b.v("con 1500 W per 3 minuti decide lo stadio 1", x.imp.decisione.stadio == 1)
    b.v("ma in osservazione non tocca i contatti", x.chiamate == [] and x.imp.stato_contatti == "normale")

    b.sezione("2. Entita'")
    richiesto, contatti, esubero, anomalia = (
        StadioRichiestoSensor(x.imp), StadioContattiSensor(x.imp), EsuberoSensor(x.imp), AnomaliaSensor(x.imp))
    for entita in (richiesto, contatti, esubero, anomalia):
        await entita.async_added_to_hass()
    b.v("stadio richiesto: consigliato", richiesto.native_value == "consigliato")
    b.v("stadio dei contatti: normale", contatti.native_value == "normale")
    b.v("esubero lordo: 1500 W", esubero.native_value == 1500)
    attributi = richiesto.extra_state_attributes
    b.v("il motivo e' negli attributi", "sopra" in (attributi["motivo"] or "") and attributi["modalita"] == "osservazione")
    b.v("attributi nuovi a ogni lettura (copia superficiale di HA)",
        richiesto.extra_state_attributes is not attributi)
    b.v("nessuna anomalia", anomalia.is_on is False)
    b.v("id univoci distinti", len({e.unique_id for e in (richiesto, contatti, esubero, anomalia)}) == 4)
    prima = richiesto.__dict__.get("scritture", 0)
    x.rete(1600)
    await x.minuti(1)
    b.v("un cambio di esubero aggiorna le entita'", richiesto.__dict__.get("scritture", 0) > prima)

    # -------------------------------------------------------------------------
    b.sezione("3. Automatico")
    await x.imp.async_imposta_modalita("automatico")
    await x.entry.attendi_compiti()
    b.v("passando in automatico riparte dai contatti (normale) e allinea: chiude il contatto 1",
        x.chiamate == [("turn_on", C1)] and x.imp.stato_contatti == "consigliato")
    b.v("i contatti seguono la decisione", contatti.native_value == "consigliato")
    x.rete(0)
    await x.minuti(9)
    b.v("esubero sparito: il contatto resta chiuso durante il ritardo di rilascio",
        x.hass.states.get(C1).state == "on")
    await x.minuti(2)
    b.v("dopo il ritardo di rilascio il contatto 1 si apre", x.hass.states.get(C1).state == "off")

    # -------------------------------------------------------------------------
    b.sezione("4. Un altro sistema muove i contatti")
    x = Banco2(rete="1500")
    await x.imp.async_start()
    await x.imp.async_imposta_modalita("automatico")
    await x.minuti(4)
    b.v("stadio 1 comandato", x.hass.states.get(C1).state == "on")
    NOTIFICHE.clear()
    x.hass.stati.imposta(C1, "off")  # la vecchia scena dello Yubii
    b.v("avviso: contatti comandati da fuori",
        any("da un altro sistema" in n["message"] for n in NOTIFICHE))
    await x.minuti(0)
    await x.imp.async_giro()
    await x.entry.attendi_compiti()
    b.v("non si riprova subito (stesso obiettivo di un tentativo recente)", x.hass.states.get(C1).state == "off")
    await x.minuti(1)
    b.v("dopo l'attesa si riallinea", x.hass.states.get(C1).state == "on")
    avvisi = len(NOTIFICHE)
    x.hass.stati.imposta(C1, "off")
    b.v("un secondo cambio esterno entro l'ora non crea un'altra notifica", len(NOTIFICHE) == avvisi)

    # -------------------------------------------------------------------------
    b.sezione("5. Uscita dall'automatico e spento")
    x = Banco2(rete="1500")
    await x.imp.async_start()
    await x.imp.async_imposta_modalita("automatico")
    await x.minuti(4)
    await x.imp.async_imposta_modalita("osservazione")
    await x.entry.attendi_compiti()
    b.v("uscendo dall'automatico si rilascia", x.hass.states.get(C1).state == "off")
    await x.imp.async_imposta_modalita("spento")
    await x.minuti(5)
    b.v("spento: stadio 0 con esubero alto, e nessun comando",
        x.imp.decisione.stadio == 0 and x.hass.states.get(C1).state == "off" and "spento" in x.imp.decisione.motivo)
    b.v("spento: l'esubero si calcola ancora", x.imp.decisione.esubero == 1500)

    # -------------------------------------------------------------------------
    b.sezione("6. Riavvio in automatico con lo stadio gia' attivo")
    x = Banco2(c1="on", rete="0")
    await x.imp.async_start()
    await x.imp.async_imposta_modalita("automatico")
    await x.entry.attendi_compiti()
    b.v("al primo giro non rilascia", x.chiamate == [] and x.imp.decisione.stadio == 1)
    await x.minuti(9)
    b.v("tiene durante il ritardo di rilascio", x.hass.states.get(C1).state == "on")
    await x.minuti(2)
    b.v("poi rilascia", x.hass.states.get(C1).state == "off")

    # -------------------------------------------------------------------------
    b.sezione("7. Blocco e anomalia")
    x = Banco2()
    await x.imp.async_start()
    anomalia = AnomaliaSensor(x.imp)
    await anomalia.async_added_to_hass()
    NOTIFICHE.clear()
    x.hass.stati.imposta(C2, "on")
    b.v("solo contatto 2: stato «blocco» e anomalia accesa",
        x.imp.stato_contatti == "blocco" and anomalia.is_on)
    await asyncio.sleep(0.05)
    await x.entry.attendi_compiti()
    b.v("la sentinella riapre il contatto 2 anche in osservazione", x.hass.states.get(C2).state == "off")
    b.v("l'anomalia resta segnalata con il dettaglio",
        anomalia.is_on and anomalia.extra_state_attributes["tipo"] == "blocco")
    b.v("notifica persistente creata", any("blocco" in (n["notification_id"] or "") for n in NOTIFICHE))
    x.t += 16 * 60
    await x.imp.async_giro()
    b.v("dopo 15 minuti senza nuovi eventi l'anomalia si spegne", anomalia.is_on is False)

    # -------------------------------------------------------------------------
    b.sezione("8. Unita' e convenzioni")
    x = Banco2(rete="-1.2", unita="kW", parametri={"segno_rete": "prelievo_positivo"})
    await x.imp.async_start()
    b.v("-1,2 kW con «prelievo positivo» = immissione di 1200 W", x.imp.decisione.esubero == 1200)
    x.hass.stati.imposta(COMP, "on")
    await x.minuti(1)
    b.v("con il compressore acceso si aggiunge la potenza stimata (1500)", x.imp.decisione.esubero == 2700)

    # -------------------------------------------------------------------------
    b.sezione("9. Rinomina di un'entita' collegata")
    x = Banco2()
    await x.imp.async_start()
    x.hass.bus.spara(finto_ha.EVENT_ENTITY_REGISTRY_UPDATED,
                     {"action": "update", "entity_id": "sensor.scambio_nuovo", "old_entity_id": RETE})
    b.v("le opzioni puntano al nuovo id", x.entry.options.get("entita", {}).get("scambio_rete") == "sensor.scambio_nuovo")
    b.v("le altre entita' restano", x.entry.options["entita"]["contatto_1"] == C1)
    x.hass.bus.spara(finto_ha.EVENT_ENTITY_REGISTRY_UPDATED,
                     {"action": "update", "entity_id": "light.b", "old_entity_id": "light.a"})
    b.v("un'entita' non nostra non tocca le opzioni", len(x.hass.config_entries.aggiornamenti) == 1)

    # -------------------------------------------------------------------------
    b.sezione("10. Modalita' ripristinata dopo il riavvio")
    x = Banco2(c1="on", rete="1500")
    await x.imp.async_start()
    ModalitaSelect.ultimo_stato = State("select.smart_grid_pdc_modalita", "automatico")
    selettore = ModalitaSelect(x.imp)
    await selettore.async_added_to_hass()
    await x.entry.attendi_compiti()
    b.v("la select riporta l'automatico", x.imp.modalita == "automatico" and selettore.current_option == "automatico")
    b.v("e riparte dallo stadio dei contatti senza comandi", x.chiamate == [] and x.imp.decisione.stadio == 1)
    ModalitaSelect.ultimo_stato = State("select.smart_grid_pdc_modalita", "chissa'")
    x2 = Banco2()
    await x2.imp.async_start()
    await ModalitaSelect(x2.imp).async_added_to_hass()
    b.v("uno stato salvato non valido lascia l'osservazione", x2.imp.modalita == "osservazione")
    ModalitaSelect.ultimo_stato = None
    await selettore.async_select_option("spento")
    await x.entry.attendi_compiti()
    b.v("scegliere «spento» dalla select rilascia i contatti", x.imp.modalita == "spento" and x.hass.states.get(C1).state == "off")

    await x.imp.async_stop()
    b.v("allo stop si tolgono giro e ascoltatori", x.hass.intervalli == [] and x.hass.stati.ascoltatori == [])

    # -------------------------------------------------------------------------
    b.sezione("11. Revisione: riavvio con i relè non ancora caricati")
    x = Banco2(rete="0")
    x.hass.stati._stati.pop(C1)
    x.hass.stati._stati.pop(C2)
    x.hass.stati.imposta(COMP, "on")
    await x.imp.async_start()
    await x.imp.async_imposta_modalita("automatico")
    await x.minuti(2)
    b.v("contatti ancora sconosciuti: nessun comando", x.chiamate == [])
    x.hass.stati.imposta(C1, "on")
    x.hass.stati.imposta(C2, "off")
    await x.minuti(1)
    b.v("appena compaiono si riprende il loro stadio (1), senza rilasciare", x.chiamate == []
        and x.imp.decisione.stadio == 1 and x.hass.states.get(C1).state == "on")
    await x.minuti(15)
    b.v("con la pompa in marcia lo stadio resta (tenuta)", x.hass.states.get(C1).state == "on")

    b.sezione("12. Revisione: rilascio uscendo dall'automatico che fallisce")
    x = Banco2(rete="3000", parametri={"stadio_2": True, "soglia_2": 2500})
    await x.imp.async_start()
    await x.imp.async_imposta_modalita("automatico")
    await x.minuti(8)
    b.v("stadio 2 comandato", x.imp.stato_contatti == "comando")
    # L'attesa della conferma deve poter scadere: qui l'attuatore usa l'orologio vero.
    x.imp.attuatore._timeout = 0.05
    x.imp.attuatore._orologio = time.monotonic
    x.hass.services.rele[C2] = finto_ha.Rele(ritardo=None)
    await x.imp.async_imposta_modalita("spento")
    await x.entry.attendi_compiti()
    b.v("il rilascio fallisce: contatti ancora chiusi e rilascio in sospeso",
        x.imp.stato_contatti == "comando" and x.imp.rilascio_pendente)
    x.hass.services.rele[C2] = finto_ha.Rele()
    await x.minuti(20)
    b.v("in spento si riprova finche' i contatti tornano a normale",
        x.imp.stato_contatti == "normale" and not x.imp.rilascio_pendente)
    x = Banco2(c1="on", rete="0")
    await x.imp.async_start()
    ModalitaSelect.ultimo_stato = State("select.smart_grid_pdc_modalita", "osservazione", {"rilascio_pendente": True})
    await ModalitaSelect(x.imp).async_added_to_hass()
    ModalitaSelect.ultimo_stato = None
    await x.minuti(1)
    b.v("un rilascio in sospeso prima del riavvio viene portato a termine", x.hass.states.get(C1).state == "off")

    b.sezione("13. Revisione: letture della pompa e notifiche")
    x = Banco2(rete="-300")
    x.hass.stati.imposta(COMP, "on")
    await x.imp.async_start()
    await x.minuti(1)
    b.v("compressore acceso: esubero lordo 1200", x.imp.decisione.esubero == 1200)
    x.hass.stati.imposta(COMP, "unavailable")
    await x.minuti(5)
    b.v("compressore illeggibile da poco: vale l'ultima lettura", x.imp.decisione.esubero == 1200)
    await x.minuti(6)
    b.v("dopo 10 minuti non si inventa piu' niente", x.imp.decisione.esubero == -300)

    x = Banco2()
    await x.imp.async_start()
    NOTIFICHE.clear()
    x.imp.segnala("conferma", "primo")
    x.imp.segnala("conferma", "secondo")
    b.v("stessa anomalia a raffica: una sola notifica", len(NOTIFICHE) == 1)
    b.v("ma il sensore ha il dettaglio piu' recente", x.imp.anomalia["dettaglio"] == "secondo")

    x = Banco2(rete="1500")
    await x.imp.async_start()
    await x.minuti(4)
    b.v("il perche' dell'ultimo cambio di stadio resta negli attributi",
        "sopra" in (x.imp.attributi["ultimo_cambio"] or ""))
    b.v("gli attributi che cambiano a ogni giro non vanno nel database",
        {"motivo", "esubero_lordo"} <= StadioRichiestoSensor._unrecorded_attributes
        and "ultimo_cambio" not in StadioRichiestoSensor._unrecorded_attributes)

    # La modalita' decide se l'impianto comanda o guarda: un comando vocale
    # generico non deve poterla toccare. La categoria e' cio' che la tiene
    # fuori dagli assistenti.
    b.v("la modalita' ha categoria «config», quindi non e' esposta a voce",
        getattr(ModalitaSelect, "_attr_entity_category", None) == "config")

    b.fine()


asyncio.run(main())
