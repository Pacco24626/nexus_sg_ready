"""Prove a tavolino della logica: proposte, parametri, contatti e decisione.

    python tests/test_logica.py
"""

from __future__ import annotations

import finto_ha  # noqa: F401  (prepara i moduli finti di Home Assistant)
from finto_ha import Banco

from nexus_sg_ready.logica import (
    PREDEFINITI,
    Decisore,
    Ingressi,
    errori_modulo,
    immissione,
    in_fascia,
    orario,
    parametri_da_modulo,
    proposte,
    stato_contatti,
    watt,
)

b = Banco("LOGICA")

# Le entita' vere di casa (19/09/2026), piu' qualche esca.
CASA = [
    "switch.cantina_sg_canale_1_430",
    "switch.cantina_sg_canale_2_431",
    "switch.cantina_sg_canale_1_e_2_429",
    "switch.pompa_di_calore_boost_acs",
    "sensor.nexus_t_energy_monitor_potenza_scambio_rete",
    "sensor.nexus_t_energy_monitor_produzione_fotovoltaico",
    "sensor.consumi_casa_watt_159",
    "binary_sensor.pompa_di_calore_pdc_compressore_attivo",
    "sensor.pompa_di_calore_pdc_temp_accumulo_acs",
    "sensor.pompa_di_calore_pdc_temp_mandata",
    "sensor.pompa_di_calore_pdc_frequenza_compressore",
    "light.salotto",
]

# -----------------------------------------------------------------------------
b.sezione("1. Proposte dal nome")
p = proposte(CASA)
b.v("contatto 1: il canale 1 vero", p.get("contatto_1") == "switch.cantina_sg_canale_1_430")
b.v("contatto 2: il canale 2 vero", p.get("contatto_2") == "switch.cantina_sg_canale_2_431")
b.v("il raggruppamento «canale 1 e 2» non viene proposto", "switch.cantina_sg_canale_1_e_2_429" not in p.values())
b.v("scambio: la potenza di scambio del Nexus-T, non i consumi dello Yubii",
    p.get("scambio_rete") == "sensor.nexus_t_energy_monitor_potenza_scambio_rete")
b.v("compressore riconosciuto", p.get("compressore") == "binary_sensor.pompa_di_calore_pdc_compressore_attivo")
b.v("bollitore: l'accumulo ACS, non la mandata", p.get("temperatura_acs") == "sensor.pompa_di_calore_pdc_temp_accumulo_acs")
b.v("nessuna potenza della pompa inventata", "potenza_pdc" not in p)
b.v("le entita' escluse non vengono proposte",
    "contatto_1" not in proposte(CASA, escludi=["switch.cantina_sg_canale_1_430"]))
# Controllo di sanita' della regola sul numero: con un'espressione ingenua («1$»)
# il canale 2 (431, finisce per 1) verrebbe preso come contatto 1.
import re  # noqa: E402

b.v("sanita': un'espressione ingenua sbaglierebbe davvero",
    re.search(r"sg.*1(_\d+)?$", "cantina_sg_canale_2_431") is not None)

# -----------------------------------------------------------------------------
b.sezione("2. Parametri del modulo")
dati = {
    "stadi": {"soglia_1": 1200, "isteresi": "250", "stadio_2": True, "soglia_2": 3000},
    "tempi": {"ora_inizio": "10:30:00", "ora_fine": "7:05", "ritardo_attivazione": 99999},
    "contatti": {"correggi_blocco": "si'"},
}
par = parametri_da_modulo(dati)
b.v("numeri convertiti", par["soglia_1"] == 1200.0 and par["isteresi"] == 250.0)
b.v("orario del selettore ridotto a HH:MM", par["ora_inizio"] == "10:30" and par["ora_fine"] == "07:05")
b.v("valore oltre il massimo riportato al massimo", par["ritardo_attivazione"] == 120.0)
b.v("booleano non valido: resta il predefinito", par["correggi_blocco"] is True)
b.v("campi assenti: predefiniti", par["durata_minima"] == PREDEFINITI["durata_minima"])
base = {**PREDEFINITI, "soglia_1": 800.0}
b.v("campi assenti nelle opzioni: resta il valore gia' configurato",
    parametri_da_modulo({}, base)["soglia_1"] == 800.0)
b.v("orari non validi rifiutati", orario("25:00") is None and orario("ieri") is None and orario("9:5") is None)

ent = {"contatto_1": "switch.a", "scambio_rete": "sensor.s", "compressore": "binary_sensor.c"}
b.v("modulo valido: nessun errore", errori_modulo(ent, PREDEFINITI) == {})
b.v("manca il contatto 1", errori_modulo({"scambio_rete": "sensor.s"}, PREDEFINITI) == {"base": "mancano_obbligatorie"})
b.v("manca lo scambio", errori_modulo({"contatto_1": "switch.a"}, PREDEFINITI) == {"base": "mancano_obbligatorie"})
b.v("stessa entita' per due voci",
    errori_modulo({**ent, "contatto_2": "switch.a"}, PREDEFINITI) == {"base": "entita_ripetuta"})
b.v("stadio 2 senza contatto 2",
    errori_modulo(ent, {**PREDEFINITI, "stadio_2": True}) == {"base": "stadio_2_senza_contatto"})
b.v("soglie invertite",
    errori_modulo({**ent, "contatto_2": "switch.b"}, {**PREDEFINITI, "stadio_2": True, "soglia_2": 900})
    == {"base": "soglie_invertite"})
b.v("soglie invertite non contano con lo stadio 2 spento",
    errori_modulo(ent, {**PREDEFINITI, "soglia_2": 900}) == {})
b.v("fascia vuota", errori_modulo(ent, {**PREDEFINITI, "ora_fine": "09:00"}) == {"base": "fascia_vuota"})
b.v("senza compressore ne' potenza della pompa: errore (l'esubero sarebbe solo netto)",
    errori_modulo({"contatto_1": "switch.a", "scambio_rete": "sensor.s"}, PREDEFINITI) == {"base": "manca_pompa"})
b.v("basta la potenza della pompa, anche senza compressore",
    errori_modulo({"contatto_1": "switch.a", "scambio_rete": "sensor.s", "potenza_pdc": "sensor.p"}, PREDEFINITI) == {})

# -----------------------------------------------------------------------------
b.sezione("3. Letture")
b.v("kW convertiti in W", watt("1.5", "kW") == 1500.0)
b.v("W restano W", watt("-435", "W") == -435.0)
b.v("non disponibile: None", watt("unavailable", "W") is None)
b.v("immissione positiva: segno invariato", immissione(-435.0, "immissione_positiva") == -435.0)
b.v("prelievo positivo: segno invertito", immissione(-435.0, "prelievo_positivo") == 435.0)
b.v("contatti aperti: normale", stato_contatti("off", "off", True) == "normale")
b.v("solo 1: consigliato", stato_contatti("on", "off", True) == "consigliato")
b.v("1 e 2: comando", stato_contatti("on", "on", True) == "comando")
b.v("solo 2: blocco", stato_contatti("off", "on", True) == "blocco")
b.v("2 chiuso e 1 illeggibile: blocco (va corretto)", stato_contatti("unavailable", "on", True) == "blocco")
b.v("1 illeggibile e 2 aperto: non si sa", stato_contatti("unavailable", "off", True) is None)
b.v("senza contatto 2: solo normale o consigliato", stato_contatti("on", None, False) == "consigliato")
b.v("fascia di giorno", in_fascia(12 * 60, "09:00", "18:00") and not in_fascia(18 * 60, "09:00", "18:00"))
b.v("fascia a cavallo della mezzanotte", in_fascia(23 * 60, "22:00", "06:00") and in_fascia(60, "22:00", "06:00")
    and not in_fascia(12 * 60, "22:00", "06:00"))

# -----------------------------------------------------------------------------
# Decisione: il tempo in minuti, l'ora del giorno in «HH:MM».
MEZZOGIORNO = 12 * 60


def giro(d: Decisore, minuto: float, imm: float | None, *, comp: bool | None = None, acs: float | None = None,
         ora: int | None = None, pot: float | None = None):
    return d.valuta(Ingressi(immissione=imm, potenza_pdc=pot, compressore=comp, acs=acs,
                             minuti=MEZZOGIORNO + int(minuto) if ora is None else ora, adesso=minuto * 60))


def nuovo(**parametri) -> Decisore:
    return Decisore(parametri={**PREDEFINITI, **parametri})


b.sezione("4. Salita allo stadio 1")
d = nuovo()
r0 = giro(d, 0, 1200, comp=False)
r2 = giro(d, 2, 1200, comp=False)
r3 = giro(d, 3, 1200, comp=False)
b.v("sopra soglia ma prima del ritardo: resta 0", r0.stadio == 0 and r2.stadio == 0)
b.v("il motivo dice quanto manca", "fra 1 min" in r2.motivo)
b.v("dopo 3 minuti sopra soglia: stadio 1", r3.stadio == 1)

d = nuovo()
for m, imm in ((0, 1200), (1, 1200), (2, 500), (3, 1200), (5, 1200)):
    r = giro(d, m, imm, comp=False)
b.v("una nuvola azzera il ritardo: al minuto 5 non ancora", r.stadio == 0)
b.v("... e sale 3 minuti dopo il ritorno del sole", giro(d, 6, 1200, comp=False).stadio == 1)

d = nuovo()
for m in range(0, 10):
    r = giro(d, m, 3000, comp=False, ora=8 * 60)
b.v("fuori fascia non si sale", r.stadio == 0 and "Fuori fascia" in r.motivo)

d = nuovo()
for m in range(0, 10):
    r = giro(d, m, 1500, comp=False, acs=49.0)
b.v("bollitore sopra 48 °C: niente rabbocco piccolo", r.stadio == 0 and "Bollitore" in r.motivo)
d = nuovo()
for m in range(0, 4):
    r = giro(d, m, 1500, comp=False, acs=47.0)
b.v("bollitore a 47 °C: si attiva", r.stadio == 1)

d = nuovo()
for m in range(0, 10):
    r = giro(d, m, None)
b.v("senza scambio non si sale", r.stadio == 0 and "non disponibile" in r.motivo)

# -----------------------------------------------------------------------------
b.sezione("5. L'anello di retroazione della vecchia logica")


def anello(decisore: Decisore) -> list[int]:
    """Esubero netto di 1200 W; al minuto 4 parte la pompa (1500 W) e l'immissione
    scende a -300 W. Ritorna lo stadio minuto per minuto."""
    stadi = []
    for m in range(0, 40):
        in_marcia = m >= 4
        stadi.append(giro(decisore, m, 1200 - (1500 if in_marcia else 0), comp=in_marcia).stadio)
    return stadi


lordo = anello(nuovo(tenuta_ciclo=False))
b.v("al lordo della pompa lo stadio resta 1 per tutti i 40 minuti", lordo[3:] == [1] * 37)
netto = anello(nuovo(tenuta_ciclo=False, potenza_stimata=100))
b.v("sanita': contando la pompa come fosse spenta (100 W) lo stadio cade", 0 in netto[3:])

# -----------------------------------------------------------------------------
b.sezione("6. Tenuta fino a fine ciclo")


def nuvola(decisore: Decisore, fine_ciclo: int, durata: int = 90) -> list[int]:
    """Stadio 1 raggiunto col sole; dal minuto 5 la pompa gira fino a fine_ciclo e
    arriva una nuvola: immissione -1500 W (esubero lordo 0)."""
    stadi = []
    for m in range(0, durata):
        marcia = 5 <= m < fine_ciclo
        imm = 1200 if m < 5 else (-1500 if marcia else 0)
        stadi.append(giro(decisore, m, imm, comp=marcia).stadio)
    return stadi


tenuta = nuvola(nuovo(), fine_ciclo=40)
b.v("con la tenuta resta 1 finche' la pompa gira", all(s == 1 for s in tenuta[3:40]))
b.v("appena la pompa si ferma rilascia (il ritardo era gia' passato)", tenuta[40] == 0)
senza = nuvola(nuovo(tenuta_ciclo=False), fine_ciclo=40)
b.v("sanita': senza tenuta rilascia a meta' ciclo", 0 in senza[5:40])
lunga = nuvola(nuovo(tenuta_massima=60), fine_ciclo=85, durata=90)
b.v("la tenuta massima (60 min) fa cadere lo stadio anche con la pompa accesa",
    lunga[64] == 1 and 0 in lunga[65:85])

# -----------------------------------------------------------------------------
b.sezione("7. Durata minima, ritardo di rilascio e pausa")
d = nuovo(durata_minima=20, tenuta_ciclo=False)
stadi = [giro(d, m, 1200 if m < 4 else 0, comp=False).stadio for m in range(0, 40)]
b.v("salita al minuto 3", stadi[3] == 1)
b.v("esubero sparito al minuto 4: il ritardo (10) scadrebbe al 14, ma la durata minima tiene fino al 23",
    stadi[22] == 1 and stadi[23] == 0)
d = nuovo(durata_minima=0, tenuta_ciclo=False)
stadi = [giro(d, m, 1200 if m < 4 else 0, comp=False).stadio for m in range(0, 30)]
b.v("senza durata minima decide il ritardo di rilascio: 10 minuti", stadi[13] == 1 and stadi[14] == 0)

d = nuovo(durata_minima=0, tenuta_ciclo=False)
stadi = [giro(d, m, 1200 if (m < 4 or m >= 15) else 0, comp=False).stadio for m in range(0, 40)]
rilascio = stadi.index(0, 4)
risalita = stadi.index(1, rilascio)
b.v("dopo il rilascio il sole torna subito, ma la pausa (10) + il ritardo (3) contano",
    risalita - rilascio >= 10)
d = nuovo(tenuta_ciclo=False)
stadi = [giro(d, m, 1200 if m < 4 else 800, comp=False).stadio for m in range(0, 40)]
b.v("isteresi: a 800 W (sopra 1000 - 300) lo stadio 1 resta", stadi[-1] == 1)
d = nuovo(tenuta_ciclo=False)
stadi = [giro(d, m, 1200 if m < 4 else 650, comp=False).stadio for m in range(0, 40)]
b.v("isteresi: a 650 W (sotto 700) si rilascia", stadi[-1] == 0)

# -----------------------------------------------------------------------------
b.sezione("8. Fascia oraria e scambio mancante")
d = nuovo(durata_minima=0)
for m in range(0, 5):
    giro(d, m, 1500, comp=False, ora=17 * 60 + 50 + m)
r = giro(d, 10, 1500, comp=False, ora=18 * 60)
b.v("alle 18 si rilascia subito", r.stadio == 0 and "Fuori fascia" in r.motivo)
d = nuovo(durata_minima=0)
for m in range(0, 5):
    giro(d, m, 1500, comp=m >= 4, ora=17 * 60 + 50 + m)
r = giro(d, 10, 1500, comp=True, ora=18 * 60)
b.v("ma se la pompa sta scaldando si tiene", r.stadio == 1 and "tengo" in r.motivo)

d = nuovo(durata_minima=0, tenuta_ciclo=False)
stadi = [giro(d, m, 1200 if m < 4 else None, comp=False).stadio for m in range(0, 20)]
b.v("scambio sparito: si rilascia dopo il ritardo, come senza esubero", stadi[13] == 1 and stadi[14] == 0)

# -----------------------------------------------------------------------------
b.sezione("9. Stadio 2")
d = nuovo(stadio_2=True, soglia_2=2500)
stadi = [giro(d, m, 3000, comp=False).stadio for m in range(0, 10)]
b.v("uno scalino alla volta: 1 al minuto 3, poi altri 3 minuti per il 2 (al 7)",
    stadi[2] == 0 and stadi[3] == 1 and stadi[6] == 1 and stadi[7] == 2)
d = nuovo(stadio_2=True, soglia_2=2500, ritardo_attivazione=0)
b.v("anche senza ritardo non si salta da 0 a 2", giro(d, 0, 5000).stadio == 1 and giro(d, 1, 5000).stadio == 2)
d = nuovo(stadio_2=True, soglia_2=2500, durata_minima=0, tenuta_ciclo=False)
stadi = [giro(d, m, 3000 if m < 10 else 2000, comp=False).stadio for m in range(0, 40)]
b.v("esubero a 2000 dal minuto 10 (sotto 2500-300): torna a 1 dopo il ritardo, al 20, e ci resta",
    stadi[9] == 2 and stadi[19] == 2 and stadi[20] == 1 and stadi[-1] == 1)
d = nuovo(stadio_2=True, soglia_2=2500)
for m in range(0, 7):
    giro(d, m, 3000)
d.parametri["stadio_2"] = False
r = giro(d, 7, 3000)
b.v("stadio 2 tolto dalle opzioni mentre e' attivo: subito a 1", r.stadio == 1)
d = nuovo()
stadi = [giro(d, m, 9000).stadio for m in range(0, 15)]
b.v("stadio 2 non abilitato: con qualunque esubero si resta a 1", max(stadi) == 1)

# -----------------------------------------------------------------------------
b.sezione("9b. Revisione: altalena fra stadio 1 e 2, timer vecchi")
# Dal 2 si scende a 1; il sole torna subito: senza pausa si risalirebbe dopo il
# solo ritardo (3 min), e con la resistenza che entra solo allo stadio 2 si
# andrebbe avanti e indietro.
d = nuovo(stadio_2=True, soglia_2=2500, durata_minima=0, tenuta_ciclo=False, ritardo_rilascio=0)
stadi = [giro(d, m, 3000 if (m < 10 or m >= 11) else 1500, comp=False).stadio for m in range(0, 30)]
sceso = stadi.index(1, stadi.index(2))
risalito = stadi.index(2, sceso)
b.v("dopo la discesa 2 -> 1 la risalita aspetta la pausa minima (10) piu' il ritardo (3)",
    risalito - sceso >= 10)

# Stadio 1 con stadio 2 abilitato: un'attesa di salita non deve lasciare vecchio
# il conto alla rovescia di discesa.
d = nuovo(stadio_2=True, soglia_2=2500, durata_minima=0, tenuta_ciclo=False)
profilo = {m: (1200 if m < 4 else 500 if m < 8 else 3000 if m < 10 else 500) for m in range(0, 30)}
stadi = [giro(d, m, profilo[m], comp=False).stadio for m in range(0, 30)]
b.v("esubero sceso di nuovo al minuto 10: il rilascio arriva 10 minuti dopo, non prima",
    stadi[3] == 1 and stadi[19] == 1 and stadi[20] == 0)

# Lo spento azzera i conti alla rovescia.
d = nuovo()
giro(d, 0, 1500)
giro(d, 1, 1500)
d.valuta(Ingressi(1500, None, False, None, MEZZOGIORNO + 2, 2 * 60), spento=True)
r = giro(d, 120, 1500)
b.v("dopo lo spento si riparte con il ritardo intero", r.stadio == 0 and "fra 3 min" in r.motivo)

b.sezione("10. Potenza della pompa")
d = nuovo()
r = giro(d, 0, -200, comp=True, pot=800)
b.v("con la misura si usa la misura (800), non la stima", r.potenza_pdc == 800 and r.esubero == 600)
r = giro(d, 1, -200, comp=True, pot=None)
b.v("misura non leggibile: si ripiega sulla stima", r.potenza_pdc == 1500 and r.esubero == 1300)
r = giro(d, 2, -200, comp=None, pot=900)
b.v("senza compressore, la potenza misurata dice se e' in marcia", r.in_marcia is True)
r = giro(d, 3, 400, comp=False)
b.v("compressore spento: nessuna potenza aggiunta", r.potenza_pdc == 0 and r.esubero == 400)

# -----------------------------------------------------------------------------
b.sezione("11. Riavvio e modalita' spento")
d = nuovo(tenuta_ciclo=False)
d.riprendi(1, 100 * 60)
stadi = [giro(d, 100 + m, 0, comp=False).stadio for m in range(0, 15)]
b.v("ripreso allo stadio 1: nessuna durata minima da ripagare, rilascio dopo il solo ritardo",
    stadi[0] == 1 and stadi[9] == 1 and stadi[10] == 0)
d = nuovo()
for m in range(0, 4):
    giro(d, m, 1500)
d.imposta(0, 4 * 60)
r = d.valuta(Ingressi(1500, None, False, None, MEZZOGIORNO, 5 * 60), spento=True)
b.v("spento: stadio 0 e le misure restano calcolate", r.stadio == 0 and r.esubero == 1500 and "spento" in r.motivo)

b.fine()
