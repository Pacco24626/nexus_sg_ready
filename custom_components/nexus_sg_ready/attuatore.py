"""I due contatti SG: comandarli nell'ordine sicuro e sorvegliarli.

Solo il contatto 2 chiuso e' il «blocco» (sull'Ecodan: niente riscaldamento,
niente acqua calda, niente antilegionella). L'integrazione non lo chiede mai, e
questo modulo fa in modo che non ci si passi nemmeno per un attimo:

- per salire si chiude il contatto 1 e si ASPETTA che Home Assistant lo veda
  chiuso, solo dopo si chiude il 2;
- per scendere si apre il 2, si aspetta che risulti aperto, poi si apre l'1;
- se un contatto non conferma entro il tempo previsto ci si ferma li' e si
  segnala, invece di proseguire alla cieca;
- una sentinella guarda i contatti sempre, qualunque sia la modalita': se trova
  il 2 chiuso con l'1 aperto (per un comando nostro, di un altro sistema o a
  mano) apre il 2 dopo pochi secondi.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import GRAZIA_BLOCCO, GRAZIA_BLOCCO_INCERTO, STATI_CONTATTI, TIMEOUT_CONFERMA
from .logica import acceso, stato_contatti

_LOGGER = logging.getLogger(__name__)

# Tetto dell'attesa fra due correzioni del blocco fallite (o ripetute) di fila.
ATTESA_MASSIMA = 600
# Un blocco che torna piu' di RIPETIZIONI_MASSIME volte in FINESTRA_RIPETIZIONI
# secondi (qualcuno o qualcosa continua a richiudere il contatto 2) si corregge
# con attese crescenti, come un relè guasto.
FINESTRA_RIPETIZIONI = 600
RIPETIZIONI_MASSIME = 3


class Attuatore:
    """Comanda e sorveglia i contatti di una voce."""

    def __init__(
        self,
        hass: HomeAssistant,
        contatto_1: str,
        contatto_2: str | None,
        *,
        correggi_blocco: bool,
        crea_attivita: Callable[[Coroutine[Any, Any, Any], str], Any],
        al_cambio: Callable[[], None],
        segnala: Callable[[str, str], None],
        orologio: Callable[[], float],
        timeout: float = TIMEOUT_CONFERMA,
        grazia: float = GRAZIA_BLOCCO,
        grazia_incerta: float = GRAZIA_BLOCCO_INCERTO,
    ) -> None:
        self.hass = hass
        self.contatto_1 = contatto_1
        self.contatto_2 = contatto_2
        self._correggi_blocco = correggi_blocco
        self._crea_attivita = crea_attivita
        self._al_cambio = al_cambio
        self._segnala = segnala
        self._orologio = orologio
        self._timeout = timeout
        self._grazia = grazia
        self._grazia_incerta = grazia_incerta

        self._lock = asyncio.Lock()
        self._in_comando = 0
        self._visto = asyncio.Event()
        self._correzione_in_attesa = False
        self._fallimenti = 0
        self._correzioni: list[float] = []
        self._fermo = False
        self._annulla: list[Callable[[], None]] = []
        self.ultimo_cambio_esterno: float | None = None

    # -------------------------------------------------------------------------
    @property
    def contatti(self) -> list[str]:
        return [e for e in (self.contatto_1, self.contatto_2) if e]

    @property
    def occupato(self) -> bool:
        return self._lock.locked()

    def _grezzo(self, entity_id: str | None) -> str | None:
        if not entity_id:
            return None
        stato = self.hass.states.get(entity_id)
        return None if stato is None else stato.state

    def stato(self) -> str | None:
        """normale / consigliato / comando / blocco, o None se non si puo' dire."""
        return stato_contatti(
            self._grezzo(self.contatto_1), self._grezzo(self.contatto_2), self.contatto_2 is not None
        )

    def stadio(self) -> int | None:
        """0, 1 o 2 se i contatti sono in uno stato valido; None altrimenti."""
        stato = self.stato()
        if stato is None or stato == "blocco":
            return None
        return STATI_CONTATTI.index(stato)

    # -------------------------------------------------------------------------
    async def async_start(self) -> None:
        self._annulla.append(async_track_state_change_event(self.hass, self.contatti, self._al_cambio_contatti))
        self._controlla_blocco()

    async def async_stop(self) -> None:
        # Dopo lo scaricamento nessuna correzione deve ripartire da sola: HA ha
        # gia' annullato i compiti della voce e non vedrebbe quelli nuovi.
        self._fermo = True
        while self._annulla:
            self._annulla.pop()()

    # -------------------------------------------------------------------------
    async def porta_a(self, stadio: int) -> bool:
        """Porta i contatti allo stadio chiesto, uno scalino alla volta.

        True se ci arriva; False se un contatto non conferma (e si segnala).
        """
        if self.contatto_2 is None:
            stadio = min(stadio, 1)
        stadio = max(0, min(2, stadio))
        async with self._lock:
            # Al piu' quattro comandi servono davvero (2 -> blocco corretto -> ...):
            # il limite evita un giro infinito se un contatto torna indietro da solo.
            for _ in range(6):
                uno = acceso(self._grezzo(self.contatto_1))
                due = acceso(self._grezzo(self.contatto_2)) if self.contatto_2 else False

                if self.contatto_2 and due is True and uno is not True:
                    entita, voluto = self.contatto_2, False  # blocco: prima di tutto
                elif stadio >= 1 and uno is not True:
                    entita, voluto = self.contatto_1, True
                elif stadio == 2 and due is not True:
                    entita, voluto = self.contatto_2, True  # qui il contatto 1 risulta chiuso
                elif stadio <= 1 and self.contatto_2 and due is not False:
                    entita, voluto = self.contatto_2, False
                elif stadio == 0 and uno is not False:
                    # Si arriva qui solo con il contatto 2 confermato aperto (o assente).
                    entita, voluto = self.contatto_1, False
                else:
                    return True

                if not await self._comanda(entita, voluto):
                    self._segnala(
                        "conferma",
                        f"{entita} non ha confermato «{'on' if voluto else 'off'}» entro "
                        f"{self._timeout:g} s: comando fermato per sicurezza.",
                    )
                    return False
            return self.stadio() == stadio

    async def _comanda(self, entity_id: str, voluto: bool) -> bool:
        servizio = "turn_on" if voluto else "turn_off"
        atteso = "on" if voluto else "off"
        self._in_comando += 1
        try:
            if self._grezzo(entity_id) == atteso:
                return True
            try:
                await self.hass.services.async_call(
                    entity_id.partition(".")[0], servizio, {"entity_id": entity_id}, blocking=True
                )
            except Exception as err:  # noqa: BLE001 - qualunque errore del relè va solo segnalato
                _LOGGER.warning("Comando %s su %s fallito: %s", servizio, entity_id, err)
                return False
            return await self._attendi(entity_id, atteso)
        finally:
            self._in_comando -= 1

    async def _attendi(self, entity_id: str, atteso: str) -> bool:
        """Aspetta che Home Assistant veda il contatto nello stato voluto."""
        fine = self._orologio() + self._timeout
        while True:
            if self._grezzo(entity_id) == atteso:
                return True
            resto = fine - self._orologio()
            if resto <= 0:
                return False
            self._visto.clear()
            try:
                await asyncio.wait_for(self._visto.wait(), timeout=min(resto, 1.0))
            except TimeoutError:
                pass

    # -------------------------------------------------------------------------
    @callback
    def _al_cambio_contatti(self, evento: Event) -> None:
        self._visto.set()
        # Conta come «cambiato da fuori» solo un vero passaggio acceso <-> spento
        # fatto mentre non stiamo comandando: non la comparsa di un'entita'
        # all'avvio, non un passaggio da o verso «unavailable».
        vecchio, nuovo = evento.data.get("old_state"), evento.data.get("new_state")
        prima = acceso(vecchio.state) if vecchio is not None else None
        dopo = acceso(nuovo.state) if nuovo is not None else None
        if not self._in_comando and prima is not None and dopo is not None and prima != dopo:
            self.ultimo_cambio_esterno = self._orologio()
        if self.stato() != "blocco":
            self._fallimenti = 0
        self._controlla_blocco()
        self._al_cambio()

    @callback
    def _controlla_blocco(self) -> None:
        if self._fermo or not self._correggi_blocco or self._correzione_in_attesa or self.stato() != "blocco":
            return
        # Contatto 1 aperto: e' un blocco vero, si corregge subito dopo la grazia.
        # Contatto 1 non leggibile (per esempio all'avvio): si aspetta di piu'.
        # Dopo una correzione fallita l'attesa raddoppia a ogni tentativo, fino a
        # ATTESA_MASSIMA: un relè guasto non deve riempire registro e notifiche.
        certo = acceso(self._grezzo(self.contatto_1)) is False
        livello = max(self._fallimenti, self._ripetizioni())
        if livello:
            attesa = min(self._grazia_incerta * 2 ** (livello - 1), ATTESA_MASSIMA)
        else:
            attesa = self._grazia if certo else self._grazia_incerta
        self._correzione_in_attesa = True
        self._crea_attivita(
            self._correggi_dopo(attesa),
            "nexus_sg_ready correzione blocco",
        )

    def _ripetizioni(self) -> int:
        """Quante correzioni riuscite oltre il limite nella finestra: 0 se poche."""
        adesso = self._orologio()
        self._correzioni = [t for t in self._correzioni if adesso - t < FINESTRA_RIPETIZIONI]
        return max(0, len(self._correzioni) - RIPETIZIONI_MASSIME + 1)

    async def _correggi_dopo(self, attesa: float) -> None:
        annullato = False
        try:
            await asyncio.sleep(attesa)
            if self.stato() != "blocco" or not self.contatto_2:
                self._fallimenti = 0
                return
            _LOGGER.warning("Contatto 2 chiuso con il contatto 1 aperto (blocco della macchina): apro il contatto 2")
            ok = await self._comanda(self.contatto_2, False)
            if ok:
                self._fallimenti = 0
                self._correzioni.append(self._orologio())
            else:
                self._fallimenti += 1
            ripetuto = self._ripetizioni() > 0
            self._segnala(
                "blocco",
                "Trovato il solo contatto 2 chiuso, cioe' il blocco della macchina: "
                + ("contatto 2 riaperto." if ok else "NON sono riuscito a riaprire il contatto 2, controlla il relè.")
                + (" Il blocco si ripresenta: qualcosa continua a richiudere il contatto 2." if ripetuto else ""),
            )
        except asyncio.CancelledError:
            annullato = True
            raise
        finally:
            self._correzione_in_attesa = False
            # Se nel frattempo e' tornato il blocco, si riprogramma (non dopo uno
            # stop o un annullamento: la voce e' stata scaricata).
            if not annullato and self.stato() == "blocco":
                self._controlla_blocco()
