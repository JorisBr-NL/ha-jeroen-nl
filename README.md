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
| Gasprijs | totaalprijs vandaag (EUR/m³) | |
| Opwekvoorspelling vandaag / morgen | kWh | `p10`, `p90`; bij vandaag ook `days` en `hours` |

`prices` is de lijst om mee te plannen: de vastgestelde kwartierprijzen vanaf nu, gevolgd door de voorspelde kwartieren met `low`/`high` en `forecast: true`. Elke regel heeft `start`, `end`, `price` (totaal) en `market`. De grote lijsten worden niet in de recorder opgeslagen.

Totaalprijs = (marktprijs + opslag + energiebelasting) × (1 + btw). Opslag, energiebelasting en btw stel je in bij *Configureren*.

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
