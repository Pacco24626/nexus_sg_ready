<img src="icons/logo.png" alt="Nexus SG Ready" width="420">

# Nexus SG Ready

Lo Smart Grid della pompa di calore comandato dall'esubero fotovoltaico, in Home Assistant.
Colleghi i due contatti SG-Ready (i relè che hai già in Home Assistant), lo scambio con la
rete e qualche lettura della pompa. L'integrazione chiude e apre i contatti **nell'ordine
sicuro**, e decide sull'esubero **al lordo della pompa**.

Non parla con la pompa di calore e non dipende dalla marca. È stata scritta e provata per una
Mitsubishi **Ecodan**, ma funziona con qualunque macchina che abbia gli ingressi SG-Ready a
quattro stati.

## Perché al lordo della pompa

Una logica che guarda solo l'immissione in rete si spegne da sola. La pompa parte, si mangia
l'esubero, l'immissione sparisce e il contatto si apre. Poi la pompa si ferma, l'esubero torna
e il contatto si richiude, e così via.

Qui l'esubero è **immissione in rete più quello che la pompa sta assorbendo**. Quando la
pompa parte, questa grandezza non cambia: la decisione resta ferma.

La potenza della pompa viene da un sensore, se ne hai uno (pinza o contatore). Altrimenti è la
**potenza stimata** mentre il compressore è acceso. Serve almeno uno dei due: senza, il modulo
non si salva. Conta anche quando la pompa è partita da sola: così un ciclo ACS già in corso col
sole si allunga dei gradi dello SG.

Se compressore o potenza diventano illeggibili per poco (rete, riavvio del gateway), per 10
minuti vale l'ultima lettura valida. Così non si crede la pompa ferma e non si rilascia a metà
ciclo.

## I contatti

| Contatto 1 | Contatto 2 | Stato della macchina (Ecodan) | Chi lo chiede |
|---|---|---|---|
| aperto | aperto | normale | stadio 0 |
| **chiuso** | aperto | accensione consigliata: ACS all'obiettivo + il delta impostato nella macchina | stadio 1 |
| **chiuso** | **chiuso** | comando di accensione: ACS al massimo del bollitore, anche con la resistenza | stadio 2, solo se abilitato |
| aperto | **chiuso** | **blocco**: niente riscaldamento, ACS e antilegionella | **mai** |

Il blocco non viene mai chiesto, e non ci si passa nemmeno per un attimo:

- **in salita** chiude il contatto 1, **aspetta** che Home Assistant lo veda chiuso, e solo dopo
  chiude il 2;
- **in discesa** apre il 2, aspetta la conferma, poi apre l'1;
- se un relè non conferma entro 30 secondi **si ferma lì** e segnala l'anomalia, invece di
  andare avanti alla cieca. Se è il 2 che non si apre, l'1 resta chiuso;
- una **sentinella** sorveglia i contatti in tutte le modalità. Se trova il solo contatto 2
  chiuso, lo riapre dopo 5 secondi, chiunque l'abbia chiuso (un'altra automazione, un tasto,
  un'app). Lo segnala con una notifica e con il sensore «Anomalia contatti». Se il relè non
  risponde, o se qualcosa continua a richiudere il contatto 2, riprova con attese che
  raddoppiano, fino a 10 minuti;
- comanda solo contatti che **legge**: dopo un riavvio aspetta che i relè compaiano e riparte
  dal loro stadio, invece di rilasciarlo;
- dalle opzioni **non si può togliere né cambiare un contatto chiuso**: l'integrazione
  ricaricata non lo vedrebbe più. E due voci non possono comandare gli stessi relè.

Consiglio per l'installatore: se puoi, rendi il blocco **fisicamente impossibile**. Fai passare
il contatto 2 da un secondo polo comandato dal contatto 1, per esempio con un relè ausiliario
bipolare.

## Come decide

- **Sale** di uno stadio quando l'esubero lordo resta sopra la soglia per tutto il **ritardo di
  attivazione**. Deve essere dentro la **fascia oraria**, passata la **pausa minima**
  dall'ultimo rilascio e, se colleghi il bollitore, con l'acqua **sotto la temperatura
  indicata**. Così ogni rabbocco è abbastanza grande da valere la partenza del compressore.
- **Scende** quando l'esubero resta sotto la soglia meno l'**isteresi** per tutto il **ritardo di
  rilascio**, oppure subito fuori fascia. Mai prima della **durata minima**.
- **Tiene fino a fine ciclo**: se la pompa sta scaldando, lo stadio non si rilascia finché non si
  ferma, al massimo per la **tenuta massima**. Rilasciare a metà ciclo riporterebbe l'obiettivo al
  valore normale, e la macchina si fermerebbe con l'acqua già sopra.
- Uno scalino alla volta: da 0 a 2 si passa sempre per 1. La pausa minima vale anche prima di
  risalire da 1 a 2, così non si fa l'altalena fra i due stadi.
- Se lo scambio con la rete non si legge, non si sale, e si scende come senza esubero.

Il perché di ogni decisione è scritto nell'attributo `motivo` del sensore **Stadio richiesto**.
Per esempio: «Esubero 1350 W sopra 1000 W: stadio 1 fra 2 min».

## Modalità

Una select con tre modalità, che sopravvive ai riavvii:

| Modalità | Cosa fa |
|---|---|
| **Osservazione** | Decide e scrive il perché, ma **non tocca i contatti**. È quella di partenza, per confrontare le decisioni con il sistema che comanda i contatti oggi. |
| **Automatico** | Allinea i contatti alla decisione. Entrando in automatico non rilascia di colpo uno stadio che i contatti hanno già. Uscendo, riporta i contatti a normale, e riprova finché non ci arrivano, anche dopo un riavvio. |
| **Spento** | Non chiede nessuno stadio. Calcola ancora l'esubero. |

Se in automatico un altro sistema muove i contatti (una vecchia scena, un'automazione),
l'integrazione aspetta un minuto e li rimette, e ti avvisa con una notifica. Prima di passare
all'automatico **disattiva le altre logiche** che comandano gli stessi relè.

## Cosa nasce

| Entità | Cosa dice |
|---|---|
| **Modalità** (select) | automatico, osservazione, spento |
| **Stadio richiesto** | `normale`, `consigliato` o `comando`, con attributi `motivo`, `esubero_lordo`, `potenza_pdc`, `pompa_in_marcia`, `tenuta`, `stato_contatti`, `ultimo_cambio`. Nel database finiscono solo quelli che cambiano di rado: `ultimo_cambio` conserva l'ora e il perché dell'ultimo cambio di stadio. |
| **Stadio dei contatti** | cosa vede la macchina dai contatti, chiunque li abbia mossi: anche `blocco` |
| **Esubero lordo** | W, immissione in rete più potenza della pompa |
| **Anomalia contatti** | diagnostico: blocco trovato o relè che non conferma, con il dettaglio |

## Installazione

1. HACS → menu ⋮ → **Repository personalizzate**
2. URL `https://github.com/Pacco24626/nexus_sg_ready`, categoria **Integration**
3. Installa e **riavvia Home Assistant**
4. **Impostazioni → Dispositivi e servizi → Aggiungi integrazione → Nexus SG Ready**

## Configurazione

Il modulo arriva già compilato. Le entità sono riconosciute dal nome. La convenzione del
contatore è presa da Nexus Fotovoltaico, se legge lo stesso scambio. Tutto si cambia poi da
**Configura**.

| Sezione | Voci |
|---|---|
| Contatti SG | **contatto 1** (obbligatorio), contatto 2 (solo per lo stadio 2), correggi il blocco |
| Rete | **scambio con la rete** (obbligatorio, W o kW), convenzione del contatore |
| Pompa di calore | potenza assorbita e/o compressore attivo (**almeno uno**), temperatura del bollitore, potenza stimata in marcia (1500 W), attiva solo con il bollitore sotto (48 °C) |
| Stadi | esubero per lo stadio 1 (1000 W), isteresi (300 W), abilita lo stadio 2 (no), esubero per lo stadio 2 (2500 W) |
| Tempi e fascia oraria | fascia 09:00–18:00, ritardo di attivazione 3 min, ritardo di rilascio 10 min, durata minima 10 min, pausa minima 10 min, tieni fino a fine ciclo (sì), tenuta massima 60 min |

Durata minima e pausa di 10 minuti seguono la specifica SG-Ready (BWP 1.1, §3.1).

## Ecodan: da impostare sulla macchina

Nel menu di servizio, **Smart grid ready**: acqua calda **On** e il delta per l'accensione
consigliata, per esempio +5 °C. Di fabbrica è inattivo, e il contatto 1 da solo non fa niente.
Lascia il riscaldamento **Off**, a meno di avere un accumulo: negli stati SG la zona 1 lavora
con mandate da 50–60 °C.

## Licenza

Apache 2.0.
