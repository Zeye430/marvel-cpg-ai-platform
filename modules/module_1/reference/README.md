# City reference points for the Module 1 map

`city_centroids.csv` gives one latitude/longitude per store city (`city`, `state` as in the
store dimension). The map uses it to draw **city-level approximate** bubbles: it is not a
store address or store location, and several stores in one city share one point.

Source: U.S. Census Bureau, 2023 Gazetteer Files, national places file
(`https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_place_national.zip`,
zip sha256 `3c8b23c0dcdb15c0ebd16fee7373e032466e6b585650aa219bc1bee5ac8bd58f`). Coordinates are
the Census internal point (`INTPTLAT`, `INTPTLONG`) of the matching place, rounded to four
decimals; `census_name` and `geoid` record the exact place used. U.S. Government work, public
domain.

Matching: `"<city> city"` in the same state, otherwise the single place named
`<city> city|town|village|CDP|municipality`. Five names were mapped by hand:
New York City → "New York city", Saint Paul → "St. Paul city",
Nashville → "Nashville-Davidson metropolitan government (balance)",
Augusta → "Augusta-Richmond County consolidated government (balance)",
Arlington VA → "Arlington CDP".

A store whose city is not in this file is still listed (state level and in the store list);
it gets no map point. No geocoding service is called.
