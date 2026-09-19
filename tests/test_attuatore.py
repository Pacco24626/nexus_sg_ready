"""Prove a tavolino dell'attuatore: ordine dei comandi, conferme e sentinella del blocco.

Relè simulati: rispondono subito, in ritardo, mai, o danno errore. Un
osservatore registra ogni stato dei contatti: se compare anche per un attimo il
solo contatto 2 chiuso, la prova lo vede.

    python tests/test_attuatore.py
"""

from __future__ import annotations

import asyncio
import time

import finto_ha
from finto_ha import Banco, Hass, Rele

from nexus_sg_ready.attuatore import Attuatore
from nexus_sg_ready.logica import stato_contatti

b = Banco("ATTUATORE")

C1 = "switch.cantina_sg_canale_1_430"
C2 = "switch.cantina_sg_canale_2_431"


class Prova:
    """Un attuatore su un Home Assistant finto, con l'osservatore dei contatti."""

    def __init__(self, c1="off", c2="off", *, con_2=True, correggi=True, timeout=0.3, grazia=0.05,
                 grazia_incerta=0.3) -> None:
        self.hass = Hass()
        self.hass.stati.imposta(C1, c1)
        if con_2:
            self.hass.stati.imposta(C2, c2)
        self.segnalazioni: list[tuple[str, str]] = []
        self.visti: list[str | None] = []
        self.compiti: list = []
        self.a = Attuatore(
            self.hass,
            C1,
            C2 if con_2 else None,
            correggi_blocco=correggi,
            crea_attivita=lambda coro, nome: self.compiti.append(asyncio.get_running_loop().create_task(coro)),
            al_cambio=self._al_cambio,
            segnala=lambda tipo, testo: self.segnalazioni.append((tipo, testo)),
            orologio=time.monotonic,
            timeout=timeout,
            grazia=grazia,
            grazia_incerta=grazia_incerta,
        )
        self.con_2 = con_2

    def _al_cambio(self) -> None:
        s1 = self.hass.states.get(C1)
        s2 = self.hass.states.get(C2)
        self.visti.append(stato_contatti(s1.state if s1 else None, s2.state if s2 else None, self.con_2))

    @property
    def chiamate(self):
        return [(servizio, entita) for _, servizio, entita in self.hass.services.chiamate]

    async def attendi(self, secondi: float = 0.0) -> None:
        await asyncio.sleep(secondi)
        while any(not c.done() for c in self.compiti):
            await asyncio.gather(*[c for c in self.compiti if not c.done()], return_exceptions=True)


async def main():
    # -------------------------------------------------------------------------
    b.sezione("1. Salita e discesa nell'ordine sicuro")
    p = Prova()
    await p.a.async_start()
    ok = await p.a.porta_a(2)
    b.v("0 -> 2 riuscito", ok and p.a.stato() == "comando")
    b.v("prima il contatto 1, poi il 2", p.chiamate == [("turn_on", C1), ("turn_on", C2)])
    b.v("il blocco non compare mai durante la salita", "blocco" not in p.visti)
    ok = await p.a.porta_a(0)
    b.v("2 -> 0 riuscito", ok and p.a.stato() == "normale")
    b.v("in discesa prima il 2, poi l'1", p.chiamate[2:] == [("turn_off", C2), ("turn_off", C1)])
    b.v("il blocco non compare mai durante la discesa", "blocco" not in p.visti)
    b.v("nessuna segnalazione", p.segnalazioni == [])
    b.v("i nostri comandi non contano come cambi esterni", p.a.ultimo_cambio_esterno is None)

    # Controllo di sanita' dell'osservatore: l'ordine sbagliato (2 poi 1) lo fa scattare.
    p = Prova(correggi=False)
    await p.a.async_start()
    p.hass.stati.imposta(C2, "on")
    p.hass.stati.imposta(C1, "on")
    b.v("sanita': l'osservatore vede il blocco se l'ordine e' sbagliato", "blocco" in p.visti)

    # -------------------------------------------------------------------------
    b.sezione("2. Relè lento: si aspetta la conferma")
    p = Prova()
    p.hass.services.rele[C1] = Rele(ritardo=0.1)
    await p.a.async_start()
    inizio = time.monotonic()
    ok = await p.a.porta_a(2)
    b.v("arriva allo stadio 2", ok and p.a.stato() == "comando")
    b.v("ha aspettato il contatto 1 (almeno 0,1 s)", time.monotonic() - inizio >= 0.09)
    b.v("mai il blocco, anche con il contatto 1 in ritardo", "blocco" not in p.visti)

    # -------------------------------------------------------------------------
    b.sezione("3. Relè che non risponde: ci si ferma")
    p = Prova()
    p.hass.services.rele[C1] = Rele(ritardo=None)
    await p.a.async_start()
    ok = await p.a.porta_a(2)
    b.v("porta_a restituisce False", ok is False)
    b.v("il contatto 2 non viene nemmeno comandato", ("turn_on", C2) not in p.chiamate)
    b.v("segnalazione di conferma mancante", [t for t, _ in p.segnalazioni] == ["conferma"])

    p = Prova()
    p.hass.services.rele[C1] = Rele(errore=True)
    await p.a.async_start()
    ok = await p.a.porta_a(1)
    b.v("errore del servizio: False e segnalazione", ok is False and p.segnalazioni[0][0] == "conferma")

    # Discesa con il contatto 2 che non conferma l'apertura: l'1 resta chiuso.
    p = Prova("on", "on")
    p.hass.services.rele[C2] = Rele(ritardo=None)
    await p.a.async_start()
    ok = await p.a.porta_a(0)
    b.v("contatto 2 bloccato chiuso: l'1 NON si apre (sarebbe il blocco)",
        ok is False and ("turn_off", C1) not in p.chiamate and p.a.stato() == "comando")

    # Contatto 2 illeggibile: prima di aprire l'1 si prova ad aprire il 2.
    p = Prova("on", "unavailable")
    p.hass.services.rele[C2] = Rele(ritardo=None)
    await p.a.async_start()
    ok = await p.a.porta_a(0)
    b.v("contatto 2 illeggibile: si comanda il 2 e, senza conferma, l'1 resta chiuso",
        ok is False and p.chiamate == [("turn_off", C2)])

    # -------------------------------------------------------------------------
    b.sezione("4. Sentinella del blocco")
    p = Prova()
    await p.a.async_start()
    p.hass.stati.imposta(C2, "on")  # qualcuno chiude il solo contatto 2
    b.v("cambio fatto da fuori: registrato", p.a.ultimo_cambio_esterno is not None)
    await asyncio.sleep(0.01)
    b.v("dentro la grazia non si tocca ancora", p.chiamate == [])
    await p.attendi(0.1)
    b.v("dopo la grazia il contatto 2 viene riaperto", p.chiamate == [("turn_off", C2)] and p.a.stato() == "normale")
    b.v("e il blocco viene segnalato", [t for t, _ in p.segnalazioni] == ["blocco"])

    p = Prova(correggi=False)
    await p.a.async_start()
    p.hass.stati.imposta(C2, "on")
    await p.attendi(0.1)
    b.v("con la correzione spenta non si interviene", p.chiamate == [] and p.a.stato() == "blocco")

    p = Prova()
    await p.a.async_start()
    p.hass.stati.imposta(C1, "unavailable")
    p.hass.stati.imposta(C2, "on")
    await asyncio.sleep(0.1)
    b.v("contatto 1 illeggibile: si aspetta la grazia lunga", p.chiamate == [])
    await p.attendi(0.35)
    b.v("... poi si corregge comunque", p.chiamate == [("turn_off", C2)])

    p = Prova(c1="off", c2="on")
    await p.a.async_start()
    await p.attendi(0.1)
    b.v("blocco gia' presente all'avvio: corretto", p.chiamate == [("turn_off", C2)])

    p = Prova()
    p.hass.services.rele[C2] = Rele(errore=True)
    await p.a.async_start()
    p.hass.stati.imposta(C2, "on")
    await asyncio.sleep(0.1)
    b.v("correzione fallita: segnalata come tale",
        len(p.segnalazioni) == 1 and "NON sono riuscito" in p.segnalazioni[0][1])
    tentativi = len(p.chiamate)
    await asyncio.sleep(0.1)
    b.v("dopo un fallimento il nuovo tentativo aspetta la grazia lunga", len(p.chiamate) == tentativi)
    await asyncio.sleep(0.3)
    b.v("... e poi riprova (0,3 s)", len(p.chiamate) == tentativi + 1)
    await asyncio.sleep(0.25)
    b.v("il terzo tentativo aspetta il doppio (0,6 s): non ancora", len(p.chiamate) == tentativi + 1)
    await asyncio.sleep(0.4)
    b.v("... ora si'", len(p.chiamate) == tentativi + 2)
    p.hass.services.rele[C2] = Rele()
    await asyncio.sleep(1.3)
    b.v("relè riparato: al tentativo dopo il blocco e' corretto", p.a.stato() == "normale")
    b.v("e i tentativi si fermano", p.a._fallimenti == 0)
    for compito in p.compiti:
        compito.cancel()
    await asyncio.sleep(0)

    # -------------------------------------------------------------------------
    b.sezione("4b. Revisione: cambi esterni, fallimenti, ripetizioni, stop")
    p = Prova("unavailable", "unavailable")
    await p.a.async_start()
    p.hass.stati.imposta(C1, "on")
    p.hass.stati.imposta(C2, "off")
    b.v("relè che ricompaiono (da «unavailable»): non sono un cambio esterno", p.a.ultimo_cambio_esterno is None)
    p.hass.stati.imposta(C1, "off")
    b.v("un vero on -> off fatto da fuori si'", p.a.ultimo_cambio_esterno is not None)

    # Una correzione che conferma in ritardo non deve rallentare i blocchi successivi.
    p = Prova()
    p.hass.services.rele[C2] = Rele(ritardo=0.5)
    await p.a.async_start()
    p.hass.stati.imposta(C2, "on")
    await asyncio.sleep(0.45)
    b.v("correzione con conferma in ritardo: contata come fallita", p.a._fallimenti == 1)
    await asyncio.sleep(0.2)
    b.v("il contatto poi si apre e i fallimenti si azzerano", p.a.stato() == "normale" and p.a._fallimenti == 0)
    p.hass.services.rele[C2] = Rele()
    p.hass.stati.imposta(C2, "on")
    await asyncio.sleep(0.1)
    b.v("il blocco successivo si corregge di nuovo dopo la grazia breve", p.a.stato() == "normale")
    await p.attendi()

    # Qualcuno continua a richiudere il contatto 2: dopo 3 correzioni si rallenta.
    p = Prova()
    await p.a.async_start()
    for _ in range(3):
        p.hass.stati.imposta(C2, "on")
        await asyncio.sleep(0.08)
    b.v("tre blocchi corretti in fretta", p.a.stato() == "normale" and len(p.chiamate) == 3)
    p.hass.stati.imposta(C2, "on")
    await asyncio.sleep(0.1)
    b.v("il quarto aspetta la grazia lunga", p.a.stato() == "blocco")
    await asyncio.sleep(0.3)
    b.v("... poi lo corregge", p.a.stato() == "normale")
    b.v("e la segnalazione dice che il blocco si ripresenta", "si ripresenta" in p.segnalazioni[-1][1])
    await p.attendi()

    # Allo stop una correzione in attesa non deve rinascere.
    p = Prova(grazia=0.2)
    await p.a.async_start()
    p.hass.stati.imposta(C2, "on")
    await asyncio.sleep(0.05)
    await p.a.async_stop()
    for compito in p.compiti:
        compito.cancel()
    await asyncio.sleep(0.3)
    b.v("dopo lo stop nessuna correzione riparte", p.chiamate == [] and all(c.done() for c in p.compiti))

    b.sezione("5. Senza contatto 2")
    p = Prova(con_2=False)
    await p.a.async_start()
    ok = await p.a.porta_a(2)
    b.v("lo stadio 2 diventa 1", ok and p.a.stato() == "consigliato" and p.chiamate == [("turn_on", C1)])
    b.v("stadio letto: 1", p.a.stadio() == 1)
    await p.a.porta_a(0)
    b.v("e si torna a 0", p.a.stato() == "normale")

    # -------------------------------------------------------------------------
    b.sezione("6. Stato e stadio")
    p = Prova("on", "off")
    b.v("stadio 1 letto dai contatti", p.a.stadio() == 1)
    p = Prova("off", "on", correggi=False)
    b.v("blocco: nessuno stadio valido", p.a.stadio() is None and p.a.stato() == "blocco")
    p = Prova("unavailable", "off")
    b.v("contatto illeggibile: stadio sconosciuto", p.a.stadio() is None and p.a.stato() is None)

    b.fine()


asyncio.run(main())
