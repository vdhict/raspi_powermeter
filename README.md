# raspi_powermeter

Meet het stroomverbruik via de P1-poort van de slimme meter met een Raspberry Pi,
en reken door wat een (plug-in) thuisbatterij zou opleveren.

- **Logger** – leest elk telegram van de meter (DSMR 2 t/m 5, ook Belgische meters) en
  bewaart per 10 seconden het gemiddelde vermogen van afname en teruglevering in SQLite.
- **Dashboard** – live vermogen, grafiek per dag/week/maand, dagtotalen en CSV-export.
- **Batterijsimulatie** – speelt de gemeten data af met batterijen van verschillende
  groottes en laat zien hoeveel afname en teruglevering overblijft en wat dat bespaart.

Alleen `pyserial` is nodig; de rest is de Python-standaardbibliotheek.

## Benodigdheden

- Raspberry Pi (elk model met USB en netwerk; een Zero W met OTG-adapter volstaat)
  met Raspberry Pi OS Bookworm of nieuwer (Python ≥ 3.11)
- P1-kabel (RJ12 → USB)
- Slimme meter met P1-poort. Bij sommige meters (vooral in België) moet de poort eerst
  worden opengezet via de netbeheerder.

Een DSMR 5-meter voedt de kabel zelf en stuurt elke seconde een meting; oudere meters
elke 10 seconden.

## Installatie

Op de Pi, met de P1-kabel in de meter en in een USB-poort:

```bash
git clone https://github.com/vdhict/raspi_powermeter.git
```

```bash
cd raspi_powermeter && sh deploy/install.sh
```

Het script maakt een virtualenv, kopieert `config.example.toml` naar `config.toml`,
en installeert en start de systemd-service `p1meter`. Daarna staat het dashboard op
`http://<ip-van-de-pi>:8080` en start alles vanzelf opnieuw na stroomuitval.

Controleer of de tijdzone van de Pi klopt (dagtotalen volgen de lokale tijd):

```bash
sudo timedatectl set-timezone Europe/Amsterdam
```

### Kabel controleren

```bash
.venv/bin/python -m p1meter -c config.toml test
```

Dit toont tien metingen. Komt er niets, stop dan eerst de service
(`sudo systemctl stop p1meter`, de poort kan maar door één programma gelezen worden),
kijk of de kabel als `/dev/ttyUSB0` verschijnt (`ls /dev/ttyUSB*`) en pas zo nodig
`device` aan in `config.toml`. De seriële instelling wordt automatisch gevonden;
forceren kan met `serial_mode = "dsmr5"` of `"dsmr2"`.

### Op afstand meekijken

De Pi staat bij je ouders, dus het dashboard is alleen daar op het thuisnetwerk te
bereiken. De eenvoudigste veilige manier om van buitenaf mee te kijken is
[Tailscale](https://tailscale.com/download/linux) op de Pi en op je eigen apparaat.
Zet de poort **niet** open in de router: het dashboard heeft geen wachtwoord.

## Batterij doorrekenen

Onderaan het dashboard, of in de terminal:

```bash
.venv/bin/python -m p1meter -c config.toml report --power 800 --capacities 1 2 3 5 8 10
```

De simulatie volgt de "nul op de meter"-regeling die plug-in batterijen met een
P1-meter gebruiken: laden met stroom die anders teruggeleverd zou worden, ontladen
zodra er stroom van het net gevraagd wordt, begrensd door het vermogen (standaard
800 W, het maximum voor een stekkerbatterij), de capaciteit en het rendement.

Besparing = *kWh uit de batterij* × prijs afname − *kWh in de batterij* × vergoeding
teruglevering. Stel de prijzen in `config.toml` of in het dashboard in. Met salderen
is de vergoeding gelijk aan de afnameprijs en levert een batterij niets op; reken dus
met de tarieven van ná het einde van de salderingsregeling (1 januari 2027).

Waar je op moet letten:

- **Meet lang genoeg.** In de winter is er nauwelijks overschot, in de zomer veel.
  "Per jaar" rekent het gemeten patroon door naar 365 dagen en klopt dus pas als de
  meetperiode alle seizoenen dekt.
- Het eigen verbruik van de batterij (stand-by) en dynamische tarieven zijn niet
  meegerekend; de uitkomst is daardoor eerder iets te gunstig dan te somber.
- De simulatie rekent op gemiddelden per minuut.

## Uitproberen zonder meter

```bash
python3 -m p1meter demo --days 14
```

```bash
python3 -m p1meter run --simulate
```

Het eerste commando vult `data/p1meter.db` met een gesimuleerd huishouden met
zonnepanelen, het tweede start het dashboard met een gesimuleerde live meter.
Verwijder `data/` voordat je echt gaat meten.

## Data

Alles staat in één SQLite-bestand (`data/p1meter.db`, tabel `samples`), ongeveer
150 MB per jaar. Er wordt eens per minuut geschreven om de SD-kaart te sparen.
Back-up of analyse elders: download `http://<pi>:8080/export.csv` of kopieer het
databasebestand.

## Ontwikkelen

```bash
pip install pyserial pytest && python3 -m pytest
```

| Bestand | Inhoud |
|---|---|
| `p1meter/telegram.py` | DSMR-telegram parsen, CRC-controle |
| `p1meter/reader.py` | seriële poort, automatische detectie, middelen per 10 s |
| `p1meter/store.py` | SQLite-opslag en queries |
| `p1meter/battery.py` | batterijsimulatie |
| `p1meter/web.py`, `p1meter/static/` | API en dashboard |
| `p1meter/simulator.py` | gesimuleerd huishouden |
