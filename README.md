# valued.work vacaturescraper

Haalt dagelijks de vacatures op bij de bedrijven in `companies.csv`, beoordeelt nieuwe vacatures met Claude
en schrijft een feed met alle open vacatures. Draait in GitHub Actions; er is geen handwerk nodig.

## Hoe het werkt

1. Per bedrijf wordt de vacaturelijst opgehaald: rechtstreeks uit het vacaturesysteem waar dat kan
   (Greenhouse, Lever, Workable, Personio, Recruitee, Ashby, SmartRecruiters, Teamtailor, Workday), anders
   leest Claude de carrièrepagina uit. Een pagina die niet gewijzigd is sinds de vorige run kost niets.
2. Elke nieuwe vacature wordt één keer beoordeeld aan de hand van `criteria.md`. Afgewezen vacatures worden
   onthouden en niet opnieuw beoordeeld.
3. Een vacature die twee geslaagde runs achter elkaar niet meer op de bron staat, wordt gesloten.
4. Uitvoer: `public/feed.xml`, `public/jobs.json`, `public/index.html` en het rapport `data/last_run.md`.
   De status staat in `data/state.json`.

Als meer dan de helft van de bedrijven een fout geeft, faalt de run en mailt GitHub je; de feed wordt dan
niet bijgewerkt.

## Installatie (eenmalig)

1. Maak een GitHub-repository en push deze map.
2. Voeg onder Settings > Secrets and variables > Actions het secret `ANTHROPIC_API_KEY` toe.
3. Optioneel, onder Variables: `VW_MODEL` (standaard `claude-opus-5-5`) en `PUBLISH_PAGES=true` om
   `public/` via GitHub Pages te publiceren (zet Pages dan op "GitHub Actions" als bron).
4. Start de workflow "Vacatures ophalen" één keer handmatig; daarna draait hij elke dag om 05:00 UTC.

## Aanpassen

- Bedrijf toevoegen of wijzigen: regel in `companies.csv`. `ats_url` mag leeg blijven.
- Selectie strenger of ruimer: `criteria.md`.
- Lijsten opnieuw uit Excel inlezen: `python -m tools.import_excel REGIO=bestand.xlsx ...`
- Lokaal proefdraaien: `python -m scraper.main --only "naam" [--no-llm]`

## Bekende beperkingen

- Bedrijven met alleen een LinkedIn-vacaturepagina worden overgeslagen (niet uit te lezen zonder login).
- Bij concerns met honderden vacatures worden per run maximaal 10 nieuwe vacatures per bedrijf beoordeeld,
  voorgeselecteerd op titel.
