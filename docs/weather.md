# SMART-HERZ weather

`get_weather` is a local assistant tool in the existing FastAPI backend, shared by JSON and streaming chat. No separate MCP, endpoint, service, API key or new SQL table is needed. The existing frontend displays the answer as chat text.

Source: [MET Norway Locationforecast 2.0](https://api.met.no/weatherapi/locationforecast/2.0/documentation). It uses ECMWF global forecasts for this region. Data is available under [CC BY 4.0](https://docs.api.met.no/doc/License); responses credit MET Norway and link the license. A forecast is not an observation or a guarantee of conditions on a mountain.

Parameters: `municipality` is one of `trebinje`, `bileca`, `berkovici`, `kalinovik`, `nevesinje`, `gacko`, `ljubinje`, `istocni-mostar`. `date_from` and `date_to` are inclusive `YYYY-MM-DD` dates in `Europe/Sarajevo`; null means today / the same day, respectively. Supported dates are today through today + 6 days. Invalid dates and unsupported municipalities return a tool error without inventing a forecast.

Settlement coordinates were checked against [GeoNames](https://download.geonames.org/export/dump/BA.zip) on 26 October 2026 (GeoNames IDs: 3188893, 3204161, 3204269, 3198448, 3194510, 3200768, 3196364, 3261173). Istočni Mostar uses **Gornje Zijemlje (43.4685, 17.9832)**, not Mostar. These are forecast locations, not newly populated WordPress destination fields. GeoNames is licensed under [CC BY 4.0](https://www.geonames.org/export/).

The adapter returns individual forecast points: local time with timezone offset, temperature (°C), wind (km/h), condition code, and precipitation (mm) with its exact interval when supplied. Wind is converted from m/s. Points are not official daily extrema. Precipitation intervals can overlap; they must not be added as if they were disjoint daily totals. Missing variables remain missing.

There is one bounded in-memory cache entry per municipality. Provider `Expires` and `Last-Modified` / `If-Modified-Since` are respected, including HTTP 304. A missing cache expiry uses five minutes. Requests identify SMART-HERZ in the User-Agent and use a ten-second timeout. Expired forecasts are not substituted after source errors. HTTP compression and certificate verification are handled by the existing `httpx` dependency.

`sql/004_smartherz_weather.sql` registers `ai.Tools.get_weather` and enables its `ai.AppTools` assignment only for app 66/client 18. The script is repeatable and preserves existing tools and SQL prompts. As with the other local assistant tools, the actual function schema, routing and weather instructions live in backend code; the registry describes the tool in AdminApp.

Local checks: `python -m unittest tests.test_weather -v`. Deployment uses `Dockerfile.weather` over the currently deployed immutable image, preserving effective environment, secret bindings, resources, scale and pooling.
