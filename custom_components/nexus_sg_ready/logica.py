"""Logica dello Smart Grid senza Home Assistant: si prova a tavolino.

Qui stanno le regole che si possono sbagliare in silenzio: quali entita' proporre,
come si leggono i parametri del modulo, come si interpretano i due contatti e,
soprattutto, quando chiedere uno stadio SG e quando rilasciarlo.

L'esubero si calcola **al lordo della pompa di calore**: immissione in rete piu'
quello che la pompa sta assorbendo. Cosi' quando la pompa parte e si mangia
l'esubero la grandezza su cui si decide non crolla, e lo SG non si spegne da solo
(era il difetto della vecchia logica: la pompa partiva, l'immissione spariva,
il contatto si apriva e la pompa si fermava).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .const import CONVENZIONI_RETE, IMMISSIONE_POSITIVA, PRELIEVO_POSITIVO

# =============================================================================
# Configurazione: entita'
# =============================================================================


@dataclass(frozen=True)
class Slot:
    """Un'entita' da collegare: che domini accetta e dove sta nel modulo."""

    chiave: str
    domini: tuple[str, ...]
    cerca: str  # espressione sull'object_id, per la proposta automatica
    obbligatorio: bool
    sezione: str


SEZIONI = ("contatti", "rete", "pompa", "stadi", "tempi")

_INTERRUTTORI = ("switch", "input_boolean")

SLOT: tuple[Slot, ...] = (
    # Il numero del contatto deve seguire una parola che dica «canale/contatto»:
    # un semplice «...1$» prenderebbe anche «canale_2_431».
    Slot("contatto_1", _INTERRUTTORI, r"sg.*(canale|contatto|relay|rele|ch)_?1(_\d+)?$", True, "contatti"),
    Slot("contatto_2", _INTERRUTTORI, r"sg.*(canale|contatto|relay|rele|ch)_?2(_\d+)?$", False, "contatti"),
    Slot(
        "scambio_rete",
        ("sensor",),
        r"(potenza|power)_?(di_)?(scambio|rete|grid)|(scambio|grid)_?(rete_)?(potenza|power)",
        True,
        "rete",
    ),
    Slot("potenza_pdc", ("sensor",), r"(pdc|pompa_di_calore|heat_?pump).*(potenza|power)(?!.*(termica|therm|output))", False, "pompa"),
    Slot("compressore", ("binary_sensor",), r"compressore(_attivo)?$|compressor(_running|_on)?$", False, "pompa"),
    Slot("temperatura_acs", ("sensor",), r"temp.*(accumulo|boiler|bollitore|acs|dhw)|(accumulo|boiler|bollitore|dhw).*temp", False, "pompa"),
)
CHIAVI = tuple(slot.chiave for slot in SLOT)
OBBLIGATORI = tuple(slot.chiave for slot in SLOT if slot.obbligatorio)


def _dividi(entity_id: str) -> tuple[str, str]:
    dominio, _, oggetto = entity_id.partition(".")
    return dominio, oggetto


def proposte(entity_ids: Iterable[str], escludi: Iterable[str] = ()) -> dict[str, str]:
    """Le entita' che sembrano quelle giuste per ogni voce, riconosciute dal nome.

    E' solo una proposta per il modulo: a parita', l'ordine alfabetico, cosi' la
    proposta non cambia da un'apertura all'altra. Un'entita' gia' proposta per
    una voce non viene riproposta per un'altra.
    """
    esclusi = set(escludi)
    tutte = sorted(e for e in entity_ids if "." in e and e not in esclusi)
    scelte: dict[str, str] = {}
    for slot in SLOT:
        for entity_id in tutte:
            dominio, oggetto = _dividi(entity_id)
            if dominio in slot.domini and entity_id not in scelte.values() and re.search(slot.cerca, oggetto):
                scelte[slot.chiave] = entity_id
                break
    return scelte


def entita_da_modulo(dati: Mapping[str, Any]) -> dict[str, str]:
    """Le entita' scelte nel modulo, tolte dalle sezioni e senza i campi vuoti."""
    entita: dict[str, str] = {}
    for sezione in SEZIONI:
        for chiave, valore in (dati.get(sezione) or {}).items():
            if chiave in CHIAVI and isinstance(valore, str) and valore.strip():
                entita[chiave] = valore.strip()
    return entita


def rinomina(entita: Mapping[str, str], vecchio: str, nuovo: str) -> dict[str, str] | None:
    """Le entita' con un id rinominato, o None se il rinominato non e' fra le nostre."""
    if not vecchio or not nuovo or vecchio == nuovo or vecchio not in entita.values():
        return None
    return {chiave: (nuovo if valore == vecchio else valore) for chiave, valore in entita.items()}


# =============================================================================
# Configurazione: parametri
# =============================================================================


@dataclass(frozen=True)
class Campo:
    """Un parametro del modulo, con il suo tipo e i limiti del selettore."""

    chiave: str
    sezione: str
    tipo: str  # "numero", "booleano", "orario", "scelta"
    predefinito: Any
    minimo: float | None = None
    massimo: float | None = None
    passo: float | None = None
    unita: str | None = None
    opzioni: tuple[str, ...] = ()


CAMPI: tuple[Campo, ...] = (
    Campo("correggi_blocco", "contatti", "booleano", True),
    Campo("segno_rete", "rete", "scelta", IMMISSIONE_POSITIVA, opzioni=tuple(CONVENZIONI_RETE)),
    Campo("potenza_stimata", "pompa", "numero", 1500, 100, 10000, 50, "W"),
    Campo("acs_massima", "pompa", "numero", 48, 20, 70, 0.5, "°C"),
    Campo("soglia_1", "stadi", "numero", 1000, 0, 20000, 50, "W"),
    Campo("isteresi", "stadi", "numero", 300, 0, 5000, 50, "W"),
    Campo("stadio_2", "stadi", "booleano", False),
    Campo("soglia_2", "stadi", "numero", 2500, 0, 30000, 50, "W"),
    Campo("ora_inizio", "tempi", "orario", "09:00"),
    Campo("ora_fine", "tempi", "orario", "18:00"),
    Campo("ritardo_attivazione", "tempi", "numero", 3, 0, 120, 1, "min"),
    Campo("ritardo_rilascio", "tempi", "numero", 10, 0, 120, 1, "min"),
    Campo("durata_minima", "tempi", "numero", 10, 0, 240, 1, "min"),
    Campo("pausa_minima", "tempi", "numero", 10, 0, 240, 1, "min"),
    Campo("tenuta_ciclo", "tempi", "booleano", True),
    Campo("tenuta_massima", "tempi", "numero", 60, 5, 480, 5, "min"),
)
CAMPI_PER_CHIAVE = {campo.chiave: campo for campo in CAMPI}
PREDEFINITI: dict[str, Any] = {campo.chiave: campo.predefinito for campo in CAMPI}


def orario(testo: Any) -> str | None:
    """«HH:MM» da «H:MM», «HH:MM» o «HH:MM:SS» (quello del selettore); None se non valido."""
    trovato = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*", str(testo or ""))
    if not trovato:
        return None
    ore, minuti = int(trovato.group(1)), int(trovato.group(2))
    if ore > 23 or minuti > 59:
        return None
    return f"{ore:02d}:{minuti:02d}"


def minuti_del_giorno(testo: str) -> int:
    """Minuti dalla mezzanotte di un orario «HH:MM» (0 se non valido)."""
    valido = orario(testo)
    if valido is None:
        return 0
    ore, minuti = valido.split(":")
    return int(ore) * 60 + int(minuti)


def _converti(campo: Campo, valore: Any) -> Any:
    """Il valore del modulo nel tipo giusto, o None se non si puo'."""
    if campo.tipo == "booleano":
        return valore if isinstance(valore, bool) else None
    if campo.tipo == "orario":
        return orario(valore)
    if campo.tipo == "scelta":
        return valore if valore in campo.opzioni else None
    numero_letto = numero(valore)
    if numero_letto is None:
        return None
    if campo.minimo is not None:
        numero_letto = max(float(campo.minimo), numero_letto)
    if campo.massimo is not None:
        numero_letto = min(float(campo.massimo), numero_letto)
    return numero_letto


def parametri_da_modulo(dati: Mapping[str, Any], base: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """I parametri scelti nel modulo, tolti dalle sezioni e nel tipo giusto.

    Un campo assente o non valido prende il valore di `base` (quello gia'
    configurato), e se manca anche li' il predefinito.
    """
    risultato = dict(PREDEFINITI)
    for chiave, valore in (base or {}).items():
        campo = CAMPI_PER_CHIAVE.get(chiave)
        convertito = _converti(campo, valore) if campo else None
        if convertito is not None:
            risultato[chiave] = convertito
    for sezione in SEZIONI:
        for chiave, valore in (dati.get(sezione) or {}).items():
            campo = CAMPI_PER_CHIAVE.get(chiave)
            convertito = _converti(campo, valore) if campo else None
            if convertito is not None:
                risultato[chiave] = convertito
    return risultato


def errori_modulo(entita: Mapping[str, str], parametri: Mapping[str, Any]) -> dict[str, str]:
    """Gli errori del modulo, nel formato di Home Assistant (vuoto se va bene)."""
    if any(chiave not in entita for chiave in OBBLIGATORI):
        return {"base": "mancano_obbligatorie"}
    valori = list(entita.values())
    if len(valori) != len(set(valori)):
        return {"base": "entita_ripetuta"}
    # Senza compressore ne' potenza della pompa l'esubero sarebbe solo netto: la
    # pompa che parte si mangerebbe l'esubero e rilascerebbe da sola il contatto.
    if "compressore" not in entita and "potenza_pdc" not in entita:
        return {"base": "manca_pompa"}
    if parametri.get("stadio_2") and "contatto_2" not in entita:
        return {"base": "stadio_2_senza_contatto"}
    if parametri.get("stadio_2") and float(parametri["soglia_2"]) <= float(parametri["soglia_1"]):
        return {"base": "soglie_invertite"}
    if minuti_del_giorno(parametri["ora_inizio"]) == minuti_del_giorno(parametri["ora_fine"]):
        return {"base": "fascia_vuota"}
    return {}


# =============================================================================
# Letture
# =============================================================================

_NON_VALIDI = {"unknown", "unavailable", "none", ""}


def numero(stato: Any) -> float | None:
    """Il numero di uno stato grezzo, o None se non c'e' o non e' un numero."""
    if stato is None or isinstance(stato, bool):
        return None
    testo = str(stato).strip()
    if testo.lower() in _NON_VALIDI:
        return None
    try:
        valore = float(testo.replace(",", "."))
    except ValueError:
        return None
    return valore if math.isfinite(valore) else None


def acceso(stato: Any) -> bool | None:
    """True se «on», False se «off», None se non si puo' dire."""
    if stato is None:
        return None
    testo = str(stato).strip().lower()
    if testo == "on":
        return True
    if testo == "off":
        return False
    return None


def watt(stato: Any, unita: Any) -> float | None:
    """Una potenza in watt, qualunque sia l'unita' del sensore (W, kW, MW)."""
    valore = numero(stato)
    if valore is None:
        return None
    fattore = {"kw": 1000.0, "mw": 1_000_000.0}.get(str(unita or "").strip().lower(), 1.0)
    return valore * fattore


def immissione(scambio_w: float | None, convenzione: str) -> float | None:
    """Lo scambio con la rete come immissione: positivo se si cede, negativo se si preleva."""
    if scambio_w is None:
        return None
    return -scambio_w if convenzione == PRELIEVO_POSITIVO else scambio_w


def stato_contatti(contatto_1: Any, contatto_2: Any, con_contatto_2: bool) -> str | None:
    """Cosa vede la macchina dai due contatti; None se non si puo' dire.

    Solo il contatto 2 chiuso e' il «blocco»: la macchina si ferma. Lo si
    riconosce anche se il contatto 1 non si legge, perche' e' la situazione
    che va corretta.
    """
    uno = acceso(contatto_1)
    due = acceso(contatto_2) if con_contatto_2 else False
    if due is True and uno is not True:
        return "blocco"
    if uno is None or due is None:
        return None
    if uno and due:
        return "comando"
    return "consigliato" if uno else "normale"


def in_fascia(minuti: int, inizio: str, fine: str) -> bool:
    """Se l'ora e' nella fascia [inizio, fine); la fascia puo' scavalcare la mezzanotte."""
    a, b = minuti_del_giorno(inizio), minuti_del_giorno(fine)
    if a == b:
        return False
    if a < b:
        return a <= minuti < b
    return minuti >= a or minuti < b


# =============================================================================
# Decisione
# =============================================================================

# Sopra questa potenza misurata la pompa e' in marcia, se manca il compressore.
SOGLIA_MARCIA_W = 150


@dataclass
class Ingressi:
    """Una lettura dell'impianto, gia' pulita."""

    immissione: float | None  # W, positivo = si cede alla rete
    potenza_pdc: float | None  # W misurati, None se non collegata o non leggibile
    compressore: bool | None
    acs: float | None  # °C del bollitore, None se non collegato o non leggibile
    minuti: int  # minuti dalla mezzanotte, ora locale
    adesso: float  # secondi, orologio monotono


@dataclass
class Decisione:
    stadio: int
    motivo: str
    esubero: float | None
    potenza_pdc: float
    in_marcia: bool
    tenuta: bool


def _w(valore: float) -> str:
    return f"{round(valore)} W"


def _tra(secondi: float) -> str:
    return f"{max(1, math.ceil(secondi / 60))} min"


@dataclass
class Decisore:
    """Decide lo stadio SG da chiedere. Uno scalino alla volta: 0 -> 1 -> 2 e ritorno.

    Regole:
    - si sale quando l'esubero lordo resta sopra la soglia per tutto il ritardo di
      attivazione, dentro la fascia oraria, dopo la pausa minima dall'ultima
      discesa (anche 2 -> 1: niente altalena fra gli stadi) e, per lo stadio 1,
      solo se l'acqua del bollitore e' sotto la temperatura indicata: cosi' ogni
      rabbocco e' abbastanza grande da valere la partenza;
    - si scende quando l'esubero resta sotto soglia meno isteresi per tutto il
      ritardo di rilascio, o subito fuori fascia; mai prima della durata minima;
    - mentre la pompa sta scaldando uno stadio chiesto da noi si tiene fino a fine
      ciclo (al massimo per la tenuta massima): se l'obiettivo tornasse normale a
      meta' ciclo, la macchina si fermerebbe con l'acqua gia' sopra l'obiettivo;
    - senza lettura dello scambio non si sale, e si scende come senza esubero.
    """

    parametri: dict[str, Any] = field(default_factory=lambda: dict(PREDEFINITI))
    stadio: int = 0
    _su_da: float | None = None
    _giu_da: float | None = None
    _cambiato_alle: float | None = None
    _sceso_alle: float | None = None
    _marcia_da: float | None = None

    # -------------------------------------------------------------------------
    def _p(self, chiave: str) -> Any:
        return self.parametri.get(chiave, PREDEFINITI[chiave])

    def _cambia(self, stadio: int, adesso: float) -> None:
        if stadio < self.stadio:
            self._sceso_alle = adesso
        self.stadio = stadio
        self._cambiato_alle = adesso
        self._su_da = None
        self._giu_da = None
        if stadio == 0:
            self._marcia_da = None

    def imposta(self, stadio: int, adesso: float) -> None:
        """Stadio imposto da fuori (per esempio la modalita' «spento»)."""
        if stadio != self.stadio:
            self._cambia(stadio, adesso)

    def riprendi(self, stadio: int, adesso: float) -> None:
        """Dopo un riavvio: riparte dallo stadio che i contatti hanno gia'.

        Non si fa pagare di nuovo la durata minima: lo stadio c'era gia' prima.
        """
        self.stadio = max(0, min(2, stadio))
        self._cambiato_alle = adesso - float(self._p("durata_minima")) * 60
        self._su_da = None
        self._giu_da = None
        self._marcia_da = None

    # -------------------------------------------------------------------------
    def valuta(self, ingressi: Ingressi, spento: bool = False) -> Decisione:
        """Lo stadio da chiedere adesso, con il perche'.

        Con `spento` si calcolano solo le misure: lo stadio resta quello imposto.
        """
        adesso = ingressi.adesso
        potenza_stimata = float(self._p("potenza_stimata"))

        # La pompa: in marcia o no, e quanto assorbe.
        if ingressi.compressore is not None:
            in_marcia = ingressi.compressore
        else:
            in_marcia = ingressi.potenza_pdc is not None and ingressi.potenza_pdc > SOGLIA_MARCIA_W
        if ingressi.potenza_pdc is not None:
            potenza_pdc = max(0.0, ingressi.potenza_pdc)
        else:
            potenza_pdc = potenza_stimata if in_marcia else 0.0

        esubero = None if ingressi.immissione is None else ingressi.immissione + potenza_pdc

        # Tenuta: la pompa sta scaldando mentre uno stadio e' attivo.
        if self.stadio >= 1 and in_marcia:
            if self._marcia_da is None:
                self._marcia_da = adesso
        else:
            self._marcia_da = None
        in_tenuta = (
            bool(self._p("tenuta_ciclo"))
            and self._marcia_da is not None
            and adesso - self._marcia_da < float(self._p("tenuta_massima")) * 60
        )

        if spento:
            # Nessun conto alla rovescia sopravvive allo spento: quando si riaccende
            # si riparte da capo con il ritardo di attivazione.
            self._su_da = None
            self._giu_da = None
            motivo = "Modalita' spento: nessuno stadio chiesto"
        else:
            motivo = self._passo(ingressi, esubero, in_tenuta)
        return Decisione(
            stadio=self.stadio,
            motivo=motivo,
            esubero=None if esubero is None else round(esubero),
            potenza_pdc=round(potenza_pdc),
            in_marcia=in_marcia,
            tenuta=in_tenuta,
        )

    # -------------------------------------------------------------------------
    def _passo(self, ingressi: Ingressi, esubero: float | None, in_tenuta: bool) -> str:
        """Al piu' uno scalino; restituisce il perche' dello stadio di adesso."""
        adesso = ingressi.adesso
        soglia_1 = float(self._p("soglia_1"))
        soglia_2 = float(self._p("soglia_2"))
        isteresi = float(self._p("isteresi"))
        con_stadio_2 = bool(self._p("stadio_2"))
        inizio, fine = str(self._p("ora_inizio")), str(self._p("ora_fine"))
        dentro = in_fascia(ingressi.minuti, inizio, fine)
        ritardo_su = float(self._p("ritardo_attivazione")) * 60
        ritardo_giu = float(self._p("ritardo_rilascio")) * 60
        durata_minima = float(self._p("durata_minima")) * 60

        # Lo stadio 2 tolto dalle opzioni mentre e' attivo: si scende subito.
        if self.stadio == 2 and not con_stadio_2:
            self._cambia(1, adesso)
            return "Stadio 2 disattivato nelle opzioni: torno allo stadio 1"

        # --- verso l'alto -----------------------------------------------------
        if self.stadio < 2:
            soglia = soglia_1 if self.stadio == 0 else soglia_2
            motivo_no = self._ostacolo_salita(ingressi, esubero, soglia, dentro, con_stadio_2, inizio, fine)
            if motivo_no is None:
                # Si e' sopra la soglia di salita, quindi sopra quella di rilascio:
                # un eventuale conto alla rovescia per scendere non vale piu'.
                self._giu_da = None
                if self._su_da is None:
                    self._su_da = adesso
                attesa = ritardo_su - (adesso - self._su_da)
                if attesa <= 0:
                    self._cambia(self.stadio + 1, adesso)
                    return f"Esubero {_w(esubero)} sopra {_w(soglia)}: stadio {self.stadio}"
                return f"Esubero {_w(esubero)} sopra {_w(soglia)}: stadio {self.stadio + 1} fra {_tra(attesa)}"
            self._su_da = None
            if self.stadio == 0:
                return motivo_no

        # --- verso il basso (stadio 1 o 2) ------------------------------------
        soglia = soglia_1 if self.stadio == 1 else soglia_2
        rilascio = soglia - isteresi
        attivo_da = adesso - (self._cambiato_alle if self._cambiato_alle is not None else adesso)

        if not dentro:
            causa = f"Fuori fascia ({inizio}-{fine})"
            self._giu_da = self._giu_da if self._giu_da is not None else adesso
            pronto = True
        elif esubero is None:
            causa = "Scambio con la rete non disponibile"
            self._giu_da = self._giu_da if self._giu_da is not None else adesso
            pronto = adesso - self._giu_da >= ritardo_giu
        elif esubero < rilascio:
            causa = f"Esubero {_w(esubero)} sotto {_w(rilascio)}"
            self._giu_da = self._giu_da if self._giu_da is not None else adesso
            pronto = adesso - self._giu_da >= ritardo_giu
        else:
            self._giu_da = None
            return f"Esubero {_w(esubero)}: resto allo stadio {self.stadio}"

        if not pronto:
            return f"{causa}: rilascio fra {_tra(ritardo_giu - (adesso - self._giu_da))}"
        if attivo_da < durata_minima:
            return f"{causa}: rilascio dopo la durata minima, fra {_tra(durata_minima - attivo_da)}"
        if in_tenuta:
            return f"{causa}: tengo finche' la pompa non finisce il ciclo"
        self._cambia(self.stadio - 1, adesso)
        return f"{causa}: scendo allo stadio {self.stadio}"

    def _ostacolo_salita(
        self,
        ingressi: Ingressi,
        esubero: float | None,
        soglia: float,
        dentro: bool,
        con_stadio_2: bool,
        inizio: str,
        fine: str,
    ) -> str | None:
        """Perche' non si puo' salire di uno stadio adesso; None se si puo'."""
        adesso = ingressi.adesso
        if self.stadio == 1 and not con_stadio_2:
            return "Stadio 2 non abilitato"
        if not dentro:
            return f"Fuori fascia ({inizio}-{fine})"
        if esubero is None:
            return "Scambio con la rete non disponibile"
        if esubero < soglia:
            return f"Esubero {_w(esubero)} sotto {_w(soglia)}"
        if self.stadio == 0:
            acs_massima = float(self._p("acs_massima"))
            if ingressi.acs is not None and ingressi.acs > acs_massima:
                return f"Bollitore a {ingressi.acs:g} °C, sopra {acs_massima:g} °C: rabbocco troppo piccolo"
        pausa = float(self._p("pausa_minima")) * 60
        if self._sceso_alle is not None and adesso - self._sceso_alle < pausa:
            return f"Pausa dopo il rilascio: ancora {_tra(pausa - (adesso - self._sceso_alle))}"
        return None
