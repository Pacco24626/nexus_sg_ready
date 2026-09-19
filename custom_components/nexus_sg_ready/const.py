"""Costanti dell'integrazione Nexus SG Ready."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "nexus_sg_ready"
PLATFORMS: list[Platform] = [Platform.SELECT, Platform.SENSOR, Platform.BINARY_SENSOR]

CONF_NOME = "nome"
CONF_ENTITA = "entita"
CONF_PARAMETRI = "parametri"

NOME_PREDEFINITO = "Smart Grid PdC"

# Modalita' dell'integrazione. In osservazione decide e scrive il perche', ma non
# tocca i contatti: serve a confrontarla con il sistema che li comanda oggi.
AUTOMATICO = "automatico"
OSSERVAZIONE = "osservazione"
SPENTO = "spento"
MODALITA = [AUTOMATICO, OSSERVAZIONE, SPENTO]
MODALITA_PREDEFINITA = OSSERVAZIONE

# Stadi che l'integrazione chiede, nell'ordine: 0, 1, 2.
STADI = ["normale", "consigliato", "comando"]
# Stati letti dai contatti: in piu' c'e' il blocco (solo contatto 2 chiuso),
# che l'integrazione non chiede mai.
STATI_CONTATTI = ["normale", "consigliato", "comando", "blocco"]

# Convenzioni del contatore di scambio: le stesse di Nexus Fotovoltaico.
IMMISSIONE_POSITIVA = "immissione_positiva"
PRELIEVO_POSITIVO = "prelievo_positivo"
CONVENZIONI_RETE = [IMMISSIONE_POSITIVA, PRELIEVO_POSITIVO]

# Ogni quanto si rivaluta la decisione.
INTERVALLO_SECONDI = 15
# Quanto si aspetta che un contatto confermi il comando.
TIMEOUT_CONFERMA = 30
# Dopo un comando fallito o annullato da fuori, prima di riprovare.
ATTESA_RIPROVA = 60
# Solo contatto 2 chiuso = blocco della macchina. Si corregge dopo questa
# attesa se il contatto 1 e' aperto, dopo la seconda se non si legge (all'avvio
# i relè possono comparire uno alla volta).
GRAZIA_BLOCCO = 5
GRAZIA_BLOCCO_INCERTO = 60
