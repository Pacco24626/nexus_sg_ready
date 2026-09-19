"""L'impianto: legge le misure, decide lo stadio e, in automatico, comanda i contatti.

Ogni INTERVALLO_SECONDI legge scambio con la rete, pompa di calore e bollitore,
chiede al Decisore lo stadio e lo confronta con quello dei contatti. In
«automatico» li allinea con l'Attuatore; in «osservazione» decide e scrive il
perche' senza toccarli; in «spento» non chiede niente. La sentinella del blocco
lavora in tutte e tre.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Coroutine
from datetime import timedelta
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_registry import EVENT_ENTITY_REGISTRY_UPDATED
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .attuatore import Attuatore
from .const import (
    ATTESA_RIPROVA,
    AUTOMATICO,
    CONF_ENTITA,
    CONF_PARAMETRI,
    DOMAIN,
    INTERVALLO_SECONDI,
    MODALITA,
    MODALITA_PREDEFINITA,
    SPENTO,
    STADI,
)
from .logica import (
    PREDEFINITI,
    Decisione,
    Decisore,
    Ingressi,
    acceso,
    immissione,
    numero,
    rinomina,
    watt,
)

_LOGGER = logging.getLogger(__name__)

# Un'anomalia resta segnalata finche' un comando va a buon fine, o per questo
# tempo senza nuovi eventi quando l'integrazione non comanda.
DURATA_ANOMALIA = 15 * 60
# Avviso per i contatti cambiati da un altro sistema: al piu' uno ogni ora.
INTERVALLO_AVVISO_ESTERNO = 60 * 60
# Notifica della stessa anomalia: al piu' una ogni dieci minuti.
INTERVALLO_NOTIFICA = 10 * 60
# Compressore o potenza della pompa illeggibili per poco (rete, riavvio del
# gateway): si usa l'ultima lettura valida per questo tempo, invece di credere
# la pompa ferma e rilasciare a meta' ciclo.
GRAZIA_LETTURE = 10 * 60


class Impianto:
    """Una voce configurata: una pompa di calore con i suoi due contatti."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.modalita = MODALITA_PREDEFINITA
        self.decisore = Decisore(parametri=self.parametri)
        self.decisione: Decisione | None = None
        self.stato_contatti: str | None = None
        self.anomalia: dict[str, Any] | None = None

        # Orologi separati, cosi' le prove a tavolino possono guidarli.
        self._orologio: Callable[[], float] = time.monotonic
        self._ora_locale: Callable[[], int] = _minuti_adesso

        entita = self.entita
        self.attuatore = Attuatore(
            hass,
            entita["contatto_1"],
            entita.get("contatto_2"),
            correggi_blocco=bool(self.parametri["correggi_blocco"]),
            crea_attivita=self._crea_attivita,
            al_cambio=self._al_cambio_contatti,
            segnala=self.segnala,
            orologio=lambda: self._orologio(),
        )

        self._ascoltatori: list[Callable[[], None]] = []
        self._annulla: list[Callable[[], None]] = []
        self._ripreso = False
        self._ultimo_obiettivo: int | None = None
        self._ultimo_tentativo: float | None = None
        self._ultimo_avviso_esterno: float | None = None
        self._esterno_visto: float | None = None
        self._ultime_notifiche: dict[str, float] = {}
        self._ultima_lettura: dict[str, tuple[Any, float]] = {}
        # Uscendo dall'automatico i contatti vanno riportati a normale finche'
        # non ci arrivano davvero: un solo tentativo puo' fallire o essere
        # interrotto da un ricaricamento. Sopravvive ai riavvii tramite la select.
        self.rilascio_pendente = False
        self._ultimo_rilascio: float | None = None
        # Il perche' dell'ultimo cambio di stadio: resta nello storico, a
        # differenza del motivo di ogni giro.
        self.ultimo_cambio: str | None = None

    # -------------------------------------------------------------------------
    @property
    def configurazione(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    @property
    def entita(self) -> dict[str, str]:
        return dict(self.configurazione.get(CONF_ENTITA) or {})

    @property
    def parametri(self) -> dict[str, Any]:
        return {**PREDEFINITI, **(self.configurazione.get(CONF_PARAMETRI) or {})}

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name=self.entry.title,
            manufacturer="Automatic Systems",
            model="Smart Grid Ready",
        )

    @property
    def attributi(self) -> dict[str, Any]:
        """Il perche' della decisione, per il sensore «Stadio richiesto». Oggetti nuovi
        a ogni lettura: HA confronta gli attributi con una copia superficiale."""
        decisione = self.decisione
        return {
            "motivo": decisione.motivo if decisione else None,
            "modalita": self.modalita,
            "esubero_lordo": decisione.esubero if decisione else None,
            "potenza_pdc": decisione.potenza_pdc if decisione else None,
            "pompa_in_marcia": decisione.in_marcia if decisione else None,
            "tenuta": decisione.tenuta if decisione else None,
            "stato_contatti": self.stato_contatti,
            "ultimo_cambio": self.ultimo_cambio,
            "rilascio_pendente": self.rilascio_pendente,
        }

    # -------------------------------------------------------------------------
    async def async_start(self) -> None:
        await self.attuatore.async_start()
        self._annulla.append(
            async_track_time_interval(self.hass, self._async_giro, timedelta(seconds=INTERVALLO_SECONDI))
        )
        self._annulla.append(self.hass.bus.async_listen(EVENT_ENTITY_REGISTRY_UPDATED, self._al_registro))
        await self.async_giro()

    async def async_stop(self) -> None:
        while self._annulla:
            self._annulla.pop()()
        await self.attuatore.async_stop()

    @callback
    def async_add_listener(self, azione: Callable[[], None]) -> Callable[[], None]:
        self._ascoltatori.append(azione)

        def rimuovi() -> None:
            if azione in self._ascoltatori:
                self._ascoltatori.remove(azione)

        return rimuovi

    def _avvisa(self) -> None:
        for azione in list(self._ascoltatori):
            azione()

    def _crea_attivita(self, coro: Coroutine[Any, Any, Any], nome: str) -> Any:
        return self.entry.async_create_background_task(self.hass, coro, nome)

    # -------------------------------------------------------------------------
    async def async_imposta_modalita(self, modalita: str) -> None:
        """Cambio di modalita' dalla select. Uscendo dall'automatico si rilascia."""
        if modalita not in MODALITA or modalita == self.modalita:
            return
        vecchia = self.modalita
        self.modalita = modalita
        _LOGGER.info("%s: modalita' %s -> %s", self.entry.title, vecchia, modalita)
        if vecchia == AUTOMATICO:
            self.rilascio_pendente = True
            self._ultimo_rilascio = None
        if modalita == AUTOMATICO:
            self._ripreso = False
            self.rilascio_pendente = False
        self._ultimo_obiettivo = None
        await self.async_giro()

    # -------------------------------------------------------------------------
    def _grezzo(self, chiave: str) -> tuple[str | None, Any]:
        entity_id = self.entita.get(chiave)
        stato = self.hass.states.get(entity_id) if entity_id else None
        if stato is None:
            return None, None
        return stato.state, stato.attributes.get("unit_of_measurement")

    def _con_grazia(self, chiave: str, valore: Any, adesso: float) -> Any:
        """L'ultima lettura valida, se quella di adesso manca da poco."""
        if valore is not None:
            self._ultima_lettura[chiave] = (valore, adesso)
            return valore
        ultima = self._ultima_lettura.get(chiave)
        if ultima is not None and adesso - ultima[1] < GRAZIA_LETTURE:
            return ultima[0]
        return None

    def _leggi(self, adesso: float) -> Ingressi:
        entita = self.entita
        scambio, unita = self._grezzo("scambio_rete")
        potenza, unita_pdc = self._grezzo("potenza_pdc")
        compressore, _ = self._grezzo("compressore")
        acs, _ = self._grezzo("temperatura_acs")
        return Ingressi(
            immissione=immissione(watt(scambio, unita), str(self.parametri["segno_rete"])),
            potenza_pdc=self._con_grazia("potenza_pdc", watt(potenza, unita_pdc), adesso)
            if "potenza_pdc" in entita
            else None,
            compressore=self._con_grazia("compressore", acceso(compressore), adesso)
            if "compressore" in entita
            else None,
            acs=numero(acs) if "temperatura_acs" in entita else None,
            minuti=self._ora_locale(),
            adesso=adesso,
        )

    async def _async_giro(self, _ora: Any = None) -> None:
        await self.async_giro()

    async def async_giro(self) -> None:
        """Un giro: leggi, decidi, pubblica e (in automatico) allinea i contatti."""
        adesso = self._orologio()
        stato = self.attuatore.stato()

        # Entrando in automatico (anche dopo un riavvio) non si rilascia di colpo
        # uno stadio che i contatti hanno gia': se sono piu' in alto della
        # decisione si riparte da loro. Se invece la decisione e' gia' piu' in
        # alto (maturata in osservazione) si tiene quella.
        if self.modalita == AUTOMATICO and not self._ripreso:
            stadio_contatti = self.attuatore.stadio()
            if stadio_contatti is not None:
                if stadio_contatti > self.decisore.stadio:
                    self.decisore.riprendi(stadio_contatti, adesso)
                self._ripreso = True

        ingressi = self._leggi(adesso)
        if self.modalita == SPENTO:
            self.decisore.imposta(0, adesso)
            decisione = self.decisore.valuta(ingressi, spento=True)
        else:
            decisione = self.decisore.valuta(ingressi)

        if self.decisione is not None and decisione.stadio != self.decisione.stadio:
            self.ultimo_cambio = f"{dt_util.now().strftime('%H:%M')} {decisione.motivo}"

        self._scadenza_anomalia(adesso)
        cambiato = (
            self.decisione is None
            or (decisione.stadio, decisione.motivo, decisione.esubero, decisione.tenuta)
            != (self.decisione.stadio, self.decisione.motivo, self.decisione.esubero, self.decisione.tenuta)
            or stato != self.stato_contatti
        )
        self.decisione = decisione
        self.stato_contatti = stato
        if cambiato:
            self._avvisa()

        if self.modalita == AUTOMATICO:
            self._allinea(decisione.stadio, adesso)
        elif self.rilascio_pendente:
            self._rilascia(adesso)

    def _rilascia(self, adesso: float) -> None:
        """Fuori dall'automatico: riporta i contatti a normale, riprovando finche' serve."""
        stadio = self.attuatore.stadio()
        if stadio == 0:
            self.rilascio_pendente = False
            self._avvisa()
            return
        # Contatti illeggibili: si aspetta di vederli (il blocco lo gestisce la sentinella).
        if self.attuatore.stato() in (None, "blocco") or self.attuatore.occupato:
            return
        if self._ultimo_rilascio is not None and adesso - self._ultimo_rilascio < ATTESA_RIPROVA:
            return
        self._ultimo_rilascio = adesso
        self._crea_attivita(self._async_porta_a(0), "nexus_sg_ready rilascio")

    def _allinea(self, stadio: int, adesso: float) -> None:
        if self.attuatore.contatto_2 is None:
            stadio = min(stadio, 1)
        # Si comanda solo dopo aver letto i contatti e ripreso il loro stadio: con
        # i relè ancora da caricare (avvio di HA, Yubii che si riconnette) si
        # rilascerebbe uno stadio attivo. Un blocco lo corregge la sentinella.
        if not self._ripreso or self.attuatore.stato() is None:
            return
        if self.attuatore.occupato or self.attuatore.stadio() == stadio:
            return
        # Contatti appena cambiati da un altro sistema: si aspetta prima di
        # rimetterli, altrimenti i due sistemi si rincorrono.
        esterno = self.attuatore.ultimo_cambio_esterno
        if esterno is not None and adesso - esterno < ATTESA_RIPROVA:
            return
        # Stesso obiettivo di un tentativo recente: vuol dire che e' fallito.
        # Si riprova, ma senza martellare.
        if (
            stadio == self._ultimo_obiettivo
            and self._ultimo_tentativo is not None
            and adesso - self._ultimo_tentativo < ATTESA_RIPROVA
        ):
            return
        self._ultimo_obiettivo = stadio
        self._ultimo_tentativo = adesso
        _LOGGER.debug("%s: porto i contatti allo stadio %s", self.entry.title, stadio)
        self._crea_attivita(self._async_porta_a(stadio), "nexus_sg_ready comando")

    async def _async_porta_a(self, stadio: int) -> None:
        if await self.attuatore.porta_a(stadio):
            if self.anomalia and self.anomalia.get("tipo") == "conferma":
                self.anomalia = None
            if stadio == 0 and self.modalita != AUTOMATICO:
                self.rilascio_pendente = False
            self._avvisa()

    # -------------------------------------------------------------------------
    @callback
    def _al_cambio_contatti(self) -> None:
        stato = self.attuatore.stato()
        esterno = self.attuatore.ultimo_cambio_esterno
        if (
            self.modalita == AUTOMATICO
            and esterno is not None
            and esterno != self._esterno_visto
        ):
            self._esterno_visto = esterno
            self._avviso_esterno(esterno)
        if stato != self.stato_contatti:
            self.stato_contatti = stato
            self._avvisa()

    def _avviso_esterno(self, adesso: float) -> None:
        if self._ultimo_avviso_esterno is not None and adesso - self._ultimo_avviso_esterno < INTERVALLO_AVVISO_ESTERNO:
            _LOGGER.debug("%s: contatti di nuovo cambiati da fuori", self.entry.title)
            return
        _LOGGER.warning(
            "%s: i contatti SG sono stati cambiati da un altro sistema mentre l'integrazione e' in automatico",
            self.entry.title,
        )
        self._ultimo_avviso_esterno = adesso
        persistent_notification.async_create(
            self.hass,
            "I contatti SG sono stati cambiati da un altro sistema mentre Nexus SG Ready e' in "
            "automatico. Se c'e' ancora una scena o un'automazione che li comanda (per esempio "
            "sullo Yubii), disattivala: altrimenti i due sistemi si contendono i relè.",
            title=f"{self.entry.title}: contatti comandati da fuori",
            notification_id=f"{DOMAIN}_{self.entry.entry_id}_esterno",
        )

    @callback
    def segnala(self, tipo: str, dettaglio: str) -> None:
        """Un'anomalia dei contatti: sensore problema, registro e notifica."""
        adesso = self._orologio()
        self.anomalia = {"tipo": tipo, "dettaglio": dettaglio, "quando": dt_util.now().isoformat(), "_alle": adesso}
        ultima = self._ultime_notifiche.get(tipo)
        if ultima is not None and adesso - ultima < INTERVALLO_NOTIFICA:
            # Stessa anomalia a raffica: il sensore si aggiorna, la notifica no.
            _LOGGER.debug("%s: %s", self.entry.title, dettaglio)
        else:
            self._ultime_notifiche[tipo] = adesso
            _LOGGER.warning("%s: %s", self.entry.title, dettaglio)
            persistent_notification.async_create(
                self.hass,
                dettaglio,
                title=f"{self.entry.title}: anomalia dei contatti SG",
                notification_id=f"{DOMAIN}_{self.entry.entry_id}_{tipo}",
            )
        self._avvisa()

    def _scadenza_anomalia(self, adesso: float) -> None:
        if self.anomalia is None:
            return
        if self.attuatore.stato() == "blocco":
            return
        # Un rilascio che non riesce resta segnalato finche' i contatti non tornano a normale.
        if self.rilascio_pendente and self.attuatore.stadio() != 0:
            return
        if adesso - float(self.anomalia.get("_alle", adesso)) >= DURATA_ANOMALIA:
            self.anomalia = None
            self._avvisa()

    # -------------------------------------------------------------------------
    @callback
    def _al_registro(self, evento: Event) -> None:
        """Un'entita' collegata rinominata: si aggiorna il collegamento, non si perde."""
        dati = evento.data
        if dati.get("action") != "update" or "old_entity_id" not in dati:
            return
        nuove = rinomina(self.entita, dati["old_entity_id"], dati["entity_id"])
        if nuove is None:
            return
        # Le opzioni vincono sui dati: basta scriverle, il ricaricamento lo fa
        # l'ascoltatore degli aggiornamenti registrato in __init__.
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, CONF_ENTITA: nuove}
        )


def _minuti_adesso() -> int:
    ora = dt_util.now()
    return ora.hour * 60 + ora.minute


__all__ = ["Impianto", "STADI"]
