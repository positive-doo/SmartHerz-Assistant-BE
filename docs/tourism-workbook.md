# Smart ERA workbook catalogue

Chat reads `data/tourism_workbook.json`, a public projection of
`SmartEra POIs and Rutes.xlsx` reviewed on 2026-11-13. Both chat routes share
this source. The protected partner tourism export continues to read the live
Google CSV and WordPress sources.

The projection contains 281 POIs, five thematic routes (481 POI memberships),
74 businesses and 18 associations marked `Objavljeno`. POI and business IDs
are separate. Route rows are matched to the catalogue by verified identity;
route-local row numbers can collide with master IDs. The blank master row
`poi_259` was left out. The church on Gorica and Mljekara Pađeni are present
only in route tabs and have stable chat IDs. Cerovina's name is corroborated by
its route entry. The importer omits unconfirmed coordinates, conflicting
descriptions, direct contacts, images, links, and internal or unpublished
association information.

To review a newer workbook, run this offline from the backend checkout with
`openpyxl` installed in a development environment:

```powershell
python -m pip install openpyxl
python scripts/build_tourism_workbook.py 'C:\path\SmartEra POIs and Rutes.xlsx' data/tourism_workbook.json
```

Review the import messages, the counts, and the JSON diff before committing and
deploying the updated snapshot. The snapshot records the workbook's SHA-256.
Keep the original workbook outside the image and Git. No runtime spreadsheet
library, Pinecone reindexing, or SQL migration is required.
