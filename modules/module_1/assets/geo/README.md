# US state map data

`usa_110m.json` is the 1:110m US topology that plotly.js loads for `scope="usa"` maps, copied
unchanged from `https://cdn.plot.ly/usa_110m.json` (49,104 bytes, sha256
`c97dc0675b4650b266545c96c0c91bd7d59d6528496efa5af7bb98be574cbd39`). Plotly builds it from
Natural Earth data (public domain). Module 1 serves it from the app (`topojsonURL`), so the state
map never requests map data from a CDN.
