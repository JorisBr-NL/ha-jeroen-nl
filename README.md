# Jeroen.nl voor Home Assistant

Haalt de data van [jeroen.nl](https://jeroen.nl) op met een API-sleutel van je eigen account:

- **Stroomprijzen** per kwartier (day-ahead, vandaag en morgen)
- **Stroomprijsvoorspelling** voor de komende dagen, met een band (laag–hoog)
- **Gasprijs** per dag
- **Opwekvoorspelling** van je zonnepanelen (per dag met p10–p90, per uur)

Gebouwd als databron voor [Home EMS](https://github.com/JorisBr-NL/home-ems), maar ook los te gebruiken.

## Zuinig met de API

Zonder in te loggen mag een account **10 keer per dag per soort data** ophalen, voor alle sleutels samen. Deze integratie haalt alleen op wat ontbreekt en bewaart alles over een herstart heen:

| Data | Wanneer | Aanroepen per dag |
|---|---|---|
| Prijzen vandaag | alleen als ze er nog niet zijn (normaal al opgehaald als "morgen") | 0–1 |
| Prijzen morgen | vanaf 13:15, tot ze er zijn (elke 30 min een poging) | 1–2 |
| Prijsvoorspelling | één keer na 05:00 | 1 |
| Gasprijs | één keer na 06:00 | 1 |
| Opwekvoorspelling | één keer na 01:15 | 1 |

Een eigen maximum per dag (standaard 6 voor stroomprijzen) laat ruimte voor andere sleutels van hetzelfde account. De diagnostische sensoren *API-aanroepen vandaag* laten zien hoeveel er gebruikt is.

## Sensoren

| Sensor | Waarde | Belangrijkste attributen |
|---|---|---|
| Stroomprijs | totaalprijs nu (EUR/kWh) | `market_price`, `min_today`, `max_today`, `avg_today`, `today`, `tomorrow`, **`prices`** |
| Stroomprijs markt | kale beursprijs nu (EUR/kWh) | |
| Volgende prijs | totaalprijs van het volgende kwartier | `start`, `end`, `market` |
| Laagste / hoogste prijs vandaag | totaalprijs van het goedkoopste / duurste kwartier | `start`, `end`, `market` |
| Gemiddelde prijs vandaag | gemiddelde totaalprijs van de dag | |
| Goedkoopste moment vandaag | tijdstempel van het goedkoopste kwartier, direct bruikbaar als tijd-trigger | `end`, `price` |
| Laagste / hoogste / gemiddelde prijs morgen, goedkoopste moment morgen | hetzelfde voor morgen; leeg tot de prijzen van morgen bekend zijn (~13:15) | |
| Gasprijs | totaalprijs vandaag (EUR/m³) | |
| Opwekvoorspelling vandaag / morgen | kWh | `p10`, `p90`; bij vandaag ook `days` en `hours` |

`prices` is de lijst om mee te plannen: de vastgestelde kwartierprijzen vanaf nu, gevolgd door de voorspelde kwartieren met `low`/`high` en `forecast: true`. Elke regel heeft `start`, `end`, `price` (totaal) en `market`. De grote lijsten worden niet in de recorder opgeslagen.

Totaalprijs = (marktprijs + opslag + energiebelasting) × (1 + btw). Opslag, energiebelasting en btw stel je in bij *Configureren*.

## Voorbeeld: automation op het goedkoopste moment

```yaml
triggers:
  - trigger: time
    at: sensor.jeroen_nl_goedkoopste_moment_vandaag
actions:
  - action: switch.turn_on
    target:
      entity_id: switch.vaatwasser
```

## Voorbeeld: dashboardkaart

Drie tegels (nu, laagst, goedkoopst) met daaronder een grafiek van vandaag, morgen en de voorspelling voor de dagen erna. Elk vastgesteld kwartier krijgt een kleur ten opzichte van het daggemiddelde: groen is meer dan 10% goedkoper, rood meer dan 10% duurder, oranje daartussen. De voorspelde kwartieren zijn grijs.

Vereist: [apexcharts-card](https://github.com/RomRider/apexcharts-card) (HACS → Frontend). De entity-ID's hieronder gelden als Home Assistant in het Nederlands staat; controleer ze via **Instellingen → Entiteiten**.

```yaml
type: vertical-stack
cards:
  - type: grid
    columns: 3
    square: false
    cards:
      - type: tile
        entity: sensor.jeroen_nl_stroomprijs
        name: Nu
        icon: mdi:flash
        color: amber
        vertical: true
      - type: tile
        entity: sensor.jeroen_nl_laagste_prijs_vandaag
        name: Laagst
        icon: mdi:arrow-down-bold
        color: green
        vertical: true
      - type: tile
        entity: sensor.jeroen_nl_goedkoopste_moment_vandaag
        name: Goedkoopst
        icon: mdi:clock-check-outline
        color: teal
        vertical: true

  - type: custom:apexcharts-card
    graph_span: 4d
    span:
      start: day
    now:
      show: true
      label: Nu
    header:
      show: true
      title: Stroomprijs en voorspelling
    all_series_config:
      type: column
      unit: €/kWh
      float_precision: 3
      show:
        in_header: false
        legend_value: false
    yaxis:
      - decimals: 2
        apex_config:
          tickAmount: 5
    apex_config:
      chart:
        stacked: true
        height: 300
      plotOptions:
        bar:
          columnWidth: "95%"
      dataLabels:
        enabled: false
      legend:
        show: true
        position: top
      grid:
        strokeDashArray: 3
      xaxis:
        labels:
          datetimeFormatter:
            hour: "HH:mm"
      tooltip:
        shared: false
        x:
          format: "dd MMM HH:mm"
    series:
      - entity: sensor.jeroen_nl_stroomprijs
        name: Goedkoop
        color: "#43a047"
        data_generator: |
          const all = [...(entity.attributes.today || []), ...(entity.attributes.tomorrow || [])];
          const day = p => new Date(p.start).toDateString();
          const avg = {};
          for (const d of new Set(all.map(day))) {
            const xs = all.filter(p => day(p) === d).map(p => p.price);
            avg[d] = xs.reduce((a, b) => a + b, 0) / xs.length;
          }
          return all.map(p => {
            const r = p.price / avg[day(p)];
            return [new Date(p.start).getTime(), r < 0.9 && true ? p.price : null];
          });
      - entity: sensor.jeroen_nl_stroomprijs
        name: Normaal
        color: "#fb8c00"
        data_generator: |
          const all = [...(entity.attributes.today || []), ...(entity.attributes.tomorrow || [])];
          const day = p => new Date(p.start).toDateString();
          const avg = {};
          for (const d of new Set(all.map(day))) {
            const xs = all.filter(p => day(p) === d).map(p => p.price);
            avg[d] = xs.reduce((a, b) => a + b, 0) / xs.length;
          }
          return all.map(p => {
            const r = p.price / avg[day(p)];
            return [new Date(p.start).getTime(), r >= 0.9 && r <= 1.1 ? p.price : null];
          });
      - entity: sensor.jeroen_nl_stroomprijs
        name: Duur
        color: "#e53935"
        data_generator: |
          const all = [...(entity.attributes.today || []), ...(entity.attributes.tomorrow || [])];
          const day = p => new Date(p.start).toDateString();
          const avg = {};
          for (const d of new Set(all.map(day))) {
            const xs = all.filter(p => day(p) === d).map(p => p.price);
            avg[d] = xs.reduce((a, b) => a + b, 0) / xs.length;
          }
          return all.map(p => {
            const r = p.price / avg[day(p)];
            return [new Date(p.start).getTime(), r > 1.1 && true ? p.price : null];
          });
      - entity: sensor.jeroen_nl_stroomprijs
        name: Voorspelling
        color: "#9e9e9e"
        data_generator: |
          return (entity.attributes.prices || [])
            .filter(p => p.forecast)
            .map(p => [new Date(p.start).getTime(), p.price]);
```

Pas `0.9` en `1.1` aan om de grenzen voor goedkoop en duur te verschuiven, en `graph_span` om meer of minder dagen te tonen. In de tooltip van een voorspeld kwartier staat de middelste verwachting; de band (`low`/`high`) staat in het attribuut `prices`.

## Installeren

1. HACS → Integraties → menu → *Custom repositories* → deze repo, categorie *Integration*.
2. *Jeroen.nl* installeren en Home Assistant herstarten.
3. Instellingen → Apparaten & diensten → *Integratie toevoegen* → *Jeroen.nl*, en een API-sleutel invullen (maak er een op jeroen.nl → Account → API-sleutels). Het controleren van de sleutel kost één aanroep van de opwekvoorspelling.

## Ontwikkelen

```bash
python -m pip install -r requirements_test.txt
python -m pytest
```

De logica (parsen, tarieven, wanneer er opgehaald wordt) staat in `custom_components/jeroen_nl/core.py`, zonder Home Assistant, zodat die los te testen is.

Data: jeroen.nl. Wat je met de data mag: zie de [API- en MCP-voorwaarden](https://jeroen.nl/api-voorwaarden).

## Icoon

Het icoon in `custom_components/jeroen_nl/brand/` is een eigen ontwerp voor deze integratie. Het is geen logo van jeroen.nl, en deze integratie is niet officieel van of verbonden aan jeroen.nl.
